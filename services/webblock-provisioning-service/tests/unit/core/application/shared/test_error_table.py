"""Every row of the decision 0006 section 3 error table the application can produce, the class each
error is reported as, and the completeness of the class table."""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from tests.unit.core.application.operations.fakes import (
    ID_ACTIVE,
    ID_COMMAND,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    REQUESTER,
    T0,
    OperationsFixture,
    command_for,
    release_in,
    requester_principal,
)

import provisioning.core.application as application_package
import provisioning.core.domain as domain_package
from provisioning.core.application import (
    AcceptanceFacts,
    ActionDenied,
    ApplicationError,
    BuildInProgress,
    ClaimLost,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    DeployReleaseCommand,
    EnabledOperations,
    GetReleaseDiagnosticsQuery,
    GetReleaseQuery,
    IdempotencyKeyTooLong,
    InvalidCorrelationId,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    Overloaded,
    PermanentFailure,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    RedeployNotAllowed,
    ReleaseNotBuildable,
    ReleaseWithSteps,
    RetryLater,
    StoreUnreachable,
    SubdomainTaken,
    Timeout,
    Unauthenticated,
)
from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    NIL_ID,
    CommandKind,
    CommandNumberOverflow,
    DomainError,
    HostingOffline,
    HostingStatus,
    IdempotencyConflict,
    IllegalTransition,
    ImmutableField,
    InvalidAttempt,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidCommitSha,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidImageDigest,
    InvalidLogRef,
    InvalidRepositoryId,
    InvalidRequester,
    InvalidRequestFingerprint,
    InvalidSubdomain,
    MissingDeadline,
    MissingFailureCode,
    MissingImageDigest,
    OwnerStatusOutdated,
    ProjectHosting,
    ProjectMismatch,
    ReleaseOutdated,
    ReleaseStatus,
    ResultMismatch,
    RouteNotStable,
    RoutingStatus,
    StaleFence,
    UnknownEnumValue,
    is_error,
)

# ---- fixtures ----

T2 = T0 + timedelta(minutes=2)


def _deploy_command(p: Principal | None = None) -> DeployReleaseCommand:
    return DeployReleaseCommand(
        principal=requester_principal() if p is None else p,
        project_id=ID_PROJECT,
        release_id=ID_RELEASE,
        idempotency_key="key-1",
        correlation_id="corr-1",
    )


# CLASS_OF is decision 0006 section 3 for the errors the application can raise or pass on: the
# class each one is reported as. Anything that matches none of them is internal. ClaimLost,
# InvalidCorrelationId, PermanentFailure and RetryLater are worker-internal (decision 0006 has no
# row for them); ReleaseNotBuildable and BuildInProgress are the prototype's build endpoint's
# conflicts; RedeployNotAllowed and IdempotencyKeyTooLong are the admin operations' (decision
# 0023 G10, G11).
_CLASS_OF: dict[type[BaseException], str] = {
    InvalidId: "invalid",
    InvalidIdempotencyKey: "invalid",
    Unauthenticated: "unauthenticated",
    ActionDenied: "forbidden",
    ProjectAccessDenied: "not found",
    NotFound: "not found",
    IdempotencyConflict: "conflict",
    OperationInProgress: "conflict",
    RouteNotStable: "conflict",
    HostingOffline: "conflict",
    IllegalTransition: "conflict",
    ReleaseOutdated: "conflict",
    CommandNumberOverflow: "conflict",
    ReleaseNotBuildable: "conflict",
    BuildInProgress: "conflict",
    LimitReached: "limited",
    RedeployNotAllowed: "conflict",
    IdempotencyKeyTooLong: "invalid",
    DependencyUnavailable: "unavailable",
    DependencyNotConfigured: "unavailable",
    ProjectAccessUnavailable: "unavailable",
    OperationDisabled: "unavailable",
    OperationNotAvailable: "unavailable",
    Contention: "unavailable",
    Timeout: "unavailable",
    Overloaded: "unavailable",
    CorruptRow: "internal",
    StoreUnreachable: "internal",
    ProjectMismatch: "internal",
    InvalidRequester: "internal",
    StaleFence: "internal",
    InvalidCommandTarget: "internal",
    InvalidCommandNumber: "internal",
    UnknownEnumValue: "internal",
    InvalidRequestFingerprint: "internal",
    MissingFailureCode: "internal",
    InvalidFailureCode: "internal",
    ImmutableField: "internal",
    ResultMismatch: "internal",
    MissingImageDigest: "internal",
    InvalidImageDigest: "internal",
    InvalidCommitSha: "internal",
    InvalidRepositoryId: "internal",
    InvalidSubdomain: "internal",
    InvalidAttempt: "internal",
    InvalidFailureSummary: "internal",
    MissingDeadline: "internal",
    InvalidLogRef: "internal",
    OwnerStatusOutdated: "internal",
    ClaimLost: "internal",
    InvalidCorrelationId: "internal",
    PermanentFailure: "internal",
    RetryLater: "internal",
    SubdomainTaken: "internal",
}

