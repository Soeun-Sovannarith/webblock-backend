"""The command-job codec and publisher (decision 0005 section 6).

The internal command job keeps its old name (decision 0013 section 6): ``fencing_generation`` on
the wire is the command number inside the package. Since task RN this module is the only place where
an old name appears. It holds no workflow logic and no broker library type.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.core.application import InvalidCorrelationId, WorkRef
from provisioning.core.domain import DomainError, InvalidId, parse_command_kind, parse_id
from provisioning.presentation.messaging.schema import JsonSchema, parse_json
from provisioning.presentation.messaging.schemas import COMMAND_JOB_SCHEMA

# The only content type of a message this service sends or accepts.
CONTENT_TYPE_JSON = "application/json"
# The message_type of a command job and the AMQP type property it must equal (draft name, OD-26).
COMMAND_JOB_MESSAGE_TYPE = "provisioning.command_job"
# The schema_version of src/contracts/events/command-job.v1.schema.json.
COMMAND_JOB_SCHEMA_VERSION = 1
# The width of a correlation ID in the dispatch row and the envelope.
MAX_CORRELATION_ID_LEN = 128


class Reason(StrEnum):
    """Why an incoming message was refused, in the order the checks run."""

    TOO_LARGE = "too_large"
    CONTENT_TYPE = "content_type"
    MESSAGE_TYPE = "unknown_message_type"
    NOT_JSON = "not_json"
    SCHEMA_INVALID = "schema_invalid"
    ENVELOPE_MISMATCH = "envelope_mismatch"
    DOMAIN_INVALID = "domain_invalid"


class MessageRejected(Exception):
    """A refused incoming message. It carries the reason, the body size, the
    SHA-256 of the body and, for a schema refusal, the failed schema keyword locations; never any
    part of the body, and no chained cause, because a parser's or the domain's message can quote
    the input. The consumer dead-letters the delivery and logs this error."""

    def __init__(
        self, reason: Reason, size: int, body_sha256: str, schema_locations: tuple[str, ...] = ()
    ) -> None:
        text = f"message rejected: reason={reason} size={size} sha256={body_sha256}"
        if schema_locations:
            text += " schema=" + ",".join(schema_locations)
        super().__init__(text)
        self._reason = reason
        self._size = size
        self._sha = body_sha256
        self._locations = schema_locations

    @property
    def reason(self) -> Reason:
        return self._reason

    @property
    def size(self) -> int:
        return self._size

    @property
    def body_sha256(self) -> str:
        return self._sha

    @property
    def schema_locations(self) -> tuple[str, ...]:
        return self._locations


def rejection(body: bytes, reason: Reason, locations: tuple[str, ...] = ()) -> MessageRejected:
    """The refusal of ``body`` for ``reason``; raise it ``from None``."""
    return MessageRejected(reason, len(body), hashlib.sha256(body).hexdigest(), locations)


def check_envelope(
    body: bytes,
    *,
    max_body: int,
    content_type: str | None,
    message_type: str | None,
    expected_types: frozenset[str],
) -> dict[str, object]:
    """The checks every incoming message shares, in order: the body size, the content type, the
    AMQP type property against ``expected_types``, and one JSON object (``MessageRejected``). The
    schema check follows in the caller."""
    if len(body) > max_body:
        raise rejection(body, Reason.TOO_LARGE) from None
    if content_type != CONTENT_TYPE_JSON:
        raise rejection(body, Reason.CONTENT_TYPE) from None
    if message_type not in expected_types:
        raise rejection(body, Reason.MESSAGE_TYPE) from None
    try:
        doc = parse_json(body)
    except ValueError:
        raise rejection(body, Reason.NOT_JSON) from None
    if not isinstance(doc, dict):
        # Every message schema is an object schema; this is the location it would report.
        raise rejection(body, Reason.SCHEMA_INVALID, ("/type",)) from None
    return doc


def check_schema(body: bytes, schema: JsonSchema, doc: dict[str, object]) -> None:
    """``MessageRejected`` with the failed keyword locations when ``doc`` fails ``schema``."""
    locations = schema.failures(doc)
    if locations:
        raise rejection(body, Reason.SCHEMA_INVALID, locations) from None


def rfc3339(at: datetime) -> str:
    """``at`` as an RFC 3339 UTC time with microseconds; a naive time is ``ValueError``."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("a message time needs a timezone")
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class EncodedMessage:
    """A message ready for the broker: the JSON body and the AMQP properties this service sets."""

    body: bytes
    content_type: str
    message_type: str
    message_id: str
    correlation_id: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class CommandJob:
    """The message of type provisioning.command_job, field for field the properties of
    src/contracts/events/command-job.v1.schema.json (wire names)."""

    schema_version: int
    message_id: str
    message_type: str
    occurred_at: str
    correlation_id: str
    project_id: str
    command_id: str
    fencing_generation: int
    kind: str
    release_id: str | None = None

    def ref(self) -> WorkRef:
        """Parse the identifiers through the domain: ``InvalidId``, ``UnknownEnumValue``,
        ``InvalidCommandNumber`` or ``InvalidCommandTarget``. The wire's fencing_generation is the
        command number."""
        ref = WorkRef(
            command_id=parse_id(self.command_id),
            project_id=parse_id(self.project_id),
            kind=parse_command_kind(self.kind),
            command_number=self.fencing_generation,
            release_id=None if self.release_id is None else parse_id(self.release_id),
        )
        ref.validate()
        return ref

    def to_wire(self) -> dict[str, object]:
        """The JSON object; ``release_id`` only when set."""
        doc: dict[str, object] = {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "message_type": self.message_type,
            "occurred_at": self.occurred_at,
            "correlation_id": self.correlation_id,
            "project_id": self.project_id,
            "command_id": self.command_id,
            "fencing_generation": self.fencing_generation,
            "kind": self.kind,
        }
        if self.release_id is not None:
            doc["release_id"] = self.release_id
        return doc


