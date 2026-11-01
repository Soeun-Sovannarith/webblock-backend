"""Routing's wave 6 recovery parts (task W6-B): the inline cleanup after a verified outcome
(decision 0014 section 21 ruling 8), the uncertain-route alert (section 23), and the operator
rollback that takes back a route edited by hand (QA-07, section 23 item 3).
"""

import logging
from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_recovery import RecordingAudit
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    HOSTNAME,
    LIVE,
    OFFLINE,
    PREVIEW_HOSTNAME,
    PROJECT,
    FakeCluster,
    FakeEntitlement,
    Route,
    World,
    run_failed_tries,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application.jobs.audited import AuditedRouteCommandStore
from provisioning.core.application.jobs.claims import RetryLater
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    FenceRejected,
    SiteInventory,
)
from provisioning.core.application.routing.background.recovery import ReleaseCleanup, RouteAbandoner
from provisioning.core.application.routing.background.routing import (
    PROBE_CONFIRMATIONS,
    PROBE_FAILURES_AFTER_ANSWER,
    Routing,
)
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandStatus,
    Failure,
    ReleaseStatus,
    RoutingStatus,
    route_uncertain,
    start_route_change,
)

pytestmark = pytest.mark.asyncio

ROUTE = f"route-{PROJECT}"


class Setup:
    def __init__(self, *, live: bool = True) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=live)
        self.cluster = FakeCluster(self.log, serving={LIVE, CANDIDATE}, answering={CANDIDATE})
        if live:
            self.cluster.route = Route(to_release(LIVE), 0)
        self.audit = RecordingAudit()
        self.routing = Routing(
            store=AuditedRouteCommandStore(self.world, self.audit, self.world.clock),
            cluster=self.cluster,
            entitlement=FakeEntitlement(self.log),
            clock=self.world.clock,
            settings=settings(),
            audit=self.audit,
            cleanup=ReleaseCleanup(
                cluster=self.cluster,
                audit=self.audit,
                clock=self.world.clock,
                delete_timeout=timedelta(seconds=1),
                logger=logging.getLogger("test.cleanup"),
            ),
        )

    async def uncertain(self) -> None:
        """A promote whose restore was not confirmed: the route is recorded uncertain."""
        promote = self.world.accept(CommandKind.PROMOTE, CANDIDATE)
        claim = self.world.claim(promote)
        await self.world.apply_route_outcome(
            claim.fence(), lambda i: start_route_change(i, self.world.clock.now())
        )
        await self.world.apply_route_outcome(
            claim.fence(),
            lambda i: route_uncertain(
                i, Failure("route_restore_unconfirmed"), self.world.clock.now()
            ),
        )
        assert self.world.routing() is RoutingStatus.UNCERTAIN

    async def rollback(self) -> Command:
        command = self.world.accept(CommandKind.ROLLBACK, LIVE)
        await self.routing.rollback(self.world.claim(command))
        return self.world.command(command.id)

    def reclaims(self) -> list[str]:
        return [line for line in self.log if line.startswith("reclaim_route")]


def cleanups(s: Setup) -> list[AuditEvent]:
    return [e for e in s.audit.records if e.action is AuditAction.WORKLOAD_CLEANUP]


# --- cleanup inline (ruling 8) -------------------------------------------------------------


async def test_a_confirmed_promotion_deletes_the_superseded_release_only() -> None:
    s = Setup()
    s.cluster.answering = {CANDIDATE}
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    await s.routing.promote(s.world.claim(command))

    assert s.world.release(LIVE).status is ReleaseStatus.SUPERSEDED
    assert s.cluster.deleted == [LIVE]
    assert CANDIDATE in s.cluster.serving
    (event,) = cleanups(s)
    assert (event.action, event.release_id) == (AuditAction.WORKLOAD_CLEANUP, LIVE)
    assert event.command_id == command.id


async def test_a_verified_restore_deletes_the_rolled_back_candidate() -> None:
    """FR-21.1: the candidate's workload is deleted only once try 3 rolled it back."""
    s = Setup()
    s.cluster.preview_answering = {CANDIDATE}
    s.cluster.answering = {LIVE}  # the site never answers with the candidate after the switch
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    await run_failed_tries(s.routing, s.world, command, 2)
    assert s.cluster.deleted == []  # nothing is deleted between tries

    await s.routing.promote(s.world.claim(command))

    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK
    assert s.cluster.deleted == [CANDIDATE]
    assert s.cluster.route == Route(to_release(LIVE), command.command_number)


