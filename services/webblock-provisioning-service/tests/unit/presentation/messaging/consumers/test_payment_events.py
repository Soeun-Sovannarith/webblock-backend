"""Payment's prototype entitlement events (decision 0014 sections 3 and 10.5): the consumer hands
each event to the hosting intake (task F2's ``AcceptHostingEventHandler``) and settles the delivery
by what it answers (lead update to E2, 8 October 2026):

- accepted or replayed: ack;
- a permanent refusal: ack, logged with the event key (never dropped silently);
- a transient refusal (``retry_hosting_event_later``): no ack; held for a bounded backoff, then
  requeued; after ``max_retries`` redeliveries of one event key it is parked (dead-lettered, kept in
  the dead-letter queue) and logged with the event key;
- an invalid payload: dead-lettered.

Wave 6 (task W6-E): a replayed event whose command failed asks the hosting retry for a new command
(QA-03), and Project's "project deleted" event becomes a ``suspend_hosting`` command with the fixed
key ``project-deleted`` through the same settling. The periodic retry sweep's tests are in
tests/unit/core/application/hosting/background/test_retry_sweeper.py.
"""

import asyncio
import dataclasses
from datetime import UTC, datetime
from uuid import UUID

import pytest
from tests.unit._buses import intake_bus
from tests.unit._loop_fakes import (
    BODY_MARKER,
    MS,
    PROJECT,
    FakeDelivery,
    FakeStream,
    eventually,
    logger_with_handler,
)

from provisioning.core.application import (
    AcceptedCommand,
    Contention,
    DependencyUnavailable,
    NotFound,
    OperationInProgress,
    StoreUnreachable,
    Timeout,
)
from provisioning.core.application.hosting.background.retry import RetryOutcome, RetryResult
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    CommandStatus,
    IdempotencyConflict,
    InvalidCommandTarget,
    RouteNotStable,
    new_command,
)
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import (
    PaymentEventConsumer,
    ProjectDeletionConsumer,
)

pytestmark = pytest.mark.asyncio


@dataclasses.dataclass(frozen=True)
class Event:
    event_key: str
    project_id: UUID
    kind: CommandKind


ENDED = Event("pay-1", PROJECT, CommandKind.SUSPEND_HOSTING)


class FakeDecoder:
    def __init__(self, result: Event | Exception = ENDED) -> None:
        self.result = result
        self.message_ids: list[str | None] = []

    def decode_hosting_event(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> Event:
        self.message_ids.append(message_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeHostingIntake:
    """Accepts each key once; a repeat of an accepted key is a replay."""

    def __init__(self) -> None:
        self.accepted: dict[str, AcceptedCommand] = {}
        self.calls: list[tuple[str, UUID, CommandKind]] = []
        self.errors: list[Exception] = []

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        self.calls.append((event_key, project_id, kind))
        if self.errors:
            raise self.errors.pop(0)
        stored = self.accepted.get(event_key)
        if stored is not None:
            return dataclasses.replace(stored, replayed=True)
        command = new_command(
            command_id=UUID(int=len(self.accepted) + 1),
            request=CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            ),
            requested_by_subject=None,
            command_number=len(self.accepted) + 1,
            now=datetime(2026, 10, 8, tzinfo=UTC),
        )
        self.accepted[event_key] = AcceptedCommand(command=command, replayed=False)
        return self.accepted[event_key]

    def finish(self, event_key: str, status: CommandStatus) -> None:
        stored = self.accepted[event_key]
        command = dataclasses.replace(stored.command, status=status)
        self.accepted[event_key] = dataclasses.replace(stored, command=command)


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("no alert expected")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 9, 12, tzinfo=UTC)


AUDITS: list[Audit] = []


def audit() -> Audit:
    """A fresh audit record, also kept as the last one built (``last_audit``)."""
    AUDITS.append(Audit())
    return AUDITS[-1]


def last_audit() -> Audit:
    return AUDITS[-1]


