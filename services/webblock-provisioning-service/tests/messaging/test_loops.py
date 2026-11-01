"""The worker loops over a real broker with an in-memory store (decision 0005 section 8, decision
0014 section 4): a command job is published by the dispatcher and executed once by the consumer even
when it is published twice; a poison message is dead-lettered; a crashed consumer's delivery is
redelivered and executed once; the developer tool's events reach the inbox and Payment consumers."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

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
from tests.messaging._memstore import MemStore
from tests.unit._buses import intake_bus

from provisioning.core.application import (
    AcceptedCommand,
    Claim,
    CommandFinish,
    EnabledKinds,
    IntakeOutcome,
    OperationInProgress,
    WorkRef,
)
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.jobs.background.dispatcher import Dispatcher, DispatcherConfig
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    CommandStatus,
    SourcePublishedFields,
    new_command,
)
from provisioning.hosts.shared.runtime import SystemClock
from provisioning.infrastructure.rabbitmq import (
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
    SOURCE_PUBLISHED_NAMES,
    BrokerClient,
    QueueNames,
    QueueTopology,
)
from provisioning.presentation.messaging import (
    COMMAND_JOB_MESSAGE_TYPE,
    CommandJobCodec,
    CommandJobPublisher,
    EventCodecV1,
)
from provisioning.presentation.messaging.consumers.consumer import CommandConsumer, WorkerConfig
from provisioning.presentation.messaging.consumers.inbox import SourcePublishedConsumer
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import PaymentEventConsumer

pytestmark = pytest.mark.asyncio(loop_scope="function")

MS = timedelta(milliseconds=1)
PROJECT = UUID("0199c4a1-0000-7000-8000-00000000aa01")
RELEASE = UUID("0199c4a1-0000-7000-8000-00000000aa02")
REPO_ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> UUID:
        self.n += 1
        return UUID(int=(0x0199C4A1 << 96) | self.n)


class CountingExecutor:
    def __init__(self, store: MemStore, *, crash_first: bool = False) -> None:
        self.store = store
        self.runs: list[UUID] = []
        self.crash_first = crash_first

    async def execute(self, claim: Claim) -> None:
        self.runs.append(claim.ref.command_id)
        if self.crash_first and len(self.runs) == 1:
            await asyncio.sleep(3600)  # the worker is stopped while it runs
        await self.store.finish_command(
            claim.fence(),
            CommandFinish(status=CommandStatus.SUCCEEDED, failure_code="", at=datetime.now(UTC)),
        )


def worker_config() -> WorkerConfig:
    return WorkerConfig(
        claimed_by_worker="worker-it",
        concurrency=2,
        claim_ttl=timedelta(seconds=3),
        heartbeat_interval=timedelta(seconds=1),
        attempt_timeout=timedelta(seconds=10),
        broker_consumer_timeout=timedelta(minutes=5),
        max_attempts=5,
        retry_backoff=Backoff(base=10 * MS, max=50 * MS),
        store_hold=Backoff(base=10 * MS, max=50 * MS),
        store_failure_limit=5,
    )


def dispatcher_config() -> DispatcherConfig:
    return DispatcherConfig(
        poll_interval=timedelta(hours=1),  # polled by hand
        batch_size=10,
        publish_concurrency=4,
        confirm_timeout=timedelta(seconds=5),
        visibility_timeout=timedelta(seconds=30),
        redelivery_interval=timedelta(minutes=5),
        backoff=Backoff(base=10 * MS, max=50 * MS),
    )


def work(n: int = 1) -> WorkRef:
    return WorkRef(
        command_id=UUID(int=(0x0199C4A1 << 96) | (0xC000 + n)),
        project_id=PROJECT,
        kind=CommandKind.DEPLOY,
        command_number=n,
        release_id=RELEASE,
    )


class Stack:
    """A dispatcher and a command consumer on one client and one unique topology."""

    def __init__(self, broker: Broker, names: QueueNames, *, crash_first: bool = False) -> None:
        self.names = names
        self.topology = QueueTopology(names=names, limits=limits())
        self.client = BrokerClient(broker_config(broker.url, (self.topology,)), logger=logger("s"))
        self.store = MemStore()
        self.executor = CountingExecutor(self.store, crash_first=crash_first)
        codec = CommandJobCodec(max_body_bytes=4096)
        self.dispatcher = Dispatcher(
            dispatcher_config(),
            jobs=self.store,
            worker=self.store,
            publisher=CommandJobPublisher(
                codec=codec,
                raw=self.client,
                exchange=names.exchange,
                routing_key=names.routing_keys[0],
            ),
            ids=Ids(),
            clock=Clock(),
            logger=logger("dispatcher"),
        )
        self.consumer = CommandConsumer(
            worker_config(),
            jobs=self.store,
            worker=self.store,
            executors={CommandKind.DEPLOY: self.executor},
            kinds=EnabledKinds([CommandKind.DEPLOY]),
            decoder=codec,
            clock=Clock(),
            logger=logger("consumer"),
        )

    def consume(self) -> asyncio.Task[None]:
        subscription = self.client.subscribe(self.names.queue, prefetch=2)
        return asyncio.create_task(_both(subscription.run(), self.consumer.run(subscription)))


async def _both(*coros: Coroutine[Any, Any, None]) -> None:
    async with asyncio.TaskGroup() as group:
        for coro in coros:
            group.create_task(coro)


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_a_job_published_twice_is_executed_once(broker: Broker) -> None:
    stack = Stack(broker, unique_names("loop"))
    async with running(stack.client):
        stack.store.add(work(1), "corr-1")
        await stack.dispatcher.poll()
        stack.store.rows[str(work(1).command_id)].due = True  # the visibility timeout passed
        await stack.dispatcher.poll()
        assert stack.store.rows[str(work(1).command_id)].acks == 2
        assert await queue_depth(broker.url, stack.names.queue) == 2
        consuming = stack.consume()
        try:
            await wait_for(lambda: stack.store.rows[str(work(1).command_id)].finished)
            await wait_for(_empty(broker, stack.names.queue))
        finally:
            await stop(consuming)
    assert stack.executor.runs == [work(1).command_id]


async def test_a_poison_message_is_dead_lettered(broker: Broker) -> None:
    stack = Stack(broker, unique_names("poison"))
    async with running(stack.client) as client:
        await client.publish(
            exchange=stack.names.exchange,
            routing_key=stack.names.routing_keys[0],
            body=b'{"not": "a job"}',
            content_type="application/json",
            message_type=COMMAND_JOB_MESSAGE_TYPE,
            message_id="poison-1",
            correlation_id="poison",
            timestamp=datetime.now(UTC),
        )
        consuming = stack.consume()
        try:
            await wait_for(_depth_is(broker, stack.names.dead_letter_queue, 1))
            await wait_for(_empty(broker, stack.names.queue))
        finally:
            await stop(consuming)
    assert stack.executor.runs == []


async def test_a_stopped_worker_releases_its_claim_and_another_executes_once(
    broker: Broker,
) -> None:
    names = unique_names("crash")
    first = Stack(broker, names, crash_first=True)
    async with running(first.client):
        first.store.add(work(2), "corr-2")
        await first.dispatcher.poll()
        consuming = first.consume()
        await wait_for(lambda: len(first.executor.runs) == 1)
        await stop(consuming)  # the worker stops mid-execution: release, then requeue
    row = first.store.rows[str(work(2).command_id)]
    assert not row.finished and not row.claimed
    second = Stack(broker, names)
    second.store = first.store
    second.executor = CountingExecutor(first.store)
    second.consumer = CommandConsumer(
        worker_config(),
        jobs=first.store,
        worker=first.store,
        executors={CommandKind.DEPLOY: second.executor},
        kinds=EnabledKinds([CommandKind.DEPLOY]),
        decoder=CommandJobCodec(max_body_bytes=4096),
        clock=Clock(),
        logger=logger("consumer-2"),
    )
    async with running(second.client):
        consuming = second.consume()
        try:
            await wait_for(lambda: row.finished)
            await wait_for(_empty(broker, names.queue))
        finally:
            await stop(consuming)
    assert second.executor.runs == [work(2).command_id]


def _empty(broker: Broker, queue: str) -> Callable[[], Awaitable[bool]]:
    return _depth_is(broker, queue, 0)


def _depth_is(broker: Broker, queue: str, n: int) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        return await queue_depth(broker.url, queue) == n

    return check


@dataclass(frozen=True)
class Run:
    returncode: int
    stdout: str
    stderr: str


async def run_simulator(env: dict[str, str], *args: str) -> Run:
    """Run the developer tool as ``python -m provisioning.hosts.devtools.simulate``, without
    blocking the loop the consumers run on."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "provisioning.hosts.devtools.simulate",
        *args,
        cwd=REPO_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await process.communicate()
    assert process.returncode is not None
    return Run(process.returncode, stdout.decode(), stderr.decode())


