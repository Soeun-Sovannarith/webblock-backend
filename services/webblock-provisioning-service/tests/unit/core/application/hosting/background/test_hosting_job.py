"""Hosting intake from Payment events, and the suspend and resume commands (task F2; decision
0001 sections 3 and 4, decision 0014 sections 3, 4 and 10.5).

The world applies the real acceptance decision and the real domain outcome functions
(``test_routing_support``).
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    CANDIDATE,
    HOSTNAME,
    LIVE,
    OFFLINE,
    PROJECT,
    FakeCluster,
    FakeHostingEntitlement,
    Route,
    World,
    make_release,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application import (
    AcceptedCommand,
    AcceptHostingEventCommand,
    Claim,
    ClaimLost,
    Contention,
    DependencyUnavailable,
    NotFound,
    OperationInProgress,
    Timeout,
)
from provisioning.core.application.hosting.background.hosting_job import (
    CODE_ENTITLEMENT_ENDED,
    CODE_ENTITLEMENT_RESTORED,
    CODE_HOSTING_ALREADY_OFFLINE,
    CODE_HOSTING_ALREADY_ONLINE,
    CODE_ROUTE_CHANGE_UNCONFIRMED,
    DELETION_EVENT_KEY,
    Hosting,
    root_event_key,
)
from provisioning.core.application.hosting.background.retry import (
    HostingRetry,
    RetryOutcome,
    retry_key,
)
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    AcceptHostingEventHandler,
    retry_hosting_event_later,
)
from provisioning.core.application.hosting.ports import HostingIntent
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandStatus,
    HostingStatus,
    IdempotencyConflict,
    InvalidCommandTarget,
    InvalidIdempotencyKey,
    OfflineReason,
    OwnerStatusOutdated,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
    hosting_reason_of_key,
    owner_status_key,
    owner_status_version_of_key,
    split_retry_key,
)

EVENT = "payment-evt-0001"
BILLING = OfflineReason.BILLING
USER = OfflineReason.USER_SUSPENDED
DELETED = OfflineReason.PROJECT_DELETED
SKIP_CODES = {
    CODE_HOSTING_ALREADY_OFFLINE,
    CODE_HOSTING_ALREADY_ONLINE,
    CODE_ENTITLEMENT_RESTORED,
    CODE_ENTITLEMENT_ENDED,
}


class Setup:
    def __init__(self, *, live: bool = True, later: bool | None = None) -> None:
        self.log: list[str] = []
        self.world = World(self.log, live=live, candidate=False)
        self.cluster = FakeCluster(self.log, serving={LIVE})
        if live:
            self.cluster.route = Route(to_release(LIVE), 0)
        self.entitlement = FakeHostingEntitlement(self.log, later)
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

    async def event(self, kind: CommandKind, key: str = EVENT) -> Command:
        if owner_status_version_of_key(key) is not None:
            # The owner-status intake records the key's version, then accepts (decision 0021).
            return self.world.accept(kind, key=key)
        return (await self.intake.handle(AcceptHostingEventCommand(key, PROJECT, kind))).command

    async def run(self, kind: CommandKind, key: str = EVENT) -> Command:
        command = await self.event(kind, key)
        claim = self.world.claim(command)
        if kind is CommandKind.SUSPEND_HOSTING:
            await self.hosting.suspend(claim)
        else:
            await self.hosting.resume(claim)
        return self.world.command(command.id)


# --- intake -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_payment_event_becomes_a_hosting_command_keyed_by_the_event() -> None:
    """FR-26: a payment event becomes a hosting command keyed by the event."""
    s = Setup()
    accepted = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )

    command = accepted.command
    assert not accepted.replayed
    assert command.kind is CommandKind.SUSPEND_HOSTING
    assert command.idempotency_key == EVENT
    assert command.release_id is None
    assert command.requested_by_subject is None
    assert command.status is CommandStatus.ACCEPTED
    assert command.command_number == 1
    assert s.world.hosting.last_command_number == 1


@pytest.mark.asyncio
async def test_a_duplicate_payment_event_returns_the_same_command() -> None:
    s = Setup()
    first = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )
    again = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )

    assert again.replayed
    assert again.command == first.command
    assert s.world.hosting.last_command_number == 1
    assert len(s.world.commands) == 1
    # A replay asks no transaction: found by the unlocked lookup.
    assert s.log.count("acceptance accept") == 1


@pytest.mark.asyncio
async def test_a_duplicate_event_after_the_command_finished_still_returns_it() -> None:
    s = Setup()
    done = await s.run(CommandKind.SUSPEND_HOSTING)
    again = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )
    assert again.replayed
    assert again.command.id == done.id
    assert again.command.status is CommandStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_the_same_event_key_with_the_other_kind_is_a_conflict_and_not_retried() -> None:
    s = Setup()
    await s.event(CommandKind.SUSPEND_HOSTING)
    with pytest.raises(IdempotencyConflict) as raised:
        await s.intake.handle(AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.RESUME_HOSTING))
    assert not retry_hosting_event_later(raised.value)
    assert len(s.world.commands) == 1


@pytest.mark.asyncio
async def test_suspend_while_a_deploy_is_in_flight_is_refused_and_retried_later() -> None:
    s = Setup()
    s.world.releases[CANDIDATE] = make_release(CANDIDATE, ReleaseStatus.BUILT, 3)
    deploy = s.world.accept(CommandKind.DEPLOY, CANDIDATE)

    with pytest.raises(OperationInProgress) as raised:
        await s.event(CommandKind.SUSPEND_HOSTING)
    assert raised.value.command_id == deploy.id
    assert retry_hosting_event_later(raised.value)
    assert s.world.hosting.last_command_number == deploy.command_number
    assert len(s.world.commands) == 1


@pytest.mark.asyncio
async def test_a_route_that_is_not_stable_refuses_the_event_for_later() -> None:
    s = Setup()
    s.world.hosting = replace(s.world.hosting, routing_status=RoutingStatus.UNCERTAIN)
    with pytest.raises(RouteNotStable) as raised:
        await s.event(CommandKind.SUSPEND_HOSTING)
    assert retry_hosting_event_later(raised.value)


@pytest.mark.parametrize(
    ("error", "later"),
    [
        (Contention("lock timeout"), True),
        (Timeout("store"), True),
        (DependencyUnavailable("database"), True),
        (NotFound("hosting row"), False),
        (InvalidIdempotencyKey("key"), False),
        (RuntimeError("bug"), False),
    ],
)
def test_which_refusals_are_retried_later(error: Exception, later: bool) -> None:
    assert retry_hosting_event_later(error) is later


@pytest.mark.asyncio
async def test_intake_refuses_a_kind_that_is_not_hosting() -> None:
    s = Setup()
    with pytest.raises(InvalidCommandTarget):
        await s.intake.handle(AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.DEPLOY))
    assert s.log == []


@pytest.mark.parametrize("key", ["", " padded", "a\nb", "x" * 256])
@pytest.mark.asyncio
async def test_intake_refuses_a_malformed_event_key_before_any_store_call(key: str) -> None:
    s = Setup()
    with pytest.raises(InvalidIdempotencyKey):
        await s.intake.handle(AcceptHostingEventCommand(key, PROJECT, CommandKind.SUSPEND_HOSTING))
    assert s.log == []


@pytest.mark.asyncio
async def test_a_long_event_key_still_gives_a_valid_correlation_id() -> None:
    s = Setup()
    key = "k" * 255
    command = await s.event(CommandKind.SUSPEND_HOSTING, key)
    assert command.idempotency_key == key


def test_intake_refuses_a_deadline_that_is_not_positive() -> None:
    log: list[str] = []
    world = World(log)
    with pytest.raises(ValueError, match="command_deadline"):
        AcceptHostingEventHandler(
            store=world, clock=world.clock, ids=world.ids, command_deadline=timedelta(0)
        )


# --- suspend and resume -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_points_the_route_offline_and_keeps_the_live_release() -> None:
    """FR-26.1: suspend points the route offline and keeps the live release."""
    s = Setup()
    command = await s.run(CommandKind.SUSPEND_HOSTING)

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.world.hosting.live_release_id == LIVE
    assert s.world.release(LIVE).status is ReleaseStatus.LIVE
    assert s.cluster.route == Route(OFFLINE, command.command_number)
    assert s.cluster.hostnames == (HOSTNAME,)
    # Entitlement is rechecked, then the intent recorded, then the route moved.
    assert s.log.index("hosting entitlement") < s.log.index("store apply")
    assert s.log.index("store apply") < next(
        i for i, x in enumerate(s.log) if x.startswith("apply_route")
    )


@pytest.mark.asyncio
async def test_resume_points_the_route_back_at_the_live_release() -> None:
    """FR-27.1: resume points the route back at the live release."""
    s = Setup()
    await s.run(CommandKind.SUSPEND_HOSTING, "evt-ended")
    command = await s.run(CommandKind.RESUME_HOSTING, "evt-restored")

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route == Route(to_release(LIVE), command.command_number)


@pytest.mark.asyncio
async def test_a_suspend_is_skipped_when_entitlement_came_back() -> None:
    """FR-26.2: a suspend is skipped when entitlement came back."""
    s = Setup(later=True)
    command = await s.run(CommandKind.SUSPEND_HOSTING)

    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_ENTITLEMENT_RESTORED
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.applied_targets() == []
    # The record is asked about the events after the suspend's own (its idempotency key).
    assert s.entitlement.asked == [EVENT]


@pytest.mark.parametrize("later", [None, False], ids=["unknown", "ended-again"])
@pytest.mark.asyncio
async def test_a_suspend_runs_unless_a_later_restore_is_known(later: bool | None) -> None:
    """Decision 0014 section 17 item 2 (M2): a restarted worker, another replica or a record that
    never saw the suspend's event knows nothing later, and the shutdown runs."""
    s = Setup(later=later)
    command = await s.run(CommandKind.SUSPEND_HOSTING)

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.cluster.route == Route(OFFLINE, command.command_number)
    assert s.entitlement.asked == [EVENT]


