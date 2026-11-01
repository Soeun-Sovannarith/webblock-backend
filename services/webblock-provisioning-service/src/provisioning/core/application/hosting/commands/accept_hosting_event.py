"""The intake of Payment's hosting events: one event becomes a suspend_hosting or resume_hosting
command keyed by the event key (decision 0014 sections 3 and 9; cut from the former hosting module,
decision 0025 CQ6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from provisioning.core.application.hosting.ports import HostingCommandStore
from provisioning.core.application.jobs.ports import AcceptedCommand
from provisioning.core.application.operations.views import MAX_CORRELATION_ID_LEN
from provisioning.core.application.ports.dependencies import Clock, IdSource
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.errors import (
    Contention,
    CorruptRow,
    DependencyUnavailable,
    OperationInProgress,
    Timeout,
)
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    InvalidCommandTarget,
    InvalidId,
    RouteNotStable,
    compute_request_fingerprint,
    is_error,
)

# Refusals after which a Payment event is redelivered later rather than dropped.
_RETRY_LATER: tuple[type[Exception], ...] = (
    OperationInProgress,
    RouteNotStable,
    Contention,
    Timeout,
    DependencyUnavailable,
)


@dataclass(frozen=True, slots=True)
class AcceptHostingEventCommand(cqrs.Command[AcceptedCommand]):
    """One hosting event: Payment's entitlement ended or restored, or Project's "project
    deleted" (key ``project-deleted``), or a retry of one (``<event_key>.retry-<n>``)."""

    event_key: str
    project_id: UUID
    kind: CommandKind


class AcceptHostingEventHandler:
    """Accepts the hosting command of a hosting event (E2's ``HostingIntake`` port)."""

    __slots__ = ("_clock", "_command_deadline", "_ids", "_store")

    def __init__(
        self,
        *,
        store: HostingCommandStore,
        clock: Clock,
        ids: IdSource,
        command_deadline: timedelta,
    ) -> None:
        """Refuses a command_deadline that is not positive (``ValueError``). Calls no port."""
        if command_deadline <= timedelta(0):
            raise ValueError("hosting intake: command_deadline must be positive")
        self._store = store
        self._clock = clock
        self._ids = ids
        self._command_deadline = command_deadline

    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand:
        """Accept (or replay) the command of one Payment event. ``kind`` must be a hosting kind
        (``InvalidCommandTarget``); ``event_key`` follows the idempotency key rule
        (``InvalidIdempotencyKey``); both are checked before the store is called. No requester is
        recorded. The correlation ID is the event key when it fits, else the command ID."""
        event_key, project_id, kind = command.event_key, command.project_id, command.kind
        if not kind.is_hosting():
            raise InvalidCommandTarget(f"{kind} does not come from a Payment event")
        compute_request_fingerprint(  # validates the project ID and the key
            CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            )
        )
        command_id = self._ids.new_id()
        if command_id.int == 0:
            raise InvalidId("the ID source returned the nil UUID")
        now = self._clock.now()
        accepted = await self._store.accept_hosting_command(
            project_id=project_id,
            kind=kind,
            event_key=event_key,
            command_id=command_id,
            now=now,
            correlation_id=(
                event_key if len(event_key) <= MAX_CORRELATION_ID_LEN else str(command_id)
            ),
            deadline_at=now + self._command_deadline,
        )
        stored = accepted.command
        if stored.project_id != project_id or stored.kind is not kind:
            raise CorruptRow("hosting acceptance returned a command that is not the event's")
        return accepted


def retry_hosting_event_later(exc: BaseException) -> bool:
    """Whether a refusal of ``AcceptHostingEventCommand`` is temporary, so the Payment event is
    redelivered later rather than dropped: another command in flight, a route that is not stable,
    contention, a timeout or an unavailable dependency. A conflicting key, a malformed event, a
    project without a hosting row (OD-52) or any other error is permanent."""
    return any(is_error(exc, cls) for cls in _RETRY_LATER)
