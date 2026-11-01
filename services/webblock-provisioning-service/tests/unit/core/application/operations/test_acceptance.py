"""The acceptance of deploy, promote and rollback commands (decision 0004 section 4) sent through
the command bus, against recording doubles."""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable

import pytest
from tests.unit.core.application.operations.fakes import (
    CALL_ACCEPT,
    CALL_ACCESS,
    CALL_CLOCK,
    CALL_ENTITLEMENT,
    CALL_FIND,
    CALL_ID,
    CALL_POLICY,
    DEADLINE,
    ID_COMMAND,
    ID_COMMAND2,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    REQUESTER,
    T0,
    T1,
    OperationsFixture,
    assert_internal,
    command_for,
    project_hosting,
    release_in,
    requester_principal,
)

from provisioning.core.application import (
    ADMIN_ROLE,
    MAX_CORRELATION_ID_LEN,
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    Action,
    CommandAccepted,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    DeployReleaseCommand,
    LimitReached,
    NotFound,
    OperationInProgress,
    Principal,
    ProjectAccessUnavailable,
    PromoteReleaseCommand,
    RollbackReleaseCommand,
    Unauthenticated,
    in_flight_command_id,
)
from provisioning.core.domain import (
    NIL_ID,
    CommandKind,
    CommandRequest,
    CommandStatus,
    IdempotencyConflict,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidRequester,
    ReleaseStatus,
    RoutingStatus,
    is_error,
)

# ---- fixtures ----


type _MutationCommand = DeployReleaseCommand | PromoteReleaseCommand | RollbackReleaseCommand


def _mutation_request() -> DeployReleaseCommand:
    """The fields of a mutation on ID_RELEASE of ID_PROJECT under key-1 and corr-1, as a deploy;
    ``_as`` sends them as another mutation and principal."""
    return DeployReleaseCommand(
        principal=Principal(),
        project_id=ID_PROJECT,
        release_id=ID_RELEASE,
        idempotency_key="key-1",
        correlation_id="corr-1",
    )


def _as[C: _MutationCommand](kind: type[C], p: Principal, req: DeployReleaseCommand) -> C:
    """``req``'s fields as a ``kind`` command of ``p``."""
    return kind(
        principal=p,
        project_id=req.project_id,
        release_id=req.release_id,
        idempotency_key=req.idempotency_key,
        correlation_id=req.correlation_id,
        actor_kind=req.actor_kind,
    )


# ---- mutations ----

_Call = Callable[[OperationsFixture, Principal, DeployReleaseCommand], Awaitable[CommandAccepted]]


async def _deploy(f: OperationsFixture, p: Principal, req: DeployReleaseCommand) -> CommandAccepted:
    return await f.commands.send(_as(DeployReleaseCommand, p, req))


async def _promote(
    f: OperationsFixture, p: Principal, req: DeployReleaseCommand
) -> CommandAccepted:
    return await f.commands.send(_as(PromoteReleaseCommand, p, req))


async def _rollback(
    f: OperationsFixture, p: Principal, req: DeployReleaseCommand
) -> CommandAccepted:
    """An ADMIN's rollback (FR-50): ``p`` with the ADMIN role, so Project access is not asked."""
    admin = dataclasses.replace(p, scopes=(*p.scopes, ADMIN_ROLE))
    return await f.commands.send(_as(RollbackReleaseCommand, admin, req))


def _deploy_facts() -> AcceptanceFacts:
    return AcceptanceFacts(
        hosting=project_hosting(4), target=release_in(ID_RELEASE, ReleaseStatus.BUILT, T0)
    )


def _promote_facts() -> AcceptanceFacts:
    return AcceptanceFacts(
        hosting=project_hosting(4),
        target=release_in(ID_RELEASE, ReleaseStatus.CANDIDATE_READY, T0),
    )


