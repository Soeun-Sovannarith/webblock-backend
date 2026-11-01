"""The command-job consumer (decision 0005 section 4; review-port-arch.md H5), with in-memory fakes.

Times are real but small (milliseconds); every wait polls a condition with a deadline.
"""

import asyncio
import dataclasses
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.unit._loop_fakes import (
    BODY_MARKER,
    MS,
    NOW,
    FakeDelivery,
    FakeJobStore,
    FakeStream,
    FakeWorkerStore,
    FixedClock,
    claim_of,
    eventually,
    logger_with_handler,
    ref,
)

from provisioning.core.application import (
    FAILURE_CODE_EXECUTOR_UNAVAILABLE,
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    FAILURE_CODE_TRANSIENT,
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimResult,
    CommandFinish,
    Contention,
    EnabledKinds,
    Executor,
    PermanentFailure,
    RetryLater,
    StoreUnreachable,
    Timeout,
    WorkRef,
)
from provisioning.core.application.jobs.background.dispatcher import FAILURE_CODE_DEADLINE_EXCEEDED
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import CommandKind, CommandStatus, IllegalTransition, StaleFence
from provisioning.hosts.worker.wiring import PROTOTYPE_KINDS
from provisioning.presentation.messaging.consumers.consumer import (
    FAILURE_CODE_ATTEMPT_TIMEOUT,
    FAILURE_CODE_ATTEMPTS_EXHAUSTED,
    FAILURE_CODE_EXECUTOR_FAILED,
    FAILURE_CODE_WORKER_STOPPING,
    CommandConsumer,
    WorkerConfig,
)

pytestmark = pytest.mark.asyncio

ALL_KINDS = EnabledKinds(list(CommandKind))


def config(**changes: Any) -> WorkerConfig:
    base = WorkerConfig(
        claimed_by_worker="worker-1",
        concurrency=2,
        claim_ttl=90 * MS,
        heartbeat_interval=10 * MS,
        attempt_timeout=2000 * MS,
        broker_consumer_timeout=timedelta(seconds=60),
        max_attempts=3,
        retry_backoff=Backoff(base=10 * MS, max=40 * MS),
        store_hold=Backoff(base=1 * MS, max=2 * MS),
        store_failure_limit=3,
    )
    return dataclasses.replace(base, **changes)


class FakeDecoder:
    def __init__(self, result: WorkRef | Exception) -> None:
        self.result = result
        self.calls = 0

    def decode_command_job(
        self, *, body: bytes, content_type: str | None, message_type: str | None
    ) -> WorkRef:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class RecordingExecutor:
    """Runs ``behaviour`` for each claim; records the claims and whether it was cancelled."""

    def __init__(
        self, behaviour: Exception | float | None = None, finish: FakeWorkerStore | None = None
    ) -> None:
        self.behaviour = behaviour
        self.claims: list[Claim] = []
        self.cancelled = 0
        self.finished = 0
        self.started = asyncio.Event()
        self.finish = finish

    async def execute(self, claim: Claim) -> None:
        self.claims.append(claim)
        self.started.set()
        try:
            if isinstance(self.behaviour, float):
                await asyncio.sleep(self.behaviour)
            elif isinstance(self.behaviour, Exception):
                raise self.behaviour
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.finish is not None:
            await self.finish.finish_command(
                claim.fence(),
                CommandFinish(status=CommandStatus.SUCCEEDED, failure_code="", at=NOW),
            )
        self.finished += 1


class AbandoningExecutor(RecordingExecutor):
    def __init__(
        self,
        error: Exception | None = None,
        *,
        delay: float = 0.0,
        timeout: timedelta = 50 * MS,
        written: str | None = None,
    ) -> None:
        super().__init__()
        self.abandoned: list[tuple[int, str]] = []
        self.written = written
        self.error = error
        self.delay = delay
        self.timeout = timeout
        self.abandon_started = asyncio.Event()
        self.abandon_cancelled = 0

    @property
    def abandon_timeout(self) -> timedelta:
        return self.timeout

    async def abandon(self, claim: Claim, failure_code: str) -> str | None:
        self.abandoned.append((claim.claim_number, failure_code))
        self.abandon_started.set()
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.abandon_cancelled += 1
            raise
        if self.error is not None:
            raise self.error
        return self.written


