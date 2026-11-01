"""QA-03 (``ai/reviews/qa-bug-hunt.md``, adopted from ``tests/qa/test_qa_failed_suspend_is_never_
retried.py`` at d487207): a suspend or resume that failed once is retried, within a budget, by the
hosting reconcile (decision 0014 section 23, lead's rulings).

QA's sequence, unchanged up to the rollback:
1. Payment sends "entitlement ended"; the hosting intake accepts ``suspend_hosting`` keyed by the
   event.
2. The worker cannot get the offline route confirmed: the command ends ``failed`` and the route is
   ``uncertain``; hosting stays ``online``.
3. The event repeats: the key finds the failed command and returns it as a replay. The hosting
   event path asks for a retry, which the uncertain route defers.
4. The cluster recovers and the operator runs ``rollback``, which restores the recorded state
   (hosting ``online``, so the route goes back to the live release).

Then the reconcile's sweep issues ``<event>.retry-1``: a suspend is pending again, and running it
takes the site offline. QA's assertion holds: the site is never serving without a pending suspend
once the sweep ran. The mirror case, a failed resume, and the budget are below.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    LIVE,
    PROJECT,
    FakeCluster,
    FakeEntitlement,
    FakeHostingEntitlement,
    Route,
    World,
    settings,
    to_release,
)

from provisioning.core.application import AcceptHostingEventCommand
from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.hosting.background.retry import (
    HOSTING_RETRY_BUDGET,
    HostingRetry,
    RetryOutcome,
)
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    AcceptHostingEventHandler,
)
from provisioning.core.application.hosting.ports import HostingIntent
from provisioning.core.application.ports.site_cluster import RouteTargetKind
from provisioning.core.application.routing.background.routing import Routing
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandStatus,
    HostingStatus,
    OfflineReason,
    RoutingStatus,
)

EVENT = "payment-entitlement-ended-0001"


class WorldHistory:
    """``HostingHistory`` over the world's rows: the latest hosting command per reason."""

    def __init__(self, world: World) -> None:
        self.world = world

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        latest: dict[OfflineReason, Command] = {}
        for c in sorted(self.world.commands.values(), key=lambda c: c.command_number):
            if c.project_id == project_id and c.hosting_reason is not None:
                latest[c.hosting_reason] = c
        hosting = self.world.hosting
        return tuple(
            HostingIntent(
                project_id=project_id,
                kind=c.kind,
                idempotency_key=c.idempotency_key,
                status=c.status,
                hosting_reason=reason,
                offline_reasons=hosting.offline_reasons,
                owner_status_version=hosting.owner_status_version,
            )
            for reason, c in sorted(latest.items(), key=lambda item: item[1].command_number)
        )

    async def projects_to_retry(self, *, limit: int, budget: int) -> tuple[UUID, ...]:
        # Every project with hosting commands; the use case decides. The SQL filter is proven on
        # PostgreSQL (tests/integration/test_routing_store.py).
        intents = await self.latest_hosting_intents(self.world.project_id)
        return () if not intents or limit < 1 else (self.world.project_id,)


