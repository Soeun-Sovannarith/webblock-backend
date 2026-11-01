"""Project's "source published" consumer (decision 0008 section 2, decision 0014 sections 3 and
10.5), with in-memory fakes. The inbox processor's tests are in
tests/unit/core/application/intake/background/test_inbox_processor.py.

The consumer's fake intake is the inbox itself: a stored key answers DUPLICATE on a repeat with the
same source and raises ``IdempotencyConflict`` with another one, as ``ReleaseIntake.receive`` does.
"""

import asyncio
import dataclasses
from datetime import timedelta
from typing import Any
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

from provisioning.core.application import Contention, IntakeOutcome, StoreUnreachable, Timeout
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import (
    IdempotencyConflict,
    InvalidCommitSha,
    SourcePublishedFields,
    parse_source_published,
)
from provisioning.presentation.messaging.consumers.inbox import SourcePublishedConsumer
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig

pytestmark = pytest.mark.asyncio

REQUEST = UUID("0199c4a1-0000-7000-8000-0000000000a1")
SHA = "a" * 40
FIELDS = SourcePublishedFields(
    project_id=str(PROJECT),
    generation_request_id=str(REQUEST),
    repository_id=42,
    git_commit_sha=SHA,
)


def consumer_config(**changes: Any) -> EventConsumerConfig:
    base = EventConsumerConfig(
        concurrency=2, store_hold=Backoff(base=MS, max=2 * MS), store_failure_limit=2
    )
    return dataclasses.replace(base, **changes)


