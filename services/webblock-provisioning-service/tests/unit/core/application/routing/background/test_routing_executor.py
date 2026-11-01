"""The executor of promote and rollback commands (task F2): a thin adapter from a claim to the
routing use case."""

from dataclasses import replace
from datetime import timedelta

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    LIVE,
    FakeCluster,
    FakeEntitlement,
    Route,
    World,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application import FAILURE_CODE_OPERATION_UNAVAILABLE, PermanentFailure
from provisioning.core.application.routing.background.recovery import RouteAbandoner
from provisioning.core.application.routing.background.routing import Routing
from provisioning.core.application.routing.background.routing_executor import RoutingExecutor
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    ReleaseStatus,
    RoutingStatus,
    start_route_change,
)
from provisioning.presentation.messaging.consumers.consumer import Abandoner

ABANDON_TIMEOUT = timedelta(seconds=5)


def executor(world: World, cluster: FakeCluster) -> RoutingExecutor:
    routing = Routing(
        store=world,
        cluster=cluster,
        entitlement=FakeEntitlement(world.log),
        clock=world.clock,
        settings=settings(),
    )
    abandoner = RouteAbandoner(store=world, cluster=cluster, clock=world.clock, settings=settings())
    return RoutingExecutor(routing, abandoner, abandon_timeout=ABANDON_TIMEOUT)


@pytest.mark.asyncio
async def test_a_promote_claim_runs_the_promotion() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE}, answering={CANDIDATE})
    command = world.accept(CommandKind.PROMOTE, CANDIDATE)

    await executor(world, cluster).execute(world.claim(command))

    assert world.command(command.id).status is CommandStatus.SUCCEEDED
    assert world.release(CANDIDATE).status is ReleaseStatus.LIVE


@pytest.mark.asyncio
async def test_a_rollback_claim_runs_the_operator_rollback() -> None:
    log: list[str] = []
    world = World(log)
    # The candidate answers on its preview, not once the site's route moved; the restore fails.
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE}, preview_answering={CANDIDATE})
    cluster.fail_apply[to_release(LIVE)] = unavailable()
    run = executor(world, cluster)
    await run.execute(world.claim(world.accept(CommandKind.PROMOTE, CANDIDATE)))
    assert world.routing() is RoutingStatus.UNCERTAIN

    cluster.fail_apply.clear()
    rollback = world.accept(CommandKind.ROLLBACK, CANDIDATE)
    await run.execute(world.claim(rollback))

    assert world.command(rollback.id).status is CommandStatus.SUCCEEDED
    assert world.routing() is RoutingStatus.STABLE


@pytest.mark.parametrize("kind", [CommandKind.SUSPEND_HOSTING, CommandKind.DEPLOY])
@pytest.mark.asyncio
async def test_another_kind_is_refused_without_calling_anything(kind: CommandKind) -> None:
    log: list[str] = []
    world = World(log)
    world.releases[CANDIDATE] = replace(world.releases[CANDIDATE], status=ReleaseStatus.BUILT)
    command = world.accept(kind, None if kind.is_hosting() else CANDIDATE, key="k-1")
    claim = world.claim(command)
    log.clear()
    with pytest.raises(PermanentFailure) as raised:
        await executor(world, FakeCluster(log)).execute(claim)
    assert raised.value.code == FAILURE_CODE_OPERATION_UNAVAILABLE
    assert log == []


def test_the_routing_executor_is_an_abandoner_with_its_timeout() -> None:
    """The gap analysis's defect: without an abandon hook the consumer could never end a promote
    that ran out of time while the route was changing."""
    log: list[str] = []
    world = World(log)
    run = executor(world, FakeCluster(log))
    assert isinstance(run, Abandoner)
    assert run.abandon_timeout == ABANDON_TIMEOUT


@pytest.mark.parametrize("timeout", [timedelta(0), timedelta(milliseconds=150)])
def test_an_abandon_timeout_not_above_the_restore_deadline_is_refused(timeout: timedelta) -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log)
    routing = Routing(
        store=world,
        cluster=cluster,
        entitlement=FakeEntitlement(log),
        clock=world.clock,
        settings=settings(),
    )
    abandoner = RouteAbandoner(store=world, cluster=cluster, clock=world.clock, settings=settings())
    with pytest.raises(ValueError, match="abandon timeout"):
        RoutingExecutor(routing, abandoner, abandon_timeout=timeout)


@pytest.mark.asyncio
async def test_abandoning_a_promote_stuck_while_changing_restores_the_live_release() -> None:
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE}, answering={CANDIDATE})
    cluster.route = Route(to_release(CANDIDATE), 1)
    command = world.accept(CommandKind.PROMOTE, CANDIDATE)
    first = world.claim(command)
    await world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, world.clock.now())
    )

    written = await executor(world, cluster).abandon(world.claim(command), "deadline_exceeded")

    assert written == "deadline_exceeded"
    assert world.routing() is RoutingStatus.STABLE
    assert world.command(command.id).failure_code == "deadline_exceeded"
    assert cluster.route == Route(to_release(LIVE), command.command_number)


@pytest.mark.asyncio
async def test_tr57_abandoning_a_promote_returns_the_try_code_the_abandoner_wrote() -> None:
    """TR-57 (OQ-5 b): the executor passes on the code ``RouteAbandoner`` wrote, so the
    consumer's log names it."""
    log: list[str] = []
    world = World(log)
    cluster = FakeCluster(log, serving={LIVE, CANDIDATE}, answering={CANDIDATE})
    command = world.accept(CommandKind.PROMOTE, CANDIDATE)
    first = world.claim(command)
    await world.apply_route_outcome(
        first.fence(), lambda i: start_route_change(i, world.clock.now())
    )
    claim = replace(world.claim(command), last_failure_code="route_not_accepted")

    written = await executor(world, cluster).abandon(claim, "deadline_exceeded")

    assert written == "route_not_accepted"
    assert world.command(command.id).failure_code == "route_not_accepted"


@pytest.mark.asyncio
async def test_abandoning_another_kind_does_nothing() -> None:
    log: list[str] = []
    world = World(log)
    world.releases[CANDIDATE] = replace(world.releases[CANDIDATE], status=ReleaseStatus.BUILT)
    claim = world.claim(world.accept(CommandKind.DEPLOY, CANDIDATE))
    log.clear()
    assert await executor(world, FakeCluster(log)).abandon(claim, "deadline_exceeded") is None
    assert log == []