class Scene:
    def __init__(self, *, hosting: HostingStatus = HostingStatus.ONLINE) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=True, candidate=False)
        if hosting is HostingStatus.OFFLINE:
            self.world.set_reasons(OfflineReason.BILLING)  # a Payment suspend, as these tests imply
        self.cluster = FakeCluster(self.log, serving={LIVE}, answering={LIVE})
        if hosting is HostingStatus.ONLINE:
            self.cluster.route = Route(to_release(LIVE), 0)
        self.entitlement = FakeHostingEntitlement(self.log, None)
        self.intake = AcceptHostingEventHandler(
            store=self.world,
            clock=self.world.clock,
            ids=self.world.ids,
            command_deadline=timedelta(hours=1),
        )
        self.hosting = Hosting(
            store=self.world,
            cluster=self.cluster,
            entitlement=self.entitlement,
            clock=self.world.clock,
            settings=settings(),
        )
        self.routing = Routing(
            store=self.world,
            cluster=self.cluster,
            entitlement=FakeEntitlement(self.log),
            clock=self.world.clock,
            settings=settings(),
        )
        self.retry = HostingRetry(history=WorldHistory(self.world), intake=self.intake)

    async def run(self, kind: CommandKind, command_id: UUID) -> None:
        claim = self.world.claim(self.world.command(command_id))
        if kind is CommandKind.SUSPEND_HOSTING:
            await self.hosting.suspend(claim)
        else:
            await self.hosting.resume(claim)

    async def operator_rollback(self) -> None:
        rollback = self.world.accept(CommandKind.ROLLBACK, LIVE)
        await self.routing.rollback(self.world.claim(rollback))

    def serving(self) -> bool:
        route = self.cluster.route
        return route is not None and route.target.kind is RouteTargetKind.RELEASE

    def pending(self, kind: CommandKind) -> bool:
        return any(c.kind is kind and c.status.unfinished() for c in self.world.commands.values())


@pytest.mark.asyncio
async def test_a_suspend_that_failed_is_not_forgotten_when_the_event_repeats_and_the_route_recovers() -> (  # noqa: E501 - QA's test name
    None
):
    """FR-31.3: a suspend that failed is not forgotten when the event repeats and the route
    recovers."""
    s = Scene()

    # 1 and 2: the suspend cannot be confirmed.
    s.cluster.apply_lands = False
    first = (
        await s.intake.handle(
            AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
        )
    ).command
    await s.run(CommandKind.SUSPEND_HOSTING, first.id)
    assert s.world.command(first.id).status is CommandStatus.FAILED
    assert s.world.routing() is RoutingStatus.UNCERTAIN

    # 3: the same event again; the retry it asks for waits for a stable route.
    again = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )
    assert again.replayed
    deferred = await s.retry.retry(PROJECT)
    assert deferred.outcome is RetryOutcome.DEFERRED

    # 4: the cluster recovers; the operator rolls back to the recorded state.
    s.cluster.apply_lands = True
    await s.operator_rollback()
    assert s.world.routing() is RoutingStatus.STABLE

    # The reconcile's sweep issues the retry of the Payment intent.
    (swept,) = await s.retry.sweep(10)
    assert swept.outcome is RetryOutcome.RETRIED
    assert swept.event_key == f"{EVENT}.retry-1"

    pending_suspend = s.pending(CommandKind.SUSPEND_HOSTING)
    site_is_serving = s.world.hosting_status() is HostingStatus.ONLINE and s.serving()
    assert not (site_is_serving and not pending_suspend), (
        "Payment ended the entitlement, but the site is online and serving, "
        "and no suspend is pending"
    )

    assert swept.accepted is not None
    await s.run(CommandKind.SUSPEND_HOSTING, swept.accepted.command.id)
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert not s.serving()
    # The retry's recheck asked about the events after the original Payment event.
    assert s.entitlement.asked[-1] == EVENT


@pytest.mark.asyncio
async def test_a_failed_resume_does_not_leave_a_paying_customer_offline() -> None:
    s = Scene(hosting=HostingStatus.OFFLINE)
    s.cluster.apply_lands = False
    first = (
        await s.intake.handle(
            AcceptHostingEventCommand("pay-restored-1", PROJECT, CommandKind.RESUME_HOSTING)
        )
    ).command
    await s.run(CommandKind.RESUME_HOSTING, first.id)
    assert s.world.command(first.id).status is CommandStatus.FAILED
    assert s.world.hosting_status() is HostingStatus.OFFLINE

    s.cluster.apply_lands = True
    await s.operator_rollback()
    # The rollback restores the recorded state: offline, so the suspended site does not serve.
    assert s.world.routing() is RoutingStatus.STABLE
    assert not s.serving()

    (swept,) = await s.retry.sweep(10)
    assert swept.outcome is RetryOutcome.RETRIED
    assert swept.accepted is not None
    await s.run(CommandKind.RESUME_HOSTING, swept.accepted.command.id)
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.serving()


