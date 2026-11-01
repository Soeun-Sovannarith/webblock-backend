"""The usage event's publisher (decision 0014 section 21 answer 2): ``UsageEventSender`` over the
broker adapter. It encodes the v1 "publish completed" event (decision 0022 E4) and publishes it
persistent and mandatory with a publisher confirm; it returns only when the broker confirmed it and
did not return it as unroutable. On ``provisioning.events`` an event no queue is bound for goes to
the alternate exchange, so it is confirmed, not returned (E6)."""

from __future__ import annotations

from uuid import UUID

from provisioning.core.application.usage.ports import UsageEvent
from provisioning.presentation.messaging.codec import RawPublisher, publish_encoded
from provisioning.presentation.messaging.v1 import EventCodecV1


class UsageEventPublisher:
    """Encodes and publishes usage events to one exchange and routing key."""

    __slots__ = ("_codec", "_exchange", "_raw", "_routing_key")

    def __init__(
        self, *, codec: EventCodecV1, raw: RawPublisher, exchange: str, routing_key: str
    ) -> None:
        self._codec = codec
        self._raw = raw
        self._exchange = exchange
        self._routing_key = routing_key

    async def publish_usage_event(self, event: UsageEvent, *, message_id: UUID) -> None:
        message = self._codec.encode_publish_completed(
            message_id=message_id,
            project_id=event.project_id,
            release_id=event.release_id,
            occurred_at=event.completed_at,
        )
        await publish_encoded(self._raw, self._exchange, self._routing_key, message)
