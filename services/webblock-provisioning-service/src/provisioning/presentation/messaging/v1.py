"""The v1 event codec (decision 0022 E4, E5 and E11).

Provisioning's events follow the accepted envelope of the Notion page "Web Block architecture"
4.1: snake_case bodies, ``message_type`` of the form ``<owner>.<name>.v1``, and an AMQP ``type``
property and ``message_id`` that equal the body's. The schemas are the files of
``src/contracts/shared/events``, embedded in ``schemas.py``; ``models.py`` is their typed view. An
incoming event is checked in this order: size, content type, AMQP type, JSON, the typed model (it
replaces the runtime schema check), the AMQP message ID against the body's, then the domain; each
refusal is a ``MessageRejected``. The message ID is checked and never returned (E5): the caller
logs it. The encoders build what the developer tool and the usage publisher send; each runs its
own decoder and refuses with ``ValueError`` what the decoder would refuse.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from pydantic import ValidationError

from provisioning.core.application.hosting.ports import OwnerStatus
from provisioning.core.application.usage.ports import usage_event_key
from provisioning.core.domain import (
    CommandKind,
    DomainError,
    SourcePublishedFields,
    parse_id,
    parse_source_published,
    validate_idempotency_key,
    validate_subdomain,
)
from provisioning.presentation.messaging.codec import (
    CONTENT_TYPE_JSON,
    EncodedMessage,
    MessageRejected,
    Reason,
    check_envelope,
    rejection,
    rfc3339,
)
from provisioning.presentation.messaging.models import (
    EnvelopeV1,
    HostingEntitlementEndedV1,
    HostingEntitlementRestoredV1,
    OwnerStatusChangedV1,
    ProjectDeletedV1,
    PublishCompletedV1,
    SourcePublishedV1,
    error_locations,
)

SOURCE_PUBLISHED_V1 = "project.source_published.v1"
PROJECT_DELETED_V1 = "project.project_deleted.v1"
OWNER_STATUS_CHANGED_V1 = "project.owner_status_changed.v1"
HOSTING_ENTITLEMENT_ENDED_V1 = "payment.hosting_entitlement_ended.v1"
HOSTING_ENTITLEMENT_RESTORED_V1 = "payment.hosting_entitlement_restored.v1"
PUBLISH_COMPLETED_V1 = "provisioning.publish_completed.v1"

SCHEMA_VERSION_V1 = 1
# The correlation ID pattern of the envelope; an event key outside it falls back to this value.
_CORRELATION = re.compile(r"[A-Za-z0-9._:-]{1,128}")
FALLBACK_HOSTING_CORRELATION_ID = "payment-event"

_HOSTING_TYPES = frozenset({HOSTING_ENTITLEMENT_ENDED_V1, HOSTING_ENTITLEMENT_RESTORED_V1})
_KIND_OF = {
    HOSTING_ENTITLEMENT_ENDED_V1: CommandKind.SUSPEND_HOSTING,
    HOSTING_ENTITLEMENT_RESTORED_V1: CommandKind.RESUME_HOSTING,
}
_TYPE_OF = {kind: message_type for message_type, kind in _KIND_OF.items()}
_Event = TypeVar("_Event", bound=EnvelopeV1)


@dataclass(frozen=True, slots=True)
class HostingEvent:
    """A decoded Payment event: its key (the hosting command's idempotency key), the project and
    the hosting command kind it asks for."""

    event_key: str
    project_id: UUID
    kind: CommandKind


@dataclass(frozen=True, slots=True)
class ProjectDeleted:
    """A decoded "project deleted" event: the project only."""

    project_id: UUID


@dataclass(frozen=True, slots=True)
class PublishCompleted:
    """A decoded "publish completed" usage event; ``event_key`` is the release's key. Its decoder
    stands in for Payment's consumer in the tests and the live checks."""

    event_key: str
    project_id: UUID
    release_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OwnerStatusChanged:
    """A decoded owner-status event: the owner's status, its version and the site's sub-domain."""

    project_id: UUID
    status: OwnerStatus
    status_version: int
    subdomain: str


class EventCodecV1:
    """Decodes and encodes the six v1 events; one instance is built at start."""

    __slots__ = ("_max_body",)

    def __init__(self, *, max_body_bytes: int) -> None:
        """``max_body_bytes`` is positive (``ValueError``)."""
        if max_body_bytes <= 0:
            raise ValueError("messaging: the maximum body size must be positive")
        self._max_body = max_body_bytes

    def _open(
        self,
        body: bytes,
        model: type[_Event],
        expected: frozenset[str],
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> _Event:
        """Envelope, the typed model, then the AMQP message ID against the body's: a missing AMQP
        message ID is a mismatch too (decision 0022 E5)."""
        check_envelope(
            body,
            max_body=self._max_body,
            content_type=content_type,
            message_type=message_type,
            expected_types=expected,
        )
        try:
            event = model.model_validate_json(body)
        except ValidationError as exc:
            raise rejection(body, Reason.SCHEMA_INVALID, error_locations(exc)) from None
        if message_id is None or message_id != str(event.message_id):
            raise rejection(body, Reason.ENVELOPE_MISMATCH) from None
        return event

    def decode_source_published(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> SourcePublishedFields:
        """The four fields Provisioning reads. The optional sub-domain is checked, not returned."""
        event = self._open(
            body,
            SourcePublishedV1,
            frozenset({SOURCE_PUBLISHED_V1}),
            content_type,
            message_type,
            message_id,
        )
        fields = SourcePublishedFields(
            project_id=str(event.project_id),
            generation_request_id=str(event.generation_request_id),
            repository_id=event.repository_id,
            git_commit_sha=event.git_commit_sha,
        )
        try:
            parse_source_published(fields)
            if event.subdomain is not None:
                validate_subdomain(event.subdomain)
        except DomainError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        return fields

    def decode_project_deleted(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> ProjectDeleted:
        event = self._open(
            body,
            ProjectDeletedV1,
            frozenset({PROJECT_DELETED_V1}),
            content_type,
            message_type,
            message_id,
        )
        try:
            project_id = parse_id(str(event.project_id))
        except DomainError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        return ProjectDeleted(project_id=project_id)

    def decode_owner_status_changed(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> OwnerStatusChanged:
        event = self._open(
            body,
            OwnerStatusChangedV1,
            frozenset({OWNER_STATUS_CHANGED_V1}),
            content_type,
            message_type,
            message_id,
        )
        try:
            project_id = parse_id(str(event.project_id))
            status = OwnerStatus(event.status)
            validate_subdomain(event.subdomain)
        except DomainError, ValueError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        return OwnerStatusChanged(
            project_id=project_id,
            status=status,
            status_version=event.status_version,
            subdomain=event.subdomain,
        )

    def decode_hosting_event(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> HostingEvent:
        """An entitlement event; the AMQP type picks the model and the kind (ended: suspend,
        restored: resume). The ended event's ``reason`` is checked by the model and ignored."""
        model: type[HostingEntitlementEndedV1 | HostingEntitlementRestoredV1] = (
            HostingEntitlementEndedV1
            if message_type == HOSTING_ENTITLEMENT_ENDED_V1
            else HostingEntitlementRestoredV1
        )
        event = self._open(body, model, _HOSTING_TYPES, content_type, message_type, message_id)
        kind = _KIND_OF[str(message_type)]  # _open refused every other type
        try:
            validate_idempotency_key(event.event_key)
            project_id = parse_id(str(event.project_id))
        except DomainError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        return HostingEvent(event_key=event.event_key, project_id=project_id, kind=kind)

    def decode_publish_completed(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> PublishCompleted:
        """The usage event; a key that is not the release's key is ``DOMAIN_INVALID``."""
        event = self._open(
            body,
            PublishCompletedV1,
            frozenset({PUBLISH_COMPLETED_V1}),
            content_type,
            message_type,
            message_id,
        )
        try:
            project_id = parse_id(str(event.project_id))
            release_id = parse_id(str(event.release_id))
        except DomainError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        if event.event_key != usage_event_key(release_id):
            raise rejection(body, Reason.DOMAIN_INVALID) from None
        return PublishCompleted(
            event_key=event.event_key,
            project_id=project_id,
            release_id=release_id,
            occurred_at=event.occurred_at.astimezone(UTC),
        )

    def encode_source_published(
        self,
        *,
        message_id: UUID,
        project_id: UUID,
        generation_request_id: UUID,
        repository_id: int,
        git_commit_sha: str,
        subdomain: str | None,
        occurred_at: datetime,
    ) -> EncodedMessage:
        """The correlation ID is the generation request; ``subdomain`` is left out when None."""
        message = _encoded(
            "source published",
            SourcePublishedV1,
            SOURCE_PUBLISHED_V1,
            message_id,
            occurred_at,
            correlation_id=str(generation_request_id),
            project_id=project_id,
            generation_request_id=generation_request_id,
            repository_id=repository_id,
            git_commit_sha=git_commit_sha,
            subdomain=subdomain,
        )
        self._verify("source published", message, self.decode_source_published)
        return message

    def encode_project_deleted(
        self, *, message_id: UUID, project_id: UUID, occurred_at: datetime
    ) -> EncodedMessage:
        """The correlation ID is the project."""
        message = _encoded(
            "project deleted",
            ProjectDeletedV1,
            PROJECT_DELETED_V1,
            message_id,
            occurred_at,
            correlation_id=str(project_id),
            project_id=project_id,
        )
        self._verify("project deleted", message, self.decode_project_deleted)
        return message

    def encode_owner_status_changed(
        self,
        *,
        message_id: UUID,
        project_id: UUID,
        status: OwnerStatus,
        status_version: int,
        subdomain: str,
        occurred_at: datetime,
    ) -> EncodedMessage:
        """The correlation ID is ``owner-status:<status_version>``."""
        message = _encoded(
            "owner status changed",
            OwnerStatusChangedV1,
            OWNER_STATUS_CHANGED_V1,
            message_id,
            occurred_at,
            correlation_id=f"owner-status:{status_version}",
            project_id=project_id,
            status=status.value,
            status_version=status_version,
            subdomain=subdomain,
        )
        self._verify("owner status changed", message, self.decode_owner_status_changed)
        return message

    def encode_hosting_event(
        self,
        *,
        message_id: UUID,
        event_key: str,
        project_id: UUID,
        kind: CommandKind,
        reason: str | None,
        occurred_at: datetime,
    ) -> EncodedMessage:
        """suspend_hosting is "entitlement ended" (it carries ``reason``, which the model
        requires), resume_hosting "entitlement restored" (``reason`` is not written). The
        correlation ID is the event key, or ``payment-event`` when the key does not fit the
        correlation pattern. Another kind is ``ValueError``."""
        if _TYPE_OF.get(kind) is None:
            raise ValueError(f"{kind} is not a hosting kind")
        correlation = (
            event_key if _CORRELATION.fullmatch(event_key) else FALLBACK_HOSTING_CORRELATION_ID
        )
        if kind is CommandKind.SUSPEND_HOSTING:
            message = _encoded(
                "hosting event",
                HostingEntitlementEndedV1,
                HOSTING_ENTITLEMENT_ENDED_V1,
                message_id,
                occurred_at,
                correlation_id=correlation,
                project_id=project_id,
                event_key=event_key,
                reason=reason,
            )
        else:
            message = _encoded(
                "hosting event",
                HostingEntitlementRestoredV1,
                HOSTING_ENTITLEMENT_RESTORED_V1,
                message_id,
                occurred_at,
                correlation_id=correlation,
                project_id=project_id,
                event_key=event_key,
            )
        self._verify("hosting event", message, self.decode_hosting_event)
        return message

    def encode_publish_completed(
        self, *, message_id: UUID, project_id: UUID, release_id: UUID, occurred_at: datetime
    ) -> EncodedMessage:
        """The usage event of ``release_id``; the correlation ID is the release."""
        message = _encoded(
            "publish completed",
            PublishCompletedV1,
            PUBLISH_COMPLETED_V1,
            message_id,
            occurred_at,
            correlation_id=str(release_id),
            project_id=project_id,
            event_key=usage_event_key(release_id),
            release_id=release_id,
        )
        self._verify("publish completed", message, self.decode_publish_completed)
        return message

    @staticmethod
    def _verify(name: str, message: EncodedMessage, decode: Callable[..., object]) -> None:
        """Run the message through its own decoder; a refusal is ``ValueError``."""
        try:
            decode(
                body=message.body,
                content_type=CONTENT_TYPE_JSON,
                message_type=message.message_type,
                message_id=message.message_id,
            )
        except MessageRejected as exc:
            raise ValueError(f"invalid {name} event: {exc.reason}") from None


def _encoded(
    name: str,
    model: type[EnvelopeV1],
    message_type: str,
    message_id: UUID,
    occurred_at: datetime,
    *,
    correlation_id: str,
    **fields: object,
) -> EncodedMessage:
    """Build the typed event and its message; a field the model refuses is ``ValueError``. The
    envelope fields come first in the body, and an absent optional field is left out."""
    try:
        event = model.model_validate(
            {
                "schema_version": SCHEMA_VERSION_V1,
                "message_id": message_id,
                "message_type": message_type,
                "occurred_at": rfc3339(occurred_at),
                "correlation_id": correlation_id,
                **fields,
            }
        )
    except ValidationError:
        raise ValueError(f"invalid {name} event: {Reason.SCHEMA_INVALID}") from None
    return EncodedMessage(
        body=event.model_dump_json(exclude_none=True).encode(),
        content_type=CONTENT_TYPE_JSON,
        message_type=message_type,
        message_id=str(message_id),
        correlation_id=correlation_id,
        timestamp=occurred_at,
    )
