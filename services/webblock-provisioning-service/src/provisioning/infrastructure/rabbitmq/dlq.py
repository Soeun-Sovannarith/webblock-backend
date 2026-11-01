"""The operator's dead-letter tool (task W6-E2; .claude/docs/runbooks/operator.md section 6): list
the messages parked in the platform's dead-letter queues, and move one back to the work queue it was
dead-lettered from.

- **Only the platform's own dead-letter queues.** The allow-list is the topology of
  ``rabbitmq.config`` (``PLATFORM_DEAD_LETTER_TOPOLOGIES``); any other queue is refused before the
  broker is reached. Payment's queue for the usage event is Payment's own and is not listed.
- **A listing keeps every message.** It takes at most ``limit`` messages with ``basic.get`` on one
  channel and closes the channel without acknowledging or rejecting them, so the broker returns
  them to the queue. On RabbitMQ 4 each such return counts towards the quorum queue's delivery
  limit (20 by default; the dead-letter queues set none), and at the limit a dead-letter queue,
  which has no dead-letter exchange, drops the message: proven on 4.3.6, where the 20th listing
  lost it. So a message taken ``REFRESH_AT`` times or more is refreshed: a copy, with the broker's
  counters removed and everything else (``x-death`` included) kept, is published to the same
  dead-letter queue through the default exchange with a confirm, and only then is the original
  acknowledged. The copy starts a new count at the tail of the queue. A crash between the two steps
  leaves a duplicate parked, never a loss. Only the queue, the message ID, the death record and a
  few envelope fields are read out, each checked against a narrow form; the body itself is never
  returned.
- **A re-drive removes the parked message only after the broker confirmed the copy.** The copy
  is moved only when the ``x-death`` record of the dead-letter queue's own work queue names an
  exchange and routing key of that queue's topology. It goes straight back to that work queue
  through the default exchange (routing key = the queue's name), never to the origin exchange,
  which for Project's and Payment's events would reach other services' queues; persistent and
  mandatory on a confirm channel. The parked message is then acknowledged and a round trip on
  the same channel proves the broker processed the acknowledgment. A crash between the confirm
  and the acknowledgment leaves the message parked as well as delivered: a later re-drive is a
  duplicate, which every consumer tolerates; a message is never lost.

No body, broker URL or broker error text is ever logged or put into an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage
from aio_pika.exceptions import DeliveryError, PublishError

from provisioning.core.domain import DomainError, parse_id
from provisioning.infrastructure.rabbitmq.config import PROVISIONING_QUEUES, QueueNames
from provisioning.infrastructure.rabbitmq.headers import field_table

# The dead-letter queues an operator may list and re-drive: those of the six queues Provisioning
# owns (decision 0022 E6), in this order.
PLATFORM_DEAD_LETTER_TOPOLOGIES: Final = PROVISIONING_QUEUES
# The most messages one listing or one re-drive's search takes from a queue.
MAX_LIST_LIMIT: Final = 500
MAX_SCAN_LIMIT: Final = 500
# A body larger than the consumers' limit is not parsed for the listing.
MAX_ENVELOPE_BYTES: Final = 65536
# A message taken this many times or more is refreshed before it is returned: half of RabbitMQ 4's
# default quorum delivery limit (20), at which a dead-letter queue drops a returned message.
REFRESH_AT: Final = 10
# The broker's delivery counters, dropped from a refreshed copy and from a re-driven one.
_COUNT_HEADERS: Final = frozenset({"x-delivery-count", "x-acquired-count"})
# The broker's own records of a dead-lettering and its counters, dropped from a re-driven copy.
_DEATH_HEADERS: Final = _COUNT_HEADERS | frozenset(
    {
        "x-death",
        "x-first-death-queue",
        "x-first-death-reason",
        "x-first-death-exchange",
        "x-last-death-queue",
        "x-last-death-reason",
        "x-last-death-exchange",
    }
)
_DEATH_REASONS: Final = frozenset({"rejected", "expired", "maxlen", "delivery_limit"})
_MESSAGE_ID: Final = re.compile(r"[A-Za-z0-9._:-]{1,100}")
_MESSAGE_TYPE: Final = re.compile(r"[a-z0-9_.]{1,128}")
_NAME: Final = re.compile(r"[\x21-\x7e]{1,255}")
_MAX_KEY_BYTES: Final = 255


class DeadLetterError(Exception):
    """A refused or failed listing or re-drive. The text is fixed; it never quotes a body."""


class QueueNotAllowed(DeadLetterError):
    """The queue is not one of the platform's dead-letter queues."""