class Built:
    def __init__(
        self,
        *,
        jobs: FakeJobStore | None = None,
        executor: RecordingExecutor | None = None,
        executors: Mapping[CommandKind, Executor] | None = None,
        kinds: EnabledKinds = ALL_KINDS,
        decoded: WorkRef | Exception | None = None,
        cfg: WorkerConfig | None = None,
        metrics: RecordingMetrics | None = None,
    ) -> None:
        self.jobs = jobs or FakeJobStore()
        self.worker = FakeWorkerStore(jobs=self.jobs)
        self.executor = executor or RecordingExecutor()
        self.decoder = FakeDecoder(ref() if decoded is None else decoded)
        self.logger, self.log = logger_with_handler("consumer")
        self.consumer = CommandConsumer(
            cfg or config(),
            jobs=self.jobs,
            worker=self.worker,
            executors=executors
            if executors is not None
            else dict.fromkeys(CommandKind, self.executor),
            kinds=kinds,
            decoder=self.decoder,
            clock=FixedClock(),
            logger=self.logger,
            metrics=metrics,
        )

    def failures(self) -> list[tuple[str, CommandStatus]]:
        return [(finish.failure_code, finish.status) for _, finish in self.worker.finishes]


def claimed(
    attempt: int = 1, *, deadline_passed: bool = False, kind: CommandKind = CommandKind.DEPLOY
) -> ClaimResult:
    return ClaimResult(
        outcome=ClaimOutcome.CLAIMED,
        claim=claim_of(ref(kind), attempt=attempt),
        deadline_passed=deadline_passed,
    )


@pytest.mark.parametrize(
    ("outcome", "action"),
    [
        (ClaimOutcome.FINISHED, "ack"),
        (ClaimOutcome.HELD, "ack"),
        (ClaimOutcome.BACKOFF, "ack"),
        (ClaimOutcome.STALE_COMMAND_NUMBER, "ack"),
        (ClaimOutcome.NO_WORK, "dead_letter"),
        (ClaimOutcome.MISMATCH, "dead_letter"),
        (ClaimOutcome.CORRUPT, "dead_letter"),
    ],
)
async def test_every_claim_outcome_maps_to_its_broker_action(
    outcome: ClaimOutcome, action: str
) -> None:
    b = Built(jobs=FakeJobStore(claims=[ClaimResult(outcome=outcome)]))
    d = FakeDelivery()
    assert await b.consumer.handle(d) is False
    assert d.actions == [action]
    assert b.executor.claims == []
    assert b.worker.calls == []


async def test_the_claim_request_carries_the_reference_owner_and_ttl() -> None:
    b = Built(decoded=ref(CommandKind.ROLLBACK))
    await b.consumer.handle(FakeDelivery())
    [request] = b.jobs.requests
    assert request.ref == ref(CommandKind.ROLLBACK)
    assert request.claimed_by_worker == "worker-1"
    assert request.ttl == 90 * MS


async def test_an_invalid_message_is_dead_lettered_without_a_claim_and_never_logged() -> None:
    b = Built(decoded=ValueError("message rejected: reason=schema_invalid size=10 sha256=ab"))
    d = FakeDelivery(BODY_MARKER.encode())
    await b.consumer.handle(d)
    assert d.actions == ["dead_letter"]
    assert b.jobs.calls == []
    assert BODY_MARKER not in b.log.text()
    assert "schema_invalid" in b.log.text()


async def test_a_successful_execution_is_acknowledged_after_the_executor_returns() -> None:
    b = Built(executor=RecordingExecutor(0.02))
    d = FakeDelivery()
    task = asyncio.create_task(b.consumer.handle(d))
    await b.executor.started.wait()
    await asyncio.sleep(0.005)
    assert d.actions == []
    await task
    assert d.actions == ["ack"]
    assert b.executor.finished == 1
    assert b.worker.calls == [] and "release_claim" not in b.jobs.names()


async def test_a_duplicate_delivery_is_executed_once() -> None:
    jobs = FakeJobStore()
    b = Built(jobs=jobs)
    b.executor.finish = b.worker
    first, second = FakeDelivery(), FakeDelivery(redelivered=True)
    await b.consumer.handle(first)
    await b.consumer.handle(second)
    assert first.actions == ["ack"] and second.actions == ["ack"]
    assert len(b.executor.claims) == 1


async def test_a_duplicate_delivered_while_the_first_runs_is_held_and_acknowledged() -> None:
    b = Built(executor=RecordingExecutor(0.05))
    first, second = FakeDelivery(), FakeDelivery(redelivered=True)
    task = asyncio.create_task(b.consumer.handle(first))
    await b.executor.started.wait()
    await b.consumer.handle(second)
    assert second.actions == ["ack"]
    await task
    assert first.actions == ["ack"]
    assert len(b.executor.claims) == 1


async def test_a_permanent_failure_fails_the_command_with_its_code() -> None:
    b = Built(executor=RecordingExecutor(PermanentFailure("candidate_not_ready")))
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == [("candidate_not_ready", CommandStatus.FAILED)]
    [(fence, _)] = b.worker.finishes
    assert fence == claim_of(ref()).fence()
    assert d.actions == ["ack"]


