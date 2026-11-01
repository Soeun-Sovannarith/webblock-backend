"""The inbox processor: it accepts the inbox rows of Project's "source published" event and starts
their builds (decision 0008 section 2, decision 0014 sections 3 and 10.5).

The processor runs ``process_batch`` every interval, and at once after the consumer
(``presentation.messaging.consumers.inbox``) stored a new row: it confirms the repository, accepts
the release and starts its build (``StartBuild``, through the use case). Runs in the build worker
only. Split out of the inbox consumer module by the decision 0025 (FL7).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from provisioning.core.application import (
    INTAKE_REASON_SUBDOMAIN_TAKEN,
    MAX_INBOX_BATCH_SIZE,
    DependencyNotConfigured,
    InboxBatchParams,
    InboxBatchResult,
    InboxRowOutcome,
    InboxRowResult,
    TakeInboxParams,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig


class InboxBatches(Protocol):
    """``ReleaseIntake.process_batch``."""

    async def process_batch(self, params: InboxBatchParams) -> InboxBatchResult: ...


@dataclass(frozen=True, slots=True)
class InboxProcessorConfig:
    """The processor's limits (decision 0008 section 2); every value required."""

    poll_interval: timedelta
    batch_size: int
    max_per_project: int
    visibility: timedelta
    concurrency: int
    registry_timeout: timedelta
    transaction_timeout: timedelta
    max_attempts: int
    deadline: timedelta
    backoff: Backoff

    def batch_params(self) -> InboxBatchParams:
        """The configuration as the use case takes it."""
        return InboxBatchParams(
            take=TakeInboxParams(
                batch_size=self.batch_size,
                max_per_project=self.max_per_project,
                visibility=self.visibility,
            ),
            concurrency=self.concurrency,
            registry_timeout=self.registry_timeout,
            transaction_timeout=self.transaction_timeout,
            max_attempts=self.max_attempts,
            deadline=self.deadline,
            retry_delay=self.backoff.delay,
        )

    def validate(self) -> None:
        """``InvalidConfig`` for what ``InboxBatchParams.validate`` refuses, a bad backoff or a
        non-positive poll interval."""
        if self.poll_interval <= timedelta(0):
            raise InvalidConfig("inbox poll interval must be positive")
        if self.batch_size > MAX_INBOX_BATCH_SIZE:
            raise InvalidConfig(f"inbox batch size must be at most {MAX_INBOX_BATCH_SIZE}")
        self.backoff.validate()
        try:
            self.batch_params().validate()
        except ValueError as exc:
            raise InvalidConfig(str(exc)) from None


def _row_fields(row: InboxRowResult) -> str:
    return (
        f"project_id={row.take.project_id} "
        f"generation_request_id={row.take.generation_request_id} "
        f"attempt={row.take.attempts} status={row.outcome}"
    )


class InboxProcessor:
    """One batch every ``poll_interval`` or when woken, never two at once."""

    __slots__ = ("_config", "_intake", "_logger", "_wake")

    def __init__(
        self, config: InboxProcessorConfig, *, intake: InboxBatches, logger: logging.Logger
    ) -> None:
        config.validate()
        self._config = config
        self._intake = intake
        self._logger = logger
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Run the next batch now instead of at the end of the interval."""
        self._wake.set()

    async def poll(self) -> None:
        """Process one batch and log each row (IDs, attempt, outcome and code only)."""
        result = await self._intake.process_batch(self._config.batch_params())
        for row in result.rows:
            self._log_row(row)

    async def run(self) -> None:
        """Poll until cancelled. A failed batch is logged; ``DependencyNotConfigured`` (the
        repository registry, OD-01) stops the processor and is raised, leaving the rows
        pending."""
        interval = self._config.poll_interval.total_seconds()
        while True:
            self._wake.clear()
            try:
                await self.poll()
            except DependencyNotConfigured:
                self._logger.error(
                    "inbox processor stopped: the repository registry is not configured "
                    "(OD-01); the rows stay pending"
                )
                raise
            except Exception as exc:  # a failed batch must not end the loop
                self._logger.warning("inbox batch failed (error=%s)", type(exc).__name__)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(interval):
                    await self._wake.wait()

    def _log_row(self, row: InboxRowResult) -> None:
        fields = _row_fields(row)
        match row.outcome:
            case InboxRowOutcome.ACCEPTED:
                what = "created with its first build step" if row.created else "existing release"
                build = "" if row.build_error is None else type(row.build_error).__name__
                self._logger.info(
                    "release accepted: %s (%s release_id=%s build_error=%s)",
                    what,
                    fields,
                    row.release_id,
                    build or "none",
                )
            case InboxRowOutcome.REJECTED:
                self._logger.info("inbox row rejected (%s failure_code=%s)", fields, row.code)
            case InboxRowOutcome.RETRIED:
                self._logger.info(
                    "repository not confirmed; retry scheduled (%s reason=%s retry_after=%s)",
                    fields,
                    row.reason,
                    row.retry_after,
                )
            case InboxRowOutcome.FAILED if row.reason == INTAKE_REASON_SUBDOMAIN_TAKEN:
                self._logger.error(
                    "inbox row failed: the registry's sub-domain belongs to another project and "
                    "the attempt budget is spent; an operator must look (%s failure_code=%s "
                    "reason=%s error=%s)",
                    fields,
                    row.code,
                    row.reason,
                    type(row.error).__name__,
                )
            case InboxRowOutcome.FAILED:
                self._logger.warning(
                    "inbox row failed: repository unconfirmed within its budget "
                    "(%s failure_code=%s reason=%s)",
                    fields,
                    row.code,
                    row.reason,
                )
            case InboxRowOutcome.SUPERSEDED:
                self._logger.debug("inbox row superseded by a newer take (%s)", fields)
            case _:
                error = "unknown" if row.error is None else type(row.error).__name__
                self._logger.warning(
                    "inbox row write failed; the row comes due after the visibility timeout "
                    "(%s error=%s)",
                    fields,
                    error,
                )