@pytest.mark.parametrize("later", [None, True], ids=["unknown", "restored-again"])
@pytest.mark.asyncio
async def test_a_resume_runs_unless_a_later_end_is_known(later: bool | None) -> None:
    s = Setup()
    await s.run(CommandKind.SUSPEND_HOSTING, "evt-ended")
    s.entitlement.later = later
    command = await s.run(CommandKind.RESUME_HOSTING, "evt-restored")

    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.entitlement.asked == ["evt-ended", "evt-restored"]


@pytest.mark.asyncio
async def test_a_resume_is_skipped_when_entitlement_ended_again() -> None:
    """FR-27.3: a resume is skipped when entitlement ended again."""
    s = Setup()
    await s.run(CommandKind.SUSPEND_HOSTING, "evt-ended")
    s.entitlement.later = False
    command = await s.run(CommandKind.RESUME_HOSTING, "evt-restored")

    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_ENTITLEMENT_ENDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE


@pytest.mark.asyncio
async def test_a_suspend_of_an_offline_project_is_skipped_without_asking_payment() -> None:
    s = Setup()
    await s.run(CommandKind.SUSPEND_HOSTING, "evt-1")
    s.log.clear()
    command = await s.run(CommandKind.SUSPEND_HOSTING, "evt-2")

    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_HOSTING_ALREADY_OFFLINE
    assert "hosting entitlement" not in s.log


