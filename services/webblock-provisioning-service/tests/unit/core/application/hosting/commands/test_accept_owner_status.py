"""The owner-status intake (decision 0021 D9; Notion "5 · Provisioning", "User suspension" and
"Out-of-order events"): ``project.owner_status_changed.v1`` becomes a ``suspend_hosting`` or
``resume_hosting`` command with reason ``user_suspended`` and key ``owner-status:<version>``; a
project without a hosting row gets one and no command; an older version changes nothing.

The fake store follows the store's steps and applies the real acceptance decision
(``new_acceptance_decider``), so refusals come from the real rules."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.unit.core.application.routing.background.test_routing_support import FakeClock, FakeIds

from provisioning.core.application import (
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    AcceptOwnerStatusCommand,
    AcceptOwnerStatusHandler,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    OperationInProgress,
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
    new_acceptance_decider,
)
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    retry_hosting_event_later,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    InvalidCommandNumber,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidSubdomain,
    OfflineReason,
    ProjectHosting,
    RoutingStatus,
    new_owner_status_hosting,
    new_project_hosting,
    owner_status_key,
)

pytestmark = pytest.mark.asyncio

PROJECT = UUID("0190a1b2-0000-7000-8000-00000000a001")


class FakeOwnerStatusStore:
    """``OwnerStatusStore`` over one project's rows, in the store's order: the row insert when
    there is no row, the version check (a replay when the key holds this event's command, else
    stale; decision 0023 G17), then the version and the command together (a refusal of the
    decision stores nothing)."""

    def __init__(self, hosting: ProjectHosting | None) -> None:
        self.hosting = hosting
        self.commands: dict[str, Command] = {}
        self.calls = 0
        self.subdomains: list[str] = []

    async def accept_owner_status(
        self,
        *,
        project_id: UUID,
        status: OwnerStatus,
        version: int,
        subdomain: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> OwnerStatusResult:
        self.calls += 1
        self.subdomains.append(subdomain)
        suspended = status is OwnerStatus.SUSPENDED
        kind = CommandKind.SUSPEND_HOSTING if suspended else CommandKind.RESUME_HOSTING
        request = CommandRequest(
            project_id=project_id,
            kind=kind,
            release_id=None,
            idempotency_key=owner_status_key(version),
        )
        if self.hosting is None:
            self.hosting = new_owner_status_hosting(
                project_id, now, suspended=suspended, version=version, subdomain=subdomain
            )
            return OwnerStatusResult(OwnerStatusOutcome.ROW_INSERTED, None)
        if version <= self.hosting.owner_status_version:
            stored = self.commands.get(request.idempotency_key)
            if stored is None or stored.kind is not kind:
                return OwnerStatusResult(OwnerStatusOutcome.STALE, None)
            stored.check_replay(request)
            return OwnerStatusResult(OwnerStatusOutcome.REPLAYED, AcceptedCommand(stored, True))
        updated = replace(self.hosting, owner_status_version=version)
        params = AcceptCommandParams(
            request=request,
            command_id=command_id,
            requested_by_subject="",
            now=now,
            correlation_id=correlation_id,
            deadline_at=deadline_at,
        )
        in_flight = next((c for c in self.commands.values() if c.status.unfinished()), None)
        decision = new_acceptance_decider(params)(
            AcceptanceFacts(hosting=updated, in_flight=in_flight)
        )
        command = decision.command
        self.hosting = replace(updated, last_command_number=command.command_number)
        self.commands[request.idempotency_key] = command
        return OwnerStatusResult(OwnerStatusOutcome.ACCEPTED, AcceptedCommand(command, False))


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        self.events.append(event)


def intake_of(
    store: FakeOwnerStatusStore, audit: RecordingAudit | None = None
) -> AcceptOwnerStatusHandler:
    return AcceptOwnerStatusHandler(
        store=store,
        clock=FakeClock(),
        ids=FakeIds(),
        command_deadline=timedelta(hours=1),
        audit=audit or RecordingAudit(),
        logger=logging.getLogger("test.owner_status"),
    )


def routed_row() -> ProjectHosting:
    return new_project_hosting(PROJECT, datetime(2026, 10, 24, tzinfo=UTC), subdomain="shop")


async def test_suspended_owner_creates_a_user_suspended_suspend() -> None:
    """FR-29.1: suspended owner creates a user suspended suspend."""
    audit = RecordingAudit()
    store = FakeOwnerStatusStore(routed_row())
    result = await intake_of(store, audit).handle(
        AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 3, "shop")
    )
    assert result.outcome is OwnerStatusOutcome.ACCEPTED
    assert result.accepted is not None and not result.accepted.replayed
    command = result.accepted.command
    assert command.kind is CommandKind.SUSPEND_HOSTING
    assert command.idempotency_key == "owner-status:3"
    assert command.hosting_reason is OfflineReason.USER_SUSPENDED
    assert command.requested_by_subject is None
    assert store.hosting is not None and store.hosting.owner_status_version == 3
    (event,) = audit.events
    assert event.actor_kind is AuditActorKind.EVENT
    assert event.result is AuditResult.ACCEPTED
    assert event.command_id == command.id
    assert event.correlation_id == "owner-status:3"


async def test_active_owner_creates_a_resume() -> None:
    store = FakeOwnerStatusStore(routed_row())
    result = await intake_of(store).handle(
        AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 4, "shop")
    )
    assert result.outcome is OwnerStatusOutcome.ACCEPTED
    assert result.accepted is not None
    assert result.accepted.command.kind is CommandKind.RESUME_HOSTING
    assert result.accepted.command.hosting_reason is OfflineReason.USER_SUSPENDED


async def test_a_repeated_event_is_a_replay_without_audit() -> None:
    audit = RecordingAudit()
    store = FakeOwnerStatusStore(routed_row())
    intake = intake_of(store, audit)
    first = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 3, "shop"))
    again = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 3, "shop"))
    assert again.outcome is OwnerStatusOutcome.REPLAYED
    assert first.accepted is not None and again.accepted is not None
    assert again.accepted.replayed and again.accepted.command.id == first.accepted.command.id
    assert len(audit.events) == 1


@pytest.mark.parametrize(
    ("status", "hosting", "reasons"),
    [
        (OwnerStatus.SUSPENDED, HostingStatus.OFFLINE, {OfflineReason.USER_SUSPENDED}),
        (OwnerStatus.ACTIVE, HostingStatus.ONLINE, set()),
    ],
)
async def test_no_hosting_row_inserts_one_and_no_command(
    status: OwnerStatus, hosting: HostingStatus, reasons: set[OfflineReason]
) -> None:
    """FR-29.3: no hosting row inserts one and no command."""
    audit = RecordingAudit()
    store = FakeOwnerStatusStore(None)
    result = await intake_of(store, audit).handle(
        AcceptOwnerStatusCommand(PROJECT, status, 2, "shop")
    )
    assert result == OwnerStatusResult(OwnerStatusOutcome.ROW_INSERTED, None)
    assert store.commands == {}
    assert store.hosting is not None
    row = store.hosting
    assert (row.hosting_status, row.offline_reasons, row.owner_status_version) == (
        hosting,
        frozenset(reasons),
        2,
    )
    assert (row.last_command_number, row.routing_status, row.live_release_id) == (
        0,
        RoutingStatus.STABLE,
        None,
    )
    assert audit.events == []


async def test_older_owner_status_changes_nothing() -> None:
    """Version 3 (active) arrives before version 2 (suspended): version 2 changes nothing, so the
    site stays online; a version 2 after a row inserted by version 3 is ignored too.

    FR-29.2."""
    store = FakeOwnerStatusStore(routed_row())
    intake = intake_of(store)
    newer = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 3, "shop"))
    assert newer.outcome is OwnerStatusOutcome.ACCEPTED
    before = store.hosting
    older = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 2, "shop"))
    assert older == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    assert store.hosting == before
    assert list(store.commands) == ["owner-status:3"]

    inserted = FakeOwnerStatusStore(None)
    intake = intake_of(inserted)
    await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 3, "shop"))
    row = inserted.hosting
    stale = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 2, "shop"))
    assert stale.outcome is OwnerStatusOutcome.STALE
    assert inserted.hosting == row and inserted.commands == {}


async def test_same_version_other_status_is_stale() -> None:
    """Decision 0023 G17: the version is checked first, so "active" at the version of a stored
    suspend is stale (not ``IdempotencyConflict``), writes nothing and records no audit event.

    FR-29.2."""
    audit = RecordingAudit()
    store = FakeOwnerStatusStore(routed_row())
    intake = intake_of(store, audit)
    first = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 3, "shop"))
    assert first.outcome is OwnerStatusOutcome.ACCEPTED
    before = store.hosting
    other = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 3, "shop"))
    assert other == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    assert store.hosting == before
    assert list(store.commands) == ["owner-status:3"]
    assert len(audit.events) == 1


async def test_older_version_is_stale() -> None:
    """An older event whose key holds a command of the other kind is stale too, whatever its
    status: version 2 (suspend) and 3 (resume) were applied, then version 2 "active" arrives."""
    store = FakeOwnerStatusStore(routed_row())
    intake = intake_of(store)
    suspend = await intake.handle(
        AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 2, "shop")
    )
    assert suspend.accepted is not None
    store.commands["owner-status:2"] = replace(
        suspend.accepted.command, status=CommandStatus.SUCCEEDED
    )
    resume = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 3, "shop"))
    assert resume.outcome is OwnerStatusOutcome.ACCEPTED
    before = store.hosting
    for status, version in ((OwnerStatus.ACTIVE, 2), (OwnerStatus.SUSPENDED, 1)):
        older = await intake.handle(AcceptOwnerStatusCommand(PROJECT, status, version, "shop"))
        assert older == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    assert store.hosting == before
    assert list(store.commands) == ["owner-status:2", "owner-status:3"]


async def test_busy_project_is_retried_later_and_stores_nothing() -> None:
    store = FakeOwnerStatusStore(routed_row())
    intake = intake_of(store)
    first = await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 1, "shop"))
    assert first.accepted is not None
    assert first.accepted.command.status is CommandStatus.ACCEPTED  # still in flight
    before = store.hosting
    with pytest.raises(OperationInProgress) as raised:
        await intake.handle(AcceptOwnerStatusCommand(PROJECT, OwnerStatus.ACTIVE, 2, "shop"))
    assert retry_hosting_event_later(raised.value)
    assert store.hosting == before  # the version stays 1
    assert list(store.commands) == ["owner-status:1"]


@pytest.mark.parametrize("version", [0, -1, 2**53])
async def test_invalid_version_is_refused_before_the_store(version: int) -> None:
    store = FakeOwnerStatusStore(routed_row())
    with pytest.raises(InvalidIdempotencyKey):
        await intake_of(store).handle(
            AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, version, "shop")
        )
    assert store.calls == 0


async def test_nil_project_is_refused_before_the_store() -> None:
    store = FakeOwnerStatusStore(routed_row())
    with pytest.raises(InvalidId):
        await intake_of(store).handle(
            AcceptOwnerStatusCommand(UUID(int=0), OwnerStatus.SUSPENDED, 1, "shop")
        )
    assert store.calls == 0


async def test_largest_version_is_accepted() -> None:
    store = FakeOwnerStatusStore(None)
    result = await intake_of(store).handle(
        AcceptOwnerStatusCommand(uuid4(), OwnerStatus.SUSPENDED, 2**53 - 1, "shop")
    )
    assert result.outcome is OwnerStatusOutcome.ROW_INSERTED


async def test_owner_status_values() -> None:
    assert [s.value for s in OwnerStatus] == ["active", "suspended"]
    assert [o.value for o in OwnerStatusOutcome] == [
        "accepted",
        "replayed",
        "row_inserted",
        "stale",
    ]


async def test_new_owner_status_hosting_follows_the_insert_shape() -> None:
    now = FakeClock().now()
    row = new_owner_status_hosting(PROJECT, now, suspended=True, version=7, subdomain="shop")
    row.validate()
    assert row.subdomain == "shop"
    assert row.offline_reasons == {OfflineReason.USER_SUSPENDED}
    assert row.hosting_status is HostingStatus.OFFLINE
    active = new_owner_status_hosting(PROJECT, now, suspended=False, version=7, subdomain="a")
    active.validate()
    assert active.offline_reasons == frozenset() and active.hosting_status is HostingStatus.ONLINE
    with pytest.raises(InvalidId):
        new_owner_status_hosting(UUID(int=0), now, suspended=True, version=1, subdomain="a")
    for version in (0, 2**53):
        with pytest.raises(InvalidCommandNumber):
            new_owner_status_hosting(PROJECT, now, suspended=True, version=version, subdomain="a")
    with pytest.raises(InvalidSubdomain):
        new_owner_status_hosting(PROJECT, now, suspended=True, version=1, subdomain="-a")


async def test_the_inserted_row_holds_the_event_subdomain() -> None:
    """Notion: project.owner_status_changed.v1 also carries the sub-domain, so a row that event
    inserts fills it too."""
    store = FakeOwnerStatusStore(None)
    await intake_of(store).handle(
        AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 2, "my-shop")
    )
    assert store.hosting is not None and store.hosting.subdomain == "my-shop"
    assert store.subdomains == ["my-shop"]


@pytest.mark.parametrize("subdomain", ["", "-a", "A", "a.b", "a" * 64])
async def test_invalid_subdomain_is_refused_before_the_store(subdomain: str) -> None:
    store = FakeOwnerStatusStore(None)
    with pytest.raises(InvalidSubdomain):
        await intake_of(store).handle(
            AcceptOwnerStatusCommand(PROJECT, OwnerStatus.SUSPENDED, 1, subdomain)
        )
    assert store.calls == 0