async def test_a_first_publish_that_goes_offline_deletes_its_failed_release() -> None:
    """FR-22.1: try 3 of a first deployment fails; its release is deleted."""
    s = Setup(live=False)
    s.cluster.preview_answering = {CANDIDATE}
    s.cluster.answering = set()
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    await run_failed_tries(s.routing, s.world, command, 2)

    await s.routing.promote(s.world.claim(command))

    assert s.world.release(CANDIDATE).status is ReleaseStatus.FAILED
    assert s.cluster.route is not None and s.cluster.route.target == OFFLINE
    assert s.cluster.deleted == [CANDIDATE]


async def test_a_failed_cleanup_never_fails_the_promotion() -> None:
    s = Setup()
    s.cluster.fail_delete = unavailable()
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    await s.routing.promote(s.world.claim(command))

    assert s.world.command(command.id).status is CommandStatus.SUCCEEDED
    (event,) = cleanups(s)
    assert (event.result, event.failure_code) == (AuditResult.FAILED, "cluster_unavailable")


# --- the uncertain-route alert --------------------------------------------------------------


async def test_a_restore_that_is_not_confirmed_raises_the_alert() -> None:
    s = Setup()
    s.cluster.preview_answering = {CANDIDATE}
    s.cluster.answering = set()
    s.cluster.fail_apply[to_release(LIVE)] = FenceRejected("route-x carries a higher number")
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    await s.routing.promote(s.world.claim(command))

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    (alert,) = s.audit.alerts
    assert alert.action is AuditAction.ROUTE_UNCERTAIN
    assert (alert.command_id, alert.release_id) == (command.id, CANDIDATE)
    assert alert.failure_code == "route_restore_unconfirmed"


# --- QA-07: the operator rollback takes back a route edited by hand -------------------------


async def test_qa07_a_route_stamped_with_a_higher_number_is_reclaimed_by_the_rollback() -> None:
    """QA-07 form (a): ``kubectl annotate ... command-number=999999``. The never-forced apply is
    refused by the fence, so only the forced, audited reclaim can leave ``uncertain``."""
    s = Setup()
    await s.uncertain()
    s.cluster.route = Route(to_release(CANDIDATE), 999_999)

    command = await s.rollback()

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route == Route(to_release(LIVE), command.command_number)
    assert s.reclaims() == [f"reclaim_route release {LIVE} #{command.command_number}"]
    assert not any(line.startswith("apply_route") for line in s.log)  # read first, then reclaim
    (event,) = (e for e in s.audit.records if e.action is AuditAction.ROUTE_RECLAIM)
    assert event.actor_kind is AuditActorKind.OPERATOR
    assert event.actor == "operator@example.test"
    assert (event.result, event.command_id) == (AuditResult.SUCCEEDED, command.id)


async def test_qa07_a_route_edited_by_a_foreign_manager_is_reclaimed_by_the_rollback() -> None:
    """QA-07 form (b): ``kubectl patch`` of a rule field: the route has a foreign field
    manager and a never-forced apply conflicts."""
    s = Setup()
    await s.uncertain()
    s.cluster.foreign_managers[ROUTE] = ("kubectl-patch",)
    s.cluster.fail_apply[to_release(LIVE)] = ApplyConflict("conflict with kubectl-patch")

    command = await s.rollback()

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.routing() is RoutingStatus.STABLE
    assert len(s.reclaims()) == 1
    assert ROUTE not in s.cluster.foreign_managers


async def test_a_refused_apply_is_followed_by_the_check_and_the_reclaim() -> None:
    """The first read of the route failed (unknown is not taken), the never-forced apply is
    refused, the second read shows the higher number: the reclaim follows."""
    s = Setup()
    await s.uncertain()
    s.cluster.route = Route(to_release(CANDIDATE), 999_999)
    reads = 0
    original = s.cluster.inventory

    async def flaky(project_id: UUID) -> SiteInventory:
        nonlocal reads
        reads += 1
        if reads == 1:
            raise unavailable()
        return await original(project_id)

    s.cluster.inventory = flaky  # type: ignore[method-assign]  # a failing first read

    command = await s.rollback()

    assert command.status is CommandStatus.SUCCEEDED
    assert reads == 2
    assert [line.split(" #")[0] for line in s.log if "_route " in line] == [
        f"apply_route release {LIVE}",
        f"reclaim_route release {LIVE}",
    ]


async def test_a_rollback_of_a_route_that_was_not_taken_never_forces() -> None:
    s = Setup()
    await s.uncertain()

    command = await s.rollback()

    assert command.status is CommandStatus.SUCCEEDED
    assert s.reclaims() == []
    assert not any(e.action is AuditAction.ROUTE_RECLAIM for e in s.audit.records)


async def test_a_rollback_whose_reclaim_is_not_confirmed_stays_uncertain_and_alerts() -> None:
    s = Setup()
    await s.uncertain()
    s.cluster.route = Route(to_release(CANDIDATE), 999_999)
    s.cluster.fail_reclaim = unavailable()

    command = await s.rollback()

    assert command.status is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    (event,) = (e for e in s.audit.records if e.action is AuditAction.ROUTE_RECLAIM)
    assert (event.result, event.failure_code) == (AuditResult.FAILED, "route_reclaim_unconfirmed")
    assert [a.action for a in s.audit.alerts] == [AuditAction.ROUTE_UNCERTAIN]


