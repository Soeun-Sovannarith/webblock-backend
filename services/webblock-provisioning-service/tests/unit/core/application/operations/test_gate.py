"""Registering the operations' handlers on the buses and the fixed check order of ``OperationGate``
every operation passes (decisions 0004 section 1 and 0006 section 6), for the client operations
and the admin-only actions, each sent as its command or query (decision 0025 CQ8, TR-54)."""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from tests.unit.core.application.operations.fakes import (
    CALL_ACCEPT,
    CALL_ACCESS,
    CALL_CLOCK,
    CALL_FIND,
    CALL_ID,
    CALL_POLICY,
    ID_COMMAND,
    ID_COMMAND2,
    ID_PROJECT,
    ID_RELEASE,
    T0,
    T1,
    CallLog,
    FakeClock,
    OperationsFixture,
    project_hosting,
    release_in,
    requester_principal,
)

from provisioning.core.application import (
    ADMIN_ROLE,
    AcceptanceFacts,
    Action,
    ActionDenied,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    BuildReleaseCommand,
    CommandBus,
    DependencyUnavailable,
    DeployReleaseCommand,
    EnabledOperations,
    GetCommandQuery,
    GetDeploymentQuery,
    GetReleaseDiagnosticsQuery,
    GetReleaseQuery,
    ListCommandsQuery,
    ListReleasesQuery,
    NotFound,
    OperationDisabled,
    OperationGate,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    PromoteReleaseCommand,
    QueryBus,
    RollbackReleaseCommand,
    ServiceAudit,
    register_operations,
)
from provisioning.core.domain import (
    NIL_ID,
    CommandKind,
    IllegalTransition,
    InvalidId,
    ReleaseStatus,
    RoutingStatus,
    is_error,
)

# ---- fixtures ----


type _Mutation = DeployReleaseCommand | PromoteReleaseCommand | RollbackReleaseCommand


def _mutation[C: _Mutation](
    kind: type[C], p: Principal, *, actor_kind: AuditActorKind = AuditActorKind.USER
) -> C:
    """``kind`` of ``p`` on ID_RELEASE of ID_PROJECT under key-1 and corr-1."""
    return kind(
        principal=p,
        project_id=ID_PROJECT,
        release_id=ID_RELEASE,
        idempotency_key="key-1",
        correlation_id="corr-1",
        actor_kind=actor_kind,
    )


# One of the client operations, sent with a principal and a valid request. Rollback is admin-only
# (FR-50) and is tested with the admin-only actions below.
_Op = Callable[[OperationsFixture, Principal], Awaitable[object]]


async def _op_deploy(f: OperationsFixture, p: Principal) -> object:
    return await f.commands.send(_mutation(DeployReleaseCommand, p))


async def _op_promote(f: OperationsFixture, p: Principal) -> object:
    return await f.commands.send(_mutation(PromoteReleaseCommand, p))


