"""Error classification (decision 0006 section 3 as amended by 0023 G5, G6, G8a and G14): an ordered
table over the explicit cause chain (``__cause__`` only), every public error class of the core with
a row, one UPPER_SNAKE code enum on Notion's status table, and fixed messages."""

from __future__ import annotations

import inspect
from types import ModuleType
from uuid import UUID

import pytest

import provisioning.core.application as application
import provisioning.core.application.jobs.claims as jobs
import provisioning.core.application.ports.build_system as build_system_port
import provisioning.core.application.ports.site_cluster as site_cluster_port
import provisioning.core.domain as domain
from provisioning.core.application import (
    ActionDenied,
    BuildInProgress,
    ClaimLost,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    IdempotencyKeyTooLong,
    InvalidCorrelationId,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    Overloaded,
    PermanentFailure,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    RedeployNotAllowed,
    ReleaseNotBuildable,
    RetryLater,
    StoreUnreachable,
    Timeout,
    Unauthenticated,
)
from provisioning.core.domain import (
    CommandNumberOverflow,
    HostingOffline,
    IdempotencyConflict,
    IllegalTransition,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidRequester,
    ReleaseOutdated,
    RouteNotStable,
    UnknownEnumValue,
)
from provisioning.presentation.operations import (
    CLASSES,
    Code,
    FieldCode,
    FieldError,
    IdentityUnavailable,
    MalformedRequest,
    RequestTooLarge,
    UnknownRoute,
    classify,
    field_errors,
    field_message,
    message,
    status_and_code,
    status_of,
)

# The audit's table C.1.3 (decision 0023 G6, G8a), written out as the specification: status and
# code of each class the transports can meet. Every other public error class of the core is
# INTERNAL_ERROR.
SPEC: dict[type[BaseException], tuple[int, str]] = {
    MalformedRequest: (400, "VALIDATION_FAILED"),
    RequestTooLarge: (413, "REQUEST_TOO_LARGE"),
    IdentityUnavailable: (503, "IDENTITY_UNAVAILABLE"),
    UnknownRoute: (404, "RESOURCE_NOT_FOUND"),
    Unauthenticated: (401, "UNAUTHENTICATED"),
    ActionDenied: (403, "ACCESS_DENIED"),
    ProjectAccessDenied: (404, "PROJECT_NOT_FOUND"),
    NotFound: (404, "PROJECT_NOT_FOUND"),
    CorruptRow: (500, "INTERNAL_ERROR"),
    StoreUnreachable: (503, "DEPENDENCY_UNAVAILABLE"),
    ProjectAccessUnavailable: (503, "PROJECT_ACCESS_UNAVAILABLE"),
    OperationNotAvailable: (503, "OPERATION_UNAVAILABLE"),
    OperationDisabled: (503, "OPERATION_UNAVAILABLE"),
    OperationInProgress: (409, "COMMAND_IN_PROGRESS"),
    ReleaseNotBuildable: (422, "RELEASE_NOT_BUILDABLE"),
    BuildInProgress: (409, "BUILD_IN_PROGRESS"),
    LimitReached: (422, "PUBLISH_LIMIT_REACHED"),
    RedeployNotAllowed: (422, "REDEPLOY_NOT_ALLOWED"),
    IdempotencyKeyTooLong: (400, "VALIDATION_FAILED"),
    Contention: (409, "CONCURRENT_CHANGE"),
    Timeout: (504, "REQUEST_TIMEOUT"),
    Overloaded: (503, "SERVICE_OVERLOADED"),
    DependencyUnavailable: (503, "DEPENDENCY_UNAVAILABLE"),
    DependencyNotConfigured: (503, "DEPENDENCY_UNAVAILABLE"),
    InvalidId: (400, "VALIDATION_FAILED"),
    InvalidIdempotencyKey: (400, "VALIDATION_FAILED"),
    InvalidRequester: (401, "UNAUTHENTICATED"),
    IdempotencyConflict: (422, "IDEMPOTENCY_KEY_REUSED"),
    RouteNotStable: (422, "ROUTE_NOT_STABLE"),
    HostingOffline: (422, "HOSTING_OFFLINE"),
    IllegalTransition: (422, "RELEASE_STATE_INVALID"),
    ReleaseOutdated: (422, "RELEASE_OUTDATED"),
    CommandNumberOverflow: (422, "COMMAND_NUMBER_EXHAUSTED"),
    TimeoutError: (504, "REQUEST_TIMEOUT"),
}

