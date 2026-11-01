"""The usage publisher and the deletion event on a real RabbitMQ (decision 0014 section 21 answers
2 and 4; task W6-E).

- A usage event is published persistent with a confirm, reaches the queue that stands in for
  Payment's, and decodes under the v1 schema.
- Broker outage, then recovery: while the broker is down the publish fails and the row stays
  pending with a failure recorded; after the broker returns the next poll sends it. Nothing is lost.
- A crash between the send and the mark: the event reaches the queue, the row stays pending, and
  after the visibility timeout it is sent again. Payment sees a duplicate with the same key, never
  a loss.
- The simulator's "project deleted" event reaches the deletion consumer, which accepts the
  ``suspend_hosting`` command with the fixed key ``project-deleted``.

The outbox here is in memory (its SQL is proven in tests/integration/test_routing_store.py); the
broker, the client, the codec and the publisher are the real ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import aio_pika
import pytest
from tests.messaging._broker import (
    Broker,
    broker_config,
    declare_producers,
    limits,
    logger,
    queue_depth,
    running,
    unique_names,
    wait_for,
)
from tests.messaging.test_loops import simulate
from tests.unit._buses import intake_bus

from provisioning.core.application import AcceptedCommand
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.application.usage.background.usage_publisher import (
    UsagePublisher,
    UsagePublisherConfig,
)
from provisioning.core.application.usage.ports import UsageEvent, usage_event_key
from provisioning.core.domain import CommandKind, CommandRequest, new_command
from provisioning.hosts.shared.runtime import SystemClock, UUIDv7
from provisioning.infrastructure.rabbitmq import (
    PROJECT_DELETED_NAMES,
    PUBLISH_COMPLETED_ROUTING_KEY,
    BrokerClient,
    QueueNames,
    QueueTopology,
)
from provisioning.presentation.messaging import (
    PUBLISH_COMPLETED_V1,
    EventCodecV1,
    PublishCompleted,
    UsageEventPublisher,
)
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import ProjectDeletionConsumer

pytestmark = pytest.mark.asyncio

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
AT = datetime(2026, 10, 9, 10, tzinfo=UTC)
MS = timedelta(milliseconds=1)


def release(n: int) -> UUID:
    return UUID(int=0x0199C4A1_7C1E_7D4B_9A3E_5B2F8C6D0000 + n)


class Outbox:
    """The outbox's contract in memory: a take counts an attempt and hides the row for the
    visibility timeout; a mark or a failure applies only at the taken attempt."""

    def __init__(self, n: int) -> None:
        loop = asyncio.get_running_loop()
        self.now = loop.time
        self.rows: dict[UUID, dict[str, object]] = {
            release(i): {"attempts": 0, "sent": False, "due": 0.0, "failure": None}
            for i in range(n)
        }
        self.mark_faults: list[Exception] = []

    async def take_due(self, limit: int, visibility: timedelta) -> tuple[UsageEvent, ...]:
        out: list[UsageEvent] = []
        for rid, row in self.rows.items():
            if row["sent"] or float(str(row["due"])) > self.now() or len(out) >= limit:
                continue
            row["attempts"] = int(str(row["attempts"])) + 1
            row["due"] = self.now() + visibility.total_seconds()
            out.append(
                UsageEvent(
                    release_id=rid,
                    project_id=PROJECT,
                    event_key=usage_event_key(rid),
                    completed_at=AT,
                    attempts=int(str(row["attempts"])),
                )
            )
        return tuple(out)

    async def mark_sent(self, event: UsageEvent) -> bool:
        if self.mark_faults:
            raise self.mark_faults.pop(0)
        row = self.rows[event.release_id]
        if row["sent"] or row["attempts"] != event.attempts:
            return False
        row["sent"] = True
        return True

    async def record_failure(self, event: UsageEvent, retry_after: timedelta, code: str) -> bool:
        row = self.rows[event.release_id]
        if row["sent"] or row["attempts"] != event.attempts:
            return False
        row["due"] = self.now() + retry_after.total_seconds()
        row["failure"] = code
        return True

    def all_sent(self) -> bool:
        return all(bool(row["sent"]) for row in self.rows.values())


def publisher(client: BrokerClient, names: QueueNames, outbox: Outbox) -> UsagePublisher:
    return UsagePublisher(
        UsagePublisherConfig(
            interval=20 * MS,
            batch_size=10,
            confirm_timeout=timedelta(seconds=2),
            visibility_timeout=timedelta(seconds=3),
            backoff=Backoff(base=50 * MS, max=200 * MS),
        ),
        outbox=outbox,
        sender=UsageEventPublisher(
            codec=EventCodecV1(max_body_bytes=4096),
            raw=client,
            exchange=names.exchange,
            routing_key=names.routing_keys[0],
        ),
        ids=UUIDv7(),
        audit=NullAuditLog(),
        clock=SystemClock(),
        logger=logger("usage"),
    )


async def drain(url: str, queue: str) -> list[PublishCompleted]:
    """Every message in ``queue``, decoded under the v1 schema, acknowledged."""
    codec = EventCodecV1(max_body_bytes=4096)
    connection = await aio_pika.connect(url)
    out: list[PublishCompleted] = []
    try:
        channel = await connection.channel()
        declared = await channel.declare_queue(queue, passive=True)
        while (message := await declared.get(fail=False)) is not None:
            assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
            assert message.type == PUBLISH_COMPLETED_V1
            out.append(
                codec.decode_publish_completed(
                    body=message.body,
                    content_type=message.content_type,
                    message_type=message.type,
                    message_id=message.message_id,
                )
            )
            await message.ack()
    finally:
        await connection.close()
    return out


def setup(broker: Broker) -> tuple[QueueNames, BrokerClient]:
    names = unique_names("usage", routing_keys=(PUBLISH_COMPLETED_ROUTING_KEY,))
    client = BrokerClient(
        broker_config(broker.url, (QueueTopology(names=names, limits=limits()),)),
        logger=logger("usage-client"),
    )
    return names, client


async def test_each_usage_event_reaches_the_queue_under_the_schema(broker: Broker) -> None:
    """FR-42.1: each usage event reaches the queue under the schema."""
    names, client = setup(broker)
    outbox = Outbox(3)
    async with running(client):
        sent = await publisher(client, names, outbox).poll()
    assert sent == 3 and outbox.all_sent()
    got = await drain(broker.url, names.queue)
    assert sorted(e.event_key for e in got) == sorted(usage_event_key(release(i)) for i in range(3))
    assert all(e.project_id == PROJECT and e.occurred_at == AT for e in got)


async def test_a_broker_outage_holds_the_events_until_the_broker_returns(broker: Broker) -> None:
    """FR-42.2: a broker outage holds the events until the broker returns."""
    names, client = setup(broker)
    outbox = Outbox(2)
    async with running(client):
        loop = asyncio.create_task(publisher(client, names, outbox).run())
        try:
            broker.stop_app()
            try:
                await wait_for(lambda: not client.connected)
                # A poll during the outage records a failure; nothing is marked sent.
                await wait_for(
                    lambda: all(row["failure"] is not None for row in outbox.rows.values()),
                    within_seconds=20,
                )
                assert not any(row["sent"] for row in outbox.rows.values())
            finally:
                broker.start_app()
            await wait_for(outbox.all_sent, within_seconds=60)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop
    got = await drain(broker.url, names.queue)
    assert {e.release_id for e in got} == {release(0), release(1)}


async def test_a_crash_between_the_send_and_the_mark_sends_again_and_loses_nothing(
    broker: Broker,
) -> None:
    names, client = setup(broker)
    outbox = Outbox(1)
    outbox.mark_faults = [ConnectionError("the database went away after the confirm")]
    async with running(client):
        loop = asyncio.create_task(publisher(client, names, outbox).run())
        try:
            await wait_for(lambda: outbox.all_sent(), within_seconds=30)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop
    got = await drain(broker.url, names.queue)
    assert [e.event_key for e in got] == [usage_event_key(release(0))] * 2  # a duplicate
    assert outbox.rows[release(0)]["attempts"] == 2


async def both(*coros: Coroutine[Any, Any, None]) -> None:
    async with asyncio.TaskGroup() as group:
        for coro in coros:
            group.create_task(coro)


class DeletionIntake:
    def __init__(self) -> None:
        self.events: list[tuple[str, UUID, CommandKind]] = []

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        replayed = any(e == (event_key, project_id, kind) for e in self.events)
        self.events.append((event_key, project_id, kind))
        command = new_command(
            command_id=UUID(int=1),
            request=CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            ),
            requested_by_subject=None,
            command_number=1,
            now=datetime.now(UTC),
        )
        return AcceptedCommand(command=command, replayed=replayed)

    async def retry(self, project_id: UUID) -> RetryResult:
        raise AssertionError(f"no retry expected for {project_id}")


async def test_the_simulated_deletion_becomes_a_suspend_with_the_fixed_key(
    broker: Broker,
) -> None:
    await declare_producers(broker.url)
    client = BrokerClient(
        broker_config(broker.url, (QueueTopology(names=PROJECT_DELETED_NAMES, limits=limits()),)),
        logger=logger("deletion"),
    )
    intake = DeletionIntake()
    consumer = ProjectDeletionConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=MS, max=MS), store_failure_limit=3
        ),
        decoder=EventCodecV1(max_body_bytes=4096),
        commands=intake_bus(hosting=intake),
        retries=intake,
        audit=NullAuditLog(),
        clock=SystemClock(),
        logger=logger("deletion-consumer"),
        retry_backoff=Backoff(base=MS, max=MS),
        max_retries=3,
    )
    async with running(client):
        subscription = client.subscribe(PROJECT_DELETED_NAMES.queue, prefetch=1)
        consuming = asyncio.create_task(both(subscription.run(), consumer.run(subscription)))
        try:
            for _ in range(2):
                out = await simulate(broker, "project-deleted", "--project", str(PROJECT))
                assert out.returncode == 0, out.stderr
                assert out.stdout.strip() == "event_key=project-deleted"
            await wait_for(lambda: len(intake.events) == 2)
            assert await queue_depth(broker.url, PROJECT_DELETED_NAMES.queue) == 0
        finally:
            consuming.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consuming
    assert intake.events == [("project-deleted", PROJECT, CommandKind.SUSPEND_HOSTING)] * 2
