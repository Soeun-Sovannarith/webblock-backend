"""The usage outbox (migration 00016; decision 0014 section 21 answer 2) on PostgreSQL, as
``provisioning_worker``.

- :func:`insert_usage_event` writes the row of a release that goes live; the routing store calls it
  inside the transaction that confirms the release live, after the release row, so the insert guard
  sees the release ``live`` and the row exists exactly when the promotion committed.
- :class:`PgUsageOutbox` is the publisher's ``UsageOutbox``: the take, the send and the failure.
  Its arithmetic uses the database clock, read once per transaction (decision 0008 section 6).

Every value is a bound parameter; driver text never leaves the store (``Transactions``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, RowMapping, and_, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import CorruptRow
from provisioning.core.application.usage.ports import (
    MAX_USAGE_BATCH_SIZE,
    UsageEvent,
    UsageEventStatus,
    usage_event_key,
)
from provisioning.core.domain import validate_code
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.schema import usage_outbox
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_U = usage_outbox
_MICROSECOND = timedelta(microseconds=1)
_PENDING = UsageEventStatus.PENDING.value


async def insert_usage_event(
    conn: AsyncConnection, *, project_id: UUID, release_id: UUID, completed_at: datetime
) -> None:
    """Insert the pending usage event of a release that went live, in the caller's transaction.
    ``completed_at`` is the release's promoted_at; created_at and updated_at are the same time, and
    the row is due at once."""
    await conn.execute(
        insert(_U).values(
            release_id=release_id,
            project_id=project_id,
            event_key=usage_event_key(release_id),
            completed_at=completed_at,
            status=_PENDING,
            attempts=0,
            next_attempt_at=completed_at,
            last_failure_code=None,
            sent_at=None,
            created_at=completed_at,
            updated_at=completed_at,
        )
    )


def _duration(name: str, value: timedelta) -> None:
    if value < _MICROSECOND:
        raise InvalidArgument(f"{name} must be at least one microsecond")


def _taken(event: UsageEvent) -> ColumnElement[bool]:
    """The condition of a write on a taken row: still pending, at the attempt the take counted."""
    return and_(
        _U.c.release_id == event.release_id,
        _U.c.status == _PENDING,
        _U.c.attempts == event.attempts,
    )


class PgUsageOutbox:
    """``UsageOutbox`` on PostgreSQL, as ``provisioning_worker``."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def take_due(self, limit: int, visibility: timedelta) -> tuple[UsageEvent, ...]:
        if not 1 <= limit <= MAX_USAGE_BATCH_SIZE:
            raise InvalidArgument(f"usage batch size must be 1 to {MAX_USAGE_BATCH_SIZE}")
        _duration("visibility", visibility)

        async def take(conn: AsyncConnection) -> tuple[UsageEvent, ...]:
            now = await statements.read_clock(conn)
            due: list[UUID] = list(
                (
                    await conn.execute(
                        select(_U.c.release_id)
                        .where(_U.c.status == _PENDING, _U.c.next_attempt_at <= now)
                        .order_by(_U.c.next_attempt_at, _U.c.release_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            if not due:
                return ()
            taken = (
                await conn.execute(
                    update(_U)
                    .where(_U.c.release_id.in_(due))
                    .values(
                        attempts=_U.c.attempts + 1,
                        next_attempt_at=now + visibility,
                        updated_at=now,
                    )
                    .returning(
                        _U.c.release_id,
                        _U.c.project_id,
                        _U.c.event_key,
                        _U.c.completed_at,
                        _U.c.attempts,
                    )
                )
            ).mappings()
            return tuple(
                sorted(
                    (_event_of(row) for row in taken), key=lambda e: (e.completed_at, e.release_id)
                )
            )

        return await self._tx.run("take usage events", take)

    async def mark_sent(self, event: UsageEvent) -> bool:
        async def mark(conn: AsyncConnection) -> bool:
            now = await statements.read_clock(conn)
            result = await conn.execute(
                update(_U)
                .where(_taken(event))
                .values(
                    status=UsageEventStatus.SENT.value,
                    sent_at=now,
                    last_failure_code=None,
                    updated_at=now,
                )
            )
            return result.rowcount == 1

        return await self._tx.run("mark usage event sent", mark)

    async def record_failure(self, event: UsageEvent, retry_after: timedelta, code: str) -> bool:
        _duration("retry_after", retry_after)
        validate_code(code)

        async def record(conn: AsyncConnection) -> bool:
            now = await statements.read_clock(conn)
            result = await conn.execute(
                update(_U)
                .where(_taken(event))
                .values(next_attempt_at=now + retry_after, last_failure_code=code, updated_at=now)
            )
            return result.rowcount == 1

        return await self._tx.run("record usage event failure", record)


def _event_of(row: RowMapping) -> UsageEvent:
    """A taken row as an event; a row whose key is not the release's is ``CorruptRow`` (the check
    constraint makes it impossible)."""
    release_id: UUID = row["release_id"]
    event_key: str = row["event_key"]
    if event_key != usage_event_key(release_id):
        raise CorruptRow(f"postgres: usage event of release {release_id} has another key")
    return UsageEvent(
        release_id=release_id,
        project_id=row["project_id"],
        event_key=event_key,
        completed_at=row["completed_at"],
        attempts=row["attempts"],
    )