async def _op_get_release(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(GetReleaseQuery(p, project_id=ID_PROJECT, release_id=ID_RELEASE))


async def _op_get_command(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(GetCommandQuery(p, project_id=ID_PROJECT, command_id=ID_COMMAND))


async def _op_get_deployment(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(GetDeploymentQuery(p, project_id=ID_PROJECT))


async def _op_get_release_diagnostics(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(
        GetReleaseDiagnosticsQuery(p, project_id=ID_PROJECT, release_id=ID_RELEASE)
    )


async def _op_build_release(f: OperationsFixture, p: Principal) -> object:
    return await f.commands.send(
        BuildReleaseCommand(p, project_id=ID_PROJECT, release_id=ID_RELEASE)
    )


_OPERATIONS: tuple[tuple[Action, _Op], ...] = (
    (Action.DEPLOY_RELEASE, _op_deploy),
    (Action.PROMOTE_RELEASE, _op_promote),
    (Action.GET_RELEASE, _op_get_release),
    (Action.GET_COMMAND, _op_get_command),
    (Action.GET_DEPLOYMENT, _op_get_deployment),
    (Action.GET_RELEASE_DIAGNOSTICS, _op_get_release_diagnostics),
    (Action.BUILD_RELEASE, _op_build_release),
)
_OPERATION_IDS = [action.value for action, _ in _OPERATIONS]


# ---- tests ----


# Registering refuses a command deadline that is not positive, and calls no port.
@pytest.mark.parametrize(
    "deadline", [timedelta(0), -timedelta(seconds=1)], ids=["zero deadline", "negative deadline"]
)
def test_register_operations_refuses_a_deadline_that_is_not_positive(deadline: timedelta) -> None:
    f = OperationsFixture()
    with pytest.raises(ValueError):
        register_operations(
            CommandBus(), QueryBus(), dataclasses.replace(f.deps(), command_deadline=deadline)
        )
    assert f.log.calls == []


# An empty set of enabled operations is a build that exposes nothing, not an error.
def test_register_operations_accepts_an_empty_set_of_enabled_operations() -> None:
    f = OperationsFixture()
    commands, queries = CommandBus(), QueryBus()
    register_operations(
        commands, queries, dataclasses.replace(f.deps(), enabled=EnabledOperations())
    )
    assert len(commands.kinds()) + len(queries.kinds()) == len(Action)
    assert f.log.calls == []


def _no_arrangement(f: OperationsFixture, action: Action) -> None:
    pass


def _deny_action(f: OperationsFixture, action: Action) -> None:
    f.policy.err = ActionDenied()


def _break_policy(f: OperationsFixture, action: Action) -> None:
    f.policy.err = RuntimeError("scope mapping broken")


def _switch_off(f: OperationsFixture, action: Action) -> None:
    f.enabled = EnabledOperations(a for a in Action if a is not action)
    f.build()


def _deny_access(f: OperationsFixture, action: Action) -> None:
    f.access.err = ProjectAccessDenied()


def _access_down(f: OperationsFixture, action: Action) -> None:
    f.access.err = DependencyUnavailable("project service")


# (name, arrange, principal, wanted error, calls)
_CHECK_STEPS: tuple[
    tuple[str, Callable[[OperationsFixture, Action], None], Principal, type[Exception], list[str]],
    ...,
] = (
    ("zero principal", _no_arrangement, Principal(), ActionDenied, []),
    ("action denied", _deny_action, requester_principal(), ActionDenied, [CALL_POLICY]),
    ("policy fails otherwise", _break_policy, requester_principal(), ActionDenied, [CALL_POLICY]),
    (
        "operation switched off",
        _switch_off,
        requester_principal(),
        OperationDisabled,
        [CALL_POLICY],
    ),
    (
        "no access to the project",
        _deny_access,
        requester_principal(),
        ProjectAccessDenied,
        [CALL_POLICY, CALL_ACCESS],
    ),
    (
        "project access unavailable",
        _access_down,
        requester_principal(),
        ProjectAccessUnavailable,
        [CALL_POLICY, CALL_ACCESS],
    ),
)


# Every operation checks, in this order and in one place: action permission, operation gate,
# project access, validation, use case (decisions 0004 section 1 and 0006 section 6). Each failure
# prevents every later call, which the shared call log shows.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "op", "step"),
    [(action, op, step) for action, op in _OPERATIONS for step in _CHECK_STEPS],
    ids=[f"{action.value}/{step[0]}" for action, _ in _OPERATIONS for step in _CHECK_STEPS],
)
async def test_check_order_stops_at_the_first_failure(
    action: Action,
    op: _Op,
    step: tuple[
        str, Callable[[OperationsFixture, Action], None], Principal, type[Exception], list[str]
    ],
) -> None:
    _, arrange, principal, want, calls = step
    f = OperationsFixture()
    arrange(f, action)
    with pytest.raises(want):
        await op(f, principal)
    assert f.log.calls == calls


# The policy is asked about the operation's own action with the caller's principal, and project
# access receives the principal, the request's project and the action.
@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "op"), _OPERATIONS, ids=_OPERATION_IDS)
async def test_checks_receive_the_principal_project_and_action(action: Action, op: _Op) -> None:
    f = OperationsFixture()
    f.reads.err = NotFound()  # stop the reads after authorization
    p = requester_principal()
    # The outcome after the checks does not matter here: a read fails NotFound,
    # diagnostics is not available, and a mutation's decision depends on the fixture's facts.
    with contextlib.suppress(Exception):
        await op(f, p)
    assert f.policy.actions == [action]
    assert [q.subject for q in f.policy.principals] == [p.subject]
    assert f.access.actions == [action]
    assert f.access.projects == [ID_PROJECT]
    assert [q.subject for q in f.access.principals] == [p.subject]


# A switched-off operation asks nobody: not project access, not entitlement, no store, no clock, no
# ID source, no build. The empty EnabledOperations switches everything off.
@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "op"), _OPERATIONS, ids=_OPERATION_IDS)
async def test_switched_off_operation_asks_nobody(action: Action, op: _Op) -> None:
    f = OperationsFixture()
    f.enabled = EnabledOperations()
    f.build()
    with pytest.raises(OperationDisabled):
        await op(f, requester_principal())
    assert f.log.calls == [CALL_POLICY]


