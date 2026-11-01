"""The usage publisher (decision 0014 section 21 answer 2): it takes due outbox rows, publishes
each with a confirm, and marks it sent only after the confirm. A failed or unconfirmed publish
records a failure with a backoff; a crash between the publish and the mark leaves the row to come
due again, so the event is published again (a duplicate, which Payment deduplicates), never lost.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.unit._loop_fakes import MS, eventually, logger_with_handler

from provisioning.core.application.shared.audit import AuditAction, AuditEvent, AuditResult
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.application.usage.background.usage_publisher import (
    CODE_PUBLISH_FAILED,
    CODE_PUBLISH_UNCONFIRMED,
    UsagePublisher,
    UsagePublisherConfig,
)
from provisioning.core.application.usage.ports import UsageEvent, usage_event_key

pytestmark = pytest.mark.asyncio

PROJECT = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
AT = datetime(2026, 10, 9, 10, tzinfo=UTC)


def release(n: int) -> UUID:
    return UUID(int=0x0199C4A1_7C1E_7D4B_9A3E_5B2F8C6D0000 + n)


class Outbox:
    """Rows by release: pending until marked sent; the take counts an attempt and hides the row
    for the visibility timeout (here: until ``expire`` is called)."""

    def __init__(self, n: int) -> None:
        self.rows = {release(i): {"attempts": 0, "sent": False, "hidden": False} for i in range(n)}
        self.failures: list[tuple[UUID, timedelta, str]] = []
        self.mark_faults: list[Exception] = []
        self.visibility: list[timedelta] = []

    def expire(self) -> None:
        for row in self.rows.values():
            row["hidden"] = False

    async def take_due(self, limit: int, visibility: timedelta) -> tuple[UsageEvent, ...]:
        self.visibility.append(visibility)
        out: list[UsageEvent] = []
        for rid, row in self.rows.items():
            if row["sent"] or row["hidden"] or len(out) >= limit:
                continue
            row["attempts"] = int(row["attempts"]) + 1
            row["hidden"] = True
            out.append(
                UsageEvent(
                    release_id=rid,
                    project_id=PROJECT,
                    event_key=usage_event_key(rid),
                    completed_at=AT,
                    attempts=int(row["attempts"]),
                )
            )
        return tuple(out)

    async def mark_sent(self, event: UsageEvent) -> bool:
        if self.mark_faults:
            raise self.mark_faults.pop(0)
        row = self.rows[event.release_id]
        if row["sent"] or row["attempts"] != event.attempts:
            return False
        row["sent"] = True
        return True

    async def record_failure(self, event: UsageEvent, retry_after: timedelta, code: str) -> bool:
        self.failures.append((event.release_id, retry_after, code))
        return True


class Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.ids: list[UUID] = []
        self.faults: list[Exception] = []
        self.hang = False

    async def publish_usage_event(self, event: UsageEvent, *, message_id: UUID) -> None:
        if self.hang:
            await asyncio.sleep(10)
        if self.faults:
            raise self.faults.pop(0)
        self.sent.append(event.event_key)
        self.ids.append(message_id)


class Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> UUID:
        self.n += 1
        return UUID(int=self.n)


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("no alert expected")


class Clock:
    def now(self) -> datetime:
        return AT


def config(**over: object) -> UsagePublisherConfig:
    values: dict[str, object] = {
        "interval": MS,
        "batch_size": 10,
        "confirm_timeout": 50 * MS,
        "visibility_timeout": 100 * MS,
        "backoff": Backoff(base=MS, max=4 * MS),
    }
    values.update(over)
    return UsagePublisherConfig(**values)  # type: ignore[arg-type]  # test values by name


AUDIT: list[Audit] = []


def build(outbox: Outbox, sender: Sender, **over: object) -> tuple[UsagePublisher, list[str]]:
    logger, handler = logger_with_handler("usage")
    AUDIT.append(Audit())
    publisher = UsagePublisher(
        config(**over),
        outbox=outbox,
        sender=sender,
        ids=Ids(),
        audit=AUDIT[-1],
        clock=Clock(),
        logger=logger,
    )
    return publisher, handler.lines


async def test_each_due_event_is_published_then_marked_sent() -> None:
    """FR-42.1: each due event is published then marked sent."""
    outbox, sender = Outbox(3), Sender()
    publisher, lines = build(outbox, sender)
    assert await publisher.poll() == 3
    assert sorted(sender.sent) == sorted(usage_event_key(release(i)) for i in range(3))
    assert all(row["sent"] for row in outbox.rows.values())
    assert len(set(sender.ids)) == 3  # a new message ID per publish
    assert outbox.visibility == [100 * MS]
    assert sum("usage event published" in line for line in lines) == 3


async def test_the_batch_size_bounds_one_poll() -> None:
    outbox, sender = Outbox(5), Sender()
    publisher, _ = build(outbox, sender, batch_size=2)
    assert await publisher.poll() == 2
    assert len(sender.sent) == 2


async def test_a_failed_publish_is_recorded_with_a_backoff_and_retried_later() -> None:
    outbox, sender = Outbox(1), Sender()
    sender.faults = [ConnectionError("broker down")]
    publisher, lines = build(outbox, sender)
    await publisher.poll()
    assert sender.sent == []
    assert not outbox.rows[release(0)]["sent"]
    ((rid, retry_after, code),) = outbox.failures
    assert rid == release(0) and code == CODE_PUBLISH_FAILED
    assert MS / 2 < retry_after <= MS
    assert any("ConnectionError" in line for line in lines)
    outbox.expire()
    await publisher.poll()
    assert outbox.rows[release(0)]["sent"]
    assert sender.sent == [usage_event_key(release(0))]


async def test_an_unconfirmed_publish_is_a_failure_after_the_confirm_timeout() -> None:
    """FR-42.2: an unconfirmed publish is a failure after the confirm timeout."""
    outbox, sender = Outbox(1), Sender()
    sender.hang = True
    publisher, _ = build(outbox, sender, confirm_timeout=5 * MS)
    await publisher.poll()
    assert [c for _, _, c in outbox.failures] == [CODE_PUBLISH_UNCONFIRMED]
    assert not outbox.rows[release(0)]["sent"]


async def test_a_crash_between_the_publish_and_the_mark_publishes_again_and_loses_nothing() -> None:
    outbox, sender = Outbox(1), Sender()
    outbox.mark_faults = [ConnectionError("db gone after the confirm")]
    publisher, _ = build(outbox, sender)
    await publisher.poll()
    assert sender.sent == [usage_event_key(release(0))]
    assert not outbox.rows[release(0)]["sent"]
    assert outbox.failures == []  # the row comes due after the visibility timeout
    outbox.expire()
    await publisher.poll()
    assert sender.sent == [usage_event_key(release(0))] * 2  # a duplicate, never a loss
    assert outbox.rows[release(0)]["sent"]


async def test_a_mark_refused_because_the_row_was_taken_again_is_not_an_error() -> None:
    outbox, sender = Outbox(1), Sender()
    publisher, lines = build(outbox, sender)

    async def taken_again(event: UsageEvent) -> bool:
        return False

    outbox.mark_sent = taken_again  # type: ignore[method-assign]  # a store that lost the race
    await publisher.poll()
    assert any("not recorded" in line for line in lines)


async def test_run_polls_until_cancelled_and_survives_a_failed_take() -> None:
    outbox, sender = Outbox(1), Sender()
    publisher, lines = build(outbox, sender)
    takes = 0
    real_take = outbox.take_due

    async def flaky(limit: int, visibility: timedelta) -> tuple[UsageEvent, ...]:
        nonlocal takes
        takes += 1
        if takes == 1:
            raise ConnectionError("db")
        return await real_take(limit, visibility)

    outbox.take_due = flaky  # type: ignore[method-assign]  # a take that fails once
    task = asyncio.create_task(publisher.run())
    await eventually(lambda: bool(outbox.rows[release(0)]["sent"]))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("usage poll failed" in line for line in lines)


@pytest.mark.parametrize(
    "over",
    [
        {"interval": 0 * MS},
        {"batch_size": 0},
        {"batch_size": 1001},
        {"confirm_timeout": 0 * MS},
        {"visibility_timeout": 50 * MS},
        {"backoff": Backoff(base=2 * MS, max=MS)},
    ],
)
async def test_the_settings_are_checked(over: dict[str, object]) -> None:
    with pytest.raises(InvalidConfig):
        build(Outbox(0), Sender(), **over)


async def test_each_marked_send_is_audited_and_a_failure_is_not() -> None:
    outbox, sender = Outbox(2), Sender()
    sender.faults = [ConnectionError("down")]
    publisher, _ = build(outbox, sender)
    await publisher.poll()
    (event,) = AUDIT[-1].events
    assert event.action is AuditAction.USAGE_EVENT_PUBLISHED
    assert event.result is AuditResult.SUCCEEDED
    assert event.release_id is not None and outbox.rows[event.release_id]["sent"]
    assert event.correlation_id == usage_event_key(event.release_id)