# The base classes are never raised themselves; an instance would be internal by default.
BASES = {domain.DomainError, application.ApplicationError}


def _public_errors(*modules: ModuleType) -> set[type[BaseException]]:
    found: set[type[BaseException]] = set()
    for module in modules:
        for name, value in inspect.getmembers(module, inspect.isclass):
            if not name.startswith("_") and issubclass(value, BaseException):
                if value.__module__.startswith("provisioning."):
                    found.add(value)
    return found - BASES


def test_every_public_error_of_the_core_has_an_explicit_row() -> None:
    errors = _public_errors(domain, application, jobs, build_system_port, site_cluster_port)
    assert len(errors) > 40, "the scan found too few classes: it is not looking at the core"
    missing = []
    for cls in sorted(errors, key=lambda c: c.__name__):
        try:
            instance = cls()
        except TypeError:
            instance = cls.__new__(cls)
        row, explicit = classify(instance)
        if not explicit or row.error is not cls:
            missing.append(cls.__name__)
            continue
        want = SPEC.get(cls, (500, "INTERNAL_ERROR"))
        assert (row.status, row.code) == want, cls.__name__
    assert missing == []


def test_transport_errors_and_the_timeout_have_rows() -> None:
    for cls, (status, code) in SPEC.items():
        row, explicit = classify(cls())
        assert explicit, cls.__name__
        assert (row.status, row.code) == (status, code), cls.__name__


def _from(outer: BaseException, cause: BaseException) -> BaseException:
    try:
        raise outer from cause
    except BaseException as exc:
        return exc


def _inside(outer: BaseException, handled: BaseException) -> BaseException:
    """``outer`` raised while handling ``handled``: an implicit __context__, not a cause."""
    try:
        try:
            raise handled
        except BaseException:
            raise outer  # noqa: B904  # the implicit context is what this helper builds
    except BaseException as exc:
        return exc


@pytest.mark.parametrize(
    ("err", "code"),
    [
        (
            _from(ProjectAccessUnavailable(), DependencyNotConfigured()),
            "PROJECT_ACCESS_UNAVAILABLE",
        ),
        (_from(ProjectAccessUnavailable(), DependencyUnavailable()), "PROJECT_ACCESS_UNAVAILABLE"),
        (_from(DependencyUnavailable(), ProjectAccessUnavailable()), "PROJECT_ACCESS_UNAVAILABLE"),
        (_from(ProjectAccessUnavailable(), TimeoutError()), "PROJECT_ACCESS_UNAVAILABLE"),
        (_from(CorruptRow(), InvalidId()), "INTERNAL_ERROR"),
        (_from(CorruptRow(), UnknownEnumValue()), "INTERNAL_ERROR"),
        (
            OperationInProgress(UUID("0190a1b2-0000-7000-8000-0000000000c1")),
            "COMMAND_IN_PROGRESS",
        ),
        (_from(DependencyUnavailable(), TimeoutError()), "DEPENDENCY_UNAVAILABLE"),
        (ClaimLost(), "INTERNAL_ERROR"),
        (InvalidCorrelationId(), "INTERNAL_ERROR"),
        (PermanentFailure("candidate_failed"), "INTERNAL_ERROR"),
        (RetryLater("tenant_not_ready"), "INTERNAL_ERROR"),
        (RuntimeError("pq: connection refused at 10.0.0.1"), "INTERNAL_ERROR"),
        (_from(RuntimeError("accept deploy"), IdempotencyConflict()), "IDEMPOTENCY_KEY_REUSED"),
        # H4: an error raised while handling another does not take the other's class.
        (_inside(Timeout(), ProjectAccessDenied()), "REQUEST_TIMEOUT"),
        (_inside(RuntimeError("x"), NotFound()), "INTERNAL_ERROR"),
    ],
)
def test_classification_precedence(err: BaseException, code: str) -> None:
    assert classify(err)[0].code == code


