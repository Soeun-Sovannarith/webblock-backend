"""The hosting events: Payment's "hosting entitlement ended" and "hosting entitlement restored"
events (decision 0014 sections 3 and 10.5; v1, decision 0022 E4), Project's "project deleted"
event (section 21 answer 4) and Project's "owner status changed" event (decision 0021 D9).

The Payment consumer decodes the event and sends it as ``AcceptHostingEventCommand`` through the
worker's command bus (decision 0025 CQ11) to the hosting intake (task F2's
``core.application.hosting.commands.accept_hosting_event.AcceptHostingEventHandler``), which
accepts a ``suspend_hosting`` or ``resume_hosting`` command whose idempotency key is the event key,
through a login of ``provisioning_hosting_intake`` alone (decision 0014 section 9). The deletion
consumer sends a ``suspend_hosting`` command with the fixed key ``project-deleted`` the same way.
The owner-status consumer sends ``AcceptOwnerStatusCommand`` to
``core.application.hosting.commands.accept_owner_status.AcceptOwnerStatusHandler`` on the same
login: a suspend or resume with the key ``owner-status:<version>``, or no command for a stale
version or a project it gives its first hosting row (both acked). Unlike the other two, it
dead-letters a permanent refusal (an invalid ID, version or sub-domain, a conflicting key): such an
event is wrong, and the dead-letter queue keeps it for an operator (decision 0022 Task 5).

Settling is that of every event that becomes a command
(``presentation.messaging.consumers.event_commands``): accepted or replayed is acked; a permanent
refusal is acked and logged by the Payment and deletion consumers and dead-lettered by the
owner-status consumer; a database that does not answer (unreachable, timed out, contended) is held
and requeued without limit and never counted; a transient refusal (``retry_hosting_event_later``) is
held, requeued and parked after ``max_retries``; any other error is held, requeued and dead-lettered
at its fifth counted failure; an invalid payload is dead-lettered.

QA-03: an event that finds its command ``failed`` (a replay) asks the hosting retry for a new
command (``<event_key>.retry-<n>``). The event is acked whatever the retry answers: a deferred or
failed retry is taken up by the periodic sweep (``HostingRetrySweeper``, in
``core.application.hosting.background.retry_sweeper``), and a spent budget is logged as an error for
an operator.

Audit (decision 0014 section 21 answer 3): every command an event creates is an audit event
(``accepted``) with the event as the actor, and every retry command one with the retry as the
actor; a replay is not a new event.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from provisioning.core.application import (
    AcceptedCommand,
    AcceptHostingEventCommand,
    AcceptOwnerStatusCommand,
    Clock,
)
from provisioning.core.application.hosting.background.hosting_job import DELETION_EVENT_KEY
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.hosting.background.retry_sweeper import audit_accepted, log_retry
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    retry_hosting_event_later,
)
from provisioning.core.application.hosting.ports import OwnerStatus
from provisioning.core.application.shared.audit import AuditActorKind, AuditLog
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.domain import CommandKind, CommandStatus
from provisioning.presentation.messaging.consumers.event_commands import (
    MAX_TRACKED_EVENT_KEYS,
    CommandEventConsumer,
    EventCommandSource,
)
from provisioning.presentation.messaging.consumers.messages import (
    Action,
    CommandSender,
    Delivery,
    EventConsumerConfig,
)

__all__ = [
    "MAX_TRACKED_EVENT_KEYS",
    "HostingEvent",
    "HostingEventDecoder",
    "HostingRetries",
    "OwnerStatusConsumer",
    "OwnerStatusDecoder",
    "OwnerStatusEvent",
    "PaymentEventConsumer",
    "ProjectDeletedEvent",
    "ProjectDeletionConsumer",
    "ProjectDeletionDecoder",
]


class HostingEvent(Protocol):
    """A decoded Payment event."""

    @property
    def event_key(self) -> str: ...

    @property
    def project_id(self) -> UUID: ...

    @property
    def kind(self) -> CommandKind: ...


class HostingEventDecoder(Protocol):
    """Implemented by ``presentation.messaging.EventCodecV1``; a refusal raises an error that
    holds no part of the body. ``message_id`` is the AMQP property."""

    def decode_hosting_event(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> HostingEvent: ...


class ProjectDeletedEvent(Protocol):
    """A decoded "project deleted" event."""

    @property
    def project_id(self) -> UUID: ...


class ProjectDeletionDecoder(Protocol):
    """Implemented by ``presentation.messaging.EventCodecV1``."""

    def decode_project_deleted(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> ProjectDeletedEvent: ...


class OwnerStatusEvent(Protocol):
    """A decoded "owner status changed" event."""

    @property
    def project_id(self) -> UUID: ...

    @property
    def status(self) -> OwnerStatus: ...

    @property
    def status_version(self) -> int: ...

    @property
    def subdomain(self) -> str: ...


class OwnerStatusDecoder(Protocol):
    """Implemented by ``presentation.messaging.EventCodecV1``."""

    def decode_owner_status_changed(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> OwnerStatusEvent: ...


class HostingRetries(Protocol):
    """``core.application.hosting.background.retry.HostingRetry``."""

    async def retry(self, project_id: UUID) -> RetryResult: ...


def _fields(event: HostingEvent) -> str:
    return f"event_key={event.event_key} project_id={event.project_id} kind={event.kind}"


def _deletion_fields(event: ProjectDeletedEvent) -> str:
    return f"event_key={DELETION_EVENT_KEY} project_id={event.project_id}"


def _owner_status_fields(event: OwnerStatusEvent) -> str:
    return (
        f"event_key=owner-status:{event.status_version} project_id={event.project_id} "
        f"status={event.status}"
    )


async def _retry_failed(
    retries: HostingRetries,
    logger: logging.Logger,
    audit: AuditLog,
    clock: Clock,
    project_id: UUID,
    fields: str,
) -> None:
    """Ask for the retry of a failed command; never raises (the sweep takes it up)."""
    try:
        result = await retries.retry(project_id)
    except Exception as exc:  # the periodic sweep retries; the event is acked either way
        logger.warning(
            "hosting retry failed; the sweep takes it up (%s error=%s)",
            fields,
            type(exc).__name__,
        )
        return
    log_retry(logger, result, audit, clock)


class PaymentEventConsumer(CommandEventConsumer[HostingEvent]):
    __slots__ = ()

    def __init__(
        self,
        config: EventConsumerConfig,
        *,
        decoder: HostingEventDecoder,
        commands: CommandSender,
        retries: HostingRetries,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
        retry_backoff: Backoff,
        max_retries: int,
    ) -> None:
        """``retry_backoff`` is the hold before a transient refusal is requeued, by its
        redelivery count; ``max_retries`` (at least 1) the redeliveries of one event key before
        it is parked (``InvalidConfig``). ``retries`` is asked when the event's command failed."""

        def decode(delivery: Delivery) -> HostingEvent:
            return decoder.decode_hosting_event(
                body=delivery.body,
                content_type=delivery.content_type,
                message_type=delivery.message_type,
                message_id=delivery.message_id,
            )

        async def accept(event: HostingEvent) -> AcceptedCommand:
            accepted = await commands.send(
                AcceptHostingEventCommand(
                    event_key=event.event_key, project_id=event.project_id, kind=event.kind
                )
            )
            audit_accepted(
                audit,
                clock,
                accepted,
                actor_kind=AuditActorKind.EVENT,
                actor="payment",
                correlation_id=event.event_key,
            )
            if accepted.replayed and accepted.command.status is CommandStatus.FAILED:
                await _retry_failed(retries, logger, audit, clock, event.project_id, _fields(event))
            return accepted

        super().__init__(
            config,
            source=EventCommandSource(
                decode=decode,
                accept=accept,
                retry_later=retry_hosting_event_later,
                event_key=lambda e: e.event_key,
                fields=_fields,
                event_label="payment event",
                command_label="hosting",
            ),
            logger=logger,
            retry_backoff=retry_backoff,
            max_retries=max_retries,
        )


