"""The owner-status intake (decision 0021 D9; Notion "5 · Provisioning", "User suspension" and
"Out-of-order events").

Project's ``project.owner_status_changed.v1`` says that the owner of a project is ``suspended`` or
``active`` again, with a version that grows with each change. ``AcceptOwnerStatusHandler`` turns
one such event (``AcceptOwnerStatusCommand``) into a hosting command with reason ``user_suspended``
and the key ``owner-status:<version>``: ``suspend_hosting`` for a suspended owner,
``resume_hosting`` for an active one. The store does it in one transaction on the hosting intake's
login, hosting row first:

1. A project without a hosting row gets one (``ROW_INSERTED``): online with no reason, or offline
   with ``user_suspended``, holding the event's version and sub-domain. No command is created.
2. A version that is not newer than the row's ``owner_status_version`` changes nothing, whatever
   its status, so the newest event wins whatever the order of arrival (decision 0023 G17; Notion
   "an older event changes nothing"). It is a replay (``REPLAYED``) when the key holds this
   event's command (the same event again), else ``STALE``; a key held by the other kind is
   ``STALE``, never ``IdempotencyConflict``.
3. Otherwise the row's version becomes the event's and the command is accepted with the normal
   acceptance decision (``ACCEPTED``). A refusal (another command in flight, a route that is not
   stable, contention) rolls both back: nothing is stored and the event is retried later
   (``retry_hosting_event_later``).

The worker's ``presentation.messaging.consumers.payment_events.OwnerStatusConsumer`` calls it for
each event from ``provisioning.owner_status_changed.v1`` (decision 0022 Task 5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from provisioning.core.application.hosting.ports import (
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
    OwnerStatusStore,
)
from provisioning.core.application.ports.dependencies import Clock, IdSource
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.application.shared.errors import CorruptRow
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    InvalidId,
    compute_request_fingerprint,
    owner_status_key,
    validate_subdomain,
)


def owner_status_kind(status: OwnerStatus) -> CommandKind:
    """``suspend_hosting`` for a suspended owner, ``resume_hosting`` for an active one."""
    if status is OwnerStatus.SUSPENDED:
        return CommandKind.SUSPEND_HOSTING
    return CommandKind.RESUME_HOSTING


_ACTION_OF = {
    CommandKind.SUSPEND_HOSTING: AuditAction.SUSPEND_HOSTING,
    CommandKind.RESUME_HOSTING: AuditAction.RESUME_HOSTING,
}


@dataclass(frozen=True, slots=True)
class AcceptOwnerStatusCommand(cqrs.Command[OwnerStatusResult]):
    """One owner-status event (decision 0021 D9): the owner's status, the event's version and the
    project's sub-domain the event carries."""

    project_id: UUID
    status: OwnerStatus
    version: int
    subdomain: str


class AcceptOwnerStatusHandler:
    """Accepts the hosting command of one owner-status event."""

    __slots__ = ("_audit", "_clock", "_command_deadline", "_ids", "_logger", "_store")

    def __init__(
        self,
        *,
        store: OwnerStatusStore,
        clock: Clock,
        ids: IdSource,
        command_deadline: timedelta,
        audit: AuditLog,
        logger: logging.Logger,
    ) -> None:
        """Refuses a command_deadline that is not positive (``ValueError``). Calls no port."""
        if command_deadline <= timedelta(0):
            raise ValueError("owner status intake: command_deadline must be positive")
        self._store = store
        self._clock = clock
        self._ids = ids
        self._command_deadline = command_deadline
        self._audit = audit
        self._logger = logger

    async def handle(self, command: AcceptOwnerStatusCommand, /) -> OwnerStatusResult:
        """Apply one owner-status event. ``subdomain`` is the project's sub-domain the event
        carries (Notion); only a hosting row the event inserts uses it. A version outside 1 to
        2^53-1 is ``InvalidIdempotencyKey``, a nil project ID ``InvalidId`` and a sub-domain that
        is not a DNS label ``InvalidSubdomain``, all before the store is called. The correlation
        ID is the key. ``ACCEPTED`` writes the same audit event as a
        Payment event's command (actor kind event, actor ``project``); ``ROW_INSERTED`` and
        ``STALE`` are logged at info."""
        project_id, version, subdomain = command.project_id, command.version, command.subdomain
        status = OwnerStatus(command.status)
        key = owner_status_key(version)
        kind = owner_status_kind(status)
        compute_request_fingerprint(  # validates the project ID
            CommandRequest(project_id=project_id, kind=kind, release_id=None, idempotency_key=key)
        )
        validate_subdomain(subdomain)
        command_id = self._ids.new_id()
        if command_id.int == 0:
            raise InvalidId("the ID source returned the nil UUID")
        now = self._clock.now()
        result = await self._store.accept_owner_status(
            project_id=project_id,
            status=status,
            version=version,
            subdomain=subdomain,
            command_id=command_id,
            now=now,
            correlation_id=key,
            deadline_at=now + self._command_deadline,
        )
        _check(result, project_id, kind)
        if result.outcome is OwnerStatusOutcome.ACCEPTED and result.accepted is not None:
            stored = result.accepted.command
            self._audit.record(
                AuditEvent(
                    actor_kind=AuditActorKind.EVENT,
                    actor="project",
                    action=_ACTION_OF[stored.kind],
                    project_id=project_id,
                    release_id=None,
                    command_id=stored.id,
                    result=AuditResult.ACCEPTED,
                    failure_code=None,
                    at=self._clock.now(),
                    correlation_id=key,
                )
            )
        elif result.outcome in (OwnerStatusOutcome.ROW_INSERTED, OwnerStatusOutcome.STALE):
            self._logger.info(
                "owner status %s (project_id=%s version=%d)",
                result.outcome.value,
                project_id,
                version,
            )
        return result


def _check(result: OwnerStatusResult, project_id: UUID, kind: CommandKind) -> None:
    """Refuse, as ``CorruptRow``, a store answer that does not fit the event."""
    with_command = result.outcome in (OwnerStatusOutcome.ACCEPTED, OwnerStatusOutcome.REPLAYED)
    if (result.accepted is not None) != with_command:
        raise CorruptRow(f"owner status store answered {result.outcome} with the wrong command")
    if result.accepted is None:
        return
    command = result.accepted.command
    if command.project_id != project_id or command.kind is not kind:
        raise CorruptRow("owner status acceptance returned a command that is not the event's")
    if result.accepted.replayed != (result.outcome is OwnerStatusOutcome.REPLAYED):
        raise CorruptRow(f"owner status store answered {result.outcome} with a replay flag")