def _rollback_facts() -> AcceptanceFacts:
    return AcceptanceFacts(
        hosting=dataclasses.replace(project_hosting(4), routing_status=RoutingStatus.UNCERTAIN),
        target=release_in(ID_RELEASE, ReleaseStatus.PROMOTING, T0),
    )


@dataclasses.dataclass(frozen=True)
class _Mutation:
    """One of the three mutations with the command kind it accepts and whether it asks Entitlement
    at acceptance (deploy and promote only; decision 0004 section 2). ``facts`` are acceptance
    facts under which the kind's preconditions hold, at command number 4."""

    action: Action
    kind: CommandKind
    entitlement: bool
    call: _Call
    facts: Callable[[], AcceptanceFacts]

    def checks(self) -> list[str]:
        """The gate's calls: the action permission, then Project access unless the action is
        admin-only (an ADMIN's rollback asks no Project access, FR-50.1)."""
        if self.action.is_admin_only():
            return [CALL_POLICY]
        return [CALL_POLICY, CALL_ACCESS]


_MUTATIONS = (
    _Mutation(Action.DEPLOY_RELEASE, CommandKind.DEPLOY, True, _deploy, _deploy_facts),
    _Mutation(Action.PROMOTE_RELEASE, CommandKind.PROMOTE, True, _promote, _promote_facts),
    _Mutation(Action.ROLLBACK_RELEASE, CommandKind.ROLLBACK, False, _rollback, _rollback_facts),
)
_MUTATION_IDS = [m.action.value for m in _MUTATIONS]


# ---- tests ----


# A mutation that passes every check asks, in order: action permission, project access, the
# unlocked lookup by key, entitlement (deploy and promote only), the ID source, the clock, then the
# acceptance store. The store receives the normalized request, the new ID, the principal's subject
# as requester, the clock's time, the correlation ID and the deadline; the decide function inserts
# the command at the advanced command number.
@pytest.mark.asyncio
@pytest.mark.parametrize("m", _MUTATIONS, ids=_MUTATION_IDS)
async def test_mutation_accepts_a_new_command(m: _Mutation) -> None:
    f = OperationsFixture()
    f.acceptance.facts = [m.facts()]
    req = _mutation_request()

    got = await m.call(f, requester_principal(), req)

    want_calls = [*m.checks(), CALL_FIND]
    if m.entitlement:
        want_calls.append(CALL_ENTITLEMENT)
    want_calls += [CALL_ID, CALL_CLOCK, CALL_ACCEPT]
    assert f.log.calls == want_calls

    want_request = CommandRequest(
        project_id=ID_PROJECT, kind=m.kind, release_id=ID_RELEASE, idempotency_key="key-1"
    )
    assert f.acceptance.requests == [want_request], "the lookup by key used another request"
    assert len(f.acceptance.params) == 1
    assert f.acceptance.params[0] == AcceptCommandParams(
        request=want_request,
        command_id=ID_COMMAND2,
        requested_by_subject=REQUESTER,
        now=T1,
        correlation_id="corr-1",
        deadline_at=T1 + DEADLINE,
    )

    assert len(f.acceptance.decisions) == 1
    assert f.acceptance.decisions[0].insert, f"decide did not insert: {f.acceptance.decide_errors}"
    cmd = f.acceptance.decisions[0].command
    assert cmd.command_number == 5
    assert cmd.kind is m.kind
    assert cmd.id == ID_COMMAND2
    assert cmd.requested_by_subject == REQUESTER
    assert cmd.status is CommandStatus.ACCEPTED

    assert got == CommandAccepted(
        command_id=cmd.id, project_id=cmd.project_id, status=CommandStatus.ACCEPTED, replayed=False
    )
    if m.entitlement:
        assert f.entitlement.kinds == [m.kind]
        assert f.entitlement.projects == [req.project_id]