# The forbidden answer depends on the token alone (security review L1): whether the project exists
# or the caller has access to it makes no difference, because project access is never asked.
@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "op"), _OPERATIONS, ids=_OPERATION_IDS)
async def test_forbidden_does_not_depend_on_the_project(action: Action, op: _Op) -> None:
    texts: list[str] = []
    for access_err in (None, ProjectAccessDenied()):
        f = OperationsFixture()
        f.policy.err = ActionDenied()
        f.access.err = access_err
        with pytest.raises(ActionDenied) as exc_info:
            await op(f, requester_principal())
        assert not is_error(exc_info.value, ProjectAccessDenied)
        assert f.access.projects == [], "project access was asked before the action permission"
        texts.append(str(exc_info.value))
    assert texts[0] == texts[1]


async def _zero_project_deploy(f: OperationsFixture) -> object:
    return await f.commands.send(
        DeployReleaseCommand(
            requester_principal(),
            project_id=NIL_ID,
            release_id=ID_RELEASE,
            idempotency_key="k",
            correlation_id="c",
        )
    )


async def _zero_project_promote(f: OperationsFixture) -> object:
    return await f.commands.send(
        PromoteReleaseCommand(
            requester_principal(),
            project_id=NIL_ID,
            release_id=ID_RELEASE,
            idempotency_key="k",
            correlation_id="c",
        )
    )


async def _zero_project_get_release(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseQuery(requester_principal(), project_id=NIL_ID, release_id=ID_RELEASE)
    )


async def _zero_project_get_command(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetCommandQuery(requester_principal(), project_id=NIL_ID, command_id=ID_RELEASE)
    )


async def _zero_project_get_deployment(f: OperationsFixture) -> object:
    return await f.queries.send(GetDeploymentQuery(requester_principal(), project_id=NIL_ID))


async def _zero_project_diagnostics(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseDiagnosticsQuery(requester_principal(), project_id=NIL_ID, release_id=ID_RELEASE)
    )


async def _zero_project_build(f: OperationsFixture) -> object:
    return await f.commands.send(
        BuildReleaseCommand(requester_principal(), project_id=NIL_ID, release_id=ID_RELEASE)
    )


# The nil project ID is invalid input: no other service is asked about it.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        _zero_project_deploy,
        _zero_project_promote,
        _zero_project_get_release,
        _zero_project_get_command,
        _zero_project_get_deployment,
        _zero_project_diagnostics,
        _zero_project_build,
    ],
    ids=[
        "deploy",
        "promote",
        "get_release",
        "get_command",
        "get_deployment",
        "diagnostics",
        "build_release",
    ],
)
async def test_zero_project_id_is_invalid_before_project_access(
    call: Callable[[OperationsFixture], Awaitable[object]],
) -> None:
    f = OperationsFixture()
    with pytest.raises(InvalidId):
        await call(f)
    assert f.log.calls == [CALL_POLICY]


def _zero_target_mutation[C: _Mutation](kind: type[C], p: Principal) -> C:
    return dataclasses.replace(_mutation(kind, p), release_id=NIL_ID)


async def _zero_target_deploy(f: OperationsFixture) -> object:
    return await f.commands.send(_zero_target_mutation(DeployReleaseCommand, requester_principal()))


async def _zero_target_promote(f: OperationsFixture) -> object:
    return await f.commands.send(
        _zero_target_mutation(PromoteReleaseCommand, requester_principal())
    )


async def _zero_target_get_release(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=NIL_ID)
    )


async def _zero_target_get_command(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=NIL_ID)
    )


async def _zero_target_diagnostics(f: OperationsFixture) -> object:
    return await f.queries.send(
        GetReleaseDiagnosticsQuery(requester_principal(), project_id=ID_PROJECT, release_id=NIL_ID)
    )


async def _zero_target_build(f: OperationsFixture) -> object:
    return await f.commands.send(
        BuildReleaseCommand(requester_principal(), project_id=ID_PROJECT, release_id=NIL_ID)
    )


