"""Command outcomes, the rows each commits together (decision 0001 section 7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest
from tests.unit.core.domain.builders import (
    ID_COMMAND,
    ID_PREV,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    ID_RELEASE2,
    T0,
    T1,
    valid_row,
)

from provisioning.core.domain import (
    CODE_CANDIDATE_REFUSED,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    DomainError,
    Failure,
    HostingStatus,
    IllegalTransition,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidFailureCode,
    InvalidFailureSummary,
    MissingFailureCode,
    OfflineReason,
    Outcome,
    OutcomeInput,
    ProjectHosting,
    ProjectMismatch,
    Release,
    ReleaseStatus,
    RoutingStatus,
    StaleFence,
    confirm_live,
    deploy_succeeded,
    fail_without_route_change,
    hosting_verified,
    new_command,
    record_hosting_reason,
    redeploy_succeeded,
    restore_verified,
    route_removed_verified,
    route_uncertain,
    skip_hosting_command,
    start_route_change,
)

# ---- fixtures ----


DIGEST_A = "sha256:" + "a" * 64

# The hosting kinds, by hand: the test does not take them from the code under test.
HOSTING_KINDS = frozenset(  # no release, no requester; sync_domains joins them (0014 s16)
    {CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING, CommandKind.SYNC_DOMAINS}
)
ALL_KINDS = list(CommandKind)

FAILURE = Failure(code="promotion_failed", summary="health check failed")
# The failure an outcome that writes no release takes: a command has no summary column.
CODE_ONLY = Failure(code=FAILURE.code)
# A deploy's release failure: only codes classified as failing the release fail it (decision 0011
# sections 3 and 12).
DEPLOY_RELEASE_FAILURE = Failure(
    code=CODE_CANDIDATE_REFUSED, summary="The cluster refused the release intent."
)


def _is_hosting(kind: CommandKind) -> bool:
    return kind in HOSTING_KINDS


def _release_failure_for(kind: CommandKind) -> Failure:
    """The release failure a kind's fail_without_route_change takes."""
    return DEPLOY_RELEASE_FAILURE if kind == CommandKind.DEPLOY else FAILURE


def _live_release() -> Release:
    """The project's live release ID_PREV, created at T0."""
    return replace(valid_row(ReleaseStatus.LIVE), id=ID_PREV)


def _new_command(kind: CommandKind) -> Command:
    """An accepted command of the kind with command number 4."""
    if _is_hosting(kind):
        request = CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
        )
        requester = None
    else:
        request = CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
        )
        requester = "user-7f3a"
    return new_command(
        command_id=ID_COMMAND,
        request=request,
        requested_by_subject=requester,
        command_number=4,
        now=T0,
    )


def _running(
    kind: CommandKind,
    status: ReleaseStatus | None,
    routing: RoutingStatus,
    hosting: HostingStatus,
    with_live: bool,
) -> OutcomeInput:
    """The rows an outcome reads: the hosting row at command number 4 with the routing and hosting
    statuses, a running command of the kind numbered 4, its release in the status (none for a
    hosting kind) and, when ``with_live``, the live release ID_PREV."""
    hosting_row = ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=4,
        routing_status=routing,
        hosting_status=hosting,
        updated_at=T0,
        # A Payment event key gives billing; an offline row holds at least one reason.
        offline_reasons=(
            frozenset({OfflineReason.BILLING}) if hosting is HostingStatus.OFFLINE else frozenset()
        ),
        subdomain="shop",
    )
    cmd = replace(_new_command(kind), status=CommandStatus.RUNNING)
    release = None
    if not _is_hosting(kind):
        assert status is not None, "fixture: a release kind needs a release status"
        release = valid_row(status)
    live = None
    if with_live:
        hosting_row = replace(hosting_row, live_release_id=ID_PREV)
        live = _live_release()
    return OutcomeInput(hosting=hosting_row, command=cmd, release=release, live_release=live)


# ---- expectations ----


def _expect_release(r: Release | None, status: ReleaseStatus) -> Release:
    assert r is not None, f"no release in the outcome, want {status}"
    assert r.status == status
    assert r.updated_at == T1
    return r


def _expect_command(c: Command, status: CommandStatus, code: str) -> None:
    assert c.status == status
    assert c.updated_at == T1
    if code == "":
        assert c.failure_code is None
    else:
        assert c.failure_code == code


def _expect_live_state(inp: OutcomeInput, out: Outcome) -> None:
    assert inp.release is not None
    assert out.hosting.live_release_id == inp.release.id
    assert out.hosting.routing_status == RoutingStatus.STABLE
    assert out.hosting.hosting_status == inp.hosting.hosting_status
    assert out.hosting.last_command_number == inp.hosting.last_command_number
    assert out.hosting.updated_at == T1


def _expect_stable_keeping_live(inp: OutcomeInput, out: Outcome) -> None:
    assert out.hosting.routing_status == RoutingStatus.STABLE
    assert out.hosting.live_release_id == inp.hosting.live_release_id
    assert out.hosting.hosting_status == inp.hosting.hosting_status
    assert out.hosting.updated_at == T1
    assert out.superseded_release is None, "a release was superseded"


# ---- the outcome cases ----


