"""Recovery on the worker (task W6-B; decision 0014 section 21 rulings 8 and 9).

- ``RouteAbandoner``: the consumer's abandon hook for promote, rollback, suspend and resume. A
  command that passes its deadline, or uses up its attempts, while routing is ``changing`` ends
  ``stable`` or ``uncertain``, never stuck (the gap analysis's first defect).
- ``ReleaseCleanup``: the guarded delete of superseded, rolled-back and failed workloads, never
  the routed release nor the live one.

The fakes (``test_routing_support``) apply the real domain outcome functions and the real
acceptance decision.
"""

import logging
from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    LIVE,
    OFFLINE,
    OTHER_BUILT,
    PROJECT,
    FakeCluster,
    Route,
    World,
    make_release,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application.jobs.audited import AuditedRouteCommandStore
from provisioning.core.application.jobs.claims import Claim
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.site_cluster import ClusterRefused, RouteTarget
from provisioning.core.application.routing.background.recovery import ReleaseCleanup, RouteAbandoner
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.core.domain import (
    GO_LIVE_TRY_CODES,
    Command,
    CommandKind,
    CommandStatus,
    Failure,
    HostingStatus,
    Outcome,
    ReleaseStatus,
    RoutingStatus,
    route_uncertain,
    start_route_change,
)

pytestmark = pytest.mark.asyncio

DEADLINE_EXCEEDED = "deadline_exceeded"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"


class RecordingAudit:
    def __init__(self) -> None:
        self.records: list[AuditEvent] = []
        self.alerts: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.records.append(event)

    def alert(self, event: AuditEvent) -> None:
        self.alerts.append(event)


class Stuck:
    """A world whose command recorded its intent (routing ``changing``), after which the process
    died; ``reclaim()`` is the consumer's next claim, past the deadline."""

    def __init__(self, *, live: bool = True, serving: frozenset[UUID] = frozenset()) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=live)
        self.cluster = FakeCluster(
            self.log, serving={LIVE, CANDIDATE} | set(serving), answering={LIVE, CANDIDATE}
        )
        if live:
            self.cluster.route = Route(to_release(LIVE), 0)
        self.audit = RecordingAudit()
        self.cleanup = ReleaseCleanup(
            cluster=self.cluster,
            audit=self.audit,
            clock=self.world.clock,
            delete_timeout=timedelta(seconds=1),
            logger=logging.getLogger("test.cleanup"),
        )
        # As the worker wires it: the abandoner writes through W6-D's audited store, the single
        # source of the uncertain-route alert.
        self.abandoner = RouteAbandoner(
            store=AuditedRouteCommandStore(self.world, self.audit, self.world.clock),
            cluster=self.cluster,
            clock=self.world.clock,
            settings=settings(),
            cleanup=self.cleanup,
        )
        self.command: Command | None = None

    async def start(self, kind: CommandKind, release_id: UUID | None = None) -> Claim:
        """Accept and claim a command and record its intent, as the use case's first write."""
        self.command = self.world.accept(kind, release_id)
        claim = self.world.claim(self.command)
        await self.world.apply_route_outcome(
            claim.fence(), lambda i: start_route_change(i, self.world.clock.now())
        )
        assert self.world.routing() is RoutingStatus.CHANGING
        return claim

    def reclaim(self) -> Claim:
        assert self.command is not None
        return self.world.claim(self.command)

    def stored(self) -> Command:
        assert self.command is not None
        return self.world.command(self.command.id)


# --- the stuck project (ruling 9) ----------------------------------------------------------


