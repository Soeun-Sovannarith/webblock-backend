"""Project's "owner status changed" event (decision 0021 D9, decision 0022 Task 5): the consumer
decodes the event and hands it to the owner-status intake (``AcceptOwnerStatusHandler``, here with a
fake store), then settles the delivery:

- accepted, replayed, stale or a first hosting row: ack;
- an invalid body, or a domain error from the use case: dead-lettered at once, no store call;
- a transient refusal: held, requeued, parked after ``max_retries``;
- a store error: requeued, dead-lettered at the fifth counted failure of the same message.
"""

import dataclasses
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid7

import pytest
from tests.unit._buses import intake_bus
from tests.unit._loop_fakes import MS, PROJECT, FakeDelivery, SequentialIds, logger_with_handler

from provisioning.core.application import AcceptedCommand, OperationInProgress, SubdomainTaken
from provisioning.core.application.hosting.background.retry import RetryOutcome, RetryResult
from provisioning.core.application.hosting.commands.accept_owner_status import (
    AcceptOwnerStatusHandler,
    owner_status_kind,
)
from provisioning.core.application.hosting.ports import (
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
)
from provisioning.core.application.shared.audit import AuditEvent
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import (
    CommandRequest,
    CommandStatus,
    IdempotencyConflict,
    new_command,
    owner_status_key,
)
from provisioning.presentation.messaging import EventCodecV1
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import OwnerStatusConsumer

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
MESSAGE_ID = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"


@dataclasses.dataclass(frozen=True)
class Changed:
    project_id: UUID
    status: OwnerStatus
    status_version: int
    subdomain: str


SUSPENDED = Changed(PROJECT, OwnerStatus.SUSPENDED, 1, "shop")


class FakeDecoder:
    def __init__(self, result: Changed | Exception = SUSPENDED) -> None:
        self.result = result

    def decode_owner_status_changed(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> Changed:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeStore:
    """``OwnerStatusStore``: answers ``outcome`` for each call (accepting a command for
    ``ACCEPTED`` and ``REPLAYED``), or raises the next of ``errors``."""

    def __init__(self, outcome: OwnerStatusOutcome = OwnerStatusOutcome.ACCEPTED) -> None:
        self.outcome = outcome
        self.command_status = CommandStatus.ACCEPTED
        self.errors: list[Exception] = []
        self.calls: list[tuple[UUID, OwnerStatus, int, str]] = []

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
        self.calls.append((project_id, status, version, subdomain))
        if self.errors:
            raise self.errors.pop(0)
        if self.outcome not in (OwnerStatusOutcome.ACCEPTED, OwnerStatusOutcome.REPLAYED):
            return OwnerStatusResult(self.outcome, None)
        command = new_command(
            command_id=command_id,
            request=CommandRequest(
                project_id=project_id,
                kind=owner_status_kind(status),
                release_id=None,
                idempotency_key=owner_status_key(version),
            ),
            requested_by_subject=None,
            command_number=1,
            now=now,
        )
        command = dataclasses.replace(command, status=self.command_status)
        replayed = self.outcome is OwnerStatusOutcome.REPLAYED
        return OwnerStatusResult(self.outcome, AcceptedCommand(command=command, replayed=replayed))


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("no alert expected")


class Clock:
    def now(self) -> datetime:
        return NOW


class FakeRetries:
    def __init__(self) -> None:
        self.asked: list[UUID] = []

    async def retry(self, project_id: UUID) -> RetryResult:
        self.asked.append(project_id)
        return RetryResult(project_id, RetryOutcome.NOT_NEEDED, None, None)


@dataclasses.dataclass
class Built:
    consumer: OwnerStatusConsumer
    store: FakeStore
    audit: Audit
    retries: FakeRetries
    lines: list[str]


def build(
    decoder: FakeDecoder | EventCodecV1 | None = None,
    *,
    store: FakeStore | None = None,
    max_retries: int = 2,
) -> Built:
    store = store or FakeStore()
    audit = Audit()
    retries = FakeRetries()
    logger, handler = logger_with_handler("owner-status")
    intake = AcceptOwnerStatusHandler(
        store=store,
        clock=Clock(),
        ids=SequentialIds(),
        command_deadline=timedelta(hours=1),
        audit=audit,
        logger=logging.getLogger("owner-status-intake"),
    )
    consumer = OwnerStatusConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=MS, max=2 * MS), store_failure_limit=10
        ),
        decoder=decoder or FakeDecoder(),
        commands=intake_bus(owner_status_handler=intake),
        retries=retries,
        audit=audit,
        clock=Clock(),
        logger=logger,
        retry_backoff=Backoff(base=MS, max=4 * MS),
        max_retries=max_retries,
    )
    return Built(consumer, store, audit, retries, handler.lines)


def body_with(subdomain: str) -> bytes:
    """A v1 owner-status body as Project sends it, with ``subdomain`` written as given."""
    message_id = str(uuid7())
    return json.dumps(
        {
            "schema_version": 1,
            "message_id": message_id,
            "message_type": "project.owner_status_changed.v1",
            "occurred_at": "2026-10-24T12:00:00Z",
            "correlation_id": "owner-status:1",
            "project_id": str(PROJECT),
            "status": "suspended",
            "status_version": 1,
            "subdomain": subdomain,
        }
    ).encode()


