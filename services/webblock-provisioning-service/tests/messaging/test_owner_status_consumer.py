"""Project's "owner status changed" event over a real broker (decision 0021 D9, decision 0022
Task 5): the worker's owner-status consumer on ``provisioning.owner_status_changed.v1`` hands the
event to the owner-status intake (the real use case, an in-memory store here).

- the same event delivered twice gives one command;
- a store error is redelivered and then succeeds (the event comes from the simulator);
- a store that keeps failing: the fifth counted failure of the message lands it in
  ``provisioning.owner_status_changed.v1.dlq`` (lead ruling: five counted failures of the
  consumer, the per-message bound, not broker returns).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    wait_for,
)
from tests.unit._buses import intake_bus

from provisioning.core.application import AcceptedCommand, SubdomainTaken
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.hosting.commands.accept_owner_status import (
    AcceptOwnerStatusHandler,
    owner_status_kind,
)
from provisioning.core.application.hosting.ports import (
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
)
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import CommandKind, CommandRequest, new_command, owner_status_key
from provisioning.hosts.shared.runtime import SystemClock, UUIDv7
from provisioning.infrastructure.rabbitmq import OWNER_STATUS_NAMES, BrokerClient, QueueTopology
from provisioning.presentation.messaging import EventCodecV1
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import OwnerStatusConsumer

pytestmark = pytest.mark.asyncio(loop_scope="function")

MS = timedelta(milliseconds=1)
PROJECT = UUID("0199c4a1-0000-7000-8000-00000000ab01")
NAMES = OWNER_STATUS_NAMES
REPO_ROOT = Path(__file__).resolve().parents[2]


class MemOwnerStatusStore:
    """``OwnerStatusStore`` in memory: one command per key, a replay after; raises the next of
    ``errors`` first (a failing store)."""

    def __init__(self) -> None:
        self.commands: dict[str, AcceptedCommand] = {}
        self.calls = 0
        self.errors: list[Exception] = []
        self.always: Exception | None = None

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
        if self.always is not None:
            raise self.always
        if self.errors:
            raise self.errors.pop(0)
        key = owner_status_key(version)
        stored = self.commands.get(key)
        if stored is not None:
            return OwnerStatusResult(
                OwnerStatusOutcome.REPLAYED, dataclasses.replace(stored, replayed=True)
            )
        command = new_command(
            command_id=command_id,
            request=CommandRequest(
                project_id=project_id,
                kind=owner_status_kind(status),
                release_id=None,
                idempotency_key=key,
            ),
            requested_by_subject=None,
            command_number=len(self.commands) + 1,
            now=now,
        )
        self.commands[key] = AcceptedCommand(command=command, replayed=False)
        return OwnerStatusResult(OwnerStatusOutcome.ACCEPTED, self.commands[key])


class NoRetries:
    async def retry(self, project_id: UUID) -> RetryResult:
        raise AssertionError(f"no hosting retry expected for {project_id}")


async def simulate(broker: Broker, *args: str) -> tuple[int, str, str]:
    """Run ``python -m provisioning.hosts.devtools.simulate`` against ``broker``:
    (status, out, err)."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "provisioning.hosts.devtools.simulate",
        *args,
        cwd=REPO_ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "WEBBLOCK_SIMULATOR_AMQP_URL": broker.url,
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await process.communicate()
    assert process.returncode is not None
    return process.returncode, stdout.decode(), stderr.decode()


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _purge(url: str) -> None:
    """Empty the queue and its dead-letter queue (they outlive a test on the session broker)."""
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        for name in (NAMES.queue, NAMES.dead_letter_queue):
            queue = await channel.declare_queue(name, passive=True)
            await queue.purge()
    finally:
        await connection.close()


@asynccontextmanager
async def consuming(broker: Broker, store: MemOwnerStatusStore) -> AsyncIterator[BrokerClient]:
    """The worker's owner-status consumer on the real queue, running for the block."""
    await declare_producers(broker.url)
    topology = QueueTopology(names=NAMES, limits=limits(delivery_limit=5))
    client = BrokerClient(broker_config(broker.url, (topology,)), logger=logger("owner"))
    clock = SystemClock()
    consumer = OwnerStatusConsumer(
        EventConsumerConfig(
            concurrency=2, store_hold=Backoff(base=10 * MS, max=50 * MS), store_failure_limit=5
        ),
        decoder=EventCodecV1(max_body_bytes=4096),
        commands=intake_bus(
            owner_status_handler=AcceptOwnerStatusHandler(
                store=store,
                clock=clock,
                ids=UUIDv7(),
                command_deadline=timedelta(hours=1),
                audit=NullAuditLog(),
                logger=logging.getLogger("tests.messaging.owner-intake"),
            )
        ),
        retries=NoRetries(),
        audit=NullAuditLog(),
        clock=clock,
        logger=logger("owner-consumer"),
        retry_backoff=Backoff(base=10 * MS, max=50 * MS),
        max_retries=5,
    )
    async with running(client):
        await _purge(broker.url)
        subscription = client.subscribe(NAMES.queue, prefetch=2)

        async def both() -> None:
            async with asyncio.TaskGroup() as group:
                group.create_task(subscription.run())
                group.create_task(consumer.run(subscription))

        task = asyncio.create_task(both())
        try:
            yield client
        finally:
            await stop(task)