@pytest.mark.parametrize("code", [DEADLINE_EXCEEDED, ATTEMPTS_EXHAUSTED])
async def test_a_promote_stuck_while_changing_is_restored_to_the_live_release(code: str) -> None:
    """The gap analysis's defect: the switch landed, then the worker died past the deadline. The
    abandon hook points the route back at the live release and records ``restore_verified``, so the
    project is stable and the next command is accepted."""
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)
    s.cluster.route = Route(to_release(CANDIDATE), s.stored().command_number)  # the switch landed

    await s.abandoner.abandon(s.reclaim(), code)

    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route == Route(to_release(LIVE), s.stored().command_number)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.world.release(CANDIDATE).failure_code == code
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    assert (s.stored().status, s.stored().failure_code) == (CommandStatus.FAILED, code)
    assert s.audit.alerts == []
    # Never stuck: a new command is accepted.
    s.world.releases[OTHER_BUILT] = make_release(OTHER_BUILT, ReleaseStatus.CANDIDATE_READY, 3)
    later = s.world.accept(CommandKind.PROMOTE, OTHER_BUILT)
    assert later.status is CommandStatus.ACCEPTED


async def test_a_first_publish_stuck_while_changing_goes_offline_and_its_release_fails() -> None:
    s = Stuck(live=False)
    await s.start(CommandKind.PROMOTE, CANDIDATE)
    s.cluster.route = Route(to_release(CANDIDATE), s.stored().command_number)

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route is not None and s.cluster.route.target == OFFLINE
    assert s.world.release(CANDIDATE).status is ReleaseStatus.FAILED
    assert s.stored().status is CommandStatus.FAILED


async def test_a_restore_that_cannot_be_confirmed_ends_uncertain_and_alerts() -> None:
    """Never stuck: when the restore is refused the route is recorded ``uncertain`` (the command
    failed), the alert is raised, and the operator rollback is accepted."""
    s = Stuck()
    claim = await s.start(CommandKind.PROMOTE, CANDIDATE)
    s.cluster.fail_apply[to_release(LIVE)] = ClusterRefused("admission")

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert (s.stored().status, s.stored().failure_code) == (CommandStatus.FAILED, DEADLINE_EXCEEDED)
    (alert,) = s.audit.alerts
    assert alert.action is AuditAction.ROUTE_UNCERTAIN
    assert alert.actor_kind is AuditActorKind.SYSTEM
    assert alert.result is AuditResult.FAILED
    assert (alert.project_id, alert.command_id) == (PROJECT, claim.ref.command_id)
    assert alert.failure_code == DEADLINE_EXCEEDED
    rollback = s.world.accept(CommandKind.ROLLBACK, LIVE)
    assert rollback.status is CommandStatus.ACCEPTED


async def test_an_unconfirmed_restore_within_the_restore_deadline_ends_uncertain() -> None:
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)
    s.cluster.apply_lands = False  # applied, never visible

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert len(s.audit.alerts) == 1


@pytest.mark.parametrize(
    ("kind", "route_before", "target", "hosting"),
    [
        (CommandKind.SUSPEND_HOSTING, to_release(LIVE), OFFLINE, HostingStatus.OFFLINE),
        (CommandKind.RESUME_HOSTING, OFFLINE, to_release(LIVE), HostingStatus.ONLINE),
    ],
)
async def test_a_hosting_command_stuck_while_changing_completes_its_change(
    kind: CommandKind, route_before: RouteTarget, target: RouteTarget, hosting: HostingStatus
) -> None:
    s = Stuck()
    if kind is CommandKind.RESUME_HOSTING:
        s.world.hosting = replace(s.world.hosting, hosting_status=HostingStatus.OFFLINE)
    s.cluster.route = Route(route_before, 0)
    await s.start(kind)

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.hosting_status() is hosting
    assert s.cluster.route is not None and s.cluster.route.target == target
    assert s.stored().status is CommandStatus.SUCCEEDED


async def test_a_hosting_command_whose_change_cannot_be_confirmed_ends_uncertain() -> None:
    s = Stuck()
    await s.start(CommandKind.SUSPEND_HOSTING)
    s.cluster.fail_apply[OFFLINE] = ClusterRefused("admission")

    await s.abandoner.abandon(s.reclaim(), ATTEMPTS_EXHAUSTED)

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.stored().failure_code == ATTEMPTS_EXHAUSTED
    assert [a.action for a in s.audit.alerts] == [AuditAction.ROUTE_UNCERTAIN]