class FakeRetries:
    """``HostingRetry.retry``: answers ``outcome`` (or raises ``fault``) and records the asks."""

    def __init__(self, outcome: RetryOutcome = RetryOutcome.RETRIED) -> None:
        self.outcome = outcome
        self.fault: Exception | None = None
        self.asked: list[UUID] = []

    async def retry(self, project_id: UUID) -> RetryResult:
        self.asked.append(project_id)
        if self.fault is not None:
            raise self.fault
        accepted = None
        if self.outcome is RetryOutcome.RETRIED:
            accepted = AcceptedCommand(
                command=new_command(
                    command_id=UUID(int=99),
                    request=CommandRequest(
                        project_id=project_id,
                        kind=CommandKind.SUSPEND_HOSTING,
                        release_id=None,
                        idempotency_key="pay-1.retry-1",
                    ),
                    requested_by_subject=None,
                    command_number=99,
                    now=datetime(2026, 10, 8, tzinfo=UTC),
                ),
                replayed=False,
            )
        return RetryResult(project_id, self.outcome, "pay-1.retry-1", accepted)


def build(
    decoder: FakeDecoder | None = None,
    *,
    max_retries: int = 3,
    retries: FakeRetries | None = None,
) -> tuple[PaymentEventConsumer, FakeHostingIntake, list[str]]:
    intake = FakeHostingIntake()
    logger, handler = logger_with_handler("payment")
    consumer = PaymentEventConsumer(
        EventConsumerConfig(
            concurrency=2, store_hold=Backoff(base=MS, max=2 * MS), store_failure_limit=2
        ),
        decoder=decoder or FakeDecoder(),
        commands=intake_bus(hosting=intake),
        retries=retries or FakeRetries(),
        audit=audit(),
        clock=Clock(),
        logger=logger,
        retry_backoff=Backoff(base=MS, max=4 * MS),
        max_retries=max_retries,
    )
    return consumer, intake, handler.lines


async def test_an_event_becomes_a_hosting_command_and_is_acknowledged() -> None:
    consumer, intake, _ = build()
    d = FakeDelivery()
    assert await consumer.handle(d) is False
    assert intake.calls == [("pay-1", PROJECT, CommandKind.SUSPEND_HOSTING)]
    assert d.actions == ["ack"]


async def test_a_duplicate_event_is_processed_once() -> None:
    consumer, intake, _ = build()
    first, second = FakeDelivery(), FakeDelivery(redelivered=True)
    await consumer.handle(first)
    await consumer.handle(second)
    assert first.actions == ["ack"] and second.actions == ["ack"]
    assert list(intake.accepted) == ["pay-1"]
    assert intake.accepted["pay-1"].command.idempotency_key == "pay-1"


@pytest.mark.parametrize(
    "error",
    [
        Contention("x"),
        Timeout("x"),
        DependencyUnavailable("x"),
        OperationInProgress(),
        RouteNotStable("x"),
    ],
)
async def test_a_transient_refusal_is_requeued_without_an_ack(error: Exception) -> None:
    consumer, intake, _ = build()
    intake.errors = [error]
    d = FakeDelivery()
    assert await consumer.handle(d) is False
    assert d.actions == ["requeue"]


async def test_a_busy_project_is_retried_until_the_command_is_accepted() -> None:
    consumer, intake, _ = build()
    intake.errors = [OperationInProgress(), OperationInProgress()]
    actions = []
    for _ in range(3):
        d = FakeDelivery()
        await consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue", "requeue", "ack"]
    assert list(intake.accepted) == ["pay-1"]


async def test_redeliveries_are_bounded_then_the_event_is_parked_and_logged() -> None:
    consumer, intake, lines = build(max_retries=2)
    intake.errors = [OperationInProgress()] * 3
    actions = []
    for _ in range(3):
        d = FakeDelivery()
        await consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue", "requeue", "dead_letter"]
    parked = [line for line in lines if line.startswith("ERROR")]
    assert len(parked) == 1 and "event_key=pay-1" in parked[0]
    # The count is per event key and starts again after parking.
    intake.errors = [OperationInProgress()]
    d = FakeDelivery()
    await consumer.handle(d)
    assert d.actions == ["requeue"]