@pytest.mark.asyncio
async def test_the_retries_stop_at_the_budget() -> None:
    s = Scene()
    s.cluster.apply_lands = False
    command = (
        await s.intake.handle(
            AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
        )
    ).command
    keys = [command.idempotency_key]
    for _ in range(HOSTING_RETRY_BUDGET):
        await s.run(CommandKind.SUSPEND_HOSTING, command.id)
        assert s.world.command(command.id).status is CommandStatus.FAILED
        s.cluster.apply_lands = True
        await s.operator_rollback()
        s.cluster.apply_lands = False
        result = await s.retry.retry(PROJECT)
        assert result.outcome is RetryOutcome.RETRIED
        assert result.accepted is not None
        command = result.accepted.command
        keys.append(command.idempotency_key)
    await s.run(CommandKind.SUSPEND_HOSTING, command.id)
    s.cluster.apply_lands = True
    await s.operator_rollback()

    spent = await s.retry.retry(PROJECT)
    assert spent.outcome is RetryOutcome.BUDGET_SPENT
    assert keys == [EVENT] + [f"{EVENT}.retry-{n}" for n in range(1, HOSTING_RETRY_BUDGET + 1)]
    suspends = [c for c in s.world.commands.values() if c.kind is CommandKind.SUSPEND_HOSTING]
    assert len(suspends) == HOSTING_RETRY_BUDGET + 1


# --- retries per reason (decision 0021) ----------------------------------------------------------


async def fail_then_recover(s: Scene, kind: CommandKind, key: str) -> UUID:
    """A hosting command whose route change cannot be confirmed; then the operator's rollback
    makes the route stable again. Returns the failed command's ID."""
    s.cluster.apply_lands = False
    command = s.world.accept(kind, key=key)
    await s.run(kind, command.id)
    assert s.world.command(command.id).status is CommandStatus.FAILED
    s.cluster.apply_lands = True
    await s.operator_rollback()
    assert s.world.routing() is RoutingStatus.STABLE
    return command.id


def owner_status_version(s: Scene, version: int) -> None:
    """The owner-status intake recorded ``version`` (Task 6 writes it; set here directly)."""
    s.world.hosting = replace(s.world.hosting, owner_status_version=version)


