"""The operator's dead-letter tool on a real RabbitMQ (task W6-E2).

- A Payment event rejected by its consumer is parked by the broker with an ``x-death`` record; the
  tool lists it, re-drives it, and the queue's consumer receives it exactly once, decodable under
  the v1 schema, while the dead-letter queue is empty.
- A listing settles nothing: listed more often than a quorum queue's default delivery limit, the
  message is still parked, intact.
- A queue outside the allow-list is refused and its messages are untouched.
- A copy the broker returns (no queue bound) is not confirmed, so the parked message stays.
- A crash between the confirm and the removal leaves the message both delivered and parked: a
  second re-drive delivers a duplicate, never a loss.

The topology is declared by the real ``BrokerClient`` with the production queue arguments (quorum
queues, a dead-letter exchange), under unique names per test; the allow-list is that topology.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import aio_pika
import pytest
from aio_pika.abc import AbstractIncomingMessage
from tests.messaging._broker import (
    Broker,
    broker_config,
    limits,
    logger,
    queue_depth,
    running,
    unique_names,
    wait_for,
)

from provisioning.core.domain import CommandKind
from provisioning.infrastructure.rabbitmq import BrokerClient, QueueNames, QueueTopology
from provisioning.infrastructure.rabbitmq.dlq import (
    DeadLetterSession,
    DeadLetterTool,
    Fetched,
    QueueNotAllowed,
    RedrivePublishFailed,
    RemovalUnconfirmed,
    SessionFactory,
    amqp_sessions,
)
from provisioning.presentation.messaging import (
    CONTENT_TYPE_JSON,
    EncodedMessage,
    EventCodecV1,
)

pytestmark = pytest.mark.asyncio

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
AT = datetime(2026, 10, 10, 9, tzinfo=UTC)
CODEC = EventCodecV1(max_body_bytes=4096)
ENDED_KEY = "hosting_entitlement_ended.v1"
SETTLE_SECONDS = 5.0
"""How long a count may take to reach its expected value before the test reads it as final."""


def topology() -> QueueNames:
    return unique_names("dlq", routing_keys=(ENDED_KEY,))


def sessions(broker: Broker) -> SessionFactory:
    return amqp_sessions(
        broker.url,
        dial_timeout=timedelta(seconds=5),
        call_timeout=timedelta(seconds=5),
        connection_name=f"test-dlq-{secrets.token_hex(3)}",
    )


def event(key: str) -> EncodedMessage:
    return CODEC.encode_hosting_event(
        message_id=UUID(int=secrets.randbits(122)),
        event_key=key,
        project_id=PROJECT,
        kind=CommandKind.SUSPEND_HOSTING,
        reason="trial_ended",
        occurred_at=AT,
    )


@asynccontextmanager
async def declared(broker: Broker, names: QueueNames) -> AsyncIterator[BrokerClient]:
    client = BrokerClient(
        broker_config(broker.url, (QueueTopology(names=names, limits=limits()),)),
        logger=logger("dlq-client"),
    )
    async with running(client):
        yield client


async def park(broker: Broker, client: BrokerClient, names: QueueNames, key: str) -> EncodedMessage:
    """Publish a well-formed event, then reject it as its consumer would: the broker parks it."""
    message = event(key)
    await client.publish(
        exchange=names.exchange,
        routing_key=ENDED_KEY,
        body=message.body,
        content_type=message.content_type,
        message_type=message.message_type,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        timestamp=message.timestamp,
    )
    connection = await aio_pika.connect(broker.url)
    try:
        channel = await connection.channel()
        queue = await channel.get_queue(names.queue, ensure=False)
        got = await queue.get(no_ack=False, fail=True, timeout=5)
        assert got is not None and got.message_id == message.message_id
        await got.reject(requeue=False)
    finally:
        await connection.close()

    async def parked() -> bool:
        return await queue_depth(broker.url, names.dead_letter_queue) >= 1

    await wait_for(parked, within_seconds=10)
    return message


async def settled_depth(broker: Broker, queue: str, expected: int) -> int:
    """The depth of ``queue`` once it reads ``expected``, or the last depth read when
    ``SETTLE_SECONDS`` pass. A quorum queue takes back the messages of a closed channel a moment
    after the close returns, so a count read at once can still miss them."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_SECONDS
    depth = await queue_depth(broker.url, queue)
    while depth != expected and loop.time() < deadline:
        await asyncio.sleep(0.05)
        depth = await queue_depth(broker.url, queue)
    return depth


async def take_all(broker: Broker, queue: str) -> list[AbstractIncomingMessage]:
    """Every message of ``queue``, acknowledged: what its consumer would receive."""
    connection = await aio_pika.connect(broker.url)
    out: list[AbstractIncomingMessage] = []
    try:
        channel = await connection.channel()
        declared_queue = await channel.get_queue(queue, ensure=False)
        while (message := await declared_queue.get(fail=False, timeout=5)) is not None:
            out.append(message)
            await message.ack()
    finally:
        await connection.close()
    return out