async def test_each_retry_is_held_before_the_requeue() -> None:
    consumer, intake, _ = build(max_retries=5)
    intake.errors = [OperationInProgress()] * 3
    loop = asyncio.get_running_loop()
    for _ in range(3):
        start = loop.time()
        await consumer.handle(FakeDelivery())
        assert loop.time() - start >= 0.0005  # more than half of the first delay of 1 ms


@pytest.mark.parametrize(
    "error", [NotFound("x"), IdempotencyConflict("x"), InvalidCommandTarget("x")]
)
async def test_a_permanent_refusal_is_acknowledged_and_logged_with_the_event_key(
    error: Exception,
) -> None:
    consumer, intake, lines = build()
    intake.errors = [error]
    d = FakeDelivery()
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert any("event_key=pay-1" in line and type(error).__name__ in line for line in lines)


async def test_payment_event_for_a_never_deployed_project_is_acked_and_logged() -> None:
    """Notion "5 · Provisioning", commands, Good to know (24 October 2026): a Payment event for a
    project that was never deployed is acknowledged and logged; no command, no row, no table.

    FR-30.1."""
    consumer, intake, lines = build()
    intake.errors = [NotFound("no hosting row")]
    d = FakeDelivery()
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert intake.accepted == {}
    warnings = [line for line in lines if line.startswith("WARNING ")]
    assert len(warnings) == 1
    assert "event_key=pay-1" in warnings[0]
    assert f"project_id={PROJECT}" in warnings[0]
    assert "NotFound" in warnings[0]


async def test_an_invalid_payload_is_dead_lettered_without_calling_the_intake() -> None:
    consumer, intake, lines = build(
        FakeDecoder(ValueError("message rejected: reason=schema_invalid"))
    )
    d = FakeDelivery(BODY_MARKER.encode())
    await consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert intake.calls == []
    assert BODY_MARKER not in "\n".join(lines)


async def test_the_amqp_message_id_reaches_the_decoder_and_a_refusal_logs_it() -> None:
    decoder = FakeDecoder(ValueError("message rejected: reason=envelope_mismatch"))
    consumer, _, lines = build(decoder)
    message_id = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"
    await consumer.handle(FakeDelivery(message_id=message_id))
    await consumer.handle(FakeDelivery())
    assert decoder.message_ids == [message_id, None]
    assert f"message_id={message_id}" in "\n".join(lines)
    assert "message_id=none" in "\n".join(lines)


async def test_an_unexpected_error_is_requeued_and_restarts_after_the_limit() -> None:
    """A failure while the database answered (here a plain error) counts."""
    consumer, intake, _ = build(max_retries=10)
    intake.errors = [ConnectionError("db"), ConnectionError("db")]
    assert [await consumer.handle(FakeDelivery()) for _ in range(2)] == [False, True]


async def test_an_event_whose_intake_keeps_failing_is_dead_lettered_at_the_fifth_failure() -> None:
    """FR-32.1: an event whose intake keeps failing is dead lettered at the fifth failure."""
    consumer, intake, _ = build(max_retries=10)
    intake.errors = [ConnectionError("db")] * 5
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        await consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]


async def test_the_retry_settings_are_required() -> None:
    for retry_backoff, max_retries in (
        (Backoff(base=MS, max=MS), 0),
        (Backoff(base=2 * MS, max=MS), 3),
    ):
        with pytest.raises(InvalidConfig):
            PaymentEventConsumer(
                EventConsumerConfig(
                    concurrency=1, store_hold=Backoff(base=MS, max=MS), store_failure_limit=1
                ),
                decoder=FakeDecoder(),
                commands=intake_bus(hosting=FakeHostingIntake()),
                retries=FakeRetries(),
                audit=audit(),
                clock=Clock(),
                logger=logger_with_handler("payment-config")[0],
                retry_backoff=retry_backoff,
                max_retries=max_retries,
            )


async def test_run_serves_the_stream() -> None:
    consumer, _, _ = build()
    stream = FakeStream()
    d = FakeDelivery()
    stream.put(d)
    task = asyncio.create_task(consumer.run(stream))
    await eventually(lambda: bool(d.actions))
    stream.close()
    async with asyncio.timeout(1):
        await task
    assert d.actions == ["ack"]