@pytest.mark.asyncio
async def test_failed_billing_suspend_is_retried_after_a_newer_owner_suspend() -> None:
    s = Scene()
    await fail_then_recover(s, CommandKind.SUSPEND_HOSTING, "evt-1")
    owner = s.world.accept(CommandKind.SUSPEND_HOSTING, key="owner-status:3")
    owner_status_version(s, 3)
    await s.run(CommandKind.SUSPEND_HOSTING, owner.id)
    assert s.world.command(owner.id).status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == {OfflineReason.USER_SUSPENDED}

    # The latest command overall succeeded, but the failed billing suspend is still the latest
    # with its reason, and billing is not held: it is retried.
    (swept,) = await s.retry.sweep(10)
    assert swept.outcome is RetryOutcome.RETRIED
    assert swept.event_key == "evt-1.retry-1"
    assert swept.accepted is not None
    await s.run(CommandKind.SUSPEND_HOSTING, swept.accepted.command.id)
    assert s.world.reasons() == {OfflineReason.USER_SUSPENDED, OfflineReason.BILLING}
    assert (await s.retry.retry(PROJECT)).outcome is RetryOutcome.NOT_NEEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current", "outcome"), [(3, RetryOutcome.RETRIED), (4, RetryOutcome.NOT_NEEDED)]
)
async def test_stale_owner_status_retry_is_not_issued(current: int, outcome: RetryOutcome) -> None:
    s = Scene()
    owner_status_version(s, 3)
    await fail_then_recover(s, CommandKind.SUSPEND_HOSTING, "owner-status:3")
    owner_status_version(s, current)  # version 4 is newer: the failed suspend no longer counts
    result = await s.retry.retry(PROJECT)
    assert result.outcome is outcome
    if outcome is RetryOutcome.NOT_NEEDED:
        assert not s.pending(CommandKind.SUSPEND_HOSTING)
    else:
        assert result.event_key == "owner-status:3.retry-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "key", "reason", "held"),
    [
        (CommandKind.SUSPEND_HOSTING, "evt-1", OfflineReason.BILLING, ()),
        (CommandKind.SUSPEND_HOSTING, "owner-status:3", OfflineReason.USER_SUSPENDED, ()),
        (CommandKind.SUSPEND_HOSTING, "project-deleted", OfflineReason.PROJECT_DELETED, ()),
        (CommandKind.RESUME_HOSTING, "evt-2", OfflineReason.BILLING, (OfflineReason.BILLING,)),
        (
            CommandKind.RESUME_HOSTING,
            "owner-status:3.retry-1",
            OfflineReason.USER_SUSPENDED,
            (OfflineReason.USER_SUSPENDED,),
        ),
    ],
)
async def test_retry_keeps_the_reason(
    kind: CommandKind, key: str, reason: OfflineReason, held: tuple[OfflineReason, ...]
) -> None:
    s = Scene(hosting=HostingStatus.OFFLINE if held else HostingStatus.ONLINE)
    s.world.set_reasons(*held)
    owner_status_version(s, 3)
    failed = await fail_then_recover(s, kind, key)
    assert s.world.command(failed).hosting_reason is reason
    result = await s.retry.retry(PROJECT)
    assert result.outcome is RetryOutcome.RETRIED
    assert result.accepted is not None
    assert result.accepted.command.kind is kind
    assert result.accepted.command.hosting_reason is reason


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING])
async def test_an_admin_command_that_failed_is_never_retried_through_the_event_intake(
    kind: CommandKind,
) -> None:
    """Decision 0023 G11: the event intake refuses an ``admin:`` key, so the retry leaves a failed
    admin command to the admin, and the sweep goes on without raising."""
    held = (OfflineReason.ADMIN_SUSPENDED,) if kind is CommandKind.RESUME_HOSTING else ()
    s = Scene(hosting=HostingStatus.OFFLINE if held else HostingStatus.ONLINE)
    s.world.set_reasons(*held)
    failed = await fail_then_recover(s, kind, "admin:k-1")
    assert s.world.command(failed).hosting_reason is OfflineReason.ADMIN_SUSPENDED
    assert (await s.retry.retry(PROJECT)).outcome is RetryOutcome.NOT_NEEDED
    assert [r.outcome for r in await s.retry.sweep(10)] == [RetryOutcome.NOT_NEEDED]
    assert not s.pending(kind)


class StaleReadHistory(WorldHistory):
    """Reads the intents, then a newer owner status is recorded before the retry is accepted:
    event 4's command finished between the history read and the acceptance."""

    def __init__(self, world: World, newer: int) -> None:
        super().__init__(world)
        self.newer = newer

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        intents = await super().latest_hosting_intents(project_id)
        self.world.hosting = replace(self.world.hosting, owner_status_version=self.newer)
        return intents


@pytest.mark.asyncio
async def test_owner_status_retry_is_rechecked_under_the_lock() -> None:
    """The history said version 3 is current; version 4 was recorded before the acceptance, so
    the decision on the locked row refuses ``owner-status:3.retry-1`` and nothing is stored."""
    s = Scene()
    owner_status_version(s, 3)
    await fail_then_recover(s, CommandKind.SUSPEND_HOSTING, "owner-status:3")
    count = len(s.world.commands)
    retry = HostingRetry(history=StaleReadHistory(s.world, 4), intake=s.intake)
    result = await retry.retry(PROJECT)
    assert result.outcome is RetryOutcome.NOT_NEEDED
    assert len(s.world.commands) == count
    assert not s.pending(CommandKind.SUSPEND_HOSTING)