class InvalidLimit(DeadLetterError, ValueError):
    """A listing limit or a search limit outside its bounds."""


class BrokerUnavailable(DeadLetterError):
    """The broker could not be reached, or a read on it failed."""


class PublishRefused(DeadLetterError):
    """The broker did not confirm a published copy: ``code`` is ``publish_nacked`` or
    ``publish_returned``."""

    def __init__(self, code: str) -> None:
        super().__init__(f"the broker did not confirm the copy ({code})")
        self.code = code


class MessageNotFound(DeadLetterError):
    """No message with this ID among the first messages searched."""


class _WithParked(DeadLetterError):
    def __init__(self, text: str, parked: ParkedMessage) -> None:
        super().__init__(text)
        self.parked = parked


class NoOrigin(_WithParked):
    """The message has no death record of the queue's work queue, or the exchange or routing key
    it names is not that queue's topology; nothing was published."""


class RedrivePublishFailed(_WithParked):
    """The copy was not confirmed; the message stays parked. ``code``: ``publish_nacked``,
    ``publish_returned`` or ``publish_unconfirmed``."""

    def __init__(self, parked: ParkedMessage, code: str) -> None:
        super().__init__(f"re-drive not confirmed ({code})", parked)
        self.code = code


class RemovalUnconfirmed(_WithParked):
    """The copy was confirmed but the removal of the parked message was not: it may still be
    parked, and a second re-drive would deliver a duplicate."""


@dataclass(frozen=True, slots=True)
class Fetched:
    """One message taken from a queue and not settled yet. ``tag`` identifies it in its session.
    The properties a re-driven copy keeps; ``expiration`` and ``user_id`` are not among them."""

    tag: int
    body: bytes
    message_id: str | None
    headers: Mapping[str, object]
    content_type: str | None
    content_encoding: str | None
    message_type: str | None
    correlation_id: str | None
    timestamp: datetime | None
    app_id: str | None


@dataclass(frozen=True, slots=True)
class Death:
    """The broker's record of the message's dead-lettering from its work queue; a value that is
    absent or not of the expected form is None."""

    reason: str | None
    count: int | None
    exchange: str | None
    routing_key: str | None


@dataclass(frozen=True, slots=True)
class Envelope:
    """The envelope fields a listing shows; a field that is absent or malformed is None."""

    message_type: str | None
    event_key: str | None  # event_key, generation_request_id or command_id
    project_id: UUID | None
    release_id: UUID | None
    command_id: UUID | None


@dataclass(frozen=True, slots=True)
class ParkedMessage:
    """What a listing shows of one parked message; never its body."""

    queue: str
    message_id: str | None
    death: Death | None
    envelope: Envelope


class DeadLetterSession(Protocol):
    """One channel on the broker. Leaving the session closes the channel, which returns every
    message it took and did not acknowledge to its queue."""

    async def fetch(self, queue: str) -> Fetched | None:
        """The next message of ``queue`` not yet taken in this session, or None when there is
        none (``BrokerUnavailable``)."""
        ...

    async def publish(self, exchange: str, routing_key: str, message: Fetched) -> None:
        """Publish a copy persistent and mandatory; returns once the broker confirmed it
        (``PublishRefused``, or any error when the confirm did not come)."""
        ...

    async def ack(self, message: Fetched) -> None:
        """Remove a taken message from its queue; returns once the broker processed it."""
        ...


type SessionFactory = Callable[[], AbstractAsyncContextManager[DeadLetterSession]]


def valid_message_id(value: str) -> bool:
    """1 to 100 ASCII letters, digits, '.', '_', ':' and '-': the IDs a re-drive can name."""
    return _MESSAGE_ID.fullmatch(value) is not None