# Rollback never asks entitlement, so recovery is never blocked by an expired plan (decision 0004
# section 2): a refusing entitlement makes no difference.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refusal",
    [LimitReached, DependencyNotConfigured, RuntimeError],
    ids=["limit reached", "not configured", "payment down"],
)
async def test_rollback_never_asks_entitlement(refusal: type[Exception]) -> None:
    rollback = _MUTATIONS[2]
    f = OperationsFixture()
    f.entitlement.err = refusal("payment down")
    f.acceptance.facts = [rollback.facts()]
    await rollback.call(f, requester_principal(), _mutation_request())
    assert CALL_ENTITLEMENT not in f.log.calls


# A replay found by the unlocked lookup returns the stored command, whatever its status and whoever
# issued it, without asking entitlement, the clock, the ID source or the acceptance transaction
# (decision 0004 section 4; security review L3).
@pytest.mark.asyncio
@pytest.mark.parametrize("m", _MUTATIONS, ids=_MUTATION_IDS)
async def test_replay_returns_before_any_other_call(m: _Mutation) -> None:
    f = OperationsFixture()
    f.entitlement.err = LimitReached()
    stored = dataclasses.replace(
        command_for(m.kind, ID_COMMAND, "key-1", "another-member", 9),
        status=CommandStatus.FAILED,
        failure_code="candidate_failed",
    )
    f.acceptance.found = stored

    got = await m.call(f, requester_principal(), _mutation_request())

    assert got == CommandAccepted(
        command_id=stored.id,
        project_id=stored.project_id,
        status=CommandStatus.FAILED,
        replayed=True,
    )
    assert f.log.calls == [*m.checks(), CALL_FIND]


# The same key with a different request is a conflict found by the unlocked lookup, returned before
# entitlement and without writing.
@pytest.mark.asyncio
@pytest.mark.parametrize("m", _MUTATIONS, ids=_MUTATION_IDS)
async def test_conflict_returns_before_any_other_call(m: _Mutation) -> None:
    f = OperationsFixture()
    other = CommandKind.PROMOTE if m.kind is CommandKind.DEPLOY else CommandKind.DEPLOY
    f.acceptance.found = command_for(other, ID_COMMAND, "key-1", REQUESTER, 9)
    with pytest.raises(IdempotencyConflict):
        await m.call(f, requester_principal(), _mutation_request())
    assert f.log.calls == [*m.checks(), CALL_FIND]


# A failing lookup stops the mutation with the store's error.
@pytest.mark.asyncio
async def test_lookup_error_stops_the_mutation() -> None:
    f = OperationsFixture()
    f.acceptance.find_err = Contention("lookup")
    with pytest.raises(Contention):
        await _deploy(f, requester_principal(), _mutation_request())
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_FIND]


# Entitlement refusals are reported as exactly two outcomes, "limit reached" and "dependency
# unavailable" (decision 0006 section 3), and stop the mutation before the acceptance store. A cause
# that is not one of the two never leaks its own identity into the result.
_ENTITLEMENT_CASES: tuple[
    tuple[str, Callable[[], Exception], type[Exception], tuple[type[Exception], ...]], ...
] = (
    ("limit reached", lambda: LimitReached("plan"), LimitReached, (DependencyUnavailable,)),
    (
        "dependency unavailable",
        lambda: DependencyUnavailable("payment"),
        DependencyUnavailable,
        (LimitReached,),
    ),
    (
        "not configured",
        lambda: DependencyNotConfigured("entitlement (OD-04)"),
        DependencyUnavailable,
        (LimitReached,),
    ),
    (
        "any other error",
        lambda: InvalidId("payment said"),
        DependencyUnavailable,
        (LimitReached, InvalidId),
    ),
    (
        "project access sentinel",
        lambda: ProjectAccessUnavailable(),
        DependencyUnavailable,
        (ProjectAccessUnavailable, LimitReached),
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("m", "case"),
    [(m, c) for m in _MUTATIONS[:2] for c in _ENTITLEMENT_CASES],
    ids=[f"{m.action.value}/{c[0]}" for m in _MUTATIONS[:2] for c in _ENTITLEMENT_CASES],
)
async def test_entitlement_refusals_are_two_outcomes(
    m: _Mutation,
    case: tuple[str, Callable[[], Exception], type[Exception], tuple[type[Exception], ...]],
) -> None:
    _, make, want, not_want = case
    f = OperationsFixture()
    f.entitlement.err = make()
    with pytest.raises(want) as exc_info:
        await m.call(f, requester_principal(), _mutation_request())
    for nw in not_want:
        assert not is_error(exc_info.value, nw), f"{exc_info.value!r} also matches {nw.__name__}"
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_FIND, CALL_ENTITLEMENT]