# --- QA-03: a repeated event whose command failed asks for a retry ----------------------------


async def test_a_repeat_of_a_failed_command_asks_for_a_retry_and_is_acknowledged() -> None:
    retries = FakeRetries()
    consumer, intake, lines = build(retries=retries)
    await consumer.handle(FakeDelivery())
    intake.finish("pay-1", CommandStatus.FAILED)
    d = FakeDelivery(redelivered=True)
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert retries.asked == [PROJECT]
    assert any("hosting retry retried" in line and "pay-1.retry-1" in line for line in lines)


@pytest.mark.parametrize("status", [CommandStatus.SUCCEEDED, CommandStatus.SKIPPED])
async def test_a_repeat_of_a_finished_command_asks_for_no_retry(status: CommandStatus) -> None:
    retries = FakeRetries()
    consumer, intake, _ = build(retries=retries)
    await consumer.handle(FakeDelivery())
    intake.finish("pay-1", status)
    await consumer.handle(FakeDelivery())
    assert retries.asked == []


async def test_a_first_event_asks_for_no_retry() -> None:
    retries = FakeRetries()
    consumer, _, _ = build(retries=retries)
    await consumer.handle(FakeDelivery())
    assert retries.asked == []


@pytest.mark.parametrize("outcome", [RetryOutcome.BUDGET_SPENT, RetryOutcome.UNRETRYABLE])
async def test_a_retry_that_cannot_happen_is_logged_as_an_error(outcome: RetryOutcome) -> None:
    consumer, intake, lines = build(retries=FakeRetries(outcome))
    await consumer.handle(FakeDelivery())
    intake.finish("pay-1", CommandStatus.FAILED)
    d = FakeDelivery()
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert any(line.startswith("ERROR") and outcome.value in line for line in lines)


async def test_a_retry_that_raises_is_logged_and_the_event_still_acknowledged() -> None:
    retries = FakeRetries()
    retries.fault = ConnectionError("db")
    consumer, intake, lines = build(retries=retries)
    await consumer.handle(FakeDelivery())
    intake.finish("pay-1", CommandStatus.FAILED)
    d = FakeDelivery()
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert any("ConnectionError" in line and "sweep" in line for line in lines)


# --- Project's "project deleted" event --------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Deleted:
    project_id: UUID


DELETED = Deleted(PROJECT)


class FakeDeletionDecoder:
    def __init__(self, result: Deleted | Exception = DELETED) -> None:
        self.result = result
        self.message_ids: list[str | None] = []

    def decode_project_deleted(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> Deleted:
        self.message_ids.append(message_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def build_deletion(
    decoder: FakeDeletionDecoder | None = None, retries: FakeRetries | None = None
) -> tuple[ProjectDeletionConsumer, FakeHostingIntake, list[str]]:
    intake = FakeHostingIntake()
    logger, handler = logger_with_handler("deletion")
    consumer = ProjectDeletionConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=MS, max=2 * MS), store_failure_limit=2
        ),
        decoder=decoder or FakeDeletionDecoder(),
        commands=intake_bus(hosting=intake),
        retries=retries or FakeRetries(),
        audit=audit(),
        clock=Clock(),
        logger=logger,
        retry_backoff=Backoff(base=MS, max=4 * MS),
        max_retries=2,
    )
    return consumer, intake, handler.lines


async def test_a_deletion_becomes_a_suspend_with_the_fixed_key() -> None:
    """FR-28: a deletion becomes a suspend with the fixed key."""
    consumer, intake, lines = build_deletion()
    first, again = FakeDelivery(), FakeDelivery(redelivered=True)
    await consumer.handle(first)
    await consumer.handle(again)
    assert first.actions == ["ack"] and again.actions == ["ack"]
    assert intake.calls == [("project-deleted", PROJECT, CommandKind.SUSPEND_HOSTING)] * 2
    assert list(intake.accepted) == ["project-deleted"]
    assert any(f"project_id={PROJECT}" in line and "accepted" in line for line in lines)