# The base classes are not errors anything raises.
_BASES = (DomainError, ApplicationError)


def _exported_exception_classes() -> set[type[BaseException]]:
    found: set[type[BaseException]] = set()
    for module in (domain_package, application_package):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, BaseException) and member not in _BASES:
                found.add(member)
    return found


# Every exception class the domain and application packages export has a row, and every row is an
# exported class: a new error cannot reach a transport unclassified.
def test_class_table_is_complete() -> None:
    exported = _exported_exception_classes()
    assert exported - set(_CLASS_OF) == set(), "exported errors without a class"
    assert set(_CLASS_OF) - exported == set(), "rows for errors the packages do not export"


def _classes_of(exc: BaseException) -> list[str]:
    """The classes ``exc`` matches along its cause chain, sorted; "internal" when it matches no
    classified error at all."""
    seen = {cls_name for cls, cls_name in _CLASS_OF.items() if is_error(exc, cls)}
    return sorted(seen) if seen else ["internal"]


# ---- the scenarios ----


async def _deploy(f: OperationsFixture) -> object:
    return await f.commands.send(_deploy_command())


async def _get_release(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
    )


async def _deploy_nil_release(f: OperationsFixture) -> object:
    return await f.commands.send(dataclasses.replace(_deploy_command(), release_id=NIL_ID))


async def _deploy_empty_key(f: OperationsFixture) -> object:
    return await f.commands.send(dataclasses.replace(_deploy_command(), idempotency_key=""))


async def _deploy_bad_subject(f: OperationsFixture) -> object:
    p = dataclasses.replace(requester_principal(), subject="bad\x7f")
    return await f.commands.send(_deploy_command(p))


async def _diagnostics(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseDiagnosticsQuery(
            requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE
        )
    )


def _nothing(f: OperationsFixture) -> None:
    pass


def _facts(
    change: Callable[[AcceptanceFacts], AcceptanceFacts],
) -> Callable[[OperationsFixture], None]:
    def arrange(f: OperationsFixture) -> None:
        f.acceptance.facts[0] = change(f.acceptance.facts[0])

    return arrange


def _hosting(
    change: Callable[[ProjectHosting], ProjectHosting],
) -> Callable[[OperationsFixture], None]:
    return _facts(lambda fa: dataclasses.replace(fa, hosting=change(fa.hosting)))


def _target_status(status: ReleaseStatus) -> Callable[[AcceptanceFacts], AcceptanceFacts]:
    def change(fa: AcceptanceFacts) -> AcceptanceFacts:
        assert fa.target is not None
        return dataclasses.replace(fa, target=dataclasses.replace(fa.target, status=status))

    return change


def _live_newer_than_target(fa: AcceptanceFacts) -> AcceptanceFacts:
    live = release_in(ID_ACTIVE, ReleaseStatus.LIVE, T2)
    return dataclasses.replace(
        fa, hosting=dataclasses.replace(fa.hosting, live_release_id=live.id), live=live
    )


def _deny_action(f: OperationsFixture) -> None:
    f.policy.err = ActionDenied()


def _deny_access(f: OperationsFixture) -> None:
    f.access.err = ProjectAccessDenied()


def _no_hosting_row(f: OperationsFixture) -> None:
    f.acceptance.accept_err = NotFound()


def _read_not_found(f: OperationsFixture) -> None:
    f.reads.err = NotFound()


def _conflicting_key(f: OperationsFixture) -> None:
    f.acceptance.found = command_for(CommandKind.PROMOTE, ID_COMMAND, "key-1", REQUESTER, 3)


def _limit_reached(f: OperationsFixture) -> None:
    f.entitlement.err = LimitReached()


def _entitlement_not_configured(f: OperationsFixture) -> None:
    f.entitlement.err = DependencyNotConfigured("OD-04")


def _access_not_configured(f: OperationsFixture) -> None:
    f.access.err = DependencyNotConfigured("OD-01")


def _contention(f: OperationsFixture) -> None:
    f.acceptance.accept_err = Contention()


def _corrupt_read(f: OperationsFixture) -> None:
    f.reads.err = CorruptRow()


def _unavailable_read(f: OperationsFixture) -> None:
    # Diagnostics read rows since wave 6 (decision 0014 section 21 ruling 6); the class is still
    # mapped when a store raises it.
    f.reads.err = OperationNotAvailable("x")


def _id_source_fails(f: OperationsFixture) -> None:
    f.ids.err = RuntimeError("no entropy")


def _switch_everything_off(f: OperationsFixture) -> None:
    f.enabled = EnabledOperations()
    f.build()


def _foreign_release(f: OperationsFixture) -> None:
    release = release_in(ID_RELEASE, ReleaseStatus.BUILT, T0)
    f.reads.release = ReleaseWithSteps(
        release=dataclasses.replace(release, project_id=ID_PROJECT2), steps=()
    )