@pytest.mark.asyncio
async def test_a_resume_of_an_online_project_is_skipped() -> None:
    s = Setup(later=True)
    command = await s.run(CommandKind.RESUME_HOSTING)
    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_HOSTING_ALREADY_ONLINE


@pytest.mark.asyncio
async def test_a_suspend_whose_route_is_never_confirmed_leaves_the_route_uncertain() -> None:
    """FR-23.2: a suspend whose route is never confirmed leaves the route uncertain."""
    s = Setup()
    s.cluster.fail_apply[OFFLINE] = unavailable()
    command = await s.run(CommandKind.SUSPEND_HOSTING)

    assert command.status is CommandStatus.FAILED
    assert command.failure_code == CODE_ROUTE_CHANGE_UNCONFIRMED
    assert s.world.routing() is RoutingStatus.UNCERTAIN
    assert s.world.hosting_status() is HostingStatus.ONLINE


@pytest.mark.asyncio
async def test_entitlement_that_cannot_be_checked_is_transient_and_writes_nothing() -> None:
    s = Setup()
    s.entitlement.fault = ConnectionError("payment down, token xyz")
    command = await s.event(CommandKind.SUSPEND_HOSTING)
    with pytest.raises(DependencyUnavailable) as raised:
        await s.hosting.suspend(s.world.claim(command))
    assert "xyz" not in str(raised.value)
    assert s.world.writes == []