async def test_a_promotion_never_reclaims_a_taken_route() -> None:
    """Only the operator rollback may force (section 23 item 3): a promotion against a route
    stamped with a higher number ends uncertain after its last try without any forced write."""
    s = Setup()
    s.cluster.preview_answering = {CANDIDATE}
    s.cluster.route = Route(to_release(LIVE), 999_999)
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    await run_failed_tries(s.routing, s.world, command, 2)

    await s.routing.promote(s.world.claim(command))

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.reclaims() == []


# --- QA-08: a flapping site is not promoted --------------------------------------------------


def _flapping(cluster: FakeCluster, hostname: str) -> None:
    """Every probe run of ``hostname`` alternates: 200 with the candidate's header, then 503."""
    runs = 0
    preview = hostname == PREVIEW_HOSTNAME
    if preview:
        cluster.preview_answering = set()

    def flap(host: str) -> None:
        nonlocal runs
        if host != hostname:
            if preview:
                cluster.answering = {CANDIDATE}
            return
        runs += 1
        answering = {CANDIDATE} if runs % 2 == 1 else set()
        if preview:
            cluster.preview_answering = answering
        else:
            cluster.answering = answering

    if not preview:
        cluster.preview_answering = {CANDIDATE}
    cluster.on_probe = flap


async def test_qa08_a_candidate_that_flaps_on_its_preview_is_never_promoted() -> None:
    s = Setup()
    _flapping(s.cluster, PREVIEW_HOSTNAME)
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    with pytest.raises(RetryLater) as raised:
        await s.routing.promote(s.world.claim(command))

    assert raised.value.code == "candidate_not_answering"  # a failed try (FR-21.6)
    assert s.world.release(CANDIDATE).status is ReleaseStatus.PROMOTING
    assert s.cluster.route == Route(to_release(LIVE), 0)  # the site's route never moved
    # Bounded: it stopped after its allowed failed runs, long before the switch deadline.
    assert len(s.cluster.preview_probes()) <= 1 + PROBE_CONFIRMATIONS + PROBE_FAILURES_AFTER_ANSWER


async def test_qa08_a_site_that_flaps_after_the_switch_is_restored() -> None:
    """FR-21.1: a site that flaps on every try is rolled back after try 3."""
    s = Setup()
    _flapping(s.cluster, HOSTNAME)
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    await run_failed_tries(s.routing, s.world, command, 2)

    await s.routing.promote(s.world.claim(command))

    assert s.world.command(command.id).failure_code == "site_not_answering"
    assert s.world.release(CANDIDATE).status is ReleaseStatus.ROLLED_BACK


async def test_qa08_slow_start_failures_before_the_first_answer_are_retried() -> None:
    """Before the candidate first answers (pods starting, the Gateway still on the old backend),
    the probe keeps trying until the deadline; a steady answer then promotes."""
    s = Setup()
    s.cluster.stale_site_probes = 6
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    await s.routing.promote(s.world.claim(command))

    assert s.world.command(command.id).status is CommandStatus.SUCCEEDED


# --- one uncertain outcome, exactly one alert (lead's ruling 1) -----------------------------


async def test_one_uncertain_promotion_gives_exactly_one_alert() -> None:
    """The audited store is the single source of the alert: Routing raises none of its own."""
    s = Setup()
    s.cluster.preview_answering = {CANDIDATE}
    s.cluster.answering = set()
    s.cluster.fail_apply[to_release(LIVE)] = FenceRejected("a higher number")
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)

    await s.routing.promote(s.world.claim(command))

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    alerts = [a for a in s.audit.alerts if a.command_id == command.id]
    assert [a.action for a in alerts] == [AuditAction.ROUTE_UNCERTAIN]


async def test_an_abandon_that_ends_uncertain_gives_exactly_one_alert() -> None:
    s = Setup()
    command = s.world.accept(CommandKind.PROMOTE, CANDIDATE)
    await s.world.apply_route_outcome(
        s.world.claim(command).fence(), lambda i: start_route_change(i, s.world.clock.now())
    )
    s.cluster.fail_apply[to_release(LIVE)] = FenceRejected("a higher number")
    abandoner = RouteAbandoner(
        store=AuditedRouteCommandStore(s.world, s.audit, s.world.clock),
        cluster=s.cluster,
        clock=s.world.clock,
        settings=settings(),
    )

    await abandoner.abandon(s.world.claim(command), "deadline_exceeded")

    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert [a.command_id for a in s.audit.alerts] == [command.id]
