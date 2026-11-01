"""The retry of a failed suspend or resume within a budget (QA-03; cut from the former hosting
module, decision 0025 CQ6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.hosting.commands.accept_hosting_event import (
    AcceptHostingEventCommand,
    retry_hosting_event_later,
)
from provisioning.core.application.hosting.ports import HostingHistory
from provisioning.core.application.jobs.ports import AcceptedCommand
from provisioning.core.domain import (
    CommandStatus,
    DomainError,
    OfflineReason,
    OwnerStatusOutdated,
    is_error,
    split_retry_key,
    validate_idempotency_key,
)

HOSTING_RETRY_BUDGET: Final = 3
"""The most retries of one hosting intent after its first command failed: a value in code, not a
configuration key, so that changing it is a reviewed change."""


def retry_key(event_key: str, attempt: int) -> str:
    """The idempotency key of retry ``attempt`` (1, 2, ...) of the intent of ``event_key``."""
    if attempt < 1:
        raise ValueError("a retry attempt starts at 1")
    return f"{event_key}.retry-{attempt}"


class HostingEventIntake(Protocol):
    """``AcceptHostingEventHandler``: the retry accepts its command directly, not through a bus
    (a background job, decision 0025 CQ9)."""

    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand: ...


class RetryOutcome(StrEnum):
    RETRIED = "retried"  # a retry command was accepted (or one already existed: replayed)
    NOT_NEEDED = "not_needed"  # no failed intent, a newer one, or hosting already as asked
    DEFERRED = "deferred"  # refused for now (busy, route not stable); asked again later
    BUDGET_SPENT = "budget_spent"  # HOSTING_RETRY_BUDGET retries failed; an operator must act
    UNRETRYABLE = "unretryable"  # the retry key would break the idempotency key rule


@dataclass(frozen=True, slots=True)
class RetryResult:
    project_id: UUID
    outcome: RetryOutcome
    event_key: str | None = None  # the retry's key (or the spent intent's key)
    accepted: AcceptedCommand | None = None


class HostingRetry:
    """Retries a failed suspend or resume within a budget (QA-03)."""

    __slots__ = ("_budget", "_history", "_intake", "_logger")

    def __init__(
        self,
        *,
        history: HostingHistory,
        intake: HostingEventIntake,
        budget: int = HOSTING_RETRY_BUDGET,
        logger: logging.Logger | None = None,
    ) -> None:
        """Refuses a budget below 1 (``ValueError``). Calls no port. ``logger`` gets one info
        line for each retry skipped as outdated; without one nothing is logged."""
        if budget < 1:
            raise ValueError("hosting retry: the budget must be at least 1")
        self._history = history
        self._intake = intake
        self._budget = budget
        self._logger = logger

    async def retry(self, project_id: UUID) -> RetryResult:
        """Retry the oldest latest-per-reason intent that failed, is not met and is still
        current. The acceptance checks "still current" again on the locked row
        (``OwnerStatusOutdated``), so a newer owner status that lands after the read skips the
        intent too. An intent whose budget is spent, or whose retry key would break the key rule,
        does not block another reason; when no intent can be retried, the first such intent is
        reported (``BUDGET_SPENT``, ``UNRETRYABLE``), else ``NOT_NEEDED``. A transient refusal of
        the retry is ``DEFERRED``; any other error is raised. One retry per call: the next waits
        until this one finished (one command in flight per project)."""
        stuck: RetryResult | None = None
        for intent in await self._history.latest_hosting_intents(project_id):
            if intent.status is not CommandStatus.FAILED or intent.met():
                continue
            if intent.hosting_reason is OfflineReason.ADMIN_SUSPENDED:
                # An admin's command is retried by the admin, never through the event intake,
                # which refuses its key (decision 0023 G11).
                continue
            if not intent.still_current():
                continue
            root, attempt = split_retry_key(intent.idempotency_key)
            if attempt >= self._budget:
                stuck = stuck or RetryResult(
                    project_id, RetryOutcome.BUDGET_SPENT, intent.idempotency_key
                )
                continue
            key = retry_key(root, attempt + 1)
            try:
                validate_idempotency_key(key)
            except DomainError:
                stuck = stuck or RetryResult(
                    project_id, RetryOutcome.UNRETRYABLE, intent.idempotency_key
                )
                continue
            try:
                accepted = await self._intake.handle(
                    AcceptHostingEventCommand(
                        event_key=key, project_id=project_id, kind=intent.kind
                    )
                )
            except Exception as exc:
                if is_error(exc, OwnerStatusOutdated):
                    # A newer owner status landed after the read; checked under the lock.
                    if self._logger is not None:
                        self._logger.info(
                            "hosting retry skipped, owner status outdated"
                            " (project_id=%s retry_key=%s)",
                            project_id,
                            key,
                        )
                    continue
                if retry_hosting_event_later(exc):
                    return RetryResult(project_id, RetryOutcome.DEFERRED, key)
                raise
            return RetryResult(project_id, RetryOutcome.RETRIED, key, accepted)
        return stuck or RetryResult(project_id, RetryOutcome.NOT_NEEDED)

    async def sweep(self, limit: int) -> tuple[RetryResult, ...]:
        """Retry each project the history lists (at most ``limit``), one after the other."""
        projects = await self._history.projects_to_retry(limit=limit, budget=self._budget)
        return tuple([await self.retry(project) for project in projects])