def test_an_unknown_error_is_internal_and_not_explicit() -> None:
    row, explicit = classify(ValueError("anything"))
    assert not explicit
    assert (row.status, row.code) == (500, "INTERNAL_ERROR")


def test_codes_have_fixed_messages_of_their_own() -> None:
    internal = message(Code.INTERNAL_ERROR)
    for code in Code:
        text = message(code)
        assert text
        assert code is Code.INTERNAL_ERROR or text != internal, code
    assert len({message(code) for code in Code}) == len(Code)
    assert message("NO_SUCH_CODE") == internal


def test_the_table_is_immutable() -> None:
    assert isinstance(CLASSES, tuple)


def test_status_table_matches_notion() -> None:
    expected = {
        Code.VALIDATION_FAILED: 400,
        Code.ACCESS_DENIED: 403,
        Code.IDEMPOTENCY_KEY_REUSED: 422,
        Code.COMMAND_IN_PROGRESS: 409,
        Code.PUBLISH_LIMIT_REACHED: 422,
        Code.REQUEST_TIMEOUT: 504,
        Code.DEPENDENCY_TIMEOUT: 504,
        Code.RESOURCE_NOT_FOUND: 404,
        Code.REDEPLOY_NOT_ALLOWED: 422,
    }
    for code, status in expected.items():
        assert status_of(code) == status


def test_every_code_has_the_status_of_its_group() -> None:
    """The whole table of C.1.3 plus G10: no 429 (rate only, and none is limited here), 409 only
    for duplicates and stale changes, 422 for business rules."""
    groups = {
        400: {"VALIDATION_FAILED"},
        401: {"UNAUTHENTICATED"},
        403: {"ACCESS_DENIED"},
        404: {
            "PROJECT_NOT_FOUND",
            "RELEASE_NOT_FOUND",
            "COMMAND_NOT_FOUND",
            "DEPLOYMENT_NOT_FOUND",
            "RESOURCE_NOT_FOUND",
        },
        409: {"COMMAND_IN_PROGRESS", "BUILD_IN_PROGRESS", "CONCURRENT_CHANGE"},
        413: {"REQUEST_TOO_LARGE"},
        422: {
            "IDEMPOTENCY_KEY_REUSED",
            "ROUTE_NOT_STABLE",
            "HOSTING_OFFLINE",
            "RELEASE_STATE_INVALID",
            "RELEASE_OUTDATED",
            "RELEASE_NOT_BUILDABLE",
            "COMMAND_NUMBER_EXHAUSTED",
            "PUBLISH_LIMIT_REACHED",
            "REDEPLOY_NOT_ALLOWED",
        },
        500: {"INTERNAL_ERROR"},
        503: {
            "SERVICE_OVERLOADED",
            "DEPENDENCY_UNAVAILABLE",
            "PROJECT_ACCESS_UNAVAILABLE",
            "IDENTITY_UNAVAILABLE",
            "OPERATION_UNAVAILABLE",
        },
        504: {"REQUEST_TIMEOUT", "DEPENDENCY_TIMEOUT"},
    }
    assert {code.value for code in Code} == set().union(*groups.values())
    for status, codes in groups.items():
        for code in codes:
            assert status_of(Code(code)) == status, code
    for row in CLASSES:
        assert row.status == status_of(row.code), row.error


def test_codes_are_upper_snake_and_their_own_names() -> None:
    for code in Code:
        assert code.value == code.name
        assert code.value.replace("_", "").isalpha() and code.value.isupper()
    assert [f.value for f in FieldCode] == [
        "REQUIRED",
        "INVALID_FORMAT",
        "OUT_OF_RANGE",
        "TOO_LONG",
    ]


