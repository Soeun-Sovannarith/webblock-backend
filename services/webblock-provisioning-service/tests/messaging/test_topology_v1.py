"""The broker names of decision 0022 E6 on a real RabbitMQ.

Each test runs in its own virtual host, so the fixed names (``provisioning.events``,
``project.events`` and the rest) never meet another test's objects. The test declares
``project.events`` and ``payment.events`` itself, as their owners would; Provisioning only checks
them.

- The worker's and the build worker's topology: ``provisioning.events`` is a topic exchange with
  the alternate exchange ``provisioning.events.unrouted``; each of the six queues they consume is
  quorum, has the delivery limit 5, dead-letters to ``provisioning.dlx`` and has its ``.dlq``.
- A foreign exchange is never declared: without ``project.events`` the client refuses to connect
  with ``ExchangeMissing`` and nothing named ``project.events`` appears.
- A message redelivered 5 times (each time a consumer's channel closed without a settlement)
  lands in the ``.dlq`` on its next return.
- A message whose handler fails 5 times lands in its ``.dlq``, and so does a message that meets a
  database outage 5 times (decision 0023 G15), without a stream restart, so the broker's delivery
  count stays below the limit. Contention (12 contended calls) never counts: the message is
  processed once the lock is free.
- A mandatory ``publish_completed.v1`` with no Payment queue is confirmed and kept in
  ``provisioning.events.unrouted``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import aio_pika
import pytest
import pytest_asyncio
from aio_pika.exceptions import ChannelNotFoundEntity
from tests.messaging._broker import (
    Broker,
    broker_config,
    limits,
    logger,
    queue_depth,
    running,
    wait_for,
)
from tests.unit._buses import intake_bus

from provisioning.core.application import AcceptedCommand, Contention, StoreUnreachable
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import CommandKind, CommandRequest, new_command
from provisioning.hosts.build_worker.wiring import BROKER_QUEUES as BUILD_WORKER_QUEUES
from provisioning.hosts.shared.runtime import SystemClock
from provisioning.hosts.worker.events import EVENT_PUBLISH_EXCHANGES, EVENT_QUEUES
from provisioning.infrastructure.rabbitmq import (
    COMMAND_JOB_NAMES,
    PAYMENT_EVENTS,
    PROJECT_DELETED_NAMES,
    PROJECT_EVENTS,
    PROVISIONING_EVENTS,
    PROVISIONING_EVENTS_UNROUTED,
    PUBLISH_COMPLETED_ROUTING_KEY,
    SOURCE_PUBLISHED_NAMES,
    BrokerClient,
    ExchangeTopology,
    QueueTopology,
)
from provisioning.infrastructure.rabbitmq.dlq import (
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    DeadLetterTool,
    amqp_sessions,
)
from provisioning.presentation.messaging import EventCodecV1
from provisioning.presentation.messaging.consumers.messages import (
    MAX_COUNTED_FAILURES,
    EventConsumerConfig,
)
from provisioning.presentation.messaging.consumers.payment_events import ProjectDeletionConsumer

pytestmark = pytest.mark.asyncio(loop_scope="function")

AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DELIVERY_LIMIT = 5
CONSUMED = (COMMAND_JOB_NAMES, *EVENT_QUEUES, *BUILD_WORKER_QUEUES)


@pytest_asyncio.fixture(loop_scope="function")
async def vhost(broker: Broker) -> AsyncIterator[tuple[str, str]]:
    """A fresh virtual host: (its name, its amqp URL)."""
    name = f"v1-{secrets.token_hex(4)}"
    broker.ctl("add_vhost", name)
    broker.ctl("set_permissions", "-p", name, "webblock", ".*", ".*", ".*")
    try:
        yield name, broker.url + name
    finally:
        broker.ctl("delete_vhost", name)


async def declare_producers(url: str) -> None:
    """``project.events`` and ``payment.events`` as their owners declare them."""
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        for name in (PROJECT_EVENTS, PAYMENT_EVENTS):
            await channel.declare_exchange(name, aio_pika.ExchangeType.TOPIC, durable=True)
    finally:
        await connection.close()


def processes(url: str) -> tuple[BrokerClient, BrokerClient]:
    """The worker's and the build worker's clients, with the delivery limit 5."""
    lim = limits(delivery_limit=DELIVERY_LIMIT)
    worker = broker_config(
        url,
        tuple(QueueTopology(names=n, limits=lim) for n in (COMMAND_JOB_NAMES, *EVENT_QUEUES)),
        publish_exchanges=tuple(
            ExchangeTopology(names=e, limits=lim) for e in EVENT_PUBLISH_EXCHANGES
        ),
    )
    build = broker_config(
        url, tuple(QueueTopology(names=n, limits=lim) for n in BUILD_WORKER_QUEUES)
    )
    return (
        BrokerClient(worker, logger=logger("v1-worker")),
        BrokerClient(build, logger=logger("v1-build")),
    )


def listed(broker: Broker, what: str, vhost: str, *columns: str) -> dict[str, dict[str, object]]:
    """``rabbitmqctl list_<what>`` of ``vhost`` as {name: row}."""
    out = broker.ctl(f"list_{what}", "-p", vhost, "--formatter", "json", *columns)
    return {str(row["name"]): row for row in json.loads(out)}


def arguments(row: dict[str, object]) -> dict[str, object]:
    """The ``arguments`` column, which rabbitmqctl prints as a map or as a list of
    [name, type, value] entries."""
    value = row["arguments"]
    if isinstance(value, dict):
        return value
    assert isinstance(value, list)
    return {str(entry[0]): entry[-1] for entry in value}


async def test_the_processes_declare_the_e6_topology(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    name, url = vhost
    await declare_producers(url)
    worker, build = processes(url)
    async with running(worker), running(build):
        pass
    exchanges = listed(broker, "exchanges", name, "name", "type", "arguments")
    assert exchanges[PROVISIONING_EVENTS]["type"] == "topic"
    assert arguments(exchanges[PROVISIONING_EVENTS]) == {
        "alternate-exchange": PROVISIONING_EVENTS_UNROUTED
    }
    assert exchanges[PROVISIONING_EVENTS_UNROUTED]["type"] == "fanout"
    assert exchanges["provisioning.dlx"]["type"] == "direct"
    assert exchanges["provisioning.jobs"]["type"] == "direct"
    # The producers' exchanges are as the test declared them: Provisioning added nothing.
    assert arguments(exchanges[PROJECT_EVENTS]) == {}
    assert f"{PROJECT_EVENTS}.unrouted" not in exchanges
    queues = listed(broker, "queues", name, "name", "arguments")
    assert len(CONSUMED) == 6  # the owner-status queue since 0022 Task 5
    for names in CONSUMED:
        args = arguments(queues[names.queue])
        assert args["x-queue-type"] == "quorum"
        assert args["x-delivery-limit"] == DELIVERY_LIMIT
        assert args["x-dead-letter-exchange"] == "provisioning.dlx"
        assert names.dead_letter_queue == f"{names.queue}.dlq"
        assert arguments(queues[names.dead_letter_queue])["x-queue-type"] == "quorum"
    assert arguments(queues[PROVISIONING_EVENTS_UNROUTED])["x-queue-type"] == "quorum"


async def test_a_missing_foreign_exchange_is_never_declared(
    broker: Broker, vhost: tuple[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    name, url = vhost
    lim = limits(delivery_limit=DELIVERY_LIMIT)
    client = BrokerClient(
        broker_config(url, (QueueTopology(names=SOURCE_PUBLISHED_NAMES, limits=lim),)),
        logger=logger("v1-missing"),
    )
    with caplog.at_level(logging.WARNING, logger="tests.messaging.v1-missing"):
        task = asyncio.create_task(client.run())
        await wait_for(lambda: client.connect_failures >= 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not client.connected
    assert "error=ExchangeMissing" in caplog.text
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        with pytest.raises(ChannelNotFoundEntity):
            await channel.declare_exchange(PROJECT_EVENTS, passive=True)
    finally:
        await connection.close()
    assert not [n for n in listed(broker, "exchanges", name, "name") if n.startswith("project.")]
    # The owned objects are not declared either: the check of the foreign exchange comes first.
    assert SOURCE_PUBLISHED_NAMES.queue not in listed(broker, "queues", name, "name")


async def test_a_message_redelivered_five_times_lands_in_its_dead_letter_queue(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    """Each delivery is returned the way a crashed consumer returns it: its channel closes
    without a settlement. RabbitMQ 4 counts such returns towards the delivery limit (a
    ``basic.nack`` with requeue does not). The limit counts redeliveries: the first delivery and
    five redeliveries, then the sixth return dead-letters the message."""
    _, url = vhost
    await declare_producers(url)
    worker, _ = processes(url)
    names = PROJECT_DELETED_NAMES
    async with running(worker):
        connection = await aio_pika.connect(url)
        try:
            channel = await connection.channel()
            exchange = await channel.get_exchange(PROJECT_EVENTS)
            await exchange.publish(aio_pika.Message(b"{}"), names.routing_keys[0])
            await channel.close()
            delivered = 0
            while True:
                channel = await connection.channel()
                queue = await channel.get_queue(names.queue)
                message = await queue.get(timeout=5, fail=False)
                await channel.close()  # unsettled: the broker returns it
                if message is None:
                    break
                delivered += 1
                assert delivered <= 2 * DELIVERY_LIMIT, "the delivery limit was not applied"
        finally:
            await connection.close()
        await wait_for(lambda: _depth(url, names.dead_letter_queue, 1))
    assert delivered == 1 + DELIVERY_LIMIT
    assert await queue_depth(url, names.queue) == 0


async def test_an_unbound_usage_event_is_confirmed_and_kept_unrouted(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    _, url = vhost
    await declare_producers(url)
    worker, _ = processes(url)
    async with running(worker) as client:
        await client.publish(
            exchange=PROVISIONING_EVENTS,
            routing_key=PUBLISH_COMPLETED_ROUTING_KEY,
            body=b"{}",
            content_type="application/json",
            message_type="provisioning.publish_completed.v1",
            message_id="0192c0de-0000-7000-8000-000000000001",
            correlation_id="c-1",
            timestamp=AT,
        )
    await wait_for(lambda: _depth(url, PROVISIONING_EVENTS_UNROUTED, 1))


async def _depth(url: str, queue: str, expected: int) -> bool:
    return await queue_depth(url, queue) == expected


async def test_a_redrive_reaches_only_provisionings_own_queue(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    """Fix round 1: a parked "source published" event re-driven from
    ``provisioning.source_published.v1.dlq`` goes back to ``provisioning.source_published.v1``
    only, never through ``project.events`` to another service's queue."""
    _, url = vhost
    await declare_producers(url)
    _, build = processes(url)
    names = SOURCE_PUBLISHED_NAMES
    other = f"other.{secrets.token_hex(4)}"
    async with running(build):
        connection = await aio_pika.connect(url)
        try:
            channel = await connection.channel()
            theirs = await channel.declare_queue(other, durable=True)
            await theirs.bind(PROJECT_EVENTS, names.routing_keys[0])
            exchange = await channel.get_exchange(PROJECT_EVENTS)
            await exchange.publish(
                aio_pika.Message(b"{}", message_id="m-redrive"), names.routing_keys[0]
            )
            # Provisioning's consumer refuses it: the broker parks it in the .dlq.
            ours = await channel.get_queue(names.queue)
            got = await ours.get(timeout=5)
            await got.reject(requeue=False)
            # The other service consumed its copy.
            copy = await theirs.get(timeout=5)
            await copy.ack()
        finally:
            await connection.close()
        await wait_for(lambda: _depth(url, names.dead_letter_queue, 1))
        tool = DeadLetterTool(
            PLATFORM_DEAD_LETTER_TOPOLOGIES,
            amqp_sessions(
                url,
                dial_timeout=timedelta(seconds=5),
                call_timeout=timedelta(seconds=5),
                connection_name="test-v1-redrive",
            ),
        )
        await tool.redrive(names.dead_letter_queue, "m-redrive", scan_limit=5)
        await wait_for(lambda: _depth(url, names.queue, 1))
    assert await queue_depth(url, other) == 0
    assert await queue_depth(url, names.dead_letter_queue) == 0


