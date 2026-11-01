"""The operator's inbox store (decision 0014 section 21 ruling 11; decision 0008 section 2 and
migration 00010's re-drive, renamed by 00011): on the engine of a login that is a member of
``provisioning_operator`` alone, which may read ``provisioning_ops.source_published_events`` and
update exactly the columns a re-drive writes.

- :meth:`PgOperatorStore.list_stuck` lists the events an operator may need to act on: ``failed``
  rows and ``pending`` rows, each with its state (``failed``; ``parked``: pending with its attempt
  budget spent, so its next take fails it; ``waiting``: pending and due at ``next_attempt_at``).
- :meth:`PgOperatorStore.redrive` re-drives one event in one transaction under its row lock:
  a ``failed`` row goes back to ``pending``, due now, with its outcome and finish cleared, its
  budget raised by ``extra_attempts`` above what it used, and who and when recorded (the update
  guard admits this only from a member of ``provisioning_operator``); a ``pending`` row is made due
  now. An ``accepted`` or ``rejected`` row is final (``NotRedrivable``).
- :meth:`PgOperatorStore.missing_subdomains` and :meth:`PgOperatorStore.set_subdomain` are the
  sub-domain backfill between migrations 00020 and 00021 (decision 0021 D8): the hosting rows from
  before 00020 have none, and the operator sets each once from Project's value. Migration 00021
  revokes every backfill grant, so after it both are refused (42501).

The re-drive's times come from the database clock, read once per transaction. The backfill
writes the ``now`` its caller passes (the CLI's clock, which also stamps the audit event), because
the plan's interface for ``set_subdomain`` takes it. Any other login is refused by the
grants (SQLSTATE 42501, ``DatabaseError``) or by the update guard (23514).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy import RowMapping, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import NotFound, SourceEventStatus
from provisioning.core.domain import validate_requester, validate_subdomain
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.schema import project_hosting, source_published_events
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

MAX_LIST_LIMIT: Final = 1000
MAX_EXTRA_ATTEMPTS: Final = 100

_E = source_published_events
_H = project_hosting


class NotRedrivable(Exception):
    """The event is final (accepted or rejected); a re-drive cannot reopen it."""


class InboxState(StrEnum):
    FAILED = "failed"  # its budget or deadline ran out; a re-drive reopens it
    PARKED = "parked"  # pending with its budget spent: the next take fails it
    WAITING = "waiting"  # pending, due at next_attempt_at


class RedriveKind(StrEnum):
    REOPENED = "reopened"  # failed -> pending, due now, with a larger budget
    MADE_DUE = "made_due"  # pending, due now


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One event as the operator sees it (no payload exists; the commit and repository are the
    event's own identifiers, not secrets)."""

    project_id: UUID
    generation_request_id: UUID
    repository_id: int
    git_commit_sha: str
    status: SourceEventStatus
    state: InboxState | None  # None for an accepted or rejected row
    attempts: int
    max_attempts: int
    outcome_code: str | None
    release_id: UUID | None
    next_attempt_at: datetime
    received_at: datetime
    finished_at: datetime | None
    last_manual_retry_by: str | None
    last_manual_retry_at: datetime | None


@dataclass(frozen=True, slots=True)
class RedriveResult:
    kind: RedriveKind
    before: InboxRow
    after: InboxRow


def _row(row: RowMapping) -> InboxRow:
    status = SourceEventStatus(row["status"])
    state: InboxState | None = None
    if status is SourceEventStatus.FAILED:
        state = InboxState.FAILED
    elif status is SourceEventStatus.PENDING:
        spent = row["attempts"] >= row["max_attempts"]
        state = InboxState.PARKED if spent else InboxState.WAITING
    return InboxRow(
        project_id=row["project_id"],
        generation_request_id=row["generation_request_id"],
        repository_id=row["repository_id"],
        git_commit_sha=row["git_commit_sha"],
        status=status,
        state=state,
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        outcome_code=row["outcome_code"],
        release_id=row["release_id"],
        next_attempt_at=row["next_attempt_at"],
        received_at=row["received_at"],
        finished_at=row["finished_at"],
        last_manual_retry_by=row["last_manual_retry_by"],
        last_manual_retry_at=row["last_manual_retry_at"],
    )


_COLUMNS = (
    _E.c.project_id,
    _E.c.generation_request_id,
    _E.c.repository_id,
    _E.c.git_commit_sha,
    _E.c.status,
    _E.c.attempts,
    _E.c.max_attempts,
    _E.c.outcome_code,
    _E.c.release_id,
    _E.c.next_attempt_at,
    _E.c.received_at,
    _E.c.finished_at,
    _E.c.last_manual_retry_by,
    _E.c.last_manual_retry_at,
)