class ProjectDeletionConsumer(CommandEventConsumer[ProjectDeletedEvent]):
    """Project's "project deleted" event: a ``suspend_hosting`` command with the fixed key
    ``project-deleted``, accepted by the hosting intake (not recorded as a Payment event)."""

    __slots__ = ()

    def __init__(
        self,
        config: EventConsumerConfig,
        *,
        decoder: ProjectDeletionDecoder,
        commands: CommandSender,
        retries: HostingRetries,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
        retry_backoff: Backoff,
        max_retries: int,
    ) -> None:
        """As ``PaymentEventConsumer``."""

        def decode(delivery: Delivery) -> ProjectDeletedEvent:
            return decoder.decode_project_deleted(
                body=delivery.body,
                content_type=delivery.content_type,
                message_type=delivery.message_type,
                message_id=delivery.message_id,
            )

        async def accept(event: ProjectDeletedEvent) -> AcceptedCommand:
            accepted = await commands.send(
                AcceptHostingEventCommand(
                    event_key=DELETION_EVENT_KEY,
                    project_id=event.project_id,
                    kind=CommandKind.SUSPEND_HOSTING,
                )
            )
            audit_accepted(
                audit,
                clock,
                accepted,
                actor_kind=AuditActorKind.EVENT,
                actor="project",
                correlation_id=f"{DELETION_EVENT_KEY}-{event.project_id}",
            )
            if accepted.replayed and accepted.command.status is CommandStatus.FAILED:
                await _retry_failed(
                    retries, logger, audit, clock, event.project_id, _deletion_fields(event)
                )
            return accepted

        super().__init__(
            config,
            source=EventCommandSource(
                decode=decode,
                accept=accept,
                retry_later=retry_hosting_event_later,
                event_key=lambda e: f"{DELETION_EVENT_KEY}-{e.project_id}",
                fields=_deletion_fields,
                event_label="project deleted event",
                command_label="deletion",
            ),
            logger=logger,
            retry_backoff=retry_backoff,
            max_retries=max_retries,
        )


