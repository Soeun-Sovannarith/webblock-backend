"""A broker in memory for the dead-letter tool's unit tests: queues of parked messages, and sessions
that record every call in order, so a test sees whether a removal came after the confirm."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from provisioning.infrastructure.rabbitmq import ENTITLEMENT_ENDED_NAMES, QueueNames
from provisioning.infrastructure.rabbitmq.dlq import (
    DeadLetterSession,
    Fetched,
)

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
AT = datetime(2026, 10, 10, 9, tzinfo=UTC)
CONTENT_MARKER = "customer-content-must-never-print-0001"


def entitlement_body(key: str = "payment-event-1", *, project: UUID = PROJECT) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "message_type": "payment.hosting_entitlement_ended.v1",
            "event_key": key,
            "project_id": str(project),
            "occurred_at": "2026-10-10T09:00:00Z",
            "note": CONTENT_MARKER,
        }
    ).encode()


def death(
    names: QueueNames = ENTITLEMENT_ENDED_NAMES,
    *,
    reason: object = "delivery_limit",
    count: object = 1,
    exchange: object = None,
    routing_keys: object = None,
    queue: object = None,
) -> dict[str, object]:
    return {
        "count": count,
        "reason": reason,
        "queue": names.queue if queue is None else queue,
        "time": AT,
        "exchange": names.exchange if exchange is None else exchange,
        "routing-keys": [names.routing_keys[0]] if routing_keys is None else routing_keys,
    }


def parked(
    message_id: str | None = "m-1",
    *,
    body: bytes | None = None,
    deaths: list[dict[str, object]] | None = None,
    headers: dict[str, object] | None = None,
    taken: int = 1,
) -> Fetched:
    all_headers: dict[str, object] = {
        "x-death": [death()] if deaths is None else deaths,
        "x-first-death-queue": ENTITLEMENT_ENDED_NAMES.queue,
        "x-first-death-reason": "delivery_limit",
        "x-first-death-exchange": ENTITLEMENT_ENDED_NAMES.exchange,
        "x-last-death-queue": ENTITLEMENT_ENDED_NAMES.queue,
        "x-last-death-reason": "delivery_limit",
        "x-last-death-exchange": ENTITLEMENT_ENDED_NAMES.exchange,
        "x-delivery-count": taken,
        "trace": "keep-me",
    }
    if headers is not None:
        all_headers = headers
    return Fetched(
        tag=0,
        body=entitlement_body() if body is None else body,
        message_id=message_id,
        headers=all_headers,
        content_type="application/json",
        content_encoding=None,
        message_type="payment.hosting_entitlement_ended.v1",
        correlation_id="corr-1",
        timestamp=AT,
        app_id="payment-simulator",
    )


@dataclass
class FakeBroker:
    """Queues of parked messages. A session hands out each message at most once (like basic.get on
    one channel), removes a message only on ``ack`` and returns the rest when it ends."""

    queues: dict[str, list[Fetched]] = field(default_factory=dict)
    calls: list[tuple[str, ...]] = field(default_factory=list)
    published: list[tuple[str, str, Fetched]] = field(default_factory=list)
    publish_fault: Exception | None = None
    ack_fault: Exception | None = None
    open_fault: Exception | None = None
    sessions: int = 0

    def park(self, queue: str, *messages: Fetched) -> None:
        self.queues.setdefault(queue, []).extend(messages)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[DeadLetterSession]:
        if self.open_fault is not None:
            raise self.open_fault
        self.sessions += 1
        session = _Session(self)
        try:
            yield session
        finally:
            self.calls.append(("release",))


class _Session:
    def __init__(self, broker: FakeBroker) -> None:
        self._broker = broker
        self._cursor: dict[str, int] = {}
        self._held: dict[int, tuple[str, Fetched]] = {}
        self._next_tag = 1

    async def fetch(self, queue: str) -> Fetched | None:
        index = self._cursor.get(queue, 0)
        messages = self._broker.queues.get(queue, [])
        if index >= len(messages):
            self._broker.calls.append(("get", queue, "-"))
            return None
        self._cursor[queue] = index + 1
        message = messages[index]
        tag = self._next_tag
        self._next_tag += 1
        held = Fetched(
            tag=tag,
            body=message.body,
            message_id=message.message_id,
            headers=message.headers,
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            message_type=message.message_type,
            correlation_id=message.correlation_id,
            timestamp=message.timestamp,
            app_id=message.app_id,
        )
        self._held[tag] = (queue, message)
        self._broker.calls.append(("get", queue, str(message.message_id)))
        return held

    async def publish(self, exchange: str, routing_key: str, message: Fetched) -> None:
        self._broker.calls.append(("publish", exchange, routing_key, str(message.message_id)))
        if self._broker.publish_fault is not None:
            raise self._broker.publish_fault
        self._broker.published.append((exchange, routing_key, message))
        if exchange == "":  # the default exchange: straight to the queue named by the key
            self._broker.queues.setdefault(routing_key, []).append(replace(message, tag=0))
        self._broker.calls.append(("confirmed", str(message.message_id)))

    async def ack(self, message: Fetched) -> None:
        self._broker.calls.append(("ack", str(message.message_id)))
        if self._broker.ack_fault is not None:
            raise self._broker.ack_fault
        queue, original = self._held.pop(message.tag)
        messages = self._broker.queues[queue]
        index = next(i for i, m in enumerate(messages) if m is original)
        del messages[index]
        if index < self._cursor.get(queue, 0):
            self._cursor[queue] -= 1


__all__ = [
    "AT",
    "CONTENT_MARKER",
    "PROJECT",
    "FakeBroker",
    "death",
    "entitlement_body",
    "parked",
]
