"""What every consumer loop shares (decision 0005 section 4): the
delivery and stream ports the RabbitMQ adapter implements, the broker action a decision maps to,
the kinds of failure and the hold before a requeue, the per-message bound of 5 tries (decision
0023 G15) and the bounded handler pool.

The ports are defined by their consumer, here, and the adapter satisfies them by structure; this
package imports no broker library.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from provisioning.core.application import Contention, StoreUnreachable, Timeout
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import is_error


class CommandSender(Protocol):
    """Where a consumer sends the command of an event (decision 0025 CQ11, TR-54): the
    process's ``CommandBus``, or a host's wrapper around it."""

    async def send[R](self, command: cqrs.Command[R], /) -> R: ...


class Delivery(Protocol):
    """One message received with manual acknowledgment. A consumer settles every delivery exactly
    once, and only after the database decision it depends on has returned. The body is never
    logged."""

    @property
    def body(self) -> bytes: ...

    @property
    def content_type(self) -> str | None: ...

    @property
    def message_type(self) -> str | None:
        """The AMQP type property."""
        ...

    @property
    def message_id(self) -> str | None:
        """The AMQP message ID property; a v1 event's must equal its body's (decision 0022
        E5)."""
        ...

    @property
    def redelivered(self) -> bool: ...

    def traced(self) -> AbstractContextManager[None]:
        """The consumer span of this delivery for the block, continuing the trace its
        ``traceparent`` header names; a no-op when tracing is off (decision 0022 E9)."""
        ...

    async def ack(self) -> None: ...

    async def dead_letter(self) -> None:
        """Reject without requeue: the queue dead-letters the message (counts towards the
        broker's delivery limit)."""
        ...

    async def requeue(self) -> None:
        """Negative acknowledgment with requeue: does not count towards the broker's delivery
        limit (RabbitMQ 4)."""
        ...


class DeliveryStream(Protocol):
    """A consumer's source of deliveries. The adapter re-subscribes by itself when the broker
    cancels the consumer or the channel or connection closes."""

    async def next_delivery(self) -> Delivery | None:
        """The next delivery; None once the stream has stopped for good. Several handler tasks
        may wait on it at once."""
        ...

    def restart(self) -> None:
        """Close the current broker consumer, so that its unacknowledged deliveries return to
        the queue, and subscribe again after a backoff."""
        ...


@dataclass(frozen=True, slots=True)
class EventConsumerConfig:
    """The limits of an event consumer; every value required."""

    concurrency: int  # deliveries handled at once; the broker prefetch equals it
    store_hold: Backoff  # the hold before a requeue after a failure
    store_failure_limit: int  # consecutive handler failures (Failure.COUNTED) before a restart

    def validate(self) -> None:
        """``InvalidConfig`` for a value that is not positive."""
        if self.concurrency < 1:
            raise InvalidConfig("consumer concurrency must be positive")
        if self.store_failure_limit < 1:
            raise InvalidConfig("store failure limit must be positive")
        self.store_hold.validate()


class Action(StrEnum):
    """What happens to a delivery once its database decision has returned."""

    ACK = "ack"
    DEAD_LETTER = "dead_letter"
    REQUEUE = "requeue"
    # A requeue after a failure that counts towards the message's 5 tries (``MessageFailures``);
    # ``StoreFailures.settle`` turns it into REQUEUE or, at the fifth, DEAD_LETTER.
    REQUEUE_COUNTED = "requeue_counted"


_LOGGABLE_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def loggable_message_id(delivery: Delivery) -> str:
    """The delivery's AMQP message ID for a log line when it is a lowercase UUID, ``invalid``
    otherwise (the property comes from the sender and is not trusted), ``none`` when absent."""
    value = delivery.message_id
    if value is None:
        return "none"
    return value if _LOGGABLE_ID.fullmatch(value) else "invalid"


def rejected_fields(delivery: Delivery) -> str:
    """What a log line says of a rejected message: its message ID, size and SHA-256, never the
    body."""
    body = delivery.body
    return (
        f"message_id={loggable_message_id(delivery)} size={len(body)} "
        f"sha256={hashlib.sha256(body).hexdigest()}"
    )


async def settle(delivery: Delivery, action: Action, logger: logging.Logger, fields: str) -> None:
    """Apply ``action``. A failure is logged with ``fields`` (allow-listed ``key=value`` pairs)
    and swallowed: the broker redelivers an unsettled delivery, and the durable state makes that
    harmless."""
    try:
        if action is Action.ACK:
            await delivery.ack()
        elif action is Action.DEAD_LETTER:
            await delivery.dead_letter()
        else:  # REQUEUE, or REQUEUE_COUNTED settled without its budget
            await delivery.requeue()
    except Exception as exc:  # any broker failure: the broker redelivers
        logger.warning(
            "settling a delivery failed; the broker redelivers it (%s action=%s error=%s)",
            fields,
            action,
            type(exc).__name__,
        )


MAX_COUNTED_FAILURES: Final = 5
"""Counted failures of one message before it is dead-lettered (decision 0022 E6: the delivery
limit is 5). RabbitMQ 4 does not count a ``basic.nack`` with requeue towards ``x-delivery-limit``,
so the consumer counts them itself."""
MAX_TRACKED_MESSAGES: Final = 1024
"""Messages whose counted failures one consumer remembers; the oldest is forgotten first."""


class Failure(StrEnum):
    """How a failed delivery is held and counted (decision 0023 G15: Notion 4.3, "a failing
    message goes back to the queue up to 5 times, then to the ``.dlq``").

    - ``COUNTED``: the database answered and the call failed. Counts towards the message's 5
      tries and towards the restart of the stream after ``store_failure_limit`` in a row.
    - ``OUTAGE``: the database did not answer (``StoreUnreachable``, ``Timeout``). Counts towards
      the message's 5 tries, but never restarts the stream: a restart closes the channel, and the
      deliveries it held would return to the queue (Phase B's fix, decision 0022 section 4).
    - ``WAIT``: a deliberate wait, not a failure (``Contention``, the inbox per-project cap).
      Never counts and never restarts the stream."""

    COUNTED = "counted"
    OUTAGE = "outage"
    WAIT = "wait"


def failure_of(exc: BaseException) -> Failure:
    """The kind of failure ``exc`` is, looking at its cause chain too: ``OUTAGE`` for an
    unreachable or timed out database, else ``WAIT`` for contention, else ``COUNTED``."""
    if is_error(exc, StoreUnreachable) or is_error(exc, Timeout):
        return Failure.OUTAGE
    if is_error(exc, Contention):
        return Failure.WAIT
    return Failure.COUNTED


def message_key(delivery: Delivery) -> str:
    """The key a message's failures are counted under: its AMQP message ID, or the SHA-256 of its
    body when it has none."""
    message_id = delivery.message_id
    if message_id is not None:
        return "id:" + message_id
    return "sha256:" + hashlib.sha256(delivery.body).hexdigest()


class MessageFailures:
    """Counted failures per message, in process, bounded (``MAX_TRACKED_MESSAGES``, oldest
    dropped). The broker's ``x-acquired-count`` is not used: RabbitMQ 4.3.6 sets it on a nacked
    redelivery, but it counts every acquisition, contention and the per-project cap included,
    which are waits and must not count."""

    __slots__ = ("_capacity", "_counts", "_limit")

    def __init__(
        self, *, limit: int = MAX_COUNTED_FAILURES, capacity: int = MAX_TRACKED_MESSAGES
    ) -> None:
        if limit < 1 or capacity < 1:
            raise InvalidConfig("the failure limit and the tracked messages must be positive")
        self._limit = limit
        self._capacity = capacity
        self._counts: OrderedDict[str, int] = OrderedDict()

    def failed(self, delivery: Delivery) -> bool:
        """Count one failure; True when the message reached its limit (and is forgotten)."""
        key = message_key(delivery)
        count = self._counts.pop(key, 0) + 1
        if count >= self._limit:
            return True
        self._counts[key] = count
        while len(self._counts) > self._capacity:
            self._counts.popitem(last=False)
        return False

    @property
    def limit(self) -> int:
        return self._limit

    def forget(self, delivery: Delivery) -> None:
        self._counts.pop(message_key(delivery), None)

    def __len__(self) -> int:
        return len(self._counts)


class StoreFailures:
    """The failures of one consumer and the holds before a requeue (decision 0005 section 4;
    decision 0023 G15).

    - ``Failure.COUNTED`` (the database answered, but the handler failed) is held for a backoff
      that grows with the consecutive handler failures and counts towards the message's 5 tries
      (``MessageFailures``); after ``limit`` of them in a row the consumer asks its stream to
      restart, and the count starts again.
    - ``Failure.OUTAGE`` (the database did not answer) and ``Failure.WAIT`` (contention, the
      inbox per-project cap) are held for a backoff that grows with the consecutive failures of
      these two kinds, capped by the hold's maximum, and settled by a requeue. Neither restarts
      the stream: a restart closes the channel, and the deliveries it held would return to the
      queue. An outage counts towards the message's 5 tries, so a long outage parks the message
      in its ``.dlq``, where an operator re-drives it after the fix; a wait never counts."""

    __slots__ = ("_count", "_held", "_hold", "_limit", "_messages", "_restart")

    def __init__(
        self, *, hold: Backoff, limit: int, messages: MessageFailures | None = None
    ) -> None:
        self._hold = hold
        self._limit = limit
        self._count = 0
        self._held = 0
        self._restart = False
        self._messages = messages if messages is not None else MessageFailures()

    def reset(self) -> None:
        """A database call succeeded."""
        self._count = 0
        self._held = 0

    async def hold_then_requeue(self, failure: Failure) -> Action:
        """Sleep the hold for the current count of this failure's kind and return
        ``Action.REQUEUE_COUNTED`` when it counts towards the message's 5 tries (``COUNTED``,
        ``OUTAGE``), ``Action.REQUEUE`` for a ``WAIT``. Cancellation ends the hold at once."""
        if failure is Failure.COUNTED:
            self._count += 1
            attempt = min(self._count, 64)
            if self._count >= self._limit:
                self._count = 0
                self._restart = True
        else:
            self._held = min(self._held + 1, 64)
            attempt = self._held
        await asyncio.sleep(self._hold.delay(attempt).total_seconds())
        return Action.REQUEUE if failure is Failure.WAIT else Action.REQUEUE_COUNTED

    async def settle(
        self, delivery: Delivery, action: Action, logger: logging.Logger, fields: str
    ) -> None:
        """Settle ``delivery`` as ``settle`` does, after counting a counted requeue against the
        message: at ``MAX_COUNTED_FAILURES`` it is dead-lettered instead (to its ``.dlq``) and
        logged with its message ID, size and SHA-256. Any other outcome forgets the message."""
        if action is Action.REQUEUE_COUNTED:
            if self._messages.failed(delivery):
                logger.warning(
                    "dead-lettered after %d failures (%s %s)",
                    self._messages.limit,
                    rejected_fields(delivery),
                    fields,
                )
                action = Action.DEAD_LETTER
            else:
                action = Action.REQUEUE
        elif action is not Action.REQUEUE:
            self._messages.forget(delivery)
        await settle(delivery, action, logger, fields)

    def take_restart(self) -> bool:
        """Whether the limit of handler failures in a row was reached since the last call."""
        restart, self._restart = self._restart, False
        return restart


async def serve(
    stream: DeliveryStream, concurrency: int, handle: Callable[[Delivery], Awaitable[bool]]
) -> None:
    """Handle deliveries from ``stream`` with ``concurrency`` tasks until it stops; ``handle``
    returns True to restart the stream. Each delivery is handled inside its consumer span.
    Returns when every handler has finished; cancelling it cancels them."""

    async def handler() -> None:
        while (delivery := await stream.next_delivery()) is not None:
            with delivery.traced():
                restart = await handle(delivery)
            if restart:
                stream.restart()

    async with asyncio.TaskGroup() as group:
        for _ in range(concurrency):
            group.create_task(handler())