@dataclass(frozen=True)
class _OutcomeCase:
    name: str
    kinds: tuple[CommandKind, ...]
    valid: Callable[[CommandKind], OutcomeInput]
    apply: Callable[[OutcomeInput], Outcome]
    check: Callable[[CommandKind, OutcomeInput, Outcome], None]


def _deploy_succeeded_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.BUILT, RoutingStatus.STABLE, HostingStatus.ONLINE, True)


def _deploy_succeeded_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    _expect_release(out.release, ReleaseStatus.CANDIDATE_READY)
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")
    assert out.hosting == inp.hosting
    assert out.superseded_release is None, "deploy superseded a release"


def _fail_kept_valid(k: CommandKind) -> OutcomeInput:
    status, routing = ReleaseStatus.BUILT, RoutingStatus.STABLE
    if k == CommandKind.PROMOTE:
        status = ReleaseStatus.CANDIDATE_READY
    elif k == CommandKind.ROLLBACK:
        status, routing = ReleaseStatus.PROMOTING, RoutingStatus.UNCERTAIN
    return _running(k, status, routing, HostingStatus.ONLINE, True)


def _fail_kept_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    # A command that fails before its work touched the release leaves it unchanged.
    assert out.release is None, f"release written: {out.release}"
    _expect_command(out.command, CommandStatus.FAILED, FAILURE.code)
    assert out.hosting == inp.hosting


def _fail_release_valid(k: CommandKind) -> OutcomeInput:
    status = ReleaseStatus.CANDIDATE_READY if k == CommandKind.PROMOTE else ReleaseStatus.BUILT
    return _running(k, status, RoutingStatus.STABLE, HostingStatus.ONLINE, False)


def _fail_release_check(k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    f = _release_failure_for(k)
    r = _expect_release(out.release, ReleaseStatus.FAILED)
    assert r.failure_code == f.code
    assert r.failure_summary == f.summary
    _expect_command(out.command, CommandStatus.FAILED, f.code)
    assert out.hosting == inp.hosting


def _skip_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, None, RoutingStatus.STABLE, HostingStatus.ONLINE, True)


def _skip_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    _expect_command(out.command, CommandStatus.SKIPPED, "entitlement_restored")
    assert out.hosting == inp.hosting
    assert out.release is None, "release written"


def _start_valid(k: CommandKind) -> OutcomeInput:
    if k == CommandKind.PROMOTE:
        return _running(
            k, ReleaseStatus.CANDIDATE_READY, RoutingStatus.STABLE, HostingStatus.ONLINE, True
        )
    if k == CommandKind.ROLLBACK:
        return _running(
            k, ReleaseStatus.PROMOTING, RoutingStatus.UNCERTAIN, HostingStatus.ONLINE, True
        )
    if k == CommandKind.SUSPEND_HOSTING:
        return _running(k, None, RoutingStatus.STABLE, HostingStatus.ONLINE, True)
    return _running(k, None, RoutingStatus.STABLE, HostingStatus.OFFLINE, True)


def _start_check(k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    assert out.hosting.routing_status == RoutingStatus.CHANGING
    assert out.hosting.updated_at == T1
    assert out.hosting.hosting_status == inp.hosting.hosting_status
    assert out.hosting.last_command_number == inp.hosting.last_command_number
    assert out.hosting.live_release_id == inp.hosting.live_release_id
    # The command keeps running.
    assert out.command == inp.command
    if k == CommandKind.PROMOTE:
        _expect_release(out.release, ReleaseStatus.PROMOTING)
    else:
        assert out.release is None, f"release written for {k}"


def _confirm_with_live_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.PROMOTING, RoutingStatus.CHANGING, HostingStatus.ONLINE, True)


def _confirm_with_live_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    r = _expect_release(out.release, ReleaseStatus.LIVE)
    assert r.promoted_at == T1
    assert inp.live_release is not None
    old = out.superseded_release
    assert old is not None
    assert old.id == inp.live_release.id
    assert old.status == ReleaseStatus.SUPERSEDED
    assert old.promoted_at == T0
    _expect_live_state(inp, out)
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")


def _confirm_first_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.PROMOTING, RoutingStatus.CHANGING, HostingStatus.ONLINE, False)


def _confirm_first_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    _expect_release(out.release, ReleaseStatus.LIVE)
    assert out.superseded_release is None, "first publish superseded a release"
    _expect_live_state(inp, out)
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")


def _restore_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.PROMOTING, RoutingStatus.CHANGING, HostingStatus.ONLINE, True)


def _restore_check(k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    r = _expect_release(out.release, ReleaseStatus.ROLLED_BACK)
    assert r.promoted_at is None
    assert r.failure_code == FAILURE.code
    _expect_stable_keeping_live(inp, out)
    # A promote that ends with the route restored is failed; an operator rollback succeeded.
    if k == CommandKind.PROMOTE:
        _expect_command(out.command, CommandStatus.FAILED, FAILURE.code)
    else:
        _expect_command(out.command, CommandStatus.SUCCEEDED, "")


def _removed_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.PROMOTING, RoutingStatus.CHANGING, HostingStatus.ONLINE, False)