async def test_an_operator_rollback_stuck_while_changing_repeats_its_restore() -> None:
    s = Stuck()
    promote = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    first = s.world.claim(promote)
    await s.world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, s.world.clock.now())
    )
    # The promote's restore was not confirmed: uncertain, failed.
    await s.world.apply_route_outcome(
        first.fence(),
        lambda i: route_uncertain(i, Failure("route_restore_unconfirmed"), s.world.clock.now()),
    )
    rollback_claim = await s.start(CommandKind.ROLLBACK, LIVE)
    del rollback_claim

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route is not None and s.cluster.route.target == to_release(LIVE)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.stored().status is CommandStatus.SUCCEEDED


async def test_a_command_that_never_changed_the_route_is_left_to_the_consumer() -> None:
    """Routing ``stable``: nothing moved, so the abandon hook writes nothing and touches no route;
    the consumer's own failure write ends the command."""
    s = Stuck()
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    claim = s.world.claim(command)
    writes = list(s.world.writes)

    await s.abandoner.abandon(claim, DEADLINE_EXCEEDED)

    assert s.world.writes == writes
    assert s.cluster.applied_targets() == []
    assert s.world.command(command.id).status is CommandStatus.RUNNING


async def test_the_abandon_of_another_kind_does_nothing() -> None:
    s = Stuck()
    s.world.releases[OTHER_BUILT] = make_release(OTHER_BUILT, ReleaseStatus.BUILT, 3)
    deploy = s.world.accept(CommandKind.DEPLOY, OTHER_BUILT)
    await s.abandoner.abandon(s.world.claim(deploy), DEADLINE_EXCEEDED)
    assert s.log == []


# --- cleanup (ruling 8) --------------------------------------------------------------------


async def test_a_verified_restore_deletes_the_rolled_back_candidate_never_the_live_release() -> (
    None
):
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)

    await s.abandoner.abandon(s.reclaim(), DEADLINE_EXCEEDED)

    assert s.cluster.deleted == [CANDIDATE]
    (event,) = (e for e in s.audit.records if e.action is AuditAction.WORKLOAD_CLEANUP)
    assert (event.release_id, event.result) == (CANDIDATE, AuditResult.SUCCEEDED)


def _cleanup(cluster: FakeCluster, audit: RecordingAudit, world: World) -> ReleaseCleanup:
    return ReleaseCleanup(
        cluster=cluster,
        audit=audit,
        clock=world.clock,
        delete_timeout=timedelta(seconds=1),
        logger=logging.getLogger("test.cleanup"),
    )


async def test_cleanup_never_deletes_the_routed_or_the_live_release() -> None:
    """Planted defect: cleanup deleting the routed release. The guard keeps the release the route
    points at and the live release even when asked to delete them, before any cluster call."""
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE, OTHER_BUILT})
    cluster.route = Route(to_release(CANDIDATE), 5)  # the route points at another release
    audit = RecordingAudit()
    cleanup = _cleanup(cluster, audit, world)

    result = await cleanup.delete_releases(
        PROJECT, (LIVE, CANDIDATE, OTHER_BUILT), 7, live_release_id=LIVE, command_id=None
    )

    assert result.deleted == (OTHER_BUILT,)
    assert cluster.deleted == [OTHER_BUILT]
    assert not any(f"delete_release {r}" in line for r in (LIVE, CANDIDATE) for line in log)


async def test_cleanup_deletes_nothing_when_the_route_cannot_be_read() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, OTHER_BUILT})
    cluster.route = Route(to_release(LIVE), 1)
    cluster.fail_status_once = [unavailable()]
    cleanup = _cleanup(cluster, RecordingAudit(), world)

    result = await cleanup.delete_releases(PROJECT, (OTHER_BUILT,), 2, live_release_id=LIVE)
    assert (result.deleted, result.deferred) == ((), ())
    assert cluster.deleted == []