def delivery_of(body: bytes) -> FakeDelivery:
    return FakeDelivery(
        body,
        message_type="project.owner_status_changed.v1",
        message_id=json.loads(body)["message_id"],
    )


async def test_a_valid_event_calls_the_use_case_once_with_its_four_values_and_is_acked() -> None:
    built = build()
    d = FakeDelivery(message_id=MESSAGE_ID)
    assert await built.consumer.handle(d) is False
    assert built.store.calls == [(PROJECT, OwnerStatus.SUSPENDED, 1, "shop")]
    assert d.actions == ["ack"]
    # The use case audits the accepted command; the consumer adds no second event.
    assert len(built.audit.events) == 1


async def test_the_real_decoder_hands_a_valid_body_to_the_use_case() -> None:
    built = build(EventCodecV1(max_body_bytes=4096))
    d = delivery_of(body_with("shop"))
    await built.consumer.handle(d)
    assert d.actions == ["ack"]
    assert built.store.calls == [(PROJECT, OwnerStatus.SUSPENDED, 1, "shop")]


@pytest.mark.parametrize(
    "outcome",
    [OwnerStatusOutcome.STALE, OwnerStatusOutcome.REPLAYED, OwnerStatusOutcome.ROW_INSERTED],
)
async def test_stale_replayed_and_a_first_row_are_acked(outcome: OwnerStatusOutcome) -> None:
    """FR-29.2: stale replayed and a first row are acked."""
    built = build(store=FakeStore(outcome))
    d = FakeDelivery()
    await built.consumer.handle(d)
    assert d.actions == ["ack"]
    assert built.audit.events == []
    assert built.retries.asked == []


async def test_a_replay_of_a_failed_command_asks_for_a_retry() -> None:
    store = FakeStore(OwnerStatusOutcome.REPLAYED)
    store.command_status = CommandStatus.FAILED
    built = build(store=store)
    d = FakeDelivery()
    await built.consumer.handle(d)
    assert d.actions == ["ack"]
    assert built.retries.asked == [PROJECT]


async def test_an_invalid_body_is_dead_lettered_without_calling_the_use_case() -> None:
    built = build(FakeDecoder(ValueError("message rejected: reason=schema_invalid")))
    d = FakeDelivery()
    await built.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert built.store.calls == []


@pytest.mark.parametrize("subdomain", ["-bad-", "a" * 64])
async def test_owner_status_invalid_subdomain_is_dead_lettered_without_a_row(
    subdomain: str,
) -> None:
    built = build(EventCodecV1(max_body_bytes=4096))
    d = delivery_of(body_with(subdomain))
    await built.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert built.store.calls == []


async def test_a_domain_error_from_the_use_case_is_dead_lettered_at_once() -> None:
    # A decoder that let a bad label through: the use case refuses it before the store.
    built = build(FakeDecoder(dataclasses.replace(SUSPENDED, subdomain="-bad-")))
    d = FakeDelivery()
    await built.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert built.store.calls == []
    assert any("InvalidSubdomain" in line and "dead-lettered" in line for line in built.lines)


async def test_a_conflicting_key_is_dead_lettered() -> None:
    # The owner-status store no longer answers a repeated version with a conflict (0023 G17: it is
    # STALE and acked); a conflict that still reaches the consumer is a permanent refusal.
    built = build()
    built.store.errors = [IdempotencyConflict("the key holds the other kind")]
    d = FakeDelivery()
    await built.consumer.handle(d)
    assert d.actions == ["dead_letter"]


async def test_s2_a_taken_subdomain_is_dead_lettered_at_once_and_logged() -> None:
    """FR-54.1 (TR-53; Task 12 security review S2): the sub-domain belongs to another project's
    hosting row; retrying cannot help, so the event goes to the dead-letter queue at once."""
    built = build(max_retries=10)
    built.store.errors = [SubdomainTaken("the sub-domain belongs to another project")]
    d = FakeDelivery(message_id=MESSAGE_ID)
    await built.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert len(built.store.calls) == 1
    assert any("SubdomainTaken" in line and "dead-lettered" in line for line in built.lines)


async def test_a_busy_project_is_held_requeued_then_parked() -> None:
    built = build(max_retries=2)
    built.store.errors = [OperationInProgress()] * 3
    actions = []
    for _ in range(3):
        d = FakeDelivery()
        await built.consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue", "requeue", "dead_letter"]


async def test_a_store_error_is_requeued_and_dead_lettered_at_the_fifth_failure() -> None:
    """FR-32.1: a store error is requeued and dead lettered at the fifth failure."""
    built = build(max_retries=10)
    built.store.errors = [ConnectionError("db")] * 5
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id=MESSAGE_ID)
        await built.consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]


async def test_a_store_error_then_success_acks_the_redelivery() -> None:
    built = build()
    built.store.errors = [ConnectionError("db")]
    first, again = FakeDelivery(message_id=MESSAGE_ID), FakeDelivery(message_id=MESSAGE_ID)
    await built.consumer.handle(first)
    await built.consumer.handle(again)
    assert first.actions == ["requeue"] and again.actions == ["ack"]
    assert len(built.store.calls) == 2
