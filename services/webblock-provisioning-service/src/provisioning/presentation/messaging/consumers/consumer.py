"""The command-job consumer (decision 0005 section 4).

A delivery is decoded, claimed through the job store, and its claim outcome mapped to a broker
action. A claimed command of an enabled kind runs the executor registered for its kind under the
claim, with the heartbeat as a sibling task; the reason for stopping an execution (claim lost,
attempt timeout) is recorded before the execution is cancelled (review-port-arch.md H5). Every
delivery is settled exactly once and only after the database call its action depends on returned.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from provisioning.core.application import (
    FAILURE_CODE_EXECUTOR_UNAVAILABLE,
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    FAILURE_CODE_TRANSIENT,
    MAX_CLAIMED_BY_WORKER_LEN,
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    Clock,
    CommandFinish,
    EnabledKinds,
    Executor,
    JobStore,
    PermanentFailure,
    RetryLater,
    WorkerStore,
    WorkRef,
)
from provisioning.core.application.jobs.background.dispatcher import FAILURE_CODE_DEADLINE_EXCEEDED
from provisioning.core.application.ports.metrics import CommandMetrics, NoMetrics
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    DomainError,
    IllegalTransition,
    StaleFence,
    validate_code,
)
from provisioning.presentation.messaging.consumers.messages import (
    Action,
    Delivery,
    DeliveryStream,
    StoreFailures,
    failure_of,
    rejected_fields,
    serve,
    settle,
)

# Failure codes the consumer writes (decision 0005 section 5), with the jobs module's
# executor_unavailable, operation_unavailable and transient_failure.
FAILURE_CODE_ATTEMPTS_EXHAUSTED = "attempts_exhausted"
FAILURE_CODE_EXECUTOR_FAILED = "executor_failed"
FAILURE_CODE_ATTEMPT_TIMEOUT = "attempt_timeout"
FAILURE_CODE_WORKER_STOPPING = "worker_stopping"

# How one execution attempt ended, for the execution metric (task H4); a fixed set.
RESULT_FINISHED = "finished"
RESULT_PERMANENT_FAILURE = "permanent_failure"
RESULT_TRANSIENT_FAILURE = "transient_failure"
RESULT_TRY_FAILED = "try_failed"  # a RetryLater with a wait of its own (a failed go-live try)
RESULT_ATTEMPT_TIMEOUT = "attempt_timeout"
RESULT_CLAIM_LOST = "claim_lost"
RESULT_FENCED = "fenced"
RESULT_WORKER_STOPPING = "worker_stopping"

# The not-before delay of a release on shutdown: the command may be claimed again at once, so the
# delay is only the store's smallest meaningful positive value.
_STOP_RETRY_AFTER = timedelta(milliseconds=1)


class CommandJobDecoder(Protocol):
    """Validates a delivered command job and parses its reference. A refusal raises an error whose
    text holds no part of the body (a reason, the size and a hash). Implemented by
    ``presentation.messaging.CommandJobCodec``."""

    def decode_command_job(
        self, *, body: bytes, content_type: str | None, message_type: str | None
    ) -> WorkRef: ...


@runtime_checkable
class Abandoner(Protocol):
    """An executor that cleans up before its command is failed for an exhausted deadline or
    attempt budget (decision 0011 section 3). Optional. ``abandon`` returns the failure code its
    outcome wrote, or None when it wrote none (decision 0027 T9)."""

    @property
    def abandon_timeout(self) -> timedelta: ...

    async def abandon(self, claim: Claim, failure_code: str) -> str | None: ...


class StopReason(StrEnum):
    """Why an execution was cancelled, recorded before ``cancel()`` (H5)."""

    CLAIM_LOST = "claim_lost"
    ATTEMPT_TIMEOUT = "attempt_timeout"


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    """Every value required (decision 0005 section 7)."""

    claimed_by_worker: str  # 1 to MAX_CLAIMED_BY_WORKER_LEN printable characters
    concurrency: int  # WORKER_CONCURRENCY; the broker prefetch equals it
    claim_ttl: timedelta  # WORKER_LEASE_TTL
    heartbeat_interval: timedelta  # WORKER_HEARTBEAT_INTERVAL, at most a third of the TTL
    attempt_timeout: timedelta  # WORKER_ATTEMPT_TIMEOUT, below the broker consumer timeout
    broker_consumer_timeout: timedelta  # RABBITMQ_CONSUMER_TIMEOUT, for the cross-check only
    max_attempts: int  # WORKER_MAX_ATTEMPTS
    retry_backoff: Backoff  # WORKER_RETRY_BACKOFF_BASE and _MAX, by execution attempt
    store_hold: Backoff  # WORKER_STORE_HOLD
    store_failure_limit: int  # WORKER_STORE_FAILURE_LIMIT

    def validate(self) -> None:
        """``InvalidConfig`` for a missing value or an inconsistency of decision 0005 section 7."""
        name = self.claimed_by_worker
        if not 1 <= len(name) <= MAX_CLAIMED_BY_WORKER_LEN or not name.isprintable():
            raise InvalidConfig(
                f"worker identity must be 1 to {MAX_CLAIMED_BY_WORKER_LEN} printable characters"
            )
        if self.concurrency < 1:
            raise InvalidConfig("worker concurrency must be positive")
        if self.claim_ttl <= timedelta(0):
            raise InvalidConfig("claim time must be positive")
        if not timedelta(0) < self.heartbeat_interval <= self.claim_ttl / 3:
            raise InvalidConfig(
                "heartbeat interval must be positive and at most a third of the TTL"
            )
        if self.broker_consumer_timeout <= timedelta(0):
            raise InvalidConfig("broker consumer timeout must be positive")
        if not timedelta(0) < self.attempt_timeout < self.broker_consumer_timeout:
            raise InvalidConfig(
                "attempt timeout must be positive and below the broker consumer timeout"
            )
        if self.max_attempts < 1:
            raise InvalidConfig("attempt budget must be positive")
        if self.store_failure_limit < 1:
            raise InvalidConfig("store failure limit must be positive")
        self.retry_backoff.validate()
        self.store_hold.validate()


def _release_of(error: Exception, stop: StopReason | None) -> tuple[str, timedelta | None]:
    """The dispatch row's last_failure_code after a transient failure, and the executor's own wait
    before the next claim (None: the generic retry backoff). ``attempt_timeout`` for an expired
    attempt; for a ``RetryLater`` its code when it is well formed, else ``transient_failure``, and
    its ``after``; for any other error ``transient_failure``."""
    if stop is StopReason.ATTEMPT_TIMEOUT:
        return FAILURE_CODE_ATTEMPT_TIMEOUT, None
    if isinstance(error, RetryLater):
        try:
            validate_code(error.code)
        except DomainError:
            return FAILURE_CODE_TRANSIENT, error.after
        return error.code, error.after
    return FAILURE_CODE_TRANSIENT, None


def _fields(work: WorkRef) -> str:
    return (
        f"command_id={work.command_id} project_id={work.project_id} kind={work.kind} "
        f"command_number={work.command_number}"
    )


def _claim_fields(claim: Claim) -> str:
    return (
        f"{_fields(claim.ref)} claim_number={claim.claim_number} attempt={claim.execution_attempt}"
    )


class _Execution:
    """The outcome of one execution: the stop reason, recorded before the cancel, and the
    executor's error."""

    __slots__ = ("error", "stop")

    def __init__(self) -> None:
        self.stop: StopReason | None = None
        self.error: Exception | None = None