class _FailingIntake:
    """A hosting intake whose call always fails while the database answered (a handler error):
    every call is a counted failure."""

    def __init__(self) -> None:
        self.calls = 0

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        self.calls += 1
        raise RuntimeError("the handler failed")

    async def retry(self, project_id: UUID) -> RetryResult:
        raise AssertionError(f"no retry expected for {project_id}")


async def test_a_message_that_always_fails_lands_in_its_dlq_after_five_failures(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    """Fix round 1: RabbitMQ 4 does not count a nack with requeue, so the consumer counts the
    message's failures itself and dead-letters it at the fifth; it is not delivered again.

    FR-32.1."""
    _, url = vhost
    await declare_producers(url)
    worker, _ = processes(url)
    names = PROJECT_DELETED_NAMES
    intake = _FailingIntake()
    ms = timedelta(milliseconds=1)
    consumer = ProjectDeletionConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=ms, max=5 * ms), store_failure_limit=2
        ),
        decoder=EventCodecV1(max_body_bytes=4096),
        commands=intake_bus(hosting=intake),
        retries=intake,
        audit=NullAuditLog(),
        clock=SystemClock(),
        logger=logger("v1-budget"),
        retry_backoff=Backoff(base=ms, max=ms),
        max_retries=3,
    )
    message = EventCodecV1(max_body_bytes=4096).encode_project_deleted(
        message_id=UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"),
        project_id=UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01"),
        occurred_at=AT,
    )
    async with running(worker) as client:
        subscription = client.subscribe(names.queue, prefetch=1)
        consuming = asyncio.create_task(_together(subscription.run(), consumer.run(subscription)))
        try:
            connection = await aio_pika.connect(url)
            try:
                channel = await connection.channel()
                exchange = await channel.get_exchange(PROJECT_EVENTS)
                await exchange.publish(
                    aio_pika.Message(
                        message.body,
                        content_type=message.content_type,
                        type=message.message_type,
                        message_id=message.message_id,
                    ),
                    names.routing_keys[0],
                )
            finally:
                await connection.close()
            await wait_for(lambda: _depth(url, names.dead_letter_queue, 1), within_seconds=30)
            await asyncio.sleep(0.5)  # nothing more arrives
            assert intake.calls == 5
            assert await queue_depth(url, names.queue) == 0
        finally:
            consuming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consuming