@pytest.mark.asyncio
async def test_a_suspend_resumed_after_a_lost_claim_finishes() -> None:
    s = Setup()
    command = await s.event(CommandKind.SUSPEND_HOSTING)
    first = s.world.claim(command)
    s.cluster.on_apply = lambda _: s.world.steal_claim(command.id)
    with pytest.raises(ClaimLost):
        await s.hosting.suspend(first)
    assert s.world.routing() is RoutingStatus.CHANGING

    s.cluster.on_apply = None
    s.log.clear()
    second: Claim = s.world.claim(command)
    await s.hosting.suspend(second)

    assert "hosting entitlement" not in s.log
    assert s.world.command(command.id).status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE


@pytest.mark.asyncio
async def test_the_intake_hands_the_event_to_the_hosting_command_store() -> None:
    """B3's HostingIntakeStore.accept_hosting_command: one call with the event key, a fresh
    command ID, the acceptance time, a correlation ID and the deadline."""
    s = Setup()
    accepted = await s.intake.handle(
        AcceptHostingEventCommand(EVENT, PROJECT, CommandKind.SUSPEND_HOSTING)
    )

    (call,) = s.world.hosting_calls
    assert call["project_id"] == PROJECT
    assert call["kind"] is CommandKind.SUSPEND_HOSTING
    assert call["event_key"] == EVENT
    assert call["command_id"] == accepted.command.id
    assert call["correlation_id"] == EVENT
    now = call["now"]
    deadline = call["deadline_at"]
    assert isinstance(now, datetime) and isinstance(deadline, datetime)
    assert deadline - now == timedelta(hours=1)


# --- offline reasons (decision 0021, D5 to D7) --------------------------------------------------


def key_of(reason: OfflineReason, n: int = 1) -> str:
    """A key that fixes ``reason`` by the key rule."""
    if reason is BILLING:
        return f"evt-{n}"
    if reason is USER:
        return owner_status_key(n)
    return DELETION_EVENT_KEY if n == 1 else retry_key(DELETION_EVENT_KEY, n - 1)


def offline_setup(*reasons: OfflineReason, live: bool = True) -> Setup:
    """A project whose hosting row holds ``reasons``; the route matches the status they imply."""
    s = Setup(live=live)
    s.world.set_reasons(*reasons)
    if live and reasons:
        s.cluster.route = Route(OFFLINE, 0)
    return s


def cluster_calls(log: list[str]) -> list[str]:
    return [x for x in log if x.startswith(("apply_route", "route_status", "probe"))]


@pytest.mark.asyncio
async def test_billing_resume_keeps_site_offline_while_user_suspended() -> None:
    """FR-27.2: billing resume keeps site offline while user suspended."""
    s = offline_setup(BILLING, USER)
    command = await s.run(CommandKind.RESUME_HOSTING, "evt-r")

    assert command.status is CommandStatus.SUCCEEDED
    assert command.failure_code is None
    assert s.world.reasons() == {USER}
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert cluster_calls(s.log) == []

    owner = await s.run(CommandKind.RESUME_HOSTING, owner_status_key(9))
    assert owner.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == set()
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.cluster.route == Route(to_release(LIVE), owner.command_number)
    assert s.entitlement.asked == ["evt-r"]  # the owner resume asks Payment nothing


