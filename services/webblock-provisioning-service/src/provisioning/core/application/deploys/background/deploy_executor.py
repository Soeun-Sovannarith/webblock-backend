"""DeployExecutor: the ``Executor`` for ``CommandKind.DEPLOY`` (decision 0014 section 3), on plain
Kubernetes objects.

A thin adapter from a ``Claim`` to the deploy use case: the use case's own fenced writes end the
command, so ``execute`` returning means finished; ``PermanentFailure`` (a refusal before the
attempt) is for the consumer to write; ``RetryLater`` and any other error are transient;
``ClaimLost`` and ``StaleFence`` reach the consumer unchanged, which then writes nothing.

``abandon`` is the hook of decision 0011 section 3: a consumer that is about to fail a deploy
command for its deadline or its execution budget calls it first, bounded by
``abandon_timeout``, so that the candidate's objects are deleted and the unfinished attempt is
ended. It is optional for the consumer; without it the next deploy re-applies the objects at its
own command number.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Protocol

from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    PermanentFailure,
)
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.domain import CommandKind


class DeployRunner(Protocol):
    """The deploy use case as the executor consumes it (``DeployCandidate``)."""

    async def run(self, fence: Fence, deadline_at: datetime) -> None: ...

    async def abandon(self, fence: Fence, code: str) -> None: ...


class DeployExecutor:
    """Executes claimed deploy commands; a claim of another kind is refused with
    ``operation_unavailable`` without running anything."""

    __slots__ = ("_abandon_timeout", "_runner")

    def __init__(self, runner: DeployRunner, *, abandon_timeout: timedelta) -> None:
        if abandon_timeout <= timedelta(0):
            raise ValueError("deploy executor: the abandon timeout must be positive")
        self._runner = runner
        self._abandon_timeout = abandon_timeout

    @property
    def abandon_timeout(self) -> timedelta:
        """The bound of one ``abandon`` call: at least the use case's delete timeout."""
        return self._abandon_timeout

    async def execute(self, claim: Claim) -> None:
        if claim.ref.kind is not CommandKind.DEPLOY:
            raise PermanentFailure(FAILURE_CODE_OPERATION_UNAVAILABLE)
        await self._runner.run(claim.fence(), claim.deadline_at)

    async def abandon(self, claim: Claim, code: str) -> str | None:
        """Clean up a deploy command the consumer is about to fail with ``code``; another kind
        has nothing to clean up. Returns None: the clean-up writes no failure outcome, the
        consumer's own failure write ends the command."""
        if claim.ref.kind is not CommandKind.DEPLOY:
            return None
        await self._runner.abandon(claim.fence(), code)
        return None


class AsyncioPauser:
    """The use case's ``Pauser``: a cancellable ``asyncio.sleep``."""

    __slots__ = ()

    async def pause(self, delay: timedelta) -> None:
        await asyncio.sleep(delay.total_seconds())