class CommandConsumer:
    """Handles command-job deliveries."""

    __slots__ = (
        "_clock",
        "_config",
        "_decoder",
        "_executors",
        "_failures",
        "_jobs",
        "_kinds",
        "_logger",
        "_metrics",
        "_worker",
    )

    def __init__(
        self,
        config: WorkerConfig,
        *,
        jobs: JobStore,
        worker: WorkerStore,
        executors: Mapping[CommandKind, Executor],
        kinds: EnabledKinds,
        decoder: CommandJobDecoder,
        clock: Clock,
        logger: logging.Logger,
        metrics: CommandMetrics | None = None,
    ) -> None:
        """A kind that is enabled but has no executor fails its commands
        ``executor_unavailable``; a kind that is not enabled fails them
        ``operation_unavailable`` and no executor is called. ``metrics`` measures each execution
        attempt (task H4); it changes nothing."""
        config.validate()
        for executor in executors.values():
            if isinstance(executor, Abandoner) and executor.abandon_timeout <= timedelta(0):
                raise InvalidConfig("an executor's abandon timeout must be positive")
        self._config = config
        self._jobs = jobs
        self._worker = worker
        self._executors = dict(executors)
        self._kinds = kinds
        self._decoder = decoder
        self._clock = clock
        self._logger = logger
        self._metrics: CommandMetrics = metrics if metrics is not None else NoMetrics()
        self._failures = StoreFailures(hold=config.store_hold, limit=config.store_failure_limit)

    def executors(self) -> Mapping[CommandKind, Executor]:
        """The registered executors by kind, read only (the host's wiring is checked on it)."""
        return MappingProxyType(self._executors)

    async def run(self, stream: DeliveryStream) -> None:
        """Handle deliveries with ``concurrency`` tasks until the stream stops or the task is
        cancelled; restart the stream after repeated database failures."""
        await serve(stream, self._config.concurrency, self.handle)

    async def handle(self, delivery: Delivery) -> bool:
        """Process one delivery to completion and settle it. Returns True after
        ``store_failure_limit`` handler failures in a row (the database answered, the call
        failed), so the caller restarts the stream; a database that did not answer never does,
        though it counts towards the job's 5 tries (decision 0023 G15).
        Cancelled during an execution (the worker stopping), it releases the claim with
        ``worker_stopping`` under a shield bounded by the claim TTL, requeues the delivery and
        re-raises the cancellation."""
        try:
            work = self._decoder.decode_command_job(
                body=delivery.body,
                content_type=delivery.content_type,
                message_type=delivery.message_type,
            )
        except Exception as exc:  # the decoder's errors never carry the body (its contract)
            self._logger.warning(
                "command job dead-lettered (%s error=%s)", rejected_fields(delivery), exc
            )
            await settle(delivery, Action.DEAD_LETTER, self._logger, "")
            return False
        fields = _fields(work)
        action = await self._process(work, delivery, fields)
        if action is not None:
            await self._failures.settle(delivery, action, self._logger, fields)
        restart = self._failures.take_restart()
        if restart:
            self._logger.warning("closing the consumer after repeated counted failures")
        return restart

    async def _process(self, work: WorkRef, delivery: Delivery, fields: str) -> Action | None:
        """Claim the delivery and decide its action; None when it is already settled."""
        try:
            result = await self._jobs.claim(
                ClaimRequest(
                    ref=work,
                    claimed_by_worker=self._config.claimed_by_worker,
                    ttl=self._config.claim_ttl,
                )
            )
        except Exception as exc:  # the database is unreachable or contended
            return await self._store_failed("claim", exc, fields)
        self._failures.reset()
        outcome = result.outcome
        if outcome is ClaimOutcome.CLAIMED and result.claim is not None:
            return await self._claimed(result.claim, result.deadline_passed, delivery)
        if outcome in (
            ClaimOutcome.FINISHED,
            ClaimOutcome.HELD,
            ClaimOutcome.BACKOFF,
            ClaimOutcome.STALE_COMMAND_NUMBER,
        ):
            self._logger.debug("delivery needs no execution (%s claim=%s)", fields, outcome)
            return Action.ACK
        if outcome in (ClaimOutcome.NO_WORK, ClaimOutcome.MISMATCH):
            self._logger.warning(
                "command job does not match durable work; dead-lettered (%s claim=%s)",
                fields,
                outcome,
            )
            return Action.DEAD_LETTER
        self._logger.error(
            "corrupt work: an unfinished command's number differs from the project's last "
            "command number, or a claim came without its claim; dead-lettered (%s claim=%s)",
            fields,
            outcome,
        )
        return Action.DEAD_LETTER

    async def _store_failed(self, operation: str, exc: Exception, fields: str) -> Action:
        """Hold, then requeue (decision 0005 section 4, "database unreachable"). Every failure
        counts towards the job's 5 tries, a database outage included (decision 0023 G15);
        contention does not. Only a failure while the database answered counts towards the
        restart of the stream (``failure_of``)."""
        self._logger.warning(
            "database call failed; holding the delivery, then requeueing it "
            "(%s operation=%s error=%s)",
            fields,
            operation,
            type(exc).__name__,
        )
        return await self._failures.hold_then_requeue(failure_of(exc))

    async def _claimed(
        self, claim: Claim, deadline_passed: bool, delivery: Delivery
    ) -> Action | None:
        """The kind gate, the executor, the deadline and the attempt budget, then execution."""
        fields = _claim_fields(claim)
        kind = claim.ref.kind
        if not self._kinds.enabled(kind):
            return await self._fail(claim, FAILURE_CODE_OPERATION_UNAVAILABLE, fields)
        executor = self._executors.get(kind)
        if executor is None:
            return await self._fail(claim, FAILURE_CODE_EXECUTOR_UNAVAILABLE, fields)
        if deadline_passed:
            return await self._abandon_then_fail(
                executor, claim, FAILURE_CODE_DEADLINE_EXCEEDED, delivery, fields
            )
        if claim.execution_attempt > self._config.max_attempts:
            return await self._abandon_then_fail(
                executor, claim, FAILURE_CODE_ATTEMPTS_EXHAUSTED, delivery, fields
            )
        return await self._execute(executor, claim, delivery, fields)

    async def _abandon_then_fail(
        self, executor: Executor, claim: Claim, code: str, delivery: Delivery, fields: str
    ) -> Action | None:
        """Run the executor's abandon hook, if it has one, under the claim with the heartbeat
        beside it (a route restore can outlast the claim TTL) and bounded by its timeout; then
        fail the command with ``code`` (refused and acknowledged when the hook's outcome already
        ended it). The log names the failure code the hook wrote, which may be a go-live try's
        code rather than ``code`` (decision 0027 T9). The hook's error is logged and never
        changes the action; a claim lost during the hook writes nothing more; the worker stopping
        releases the claim and requeues the delivery, as during an execution."""
        if isinstance(executor, Abandoner):
            hook = executor
            written: list[str] = []  # the failure code the hook wrote, when it wrote one

            async def abandon() -> None:
                if (wrote := await hook.abandon(claim, code)) is not None:
                    written.append(wrote)

            try:
                execution = await self._supervised(claim, abandon, executor.abandon_timeout, fields)
            except asyncio.CancelledError:
                await asyncio.shield(self._stop(claim, delivery, fields))
                raise
            if execution.stop is StopReason.CLAIM_LOST:
                self._logger.warning(
                    "claim lost during the abandon hook; writing nothing more (%s)", fields
                )
                return Action.ACK
            if execution.error is not None:
                self._logger.warning(
                    "abandon hook failed; failing the command anyway (%s failure_code=%s error=%s)",
                    fields,
                    code,
                    type(execution.error).__name__,
                )
            elif written:
                self._logger.info("abandon hook ran (%s failure_code=%s)", fields, written[0])
            else:
                self._logger.info("abandon hook ran; no failure outcome written (%s)", fields)
        return await self._fail(claim, code, fields)

    async def _fail(self, claim: Claim, code: str, fields: str) -> Action:
        """End the command running -> failed with ``code`` through the fenced worker store. A
        refusal (the route is changing, the claim or the fence is stale, the command already
        finished) writes nothing and is acknowledged."""
        try:
            await self._worker.finish_command(
                claim.fence(),
                CommandFinish(status=CommandStatus.FAILED, failure_code=code, at=self._clock.now()),
            )
        except (ClaimLost, StaleFence, IllegalTransition) as exc:
            self._logger.info(
                "command failure not applied (%s failure_code=%s error=%s)",
                fields,
                code,
                type(exc).__name__,
            )
            return Action.ACK
        except Exception as exc:  # the database is unreachable
            return await self._store_failed("finish command", exc, fields)
        self._logger.info("command failed (%s failure_code=%s)", fields, code)
        return Action.ACK

    async def _execute(
        self, executor: Executor, claim: Claim, delivery: Delivery, fields: str
    ) -> Action | None:
        """Run the executor under the claim and map its result (decision 0005 section 4)."""
        start = time.monotonic()
        try:
            execution = await self._run(executor, claim, fields)
        except asyncio.CancelledError:
            self._measure(claim, RESULT_WORKER_STOPPING, start)
            await asyncio.shield(self._stop(claim, delivery, fields))
            raise
        self._measure(claim, _result_of(execution), start)
        error = execution.error
        if execution.stop is StopReason.CLAIM_LOST:
            self._logger.warning("claim lost during execution; writing nothing more (%s)", fields)
            return Action.ACK
        if error is None:
            self._logger.info("command executed (%s)", fields)
            return Action.ACK
        if isinstance(error, PermanentFailure):
            code = error.code
            try:
                validate_code(code)
            except DomainError:
                self._logger.error(
                    "executor returned a permanent failure with an invalid code (%s)", fields
                )
                code = FAILURE_CODE_EXECUTOR_FAILED
            return await self._fail(claim, code, fields)
        if isinstance(error, (ClaimLost, StaleFence)):
            self._logger.info(
                "executor's write was fenced off; writing nothing more (%s error=%s)",
                fields,
                type(error).__name__,
            )
            return Action.ACK
        code, after = _release_of(error, execution.stop)
        retry = (
            after
            if after is not None
            else self._config.retry_backoff.delay(claim.execution_attempt)
        )
        self._logger.warning(
            "transient execution failure; releasing the claim (%s error_code=%s error=%s "
            "retry_after=%s)",
            fields,
            code,
            type(error).__name__,
            retry,
        )
        try:
            await self._jobs.release_claim(claim, retry, code)
        except ClaimLost:
            # Another worker took the command over (task H5b): nothing was released or written.
            self._logger.info(
                "claim release refused: the claim was taken over; writing nothing more "
                "(%s error=ClaimLost)",
                fields,
            )
            return Action.ACK
        except Exception as exc:  # the database is unreachable
            return await self._store_failed("release claim", exc, fields)
        return Action.ACK

    def _measure(self, claim: Claim, result: str, start: float) -> None:
        with contextlib.suppress(Exception):  # a measurement never changes the outcome
            self._metrics.command_executed(claim.ref.kind.value, result, time.monotonic() - start)

    async def _run(self, executor: Executor, claim: Claim, fields: str) -> _Execution:
        """Call the executor bounded by the attempt timeout, with the heartbeat beside it."""
        return await self._supervised(
            claim, lambda: executor.execute(claim), self._config.attempt_timeout, fields
        )

    async def _supervised(
        self,
        claim: Claim,
        work: Callable[[], Awaitable[None]],
        bound: timedelta,
        fields: str,
    ) -> _Execution:
        """Run ``work`` under ``claim`` bounded by ``bound``, with the heartbeat beside it; the
        heartbeat cancels the work when the claim is lost. Returns after both stopped; raises
        ``CancelledError`` when the consumer itself is cancelled."""
        execution = _Execution()
        limit_seconds = bound.total_seconds()

        async def call() -> None:
            try:
                async with asyncio.timeout(limit_seconds) as limit:
                    await work()
            except TimeoutError as exc:
                if limit.expired() and execution.stop is None:
                    execution.stop = StopReason.ATTEMPT_TIMEOUT
                execution.error = exc
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if (
                    execution.stop is StopReason.CLAIM_LOST
                    and task is not None
                    and task.uncancel() == 0
                ):
                    return
                raise
            except Exception as exc:  # the executor's error is mapped by the caller
                execution.error = exc

        async with asyncio.TaskGroup() as group:
            executing = group.create_task(call())

            def lose() -> None:
                execution.stop = StopReason.CLAIM_LOST
                executing.cancel()

            beating = group.create_task(self._heartbeat(claim, lose, fields))
            executing.add_done_callback(lambda _: beating.cancel())
        return execution

    async def _heartbeat(self, claim: Claim, lose: _Lose, fields: str) -> None:
        """Extend the claim every ``heartbeat_interval``. ``ClaimLost`` calls ``lose``; when no
        extension succeeded for a whole claim TTL, another worker may already own the command,
        so the claim is treated as lost."""
        loop = asyncio.get_running_loop()
        interval = self._config.heartbeat_interval.total_seconds()
        ttl = self._config.claim_ttl.total_seconds()
        current = claim
        last_ok = loop.time()
        while True:
            await asyncio.sleep(interval)
            try:
                current = await self._jobs.extend_claim(current, self._config.claim_ttl)
            except ClaimLost:
                lose()
                return
            except Exception as exc:  # retried at the next interval, within the TTL
                self._logger.warning(
                    "claim extension failed (%s error=%s)", fields, type(exc).__name__
                )
                if loop.time() - last_ok >= ttl:
                    lose()
                    return
                continue
            last_ok = loop.time()

    async def _stop(self, claim: Claim, delivery: Delivery, fields: str) -> None:
        """Release the claim of an execution ended because the worker is stopping, bounded by
        the claim TTL (after it the claim has expired anyway), then requeue the delivery."""
        try:
            async with asyncio.timeout(self._config.claim_ttl.total_seconds()):
                await self._jobs.release_claim(
                    claim, _STOP_RETRY_AFTER, FAILURE_CODE_WORKER_STOPPING
                )
        except ClaimLost:
            self._logger.info("worker stopping; the claim was already taken over (%s)", fields)
        except Exception as exc:  # the claim expires instead
            self._logger.warning(
                "releasing the claim while stopping failed; it expires instead (%s error=%s)",
                fields,
                type(exc).__name__,
            )
        else:
            self._logger.info("worker stopping; claim released (%s)", fields)
        try:
            async with asyncio.timeout(self._config.claim_ttl.total_seconds()):
                await settle(delivery, Action.REQUEUE, self._logger, fields)
        except TimeoutError:
            self._logger.warning("requeueing while stopping timed out (%s)", fields)


type _Lose = Callable[[], None]


def _result_of(execution: _Execution) -> str:
    """How an execution attempt ended, in ``_execute``'s order."""
    error = execution.error
    if execution.stop is StopReason.CLAIM_LOST:
        return RESULT_CLAIM_LOST
    if error is None:
        return RESULT_FINISHED
    if isinstance(error, PermanentFailure):
        return RESULT_PERMANENT_FAILURE
    if isinstance(error, (ClaimLost, StaleFence)):
        return RESULT_FENCED
    if execution.stop is StopReason.ATTEMPT_TIMEOUT:
        return RESULT_ATTEMPT_TIMEOUT
    if isinstance(error, RetryLater) and error.after is not None:
        return RESULT_TRY_FAILED
    return RESULT_TRANSIENT_FAILURE