@pytest.mark.asyncio
async def test_owner_reactivation_keeps_site_offline_while_billing_ended() -> None:
    s = offline_setup(BILLING, USER)
    owner = await s.run(CommandKind.RESUME_HOSTING, owner_status_key(9))

    assert owner.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == {BILLING}
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert cluster_calls(s.log) == []
    assert s.entitlement.asked == []

    billing = await s.run(CommandKind.RESUME_HOSTING, "evt-r")
    assert billing.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == set()
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.cluster.route == Route(to_release(LIVE), billing.command_number)


@pytest.mark.parametrize(
    ("held", "reason"),
    [
        ({BILLING}, BILLING),
        ({USER}, USER),
        ({DELETED}, DELETED),
        ({BILLING, USER}, USER),
        ({BILLING, DELETED}, BILLING),
    ],
)
@pytest.mark.asyncio
async def test_suspend_with_its_reason_present_is_skipped(
    held: set[OfflineReason], reason: OfflineReason
) -> None:
    """FR-31.1: suspend with its reason present is skipped."""
    s = offline_setup(*held)
    command = await s.run(CommandKind.SUSPEND_HOSTING, key_of(reason, 2))

    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_HOSTING_ALREADY_OFFLINE
    assert s.world.reasons() == held
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert "hosting entitlement" not in s.log
    assert cluster_calls(s.log) == []


@pytest.mark.parametrize(
    ("held", "reason"),
    [
        (set(), BILLING),
        (set(), USER),
        ({USER}, BILLING),
        ({BILLING}, USER),
        ({DELETED}, BILLING),
        ({DELETED, USER}, BILLING),
    ],
)
@pytest.mark.asyncio
async def test_resume_with_its_reason_absent_is_skipped(
    held: set[OfflineReason], reason: OfflineReason
) -> None:
    """D5: ``hosting_already_online`` also when another reason keeps the site offline; the row
    keeps its status and its reasons.

    FR-31.2."""
    s = offline_setup(*held)
    status = s.world.hosting_status()
    command = await s.run(CommandKind.RESUME_HOSTING, key_of(reason, 2))

    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_HOSTING_ALREADY_ONLINE
    assert s.world.reasons() == held
    assert s.world.hosting_status() is status
    assert status is (HostingStatus.OFFLINE if held else HostingStatus.ONLINE)
    assert "hosting entitlement" not in s.log
    assert cluster_calls(s.log) == []


_REASON_SETS: list[frozenset[OfflineReason]] = [
    frozenset(r for r, bit in zip((BILLING, USER, DELETED), (1, 2, 4), strict=True) if n & bit)
    for n in range(8)
]


@pytest.mark.asyncio
async def test_no_new_skip_code() -> None:
    """Every code a hosting command ends ``skipped`` with is one of the four existing codes (D5),
    over every reason, every set of held reasons, with and without a live release, and every
    answer of the entitlement record."""
    seen: set[str] = set()
    for kind in (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING):
        for reason in (BILLING, USER, DELETED):
            if kind is CommandKind.RESUME_HOSTING and reason is DELETED:
                continue  # the key rule refuses it
            for held in _REASON_SETS:
                for live in (True, False):
                    for later in (None, True, False):
                        s = offline_setup(*held, live=live)
                        s.entitlement.later = later
                        command = await s.run(kind, key_of(reason, 2))
                        assert command.status in (CommandStatus.SUCCEEDED, CommandStatus.SKIPPED)
                        if command.status is CommandStatus.SKIPPED:
                            assert command.failure_code is not None
                            seen.add(command.failure_code)
    assert seen == SKIP_CODES


@pytest.mark.asyncio
async def test_suspend_adds_a_reason_to_an_offline_site_without_route_call() -> None:
    s = offline_setup(BILLING)
    command = await s.run(CommandKind.SUSPEND_HOSTING, owner_status_key(4))

    assert command.status is CommandStatus.SUCCEEDED
    assert command.failure_code is None
    assert s.world.reasons() == {BILLING, USER}
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route == Route(OFFLINE, 0)
    assert cluster_calls(s.log) == []