_Row = tuple[
    Callable[[OperationsFixture], None],
    Callable[[OperationsFixture], Awaitable[object]],
    type[BaseException] | None,
    str,
]

# name -> (arrange, call, wanted error or None for internal matching no classified error, class)
_ROWS: dict[str, _Row] = {
    "invalid_id": (_nothing, _deploy_nil_release, InvalidId, "invalid"),
    "invalid_idempotency_key": (_nothing, _deploy_empty_key, InvalidIdempotencyKey, "invalid"),
    "unauthenticated": (_nothing, _deploy_bad_subject, Unauthenticated, "unauthenticated"),
    "action_not_permitted": (
        _deny_action,
        _deploy,
        ActionDenied,
        "forbidden",
    ),
    "not_found: no access": (
        _deny_access,
        _deploy,
        ProjectAccessDenied,
        "not found",
    ),
    "not_found: missing release at acceptance": (
        _facts(lambda fa: dataclasses.replace(fa, target=None)),
        _deploy,
        NotFound,
        "not found",
    ),
    "not_found: no hosting row": (
        _no_hosting_row,
        _deploy,
        NotFound,
        "not found",
    ),
    "not_found: read": (_read_not_found, _get_release, NotFound, "not found"),
    "idempotency_conflict": (
        _conflicting_key,
        _deploy,
        IdempotencyConflict,
        "conflict",
    ),
    "idempotency_conflict under the lock": (
        _facts(
            lambda fa: dataclasses.replace(
                fa,
                by_key=command_for(CommandKind.PROMOTE, ID_COMMAND, "key-1", REQUESTER, 3),
            )
        ),
        _deploy,
        IdempotencyConflict,
        "conflict",
    ),
    "operation_in_progress": (
        _facts(
            lambda fa: dataclasses.replace(
                fa,
                in_flight=command_for(CommandKind.PROMOTE, ID_COMMAND, "other", REQUESTER, 4),
            )
        ),
        _deploy,
        OperationInProgress,
        "conflict",
    ),
    "route_not_stable": (
        _hosting(lambda h: dataclasses.replace(h, routing_status=RoutingStatus.CHANGING)),
        _deploy,
        RouteNotStable,
        "conflict",
    ),
    "hosting_offline": (
        _hosting(lambda h: dataclasses.replace(h, hosting_status=HostingStatus.OFFLINE)),
        _deploy,
        HostingOffline,
        "conflict",
    ),
    "illegal_state": (
        _facts(_target_status(ReleaseStatus.FAILED)),
        _deploy,
        IllegalTransition,
        "conflict",
    ),
    "release_outdated": (_facts(_live_newer_than_target), _deploy, ReleaseOutdated, "conflict"),
    "command_number_exhausted": (
        _hosting(lambda h: dataclasses.replace(h, last_command_number=MAX_COMMAND_NUMBER)),
        _deploy,
        CommandNumberOverflow,
        "conflict",
    ),
    "limit_reached": (
        _limit_reached,
        _deploy,
        LimitReached,
        "limited",
    ),
    "dependency_unavailable": (
        _entitlement_not_configured,
        _deploy,
        DependencyUnavailable,
        "unavailable",
    ),
    "project_access_unavailable": (
        _access_not_configured,
        _get_release,
        ProjectAccessUnavailable,
        "unavailable",
    ),
    "operation_unavailable: switched off": (
        _switch_everything_off,
        _deploy,
        OperationDisabled,
        "unavailable",
    ),
    "operation_unavailable: from the read store": (
        _unavailable_read,
        _diagnostics,
        OperationNotAvailable,
        "unavailable",
    ),
    "contention": (
        _contention,
        _deploy,
        Contention,
        "unavailable",
    ),
    "internal: corrupt row": (
        _corrupt_read,
        _get_release,
        CorruptRow,
        "internal",
    ),
    "internal: facts of another project": (
        _hosting(lambda h: dataclasses.replace(h, project_id=ID_PROJECT2)),
        _deploy,
        None,
        "internal",
    ),
    "internal: ID source failure": (
        _id_source_fails,
        _deploy,
        None,
        "internal",
    ),
    "internal: release of another project from the store": (
        _foreign_release,
        _get_release,
        None,
        "internal",
    ),
}


# Every row of the decision 0006 section 3 table that the application can produce, by error
# identity: each scenario raises its error, and the error falls into exactly one class.
@pytest.mark.asyncio
@pytest.mark.parametrize("row", list(_ROWS.values()), ids=list(_ROWS))
async def test_error_table(row: _Row) -> None:
    arrange, call, want, cls_name = row
    f = OperationsFixture(with_history=True)
    arrange(f)
    with pytest.raises(Exception) as exc_info:  # matched below, along the cause chain
        await call(f)
    err = exc_info.value
    assert not is_error(err, NotImplementedError), f"{err!r} is a stub"
    if want is not None:
        assert is_error(err, want), f"error {err!r}, want {want.__name__}"
    assert _classes_of(err) == [cls_name]