async def test_a_parked_event_is_listed_redriven_and_received_once(broker: Broker) -> None:
    names = topology()
    async with declared(broker, names) as client:
        sent = await park(broker, client, names, "dlq-e1")
        tool = DeadLetterTool((names,), sessions(broker))

        (item,) = await tool.list(names.dead_letter_queue, 10)
        assert item.message_id == sent.message_id
        assert item.death is not None
        assert (item.death.reason, item.death.count) == ("rejected", 1)
        assert (item.death.exchange, item.death.routing_key) == (
            names.exchange,
            ENDED_KEY,
        )
        assert item.envelope.event_key == "dlq-e1"
        assert item.envelope.project_id == PROJECT
        assert await settled_depth(broker, names.dead_letter_queue, 1) == 1

        await tool.redrive(names.dead_letter_queue, sent.message_id, scan_limit=10)
        assert await settled_depth(broker, names.dead_letter_queue, 0) == 0
        received = await take_all(broker, names.queue)
    assert len(received) == 1
    copy = received[0]
    assert copy.message_id == sent.message_id
    assert copy.body == sent.body
    assert copy.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
    assert "x-death" not in (copy.headers or {})
    decoded = CODEC.decode_hosting_event(
        body=copy.body,
        content_type=copy.content_type,
        message_type=copy.type,
        message_id=copy.message_id,
    )
    assert (decoded.event_key, decoded.project_id) == ("dlq-e1", PROJECT)
    assert copy.content_type == CONTENT_TYPE_JSON


async def test_listing_more_often_than_the_delivery_limit_loses_nothing(broker: Broker) -> None:
    names = topology()
    async with declared(broker, names) as client:
        sent = await park(broker, client, names, "dlq-e2")
        tool = DeadLetterTool((names,), sessions(broker))
        # RabbitMQ 4.3.6 counts each return towards the default delivery limit of 20 and drops the
        # message at the limit (the dead-letter queue has no dead-letter exchange): without the
        # refresh the 20th listing finds nothing. 45 listings pass the limit twice.
        for _ in range(45):
            (item,) = await tool.list(names.dead_letter_queue, 5)
            assert item.message_id == sent.message_id
            # The refreshed copy keeps its death record, so its origin is still known.
            assert item.death is not None
            assert (item.death.reason, item.death.exchange, item.death.routing_key) == (
                "rejected",
                names.exchange,
                ENDED_KEY,
            )
            assert await settled_depth(broker, names.dead_letter_queue, 1) == 1
        await tool.redrive(names.dead_letter_queue, sent.message_id, scan_limit=5)
        received = await take_all(broker, names.queue)
    assert [m.body for m in received] == [sent.body]


async def test_a_queue_outside_the_allow_list_is_refused_and_untouched(broker: Broker) -> None:
    names, other = topology(), topology()
    async with declared(broker, names) as client, declared(broker, other) as other_client:
        await park(broker, client, names, "dlq-e3")
        await park(broker, other_client, other, "dlq-e4")
        tool = DeadLetterTool((names,), sessions(broker))
        for queue in (other.dead_letter_queue, names.queue, other.queue):
            with pytest.raises(QueueNotAllowed):
                await tool.list(queue, 5)
            with pytest.raises(QueueNotAllowed):
                await tool.redrive(queue, "anything", scan_limit=5)
        assert await settled_depth(broker, other.dead_letter_queue, 1) == 1
        assert await settled_depth(broker, names.dead_letter_queue, 1) == 1


async def test_a_returned_copy_is_not_confirmed_and_the_message_stays_parked(
    broker: Broker,
) -> None:
    names = topology()
    async with declared(broker, names) as client:
        sent = await park(broker, client, names, "dlq-e5")
        connection = await aio_pika.connect(broker.url)
        try:
            channel = await connection.channel()
            # The re-drive goes to the work queue through the default exchange: with the work
            # queue gone, the broker returns the copy.
            await channel.queue_delete(names.queue)
        finally:
            await connection.close()
        tool = DeadLetterTool((names,), sessions(broker))
        with pytest.raises(RedrivePublishFailed) as caught:
            await tool.redrive(names.dead_letter_queue, sent.message_id, scan_limit=5)
        assert caught.value.code == "publish_returned"
        assert await settled_depth(broker, names.dead_letter_queue, 1) == 1
        # The one parked message is the same message, not merely some message.
        (item,) = await tool.list(names.dead_letter_queue, 5)
        assert item.message_id == sent.message_id


class _CrashBeforeRemoval:
    """A session whose removal never reaches the broker, as when the process dies between the
    confirm and the acknowledgment."""

    def __init__(self, inner: DeadLetterSession) -> None:
        self._inner = inner

    async def fetch(self, queue: str) -> Fetched | None:
        return await self._inner.fetch(queue)

    async def publish(self, exchange: str, routing_key: str, message: Fetched) -> None:
        await self._inner.publish(exchange, routing_key, message)

    async def ack(self, message: Fetched) -> None:
        del message
        raise ConnectionResetError("the operator's process died here")


async def test_a_crash_between_the_confirm_and_the_removal_gives_a_duplicate_never_a_loss(
    broker: Broker,
) -> None:
    names = topology()
    real = sessions(broker)

    @asynccontextmanager
    async def crashing() -> AsyncIterator[DeadLetterSession]:
        async with real() as session:
            yield _CrashBeforeRemoval(session)

    async with declared(broker, names) as client:
        sent = await park(broker, client, names, "dlq-e6")
        with pytest.raises(RemovalUnconfirmed):
            await DeadLetterTool((names,), crashing).redrive(
                names.dead_letter_queue, sent.message_id, scan_limit=5
            )
        # Delivered once, and still parked: nothing lost.
        assert await settled_depth(broker, names.queue, 1) == 1
        assert await settled_depth(broker, names.dead_letter_queue, 1) == 1
        # The operator re-drives again: a duplicate, which the consumers tolerate.
        await DeadLetterTool((names,), real).redrive(
            names.dead_letter_queue, sent.message_id, scan_limit=5
        )
        assert await settled_depth(broker, names.dead_letter_queue, 0) == 0
        received = await take_all(broker, names.queue)
    assert [m.message_id for m in received] == [sent.message_id, sent.message_id]