def _text(value: object) -> str | None:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return value if isinstance(value, str) else None


def _name(value: object) -> str | None:
    text = _text(value)
    return text if text is not None and _NAME.fullmatch(text) else None


def _death(names: QueueNames, headers: Mapping[str, object]) -> Death | None:
    records = headers.get("x-death")
    if not isinstance(records, list):
        return None
    for record in records:
        if not isinstance(record, Mapping) or _text(record.get("queue")) != names.queue:
            continue
        reason = _text(record.get("reason"))
        count = record.get("count")
        keys = record.get("routing-keys")
        return Death(
            reason=reason if reason in _DEATH_REASONS else None,
            count=count if isinstance(count, int) and not isinstance(count, bool) else None,
            exchange=_name(record.get("exchange")),
            routing_key=_name(keys[0]) if isinstance(keys, list) and keys else None,
        )
    return None


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_id(value)
    except DomainError:
        return None


def _event_key(value: object) -> str | None:
    if isinstance(value, str) and _is_event_key(value):
        return value
    return None


def _is_event_key(text: str) -> bool:
    """Not empty, no outer white space, printable, and at most ``_MAX_KEY_BYTES`` in UTF-8."""
    return (
        bool(text)
        and text == text.strip()
        and text.isprintable()
        and len(text.encode("utf-8")) <= _MAX_KEY_BYTES
    )


_NO_ENVELOPE: Final = Envelope(None, None, None, None, None)


def _envelope(body: bytes) -> Envelope:
    if len(body) > MAX_ENVELOPE_BYTES:
        return _NO_ENVELOPE
    try:
        document = json.loads(body)
    except ValueError, RecursionError:
        return _NO_ENVELOPE
    if not isinstance(document, dict):
        return _NO_ENVELOPE
    message_type = document.get("message_type")
    command = _uuid(document.get("command_id"))
    request = _uuid(document.get("generation_request_id"))
    key = _event_key(document.get("event_key"))
    if key is None and request is not None:
        key = str(request)
    if key is None and command is not None:
        key = str(command)
    return Envelope(
        message_type=message_type
        if isinstance(message_type, str) and _MESSAGE_TYPE.fullmatch(message_type)
        else None,
        event_key=key,
        project_id=_uuid(document.get("project_id")),
        release_id=_uuid(document.get("release_id")),
        command_id=command,
    )


def summarize(names: QueueNames, message: Fetched) -> ParkedMessage:
    """What a listing shows of a message parked in ``names``' dead-letter queue."""
    message_id = message.message_id
    return ParkedMessage(
        queue=names.dead_letter_queue,
        message_id=message_id if message_id is not None and valid_message_id(message_id) else None,
        death=_death(names, message.headers),
        envelope=_envelope(message.body),
    )


def republished_headers(headers: Mapping[str, object]) -> dict[str, object]:
    """The headers of a re-driven copy: the original ones without the broker's death records."""
    return {k: v for k, v in headers.items() if k not in _DEATH_HEADERS}


def refreshed_headers(headers: Mapping[str, object]) -> dict[str, object]:
    """The headers of a refreshed copy: the original ones, the death records included, without the
    broker's delivery counters."""
    return {k: v for k, v in headers.items() if k not in _COUNT_HEADERS}


def times_taken(headers: Mapping[str, object]) -> int:
    """How often the broker says the message was delivered or acquired (the larger), or 0."""
    counts: list[int] = []
    for key in _COUNT_HEADERS:
        value = headers.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            counts.append(value)
    return max(counts, default=0)


def _identity(message: Fetched) -> tuple[str | None, bytes]:
    return message.message_id, hashlib.sha256(message.body).digest()