@pytest.mark.asyncio
async def test_never_routed_row_changes_only_reasons() -> None:
    """D6: without a live release a suspend and a resume each only record their reason; the
    status follows the reasons and the cluster is never called."""
    s = Setup(live=False)
    suspend = await s.run(CommandKind.SUSPEND_HOSTING, "evt-ended")
    assert suspend.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == {BILLING}
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.world.routing() is RoutingStatus.STABLE

    owner = await s.run(CommandKind.SUSPEND_HOSTING, owner_status_key(1))
    assert owner.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == {BILLING, USER}

    for key in ("evt-restored", owner_status_key(2)):
        resume = await s.run(CommandKind.RESUME_HOSTING, key)
        assert resume.status is CommandStatus.SUCCEEDED
    assert s.world.reasons() == set()
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.world.routing() is RoutingStatus.STABLE
    assert s.cluster.route is None
    assert cluster_calls(s.log) == []


@pytest.mark.asyncio
async def test_user_suspended_is_never_rechecked_with_payment() -> None:
    s = Setup()
    suspend = await s.run(CommandKind.SUSPEND_HOSTING, owner_status_key(2))
    assert suspend.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.cluster.route == Route(OFFLINE, suspend.command_number)

    resume = await s.run(CommandKind.RESUME_HOSTING, owner_status_key(3))
    assert resume.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.ONLINE
    assert s.cluster.route == Route(to_release(LIVE), resume.command_number)
    assert s.entitlement.asked == []


@pytest.mark.asyncio
async def test_deletion_suspend_after_billing_adds_project_deleted() -> None:
    """FR-28.1: deletion suspend after billing adds project deleted."""
    s = Setup()
    billing = await s.run(CommandKind.SUSPEND_HOSTING, "evt-1")
    assert billing.status is CommandStatus.SUCCEEDED
    applied = s.cluster.applied_targets()
    s.entitlement.later = True  # a later restore never skips a deletion

    deletion = await s.run(CommandKind.SUSPEND_HOSTING, DELETION_EVENT_KEY)
    assert deletion.status is CommandStatus.SUCCEEDED
    assert deletion.failure_code is None
    assert s.world.reasons() == {BILLING, DELETED}
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.cluster.applied_targets() == applied  # no route call for the deletion
    assert s.entitlement.asked == ["evt-1"]


# --- project deletion and retry keys (wave 6, task W6-E) ----------------------------------------


@pytest.mark.asyncio
async def test_a_deletion_suspend_runs_although_a_later_event_restored_the_entitlement() -> None:
    s = Setup(later=True)
    command = await s.run(CommandKind.SUSPEND_HOSTING, DELETION_EVENT_KEY)
    assert command.status is CommandStatus.SUCCEEDED
    assert s.world.hosting_status() is HostingStatus.OFFLINE
    assert s.entitlement.asked == []  # a deletion is not rechecked against Payment's events


@pytest.mark.asyncio
async def test_a_deletion_suspend_of_a_deleted_site_is_skipped() -> None:
    s = Setup()
    await s.run(CommandKind.SUSPEND_HOSTING, DELETION_EVENT_KEY)
    command = await s.run(CommandKind.SUSPEND_HOSTING, retry_key(DELETION_EVENT_KEY, 1))
    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_HOSTING_ALREADY_OFFLINE


@pytest.mark.asyncio
async def test_a_retry_rechecks_against_the_events_after_its_original_event() -> None:
    s = Setup(later=True)
    command = await s.run(CommandKind.SUSPEND_HOSTING, f"{EVENT}.retry-2")
    assert command.status is CommandStatus.SKIPPED
    assert command.failure_code == CODE_ENTITLEMENT_RESTORED
    assert s.entitlement.asked == [EVENT]


@pytest.mark.parametrize(
    ("key", "root", "attempt"),
    [
        ("evt", "evt", 0),
        ("evt.retry-1", "evt", 1),
        ("evt.retry-12", "evt", 12),
        ("evt.retry-1.retry-2", "evt.retry-1", 2),
        ("evt.retry-0", "evt.retry-0", 0),
        ("evt.retry-", "evt.retry-", 0),
        (".retry-3", ".retry-3", 0),
    ],
)
def test_retry_keys_split_into_the_root_and_the_attempt(key: str, root: str, attempt: int) -> None:
    assert split_retry_key(key) == (root, attempt)
    assert root_event_key(key) == root
    if attempt:
        assert retry_key(root, attempt) == key