async def simulate(broker: Broker, *args: str) -> Run:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "WEBBLOCK_SIMULATOR_AMQP_URL": broker.url,
    }
    return await run_simulator(env, *args)


class Inbox:
    def __init__(self) -> None:
        self.received: list[SourcePublishedFields] = []

    async def receive(self, fields: SourcePublishedFields) -> IntakeOutcome:
        if fields in self.received:
            return IntakeOutcome.DUPLICATE
        self.received.append(fields)
        return IntakeOutcome.STORED


class Hosting:
    def __init__(self) -> None:
        self.events: list[tuple[str, UUID, CommandKind]] = []
        self.busy = 0

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        if self.busy:
            self.busy -= 1
            raise OperationInProgress()
        self.events.append((event_key, project_id, kind))
        command = new_command(
            command_id=UUID(int=len(self.events)),
            request=CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            ),
            requested_by_subject=None,
            command_number=len(self.events),
            now=datetime.now(UTC),
        )
        return AcceptedCommand(command=command, replayed=False)

    async def retry(self, project_id: UUID) -> RetryResult:
        """``HostingRetry.retry``: never asked here, because no command is replayed failed."""
        raise AssertionError(f"no hosting retry expected for {project_id}")


async def test_the_simulator_feeds_the_inbox_and_payment_consumers(broker: Broker) -> None:
    await declare_producers(broker.url)
    topologies = (
        QueueTopology(names=SOURCE_PUBLISHED_NAMES, limits=limits()),
        QueueTopology(names=ENTITLEMENT_ENDED_NAMES, limits=limits()),
        QueueTopology(names=ENTITLEMENT_RESTORED_NAMES, limits=limits()),
    )
    client = BrokerClient(broker_config(broker.url, topologies), logger=logger("sim"))
    codec = EventCodecV1(max_body_bytes=4096)
    inbox, hosting = Inbox(), Hosting()
    hosting.busy = 1  # the first entitlement event finds a deploy in flight
    woken: list[int] = []
    consumer_config = EventConsumerConfig(
        concurrency=2, store_hold=Backoff(base=10 * MS, max=50 * MS), store_failure_limit=5
    )
    sources = SourcePublishedConsumer(
        consumer_config,
        decoder=codec,
        commands=intake_bus(source=inbox),
        logger=logger("inbox"),
        on_stored=lambda: woken.append(1),
    )
    payments = PaymentEventConsumer(
        consumer_config,
        decoder=codec,
        commands=intake_bus(hosting=hosting),
        retries=hosting,
        audit=NullAuditLog(),
        clock=SystemClock(),
        logger=logger("payment"),
        retry_backoff=Backoff(base=10 * MS, max=50 * MS),
        max_retries=5,
    )
    async with running(client):
        source_sub = client.subscribe(SOURCE_PUBLISHED_NAMES.queue, prefetch=2)
        # Two Payment queues, one handler: the AMQP type picks the kind.
        ended_sub = client.subscribe(ENTITLEMENT_ENDED_NAMES.queue, prefetch=2)
        restored_sub = client.subscribe(ENTITLEMENT_RESTORED_NAMES.queue, prefetch=2)
        consuming = asyncio.create_task(
            _both(
                source_sub.run(),
                sources.run(source_sub),
                ended_sub.run(),
                payments.run(ended_sub),
                restored_sub.run(),
                payments.run(restored_sub),
            )
        )
        try:
            sha = "c" * 40
            out = await simulate(
                broker,
                "source-published",
                "--project",
                str(PROJECT),
                "--repository-id",
                "42",
                "--commit",
                sha,
                "--subdomain",
                "sunny-bakery",
            )
            assert out.returncode == 0, out.stderr
            assert out.stdout.startswith("event_key=")
            ended = await simulate(
                broker, "entitlement-ended", "--project", str(PROJECT), "--reason", "grace_expired"
            )
            assert ended.returncode == 0, ended.stderr
            key = ended.stdout.strip().removeprefix("event_key=")
            restored = await simulate(
                broker, "entitlement-restored", "--project", str(PROJECT), "--event-key", "pay-r1"
            )
            assert restored.stdout.strip() == "event_key=pay-r1"
            await wait_for(lambda: len(inbox.received) == 1 and len(hosting.events) == 2)
        finally:
            await stop(consuming)
    [fields] = inbox.received
    assert (fields.project_id, fields.repository_id, fields.git_commit_sha) == (
        str(PROJECT),
        42,
        sha,
    )
    assert out.stdout.strip() == f"event_key={fields.generation_request_id}"
    assert woken == [1]
    assert sorted(hosting.events) == sorted(
        [
            (key, PROJECT, CommandKind.SUSPEND_HOSTING),
            ("pay-r1", PROJECT, CommandKind.RESUME_HOSTING),
        ]
    )


async def test_the_simulator_refuses_bad_input_and_a_missing_url(broker: Broker) -> None:
    out = await simulate(broker, "entitlement-ended", "--project", "not-a-uuid")
    assert out.returncode == 2
    assert "project" in out.stderr
    missing_url = await run_simulator(
        {"PATH": os.environ.get("PATH", "")}, "entitlement-ended", "--project", str(PROJECT)
    )
    assert missing_url.returncode == 2
    assert "WEBBLOCK_SIMULATOR_AMQP_URL" in missing_url.stderr