async def test_a_refused_delete_is_deferred_audited_skipped_and_never_raised() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, OTHER_BUILT})
    cluster.route = Route(to_release(LIVE), 1)
    cluster.fail_delete = ClusterRefused("admission: the routed release")
    audit = RecordingAudit()

    result = await _cleanup(cluster, audit, world).delete_releases(
        PROJECT, (OTHER_BUILT,), 2, live_release_id=LIVE
    )

    assert result.deleted == ()
    assert result.deferred == ((OTHER_BUILT, "cluster_refused"),)
    (event,) = audit.records
    assert (event.result, event.failure_code) == (AuditResult.SKIPPED, "cluster_refused")


async def test_after_outcome_deletes_only_superseded_rolled_back_and_failed_releases() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE})
    cluster.route = Route(to_release(CANDIDATE), 3)
    cleanup = _cleanup(cluster, RecordingAudit(), world)
    live_now = world.release(CANDIDATE)
    old_live = world.release(LIVE)
    outcome = Outcome(
        hosting=replace(world.hosting, live_release_id=CANDIDATE),
        command=world.accept(CommandKind.PROMOTE, CANDIDATE),
        release=replace(live_now, status=ReleaseStatus.LIVE),
        superseded_release=replace(old_live, status=ReleaseStatus.SUPERSEDED),
    )

    await cleanup.after_outcome(Fence(PROJECT, outcome.command.id, 3, 1), outcome)

    assert cluster.deleted == [LIVE]


# --- a promote abandoned after a failed try keeps that try's code (TR-57; decision 0027 T9) -------


def _with_code(claim: Claim, code: str | None) -> Claim:
    """The claim as ``JobStore.claim`` reads it from a dispatch row holding ``code``."""
    return replace(claim, last_failure_code=code)


@pytest.mark.parametrize("code", sorted(GO_LIVE_TRY_CODES))
async def test_tr57_a_promote_abandoned_after_a_failed_try_ends_with_that_try_code(
    code: str,
) -> None:
    """TR-57, FR-21, FR-37.3: the deadline passed while a try waited; the row still holds the try's
    code, so the release and the command end with it and the client sees why the go-live failed."""
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)

    written = await s.abandoner.abandon(_with_code(s.reclaim(), code), DEADLINE_EXCEEDED)

    assert written == code
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route == Route(to_release(LIVE), s.stored().command_number)
    assert (s.world.release(CANDIDATE).status, s.world.release(CANDIDATE).failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        code,
    )
    assert (s.stored().status, s.stored().failure_code) == (CommandStatus.FAILED, code)


async def test_tr57_a_first_publish_abandoned_after_a_failed_try_fails_with_that_try_code() -> None:
    """TR-57, FR-22: with no live release the route goes offline and the release fails with the
    try's code."""
    s = Stuck(live=False)
    await s.start(CommandKind.PROMOTE, CANDIDATE)

    written = await s.abandoner.abandon(
        _with_code(s.reclaim(), "site_not_answering"), ATTEMPTS_EXHAUSTED
    )

    assert written == "site_not_answering"
    assert s.cluster.route is not None and s.cluster.route.target == OFFLINE
    assert (s.world.release(CANDIDATE).status, s.world.release(CANDIDATE).failure_code) == (
        ReleaseStatus.FAILED,
        "site_not_answering",
    )
    assert (s.stored().status, s.stored().failure_code) == (
        CommandStatus.FAILED,
        "site_not_answering",
    )


@pytest.mark.parametrize("row_code", [None, "worker_stopping", "transient_failure"])
async def test_tr57_a_promote_whose_row_holds_another_code_ends_with_the_consumer_code(
    row_code: str | None,
) -> None:
    """TR-57: a stop or a database error after the failed try overwrote its code in the row, so
    the abandon writes the consumer's code, as before 0027."""
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)

    written = await s.abandoner.abandon(_with_code(s.reclaim(), row_code), DEADLINE_EXCEEDED)

    assert written == DEADLINE_EXCEEDED
    assert s.world.release(CANDIDATE).failure_code == DEADLINE_EXCEEDED
    assert (s.stored().status, s.stored().failure_code) == (
        CommandStatus.FAILED,
        DEADLINE_EXCEEDED,
    )