# A nil target ID (release or command) is InvalidId after authorization and before any store.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        _zero_target_deploy,
        _zero_target_promote,
        _zero_target_get_release,
        _zero_target_get_command,
        _zero_target_diagnostics,
        _zero_target_build,
    ],
    ids=[
        "deploy",
        "promote",
        "get_release",
        "get_command",
        "diagnostics",
        "build_release",
    ],
)
async def test_zero_target_id_is_invalid_after_authorization(
    call: Callable[[OperationsFixture], Awaitable[object]],
) -> None:
    f = OperationsFixture()
    with pytest.raises(InvalidId):
        await call(f)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS]


# ---- an ADMIN acts on any site (decision 0019 section 7, OD-45; owner, 18 October 2026) ----

ADMIN_SUBJECT = "admin-1"


def _admin(*roles: str) -> Principal:
    """A verified principal holding the fixture's scope and ``roles`` (the roles claim)."""
    return Principal(subject=ADMIN_SUBJECT, scopes=("provisioning", *roles))


def _uncertain_route(f: OperationsFixture) -> None:
    """Acceptance facts a rollback is accepted on: an uncertain route, a promoting release."""
    hosting = dataclasses.replace(project_hosting(4), routing_status=RoutingStatus.UNCERTAIN)
    f.acceptance.facts = [
        AcceptanceFacts(hosting=hosting, target=release_in(ID_RELEASE, ReleaseStatus.PROMOTING, T0))
    ]


def test_the_admin_role_is_the_owners_case_sensitive_name() -> None:
    assert ADMIN_ROLE == "ADMIN"


@pytest.mark.asyncio
async def test_an_admin_rolls_back_a_project_they_do_not_own_without_project_access() -> None:
    """FR-50.1: an ADMIN's rollback is accepted for a project the Project service would deny
    them: project access is never asked, and the command records the admin's subject."""
    f = OperationsFixture()
    _uncertain_route(f)
    f.access.err = ProjectAccessDenied()  # not the owner
    accepted = await f.commands.send(_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))
    assert f.access.projects == []
    assert CALL_ACCESS not in f.log.calls
    assert f.log.calls == [CALL_POLICY, CALL_FIND, CALL_ID, CALL_CLOCK, CALL_ACCEPT]
    assert accepted.command_id == ID_COMMAND2
    [params] = f.acceptance.params
    assert params.requested_by_subject == ADMIN_SUBJECT
    [decision] = f.acceptance.decisions
    assert decision.command.requested_by_subject == ADMIN_SUBJECT
    assert decision.command.kind == CommandKind.ROLLBACK


async def _op_list_releases(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(
        ListReleasesQuery(p, project_id=ID_PROJECT, page_size=10, after=None)
    )


async def _op_list_commands(f: OperationsFixture, p: Principal) -> object:
    return await f.queries.send(
        ListCommandsQuery(p, project_id=ID_PROJECT, page_size=10, after=None)
    )


# Every operation that is not admin-only (review finding M-3: the two listings included).
_ADMIN_OTHER_OPERATIONS: tuple[tuple[Action, _Op], ...] = (
    *_OPERATIONS,
    (Action.LIST_RELEASES, _op_list_releases),
    (Action.LIST_COMMANDS, _op_list_commands),
)


def test_the_admins_other_operations_are_every_action_that_is_not_admin_only() -> None:
    assert sorted(a for a, _ in _ADMIN_OTHER_OPERATIONS) == sorted(
        a for a in Action if not a.is_admin_only()
    )


# Every other operation of an ADMIN still asks project access, and a denial still conceals.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "op"),
    _ADMIN_OTHER_OPERATIONS,
    ids=[a.value for a, _ in _ADMIN_OTHER_OPERATIONS],
)
async def test_an_admins_other_operations_keep_the_owner_check(action: Action, op: _Op) -> None:
    f = OperationsFixture()
    f.access.err = ProjectAccessDenied()
    with pytest.raises(ProjectAccessDenied):
        await op(f, _admin(ADMIN_ROLE))
    assert f.access.actions == [action]
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False], ids=["owner", "not the owner"])
@pytest.mark.parametrize(
    "roles",
    [(), ("USER",), ("admin",), ("Admin",), ("ADMIN ",), ("ADMINS",), ("USER", "admin")],
    ids=["no role", "USER", "admin", "Admin", "trailing space", "ADMINS", "USER and admin"],
)
async def test_fr50_2_a_caller_without_admin_is_denied_a_rollback(
    roles: tuple[str, ...], owner: bool
) -> None:
    """FR-50.2: without the exact ADMIN role (case-sensitive, compared whole; decision 0019
    section 3, A5) a rollback is ActionDenied (403 ACCESS_DENIED), even for the owner and even
    when the policy permits the action: Project access is not asked and nothing is stored."""
    f = OperationsFixture()
    _uncertain_route(f)
    if not owner:
        f.access.err = ProjectAccessDenied()
    with pytest.raises(ActionDenied):
        await f.commands.send(_mutation(RollbackReleaseCommand, _admin(*roles)))
    assert f.log.calls == [CALL_POLICY]
    assert f.access.projects == []
    assert f.acceptance.params == []
    assert f.acceptance.decisions == []