def test_field_error_never_contains_the_value() -> None:
    value = "caller-value-\u202e-zq"
    exc = MalformedRequest("bad id", errors=(FieldError("projectId", FieldCode.INVALID_FORMAT),))
    entry = exc.errors[0]
    assert entry.as_json() == {
        "field": "projectId",
        "code": "INVALID_FORMAT",
        "message": field_message(FieldCode.INVALID_FORMAT),
    }
    assert value not in str(entry.as_json())
    assert len({field_message(code) for code in FieldCode}) == len(FieldCode)
    with pytest.raises(TypeError):
        FieldError("projectId", "INVALID_FORMAT")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="field"):
        FieldError("", FieldCode.REQUIRED)


def test_grpc_deadline_is_504_dependency_timeout() -> None:
    inner = DependencyUnavailable("payment: DEADLINE_EXCEEDED", deadline=True)
    for exc in (inner, _from(DependencyUnavailable("wrapped"), inner)):
        row, _ = classify(exc)
        assert status_and_code(row, exc) == (504, "DEPENDENCY_TIMEOUT")
    plain = DependencyUnavailable("down")
    assert status_and_code(classify(plain)[0], plain) == (503, "DEPENDENCY_UNAVAILABLE")
    assert plain.upstream_reason is None and plain.deadline is False


@pytest.mark.parametrize(
    ("reason", "deadline"),
    [
        (None, False),
        (None, True),
        ("PROJECT_ARCHIVED", False),
        ("PROJECT_ARCHIVED", True),
        # Project's denial reasons: each would tell a project that exists from one that does not.
        ("NOT_MANAGER", False),
        ("PROJECT_DELETED", False),
        ("PROJECT_SUSPENDED", False),
        ("NOT_FOUND", False),
        ("PUBLISH_LIMIT_REACHED", False),
    ],
)
def test_a_project_access_failure_never_passes_anything_on(
    reason: str | None, deadline: bool
) -> None:
    """Lead ruling (Task 2 review): an access outage looks the same for every project, a
    deadline included, whatever reason Project sent."""
    inner = ProjectAccessUnavailable("project service", upstream_reason=reason, deadline=deadline)
    for exc in (
        inner,
        _from(ProjectAccessUnavailable("project access unavailable"), inner),
        _from(DependencyUnavailable("x"), inner),
        _from(ProjectAccessUnavailable("x"), DependencyUnavailable("y", upstream_reason="Z_DOWN")),
    ):
        row, _ = classify(exc)
        assert status_and_code(row, exc) == (503, "PROJECT_ACCESS_UNAVAILABLE"), (reason, exc)


