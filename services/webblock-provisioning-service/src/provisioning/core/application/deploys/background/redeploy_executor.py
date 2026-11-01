"""The executor of claimed redeploy commands: a thin adapter from a ``Claim`` to the redeploy use
case (decision 0023 G12; decision 0005 section 5).

The use case's own fenced writes end the command, so ``execute`` returning means finished;
``RetryLater`` and any other error are transient; ``ClaimLost`` and ``StaleFence`` reach the
consumer unchanged, which then writes nothing. There is no abandon hook: a redeploy never moves the
route and its rolling update keeps the old pods serving, so a command the consumer fails for its
deadline or its attempt budget leaves nothing to clean up.
"""

from __future__ import annotations

from typing import Protocol

from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    PermanentFailure,
)
from provisioning.core.domain import CommandKind


class RedeployRunner(Protocol):
    """The redeploy use case as the executor consumes it (``Redeploy``)."""

    async def run(self, claim: Claim) -> None: ...


class RedeployExecutor:
    """Runs ``redeploy``; a claim of any other kind is ``PermanentFailure(operation_unavailable)``
    and calls nothing."""

    __slots__ = ("_runner",)

    def __init__(self, runner: RedeployRunner) -> None:
        self._runner = runner

    async def execute(self, claim: Claim) -> None:
        if claim.ref.kind is not CommandKind.REDEPLOY:
            raise PermanentFailure(FAILURE_CODE_OPERATION_UNAVAILABLE)
        await self._runner.run(claim)
