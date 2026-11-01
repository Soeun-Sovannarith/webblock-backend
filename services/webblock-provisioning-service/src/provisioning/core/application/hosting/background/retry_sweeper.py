"""The hosting retry sweep (QA-03): every interval it retries the failed suspends and resumes the
history lists, so a failed command whose event never repeats is still retried within the budget;
and the two helpers the hosting event consumers share with it: the log line of a retry and the
audit event of an accepted hosting command (decision 0014 section 21 answer 3). Split out of the
hosting event consumers (``presentation.messaging.consumers.payment_events``) by decision 0025
FL7.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from provisioning.core.application import AcceptedCommand, Clock
from provisioning.core.application.hosting.background.retry import RetryOutcome, RetryResult
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.application.shared.backoff import InvalidConfig
from provisioning.core.domain import CommandKind

_ACTION_OF = {
    CommandKind.SUSPEND_HOSTING: AuditAction.SUSPEND_HOSTING,
    CommandKind.RESUME_HOSTING: AuditAction.RESUME_HOSTING,
}
RETRY_ACTOR = "hosting-retry"


def audit_accepted(
    audit: AuditLog,
    clock: Clock,
    accepted: AcceptedCommand,
    *,
    actor_kind: AuditActorKind,
    actor: str,
    correlation_id: str,
) -> None:
    """The audit event of a hosting command an event or a retry created; a replay is none."""
    if accepted.replayed:
        return
    command = accepted.command
    audit.record(
        AuditEvent(
            actor_kind=actor_kind,
            actor=actor,
            action=_ACTION_OF[command.kind],
            project_id=command.project_id,
            release_id=None,
            command_id=command.id,
            result=AuditResult.ACCEPTED,
            failure_code=None,
            at=clock.now(),
            correlation_id=correlation_id,
        )
    )


def log_retry(logger: logging.Logger, result: RetryResult, audit: AuditLog, clock: Clock) -> None:
    """One line per retry that did something or cannot happen; ``not_needed`` is debug."""
    fields = f"project_id={result.project_id} retry_key={result.event_key}"
    if result.outcome in (RetryOutcome.BUDGET_SPENT, RetryOutcome.UNRETRYABLE):
        logger.error(
            "hosting retry %s: the failed suspend or resume needs an operator (%s)",
            result.outcome.value,
            fields,
        )
    elif result.outcome is RetryOutcome.NOT_NEEDED:
        logger.debug("hosting retry not_needed (%s)", fields)
    else:
        logger.info("hosting retry %s (%s)", result.outcome.value, fields)
    if result.accepted is not None and result.event_key is not None:
        audit_accepted(
            audit,
            clock,
            result.accepted,
            actor_kind=AuditActorKind.SYSTEM,
            actor=RETRY_ACTOR,
            correlation_id=result.event_key,
        )


class RetrySweeps(Protocol):
    """``core.application.hosting.background.retry.HostingRetry.sweep``."""

    async def sweep(self, limit: int) -> tuple[RetryResult, ...]: ...


@dataclass(frozen=True, slots=True)
class RetrySweepConfig:
    interval: timedelta  # RECONCILE_INTERVAL
    batch_size: int  # RECONCILE_BATCH

    def validate(self) -> None:
        if self.interval <= timedelta(0):
            raise InvalidConfig("hosting retry sweep interval must be positive")
        if not 1 <= self.batch_size <= 1000:
            raise InvalidConfig("hosting retry sweep batch must be 1 to 1000")


class HostingRetrySweeper:
    """Every ``interval``, retries the failed suspends and resumes the history lists (QA-03), so a
    failed command whose event never repeats is still retried within the budget."""

    __slots__ = ("_audit", "_clock", "_config", "_logger", "_retries")

    def __init__(
        self,
        config: RetrySweepConfig,
        *,
        retries: RetrySweeps,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        config.validate()
        self._config = config
        self._retries = retries
        self._audit = audit
        self._clock = clock
        self._logger = logger

    async def sweep(self) -> tuple[RetryResult, ...]:
        """One sweep; each result is logged. The history's error is raised."""
        results = await self._retries.sweep(self._config.batch_size)
        for result in results:
            log_retry(self._logger, result, self._audit, self._clock)
        return results

    async def run(self) -> None:
        """Sweep every ``interval`` until cancelled; a failed sweep is logged and the next one
        runs on schedule."""
        while True:
            await asyncio.sleep(self._config.interval.total_seconds())
            try:
                await self.sweep()
            except Exception as exc:  # a failed sweep must not end the loop
                self._logger.warning("hosting retry sweep failed (error=%s)", type(exc).__name__)