def test_a_retry_attempt_starts_at_one() -> None:
    with pytest.raises(ValueError, match="starts at 1"):
        retry_key(EVENT, 0)


class _History:
    def __init__(self, *intents: HostingIntent) -> None:
        self.intents = intents

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        return self.intents

    async def projects_to_retry(self, *, limit: int, budget: int) -> tuple[UUID, ...]:
        return (PROJECT,)


class _NoIntake:
    def __init__(self, fault: Exception | None = None) -> None:
        self.fault = fault
        self.asked: list[str] = []

    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand:
        self.asked.append(command.event_key)
        if self.fault is not None:
            raise self.fault
        raise AssertionError("no retry expected")


def _intent(
    status: CommandStatus,
    held: frozenset[OfflineReason] = frozenset(),
    key: str = EVENT,
    kind: CommandKind = CommandKind.SUSPEND_HOSTING,
    version: int = 0,
) -> HostingIntent:
    return HostingIntent(
        project_id=PROJECT,
        kind=kind,
        idempotency_key=key,
        status=status,
        hosting_reason=hosting_reason_of_key(kind, key),
        offline_reasons=held,
        owner_status_version=version,
    )


@pytest.mark.parametrize(
    ("intent", "met"),
    [
        (_intent(CommandStatus.FAILED), False),
        (_intent(CommandStatus.FAILED, frozenset({BILLING})), True),
        (_intent(CommandStatus.FAILED, frozenset({USER})), False),  # another reason is held
        (_intent(CommandStatus.FAILED, kind=CommandKind.RESUME_HOSTING), True),
        (_intent(CommandStatus.FAILED, frozenset({USER}), kind=CommandKind.RESUME_HOSTING), True),
        (
            _intent(CommandStatus.FAILED, frozenset({BILLING}), kind=CommandKind.RESUME_HOSTING),
            False,
        ),
    ],
)
def test_an_intent_is_met_by_its_own_reason(intent: HostingIntent, met: bool) -> None:
    assert intent.met() is met


@pytest.mark.parametrize(
    ("key", "version", "current"),
    [
        (EVENT, 7, True),
        (DELETION_EVENT_KEY, 7, True),
        (owner_status_key(3), 3, True),
        (f"{owner_status_key(3)}.retry-2", 3, True),
        (owner_status_key(3), 4, False),
        (owner_status_key(3), 0, False),
    ],
)
def test_only_the_current_owner_status_is_still_current(
    key: str, version: int, current: bool
) -> None:
    assert _intent(CommandStatus.FAILED, key=key, version=version).still_current() is current


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intents",
    [
        (),
        (_intent(CommandStatus.SUCCEEDED),),
        (_intent(CommandStatus.SKIPPED),),
        (_intent(CommandStatus.RUNNING),),
        (_intent(CommandStatus.FAILED, frozenset({BILLING})),),
        (_intent(CommandStatus.FAILED, kind=CommandKind.RESUME_HOSTING),),
        (_intent(CommandStatus.FAILED, key=owner_status_key(3), version=4),),
    ],
)
async def test_no_retry_without_a_failed_intent_that_is_not_met(
    intents: tuple[HostingIntent, ...],
) -> None:
    intake = _NoIntake()
    result = await HostingRetry(history=_History(*intents), intake=intake).retry(PROJECT)
    assert result.outcome is RetryOutcome.NOT_NEEDED
    assert intake.asked == []


class _Intake:
    """``HostingEventIntake`` that accepts every retry; ``asked`` the keys. The row holds
    ``owner_status_version``, as the owner-status intake left it."""

    def __init__(self, owner_status_version: int = 0) -> None:
        self.world = World([], live=True, candidate=False)
        self.world.hosting = replace(self.world.hosting, owner_status_version=owner_status_version)
        self.asked: list[str] = []

    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand:
        self.asked.append(command.event_key)
        return AcceptedCommand(
            command=self.world.accept(command.kind, key=command.event_key), replayed=False
        )