# "us\x00er" and "user\xff": a Python str holding a lone surrogate cannot be UTF-8.
_BAD_SUBJECTS = {
    "empty": "",
    "leading space": " user",
    "trailing space": "user ",
    "control": "us\x00er",
    "line feed": "user\n",
    "too long": "s" * 256,
    "invalid utf8": "user\udcff",
    "only whitespace": "   ",
}


# The requester is the principal's subject. A subject that fails the requester rule is
# Unauthenticated after authorization, nothing is looked up or stored, and the error never repeats
# the subject.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("m", "subject"),
    [(m, s) for m in _MUTATIONS for s in _BAD_SUBJECTS.values()],
    ids=[f"{m.action.value}/{name}" for m in _MUTATIONS for name in _BAD_SUBJECTS],
)
async def test_mutation_refuses_an_invalid_subject(m: _Mutation, subject: str) -> None:
    f = OperationsFixture()
    p = dataclasses.replace(requester_principal(), subject=subject)
    with pytest.raises(Unauthenticated) as exc_info:
        await m.call(f, p, _mutation_request())
    assert not is_error(exc_info.value, InvalidRequester)
    if subject.strip():
        assert subject.strip() not in str(exc_info.value), "the error repeats the subject"
    assert f.log.calls == m.checks()


# A subject of 255 bytes is a valid requester and is stored as given.
@pytest.mark.asyncio
async def test_mutation_stores_the_longest_valid_subject() -> None:
    f = OperationsFixture()
    p = dataclasses.replace(requester_principal(), subject="s" * 255)
    await _deploy(f, p, _mutation_request())
    assert f.acceptance.params[0].requested_by_subject == p.subject
    assert f.acceptance.decisions[0].command.requested_by_subject == p.subject


_BAD_KEYS = ["", " k", "k ", "k\tk", "k" * 256, "\udcff"]
_BAD_KEY_IDS = ["empty", "leading space", "trailing space", "tab", "too long", "invalid utf8"]


# An invalid idempotency key is refused after authorization and before any store call.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("m", "key"),
    [(m, k) for k in _BAD_KEYS for m in _MUTATIONS],
    ids=[f"{m.action.value}/{name}" for name in _BAD_KEY_IDS for m in _MUTATIONS],
)
async def test_mutation_refuses_an_invalid_key(m: _Mutation, key: str) -> None:
    f = OperationsFixture()
    req = dataclasses.replace(_mutation_request(), idempotency_key=key)
    with pytest.raises(InvalidIdempotencyKey):
        await m.call(f, requester_principal(), req)
    assert f.log.calls == m.checks()


# A correlation ID outside 1 to MAX_CORRELATION_ID_LEN characters is a transport fault: an internal
# error, not any typed error, and nothing is looked up or stored.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "correlation_id", ["", "c" * (MAX_CORRELATION_ID_LEN + 1)], ids=["empty", "too long"]
)
async def test_mutation_refuses_a_correlation_id_out_of_range(correlation_id: str) -> None:
    f = OperationsFixture()
    req = dataclasses.replace(_mutation_request(), correlation_id=correlation_id)
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await _deploy(f, requester_principal(), req)
    assert_internal(exc_info.value)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS]