def new_command_job(
    *, message_id: UUID, ref: WorkRef, correlation_id: str, occurred_at: datetime
) -> CommandJob:
    """The job for one work reference: a fresh message ID for every publish (the deduplication key
    is the command and its durable state, never the message ID), the reference's identifiers, the
    dispatch row's correlation ID and the publish time. Refused: a nil message ID (``InvalidId``),
    an invalid reference (``WorkRef.validate``), a correlation ID that is not 1 to 128 characters
    (``InvalidCorrelationId``), a naive time (``ValueError``)."""
    if message_id.int == 0:
        raise InvalidId("command job: a message ID is required")
    ref.validate()
    if not 1 <= len(correlation_id) <= MAX_CORRELATION_ID_LEN:
        raise InvalidCorrelationId(f"correlation ID must be 1 to {MAX_CORRELATION_ID_LEN} long")
    return CommandJob(
        schema_version=COMMAND_JOB_SCHEMA_VERSION,
        message_id=str(message_id),
        message_type=COMMAND_JOB_MESSAGE_TYPE,
        occurred_at=rfc3339(occurred_at),
        correlation_id=correlation_id,
        project_id=str(ref.project_id),
        command_id=str(ref.command_id),
        fencing_generation=ref.command_number,
        kind=str(ref.kind),
        release_id=None if ref.release_id is None else str(ref.release_id),
    )


_COMMAND_JOB_TYPES = frozenset({COMMAND_JOB_MESSAGE_TYPE})