async def test_tr57_an_unconfirmed_abandon_keeps_the_consumer_code() -> None:
    """TR-57, FR-23.1: the restore is not confirmed, so the route is uncertain with the consumer's
    code; the release stays promoting and gets no code until the operator rollback."""
    s = Stuck()
    await s.start(CommandKind.PROMOTE, CANDIDATE)
    s.cluster.fail_apply[to_release(LIVE)] = ClusterRefused("admission")

    written = await s.abandoner.abandon(
        _with_code(s.reclaim(), "route_not_accepted"), DEADLINE_EXCEEDED
    )

    assert written == DEADLINE_EXCEEDED
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert (s.stored().status, s.stored().failure_code) == (
        CommandStatus.FAILED,
        DEADLINE_EXCEEDED,
    )
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.world.release(CANDIDATE).failure_code is None


async def test_tr57_an_abandon_that_writes_nothing_returns_none() -> None:
    """TR-57: routing ``stable`` (no intent was written) or another kind: no outcome, None."""
    s = Stuck()
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    claim = _with_code(s.world.claim(command), "route_refused")
    writes = list(s.world.writes)

    assert await s.abandoner.abandon(claim, DEADLINE_EXCEEDED) is None
    assert s.world.writes == writes
    assert s.cluster.applied_targets() == []

    other = Stuck()
    other.world.releases[OTHER_BUILT] = make_release(OTHER_BUILT, ReleaseStatus.BUILT, 3)
    deploy = other.world.claim(other.world.accept(CommandKind.DEPLOY, OTHER_BUILT))
    assert await other.abandoner.abandon(deploy, DEADLINE_EXCEEDED) is None
    assert other.log == []


async def test_tr57_a_rollback_or_hosting_abandon_ignores_a_try_code() -> None:
    """TR-57: only a promote keeps a try code. A confirmed rollback or hosting change writes no
    failure code (its command succeeds), so None; an unconfirmed hosting change writes the
    consumer's code."""
    s = Stuck()
    promote = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    first = s.world.claim(promote)
    await s.world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, s.world.clock.now())
    )
    await s.world.apply_route_outcome(
        first.fence(),
        lambda i: route_uncertain(i, Failure("route_restore_unconfirmed"), s.world.clock.now()),
    )
    await s.start(CommandKind.ROLLBACK, LIVE)

    written = await s.abandoner.abandon(
        _with_code(s.reclaim(), "route_not_accepted"), DEADLINE_EXCEEDED
    )

    assert written is None
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.world.release(CANDIDATE).failure_code == "operator_rollback"
    assert s.stored().status is CommandStatus.SUCCEEDED

    hosting = Stuck()
    hosting.cluster.route = Route(to_release(LIVE), 0)
    await hosting.start(CommandKind.SUSPEND_HOSTING)
    written = await hosting.abandoner.abandon(
        _with_code(hosting.reclaim(), "route_not_accepted"), DEADLINE_EXCEEDED
    )
    assert written is None
    assert hosting.stored().status is CommandStatus.SUCCEEDED

    uncertain = Stuck()
    await uncertain.start(CommandKind.SUSPEND_HOSTING)
    uncertain.cluster.fail_apply[OFFLINE] = ClusterRefused("admission")
    written = await uncertain.abandoner.abandon(
        _with_code(uncertain.reclaim(), "route_not_accepted"), ATTEMPTS_EXHAUSTED
    )
    assert written == ATTEMPTS_EXHAUSTED
    assert uncertain.stored().failure_code == ATTEMPTS_EXHAUSTED