def _removed_check(k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    r = _expect_release(out.release, ReleaseStatus.FAILED)
    assert r.failure_code == FAILURE.code
    _expect_stable_keeping_live(inp, out)
    # No live release: a promote ends failed, an operator rollback succeeded (decision 0001
    # sections 4 and 7).
    if k == CommandKind.ROLLBACK:
        _expect_command(out.command, CommandStatus.SUCCEEDED, "")
    else:
        _expect_command(out.command, CommandStatus.FAILED, FAILURE.code)


def _uncertain_valid(k: CommandKind) -> OutcomeInput:
    return _running(k, ReleaseStatus.PROMOTING, RoutingStatus.CHANGING, HostingStatus.ONLINE, True)


def _uncertain_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    # The release stays promoting: no transition reports a recovery that was not verified.
    assert out.release is None, f"release written: {out.release}"
    assert out.hosting.routing_status == RoutingStatus.UNCERTAIN
    assert out.hosting.hosting_status == inp.hosting.hosting_status
    assert out.hosting.live_release_id == inp.hosting.live_release_id
    assert out.hosting.updated_at == T1
    _expect_command(out.command, CommandStatus.FAILED, FAILURE.code)


def _redeploy_valid(k: CommandKind) -> OutcomeInput:
    """A running redeploy of the live release (decision 0023 G12): its release is the live one."""
    inp = _running(k, ReleaseStatus.LIVE, RoutingStatus.STABLE, HostingStatus.ONLINE, False)
    return replace(
        inp,
        live_release=inp.release,
        hosting=replace(inp.hosting, live_release_id=ID_RELEASE),
    )


def _redeploy_check(_k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    """Only the command ends: the hosting row (updated_at included) and every release stay."""
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")
    assert out.hosting == inp.hosting
    assert out.release is None, "release written"
    assert out.superseded_release is None, "release written"


def _not_live(inp: OutcomeInput) -> OutcomeInput:
    """The live release is another one than the redeploy's (the target changed)."""
    assert inp.release is not None
    return replace(
        inp,
        release=replace(inp.release, status=ReleaseStatus.SUPERSEDED),
        live_release=_live_release(),
        hosting=replace(inp.hosting, live_release_id=ID_PREV),
    )


def _hosting_valid(k: CommandKind) -> OutcomeInput:
    hosting = HostingStatus.OFFLINE if k == CommandKind.RESUME_HOSTING else HostingStatus.ONLINE
    return _running(k, None, RoutingStatus.CHANGING, hosting, True)


def _hosting_check(k: CommandKind, inp: OutcomeInput, out: Outcome) -> None:
    want = HostingStatus.ONLINE if k == CommandKind.RESUME_HOSTING else HostingStatus.OFFLINE
    assert out.hosting.hosting_status == want
    assert out.hosting.routing_status == RoutingStatus.STABLE
    assert out.hosting.live_release_id == inp.hosting.live_release_id
    assert out.hosting.updated_at == T1
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")
    assert out.release is None, "release written"
    assert out.superseded_release is None, "release written"


OUTCOME_CASES: tuple[_OutcomeCase, ...] = (
    _OutcomeCase(
        "DeploySucceeded",
        (CommandKind.DEPLOY,),
        _deploy_succeeded_valid,
        lambda inp: deploy_succeeded(inp, T1),
        _deploy_succeeded_check,
    ),
    _OutcomeCase(
        "FailWithoutRouteChange/release kept",
        (
            CommandKind.DEPLOY,
            CommandKind.PROMOTE,
            CommandKind.ROLLBACK,
            CommandKind.SUSPEND_HOSTING,
            CommandKind.RESUME_HOSTING,
            # The worker fails a claimed redeploy operation_unavailable (decision 0021 D10).
            CommandKind.REDEPLOY,
            CommandKind.SYNC_DOMAINS,
        ),
        _fail_kept_valid,
        lambda inp: fail_without_route_change(inp, CODE_ONLY, False, T1),
        _fail_kept_check,
    ),
    _OutcomeCase(
        "FailWithoutRouteChange/release failed",
        (CommandKind.DEPLOY, CommandKind.PROMOTE),
        _fail_release_valid,
        lambda inp: fail_without_route_change(
            inp, _release_failure_for(inp.command.kind), True, T1
        ),
        _fail_release_check,
    ),
    _OutcomeCase(
        "SkipHostingCommand",
        (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING),
        _skip_valid,
        lambda inp: skip_hosting_command(inp, Failure(code="entitlement_restored"), T1),
        _skip_check,
    ),
    _OutcomeCase(
        "StartRouteChange",
        (
            CommandKind.PROMOTE,
            CommandKind.ROLLBACK,
            CommandKind.SUSPEND_HOSTING,
            CommandKind.RESUME_HOSTING,
        ),
        _start_valid,
        lambda inp: start_route_change(inp, T1),
        _start_check,
    ),
    _OutcomeCase(
        "ConfirmLive/with active",
        (CommandKind.PROMOTE,),
        _confirm_with_live_valid,
        lambda inp: confirm_live(inp, T1),
        _confirm_with_live_check,
    ),
    _OutcomeCase(
        "ConfirmLive/first publish",
        (CommandKind.PROMOTE,),
        _confirm_first_valid,
        lambda inp: confirm_live(inp, T1),
        _confirm_first_check,
    ),
    _OutcomeCase(
        "RestoreVerified",
        (CommandKind.PROMOTE, CommandKind.ROLLBACK),
        _restore_valid,
        lambda inp: restore_verified(inp, FAILURE, T1),
        _restore_check,
    ),
    _OutcomeCase(
        "RouteRemovedVerified",
        (CommandKind.PROMOTE, CommandKind.ROLLBACK),
        _removed_valid,
        lambda inp: route_removed_verified(inp, FAILURE, T1),
        _removed_check,
    ),
    _OutcomeCase(
        "RouteUncertain",
        (
            CommandKind.PROMOTE,
            CommandKind.ROLLBACK,
            CommandKind.SUSPEND_HOSTING,
            CommandKind.RESUME_HOSTING,
        ),
        _uncertain_valid,
        lambda inp: route_uncertain(inp, CODE_ONLY, T1),
        _uncertain_check,
    ),
    _OutcomeCase(
        "HostingVerified",
        (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING),
        _hosting_valid,
        lambda inp: hosting_verified(inp, T1),
        _hosting_check,
    ),
    _OutcomeCase(
        "RedeploySucceeded",
        (CommandKind.REDEPLOY,),
        _redeploy_valid,
        lambda inp: redeploy_succeeded(inp, T1),
        _redeploy_check,
    ),
)
BY_NAME = {c.name: c for c in OUTCOME_CASES}

_APPLY_CASES = [(c, k) for c in OUTCOME_CASES for k in c.kinds]
_APPLY_IDS = [f"{c.name}/{k.value}" for c, k in _APPLY_CASES]
_WRONG_KIND_CASES = [(c, k) for c in OUTCOME_CASES for k in ALL_KINDS if k not in c.kinds]
_WRONG_KIND_IDS = [f"{c.name}/wrong kind {k.value}" for c, k in _WRONG_KIND_CASES]


@pytest.mark.parametrize(("case", "kind"), _APPLY_CASES, ids=_APPLY_IDS)
def test_outcomes_apply(case: _OutcomeCase, kind: CommandKind) -> None:
    inp = case.valid(kind)
    before = replace(inp)
    out = case.apply(inp)
    assert inp == before, "input changed"
    # The fence counter never moves in an outcome, and every row the outcome returns is valid.
    assert out.hosting.last_command_number == inp.hosting.last_command_number
    # The command's identity, requester included, never changes.
    assert out.command.id == inp.command.id
    assert out.command.kind == inp.command.kind
    assert out.command.request_fingerprint == inp.command.request_fingerprint
    assert out.command.command_number == inp.command.command_number
    assert out.command.requested_by_subject == inp.command.requested_by_subject
    assert _is_hosting(kind) == (inp.command.requested_by_subject is None), "fixture requester"
    out.hosting.validate()
    out.command.validate()
    for r in (out.release, out.superseded_release):
        if r is not None:
            r.validate()
    case.check(kind, inp, out)


def _rejects_outcome(case: _OutcomeCase, inp: OutcomeInput, want: type[DomainError]) -> None:
    """Assert the error, and that the input is untouched."""
    before = replace(inp)
    with pytest.raises(want):
        case.apply(inp)
    assert inp == before, "input changed"


def _with_hosting(inp: OutcomeInput, f: Callable[[ProjectHosting], ProjectHosting]) -> OutcomeInput:
    return replace(inp, hosting=f(inp.hosting))


def _with_command(inp: OutcomeInput, f: Callable[[Command], Command]) -> OutcomeInput:
    return replace(inp, command=f(inp.command))


def _with_release(inp: OutcomeInput, f: Callable[[Release], Release]) -> OutcomeInput:
    assert inp.release is not None
    return replace(inp, release=f(inp.release))


@pytest.mark.parametrize(("case", "kind"), _APPLY_CASES, ids=_APPLY_IDS)
def test_outcomes_common_checks(case: _OutcomeCase, kind: CommandKind) -> None:
    """Every outcome checks the fence, that the command is running, and that the rows belong
    together."""
    _rejects_outcome(
        case,
        _with_hosting(case.valid(kind), lambda h: replace(h, last_command_number=5)),
        StaleFence,
    )
    _rejects_outcome(
        case, _with_command(case.valid(kind), lambda c: replace(c, command_number=3)), StaleFence
    )

    invalid = _with_command(case.valid(kind), lambda c: replace(c, command_number=0))
    invalid = _with_hosting(invalid, lambda h: replace(h, last_command_number=0))
    _rejects_outcome(case, invalid, InvalidCommandNumber)

    for st in (
        CommandStatus.ACCEPTED,
        CommandStatus.SUCCEEDED,
        CommandStatus.FAILED,
        CommandStatus.SKIPPED,
    ):
        fresh = case.valid(kind)
        not_running = replace(fresh, command=replace(fresh.command, status=st))
        if st in (CommandStatus.FAILED, CommandStatus.SKIPPED):
            not_running = _with_command(not_running, lambda c: replace(c, failure_code="earlier"))
        _rejects_outcome(case, not_running, IllegalTransition)

    _rejects_outcome(
        case,
        _with_command(case.valid(kind), lambda c: replace(c, project_id=ID_PROJECT2)),
        ProjectMismatch,
    )

    if _is_hosting(kind):
        with_release = replace(case.valid(kind), release=valid_row(ReleaseStatus.PROMOTING))
        _rejects_outcome(case, with_release, InvalidCommandTarget)
    else:
        _rejects_outcome(case, replace(case.valid(kind), release=None), InvalidCommandTarget)
        _rejects_outcome(
            case,
            _with_release(case.valid(kind), lambda r: replace(r, id=ID_RELEASE2)),
            InvalidCommandTarget,
        )
        _rejects_outcome(
            case,
            _with_release(case.valid(kind), lambda r: replace(r, project_id=ID_PROJECT2)),
            ProjectMismatch,
        )

    inconsistent = case.valid(kind)
    if inconsistent.live_release is not None:
        inconsistent = replace(inconsistent, live_release=None)
    else:
        inconsistent = replace(inconsistent, live_release=_live_release())
    _rejects_outcome(case, inconsistent, IllegalTransition)


@pytest.mark.parametrize(("case", "kind"), _WRONG_KIND_CASES, ids=_WRONG_KIND_IDS)
def test_outcomes_common_checks_wrong_kind(case: _OutcomeCase, kind: CommandKind) -> None:
    """Every kind the outcome does not fit is refused."""
    inp = case.valid(case.kinds[0])
    inp = replace(inp, command=replace(_new_command(kind), status=CommandStatus.RUNNING))
    if _is_hosting(kind):
        inp = replace(inp, release=None)
    elif inp.release is None:
        inp = replace(inp, release=valid_row(ReleaseStatus.PROMOTING))
    _rejects_outcome(case, inp, IllegalTransition)


# ---- states an outcome does not fit ----

_Mutate = Callable[[OutcomeInput], OutcomeInput]


def _set_routing(r: RoutingStatus) -> _Mutate:
    return lambda inp: _with_hosting(inp, lambda row: replace(row, routing_status=r))


def _set_hosting(h: HostingStatus) -> _Mutate:
    return lambda inp: _with_hosting(inp, lambda row: replace(row, hosting_status=h))


def _set_status(s: ReleaseStatus) -> _Mutate:
    return lambda inp: replace(inp, release=valid_row(s))


def _same_as_live(inp: OutcomeInput) -> OutcomeInput:
    """The live release passed in is the promoting release itself."""
    assert inp.release is not None
    a = replace(inp.release, status=ReleaseStatus.LIVE, promoted_at=T0)
    return replace(inp, live_release=a, hosting=replace(inp.hosting, live_release_id=a.id))


def _drop_live(inp: OutcomeInput) -> OutcomeInput:
    return replace(inp, live_release=None, hosting=replace(inp.hosting, live_release_id=None))


def _add_live(inp: OutcomeInput) -> OutcomeInput:
    return replace(
        inp, live_release=_live_release(), hosting=replace(inp.hosting, live_release_id=ID_PREV)
    )


def _unchanged(inp: OutcomeInput) -> OutcomeInput:
    return inp


_D, _P, _R = CommandKind.DEPLOY, CommandKind.PROMOTE, CommandKind.ROLLBACK
_S, _U = CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING
_IT = IllegalTransition

_MISFITS: list[tuple[str, str, CommandKind, _Mutate, type[DomainError] | None]] = [
    ("DeploySucceeded", "route changing", _D, _set_routing(RoutingStatus.CHANGING), _IT),
    (
        "DeploySucceeded",
        "release candidate_ready",
        _D,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "route changing",
        _D,
        _set_routing(RoutingStatus.CHANGING),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "route uncertain",
        _P,
        _set_routing(RoutingStatus.UNCERTAIN),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "route stable",
        _R,
        _set_routing(RoutingStatus.STABLE),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "route changing",
        _R,
        _set_routing(RoutingStatus.CHANGING),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "route changing",
        _S,
        _set_routing(RoutingStatus.CHANGING),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "release promoting",
        _D,
        _set_status(ReleaseStatus.PROMOTING),
        _IT,
    ),
    (
        "FailWithoutRouteChange/release kept",
        "release built",
        _P,
        _set_status(ReleaseStatus.BUILT),
        _IT,
    ),
    # Decision 0014 section 13: a rollback may name the live release, so these three fit now.
    (
        "FailWithoutRouteChange/release kept",
        "release live",
        _R,
        _set_status(ReleaseStatus.LIVE),
        None,
    ),
    ("SkipHostingCommand", "route changing", _S, _set_routing(RoutingStatus.CHANGING), _IT),
    ("StartRouteChange", "route changing", _P, _set_routing(RoutingStatus.CHANGING), _IT),
    ("StartRouteChange", "route uncertain", _P, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    ("StartRouteChange", "release built", _P, _set_status(ReleaseStatus.BUILT), _IT),
    ("StartRouteChange", "route stable", _R, _set_routing(RoutingStatus.STABLE), _IT),
    (
        "StartRouteChange",
        "release candidate_ready",
        _R,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        None,
    ),
    ("StartRouteChange", "route uncertain", _S, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    # Suspending an offline project or resuming an online one is not a route change; the caller
    # decides how such a command ends (OD-52).
    ("StartRouteChange", "hosting offline", _S, _set_hosting(HostingStatus.OFFLINE), _IT),
    ("StartRouteChange", "hosting online", _U, _set_hosting(HostingStatus.ONLINE), _IT),
    ("ConfirmLive/with active", "route stable", _P, _set_routing(RoutingStatus.STABLE), _IT),
    ("ConfirmLive/with active", "route uncertain", _P, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    (
        "ConfirmLive/with active",
        "release candidate_ready",
        _P,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        _IT,
    ),
    ("RestoreVerified", "drop live", _P, _drop_live, _IT),
    ("RestoreVerified", "drop live", _R, _drop_live, _IT),
    ("RestoreVerified", "route uncertain", _P, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    (
        "RestoreVerified",
        "release candidate_ready",
        _P,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        _IT,
    ),
    ("RestoreVerified", "control", _P, _unchanged, None),
    # The live release passed in is the promoting release itself.
    ("ConfirmLive/with active", "same as live", _P, _same_as_live, _IT),
    ("RestoreVerified", "same as live", _R, _same_as_live, _IT),
    ("RouteRemovedVerified", "add live", _P, _add_live, _IT),
    ("RouteRemovedVerified", "add live", _R, _add_live, _IT),
    ("RouteRemovedVerified", "route uncertain", _R, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    (
        "RouteRemovedVerified",
        "release candidate_ready",
        _R,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        _IT,
    ),
    (
        "RouteRemovedVerified",
        "release candidate_ready",
        _P,
        _set_status(ReleaseStatus.CANDIDATE_READY),
        _IT,
    ),
    ("RouteRemovedVerified", "release built", _P, _set_status(ReleaseStatus.BUILT), _IT),
    ("RouteRemovedVerified", "route stable", _P, _set_routing(RoutingStatus.STABLE), _IT),
    ("RouteUncertain", "route stable", _P, _set_routing(RoutingStatus.STABLE), _IT),
    ("RouteUncertain", "route uncertain", _P, _set_routing(RoutingStatus.UNCERTAIN), _IT),
    ("RouteUncertain", "release rolled_back", _R, _set_status(ReleaseStatus.ROLLED_BACK), None),
    ("RouteUncertain", "route stable", _S, _set_routing(RoutingStatus.STABLE), _IT),
    ("HostingVerified", "route stable", _S, _set_routing(RoutingStatus.STABLE), _IT),
    ("HostingVerified", "hosting offline", _S, _set_hosting(HostingStatus.OFFLINE), _IT),
    ("HostingVerified", "hosting online", _U, _set_hosting(HostingStatus.ONLINE), _IT),
    # Decision 0023 G12: a redeploy succeeds only on the live release with the route stable.
    (
        "RedeploySucceeded",
        "route changing",
        CommandKind.REDEPLOY,
        _set_routing(RoutingStatus.CHANGING),
        _IT,
    ),
    (
        "RedeploySucceeded",
        "route uncertain",
        CommandKind.REDEPLOY,
        _set_routing(RoutingStatus.UNCERTAIN),
        _IT,
    ),
    ("RedeploySucceeded", "release not live", CommandKind.REDEPLOY, _not_live, _IT),
    ("RedeploySucceeded", "control", CommandKind.REDEPLOY, _unchanged, None),
]


def test_misfit_cases_name_known_outcomes() -> None:
    unknown = {m[0] for m in _MISFITS} - set(BY_NAME)
    assert not unknown, f"no outcome {unknown}"


@pytest.mark.parametrize(
    ("outcome", "kind", "mutate", "want"),
    [(m[0], m[2], m[3], m[4]) for m in _MISFITS],
    ids=[f"{m[0]}/{m[2].value}/{m[1]}" for m in _MISFITS],
)
def test_outcomes_refuse_states_they_do_not_fit(
    outcome: str, kind: CommandKind, mutate: _Mutate, want: type[DomainError] | None
) -> None:
    """Each outcome fits only the routing status, hosting status and release status of its row."""
    case = BY_NAME[outcome]
    inp = mutate(case.valid(kind))
    if want is None:
        case.apply(inp)  # control: accepted
        return
    _rejects_outcome(case, inp, want)


# ---- failure input ----

_BAD_FAILURES: dict[str, tuple[Failure, type[DomainError]]] = {
    "empty": (Failure(code=""), MissingFailureCode),
    "bad code": (Failure(code="Bad"), InvalidFailureCode),
    "summary line feed": (Failure(code="ok", summary="a\nb"), InvalidFailureSummary),
    "summary bidi control": (Failure(code="ok", summary="‮"), InvalidFailureSummary),
}
_FAILURE_INPUT_CASES = [
    (fname, oname, f, want)
    for fname, (f, want) in _BAD_FAILURES.items()
    for oname in ("RestoreVerified", "RouteUncertain")
]


@pytest.mark.parametrize(
    ("outcome", "failure", "want"),
    [c[1:] for c in _FAILURE_INPUT_CASES],
    ids=[f"{c[1]}/{c[0]}" for c in _FAILURE_INPUT_CASES],
)
def test_outcome_failure_input(outcome: str, failure: Failure, want: type[DomainError]) -> None:
    """Failure inputs follow the code and summary rules."""
    inp = _running(
        CommandKind.PROMOTE,
        ReleaseStatus.PROMOTING,
        RoutingStatus.CHANGING,
        HostingStatus.ONLINE,
        True,
    )
    with pytest.raises(want):
        if outcome == "RestoreVerified":
            restore_verified(inp, failure, T1)
        else:
            route_uncertain(inp, failure, T1)


def test_outcome_failure_input_code_required() -> None:
    """Skipped needs a code like failed does; a release failure needs a code and a valid
    summary."""
    h = _running(
        CommandKind.SUSPEND_HOSTING, None, RoutingStatus.STABLE, HostingStatus.ONLINE, False
    )
    with pytest.raises(MissingFailureCode):
        skip_hosting_command(h, Failure(code=""), T1)
    d = _running(
        CommandKind.DEPLOY, ReleaseStatus.BUILT, RoutingStatus.STABLE, HostingStatus.ONLINE, False
    )
    # The rollback command succeeds, but its release still fails with a required code.
    rb = _running(
        CommandKind.ROLLBACK,
        ReleaseStatus.PROMOTING,
        RoutingStatus.CHANGING,
        HostingStatus.ONLINE,
        False,
    )
    with pytest.raises(MissingFailureCode):
        route_removed_verified(rb, Failure(code=""), T1)
    with pytest.raises(MissingFailureCode):
        fail_without_route_change(d, Failure(code=""), False, T1)
    with pytest.raises(InvalidFailureSummary):
        fail_without_route_change(d, Failure(code=CODE_CANDIDATE_REFUSED, summary="a\nb"), True, T1)
    p = _running(
        CommandKind.PROMOTE,
        ReleaseStatus.PROMOTING,
        RoutingStatus.CHANGING,
        HostingStatus.ONLINE,
        False,
    )
    with pytest.raises(MissingFailureCode):
        route_removed_verified(p, Failure(code=""), T1)


_NOT_RELEASE_FAILING = [
    CommandKind.ROLLBACK,
    CommandKind.SUSPEND_HOSTING,
    CommandKind.RESUME_HOSTING,
]


@pytest.mark.parametrize("kind", _NOT_RELEASE_FAILING, ids=[k.value for k in _NOT_RELEASE_FAILING])
def test_outcome_failure_input_only_deploy_and_promote_fail_their_release(
    kind: CommandKind,
) -> None:
    inp = BY_NAME["FailWithoutRouteChange/release kept"].valid(kind)
    with pytest.raises(IllegalTransition):
        fail_without_route_change(inp, FAILURE, True, T1)


# ---- a summary where no release is written ----

_SummaryApply = Callable[[OutcomeInput, Failure], Outcome]


def _summary_cases() -> dict[str, tuple[Callable[[], OutcomeInput], _SummaryApply]]:
    return {
        "FailWithoutRouteChange/deploy": (
            lambda: _running(
                CommandKind.DEPLOY,
                ReleaseStatus.BUILT,
                RoutingStatus.STABLE,
                HostingStatus.ONLINE,
                False,
            ),
            lambda inp, f: fail_without_route_change(inp, f, False, T1),
        ),
        "FailWithoutRouteChange/suspend_hosting": (
            lambda: _running(
                CommandKind.SUSPEND_HOSTING, None, RoutingStatus.STABLE, HostingStatus.ONLINE, False
            ),
            lambda inp, f: fail_without_route_change(inp, f, False, T1),
        ),
        "SkipHostingCommand": (
            lambda: _running(
                CommandKind.RESUME_HOSTING, None, RoutingStatus.STABLE, HostingStatus.OFFLINE, False
            ),
            lambda inp, f: skip_hosting_command(inp, f, T1),
        ),
        "RouteUncertain/promote": (
            lambda: _running(
                CommandKind.PROMOTE,
                ReleaseStatus.PROMOTING,
                RoutingStatus.CHANGING,
                HostingStatus.ONLINE,
                True,
            ),
            lambda inp, f: route_uncertain(inp, f, T1),
        ),
        "RouteUncertain/suspend_hosting": (
            lambda: _running(
                CommandKind.SUSPEND_HOSTING,
                None,
                RoutingStatus.CHANGING,
                HostingStatus.ONLINE,
                False,
            ),
            lambda inp, f: route_uncertain(inp, f, T1),
        ),
    }


_SUMMARY_CASES = _summary_cases()


@pytest.mark.parametrize(
    ("make_input", "apply"), list(_SUMMARY_CASES.values()), ids=list(_SUMMARY_CASES)
)
def test_outcome_summary_refused_where_no_release_is_written(
    make_input: Callable[[], OutcomeInput], apply: _SummaryApply
) -> None:
    """A command has no summary column: where an outcome writes no release, a summary is refused,
    not ignored, and nothing changes (decision 0001 section 7; validation finding VR-02)."""
    inp = make_input()
    # A well-formed summary and a malformed one are both refused.
    for summary in ("health check failed", "a\nb", "‮"):
        before = replace(inp)
        with pytest.raises(InvalidFailureSummary):
            apply(inp, Failure(code="entitlement_restored", summary=summary))
        assert inp == before, f"summary {summary!r}: input changed"
    # Without a summary the same outcome is accepted.
    apply(inp, Failure(code="entitlement_restored"))


# ---- reasons (decision 0021) ----


def _reasoned(
    kind: CommandKind,
    key: str,
    reasons: set[OfflineReason],
    *,
    routing: RoutingStatus,
    live: bool = True,
) -> OutcomeInput:
    """A running hosting command under ``key`` on a hosting row holding ``reasons``."""
    base = _running(kind, None, routing, HostingStatus.ONLINE, live)
    hosting = replace(
        base.hosting,
        offline_reasons=frozenset(reasons),
        hosting_status=HostingStatus.OFFLINE if reasons else HostingStatus.ONLINE,
    )
    request = CommandRequest(project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key=key)
    command = replace(
        new_command(
            command_id=ID_COMMAND,
            request=request,
            requested_by_subject=None,
            command_number=4,
            now=T0,
        ),
        status=CommandStatus.RUNNING,
    )
    return replace(base, hosting=hosting, command=command)


def test_hosting_verified_adds_the_reason() -> None:
    inp = _reasoned(CommandKind.SUSPEND_HOSTING, "pay-evt-1", set(), routing=RoutingStatus.CHANGING)
    out = hosting_verified(inp, T1)
    assert out.hosting.hosting_status is HostingStatus.OFFLINE
    assert out.hosting.offline_reasons == frozenset({OfflineReason.BILLING})
    assert out.hosting.routing_status is RoutingStatus.STABLE


def test_hosting_verified_resume_removes_only_its_reason() -> None:
    inp = _reasoned(
        CommandKind.RESUME_HOSTING,
        "owner-status:2",
        {OfflineReason.USER_SUSPENDED},
        routing=RoutingStatus.CHANGING,
    )
    out = hosting_verified(inp, T1)
    assert out.hosting.hosting_status is HostingStatus.ONLINE
    assert out.hosting.offline_reasons == frozenset()
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")


def test_hosting_verified_refuses_a_change_that_leaves_the_status() -> None:
    inp = _reasoned(
        CommandKind.RESUME_HOSTING,
        "pay-evt-2",
        {OfflineReason.BILLING, OfflineReason.USER_SUSPENDED},
        routing=RoutingStatus.CHANGING,
    )
    with pytest.raises(IllegalTransition):
        hosting_verified(inp, T1)


def test_record_hosting_reason_keeps_offline_while_another_reason_remains() -> None:
    inp = _reasoned(
        CommandKind.RESUME_HOSTING,
        "pay-evt-2",
        {OfflineReason.BILLING, OfflineReason.USER_SUSPENDED},
        routing=RoutingStatus.STABLE,
    )
    out = record_hosting_reason(inp, T1)
    assert out.hosting.hosting_status is HostingStatus.OFFLINE
    assert out.hosting.offline_reasons == frozenset({OfflineReason.USER_SUSPENDED})
    assert out.hosting.routing_status is RoutingStatus.STABLE
    assert out.hosting.live_release_id == inp.hosting.live_release_id
    _expect_command(out.command, CommandStatus.SUCCEEDED, "")
    assert out.release is None


def test_record_hosting_reason_adds_a_reason_to_an_offline_site() -> None:
    inp = _reasoned(
        CommandKind.SUSPEND_HOSTING,
        "owner-status:4",
        {OfflineReason.BILLING},
        routing=RoutingStatus.STABLE,
    )
    out = record_hosting_reason(inp, T1)
    assert out.hosting.offline_reasons == frozenset(
        {OfflineReason.BILLING, OfflineReason.USER_SUSPENDED}
    )
    assert out.hosting.hosting_status is HostingStatus.OFFLINE


def test_record_hosting_reason_changes_status_only_when_never_routed() -> None:
    never = _reasoned(
        CommandKind.SUSPEND_HOSTING, "pay-evt-3", set(), routing=RoutingStatus.STABLE, live=False
    )
    out = record_hosting_reason(never, T1)
    assert out.hosting.hosting_status is HostingStatus.OFFLINE
    assert out.hosting.offline_reasons == frozenset({OfflineReason.BILLING})
    routed = _reasoned(
        CommandKind.SUSPEND_HOSTING, "pay-evt-3", set(), routing=RoutingStatus.STABLE, live=True
    )
    with pytest.raises(IllegalTransition):
        record_hosting_reason(routed, T1)


def test_record_hosting_reason_resume_of_a_never_routed_site_goes_online() -> None:
    inp = _reasoned(
        CommandKind.RESUME_HOSTING,
        "pay-evt-4",
        {OfflineReason.BILLING},
        routing=RoutingStatus.STABLE,
        live=False,
    )
    out = record_hosting_reason(inp, T1)
    assert out.hosting.hosting_status is HostingStatus.ONLINE
    assert out.hosting.offline_reasons == frozenset()


def test_record_hosting_reason_needs_a_stable_route_and_a_hosting_kind() -> None:
    changing = _reasoned(
        CommandKind.SUSPEND_HOSTING, "pay-evt-5", set(), routing=RoutingStatus.CHANGING, live=False
    )
    with pytest.raises(IllegalTransition):
        record_hosting_reason(changing, T1)
    deploy = _running(
        CommandKind.DEPLOY, ReleaseStatus.BUILT, RoutingStatus.STABLE, HostingStatus.ONLINE, False
    )
    with pytest.raises(IllegalTransition):
        record_hosting_reason(deploy, T1)
