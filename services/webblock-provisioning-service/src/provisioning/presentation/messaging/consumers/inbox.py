"""Project's "source published" event: the consumer that stores it in the inbox (decision 0008
section 2, decision 0014 sections 3 and 10.5).

The consumer decodes the v1 event (``EventCodecV1``, decision 0022 E4) and sends
``ReceiveSourcePublishedCommand`` through the build worker's command bus (decision 0025 CQ11);
the delivery is acknowledged after the inbox row is committed, so a duplicate or a redelivery is a
no-op. After it stored a new row it wakes the inbox processor
(``core.application.intake.background.inbox_processor``), which accepts the rows and starts their
builds. Runs in the build worker only."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from provisioning.core.application import IntakeOutcome, ReceiveSourcePublishedCommand
from provisioning.core.domain import DomainError, IdempotencyConflict, SourcePublishedFields
from provisioning.presentation.messaging.consumers.messages import (
    Action,
    CommandSender,
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


class SourceEventDecoder(Protocol):
    """Validates a delivered "source published" event and returns its four fields; a refusal
    raises an error that holds no part of the body. Implemented by
    ``presentation.messaging.EventCodecV1``; ``message_id`` is the AMQP property."""

    def decode_source_published(
        self,
        *,
        body: bytes,
        content_type: str | None,
        message_type: str | None,
        message_id: str | None,
    ) -> SourcePublishedFields: ...


def _event_fields(fields: SourcePublishedFields) -> str:
    # The key only: never the repository or the commit (decision 0008 section 2).
    return f"project_id={fields.project_id} generation_request_id={fields.generation_request_id}"


class SourcePublishedConsumer:
    """Stores Project's "source published" events in the inbox.

    | Result | Broker action |
    | --- | --- |
    | stored | ack after commit; wake the processor |
    | duplicate | ack |
    | mismatched repeat (``IdempotencyConflict``, recorded on the row) | ack |
    | refused by the codec or the domain | dead-letter |
    | over the per-project cap | hold, then requeue |
    | database error | hold, then requeue; restart after the failure limit |
    """

    __slots__ = ("_commands", "_config", "_decoder", "_failures", "_logger", "_on_stored")

    def __init__(
        self,
        config: EventConsumerConfig,
        *,
        decoder: SourceEventDecoder,
        commands: CommandSender,
        logger: logging.Logger,
        on_stored: Callable[[], None],
    ) -> None:
        config.validate()
        self._config = config
        self._decoder = decoder
        self._commands = commands
        self._logger = logger
        self._on_stored = on_stored
        self._failures = StoreFailures(hold=config.store_hold, limit=config.store_failure_limit)

    async def run(self, stream: DeliveryStream) -> None:
        await serve(stream, self._config.concurrency, self.handle)

    async def handle(self, delivery: Delivery) -> bool:
        """Process and settle one delivery; True asks the caller to restart the stream."""
        try:
            fields = self._decoder.decode_source_published(
                body=delivery.body,
                content_type=delivery.content_type,
                message_type=delivery.message_type,
                message_id=delivery.message_id,
            )
        except Exception as exc:  # the decoder's errors never carry the body (its contract)
            self._logger.warning(
                "source published event dead-lettered (%s error=%s)",
                rejected_fields(delivery),
                exc,
            )
            await settle(delivery, Action.DEAD_LETTER, self._logger, "")
            return False
        log_fields = _event_fields(fields)
        action = await self._receive(fields, log_fields)
        await self._failures.settle(delivery, action, self._logger, log_fields)
        return self._failures.take_restart()

    async def _receive(self, fields: SourcePublishedFields, log_fields: str) -> Action:
        try:
            outcome = await self._commands.send(ReceiveSourcePublishedCommand(fields=fields))
        except IdempotencyConflict:
            self._failures.reset()
            self._logger.warning(
                "source published again with another source; recorded on the inbox row, "
                "nothing else changed (%s)",
                log_fields,
            )
            return Action.ACK
        except DomainError as exc:
            self._logger.warning(
                "source published event refused by the domain; dead-lettered (%s error=%s)",
                log_fields,
                type(exc).__name__,
            )
            return Action.DEAD_LETTER
        except Exception as exc:  # the database is unreachable or contended
            self._logger.warning(
                "inbox write failed; holding the delivery, then requeueing it (%s error=%s)",
                log_fields,
                type(exc).__name__,
            )
            return await self._failures.hold_then_requeue(failure_of(exc))
        self._failures.reset()
        if outcome is IntakeOutcome.STORED:
            self._logger.info("source published event stored in the inbox (%s)", log_fields)
            self._on_stored()
            return Action.ACK
        if outcome is IntakeOutcome.DUPLICATE:
            self._logger.debug("duplicate source published event (%s)", log_fields)
            return Action.ACK
        self._logger.warning(
            "the project's pending inbox rows are at the cap; holding, then requeueing (%s)",
            log_fields,
        )
        return await self._failures.hold_then_requeue(Failure.WAIT)
