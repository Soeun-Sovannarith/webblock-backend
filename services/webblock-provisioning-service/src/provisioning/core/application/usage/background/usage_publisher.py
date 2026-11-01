"""The usage publisher (decision 0014 section 21 answer 2): it sends the "publish completed" usage
event of every release that went live, from the outbox the promotion wrote.

Each poll takes up to ``batch_size`` due rows in one short transaction (each counts an attempt and
is hidden for ``visibility_timeout``), publishes each with a publisher confirm bounded by
``confirm_timeout``, and only then marks it sent. A failed or unconfirmed publish records a failure
code and makes the row due after a backoff. A crash, or a lost database, between the confirm and
the mark leaves the row to come due again after the visibility timeout: the event is published
again. Duplicates reach Payment, which deduplicates on the event key; a loss cannot happen.
Each send that is marked is an audit event (``usage_event_published``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from provisioning.core.application import Clock, IdSource
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.application.usage.ports import (
    MAX_USAGE_BATCH_SIZE,
    UsageEvent,
    UsageEventSender,
    UsageOutbox,
)

CODE_PUBLISH_FAILED: Final = "publish_failed"
CODE_PUBLISH_UNCONFIRMED: Final = "publish_unconfirmed"
USAGE_ACTOR: Final = "usage-publisher"


@dataclass(frozen=True, slots=True)
class UsagePublisherConfig:
    interval: timedelta  # USAGE_PUBLISH_INTERVAL
    batch_size: int  # USAGE_BATCH_SIZE, 1 to MAX_USAGE_BATCH_SIZE
    confirm_timeout: timedelta  # DISPATCH_PUBLISH_CONFIRM_TIMEOUT, as the dispatcher's
    visibility_timeout: timedelta  # DISPATCH_VISIBILITY_TIMEOUT, above the confirm timeout
    backoff: Backoff  # DISPATCH_BACKOFF_BASE and _MAX, by attempts

    def validate(self) -> None:
        """``InvalidConfig`` for a value out of range or a visibility timeout not above the
        confirm timeout."""
        if self.interval <= timedelta(0):
            raise InvalidConfig("usage publish interval must be positive")
        if not 1 <= self.batch_size <= MAX_USAGE_BATCH_SIZE:
            raise InvalidConfig(f"usage batch size must be 1 to {MAX_USAGE_BATCH_SIZE}")
        if self.confirm_timeout <= timedelta(0):
            raise InvalidConfig("usage publish confirm timeout must be positive")
        if self.visibility_timeout <= self.confirm_timeout:
            raise InvalidConfig("usage visibility timeout must exceed the confirm timeout")
        self.backoff.validate()


def _fields(event: UsageEvent) -> str:
    return (
        f"event_key={event.event_key} project_id={event.project_id} "
        f"release_id={event.release_id} attempts={event.attempts}"
    )


class UsagePublisher:
    """Publishes the outbox's usage events until each is confirmed."""

    __slots__ = ("_audit", "_clock", "_config", "_ids", "_logger", "_outbox", "_sender")

    def __init__(
        self,
        config: UsagePublisherConfig,
        *,
        outbox: UsageOutbox,
        sender: UsageEventSender,
        ids: IdSource,
        audit: AuditLog,
        clock: Clock,
        logger: logging.Logger,
    ) -> None:
        config.validate()
        self._audit = audit
        self._clock = clock
        self._config = config
        self._outbox = outbox
        self._sender = sender
        self._ids = ids
        self._logger = logger

    async def poll(self) -> int:
        """One cycle: take a batch and publish each event, one after the other; returns how many
        were taken. The take's error is raised; an event's own error is logged and the row comes
        due again."""
        events = await self._outbox.take_due(
            self._config.batch_size, self._config.visibility_timeout
        )
        for event in events:
            await self._publish(event)
        return len(events)

    async def run(self) -> None:
        """Poll at once and then every ``interval`` until cancelled; a failed poll is logged and
        the next one runs on schedule."""
        while True:
            try:
                await self.poll()
            except Exception as exc:  # a failed poll must not end the loop
                self._logger.warning("usage poll failed (error=%s)", type(exc).__name__)
            await asyncio.sleep(self._config.interval.total_seconds())

    async def _publish(self, event: UsageEvent) -> None:
        fields = _fields(event)
        try:
            async with asyncio.timeout(self._config.confirm_timeout.total_seconds()):
                await self._sender.publish_usage_event(event, message_id=self._ids.new_id())
        except Exception as exc:  # a nack, a return, a timeout or a closed channel
            code = (
                CODE_PUBLISH_UNCONFIRMED if isinstance(exc, TimeoutError) else CODE_PUBLISH_FAILED
            )
            retry = self._config.backoff.delay(event.attempts)
            self._logger.warning(
                "usage event publish failed (%s error=%s retry_after=%s)",
                fields,
                type(exc).__name__,
                retry,
            )
            await self._record_failure(event, retry, code, fields)
            return
        try:
            marked = await self._outbox.mark_sent(event)
        except Exception as exc:  # the row comes due after the visibility timeout; resent
            self._logger.warning(
                "usage event published but not marked sent; it is published again after the "
                "visibility timeout (%s error=%s)",
                fields,
                type(exc).__name__,
            )
            return
        if marked:
            self._logger.info("usage event published (%s)", fields)
            self._audit.record(
                AuditEvent(
                    actor_kind=AuditActorKind.SYSTEM,
                    actor=USAGE_ACTOR,
                    action=AuditAction.USAGE_EVENT_PUBLISHED,
                    project_id=event.project_id,
                    release_id=event.release_id,
                    command_id=None,
                    result=AuditResult.SUCCEEDED,
                    failure_code=None,
                    at=self._clock.now(),
                    correlation_id=event.event_key,
                )
            )
        else:
            self._logger.info(
                "usage event published; the send was not recorded because the row changed "
                "since the take (%s)",
                fields,
            )

    async def _record_failure(
        self, event: UsageEvent, retry: timedelta, code: str, fields: str
    ) -> None:
        try:
            await self._outbox.record_failure(event, retry, code)
        except Exception as exc:  # the row comes due after the visibility timeout
            self._logger.warning(
                "recording a usage publish failure failed; the row comes due after the "
                "visibility timeout (%s error=%s)",
                fields,
                type(exc).__name__,
            )