class _Refresher:
    """Refreshes, within one session, the messages taken ``REFRESH_AT`` times or more, and
    recognizes its own copies when the session reaches them at the tail."""

    __slots__ = ("_copies", "_queue", "_session")

    def __init__(self, session: DeadLetterSession, queue: str) -> None:
        self._session = session
        self._queue = queue
        self._copies: set[tuple[str | None, bytes]] = set()

    def is_own_copy(self, message: Fetched) -> bool:
        return _identity(message) in self._copies

    async def keep(self, message: Fetched) -> None:
        """Leave ``message`` parked: returned with the session, or refreshed when it was taken
        too often. A copy that is not confirmed leaves the original to be returned (its count is
        still below the limit); a removal that fails after the confirm leaves a duplicate."""
        if times_taken(message.headers) < REFRESH_AT:
            return
        copy = replace(message, headers=refreshed_headers(message.headers))
        try:
            await self._session.publish("", self._queue, copy)
        except Exception:  # not refreshed: returned with the session, still below the limit
            return
        self._copies.add(_identity(message))
        with contextlib.suppress(Exception):
            await self._session.ack(message)


class DeadLetterTool:
    """Lists and re-drives the messages of the allowed dead-letter queues."""

    __slots__ = ("_by_queue", "_open")

    def __init__(self, topologies: Sequence[QueueNames], open_session: SessionFactory) -> None:
        if not topologies:
            raise ValueError("the tool needs at least one allowed dead-letter queue")
        self._by_queue = {names.dead_letter_queue: names for names in topologies}
        self._open = open_session

    @property
    def queues(self) -> tuple[str, ...]:
        """The allowed dead-letter queues, in the order given."""
        return tuple(self._by_queue)

    def _names(self, queue: str) -> QueueNames:
        names = self._by_queue.get(queue)
        if names is None:
            raise QueueNotAllowed("not one of the platform's dead-letter queues")
        return names

    async def list(self, queue: str, limit: int) -> tuple[ParkedMessage, ...]:
        """At most ``limit`` (1 to ``MAX_LIST_LIMIT``) parked messages of ``queue``, from its
        head; every one stays parked (a message taken often is refreshed, see the module)."""
        names = self._names(queue)
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise InvalidLimit(f"the limit must be 1 to {MAX_LIST_LIMIT}")
        out: list[ParkedMessage] = []
        async with self._open() as session:
            refresher = _Refresher(session, queue)
            while len(out) < limit:
                message = await session.fetch(queue)
                if message is None or refresher.is_own_copy(message):
                    break
                out.append(summarize(names, message))
                await refresher.keep(message)
        return tuple(out)

    async def redrive(self, queue: str, message_id: str, *, scan_limit: int) -> ParkedMessage:
        """Move the first message of ``queue`` with ``message_id``, among its first
        ``scan_limit`` (1 to ``MAX_SCAN_LIMIT``), back to its origin; the other messages stay
        parked. Returns what a listing showed of it."""
        names = self._names(queue)
        if not valid_message_id(message_id):
            raise ValueError("the message ID must be 1 to 100 letters, digits, '.', '_', ':', '-'")
        if not 1 <= scan_limit <= MAX_SCAN_LIMIT:
            raise InvalidLimit(f"the search limit must be 1 to {MAX_SCAN_LIMIT}")
        async with self._open() as session:
            refresher = _Refresher(session, queue)
            for _ in range(scan_limit):
                message = await session.fetch(queue)
                if message is None or refresher.is_own_copy(message):
                    break
                if message.message_id == message_id:
                    try:
                        return await _move(session, names, message)
                    except NoOrigin, RedrivePublishFailed:
                        await refresher.keep(message)  # it stays parked; keep it from the limit
                        raise
                await refresher.keep(message)
        raise MessageNotFound("no parked message with this ID among those searched")


async def _move(session: DeadLetterSession, names: QueueNames, message: Fetched) -> ParkedMessage:
    parked = summarize(names, message)
    death = parked.death
    routing_key = None if death is None else death.routing_key
    if (
        death is None
        or death.exchange != names.exchange
        or routing_key is None
        or routing_key not in names.routing_keys
    ):
        raise NoOrigin("the message names no origin of this queue's topology", parked)
    copy = replace(message, headers=republished_headers(message.headers))
    try:
        # Straight to Provisioning's own queue through the default exchange: a re-drive to
        # the origin exchange (project.events, payment.events) would reach other services'
        # queues with Provisioning as their producer.
        await session.publish("", names.queue, copy)
    except PublishRefused as exc:
        raise RedrivePublishFailed(parked, exc.code) from None
    except Exception:  # no confirm came: a timeout or a closed channel; nothing removed
        raise RedrivePublishFailed(parked, "publish_unconfirmed") from None
    try:
        await session.ack(message)
    except Exception:  # the copy is delivered; the parked one may remain (a duplicate)
        raise RemovalUnconfirmed(
            "the copy was confirmed but its removal from the dead-letter queue was not",
            parked,
        ) from None
    return parked


