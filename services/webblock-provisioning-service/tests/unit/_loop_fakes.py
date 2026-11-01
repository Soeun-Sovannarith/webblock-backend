"""In-memory fakes shared by the worker loop tests.

Every fake records the calls it receives in order, so a test asserts observable effects (what was
written, how each delivery was settled) and their order, not how the loop is built.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from provisioning.core.application import (
    Claim,
    ClaimOutcome,
    ClaimRequest,
    ClaimResult,
    CommandFinish,
    DeployOutcome,
    DueDispatch,
    Fence,
    WorkRef,
)
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import CommandKind
from provisioning.presentation.messaging.consumers.messages import Delivery

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PROJECT = UUID("0199c4a1-0000-7000-8000-000000000001")
RELEASE = UUID("0199c4a1-0000-7000-8000-000000000002")
COMMAND = UUID("0199c4a1-0000-7000-8000-000000000003")
BODY_MARKER = "body-marker-never-logged"

MS = timedelta(milliseconds=1)


def backoff(base_ms: float = 1, max_ms: float = 2) -> Backoff:
    return Backoff(base=base_ms * MS, max=max_ms * MS)


def ref(kind: CommandKind = CommandKind.DEPLOY, command_id: UUID = COMMAND) -> WorkRef:
    return WorkRef(
        command_id=command_id,
        project_id=PROJECT,
        kind=kind,
        command_number=7,
        release_id=None if kind.is_hosting() else RELEASE,
    )


def claim_of(work: WorkRef, *, number: int = 3, attempt: int = 1) -> Claim:
    return Claim(
        ref=work,
        claimed_by_worker="worker-1",
        claim_number=number,
        execution_attempt=attempt,
        expires_at=NOW + timedelta(seconds=30),
        deadline_at=NOW + timedelta(hours=1),
    )


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequentialIds:
    def __init__(self) -> None:
        self._n = itertools.count(1)
        self.fail = False

    def new_id(self) -> UUID:
        if self.fail:
            raise RuntimeError("id source down")
        return UUID(int=(0x0199C4A1 << 96) | next(self._n))


class FakeDelivery:
    """A delivery that records how it was settled; ``settled`` is set on the first action."""

    def __init__(
        self,
        body: bytes = b"{}",
        *,
        content_type: str | None = "application/json",
        message_type: str | None = "provisioning.command_job",
        message_id: str | None = None,
        redelivered: bool = False,
        fail_settle: bool = False,
    ) -> None:
        self._body = body
        self._content_type = content_type
        self._message_type = message_type
        self._message_id = message_id
        self._redelivered = redelivered
        self.actions: list[str] = []
        self.spans: list[str] = []
        self.settled = asyncio.Event()
        self.fail_settle = fail_settle

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def content_type(self) -> str | None:
        return self._content_type

    @property
    def message_type(self) -> str | None:
        return self._message_type

    @property
    def message_id(self) -> str | None:
        return self._message_id

    @property
    def redelivered(self) -> bool:
        return self._redelivered

    def traced(self) -> contextlib.AbstractContextManager[None]:
        """Records the entry and exit of the consumer span in ``spans``, and each settlement made
        inside it as ``settled``."""
        return self._traced()

    @contextlib.contextmanager
    def _traced(self) -> Iterator[None]:
        self.spans.append("start")
        try:
            yield
        finally:
            self.spans.append("end")

    async def _record(self, action: str) -> None:
        self.actions.append(action)
        if self.spans and self.spans[-1] == "start":
            self.spans.append("settled")
        self.settled.set()
        if self.fail_settle:
            raise ConnectionError("channel closed")

    async def ack(self) -> None:
        await self._record("ack")

    async def dead_letter(self) -> None:
        await self._record("dead_letter")

    async def requeue(self) -> None:
        await self._record("requeue")


class FakeStream:
    """A stream fed by the test; ``close`` makes ``next_delivery`` return None for every waiter."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Delivery | None] = asyncio.Queue(maxsize=100)
        self.restarts = 0
        self._closed = False

    def put(self, delivery: Delivery) -> None:
        self._queue.put_nowait(delivery)

    def close(self) -> None:
        self._closed = True
        self._queue.put_nowait(None)

    async def next_delivery(self) -> Delivery | None:
        if self._closed and self._queue.empty():
            return None
        got = await self._queue.get()
        if got is None:
            self._queue.put_nowait(None)  # wake the next waiter too
        return got

    def restart(self) -> None:
        self.restarts += 1


