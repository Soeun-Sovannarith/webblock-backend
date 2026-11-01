"""The release-intake store (decision 0008 sections 2 and 3) on the engine of
``provisioning_intake``: the inbox for Project's "source published" event
(provisioning_ops.source_published_events) and the release acceptance.

Every transaction reads the database clock once and uses it for every timestamp it writes
(decision 0008 section 6), and takes its row locks in the order of decision 0008 section 7: the
event row, then project_hosting (insert only), then releases, then release_steps. Nothing here
locks a hosting row, a command or a dispatch row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Update, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import (
    MAX_INT32,
    Contention,
    InboxBatch,
    InboxEntry,
    InboxTake,
    IntakeOutcome,
    IntakeParams,
    ReleaseAcceptanceDecider,
    ReleaseAcceptanceDecision,
    ReleaseAcceptanceFacts,
    ReleaseAcceptanceParams,
    ReleaseAcceptanceResult,
    SourceEventStatus,
    TakeInboxParams,
)
from provisioning.core.domain import (
    DomainError,
    HostingStatus,
    InvalidId,
    Release,
    ReleaseStatus,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
    validate_code,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import DatabaseError, InvalidArgument
from provisioning.infrastructure.postgres.hosting_insert import insert_hosting_row
from provisioning.infrastructure.postgres.rows import event_of, release_of
from provisioning.infrastructure.postgres.schema import (
    project_hosting,
    release_steps,
    releases,
    source_published_events,
)
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_E = source_published_events
_MICROSECOND = timedelta(microseconds=1)


class _InsertMissed(Exception):
    """Inside one intake: the insert found the key taken by an intake that committed later."""


class _ReleaseRaced(Exception):
    """Inside one acceptance: a release appeared under the key after it was read."""


class _Superseded(Exception):
    """Ends a superseded take's transaction with a rollback; never leaves the store."""