class FakeSourceDecoder:
    def __init__(self, result: SourcePublishedFields | Exception = FIELDS) -> None:
        self.result = result
        self.message_ids: list[str | None] = []

    def decode_source_published(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> SourcePublishedFields:
        self.message_ids.append(message_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeInbox:
    """``ReleaseIntake.receive`` over an in-memory inbox."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], SourcePublishedFields] = {}
        self.errors: list[Exception] = []
        self.calls = 0
        self.capacity = 100

    async def receive(self, fields: SourcePublishedFields) -> IntakeOutcome:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        parse_source_published(fields)
        key = (fields.project_id, fields.generation_request_id)
        stored = self.rows.get(key)
        if stored is None:
            if len(self.rows) >= self.capacity:
                return IntakeOutcome.OVER_CAPACITY
            self.rows[key] = fields
            return IntakeOutcome.STORED
        if stored != fields:
            raise IdempotencyConflict("published with another source")
        return IntakeOutcome.DUPLICATE


class Consumer:
    def __init__(
        self, decoder: FakeSourceDecoder | None = None, cfg: EventConsumerConfig | None = None
    ) -> None:
        self.inbox = FakeInbox()
        self.woken = 0
        self.logger, self.log = logger_with_handler("inbox-consumer")
        self.consumer = SourcePublishedConsumer(
            cfg or consumer_config(),
            decoder=decoder or FakeSourceDecoder(),
            commands=intake_bus(source=self.inbox),
            logger=self.logger,
            on_stored=self.wake,
        )

    def wake(self) -> None:
        self.woken += 1


async def test_a_new_event_is_stored_acknowledged_and_wakes_the_processor() -> None:
    c = Consumer()
    d = FakeDelivery()
    assert await c.consumer.handle(d) is False
    assert d.actions == ["ack"]
    assert list(c.inbox.rows) == [(str(PROJECT), str(REQUEST))]
    assert c.woken == 1


async def test_a_duplicate_event_is_a_no_op() -> None:
    """FR-10.1: a duplicate event is a no op."""
    c = Consumer()
    await c.consumer.handle(FakeDelivery())
    d = FakeDelivery(redelivered=True)
    await c.consumer.handle(d)
    assert d.actions == ["ack"]
    assert len(c.inbox.rows) == 1
    assert c.woken == 1


async def test_a_repeat_with_another_source_is_acknowledged_and_logged_by_key_only() -> None:
    """FR-10.2: a repeat with another source is acknowledged and logged by key only."""
    c = Consumer()
    await c.consumer.handle(FakeDelivery())
    other = dataclasses.replace(FIELDS, git_commit_sha="b" * 40)
    c.consumer = SourcePublishedConsumer(
        consumer_config(),
        decoder=FakeSourceDecoder(other),
        commands=intake_bus(source=c.inbox),
        logger=c.logger,
        on_stored=c.wake,
    )
    d = FakeDelivery()
    await c.consumer.handle(d)
    assert d.actions == ["ack"]
    assert c.inbox.rows[(str(PROJECT), str(REQUEST))] == FIELDS
    assert "b" * 40 not in c.log.text() and SHA not in c.log.text()
    assert str(REQUEST) in c.log.text()


async def test_an_invalid_payload_is_dead_lettered_without_touching_the_inbox() -> None:
    c = Consumer(FakeSourceDecoder(ValueError("message rejected: reason=schema_invalid")))
    d = FakeDelivery(BODY_MARKER.encode())
    await c.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert c.inbox.calls == 0
    assert BODY_MARKER not in c.log.text()


async def test_the_amqp_message_id_reaches_the_decoder_and_a_refusal_logs_it() -> None:
    decoder = FakeSourceDecoder(ValueError("message rejected: reason=envelope_mismatch"))
    c = Consumer(decoder)
    message_id = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"
    await c.consumer.handle(FakeDelivery(message_id=message_id))
    await c.consumer.handle(FakeDelivery(message_id="not\na-uuid"))
    assert decoder.message_ids == [message_id, "not\na-uuid"]
    assert f"message_id={message_id}" in c.log.text()
    assert "message_id=invalid" in c.log.text()
    assert "not\na-uuid" not in c.log.text()


async def test_an_event_the_domain_refuses_is_dead_lettered() -> None:
    c = Consumer(FakeSourceDecoder(dataclasses.replace(FIELDS, git_commit_sha="xyz")))
    d = FakeDelivery()
    await c.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert c.inbox.rows == {}
    c2 = Consumer()
    c2.inbox.errors = [InvalidCommitSha("commit")]
    d2 = FakeDelivery()
    await c2.consumer.handle(d2)
    assert d2.actions == ["dead_letter"]


async def test_over_capacity_is_held_and_requeued() -> None:
    c = Consumer()
    c.inbox.capacity = 0
    d = FakeDelivery()
    assert await c.consumer.handle(d) is False
    assert d.actions == ["requeue"]
    assert c.woken == 0


async def test_counted_errors_are_requeued_and_restart_the_stream_after_the_limit() -> None:
    """Failures while the database answered count; contention does not."""
    c = Consumer()
    c.inbox.errors = [ConnectionError("db"), Contention("lock"), ConnectionError("db")]
    results = []
    for _ in range(3):
        d = FakeDelivery()
        results.append(await c.consumer.handle(d))
        assert d.actions == ["requeue"]
    assert results == [False, False, True]


async def test_an_event_whose_inbox_write_keeps_failing_is_dead_lettered_at_the_fifth() -> None:
    """FR-32.1: an event whose inbox write keeps failing is dead lettered at the fifth."""
    c = Consumer(cfg=consumer_config(store_failure_limit=100))
    c.inbox.errors = [ConnectionError("db")] * 5
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        await c.consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]
    assert "size=2 sha256=" in c.log.text()


async def test_contention_and_the_cap_never_dead_letter_an_event() -> None:
    c = Consumer()
    c.inbox.capacity = 0
    c.inbox.errors = [Contention("lock")] * 6
    for _ in range(12):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        await c.consumer.handle(d)
        assert d.actions == ["requeue"]


async def test_run_serves_the_stream_until_it_stops() -> None:
    c = Consumer()
    stream = FakeStream()
    deliveries = [FakeDelivery(), FakeDelivery()]
    for d in deliveries:
        stream.put(d)
    task = asyncio.create_task(c.consumer.run(stream))
    await eventually(lambda: all(d.actions for d in deliveries))
    stream.close()
    async with asyncio.timeout(1):
        await task
    assert [d.actions for d in deliveries] == [["ack"], ["ack"]]


async def test_the_consumer_config_refuses_non_positive_values() -> None:
    for changes in (
        {"concurrency": 0},
        {"store_failure_limit": 0},
        {"store_hold": Backoff(base=timedelta(0), max=MS)},
    ):
        with pytest.raises(InvalidConfig):
            consumer_config(**changes).validate()
        with pytest.raises(InvalidConfig):
            Consumer(cfg=consumer_config(**changes))


async def test_a_database_outage_counts_toward_the_bound() -> None:
    """Decision 0023 G15 (owner): unanswered inbox writes count towards the event's 5 tries,
    then it goes to its ``.dlq``; never a restart (Phase B's fix).

    FR-32.1."""
    c = Consumer(cfg=consumer_config(store_failure_limit=2))
    c.inbox.errors = [StoreUnreachable("db down")] * 3 + [Timeout("pool")] * 2
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        assert await c.consumer.handle(d) is False
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]
    assert c.woken == 0