async def test_a_permanent_failure_with_an_invalid_code_uses_executor_failed() -> None:
    b = Built(executor=RecordingExecutor(PermanentFailure("Not A Code")))
    await b.consumer.handle(FakeDelivery())
    assert b.failures() == [(FAILURE_CODE_EXECUTOR_FAILED, CommandStatus.FAILED)]


async def test_a_transient_failure_releases_the_claim_with_backoff() -> None:
    b = Built(
        jobs=FakeJobStore(claims=[claimed(attempt=2)]),
        executor=RecordingExecutor(ConnectionError("cluster down")),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    [(number, retry, code)] = b.jobs.releases
    assert (number, code) == (3, FAILURE_CODE_TRANSIENT)
    assert 10 * MS < retry <= 20 * MS  # attempt 2: min(40ms, 20ms), jittered
    assert d.actions == ["ack"]
    assert b.worker.calls == []


@pytest.mark.parametrize("error", [ClaimLost("lost"), StaleFence("stale")])
async def test_a_fenced_off_executor_write_writes_nothing_more(error: Exception) -> None:
    b = Built(executor=RecordingExecutor(error))
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert d.actions == ["ack"]
    assert b.worker.calls == [] and "release_claim" not in b.jobs.names()


async def test_a_kind_that_is_not_enabled_fails_without_calling_the_executor() -> None:
    b = Built(
        kinds=EnabledKinds([CommandKind.DEPLOY]),
        decoded=ref(CommandKind.PROMOTE),
        jobs=FakeJobStore(claims=[claimed(kind=CommandKind.PROMOTE)]),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == [(FAILURE_CODE_OPERATION_UNAVAILABLE, CommandStatus.FAILED)]
    assert b.executor.claims == []
    assert d.actions == ["ack"]


async def test_the_worker_refuses_a_sync_domains_command_without_an_executor() -> None:
    """Custom domains are removed (decision 0014 section 23): the kind stays in the schema, but the
    worker's enabled kinds leave it out, so a sync_domains command that ever appears fails
    ``operation_unavailable`` and no executor runs."""
    assert CommandKind.SYNC_DOMAINS not in PROTOTYPE_KINDS
    b = Built(
        kinds=EnabledKinds(PROTOTYPE_KINDS),
        decoded=ref(CommandKind.SYNC_DOMAINS),
        jobs=FakeJobStore(claims=[claimed(kind=CommandKind.SYNC_DOMAINS)]),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == [(FAILURE_CODE_OPERATION_UNAVAILABLE, CommandStatus.FAILED)]
    assert b.executor.claims == []
    assert d.actions == ["ack"]


async def test_the_prototype_worker_runs_a_redeploy_command_through_its_executor() -> None:
    """Decision 0023 G12, G13: redeploy joined the prototype's kinds with its executor (it waited
    under decision 0021 D10), so a claimed redeploy reaches the executor registered for it."""
    assert CommandKind.REDEPLOY in PROTOTYPE_KINDS
    redeploy = RecordingExecutor()
    b = Built(
        kinds=EnabledKinds(PROTOTYPE_KINDS),
        executors={CommandKind.REDEPLOY: redeploy},
        decoded=ref(CommandKind.REDEPLOY),
        jobs=FakeJobStore(claims=[claimed(kind=CommandKind.REDEPLOY)]),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == []
    assert [c.ref.kind for c in redeploy.claims] == [CommandKind.REDEPLOY]
    assert d.actions == ["ack"]


async def test_no_enabled_kind_executes_nothing() -> None:
    b = Built(kinds=EnabledKinds())
    await b.consumer.handle(FakeDelivery())
    assert b.failures() == [(FAILURE_CODE_OPERATION_UNAVAILABLE, CommandStatus.FAILED)]
    assert b.executor.claims == []


async def test_an_enabled_kind_without_an_executor_fails_executor_unavailable() -> None:
    other = RecordingExecutor()
    b = Built(executors={CommandKind.PROMOTE: other})
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == [(FAILURE_CODE_EXECUTOR_UNAVAILABLE, CommandStatus.FAILED)]
    assert other.claims == []
    assert d.actions == ["ack"]


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (claimed(deadline_passed=True), FAILURE_CODE_DEADLINE_EXCEEDED),
        (claimed(attempt=4), FAILURE_CODE_ATTEMPTS_EXHAUSTED),
    ],
)
async def test_an_exhausted_deadline_or_budget_abandons_then_fails_without_executing(
    result: ClaimResult, code: str
) -> None:
    executor = AbandoningExecutor()
    b = Built(jobs=FakeJobStore(claims=[result]), executor=executor)
    await b.consumer.handle(FakeDelivery())
    assert executor.abandoned == [(3, code)]
    assert b.failures() == [(code, CommandStatus.FAILED)]
    assert executor.claims == []
    # Without the hook the command is failed all the same.
    plain = Built(jobs=FakeJobStore(claims=[result]))
    await plain.consumer.handle(FakeDelivery())
    assert plain.failures() == [(code, CommandStatus.FAILED)]


async def test_an_abandon_error_is_logged_and_the_command_failed_anyway() -> None:
    executor = AbandoningExecutor(error=ConnectionError("cluster down"))
    b = Built(jobs=FakeJobStore(claims=[claimed(deadline_passed=True)]), executor=executor)
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.failures() == [(FAILURE_CODE_DEADLINE_EXCEEDED, CommandStatus.FAILED)]
    assert d.actions == ["ack"]
    assert "abandon" in b.log.text()


async def test_tr57_the_abandon_log_names_the_code_written() -> None:
    """TR-57: the abandoner kept the failed try's code; the "abandon hook ran" line names the code
    it wrote, and the consumer still fails the command with its own code (refused when the
    outcome already ended it)."""
    executor = AbandoningExecutor(written="route_not_accepted")
    b = Built(
        jobs=FakeJobStore(claims=[claimed(deadline_passed=True, kind=CommandKind.PROMOTE)]),
        executor=executor,
    )
    await b.consumer.handle(FakeDelivery())
    line = next(text for text in b.log.lines if "abandon hook ran" in text)
    assert "failure_code=route_not_accepted" in line
    assert FAILURE_CODE_DEADLINE_EXCEEDED not in line
    assert b.failures() == [(FAILURE_CODE_DEADLINE_EXCEEDED, CommandStatus.FAILED)]


async def test_tr57_an_abandon_that_wrote_nothing_is_logged_so() -> None:
    """TR-57: an abandon that returns None wrote no failure outcome; the line says so and names no
    code."""
    executor = AbandoningExecutor(written=None)
    b = Built(jobs=FakeJobStore(claims=[claimed(attempt=4)]), executor=executor)
    await b.consumer.handle(FakeDelivery())
    line = next(text for text in b.log.lines if "abandon hook ran" in text)
    assert "no failure outcome written" in line
    assert "failure_code=" not in line
    assert b.failures() == [(FAILURE_CODE_ATTEMPTS_EXHAUSTED, CommandStatus.FAILED)]


async def test_the_heartbeat_extends_the_claim_while_the_abandon_hook_runs() -> None:
    """A route restore can outlast the claim TTL; without a heartbeat another worker would claim
    the command and run a second abandon beside the first."""
    executor = AbandoningExecutor(delay=0.06, timeout=500 * MS)
    b = Built(jobs=FakeJobStore(claims=[claimed(deadline_passed=True)]), executor=executor)
    await b.consumer.handle(FakeDelivery())
    assert len(b.jobs.extends) >= 2
    assert b.failures() == [(FAILURE_CODE_DEADLINE_EXCEEDED, CommandStatus.FAILED)]


async def test_losing_the_claim_during_the_abandon_hook_cancels_it_and_writes_nothing() -> None:
    async def lose(claim: Claim) -> Claim:
        raise ClaimLost("taken over")

    executor = AbandoningExecutor(delay=5.0, timeout=timedelta(seconds=10))
    jobs = FakeJobStore(claims=[claimed(deadline_passed=True)], extend=lose)
    b = Built(jobs=jobs, executor=executor)
    d = FakeDelivery()
    async with asyncio.timeout(2):
        await b.consumer.handle(d)
    assert executor.abandon_cancelled == 1
    assert d.actions == ["ack"]
    assert b.worker.calls == []


async def test_shutdown_during_the_abandon_hook_releases_the_claim_and_requeues() -> None:
    executor = AbandoningExecutor(delay=5.0, timeout=timedelta(seconds=10))
    b = Built(jobs=FakeJobStore(claims=[claimed(deadline_passed=True)]), executor=executor)
    d = FakeDelivery()
    task = asyncio.create_task(b.consumer.handle(d))
    await executor.abandon_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert executor.abandon_cancelled == 1
    [(number, _, code)] = b.jobs.releases
    assert (number, code) == (3, FAILURE_CODE_WORKER_STOPPING)
    assert d.actions == ["requeue"]
    assert b.worker.calls == []


async def test_an_abandon_hook_past_its_timeout_is_cancelled_and_the_command_failed() -> None:
    executor = AbandoningExecutor(delay=5.0, timeout=30 * MS)
    b = Built(jobs=FakeJobStore(claims=[claimed(deadline_passed=True)]), executor=executor)
    async with asyncio.timeout(2):
        await b.consumer.handle(FakeDelivery())
    assert executor.abandon_cancelled == 1
    assert b.failures() == [(FAILURE_CODE_DEADLINE_EXCEEDED, CommandStatus.FAILED)]
    assert "abandon hook failed" in b.log.text()


@pytest.mark.parametrize("refusal", [ClaimLost("x"), StaleFence("x"), IllegalTransition("x")])
async def test_a_refused_failure_write_is_acknowledged(refusal: Exception) -> None:
    b = Built(kinds=EnabledKinds())
    b.worker.finish_error = refusal
    d = FakeDelivery()
    assert await b.consumer.handle(d) is False
    assert d.actions == ["ack"]


async def test_a_failed_failure_write_is_held_and_requeued() -> None:
    b = Built(kinds=EnabledKinds())
    b.worker.finish_error = ConnectionError("db down")
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert d.actions == ["requeue"]


@pytest.mark.parametrize(
    ("error", "action"), [(ClaimLost("x"), "ack"), (ConnectionError("db"), "requeue")]
)
async def test_a_refused_release_is_acknowledged_and_a_failed_one_requeued(
    error: Exception, action: str
) -> None:
    jobs = FakeJobStore(release_error=error)
    b = Built(jobs=jobs, executor=RecordingExecutor(ConnectionError("transient")))
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert d.actions == [action]


async def test_a_refused_release_is_logged_as_a_taken_over_claim() -> None:
    """Task H5b, found live with two workers: a worker resumed after another took its command
    over logged only "transient execution failure; releasing the claim", and the refusal of that
    release was silent, so its log read as if it had released a claim it no longer held."""
    jobs = FakeJobStore(release_error=ClaimLost("x"))
    b = Built(jobs=jobs, executor=RecordingExecutor(ConnectionError("transient")))
    await b.consumer.handle(FakeDelivery())
    lines = [line for line in b.log.text().splitlines() if "claim release refused" in line]
    assert len(lines) == 1, b.log.text()
    assert "claim_number=3" in lines[0] and "error=ClaimLost" in lines[0]


async def test_a_failing_store_call_holds_requeues_and_restarts_after_the_limit() -> None:
    """A failure while the database answered (here a plain error) counts."""
    jobs = FakeJobStore(claims=[ConnectionError("db")] * 3)
    b = Built(jobs=jobs)
    results = []
    for _ in range(3):
        d = FakeDelivery()
        results.append(await b.consumer.handle(d))
        assert d.actions == ["requeue"]
    assert results == [False, False, True]
    # The count starts again, and a success resets it.
    jobs.claims = [
        ConnectionError("db"),
        ClaimResult(outcome=ClaimOutcome.HELD),
        ConnectionError("db"),
        ConnectionError("db"),
    ]
    assert [await b.consumer.handle(FakeDelivery()) for _ in range(4)] == [
        False,
        False,
        False,
        False,
    ]


async def test_a_job_whose_claim_keeps_failing_is_dead_lettered_at_the_fifth_failure() -> None:
    jobs = FakeJobStore(claims=[ConnectionError("db")] * 5)
    b = Built(jobs=jobs, cfg=config(store_failure_limit=100))
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        await b.consumer.handle(d)
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]


async def test_contention_never_dead_letters_a_job() -> None:
    jobs = FakeJobStore(claims=[Contention("lock timeout")] * 12)
    b = Built(jobs=jobs)
    for _ in range(12):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        await b.consumer.handle(d)
        assert d.actions == ["requeue"]


async def test_contention_is_requeued_without_counting_towards_a_restart() -> None:
    jobs = FakeJobStore(claims=[Contention("lock timeout")] * 5)
    b = Built(jobs=jobs)
    for _ in range(5):
        d = FakeDelivery()
        assert await b.consumer.handle(d) is False
        assert d.actions == ["requeue"]


async def test_the_heartbeat_extends_the_claim_at_its_interval() -> None:
    b = Built(executor=RecordingExecutor(0.06))
    await b.consumer.handle(FakeDelivery())
    extends = b.jobs.extends
    assert 2 <= len(extends) <= 12
    assert all(ttl == 90 * MS for _, ttl in extends)


async def test_losing_the_claim_cancels_the_execution_and_writes_nothing_more() -> None:
    async def lose(claim: Claim) -> Claim:
        raise ClaimLost("taken over")

    jobs = FakeJobStore(extend=lose)
    b = Built(jobs=jobs, executor=RecordingExecutor(5.0))
    d = FakeDelivery()
    async with asyncio.timeout(2):
        await b.consumer.handle(d)
    assert b.executor.cancelled == 1
    assert d.actions == ["ack"]
    assert b.worker.calls == [] and "release_claim" not in jobs.names()
    # The stop reason was recorded before the cancel: the outcome is "claim lost", never success.
    assert "claim lost during execution" in b.log.text()
    assert "command executed" not in b.log.text()


async def test_a_heartbeat_failing_for_a_whole_ttl_cancels_the_execution() -> None:
    async def fail(claim: Claim) -> Claim:
        raise ConnectionError("db down")

    jobs = FakeJobStore(extend=fail)
    b = Built(jobs=jobs, executor=RecordingExecutor(5.0))
    d = FakeDelivery()
    async with asyncio.timeout(2):
        await b.consumer.handle(d)
    assert b.executor.cancelled == 1
    assert d.actions == ["ack"]
    assert jobs.names().count("extend_claim") >= 8  # about TTL / interval tries
    assert b.worker.calls == [] and "release_claim" not in jobs.names()
    # The stop reason was recorded before the cancel: the outcome is "claim lost", never success.
    assert "claim lost during execution" in b.log.text()
    assert "command executed" not in b.log.text()


async def test_an_extension_returns_the_new_claim_for_the_next_extension() -> None:
    async def renew(claim: Claim) -> Claim:
        return dataclasses.replace(claim, expires_at=claim.expires_at + timedelta(seconds=1))

    jobs = FakeJobStore(extend=renew)
    b = Built(jobs=jobs, executor=RecordingExecutor(0.04))
    await b.consumer.handle(FakeDelivery())
    assert jobs.names().count("extend_claim") >= 2


async def test_an_attempt_timeout_releases_the_claim_with_its_code() -> None:
    b = Built(executor=RecordingExecutor(5.0), cfg=config(attempt_timeout=30 * MS))
    d = FakeDelivery()
    async with asyncio.timeout(2):
        await b.consumer.handle(d)
    assert b.executor.cancelled == 1
    [(_, _, code)] = b.jobs.releases
    assert code == FAILURE_CODE_ATTEMPT_TIMEOUT
    assert d.actions == ["ack"]


async def test_an_executor_timeout_of_its_own_is_transient_not_an_attempt_timeout() -> None:
    b = Built(executor=RecordingExecutor(TimeoutError()))
    await b.consumer.handle(FakeDelivery())
    [(_, _, code)] = b.jobs.releases
    assert code == FAILURE_CODE_TRANSIENT


async def test_shutdown_mid_execution_releases_with_worker_stopping_then_requeues() -> None:
    b = Built(executor=RecordingExecutor(5.0))
    d = FakeDelivery()
    task = asyncio.create_task(b.consumer.handle(d))
    await b.executor.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert b.executor.cancelled == 1
    [(number, retry, code)] = b.jobs.releases
    assert (number, code) == (3, FAILURE_CODE_WORKER_STOPPING)
    assert timedelta(0) < retry <= MS
    assert d.actions == ["requeue"]
    assert b.worker.calls == []


async def test_a_failed_release_while_stopping_still_requeues() -> None:
    b = Built(
        jobs=FakeJobStore(release_error=ConnectionError("db")), executor=RecordingExecutor(5.0)
    )
    d = FakeDelivery()
    task = asyncio.create_task(b.consumer.handle(d))
    await b.executor.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert d.actions == ["requeue"]


async def test_a_release_while_stopping_is_bounded_by_the_claim_ttl() -> None:
    class SlowRelease(FakeJobStore):
        async def release_claim(
            self, claim: Claim, retry_after: timedelta, failure_code: str
        ) -> None:
            await asyncio.sleep(10)

    b = Built(jobs=SlowRelease(), executor=RecordingExecutor(5.0), cfg=config(claim_ttl=30 * MS))
    d = FakeDelivery()
    task = asyncio.create_task(b.consumer.handle(d))
    await b.executor.started.wait()
    task.cancel()
    async with asyncio.timeout(1):
        with pytest.raises(asyncio.CancelledError):
            await task
    assert d.actions == ["requeue"]


async def test_settle_errors_are_logged_not_raised() -> None:
    b = Built()
    d = FakeDelivery(fail_settle=True)
    assert await b.consumer.handle(d) is False
    assert d.actions == ["ack"]
    assert "settling" in b.log.text()


async def test_run_bounds_concurrency_and_restarts_the_stream() -> None:
    in_flight = 0
    peak = 0

    class Slow(RecordingExecutor):
        async def execute(self, claim: Claim) -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1

    claims: list[ClaimResult | Exception] = [
        ClaimResult(outcome=ClaimOutcome.CLAIMED, claim=claim_of(ref(command_id=UUID(int=n))))
        for n in range(1, 7)
    ]
    jobs = FakeJobStore(claims=claims + [ConnectionError("db")] * 3)
    b = Built(jobs=jobs, executor=Slow(), cfg=config(concurrency=2))
    stream = FakeStream()
    deliveries = [FakeDelivery() for _ in range(9)]
    for d in deliveries:
        stream.put(d)
    task = asyncio.create_task(b.consumer.run(stream))
    await eventually(lambda: all(d.actions for d in deliveries))
    stream.close()
    async with asyncio.timeout(1):
        await task
    assert peak == 2
    assert stream.restarts == 1


async def test_run_returns_when_cancelled() -> None:
    b = Built()
    stream = FakeStream()
    task = asyncio.create_task(b.consumer.run(stream))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_nothing_logged_holds_the_body() -> None:
    b = Built(executor=RecordingExecutor(PermanentFailure("candidate_not_ready")))
    await b.consumer.handle(FakeDelivery(BODY_MARKER.encode()))
    assert BODY_MARKER not in b.log.text()
    assert str(ref().command_id) in b.log.text()


@pytest.mark.parametrize(
    "changes",
    [
        {"claimed_by_worker": ""},
        {"claimed_by_worker": "w" * 101},
        {"claimed_by_worker": "bad\nname"},
        {"concurrency": 0},
        {"claim_ttl": timedelta(0)},
        {"heartbeat_interval": timedelta(0)},
        {"heartbeat_interval": 31 * MS},
        {"broker_consumer_timeout": timedelta(0)},
        {"attempt_timeout": timedelta(0)},
        {"attempt_timeout": timedelta(seconds=60)},
        {"max_attempts": 0},
        {"store_failure_limit": 0},
        {"retry_backoff": Backoff(base=timedelta(0), max=MS)},
        {"store_hold": Backoff(base=2 * MS, max=MS)},
    ],
)
async def test_the_config_refuses_missing_and_inconsistent_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidConfig):
        config(**changes).validate()
    with pytest.raises(InvalidConfig):
        Built(cfg=config(**changes))