class _OutageIntake:
    """A hosting intake that raises ``error`` for ``outage`` calls, then accepts."""

    def __init__(self, outage: int, error: Exception) -> None:
        self.outage = outage
        self.error = error
        self.calls = 0
        self.accepted: list[str] = []

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        self.calls += 1
        if self.calls <= self.outage:
            raise self.error
        self.accepted.append(event_key)
        command = new_command(
            command_id=UUID(int=1),
            request=CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            ),
            requested_by_subject=None,
            command_number=1,
            now=datetime.now(UTC),
        )
        return AcceptedCommand(command=command, replayed=False)

    async def retry(self, project_id: UUID) -> RetryResult:
        raise AssertionError(f"no retry expected for {project_id}")


class _CountingStream:
    """The subscription, recording the broker's ``x-delivery-count`` of each delivery."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.delivery_counts: list[int] = []

    async def next_delivery(self) -> Any:
        delivery = await self._inner.next_delivery()
        if delivery is not None:
            headers = delivery._message.headers or {}  # the broker's own count
            self.delivery_counts.append(int(headers.get("x-delivery-count", 0)))
        return delivery

    def restart(self) -> None:
        self._inner.restart()


async def _run_failing_intake(
    url: str, intake: _OutageIntake, message_id: str, done: Callable[[], Awaitable[bool]]
) -> tuple[Any, _CountingStream]:
    """Run the "project deleted" consumer (``store_failure_limit`` 2) on one published event until
    ``done``, then half a second more; the subscription and the stream it read."""
    worker, _ = processes(url)
    names = PROJECT_DELETED_NAMES
    ms = timedelta(milliseconds=1)
    consumer = ProjectDeletionConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=ms, max=5 * ms), store_failure_limit=2
        ),
        decoder=EventCodecV1(max_body_bytes=4096),
        commands=intake_bus(hosting=intake),
        retries=intake,
        audit=NullAuditLog(),
        clock=SystemClock(),
        logger=logger("v1-outage"),
        retry_backoff=Backoff(base=ms, max=ms),
        max_retries=3,
    )
    message = EventCodecV1(max_body_bytes=4096).encode_project_deleted(
        message_id=UUID(message_id),
        project_id=UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02"),
        occurred_at=AT,
    )
    async with running(worker) as client:
        subscription = client.subscribe(names.queue, prefetch=1)
        stream = _CountingStream(subscription)
        consuming = asyncio.create_task(_together(subscription.run(), consumer.run(stream)))
        try:
            connection = await aio_pika.connect(url)
            try:
                channel = await connection.channel()
                exchange = await channel.get_exchange(PROJECT_EVENTS)
                await exchange.publish(
                    aio_pika.Message(
                        message.body,
                        content_type=message.content_type,
                        type=message.message_type,
                        message_id=message.message_id,
                    ),
                    names.routing_keys[0],
                )
            finally:
                await connection.close()
            await wait_for(done, within_seconds=30)
            await asyncio.sleep(0.5)  # nothing more arrives
        finally:
            consuming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consuming
    return subscription, stream


async def test_a_database_outage_parks_the_message_after_five(
    broker: Broker, vhost: tuple[str, str]
) -> None:
    """FR-32.1: Decision 0023 G15 (owner; it replaces Phase B's rule that an outage never parks a
    message): 12 calls would find the database unreachable. Each failure is held, requeued and
    counted, so the fifth parks the message in its ``.dlq`` for an operator to re-drive. The
    outage never restarts the stream (``store_failure_limit`` is 2), so the broker's own delivery
    count never reaches its limit."""
    _, url = vhost
    await declare_producers(url)
    names = PROJECT_DELETED_NAMES
    intake = _OutageIntake(
        outage=12, error=StoreUnreachable("postgres: accept: database connection failed")
    )
    subscription, stream = await _run_failing_intake(
        url,
        intake,
        "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eaa",
        lambda: _depth(url, names.dead_letter_queue, 1),
    )
    assert intake.calls == MAX_COUNTED_FAILURES
    assert intake.accepted == []
    assert subscription.subscriptions == 1  # never restarted
    assert len(stream.delivery_counts) == MAX_COUNTED_FAILURES
    assert max(stream.delivery_counts) < DELIVERY_LIMIT
    assert await queue_depth(url, names.queue) == 0
    assert await queue_depth(url, names.dead_letter_queue) == 1


async def test_contention_does_not_count(broker: Broker, vhost: tuple[str, str]) -> None:
    """Contention is a wait, not a failure: 12 contended calls, then the event is accepted once;
    nothing reaches the ``.dlq`` and the stream never restarts."""
    _, url = vhost
    await declare_producers(url)
    names = PROJECT_DELETED_NAMES
    intake = _OutageIntake(outage=12, error=Contention("postgres: accept: lock timeout"))

    async def processed() -> bool:
        return len(intake.accepted) == 1

    subscription, stream = await _run_failing_intake(
        url, intake, "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eab", processed
    )
    assert intake.calls == 13
    assert intake.accepted == ["project-deleted"]
    assert subscription.subscriptions == 1  # never restarted
    assert len(stream.delivery_counts) == 13
    assert max(stream.delivery_counts) < DELIVERY_LIMIT
    assert await queue_depth(url, names.queue) == 0
    assert await queue_depth(url, names.dead_letter_queue) == 0


async def _together(*coros: Coroutine[Any, Any, None]) -> None:
    async with asyncio.TaskGroup() as group:
        for coro in coros:
            group.create_task(coro)