async def _publish(client: BrokerClient, version: int, *, times: int) -> None:
    """Publish one owner-status message ``times`` times (the same message ID each time)."""
    message = EventCodecV1(max_body_bytes=4096).encode_owner_status_changed(
        message_id=uuid.uuid7(),
        project_id=PROJECT,
        status=OwnerStatus.SUSPENDED,
        status_version=version,
        subdomain="sunny-bakery",
        occurred_at=datetime.now(UTC),
    )
    for _ in range(times):
        await client.publish(
            exchange=NAMES.exchange,
            routing_key=NAMES.routing_keys[0],
            body=message.body,
            content_type=message.content_type,
            message_type=message.message_type,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
            timestamp=message.timestamp,
        )


async def _settled(broker: Broker) -> bool:
    return await queue_depth(broker.url, NAMES.queue) == 0


async def test_the_same_event_delivered_twice_gives_one_command(broker: Broker) -> None:
    store = MemOwnerStatusStore()
    async with consuming(broker, store) as client:
        await _publish(client, 1, times=2)
        await wait_for(lambda: store.calls == 2)
        await wait_for(lambda: _settled(broker))
    assert list(store.commands) == ["owner-status:1"]
    assert store.commands["owner-status:1"].command.kind is CommandKind.SUSPEND_HOSTING
    assert await queue_depth(broker.url, NAMES.dead_letter_queue) == 0


async def test_a_store_error_is_redelivered_then_the_simulated_event_succeeds(
    broker: Broker,
) -> None:
    store = MemOwnerStatusStore()
    store.errors = [ConnectionError("db")]
    async with consuming(broker, store):
        status, out, err = await simulate(
            broker,
            "owner-status",
            "--project",
            str(PROJECT),
            "--status",
            "active",
            "--version",
            "2",
            "--subdomain",
            "sunny-bakery",
        )
        assert status == 0, err
        assert out.strip() == "event_key=owner-status:2"
        await wait_for(lambda: len(store.commands) == 1)
        await wait_for(lambda: _settled(broker))
    assert store.calls == 2
    assert store.commands["owner-status:2"].command.kind is CommandKind.RESUME_HOSTING
    assert await queue_depth(broker.url, NAMES.dead_letter_queue) == 0


async def test_five_failures_land_the_event_in_its_dead_letter_queue(broker: Broker) -> None:
    """Failures while the database answered count; a database outage would not (decision 0022,
    ``test_topology_v1.test_a_database_outage_never_dead_letters_a_message``)."""
    store = MemOwnerStatusStore()
    store.always = RuntimeError("the handler failed")
    async with consuming(broker, store) as client:
        await _publish(client, 3, times=1)
        await wait_for(lambda: _depth_one(broker, NAMES.dead_letter_queue), within_seconds=30)
        await wait_for(lambda: _settled(broker))
    assert store.calls == 5
    assert store.commands == {}


async def _depth_one(broker: Broker, queue: str) -> bool:
    return await queue_depth(broker.url, queue) == 1


async def test_s2_a_taken_subdomain_goes_to_the_dlq_on_its_first_delivery(broker: Broker) -> None:
    """FR-54.1 (TR-53; Task 12 security review S2): a taken
    sub-domain is permanent, so the message is dead-lettered at once, never retried."""
    store = MemOwnerStatusStore()
    store.always = SubdomainTaken("the sub-domain belongs to another project's hosting row")
    async with consuming(broker, store) as client:
        await _publish(client, 7, times=1)
        await wait_for(lambda: store.calls == 1)
        await wait_for(lambda: _settled(broker))
        await wait_for(lambda: _depth_one(broker, NAMES.dead_letter_queue))
    assert store.calls == 1
    assert store.commands == {}