# The skip comes after the action permission: an ADMIN without the rollback scope is refused.
@pytest.mark.asyncio
async def test_an_admin_without_the_rollback_permission_is_refused() -> None:
    f = OperationsFixture()
    f.policy.err = ActionDenied()
    with pytest.raises(ActionDenied):
        await f.commands.send(_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))
    assert f.log.calls == [CALL_POLICY]


# Production refuses every mutation (hosts.shared.open_contracts): a switched-off rollback refuses
# an ADMIN at the operation gate, before the skip, and nothing else is asked.
@pytest.mark.asyncio
async def test_a_switched_off_rollback_refuses_an_admin() -> None:
    f = OperationsFixture()
    f.enabled = EnabledOperations(
        (a for a in Action if not a.is_mutation()),
        refused={a: ("OD-04",) for a in Action if a.is_mutation()},
    )
    f.build()
    with pytest.raises(OperationDisabled):
        await f.commands.send(_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))
    assert f.log.calls == [CALL_POLICY]


# The nil project ID is still invalid input for an ADMIN.
@pytest.mark.asyncio
async def test_an_admins_rollback_of_a_nil_release_is_invalid_before_any_store() -> None:
    f = OperationsFixture()
    with pytest.raises(InvalidId):
        await f.commands.send(_zero_target_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))
    assert f.log.calls == [CALL_POLICY]


@pytest.mark.asyncio
async def test_an_admins_rollback_of_the_nil_project_is_invalid() -> None:
    f = OperationsFixture()
    with pytest.raises(InvalidId):
        await f.commands.send(
            RollbackReleaseCommand(
                _admin(ADMIN_ROLE),
                project_id=NIL_ID,
                release_id=ID_RELEASE,
                idempotency_key="k",
                correlation_id="c",
            )
        )
    assert f.log.calls == [CALL_POLICY]


# ---- the admin-only actions: rollback (FR-50) and the four of 0023 G10 ----

_ADMIN_ONLY_ACTIONS = (
    Action.ROLLBACK_RELEASE,
    Action.SUSPEND_WEBSITE,
    Action.RESUME_WEBSITE,
    Action.REDEPLOY_RELEASE,
    Action.ADMIN_LIST_COMMANDS,
)


def test_fr50_the_admin_only_actions_are_rollback_and_the_four_of_g10() -> None:
    """FR-50: rollback_release is admin-only, with the four actions of 0023 G10."""
    assert [a.value for a in _ADMIN_ONLY_ACTIONS] == [
        "rollback_release",
        "suspend_website",
        "resume_website",
        "redeploy_release",
        "admin_list_commands",
    ]
    assert [a for a in Action if a.is_admin_only()] == list(_ADMIN_ONLY_ACTIONS)


# An ADMIN passes the gate for any project and Project's access check is never called.
@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ADMIN_ONLY_ACTIONS, ids=[a.value for a in _ADMIN_ONLY_ACTIONS])
async def test_an_admin_passes_an_admin_only_action_without_a_project_access_call(
    action: Action,
) -> None:
    """FR-4.1: an admin passes an admin only action without a project access call."""
    f = OperationsFixture()
    f.access.err = ProjectAccessDenied()  # would refuse, if it were asked
    skipped = await OperationGate(policy=f.policy, access=f.access, enabled=f.enabled).check(
        _admin(ADMIN_ROLE), action, ID_PROJECT
    )
    assert skipped is True
    assert f.access.projects == []
    assert f.log.calls == [CALL_POLICY]


