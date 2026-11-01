"""The executor of suspend_hosting and resume_hosting commands (task F2): a thin adapter from a
claim to the hosting use case."""

from dataclasses import replace
from datetime import timedelta

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    LIVE,
    OFFLINE,
    FakeCluster,
    FakeHostingEntitlement,
    Route,
    World,
    settings,
    to_release,
)

from provisioning.core.application import FAILURE_CODE_OPERATION_UNAVAILABLE, PermanentFailure
from provisioning.core.application.hosting.background.hosting_executor import HostingExecutor
from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.ports.site_cluster import ClusterRefused
from provisioning.core.application.routing.background.recovery import RouteAbandoner
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    HostingStatus,
    ReleaseStatus,
    RoutingStatus,
    start_route_change,
)
from provisioning.presentation.messaging.consumers.consumer import Abandoner

ABANDON_TIMEOUT = timedelta(seconds=5)


def executor(
    world: World, entitlement: FakeHostingEntitlement, cluster: FakeCluster | None = None
) -> HostingExecutor:
    cluster = cluster if cluster is not None else FakeCluster(world.log, serving={LIVE})
    hosting = Hosting(
        store=world,
        cluster=cluster,
        entitlement=entitlement,
        clock=world.clock,
        settings=settings(),
    )
    abandoner = RouteAbandoner(store=world, cluster=cluster, clock=world.clock, settings=settings())
    return HostingExecutor(hosting, abandoner, abandon_timeout=ABANDON_TIMEOUT)


@pytest.mark.asyncio
async def test_suspend_and_resume_claims_run_their_use_cases() -> None:
    log: list[str] = []
    world = World(log, candidate=False)
    entitlement = FakeHostingEntitlement(log)
    run = executor(world, entitlement)

    suspend = world.accept(CommandKind.SUSPEND_HOSTING, key="evt-ended")
    await run.execute(world.claim(suspend))
    assert world.command(suspend.id).status is CommandStatus.SUCCEEDED
    assert world.hosting_status() is HostingStatus.OFFLINE

    resume = world.accept(CommandKind.RESUME_HOSTING, key="evt-restored")
    await run.execute(world.claim(resume))
    assert world.command(resume.id).status is CommandStatus.SUCCEEDED
    assert world.hosting_status() is HostingStatus.ONLINE


@pytest.mark.parametrize("kind", [CommandKind.PROMOTE, CommandKind.ROLLBACK, CommandKind.DEPLOY])
@pytest.mark.asyncio
async def test_another_kind_is_refused_without_calling_anything(kind: CommandKind) -> None:
    log: list[str] = []
    world = World(log)
    if kind is CommandKind.ROLLBACK:
        world.releases[CANDIDATE] = replace(
            world.releases[CANDIDATE], status=ReleaseStatus.PROMOTING
        )
        world.hosting = replace(world.hosting, routing_status=RoutingStatus.UNCERTAIN)
    if kind is CommandKind.DEPLOY:
        world.releases[CANDIDATE] = replace(world.releases[CANDIDATE], status=ReleaseStatus.BUILT)
    command = world.accept(kind, CANDIDATE)
    claim = world.claim(command)
    log.clear()
    with pytest.raises(PermanentFailure) as raised:
        await executor(world, FakeHostingEntitlement(log)).execute(claim)
    assert raised.value.code == FAILURE_CODE_OPERATION_UNAVAILABLE
    assert log == []


def test_the_hosting_executor_is_an_abandoner_with_its_timeout() -> None:
    world = World([])
    run = executor(world, FakeHostingEntitlement(world.log))
    assert isinstance(run, Abandoner)
    assert run.abandon_timeout == ABANDON_TIMEOUT


@pytest.mark.asyncio
async def test_abandoning_a_suspend_stuck_while_changing_takes_the_site_offline() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE})
    cluster.route = Route(to_release(LIVE), 0)
    command = world.accept(CommandKind.SUSPEND_HOSTING, key="payment-1")
    first = world.claim(command)
    await world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, world.clock.now())
    )

    written = await executor(world, FakeHostingEntitlement(log), cluster).abandon(
        world.claim(command), "attempts_exhausted"
    )

    assert written is None  # TR-57 (OQ-5 b): a confirmed suspend succeeds, no failure code

    assert world.routing() is RoutingStatus.STABLE
    assert world.hosting_status() is HostingStatus.OFFLINE
    assert cluster.route is not None and cluster.route.target == OFFLINE


@pytest.mark.asyncio
async def test_tr57_abandoning_a_hosting_change_returns_the_code_the_abandoner_wrote() -> None:
    """TR-57 (OQ-5 b): an unconfirmed hosting change is recorded uncertain with the consumer's
    code, and the executor passes that code on."""
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE})
    cluster.route = Route(to_release(LIVE), 0)
    cluster.fail_apply[OFFLINE] = ClusterRefused("admission")
    command = world.accept(CommandKind.SUSPEND_HOSTING, key="payment-1")
    first = world.claim(command)
    await world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, world.clock.now())
    )

    written = await executor(world, FakeHostingEntitlement(log), cluster).abandon(
        world.claim(command), "attempts_exhausted"
    )

    assert written == "attempts_exhausted"
    assert world.routing() is RoutingStatus.UNCERTAIN
    assert world.command(command.id).failure_code == "attempts_exhausted"