class PgOperatorStore:
    """The inbox re-drive on PostgreSQL, as ``provisioning_operator``."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def list_stuck(self, limit: int) -> tuple[InboxRow, ...]:
        """Failed and pending events, failed first, then the oldest received."""
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise InvalidArgument(f"list limit must be 1 to {MAX_LIST_LIMIT}")

        async def read(conn: AsyncConnection) -> tuple[InboxRow, ...]:
            failed_first = (_E.c.status != SourceEventStatus.FAILED.value).label("not_failed")
            rows = (
                await conn.execute(
                    select(*_COLUMNS)
                    .where(
                        or_(
                            _E.c.status == SourceEventStatus.FAILED.value,
                            _E.c.status == SourceEventStatus.PENDING.value,
                        )
                    )
                    .order_by(failed_first, _E.c.received_at, _E.c.project_id)
                    .limit(limit)
                )
            ).mappings()
            return tuple(_row(r) for r in rows)

        return await self._tx.run("list inbox events", read, read_only=True)

    async def redrive(
        self, project_id: UUID, generation_request_id: UUID, *, by: str, extra_attempts: int
    ) -> RedriveResult:
        """Re-drive one event (see the module docstring). ``by`` is the operator's name (the
        idempotency key's character rule, 1 to 255 bytes); ``extra_attempts`` 1 to
        ``MAX_EXTRA_ATTEMPTS``. ``NotFound``, ``NotRedrivable``, or the database's refusal."""
        validate_requester(by)
        if not 1 <= extra_attempts <= MAX_EXTRA_ATTEMPTS:
            raise InvalidArgument(f"extra attempts must be 1 to {MAX_EXTRA_ATTEMPTS}")
        key = (_E.c.project_id == project_id, _E.c.generation_request_id == generation_request_id)

        async def write(conn: AsyncConnection) -> RedriveResult:
            now = await statements.read_clock(conn)
            found = (
                (await conn.execute(select(*_COLUMNS).where(*key).with_for_update()))
                .mappings()
                .first()
            )
            if found is None:
                raise NotFound(
                    f"inbox event of project {project_id} and request {generation_request_id}"
                )
            before = _row(found)
            if before.status is SourceEventStatus.FAILED:
                kind = RedriveKind.REOPENED
                values: dict[str, object] = {
                    "status": SourceEventStatus.PENDING.value,
                    "outcome_code": None,
                    "finished_at": None,
                    "max_attempts": func.greatest(_E.c.max_attempts, _E.c.attempts)
                    + extra_attempts,
                    "next_attempt_at": now,
                    "last_manual_retry_by": by,
                    "last_manual_retry_at": now,
                    "updated_at": now,
                }
            elif before.status is SourceEventStatus.PENDING:
                kind = RedriveKind.MADE_DUE
                values = {"next_attempt_at": now, "updated_at": now}
            else:
                raise NotRedrivable(
                    f"inbox event of project {project_id} is {before.status}, which is final"
                )
            after = (
                (await conn.execute(update(_E).where(*key).values(**values).returning(*_COLUMNS)))
                .mappings()
                .one()
            )
            return RedriveResult(kind=kind, before=before, after=_row(after))

        return await self._tx.run("redrive inbox event", write)

    async def missing_subdomains(self, limit: int) -> tuple[UUID, ...]:
        """The projects whose hosting row has no sub-domain yet, by project ID; ``limit`` 1 to
        ``MAX_LIST_LIMIT``."""
        if not 1 <= limit <= MAX_LIST_LIMIT:
            raise InvalidArgument(f"list limit must be 1 to {MAX_LIST_LIMIT}")

        async def read(conn: AsyncConnection) -> tuple[UUID, ...]:
            found = await conn.execute(
                select(_H.c.project_id)
                .where(_H.c.subdomain.is_(None))
                .order_by(_H.c.project_id)
                .limit(limit)
            )
            return tuple(found.scalars())

        return await self._tx.run("list missing subdomains", read, read_only=True)

    async def set_subdomain(self, project_id: UUID, subdomain: str, now: datetime) -> bool:
        """Set the sub-domain of one hosting row that has none, once (``UPDATE ... WHERE subdomain
        IS NULL``). False when the row is missing or already holds one; ``InvalidSubdomain`` for a
        value that is not a DNS label, before any SQL; the database's refusal otherwise."""
        validate_subdomain(subdomain)

        async def write(conn: AsyncConnection) -> bool:
            done = await conn.execute(
                update(_H)
                .where(_H.c.project_id == project_id, _H.c.subdomain.is_(None))
                .values(subdomain=subdomain, updated_at=now)
                .returning(_H.c.project_id)
            )
            return done.first() is not None

        return await self._tx.run("set subdomain", write)
