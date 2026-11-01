"""The dispatcher (decision 0005 section 3).

PostgreSQL is the authority and RabbitMQ only notifies. Each poll takes a batch of due dispatch
rows in one short transaction, publishes them with publisher confirms (at most
``publish_concurrency`` at once), and records each broker ack or failure conditionally; a command
never claimed whose deadline has passed is failed instead of published. A crash anywhere leaves the
rows to come due again after the visibility timeout, so a publish may repeat; the consumer makes a
duplicate harmless.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from provisioning.core.application import (
    ClaimLost,
    Clock,
    DueDispatch,
    IdSource,
    JobStore,
    WorkerStore,
    WorkRef,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import IllegalTransition, StaleFence

# Bounds DISPATCH_BATCH_SIZE, and with it the publishes one poll has in flight.
MAX_DISPATCH_BATCH_SIZE = 1000

# Ends a command whose deadline passed: by the dispatcher for a command never claimed, by the
# consumer after a claim.
FAILURE_CODE_DEADLINE_EXCEEDED = "deadline_exceeded"


class CommandJobPublisher(Protocol):
    """Publishes one command job; returns only when the broker confirmed it and did not return it
    as unroutable. A negative acknowledgment, a return, a closed channel or a timeout is an error.
    Implemented by ``presentation.messaging.CommandJobPublisher``."""

    async def publish_command_job(
        self, *, message_id: UUID, ref: WorkRef, correlation_id: str, occurred_at: datetime
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class DispatcherConfig:
    """Every value required (decision 0005 section 7)."""

    poll_interval: timedelta  # DISPATCH_POLL_INTERVAL
    batch_size: int  # DISPATCH_BATCH_SIZE, 1 to MAX_DISPATCH_BATCH_SIZE
    publish_concurrency: int  # publishes in flight at once, 1 to batch_size
    confirm_timeout: timedelta  # DISPATCH_PUBLISH_CONFIRM_TIMEOUT
    visibility_timeout: timedelta  # DISPATCH_VISIBILITY_TIMEOUT, above the confirm timeout
    redelivery_interval: timedelta  # DISPATCH_REDELIVERY_INTERVAL
    backoff: Backoff  # DISPATCH_BACKOFF_BASE and _MAX, by publish_attempts

    def validate(self) -> None:
        """``InvalidConfig`` for a missing value, a batch above the maximum, a concurrency
        outside 1 to the batch, or a visibility timeout not above the confirm timeout."""
        if self.poll_interval <= timedelta(0):
            raise InvalidConfig("dispatch poll interval must be positive")
        if not 1 <= self.batch_size <= MAX_DISPATCH_BATCH_SIZE:
            raise InvalidConfig(f"dispatch batch size must be 1 to {MAX_DISPATCH_BATCH_SIZE}")
        if not 1 <= self.publish_concurrency <= self.batch_size:
            raise InvalidConfig("publish concurrency must be 1 to the batch size")
        if self.confirm_timeout <= timedelta(0):
            raise InvalidConfig("publish confirm timeout must be positive")
        if self.visibility_timeout <= self.confirm_timeout:
            raise InvalidConfig("visibility timeout must exceed the publish confirm timeout")
        if self.redelivery_interval <= timedelta(0):
            raise InvalidConfig("redelivery interval must be positive")
        self.backoff.validate()


def _fields(due: DueDispatch) -> str:
    return (
        f"command_id={due.ref.command_id} project_id={due.ref.project_id} kind={due.ref.kind} "
        f"publish_attempts={due.publish_attempts}"
    )


class Dispatcher:
    """Publishes a notification for every unfinished command until it is finished."""

    __slots__ = (
        "_clock",
        "_config",
        "_ids",
        "_jobs",
        "_logger",
        "_publisher",
        "_worker",
    )

    def __init__(
        self,
        config: DispatcherConfig,
        *,
        jobs: JobStore,
        worker: WorkerStore,
        publisher: CommandJobPublisher,
        ids: IdSource,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        config.validate()
        self._config = config
        self._jobs = jobs
        self._worker = worker
        self._publisher = publisher
        self._ids = ids
        self._clock = clock
        self._logger = logger

    async def poll(self) -> None:
        """One cycle: report commands without a dispatch row, take a batch of due rows, publish
        or fail each one, and return when every row was handled. The take's error is raised;
        a row's own error is logged and the row comes due again."""
        await self._report_orphans()
        rows = await self._jobs.take_due(self._config.batch_size, self._config.visibility_timeout)
        if not rows:
            return
        slots = asyncio.Semaphore(self._config.publish_concurrency)

        async def one(row: DueDispatch) -> None:
            async with slots:
                await self._dispatch(row)

        async with asyncio.TaskGroup() as group:
            for due in rows:
                group.create_task(one(due))

    async def run(self) -> None:
        """Poll at once and then every ``poll_interval`` until cancelled; a failed poll is logged
        and the next one runs on schedule."""
        while True:
            try:
                await self.poll()
            except Exception as exc:  # a failed poll must not end the loop
                self._logger.warning("dispatch poll failed (error=%s)", type(exc).__name__)
            await asyncio.sleep(self._config.poll_interval.total_seconds())

    async def _report_orphans(self) -> None:
        try:
            count = await self._jobs.count_unfinished_without_dispatch()
        except Exception as exc:  # reporting only; the poll goes on
            self._logger.warning(
                "counting unfinished commands without a dispatch row failed (error=%s)",
                type(exc).__name__,
            )
            return
        if count > 0:
            self._logger.error(
                "unfinished commands without a dispatch row; acceptance cannot produce them and "
                "nothing repairs them (count=%d)",
                count,
            )

    async def _dispatch(self, due: DueDispatch) -> None:
        """Handle one taken row; every error is logged here, so the batch goes on."""
        fields = _fields(due)
        if due.deadline_passed and due.execution_attempts == 0:
            await self._fail_unclaimed(due, fields)
            return
        try:
            await self._publish(due)
        except Exception as exc:  # a nack, a return, a timeout or a closed channel
            retry = self._config.backoff.delay(due.publish_attempts)
            self._logger.warning(
                "command job publish failed (%s error=%s retry_after=%s)",
                fields,
                type(exc).__name__,
                retry,
            )
            try:
                await self._jobs.record_publish_failure(due, retry)
            except Exception as record_exc:  # the row comes due after the visibility timeout
                self._logger.warning(
                    "recording a publish failure failed; the row comes due after the visibility "
                    "timeout (%s error=%s)",
                    fields,
                    type(record_exc).__name__,
                )
            return
        try:
            recorded = await self._jobs.record_broker_ack(due, self._config.redelivery_interval)
        except Exception as exc:  # the row comes due after the visibility timeout
            self._logger.warning(
                "recording a broker ack failed; the row comes due after the visibility timeout "
                "(%s error=%s)",
                fields,
                type(exc).__name__,
            )
            return
        if recorded:
            self._logger.debug("command job published (%s)", fields)
        else:
            self._logger.debug(
                "broker ack not recorded: the row changed since the take (%s)", fields
            )

    async def _publish(self, due: DueDispatch) -> None:
        """A fresh message ID per publish, bounded by the confirm timeout."""
        message_id = self._ids.new_id()
        async with asyncio.timeout(self._config.confirm_timeout.total_seconds()):
            await self._publisher.publish_command_job(
                message_id=message_id,
                ref=due.ref,
                correlation_id=due.correlation_id,
                occurred_at=self._clock.now(),
            )

    async def _fail_unclaimed(self, due: DueDispatch, fields: str) -> None:
        """Fail a command never claimed before its deadline (decision 0005 section 3 step 4).
        The worker store refuses when a claim happened since the take; that is not an error."""
        try:
            await self._worker.fail_unclaimed_command(
                due.fence(), FAILURE_CODE_DEADLINE_EXCEEDED, self._clock.now()
            )
        except (ClaimLost, StaleFence, IllegalTransition) as exc:
            self._logger.info(
                "deadline failure not applied: the command changed since the take (%s error=%s)",
                fields,
                type(exc).__name__,
            )
            return
        except Exception as exc:  # the row comes due after the visibility timeout
            self._logger.warning(
                "deadline failure failed; the row comes due after the visibility timeout "
                "(%s error=%s)",
                fields,
                type(exc).__name__,
            )
            return
        self._logger.info(
            "command failed: deadline passed before any claim (%s failure_code=%s)",
            fields,
            FAILURE_CODE_DEADLINE_EXCEEDED,
        )
