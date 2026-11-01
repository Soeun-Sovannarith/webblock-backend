"""The consumer shared by the events that become commands: Payment's entitlement events
(``suspend_hosting``, ``resume_hosting``), Project's "project deleted" and "owner status changed"
events. Decision 0014 section 10.5.

Settling (lead update to E2):

- accepted or replayed: ack; a duplicate event is processed once; an event that needs no command
  (the source answers None, e.g. a stale owner status) is acked too;
- a permanent refusal: ack, logged with the event key (never dropped silently); a source may ask
  for a dead-letter instead (``refusal``; the owner-status event, decision 0022 Task 5);
- the database did not answer (unreachable, timed out; ``Failure.OUTAGE``): held for the growing
  store hold, capped, then requeued; counted towards the message's bound (5, then its ``.dlq``)
  like any failure (decision 0023 G15), but never a restart of the stream;
- contention (``Failure.WAIT``): held, then requeued; never counted, never parked;
- a transient refusal (the use case's ``retry_later``, e.g. busy while a deploy is in flight): no
  ack; held for ``retry_backoff``, then requeued; after ``max_retries`` redeliveries of the event
  key it is parked in the dead-letter queue and logged with the event key;
- any other error (the database answered, the call failed): held, then requeued; counted
  towards the message's bound (5, then its ``.dlq``) and the restart;
- a transient refusal (the hosting retry budget) and contention are waits, not failures:
  neither counts towards the 5;
- an invalid payload: dead-lettered.

The redelivery count is kept per event key in this process (bounded); a restarted process starts
counting again, so the bound holds per process.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from provisioning.core.application import AcceptedCommand, ApplicationError
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import DomainError
from provisioning.presentation.messaging.consumers.messages import (
    Action,
    Delivery,
    DeliveryStream,
    EventConsumerConfig,
    Failure,
    StoreFailures,
    failure_of,
    rejected_fields,
    serve,
    settle,
)

# Event keys whose redeliveries are counted at once; the oldest is forgotten beyond it.
MAX_TRACKED_EVENT_KEYS = 10_000


@dataclass(frozen=True, slots=True)
class EventCommandSource[E]:
    """How one kind of event becomes a command.

    - ``decode`` turns a delivery into an event, or raises an error that holds no part of the
      body;
    - ``accept`` accepts (or replays) the event's command, or answers None when the event needs
      no command;
    - ``retry_later`` tells a transient refusal from a permanent one;
    - ``event_key`` and ``fields`` name the event in logs (identifiers only);
    - ``label`` names the event and the command in log lines ("payment event", "hosting");
    - ``refusal`` is what a permanent refusal does: ack (the default) or dead-letter.
    """

    decode: Callable[[Delivery], E]
    accept: Callable[[E], Awaitable[AcceptedCommand | None]]
    retry_later: Callable[[BaseException], bool]
    event_key: Callable[[E], str]
    fields: Callable[[E], str]
    event_label: str
    command_label: str
    refusal: Action = Action.ACK


class CommandEventConsumer[E]:
    __slots__ = (
        "_config",
        "_failures",
        "_logger",
        "_max_retries",
        "_retries",
        "_retry_backoff",
        "_source",
    )

    def __init__(
        self,
        config: EventConsumerConfig,
        *,
        source: EventCommandSource[E],
        logger: logging.Logger,
        retry_backoff: Backoff,
        max_retries: int,
    ) -> None:
        """``retry_backoff`` is the hold before a transient refusal is requeued, by its
        redelivery count; ``max_retries`` (at least 1) the redeliveries of one event key before
        it is parked (``InvalidConfig``)."""
        config.validate()
        retry_backoff.validate()
        if max_retries < 1:
            raise InvalidConfig(f"{source.event_label} retries must be positive")
        self._config = config
        self._source = source
        self._logger = logger
        self._retry_backoff = retry_backoff
        self._max_retries = max_retries
        self._retries: OrderedDict[str, int] = OrderedDict()
        self._failures = StoreFailures(hold=config.store_hold, limit=config.store_failure_limit)

    async def run(self, stream: DeliveryStream) -> None:
        await serve(stream, self._config.concurrency, self.handle)

    async def handle(self, delivery: Delivery) -> bool:
        """Process and settle one delivery; True asks the caller to restart the stream."""
        label = self._source.event_label
        try:
            event = self._source.decode(delivery)
        except Exception as exc:  # the decoder's errors never carry the body (its contract)
            self._logger.warning(
                "%s dead-lettered (%s error=%s)", label, rejected_fields(delivery), exc
            )
            await settle(delivery, Action.DEAD_LETTER, self._logger, "")
            return False
        fields = self._source.fields(event)
        action = await self._accept(event, fields)
        await self._failures.settle(delivery, action, self._logger, fields)
        return self._failures.take_restart()

    async def _accept(self, event: E, fields: str) -> Action:
        source = self._source
        key = source.event_key(event)
        try:
            accepted = await source.accept(event)
        except Exception as exc:
            failure = failure_of(exc)
            if failure is not Failure.COUNTED:
                self._logger.warning(
                    "%s intake found the database %s; holding, then requeueing (%s error=%s)",
                    source.command_label,
                    "unanswered, counted" if failure is Failure.OUTAGE else "busy, not counted",
                    fields,
                    type(exc).__name__,
                )
                return await self._failures.hold_then_requeue(failure)
            if source.retry_later(exc):
                return await self._retry_later(key, exc, fields)
            if isinstance(exc, (ApplicationError, DomainError)):
                self._forget(key)
                self._failures.reset()
                self._logger.warning(
                    "%s refused for good; %s (%s error=%s)",
                    source.event_label,
                    "acknowledged" if source.refusal is Action.ACK else "dead-lettered",
                    fields,
                    type(exc).__name__,
                )
                return source.refusal
            self._logger.warning(
                "%s intake failed; holding, then requeueing (%s error=%s)",
                source.command_label,
                fields,
                type(exc).__name__,
            )
            return await self._failures.hold_then_requeue(Failure.COUNTED)
        self._forget(key)
        self._failures.reset()
        if accepted is None:
            self._logger.info("%s needed no command (%s)", source.event_label, fields)
            return Action.ACK
        self._logger.info(
            "%s command %s (%s command_id=%s)",
            source.command_label,
            "replayed" if accepted.replayed else "accepted",
            fields,
            accepted.command.id,
        )
        return Action.ACK

    async def _retry_later(self, event_key: str, exc: Exception, fields: str) -> Action:
        """Hold, then requeue; park in the dead-letter queue after ``max_retries``."""
        self._failures.reset()
        count = self._retries.pop(event_key, 0) + 1
        if count > self._max_retries:
            self._logger.error(
                "%s parked in the dead-letter queue after %d redeliveries (%s error=%s)",
                self._source.event_label,
                self._max_retries,
                fields,
                type(exc).__name__,
            )
            return Action.DEAD_LETTER
        self._retries[event_key] = count
        while len(self._retries) > MAX_TRACKED_EVENT_KEYS:
            self._retries.popitem(last=False)
        delay = self._retry_backoff.delay(count)
        self._logger.info(
            "%s command not accepted yet; holding, then requeueing "
            "(%s error=%s redelivery=%d retry_after=%s)",
            self._source.command_label,
            fields,
            type(exc).__name__,
            count,
            delay,
        )
        await asyncio.sleep(delay.total_seconds())
        return Action.REQUEUE

    def _forget(self, event_key: str) -> None:
        self._retries.pop(event_key, None)