@pytest.mark.parametrize(
    ("reason", "deadline", "want"),
    [
        # Any other passed reason: 503 with the reason.
        ("PROJECT_ARCHIVED", False, (503, "PROJECT_ARCHIVED")),
        ("PAYMENT_PROVIDER_DOWN", False, (503, "PAYMENT_PROVIDER_DOWN")),
        # One of our codes: our status for it.
        ("PUBLISH_LIMIT_REACHED", False, (422, "PUBLISH_LIMIT_REACHED")),
        ("SERVICE_OVERLOADED", False, (503, "SERVICE_OVERLOADED")),
        ("CONCURRENT_CHANGE", True, (409, "CONCURRENT_CHANGE")),
        # A deadline: 504, with the reason when it may be passed on.
        ("PAYMENT_SLOW", True, (504, "PAYMENT_SLOW")),
        (None, True, (504, "DEPENDENCY_TIMEOUT")),
        ("PROJECT_NOT_FOUND", True, (504, "DEPENDENCY_TIMEOUT")),
        # Concealment (G6 wins over G14): a reason ending in NOT_FOUND is never passed on.
        ("PROJECT_NOT_FOUND", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("RELEASE_NOT_FOUND", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("COMMAND_NOT_FOUND", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("DEPLOYMENT_NOT_FOUND", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("NOT_FOUND", False, (503, "DEPENDENCY_UNAVAILABLE")),
        # Not UPPER_SNAKE, or too long: our own code.
        ("project_archived", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("Project-Archived", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("_LEADING", False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("A" * 101, False, (503, "DEPENDENCY_UNAVAILABLE")),
        ("", False, (503, "DEPENDENCY_UNAVAILABLE")),
        (None, False, (503, "DEPENDENCY_UNAVAILABLE")),
    ],
)
def test_grpc_upstream_reason_becomes_the_code_unless_it_reveals_a_project(
    reason: str | None, deadline: bool, want: tuple[int, str]
) -> None:
    inner = DependencyUnavailable("payment", upstream_reason=reason, deadline=deadline)
    exc = _from(DependencyUnavailable("wrapped"), inner)
    row, _ = classify(exc)
    assert status_and_code(row, exc) == want


@pytest.mark.parametrize(
    ("reason", "deadline", "declared", "want"),
    [
        ("PUBLISH_LIMIT_REACHED", False, {503, 504}, (503, "DEPENDENCY_UNAVAILABLE")),
        ("CONCURRENT_CHANGE", True, {503, 504}, (504, "DEPENDENCY_TIMEOUT")),
        ("PAYMENT_SLOW", True, {503}, (504, "DEPENDENCY_TIMEOUT")),
        ("PAYMENT_DOWN", False, {504}, (503, "DEPENDENCY_UNAVAILABLE")),
        ("PUBLISH_LIMIT_REACHED", False, {422}, (422, "PUBLISH_LIMIT_REACHED")),
    ],
)
def test_an_undeclared_upstream_status_falls_back_to_our_own_code(
    reason: str, deadline: bool, declared: set[int], want: tuple[int, str]
) -> None:
    """Never a remap to INTERNAL_ERROR: our own code and status stand in."""
    exc = DependencyUnavailable("payment", upstream_reason=reason, deadline=deadline)
    row, _ = classify(exc)
    assert status_and_code(row, exc, lambda status, _code: status in declared) == want


def test_the_upstream_reason_applies_only_to_the_dependency_classes() -> None:
    inner = DependencyUnavailable("x", upstream_reason="PROJECT_ARCHIVED", deadline=True)
    exc = _from(CorruptRow("bad row"), inner)
    row, _ = classify(exc)
    assert status_and_code(row, exc) == (500, "INTERNAL_ERROR")


def test_a_redeploy_that_is_not_allowed_is_422_redeploy_not_allowed() -> None:
    row, explicit = classify(_from(RuntimeError("store"), RedeployNotAllowed("offline")))
    assert explicit
    assert (row.status, row.code) == (422, "REDEPLOY_NOT_ALLOWED")


def test_an_admin_key_that_is_too_long_is_a_field_error_of_the_key() -> None:
    """Decision 0023 G11: the stored key is admin:<key>, so the key is at most 249 bytes; the
    field is named as the transport names the key, and the value never appears."""
    exc = IdempotencyKeyTooLong("an admin key is at most 249 bytes")
    row, _ = classify(exc)
    assert (row.status, row.code) == (400, "VALIDATION_FAILED")
    assert field_errors(exc, key_field="Idempotency-Key") == (
        FieldError("Idempotency-Key", FieldCode.TOO_LONG),
    )
    assert field_errors(exc, key_field="idempotencyKey") == (
        FieldError("idempotencyKey", FieldCode.TOO_LONG),
    )
    assert field_errors(RedeployNotAllowed("x"), key_field="idempotencyKey") == ()


def test_the_key_field_has_no_default() -> None:
    """The key's name is the transport's (REST's header, MCP's argument), so the caller always
    says it: there is no REST default an MCP path could fall into."""
    with pytest.raises(TypeError):
        field_errors(IdempotencyKeyTooLong("x"))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        field_errors(IdempotencyKeyTooLong("x"), "idempotencyKey")  # type: ignore[call-arg]