@pytest.mark.asyncio
async def test_the_oldest_failed_intent_is_retried_first() -> None:
    intake = _Intake()
    older = _intent(CommandStatus.FAILED, key="evt-1")
    newer = _intent(CommandStatus.FAILED, key=owner_status_key(3), version=3)
    result = await HostingRetry(history=_History(older, newer), intake=intake).retry(PROJECT)
    assert result.outcome is RetryOutcome.RETRIED
    assert intake.asked == ["evt-1.retry-1"]


@pytest.mark.asyncio
async def test_a_spent_reason_does_not_block_the_retry_of_another() -> None:
    intake = _Intake(owner_status_version=3)
    spent = _intent(CommandStatus.FAILED, key="evt-1.retry-3")
    owner = _intent(CommandStatus.FAILED, key=owner_status_key(3), version=3)
    retry = HostingRetry(history=_History(spent, owner), intake=intake)
    result = await retry.retry(PROJECT)
    assert result.outcome is RetryOutcome.RETRIED
    assert intake.asked == ["owner-status:3.retry-1"]
    alone = await HostingRetry(history=_History(spent), intake=intake).retry(PROJECT)
    assert (alone.outcome, alone.event_key) == (RetryOutcome.BUDGET_SPENT, "evt-1.retry-3")


@pytest.mark.asyncio
async def test_a_retry_whose_key_would_be_too_long_is_unretryable() -> None:
    intake = _NoIntake()
    intent = _intent(CommandStatus.FAILED, key="k" * 250)
    result = await HostingRetry(history=_History(intent), intake=intake).retry(PROJECT)
    assert result.outcome is RetryOutcome.UNRETRYABLE
    assert intake.asked == []


@pytest.mark.asyncio
async def test_a_permanent_refusal_of_the_retry_is_raised_and_a_busy_one_deferred() -> None:
    intent = _intent(CommandStatus.FAILED)
    busy = HostingRetry(history=_History(intent), intake=_NoIntake(OperationInProgress()))
    assert (await busy.retry(PROJECT)).outcome is RetryOutcome.DEFERRED
    broken = HostingRetry(history=_History(intent), intake=_NoIntake(NotFound("no hosting row")))
    with pytest.raises(NotFound):
        await broken.retry(PROJECT)


@pytest.mark.asyncio
async def test_an_outdated_owner_status_retry_is_skipped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    intent = _intent(CommandStatus.FAILED, key=owner_status_key(3), version=3)
    retry = HostingRetry(
        history=_History(intent),
        intake=_NoIntake(OwnerStatusOutdated("owner status 4 is newer")),
        logger=logging.getLogger("test.hosting_retry"),
    )
    with caplog.at_level(logging.INFO, logger="test.hosting_retry"):
        result = await retry.retry(PROJECT)
    assert result.outcome is RetryOutcome.NOT_NEEDED
    lines = [(r.levelname, r.getMessage()) for r in caplog.records]
    assert lines == [
        (
            "INFO",
            f"hosting retry skipped, owner status outdated (project_id={PROJECT} "
            "retry_key=owner-status:3.retry-1)",
        )
    ]


def test_a_retry_key_never_moves_the_owner_status_version() -> None:
    """The fake world follows the Pg store: only a root owner-status key records its version,
    and a refused decision leaves the row as it was."""
    world = World([], live=True, candidate=False)
    with pytest.raises(OwnerStatusOutdated):
        world.accept(CommandKind.SUSPEND_HOSTING, key=f"{owner_status_key(3)}.retry-1")
    assert world.hosting.owner_status_version == 0
    world.accept(CommandKind.SUSPEND_HOSTING, key=owner_status_key(3))
    assert world.hosting.owner_status_version == 3


def test_a_refused_owner_status_leaves_the_version_unchanged() -> None:
    world = World([], live=True, candidate=False)
    world.hosting = replace(world.hosting, routing_status=RoutingStatus.CHANGING)
    with pytest.raises(RouteNotStable):
        world.accept(CommandKind.SUSPEND_HOSTING, key=owner_status_key(3))
    assert world.hosting.owner_status_version == 0


def test_the_retry_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        HostingRetry(history=_History(), intake=_NoIntake(), budget=0)