# A caller without ADMIN is refused with ActionDenied, even when the policy permits the action
# (a misconfigured AUTH_ACTION_SCOPES), and no port but the policy is called.
@pytest.mark.asyncio
@pytest.mark.parametrize("action", _ADMIN_ONLY_ACTIONS, ids=[a.value for a in _ADMIN_ONLY_ACTIONS])
@pytest.mark.parametrize(
    "roles",
    [(), ("USER",), ("admin",), ("ADMIN ",), ("ADMINS",)],
    ids=["no role", "USER", "admin", "trailing space", "ADMINS"],
)
async def test_a_caller_without_admin_is_denied_an_admin_only_action(
    action: Action, roles: tuple[str, ...]
) -> None:
    """FR-4.2: a caller without admin is denied an admin only action."""
    f = OperationsFixture()
    with pytest.raises(ActionDenied):
        await OperationGate(policy=f.policy, access=f.access, enabled=f.enabled).check(
            _admin(*roles), action, ID_PROJECT
        )
    assert f.access.projects == []
    assert f.log.calls == [CALL_POLICY]


# ---- review finding M-2: the audit event of the skip names an operator ----


class _AuditLog:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("an accepted command raises no alert")


def _audited(f: OperationsFixture) -> _AuditLog:
    """Give the fixture a recording audit log (a clock outside the call log) and rebuild."""
    log = _AuditLog()
    f.audit = ServiceAudit(log=log, clock=FakeClock(CallLog(), T1))
    f.build()
    return log


# An ADMIN's rollback without the owner check is recorded as an operator's, with the verified
# subject as the actor, whichever transport it came through.
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", [AuditActorKind.USER, AuditActorKind.AGENT])
async def test_the_admin_skip_is_audited_as_an_operator(transport: AuditActorKind) -> None:
    f = OperationsFixture()
    _uncertain_route(f)
    log = _audited(f)
    await f.commands.send(
        _mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE), actor_kind=transport)
    )
    [event] = log.events
    assert event.actor_kind is AuditActorKind.OPERATOR
    assert event.actor == ADMIN_SUBJECT
    assert event.result is AuditResult.ACCEPTED
    assert event.command_id == ID_COMMAND2


# A refusal after the skip (a rollback while the route is stable) is an operator's too.
@pytest.mark.asyncio
async def test_a_refusal_after_the_admin_skip_is_audited_as_an_operator() -> None:
    f = OperationsFixture()  # the route is stable: a rollback is refused after the gate
    log = _audited(f)
    with pytest.raises(IllegalTransition):
        await f.commands.send(_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))
    [event] = log.events
    assert event.actor_kind is AuditActorKind.OPERATOR
    assert event.actor == ADMIN_SUBJECT
    assert event.result is AuditResult.REFUSED


# Without the skip the transport's kind stays: an owner refused a rollback (FR-50.2), an ADMIN
# refused before the skip (no rollback permission, or the operation switched off), and an ADMIN's
# other mutations.
@pytest.mark.asyncio
async def test_without_the_skip_the_audit_keeps_the_transports_kind() -> None:
    owner = OperationsFixture()
    _uncertain_route(owner)
    owner_log = _audited(owner)
    with pytest.raises(ActionDenied):
        await owner.commands.send(_mutation(RollbackReleaseCommand, requester_principal()))

    denied = OperationsFixture()
    denied.policy.err = ActionDenied()
    denied_log = _audited(denied)
    with pytest.raises(ActionDenied):
        await denied.commands.send(
            _mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE), actor_kind=AuditActorKind.AGENT)
        )

    off = OperationsFixture()
    off.enabled = EnabledOperations(a for a in Action if a is not Action.ROLLBACK_RELEASE)
    off_log = _audited(off)
    with pytest.raises(OperationDisabled):
        await off.commands.send(_mutation(RollbackReleaseCommand, _admin(ADMIN_ROLE)))

    deploy = OperationsFixture()
    deploy_log = _audited(deploy)
    await deploy.commands.send(_mutation(DeployReleaseCommand, _admin(ADMIN_ROLE)))

    kinds = [log.events[0].actor_kind for log in (owner_log, denied_log, off_log, deploy_log)]
    assert kinds == [
        AuditActorKind.USER,
        AuditActorKind.AGENT,
        AuditActorKind.USER,
        AuditActorKind.USER,
    ]
    assert deploy.access.actions == [Action.DEPLOY_RELEASE]
