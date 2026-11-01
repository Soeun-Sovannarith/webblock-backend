"""Payment's "publish completed" usage event (decision 0014 section 21 answer 2; OD-04 and OD-42
stay open for Payment's real contract).

When a release is confirmed live, the worker writes one row of ``provisioning_ops.usage_outbox`` in
the same transaction (``confirm_live``). The usage publisher takes due rows, publishes each with
publisher confirms, and marks it sent. A crash between the publish and the mark publishes the event
again later: duplicates reach Payment, which deduplicates on the event key; a loss cannot happen,
because a row is marked sent only after the broker confirmed it.

This module holds the event, its key rule and the ports the publisher consumes; the loop lives in
``core.application.usage.background.usage_publisher``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID

USAGE_EVENT_KEY_PREFIX: Final = "publish-completed-"
"""The event key is this prefix and the release ID (``usage_outbox_event_key_check``)."""

MAX_USAGE_BATCH_SIZE: Final = 1000


class UsageEventStatus(StrEnum):
    """provisioning_ops.usage_event_status: pending until the broker confirmed it, then sent."""

    PENDING = "pending"
    SENT = "sent"


def usage_event_key(release_id: UUID) -> str:
    """The stable key Payment deduplicates the usage event of ``release_id`` on."""
    return f"{USAGE_EVENT_KEY_PREFIX}{release_id}"


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One taken outbox row: the event and the attempt this take counted (1 for the first)."""

    release_id: UUID
    project_id: UUID
    event_key: str
    completed_at: datetime
    attempts: int


class UsageOutbox(Protocol):
    """The outbox rows, on a login of ``provisioning_worker`` (consumer-defined; implemented by
    ``infrastructure.postgres.usage_outbox.PgUsageOutbox``)."""

    async def take_due(self, limit: int, visibility: timedelta) -> tuple[UsageEvent, ...]:
        """In one short transaction: up to ``limit`` pending rows that are due, oldest first,
        locked SKIP LOCKED; each counts an attempt and moves ``visibility`` ahead, so a crash after
        the take makes it due again."""
        ...

    async def mark_sent(self, event: UsageEvent) -> bool:
        """Mark the row sent, conditional on it still being pending at the taken attempt; False
        when it changed since the take (another publisher took it again)."""
        ...

    async def record_failure(self, event: UsageEvent, retry_after: timedelta, code: str) -> bool:
        """Record a failed publish and make the row due after ``retry_after``, conditional as
        ``mark_sent``."""
        ...


class UsageEventSender(Protocol):
    """Publishes one usage event and returns only when the broker confirmed it and did not return
    it as unroutable (implemented by ``presentation.messaging.UsageEventPublisher``). The event's
    time is ``completed_at``; ``message_id`` is new for every publish."""

    async def publish_usage_event(self, event: UsageEvent, *, message_id: UUID) -> None: ...