async def test_a_deletion_passes_the_amqp_message_id_to_its_decoder() -> None:
    decoder = FakeDeletionDecoder()
    consumer, _, _ = build_deletion(decoder)
    await consumer.handle(FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"))
    assert decoder.message_ids == ["0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"]


async def test_a_repeated_deletion_whose_suspend_failed_asks_for_a_retry() -> None:
    retries = FakeRetries()
    consumer, intake, _ = build_deletion(retries=retries)
    await consumer.handle(FakeDelivery())
    intake.finish("project-deleted", CommandStatus.FAILED)
    await consumer.handle(FakeDelivery())
    assert retries.asked == [PROJECT]


async def test_a_busy_project_holds_the_deletion_then_parks_it() -> None:
    consumer, intake, _ = build_deletion()
    intake.errors = [RouteNotStable("x")] * 3
    actions = []
    for _ in range(3):
        d = FakeDelivery()
        await consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue", "requeue", "dead_letter"]


async def test_an_invalid_deletion_is_dead_lettered() -> None:
    consumer, intake, _ = build_deletion(FakeDeletionDecoder(ValueError("rejected")))
    d = FakeDelivery(BODY_MARKER.encode())
    await consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert intake.calls == []


# --- audit ------------------------------------------------------------------------------------


async def test_an_accepted_event_command_is_audited_once_and_a_replay_not() -> None:
    consumer, intake, _ = build()
    await consumer.handle(FakeDelivery())
    await consumer.handle(FakeDelivery(redelivered=True))
    (event,) = last_audit().events
    assert event.actor_kind is AuditActorKind.EVENT and event.actor == "payment"
    assert event.action is AuditAction.SUSPEND_HOSTING
    assert event.result is AuditResult.ACCEPTED
    assert event.command_id == intake.accepted["pay-1"].command.id
    assert event.correlation_id == "pay-1"


async def test_a_retry_command_is_audited_with_the_retry_as_the_actor() -> None:
    consumer, intake, _ = build(retries=FakeRetries())
    await consumer.handle(FakeDelivery())
    intake.finish("pay-1", CommandStatus.FAILED)
    await consumer.handle(FakeDelivery())
    first, retried = last_audit().events
    assert first.actor == "payment"
    assert (retried.actor_kind, retried.actor) == (AuditActorKind.SYSTEM, "hosting-retry")
    assert retried.correlation_id == "pay-1.retry-1"
    assert retried.command_id == UUID(int=99)


async def test_a_deletion_is_audited_with_project_as_the_actor() -> None:
    consumer, _, _ = build_deletion()
    await consumer.handle(FakeDelivery())
    (event,) = last_audit().events
    assert (event.actor_kind, event.actor) == (AuditActorKind.EVENT, "project")
    assert event.action is AuditAction.SUSPEND_HOSTING


@pytest.mark.parametrize(
    "error",
    [StoreUnreachable("db down"), Timeout("pool")],
    ids=["unreachable", "timeout"],
)
async def test_a_database_outage_counts_toward_the_bound(error: Exception) -> None:
    """Decision 0023 G15 (owner): an unanswered database counts towards the event's 5 tries,
    then it goes to its ``.dlq``. It does not use the hosting retry budget (``max_retries``) and
    never restarts the stream (Phase B's fix).

    FR-32.1."""
    consumer, intake, _ = build(max_retries=10)
    intake.errors = [error] * 5
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        assert await consumer.handle(d) is False
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]
    assert list(intake.accepted) == []


async def test_contention_does_not_count() -> None:
    """Contention is a wait, not a failure: requeued beyond ``max_retries`` and the bound of 5,
    never parked, never a restart; then the event is accepted once."""
    consumer, intake, _ = build(max_retries=2)
    intake.errors = [Contention("lock")] * 12
    for _ in range(12):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        assert await consumer.handle(d) is False
        assert d.actions == ["requeue"]
    d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
    await consumer.handle(d)
    assert d.actions == ["ack"]
    assert list(intake.accepted) == ["pay-1"]