class CommandJobCodec:
    """Validates and parses incoming command jobs and encodes outgoing ones; one instance is built
    at start and shared."""

    __slots__ = ("_max_body", "_schema")

    def __init__(self, *, max_body_bytes: int) -> None:
        """``max_body_bytes`` is MESSAGING_MAX_BODY_BYTES, positive (``ValueError``)."""
        if max_body_bytes <= 0:
            raise ValueError("messaging: the maximum body size must be positive")
        self._max_body = max_body_bytes
        self._schema = JsonSchema(json.loads(COMMAND_JOB_SCHEMA))

    def decode_command_job(
        self, *, body: bytes, content_type: str | None, message_type: str | None
    ) -> WorkRef:
        """Check, in order: the body size, the content type, the AMQP type, one JSON value, the
        schema, the body's message_type, and the domain parse of the identifiers. Any failure is
        ``MessageRejected``."""
        doc = check_envelope(
            body,
            max_body=self._max_body,
            content_type=content_type,
            message_type=message_type,
            expected_types=_COMMAND_JOB_TYPES,
        )
        check_schema(body, self._schema, doc)
        if doc["message_type"] != message_type:
            raise rejection(body, Reason.MESSAGE_TYPE) from None
        try:
            return _job_of(doc).ref()
        except DomainError, TypeError:
            raise rejection(body, Reason.DOMAIN_INVALID) from None

    def encode_command_job(self, job: CommandJob) -> EncodedMessage:
        """Encode a job and check it against the same schema, the domain and the body limit
        before it may be published (``ValueError`` otherwise; a programming error)."""
        try:
            job.ref()
        except DomainError:
            raise ValueError("outgoing command job: invalid identifiers") from None
        body = json.dumps(job.to_wire(), separators=(",", ":")).encode()
        if len(body) > self._max_body:
            raise ValueError(f"outgoing command job: {len(body)} bytes exceed the limit")
        locations = self._schema.failures(parse_json(body))
        if locations:
            raise ValueError("outgoing command job: schema " + ",".join(locations))
        return EncodedMessage(
            body=body,
            content_type=CONTENT_TYPE_JSON,
            message_type=job.message_type,
            message_id=job.message_id,
            correlation_id=job.correlation_id,
            timestamp=datetime.fromisoformat(job.occurred_at),
        )


def _job_of(doc: dict[str, object]) -> CommandJob:
    """The job of a document that passed the schema. Each field's type is checked again, so a
    wrong type is a ``TypeError`` even if a schema rule is missing, never coerced to text."""
    release = doc.get("release_id")
    number = doc["fencing_generation"]
    if not isinstance(number, int) or (release is not None and not isinstance(release, str)):
        raise TypeError("command job fields")
    return CommandJob(
        schema_version=COMMAND_JOB_SCHEMA_VERSION,
        message_id=_text(doc, "message_id"),
        message_type=_text(doc, "message_type"),
        occurred_at=_text(doc, "occurred_at"),
        correlation_id=_text(doc, "correlation_id"),
        project_id=_text(doc, "project_id"),
        command_id=_text(doc, "command_id"),
        fencing_generation=number,
        kind=_text(doc, "kind"),
        release_id=release,
    )


def _text(doc: dict[str, object], name: str) -> str:
    """The string field ``name`` of ``doc``; ``TypeError`` when it is missing or no string."""
    value = doc.get(name)
    if not isinstance(value, str):
        raise TypeError(f"command job field {name} is no string")
    return value


class RawPublisher(Protocol):
    """Publishes one message persistent and mandatory with publisher confirms; returns only when
    the broker confirmed it and did not return it. Implemented by the RabbitMQ adapter."""

    async def publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        content_type: str,
        message_type: str,
        message_id: str,
        correlation_id: str,
        timestamp: datetime,
    ) -> None: ...


async def publish_encoded(
    raw: RawPublisher, exchange: str, routing_key: str, message: EncodedMessage
) -> None:
    """Publish an encoded message through ``raw``."""
    await raw.publish(
        exchange=exchange,
        routing_key=routing_key,
        body=message.body,
        content_type=message.content_type,
        message_type=message.message_type,
        message_id=message.message_id,
        correlation_id=message.correlation_id,
        timestamp=message.timestamp,
    )


class CommandJobPublisher:
    """Builds, encodes and publishes command jobs to the command exchange; the dispatcher's
    publisher port. An invalid job is never published."""

    __slots__ = ("_codec", "_exchange", "_raw", "_routing_key")

    def __init__(
        self, *, codec: CommandJobCodec, raw: RawPublisher, exchange: str, routing_key: str
    ) -> None:
        self._codec = codec
        self._raw = raw
        self._exchange = exchange
        self._routing_key = routing_key

    async def publish_command_job(
        self, *, message_id: UUID, ref: WorkRef, correlation_id: str, occurred_at: datetime
    ) -> None:
        job = new_command_job(
            message_id=message_id, ref=ref, correlation_id=correlation_id, occurred_at=occurred_at
        )
        await publish_encoded(
            self._raw, self._exchange, self._routing_key, self._codec.encode_command_job(job)
        )