@dataclass
class FakeJobStore:
    """The job store port. ``claims`` is consumed in order (an exception is raised); when it is
    empty, a claim of a command already claimed answers HELD, and after ``finished`` FINISHED."""

    claims: list[ClaimResult | Exception] = field(default_factory=list)
    calls: list[tuple[str, object]] = field(default_factory=list)
    takes: list[tuple[int, timedelta]] = field(default_factory=list)
    acks: list[tuple[UUID, timedelta]] = field(default_factory=list)
    publish_failures: list[tuple[UUID, timedelta]] = field(default_factory=list)
    requests: list[ClaimRequest] = field(default_factory=list)
    extends: list[tuple[int, timedelta]] = field(default_factory=list)
    releases: list[tuple[int, timedelta, str]] = field(default_factory=list)
    due: list[list[DueDispatch] | Exception] = field(default_factory=list)
    extend: Callable[[Claim], Awaitable[Claim]] | None = None
    release_error: Exception | None = None
    ack_error: Exception | None = None
    failure_error: Exception | None = None
    orphans: int | Exception = 0
    claimed_commands: set[UUID] = field(default_factory=set)
    finished: set[UUID] = field(default_factory=set)

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]:
        self.calls.append(("take_due", (limit, visibility)))
        self.takes.append((limit, visibility))
        if not self.due:
            return []
        got = self.due.pop(0)
        if isinstance(got, Exception):
            raise got
        return got

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        self.calls.append(("record_broker_ack", (due.ref.command_id, redeliver_after)))
        self.acks.append((due.ref.command_id, redeliver_after))
        if self.ack_error is not None:
            raise self.ack_error
        return True

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool:
        self.calls.append(("record_publish_failure", (due.ref.command_id, retry_after)))
        self.publish_failures.append((due.ref.command_id, retry_after))
        if self.failure_error is not None:
            raise self.failure_error
        return True

    async def count_unfinished_without_dispatch(self) -> int:
        if isinstance(self.orphans, Exception):
            raise self.orphans
        return self.orphans

    async def claim(self, request: ClaimRequest) -> ClaimResult:
        self.calls.append(("claim", request))
        self.requests.append(request)
        if self.claims:
            got = self.claims.pop(0)
            if isinstance(got, Exception):
                raise got
            if got.claim is not None:
                self.claimed_commands.add(got.claim.ref.command_id)
            return got
        command = request.ref.command_id
        if command in self.finished:
            return ClaimResult(outcome=ClaimOutcome.FINISHED)
        if command in self.claimed_commands:
            return ClaimResult(outcome=ClaimOutcome.HELD)
        self.claimed_commands.add(command)
        return ClaimResult(outcome=ClaimOutcome.CLAIMED, claim=claim_of(request.ref))

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim:
        self.calls.append(("extend_claim", (claim.claim_number, ttl)))
        self.extends.append((claim.claim_number, ttl))
        if self.extend is not None:
            return await self.extend(claim)
        return claim

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        self.calls.append(("release_claim", (claim.claim_number, retry_after, failure_code)))
        self.releases.append((claim.claim_number, retry_after, failure_code))
        if self.release_error is not None:
            raise self.release_error

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@dataclass
class FakeWorkerStore:
    calls: list[tuple[str, object]] = field(default_factory=list)
    finishes: list[tuple[Fence, CommandFinish]] = field(default_factory=list)
    unclaimed: list[tuple[Fence, str, datetime]] = field(default_factory=list)
    finish_error: Exception | None = None
    unclaimed_error: Exception | None = None
    jobs: FakeJobStore | None = None

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None:
        self.calls.append(("finish_command", (fence, finish)))
        self.finishes.append((fence, finish))
        if self.finish_error is not None:
            raise self.finish_error
        if self.jobs is not None:
            self.jobs.finished.add(fence.command_id)

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        self.calls.append(("fail_unclaimed_command", (fence, failure_code, at)))
        self.unclaimed.append((fence, failure_code, at))
        if self.unclaimed_error is not None:
            raise self.unclaimed_error

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None:
        raise AssertionError("the loops never apply a deploy outcome")


class RecordingHandler(logging.Handler):
    """Collects formatted log lines, to assert what is never logged."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.getMessage()}")

    def text(self) -> str:
        return "\n".join(self.lines)


def logger_with_handler(name: str) -> tuple[logging.Logger, RecordingHandler]:
    logger = logging.getLogger(f"tests.workers.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for old in list(logger.handlers):
        logger.removeHandler(old)
    handler = RecordingHandler()
    logger.addHandler(handler)
    return logger, handler


async def eventually(condition: Callable[[], bool], within_seconds: float = 2.0) -> None:
    """Poll ``condition`` until it holds or ``within_seconds`` pass (then fail)."""
    async with asyncio.timeout(within_seconds):
        while not condition():  # noqa: ASYNC110 - polls state of fakes that expose no event
            await asyncio.sleep(0.001)