@pytest.mark.parametrize(
    ("code", "expected"),
    [("tenant_not_ready", "tenant_not_ready"), ("Not A Code", FAILURE_CODE_TRANSIENT)],
)
async def test_tr57_a_retry_later_without_a_wait_uses_the_backoff(code: str, expected: str) -> None:
    """TR-57 (G11): ``RetryLater(code)`` (a deploy's transient failure) releases the claim with its
    code after the generic retry backoff, as before."""
    b = Built(
        jobs=FakeJobStore(claims=[claimed(attempt=1)]),
        executor=RecordingExecutor(RetryLater(code)),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    [(number, retry, released_code)] = b.jobs.releases
    assert (number, released_code) == (3, expected)
    assert 5 * MS < retry <= 10 * MS  # attempt 1 of the retry backoff, jittered
    assert d.actions == ["ack"]
    assert b.worker.finishes == []


async def test_tr57_a_retry_later_with_a_wait_releases_the_claim_for_exactly_that_wait() -> None:
    """FR-21.2, TR-57 (G11): a failed go-live try gives the claim back with its code and exactly
    its wait (30 s after try 1), not the backoff; the command is not finished."""
    b = Built(
        jobs=FakeJobStore(claims=[claimed(attempt=1, kind=CommandKind.PROMOTE)]),
        executor=RecordingExecutor(RetryLater("route_refused", timedelta(seconds=30))),
        decoded=ref(CommandKind.PROMOTE),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert b.jobs.releases == [(3, timedelta(seconds=30), "route_refused")]
    assert d.actions == ["ack"]
    assert b.worker.finishes == []
    assert "retry_after=0:00:30" in b.log.text()


@pytest.mark.parametrize("after", [None, timedelta(seconds=60)], ids=["backoff", "60s"])
async def test_tr57_a_retry_later_with_an_invalid_code_releases_with_transient_failure(
    after: timedelta | None,
) -> None:
    """TR-57 (G11): a code that fails ``validate_code`` becomes ``transient_failure``; the wait is
    still the one given, or the backoff."""
    b = Built(
        jobs=FakeJobStore(claims=[claimed(attempt=1)]),
        executor=RecordingExecutor(RetryLater("Not A Code", after)),
    )
    d = FakeDelivery()
    await b.consumer.handle(d)
    [(number, retry, code)] = b.jobs.releases
    assert (number, code) == (3, FAILURE_CODE_TRANSIENT)
    if after is None:
        assert 5 * MS < retry <= 10 * MS
    else:
        assert retry == after
    assert d.actions == ["ack"]
    assert b.worker.finishes == []


class RecordingMetrics:
    """Task H4's ``CommandMetrics``: the executions measured."""

    def __init__(self, broken: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.broken = broken

    def command_executed(self, kind: str, result: str, seconds: float) -> None:
        assert seconds >= 0
        self.calls.append((kind, result))
        if self.broken:
            raise RuntimeError("metrics down")


@pytest.mark.parametrize(
    ("behaviour", "result"),
    [
        (None, "finished"),
        (PermanentFailure("candidate_not_ready"), "permanent_failure"),
        (RuntimeError("boom"), "transient_failure"),
        (ClaimLost("gone"), "fenced"),
        (StaleFence("ahead"), "fenced"),
    ],
)
async def test_each_execution_attempt_is_measured_by_kind_and_how_it_ended(
    behaviour: Exception | None, result: str
) -> None:
    metrics = RecordingMetrics()
    b = Built(
        executor=RecordingExecutor(behaviour), decoded=ref(CommandKind.PROMOTE), metrics=metrics
    )
    b.jobs.claims = [claimed(kind=CommandKind.PROMOTE)]
    await b.consumer.handle(FakeDelivery())
    assert metrics.calls == [("promote", result)]


async def test_an_attempt_timeout_and_a_shutdown_are_measured() -> None:
    metrics = RecordingMetrics()
    b = Built(executor=RecordingExecutor(5.0), cfg=config(attempt_timeout=30 * MS), metrics=metrics)
    async with asyncio.timeout(2):
        await b.consumer.handle(FakeDelivery())
    stopping = Built(executor=RecordingExecutor(5.0), metrics=metrics)
    task = asyncio.create_task(stopping.consumer.handle(FakeDelivery()))
    await stopping.executor.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert metrics.calls == [("deploy", "attempt_timeout"), ("deploy", "worker_stopping")]


async def test_a_kind_that_is_not_enabled_runs_and_measures_no_execution() -> None:
    metrics = RecordingMetrics()
    b = Built(kinds=EnabledKinds([]), metrics=metrics)
    await b.consumer.handle(FakeDelivery())
    assert metrics.calls == []


async def test_tr57_a_retry_later_with_a_wait_is_measured_try_failed() -> None:
    """TR-57 (G15): an attempt that ends in a failed try with its own wait is ``try_failed``."""
    metrics = RecordingMetrics()
    b = Built(
        executor=RecordingExecutor(RetryLater("site_not_answering", timedelta(seconds=60))),
        decoded=ref(CommandKind.PROMOTE),
        metrics=metrics,
    )
    b.jobs.claims = [claimed(kind=CommandKind.PROMOTE)]
    await b.consumer.handle(FakeDelivery())
    assert metrics.calls == [("promote", "try_failed")]


async def test_tr57_a_retry_later_without_a_wait_is_measured_transient_failure() -> None:
    """TR-57 (G15): ``RetryLater`` without a wait is measured as before, ``transient_failure``."""
    metrics = RecordingMetrics()
    b = Built(executor=RecordingExecutor(RetryLater("tenant_not_ready")), metrics=metrics)
    await b.consumer.handle(FakeDelivery())
    assert metrics.calls == [("deploy", "transient_failure")]


async def test_a_failing_measurement_changes_nothing() -> None:
    metrics = RecordingMetrics(broken=True)
    b = Built(metrics=metrics)
    d = FakeDelivery()
    await b.consumer.handle(d)
    assert d.actions == ["ack"]
    assert metrics.calls == [("deploy", "finished")]


@pytest.mark.parametrize(
    "error",
    [StoreUnreachable("db down"), Timeout("pool"), RuntimeError("wrapped")],
    ids=["unreachable", "timeout", "cause-chain"],
)
async def test_a_database_outage_counts_toward_the_bound(error: Exception) -> None:
    """Decision 0023 G15 (owner): a database that does not answer counts towards the job's 5
    tries, then the job goes to its ``.dlq``. It never restarts the stream (Phase B's fix)."""
    if isinstance(error, RuntimeError):
        error.__cause__ = StoreUnreachable("db down")
    jobs = FakeJobStore(claims=[error] * 5)
    b = Built(jobs=jobs, cfg=config(store_failure_limit=2))
    actions = []
    for _ in range(5):
        d = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
        assert await b.consumer.handle(d) is False
        actions += d.actions
    assert actions == ["requeue"] * 4 + ["dead_letter"]
    assert "dead-lettered after 5 failures" in b.log.text()