class PgReleaseIntakeStore:
    """``ReleaseIntakeStore`` on PostgreSQL. ``max_attempts`` is written into every new event
    row (its attempt budget, migration 00010): the processor's configured budget."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts, *, max_attempts: int) -> None:
        if not 1 <= max_attempts <= MAX_INT32:
            raise InvalidArgument(f"max_attempts must be 1 to {MAX_INT32}")
        self._tx = Transactions(engine, timeouts)
        self._max_attempts = max_attempts

    # --- intake ---------------------------------------------------------------------------------

    async def intake(self, params: IntakeParams) -> IntakeOutcome:
        """One READ COMMITTED transaction that reads the clock, locks the row under the event's
        key if there is one, and writes at most one event row. A missed insert runs it once more,
        which then finds the row that won; a second miss is ``Contention``."""
        params.validate()
        for _ in range(2):
            try:
                return await self._tx.run("intake", lambda conn: self._intake_once(conn, params))
            except _InsertMissed:
                continue
        raise Contention("postgres: intake: a parallel intake of the key won twice")

    async def _intake_once(self, conn: AsyncConnection, params: IntakeParams) -> IntakeOutcome:
        ev = params.event
        now = await statements.read_clock(conn)
        key = (_E.c.project_id == ev.project_id) & (
            _E.c.generation_request_id == ev.generation_request_id
        )
        row = (await conn.execute(select(_E).where(key).with_for_update())).mappings().first()
        if row is not None:
            stored = event_of(row)
            try:
                ev.check_same_source(stored.event)
            except DomainError:
                result = await conn.execute(
                    update(_E)
                    .where(key)
                    .values(
                        mismatched_repeat_count=_E.c.mismatched_repeat_count + 1,
                        last_mismatched_repeat_at=now,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise DatabaseError("intake", reason="the locked row was not updated") from None
                return IntakeOutcome.MISMATCHED_REPEAT
            return IntakeOutcome.DUPLICATE
        pending = (
            await conn.execute(
                select(func.count())
                .select_from(_E)
                .where(_E.c.project_id == ev.project_id, _E.c.status == "pending")
            )
        ).scalar_one()
        if int(pending) >= params.max_pending_per_project:
            return IntakeOutcome.OVER_CAPACITY
        inserted = await conn.execute(
            pg_insert(_E)
            .values(
                project_id=ev.project_id,
                generation_request_id=ev.generation_request_id,
                repository_id=ev.repository_id,
                git_commit_sha=ev.git_commit_sha,
                status="pending",
                attempts=0,
                next_attempt_at=now,
                outcome_code=None,
                release_id=None,
                mismatched_repeat_count=0,
                last_mismatched_repeat_at=None,
                received_at=now,
                updated_at=now,
                finished_at=None,
                max_attempts=self._max_attempts,
                last_manual_retry_by=None,
                last_manual_retry_at=None,
            )
            .on_conflict_do_nothing(index_elements=[_E.c.project_id, _E.c.generation_request_id])
        )
        if inserted.rowcount != 1:
            raise _InsertMissed
        return IntakeOutcome.STORED

    # --- the processor's take -------------------------------------------------------------------

    async def take_due(self, params: TakeInboxParams) -> InboxBatch:
        """One short transaction: read the clock; per project at most ``max_per_project`` due
        rows, oldest first, of at most ``batch_size`` projects, locked SKIP LOCKED; the oldest
        ``batch_size`` of those count an attempt and move by the visibility timeout. A row that
        fails validation rolls the whole take back."""
        params.validate()
        if params.batch_size > MAX_INT32:
            raise InvalidArgument("batch size beyond the INTEGER range")

        async def take(conn: AsyncConnection) -> InboxBatch:
            now = await statements.read_clock(conn)
            due = (
                _E.c.finished_at.is_(None),
                _E.c.status == "pending",
                _E.c.next_attempt_at <= now,
            )
            projects: list[UUID] = list(
                (
                    await conn.execute(
                        select(_E.c.project_id)
                        .where(*due)
                        .group_by(_E.c.project_id)
                        .order_by(func.min(_E.c.next_attempt_at), _E.c.project_id)
                        .limit(params.batch_size)
                    )
                ).scalars()
            )
            picked: list[tuple[datetime, UUID, UUID]] = []
            for project in projects:
                rows = (
                    await conn.execute(
                        select(_E.c.project_id, _E.c.generation_request_id, _E.c.next_attempt_at)
                        .where(_E.c.project_id == project, *due)
                        .order_by(_E.c.next_attempt_at, _E.c.generation_request_id)
                        .limit(params.max_per_project)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
                picked.extend((r[2], r[0], r[1]) for r in rows)
            picked.sort()
            picked = picked[: params.batch_size]
            entries: list[InboxEntry] = []
            for _due_at, project, request in picked:
                row = (
                    (
                        await conn.execute(
                            update(_E)
                            .where(
                                _E.c.project_id == project,
                                _E.c.generation_request_id == request,
                            )
                            .values(
                                attempts=_E.c.attempts + 1,
                                next_attempt_at=now + params.visibility,
                                updated_at=now,
                            )
                            .returning(*_E.c)
                        )
                    )
                    .mappings()
                    .one()
                )
                entries.append(event_of(row))
            return InboxBatch(now=now, entries=tuple(entries))

        return await self._tx.run("take due events", take)

    # --- conditional writes on a taken row ------------------------------------------------------

    async def reject(self, take: InboxTake, code: str) -> bool:
        take.validate()
        validate_code(code)
        return await self._on_take("reject event", take, status="rejected", outcome_code=code)

    async def fail(self, take: InboxTake, code: str) -> bool:
        take.validate()
        validate_code(code)
        return await self._on_take("fail event", take, status="failed", outcome_code=code)

    async def backoff(self, take: InboxTake, delay: timedelta) -> bool:
        take.validate()
        if delay < _MICROSECOND:
            raise InvalidArgument("delay must be at least one microsecond")
        return await self._on_take("back off event", take, delay=delay)

    async def _on_take(
        self,
        op: str,
        take: InboxTake,
        *,
        status: str | None = None,
        outcome_code: str | None = None,
        delay: timedelta | None = None,
    ) -> bool:
        async def write(conn: AsyncConnection) -> bool:
            now = await statements.read_clock(conn)
            values: dict[str, Any] = {"updated_at": now}
            if status is not None:
                values.update(status=status, outcome_code=outcome_code, finished_at=now)
            if delay is not None:
                values["next_attempt_at"] = now + delay
            result = await conn.execute(_taken(take).values(**values))
            return result.rowcount == 1

        return await self._tx.run(op, write)

    # --- release acceptance ---------------------------------------------------------------------

    async def accept_release(
        self, params: ReleaseAcceptanceParams, decide: ReleaseAcceptanceDecider
    ) -> ReleaseAcceptanceResult:
        """One READ COMMITTED transaction per taken row (decision 0008 section 3), run a second
        time when a release appeared under the key between its read and its insert."""
        params.take.validate()
        if (
            params.release_id.int == 0
            or params.step_id.int == 0
            or params.release_id == params.step_id
        ):
            raise InvalidId("release and step need distinct, non-nil IDs")
        for _ in range(2):
            try:
                return await self._tx.run(
                    "accept release", lambda conn: _accept_once(conn, params, decide)
                )
            except _Superseded:
                return ReleaseAcceptanceResult(superseded=True)
            except _ReleaseRaced:
                continue
        raise Contention("postgres: accept release: a release appeared under the key twice")


def _taken(take: InboxTake) -> Update:
    """An update of the taken row, only while it is pending with the taken attempts."""
    return update(_E).where(
        _E.c.project_id == take.project_id,
        _E.c.generation_request_id == take.generation_request_id,
        _E.c.status == "pending",
        _E.c.attempts == take.attempts,
    )


async def _finish(conn: AsyncConnection, stmt: Update) -> None:
    result = await conn.execute(stmt)
    if result.rowcount != 1:
        raise DatabaseError("accept release: finish the event row", reason="no row changed")


def _check_decision(
    d: ReleaseAcceptanceDecision,
    params: ReleaseAcceptanceParams,
    entry: InboxEntry,
    existing: Release | None,
    now: datetime,
) -> None:
    """Refuse, as an internal error, a decision the store must not write."""

    def invalid(why: str) -> DatabaseError:
        return DatabaseError("accept release: decision", reason=why)

    if not d.accepted:
        try:
            validate_code(d.outcome_code)
        except DomainError:
            raise invalid("a rejection without a valid code") from None
        return
    rel = d.release
    if rel is None:
        raise invalid("an acceptance without a release")
    take = params.take
    if rel.project_id != take.project_id or rel.generation_request_id != take.generation_request_id:
        raise invalid("the release is not of the taken row's key")
    try:
        entry.event.check_release(rel)
    except DomainError:
        raise invalid("the release does not hold the taken row's source") from None
    if not d.created:
        if existing is None or rel.id != existing.id:
            raise invalid("the accepted release is not the release stored under the key")
        return
    step, hosting = d.step, d.hosting
    if step is None or hosting is None:
        raise invalid("a new release without its step or hosting row")
    if rel.id != params.release_id or step.id != params.step_id or step.release_id != rel.id:
        raise invalid("the new release or step does not carry the given IDs")
    if rel.status != ReleaseStatus.ACCEPTED or rel.created_at != now or rel.updated_at != now:
        raise invalid("the new release is not accepted at the transaction's clock")
    if (
        step.name != ReleaseStepName.BUILD
        or step.attempt != 1
        or step.status != ReleaseStepStatus.PENDING
        or step.started_at is not None
        or step.deadline_at is not None
        or step.log_ref is not None
    ):
        raise invalid("the new step is not the first build attempt, pending")
    if (
        hosting.project_id != take.project_id
        or hosting.last_command_number != 0
        or hosting.live_release_id is not None
        or hosting.routing_status != RoutingStatus.STABLE
        or hosting.hosting_status != HostingStatus.ONLINE
        or hosting.updated_at != now
    ):
        raise invalid("the hosting row is not the project's initial row at the clock")


async def _insert_release(
    conn: AsyncConnection, d: ReleaseAcceptanceDecision, now: datetime
) -> None:
    """A new release's rows in lock order: the hosting row when the project has none, the
    release, its first build step. A release insert that finds the key taken is
    ``_ReleaseRaced``. The hosting row is inserted under the sub-domain's lock (FR-54, TR-53), so
    the hosting intake's check never misses it; this login may read ``project_id`` only (migration
    00022), so it cannot check who holds the sub-domain, and the bootstrap refuses a tenant to a
    project whose sub-domain another row holds. A concurrent first insert of the same project is
    the row already there (``insert_hosting_row``; Task 12 security re-review N1)."""
    rel, step, hosting = d.release, d.step, d.hosting
    if rel is None or step is None or hosting is None:
        raise DatabaseError("accept release", reason="a new release without its step or hosting")
    await insert_hosting_row(
        conn,
        pg_insert(project_hosting)
        .values(
            project_id=rel.project_id,
            live_release_id=None,
            last_command_number=0,
            routing_status="stable",
            hosting_status="online",
            updated_at=now,
            offline_reasons=[],
            owner_status_version=0,
            subdomain=hosting.subdomain,
        )
        .on_conflict_do_nothing(index_elements=[project_hosting.c.project_id]),
        rel.project_id,
        hosting.subdomain,
        check_holder=False,
    )
    inserted = (
        (
            await conn.execute(
                pg_insert(releases)
                .values(
                    release_id=rel.id,
                    project_id=rel.project_id,
                    generation_request_id=rel.generation_request_id,
                    repository_id=rel.repository_id,
                    git_commit_sha=rel.git_commit_sha,
                    status="accepted",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[releases.c.project_id, releases.c.generation_request_id]
                )
                .returning(*releases.c)
            )
        )
        .mappings()
        .first()
    )
    if inserted is None:
        raise _ReleaseRaced
    release_of(inserted)
    result = await conn.execute(
        pg_insert(release_steps).values(
            release_step_id=step.id,
            release_id=rel.id,
            step_name="build",
            attempt=1,
            status="pending",
            started_at=None,
            finished_at=None,
            deadline_at=None,
            failure_code=None,
            log_ref=None,
        )
    )
    if result.rowcount != 1:
        raise DatabaseError("accept release", reason="the first build step was not inserted")


async def _accept_once(
    conn: AsyncConnection,
    params: ReleaseAcceptanceParams,
    decide: ReleaseAcceptanceDecider,
) -> ReleaseAcceptanceResult:
    take = params.take
    # Step 1: the taken row, locked by its conditional update, with the clock.
    now = await statements.read_clock(conn)
    locked = (
        (await conn.execute(_taken(take).values(updated_at=now).returning(*_E.c)))
        .mappings()
        .first()
    )
    if locked is None:
        raise _Superseded
    entry = event_of(locked)
    # Step 2: the release stored under the key, without a lock.
    stored = (
        (
            await conn.execute(
                select(releases).where(
                    releases.c.project_id == take.project_id,
                    releases.c.generation_request_id == take.generation_request_id,
                )
            )
        )
        .mappings()
        .first()
    )
    existing = None if stored is None else release_of(stored)
    # Step 3: the decision.
    decision = decide(
        ReleaseAcceptanceFacts(
            entry=entry,
            existing=existing,
            release_id=params.release_id,
            step_id=params.step_id,
            now=now,
        )
    )
    _check_decision(decision, params, entry, existing, now)
    if not decision.accepted:  # step 4
        await _finish(
            conn,
            _taken(take).values(
                status="rejected",
                outcome_code=decision.outcome_code,
                finished_at=now,
                updated_at=now,
            ),
        )
        return ReleaseAcceptanceResult(
            superseded=False,
            status=SourceEventStatus.REJECTED,
            outcome_code=decision.outcome_code,
        )
    release = decision.release
    if release is None:  # accepted implies a release
        raise DatabaseError("accept release", reason="an acceptance without a release")
    if decision.created:  # step 6
        await _insert_release(conn, decision, now)
    await _finish(
        conn,
        _taken(take).values(
            status="accepted", release_id=release.id, finished_at=now, updated_at=now
        ),
    )
    return ReleaseAcceptanceResult(
        superseded=False,
        status=SourceEventStatus.ACCEPTED,
        release_id=release.id,
        created=decision.created,
    )