# --- the AMQP session ---------------------------------------------------------------------------


class _AmqpSession:
    """``DeadLetterSession`` on one aio-pika confirm channel; each call bounded by
    ``call_timeout``."""

    __slots__ = ("_channel", "_held", "_tags", "_timeout")

    def __init__(self, channel: AbstractChannel, call_timeout: timedelta) -> None:
        self._channel = channel
        self._timeout = call_timeout.total_seconds()
        self._held: dict[int, tuple[str, AbstractIncomingMessage]] = {}
        self._tags = 0

    async def fetch(self, queue: str) -> Fetched | None:
        try:
            async with asyncio.timeout(self._timeout):
                target = await self._channel.get_queue(queue, ensure=False)
                message = await target.get(no_ack=False, fail=False, timeout=self._timeout)
        except Exception:  # a closed channel, a missing queue or a timeout
            raise BrokerUnavailable("reading the dead-letter queue failed") from None
        if message is None:
            return None
        self._tags += 1
        tag = self._tags
        self._held[tag] = (queue, message)
        return Fetched(
            tag=tag,
            body=message.body,
            message_id=message.message_id,
            headers=dict(message.headers or {}),
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            message_type=message.type,
            correlation_id=message.correlation_id,
            timestamp=message.timestamp,
            app_id=message.app_id,
        )

    async def publish(self, exchange: str, routing_key: str, message: Fetched) -> None:
        copy = aio_pika.Message(
            message.body,
            headers=field_table(message.headers),
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            correlation_id=message.correlation_id,
            message_id=message.message_id,
            timestamp=message.timestamp,
            type=message.message_type,
            app_id=message.app_id,
        )
        async with asyncio.timeout(self._timeout):
            target = (
                self._channel.default_exchange
                if exchange == ""
                else await self._channel.get_exchange(exchange, ensure=False)
            )
            try:
                await target.publish(copy, routing_key, mandatory=True)
            except PublishError:
                raise PublishRefused("publish_returned") from None
            except DeliveryError:
                raise PublishRefused("publish_nacked") from None

    async def ack(self, message: Fetched) -> None:
        queue, incoming = self._held.pop(message.tag)
        async with asyncio.timeout(self._timeout):
            await incoming.ack()
            # A synchronous round trip on the same channel: the broker answers it only after it
            # processed the acknowledgment sent before it.
            await self._channel.declare_queue(queue, passive=True)


def amqp_sessions(
    url: str, *, dial_timeout: timedelta, call_timeout: timedelta, connection_name: str
) -> SessionFactory:
    """A factory of sessions, each on its own connection and confirm channel. ``url`` holds the
    credentials; it is never logged or put into an error."""

    @asynccontextmanager
    async def session() -> AsyncIterator[DeadLetterSession]:
        dial = dial_timeout.total_seconds()
        try:
            async with asyncio.timeout(dial):
                connection = await aio_pika.connect(url, timeout=dial, name=connection_name)
        except Exception:  # the error text may quote the URL
            raise BrokerUnavailable("the broker could not be reached") from None
        try:
            try:
                async with asyncio.timeout(dial):
                    channel = await connection.channel(
                        publisher_confirms=True, on_return_raises=True
                    )
            except Exception:
                raise BrokerUnavailable("opening a channel failed") from None
            yield _AmqpSession(channel, call_timeout)
        finally:
            # Closing the connection returns every message taken and not acknowledged.
            # A failed close is left to the broker, which returns the messages when it notices.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(dial):
                    await connection.close()

    return session