# The longest valid correlation ID is accepted.
@pytest.mark.asyncio
async def test_mutation_accepts_the_longest_correlation_id() -> None:
    f = OperationsFixture()
    req = dataclasses.replace(_mutation_request(), correlation_id="c" * MAX_CORRELATION_ID_LEN)
    await _deploy(f, requester_principal(), req)
    assert f.acceptance.params[0].correlation_id == req.correlation_id


# A failing ID source is internal and stops before the acceptance store.
@pytest.mark.asyncio
async def test_id_source_failure_stops_before_the_store() -> None:
    f = OperationsFixture()
    f.ids.err = RuntimeError("entropy exhausted")
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await _deploy(f, requester_principal(), _mutation_request())
    assert_internal(exc_info.value)
    assert CALL_ACCEPT not in f.log.calls


# The ID source must not hand out the nil UUID.
@pytest.mark.asyncio
async def test_nil_id_from_the_id_source_is_refused() -> None:
    f = OperationsFixture()
    f.ids.id = NIL_ID
    with pytest.raises(InvalidId):
        await _deploy(f, requester_principal(), _mutation_request())
    assert CALL_ACCEPT not in f.log.calls


# Errors of the acceptance transaction come back as the store reports them.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("store_err", "want"),
    [
        (lambda: NotFound("no hosting row"), NotFound),
        (lambda: Contention("lock timeout"), Contention),
        (lambda: CorruptRow("bad row"), CorruptRow),
    ],
    ids=["not found", "contention", "corrupt row"],
)
async def test_acceptance_store_errors_pass_through(
    store_err: Callable[[], Exception], want: type[Exception]
) -> None:
    f = OperationsFixture()
    f.acceptance.accept_err = store_err()
    with pytest.raises(want):
        await _deploy(f, requester_principal(), _mutation_request())


# A busy project names the command in flight in the error, for the response's command_id.
@pytest.mark.asyncio
@pytest.mark.parametrize("m", _MUTATIONS, ids=_MUTATION_IDS)
async def test_busy_carries_the_command_in_flight(m: _Mutation) -> None:
    f = OperationsFixture()
    in_flight = command_for(CommandKind.DEPLOY, ID_COMMAND, "other-key", "someone", 4)
    f.acceptance.facts = [dataclasses.replace(m.facts(), in_flight=in_flight)]
    with pytest.raises(OperationInProgress) as exc_info:
        await m.call(f, requester_principal(), _mutation_request())
    assert in_flight_command_id(exc_info.value) == in_flight.id


# When the store calls decide again after a raced unique violation, the second facts decide: the
# row that won under the same key is a replay.
@pytest.mark.asyncio
async def test_decide_is_called_again_after_a_race() -> None:
    f = OperationsFixture()
    winner = command_for(CommandKind.DEPLOY, ID_COMMAND, "key-1", "another-member", 5)
    f.acceptance.facts.append(
        AcceptanceFacts(
            hosting=project_hosting(5),
            by_key=winner,
            in_flight=winner,
            target=release_in(ID_RELEASE, ReleaseStatus.BUILT, T0),
        )
    )
    got = await _deploy(f, requester_principal(), _mutation_request())
    assert got.command_id == winner.id
    assert got.replayed


# A store that returns a command of another project is a fault: the result is an internal error,
# never that command.
@pytest.mark.asyncio
async def test_acceptance_result_of_another_project_is_refused() -> None:
    foreign = dataclasses.replace(
        command_for(CommandKind.DEPLOY, ID_COMMAND, "key-1", REQUESTER, 5),
        project_id=ID_PROJECT2,
    )
    f = OperationsFixture()
    f.acceptance.result = AcceptedCommand(command=foreign, replayed=False)
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await _deploy(f, requester_principal(), _mutation_request())
    assert_internal(exc_info.value)
