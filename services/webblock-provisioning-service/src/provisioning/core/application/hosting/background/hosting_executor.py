"""The executor of claimed suspend_hosting and resume_hosting commands: a thin adapter from a
``Claim`` to the hosting use case (decision 0005 section 5), with the abandon hook of decision 0011
section 3 (``core.application.routing.background.recovery.RouteAbandoner``; decision 0014 section 21
ruling 9): a hosting change the consumer is about to fail while the route is ``changing`` ends
``stable`` or ``uncertain``, never stuck."""

from __future__ import annotations

from datetime import timedelta

from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    PermanentFailure,
)
from provisioning.core.application.routing.background.recovery import RouteAbandoner
from provisioning.core.application.routing.background.routing_executor import check_abandon_timeout
from provisioning.core.domain import CommandKind


class HostingExecutor:
    """Runs ``suspend_hosting`` and ``resume_hosting``; a claim of any other kind is
    ``PermanentFailure(operation_unavailable)`` and calls nothing."""

    __slots__ = ("_abandon_timeout", "_abandoner", "_hosting")

    def __init__(
        self, hosting: Hosting, abandoner: RouteAbandoner, *, abandon_timeout: timedelta
    ) -> None:
        check_abandon_timeout(abandoner, abandon_timeout)
        self._hosting = hosting
        self._abandoner = abandoner
        self._abandon_timeout = abandon_timeout

    @property
    def abandon_timeout(self) -> timedelta:
        """The bound of one ``abandon`` call: above one restore deadline."""
        return self._abandon_timeout

    async def execute(self, claim: Claim) -> None:
        if claim.ref.kind is CommandKind.SUSPEND_HOSTING:
            await self._hosting.suspend(claim)
        elif claim.ref.kind is CommandKind.RESUME_HOSTING:
            await self._hosting.resume(claim)
        else:
            raise PermanentFailure(FAILURE_CODE_OPERATION_UNAVAILABLE)

    async def abandon(self, claim: Claim, failure_code: str) -> str | None:
        """Finish or record uncertain the hosting change the consumer is about to fail with
        ``failure_code``, and return the failure code ``RouteAbandoner`` wrote (None when it
        wrote none); another kind has nothing to restore."""
        if claim.ref.kind.is_hosting():
            return await self._abandoner.abandon(claim, failure_code)
        return None
