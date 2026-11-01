"""The executor of claimed promote and rollback commands: a thin adapter from a ``Claim`` to the
routing use case (decision 0005 section 5), with the abandon hook of decision 0011 section 3
(``core.application.routing.background.recovery.RouteAbandoner``; decision 0014 section 21 ruling
9): a command the consumer is about to fail for its deadline or its attempt budget while the route
is ``changing`` ends ``stable`` or ``uncertain``, never stuck."""

from __future__ import annotations

from datetime import timedelta

from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    PermanentFailure,
)
from provisioning.core.application.routing.background.recovery import RouteAbandoner
from provisioning.core.application.routing.background.routing import Routing
from provisioning.core.domain import CommandKind


def check_abandon_timeout(abandoner: RouteAbandoner, abandon_timeout: timedelta) -> None:
    """An abandon bound must leave room for one restore deadline and the database writes after
    it, or every abandon would be cancelled before it records anything (``ValueError``)."""
    if abandon_timeout <= abandoner.longest_run:
        raise ValueError("the abandon timeout must exceed the route restore deadline")


class RoutingExecutor:
    """Runs ``promote`` and ``rollback``; a claim of any other kind is
    ``PermanentFailure(operation_unavailable)`` and calls nothing."""

    __slots__ = ("_abandon_timeout", "_abandoner", "_routing")

    def __init__(
        self, routing: Routing, abandoner: RouteAbandoner, *, abandon_timeout: timedelta
    ) -> None:
        check_abandon_timeout(abandoner, abandon_timeout)
        self._routing = routing
        self._abandoner = abandoner
        self._abandon_timeout = abandon_timeout

    @property
    def abandon_timeout(self) -> timedelta:
        """The bound of one ``abandon`` call: above one restore deadline."""
        return self._abandon_timeout

    async def execute(self, claim: Claim) -> None:
        if claim.ref.kind is CommandKind.PROMOTE:
            await self._routing.promote(claim)
        elif claim.ref.kind is CommandKind.ROLLBACK:
            await self._routing.rollback(claim)
        else:
            raise PermanentFailure(FAILURE_CODE_OPERATION_UNAVAILABLE)

    async def abandon(self, claim: Claim, failure_code: str) -> str | None:
        """Restore the route of a promote or rollback that the consumer is about to fail with
        ``failure_code``, and return the failure code ``RouteAbandoner`` wrote (None when it
        wrote none); another kind has nothing to restore."""
        if claim.ref.kind in (CommandKind.PROMOTE, CommandKind.ROLLBACK):
            return await self._abandoner.abandon(claim, failure_code)
        return None