class OwnerStatusConsumer(CommandEventConsumer[OwnerStatusEvent]):
    """Project's "owner status changed" event (decision 0021 D9): the owner-status intake accepts
    a ``suspend_hosting`` or ``resume_hosting`` with the key ``owner-status:<version>``, or no
    command (a version not newer than the row's, whatever its status, decision 0023 G17; or the
    project's first hosting row), both acked. A permanent refusal is dead-lettered."""

    __slots__ = ()

    def __init__(
        self,
        config: EventConsumerConfig,
        *,
        decoder: OwnerStatusDecoder,
        commands: CommandSender,
        retries: HostingRetries,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
        retry_backoff: Backoff,
        max_retries: int,
    ) -> None:
        """As ``PaymentEventConsumer``; ``audit`` records only the retry commands (the intake
        audits its own)."""

        def decode(delivery: Delivery) -> OwnerStatusEvent:
            return decoder.decode_owner_status_changed(
                body=delivery.body,
                content_type=delivery.content_type,
                message_type=delivery.message_type,
                message_id=delivery.message_id,
            )

        async def accept(event: OwnerStatusEvent) -> AcceptedCommand | None:
            result = await commands.send(
                AcceptOwnerStatusCommand(
                    project_id=event.project_id,
                    status=event.status,
                    version=event.status_version,
                    subdomain=event.subdomain,
                )
            )
            accepted = result.accepted
            if (
                accepted is not None
                and accepted.replayed
                and accepted.command.status is CommandStatus.FAILED
            ):
                await _retry_failed(
                    retries, logger, audit, clock, event.project_id, _owner_status_fields(event)
                )
            return accepted

        super().__init__(
            config,
            source=EventCommandSource(
                decode=decode,
                accept=accept,
                retry_later=retry_hosting_event_later,
                event_key=lambda e: f"owner-status:{e.status_version}-{e.project_id}",
                fields=_owner_status_fields,
                event_label="owner status event",
                command_label="owner status",
                refusal=Action.DEAD_LETTER,
            ),
            logger=logger,
            retry_backoff=retry_backoff,
            max_retries=max_retries,
        )
