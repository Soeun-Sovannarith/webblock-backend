"""The build store (decision 0008 section 4, decision 0014 section 3 "Build") on
the engine of ``provisioning_build``.

Lock order (decision 0008 section 7): the release, then the step; the hosting row, commands and
dispatch rows are never touched. Each transaction reads the database clock once and uses it for
every timestamp it writes and as ``now`` for the domain (decision 0008 section 6). Every release
write names its expected old status in the WHERE clause, every step write too, each checked for
exactly one row (zero is ``IllegalTransition``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, cast
from uuid import UUID

import psycopg
from psycopg import sql as psql
from sqlalchemy import Insert, Update, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import (
    BuildAttemptParams,
    BuildStepKey,
    BuildTarget,
    BuildWrite,
    CorruptRow,
    NotFound,
    OverdueBuild,
    RunningBuild,
    RunningBuilds,
    StartBuildParams,
    SweepOutcome,
)
from provisioning.core.application.builds.background.start_build import BUILD_WANTED_CHANNEL
from provisioning.core.application.builds.ports import PendingBuild, PendingBuilds
from provisioning.core.domain import (
    CODE_DEADLINE_EXCEEDED,
    BuildResult,
    IllegalTransition,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ResultMismatch,
    apply_build_result,
    apply_failed_build_result,
    fail_build_attempt,
    start_build_attempt,
    validate_log_ref,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.build_requests import (
    load_build_target as _load_build_target,
)
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.rows import release_of, step_of, utc
from provisioning.infrastructure.postgres.schema import release_steps, releases
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_MAX_INT32 = 2**31 - 1
_S = release_steps


class _NoRow(Exception):
    """A missing (or skipped) release or step; each method maps it to its own outcome."""


@dataclass(frozen=True, slots=True)
class _Locked:
    release: Release
    step: ReleaseStep
    later_attempt: bool


def _unwritable(what: str) -> CorruptRow:
    return CorruptRow(f"postgres: build outcome changes {what}, which no build path writes")


class PgBuildStore:
    """``BuildStore`` on PostgreSQL."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def start_attempt(self, params: StartBuildParams) -> BuildWrite:
        params.validate()
        # timestamptz holds microseconds; truncating makes the stored deadline the domain's.
        duration = timedelta(microseconds=params.duration // timedelta(microseconds=1))

        async def start(conn: AsyncConnection) -> BuildWrite:
            now = await statements.read_clock(conn)
            try:
                locked = await _load(conn, params.step, skip=False)
            except _NoRow:
                raise NotFound(
                    f"build step {params.step.step_id} of release {params.step.release_id}"
                    f" in project {params.step.project_id}"
                ) from None
            release, step = start_build_attempt(locked.release, locked.step, now + duration, now)
            if release.status != locked.release.status:
                if release.status != ReleaseStatus.BUILDING:
                    raise _unwritable("the release")
                await _one(
                    conn,
                    update(releases)
                    .where(
                        releases.c.project_id == release.project_id,
                        releases.c.release_id == release.id,
                        releases.c.status == "accepted",
                    )
                    .values(status="building", updated_at=release.updated_at),
                    f"release {release.id}",
                )
            elif release != locked.release:
                raise _unwritable("the release")
            if step.status != ReleaseStepStatus.RUNNING:
                raise _unwritable("the step")
            await _one(
                conn,
                update(_S)
                .where(
                    _S.c.release_id == step.release_id,
                    _S.c.release_step_id == step.id,
                    _S.c.status == "pending",
                )
                .values(status="running", started_at=step.started_at, deadline_at=step.deadline_at),
                f"step {step.id}",
            )
            return BuildWrite(release=release, step=step)

        return await self._tx.run("start build attempt", start)

    async def apply_result(self, result: BuildResult, attempt: BuildAttemptParams) -> BuildWrite:
        attempt.validate()
        key = BuildStepKey(result.project_id, result.release_id, result.step_id)
        key.validate()

        async def apply(conn: AsyncConnection) -> BuildWrite:
            now = await statements.read_clock(conn)
            try:
                locked = await _load(conn, key, skip=False)
            except _NoRow:
                # The error does not say whether the release or the step is unknown.
                raise ResultMismatch("no running build attempt matches the result") from None
            next_step: ReleaseStep | None = None
            if result.failure_code == "":
                release, step = apply_build_result(locked.release, locked.step, result, now)
            else:
                failed = apply_failed_build_result(
                    locked.release, locked.step, result, attempt.policy, attempt.next_step_id, now
                )
                release, step, next_step = failed.release, failed.step, failed.next_step
            await _write_finish(conn, locked, release, step, next_step)
            return BuildWrite(release=release, step=step, next_step=next_step)

        return await self._tx.run("apply build result", apply)

    async def scan_overdue(self, limit: int) -> list[OverdueBuild]:
        _limit(limit)

        async def scan(conn: AsyncConnection) -> list[OverdueBuild]:
            rows = (
                await conn.execute(
                    select(
                        _S.c.release_step_id,
                        _S.c.release_id,
                        releases.c.project_id,
                        _S.c.attempt,
                        _S.c.deadline_at,
                        _S.c.log_ref,
                    )
                    .join(releases, releases.c.release_id == _S.c.release_id)
                    .where(
                        _S.c.status == "running",
                        _S.c.step_name == "build",
                        _S.c.deadline_at < statements.clock(),
                    )
                    .order_by(_S.c.deadline_at, _S.c.release_step_id)
                    .limit(limit)
                )
            ).mappings()
            return [
                OverdueBuild(
                    step=BuildStepKey(r["project_id"], r["release_id"], r["release_step_id"]),
                    attempt=r["attempt"],
                    deadline_at=utc(r["deadline_at"]),
                    log_ref=r["log_ref"],
                )
                for r in rows
            ]

        return await self._tx.run("scan overdue build steps", scan, read_only=True)

    async def sweep_overdue(
        self, candidate: OverdueBuild, attempt: BuildAttemptParams
    ) -> SweepOutcome:
        candidate.step.validate()
        attempt.validate()

        async def sweep(conn: AsyncConnection) -> SweepOutcome:
            now = await statements.read_clock(conn)
            try:
                release = await _lock_release(conn, candidate.step, skip=True)
            except _NoRow:
                return SweepOutcome.BUSY
            try:
                locked = await _lock_step(conn, candidate.step, release)
            except _NoRow:
                return SweepOutcome.STALE
            step = locked.step
            if not (
                release.status == ReleaseStatus.BUILDING
                and step.name == ReleaseStepName.BUILD
                and step.status == ReleaseStepStatus.RUNNING
                and step.attempt == candidate.attempt
                and step.deadline_at is not None
                and step.deadline_at < now
                and not locked.later_attempt
            ):
                return SweepOutcome.STALE
            failed = fail_build_attempt(
                release, step, CODE_DEADLINE_EXCEEDED, attempt.policy, attempt.next_step_id, now
            )
            await _write_finish(conn, locked, failed.release, failed.step, failed.next_step)
            return SweepOutcome.FAILED

        return await self._tx.run("sweep overdue build step", sweep)

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        return await _load_build_target(self._tx, project_id, release_id)

    async def record_log_ref(self, step: BuildStepKey, log_ref: str) -> bool:
        step.validate()
        validate_log_ref(log_ref)

        async def record(conn: AsyncConnection) -> bool:
            of_project = exists().where(
                releases.c.release_id == _S.c.release_id,
                releases.c.project_id == step.project_id,
            )
            result = await conn.execute(
                update(_S)
                .where(
                    _S.c.release_id == step.release_id,
                    _S.c.release_step_id == step.step_id,
                    _S.c.step_name == "build",
                    _S.c.status == "running",
                    _S.c.log_ref.is_(None),
                    of_project,
                )
                .values(log_ref=log_ref)
            )
            return result.rowcount == 1

        return await self._tx.run("record log reference", record)

    async def scan_running(self, limit: int) -> RunningBuilds:
        _limit(limit)

        async def scan(conn: AsyncConnection) -> RunningBuilds:
            now = await statements.read_clock(conn)
            rows = (
                await conn.execute(
                    select(
                        _S.c.release_step_id,
                        _S.c.release_id,
                        releases.c.project_id,
                        _S.c.attempt,
                        releases.c.git_commit_sha,
                        releases.c.repository_id,
                        _S.c.started_at,
                        _S.c.deadline_at,
                        _S.c.log_ref,
                    )
                    .join(releases, releases.c.release_id == _S.c.release_id)
                    .where(_S.c.status == "running", _S.c.step_name == "build")
                    .order_by(_S.c.started_at, _S.c.release_step_id)
                    .limit(limit)
                )
            ).mappings()
            builds = tuple(
                RunningBuild(
                    step=BuildStepKey(r["project_id"], r["release_id"], r["release_step_id"]),
                    attempt=r["attempt"],
                    git_commit_sha=r["git_commit_sha"],
                    repository_id=r["repository_id"],
                    started_at=utc(r["started_at"]),
                    deadline_at=utc(r["deadline_at"]),
                    log_ref=r["log_ref"],
                )
                for r in rows
            )
            return RunningBuilds(now=now, builds=builds)

        return await self._tx.run("scan running build steps", scan, read_only=True)

    async def scan_pending(self, limit: int, older_than: timedelta) -> PendingBuilds:
        _limit(limit)
        if older_than <= timedelta(0):
            raise InvalidArgument("pending scan: the age must be positive")
        # When the step became pending: the end of the attempt before it, else (a first attempt)
        # the release's last write. A failed attempt that queues the next never writes the
        # release, so the greatest of the two is the moment it was queued.
        earlier = _S.alias("earlier")
        previous_end = (
            select(func.max(earlier.c.finished_at))
            .where(
                earlier.c.release_id == _S.c.release_id,
                earlier.c.step_name == "build",
                earlier.c.attempt < _S.c.attempt,
            )
            .scalar_subquery()
        )
        since = func.greatest(releases.c.updated_at, previous_end).label("pending_since")
        pending = (
            select(
                _S.c.release_step_id,
                _S.c.release_id,
                releases.c.project_id,
                _S.c.attempt,
                since,
            )
            .join(releases, releases.c.release_id == _S.c.release_id)
            .where(
                _S.c.status == "pending",
                _S.c.step_name == "build",
                releases.c.status.in_(("accepted", "building")),
            )
            .subquery()
        )

        async def scan(conn: AsyncConnection) -> PendingBuilds:
            now = await statements.read_clock(conn)
            rows = (
                await conn.execute(
                    select(pending)
                    .where(pending.c.pending_since < now - older_than)
                    .order_by(pending.c.pending_since, pending.c.release_step_id)
                    .limit(limit)
                )
            ).mappings()
            builds = tuple(
                PendingBuild(
                    step=BuildStepKey(r["project_id"], r["release_id"], r["release_step_id"]),
                    attempt=r["attempt"],
                    pending_since=utc(r["pending_since"]),
                )
                for r in rows
            )
            return PendingBuilds(now=now, builds=builds)

        return await self._tx.run("scan pending build steps", scan, read_only=True)


# Composed SQL, never text: the channel is an identifier the driver quotes.
_LISTEN: Final = psql.Composed([psql.SQL("LISTEN "), psql.Identifier(BUILD_WANTED_CHANNEL)])
_LIVENESS: Final = psql.SQL("SELECT 1")


class PgBuildHintListener:
    """Listens for build hints (task H2) on one connection of the build worker's engine
    (``provisioning_build``), outside any transaction. ``listen`` returns only by cancellation and
    raises when the connection fails; the caller reconnects. Every ``liveness`` interval without
    a notification it asks the server ``SELECT 1``, bounded by DB_STATEMENT_TIMEOUT, so a dead
    connection is noticed. The connection is closed at the end, never given back to the pool.

    LISTEN needs a session of its own: through PgBouncer in transaction mode it does not work,
    so in production the build worker connects directly or through a session-mode pool."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts, *, liveness: timedelta) -> None:
        timeouts.validate()
        if liveness <= timedelta(0):
            raise InvalidArgument("build hint listener: the liveness interval must be positive")
        self._engine = engine
        self._statement = timeouts.statement.total_seconds()
        self._connect = timeouts.transaction.total_seconds()
        self._liveness = liveness.total_seconds()

    async def listen(
        self, on_payload: Callable[[str], object], on_ready: Callable[[], object]
    ) -> None:
        """LISTEN on BUILD_WANTED_CHANNEL; ``on_ready`` once the LISTEN is in force, then
        ``on_payload`` with each notification's payload."""
        async with asyncio.timeout(self._connect):
            conn = await self._engine.connect()
        try:
            raw = await conn.get_raw_connection()
            driver = cast("psycopg.AsyncConnection[Any]", raw.driver_connection)
            async with asyncio.timeout(self._statement):
                await driver.rollback()
                await driver.set_autocommit(True)
                await driver.execute(_LISTEN)
            on_ready()
            while True:
                async for note in driver.notifies(timeout=self._liveness):
                    if note.channel == BUILD_WANTED_CHANNEL:
                        on_payload(note.payload)
                async with asyncio.timeout(self._statement):
                    await driver.execute(_LIVENESS)
        finally:
            await asyncio.shield(conn.invalidate())
            await asyncio.shield(conn.close())


def _limit(limit: int) -> None:
    if not 1 <= limit <= _MAX_INT32:
        raise InvalidArgument(f"scan limit must be 1 to {_MAX_INT32}")


async def _lock_release(conn: AsyncConnection, key: BuildStepKey, *, skip: bool) -> Release:
    stmt = statements.release(key.project_id, key.release_id).with_for_update(skip_locked=skip)
    row = (await conn.execute(stmt)).mappings().first()
    if row is None:
        raise _NoRow
    return release_of(row)


async def _lock_step(conn: AsyncConnection, key: BuildStepKey, release: Release) -> _Locked:
    later = _S.alias("later")
    later_attempt = (
        exists()
        .where(
            later.c.release_id == _S.c.release_id,
            later.c.step_name == "build",
            later.c.attempt > _S.c.attempt,
        )
        .label("later_attempt")
    )
    row = (
        (
            await conn.execute(
                select(_S, later_attempt)
                .where(_S.c.release_id == release.id, _S.c.release_step_id == key.step_id)
                .with_for_update(of=_S)
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise _NoRow
    return _Locked(release=release, step=step_of(row), later_attempt=bool(row["later_attempt"]))


async def _load(conn: AsyncConnection, key: BuildStepKey, *, skip: bool) -> _Locked:
    """Lock the release, then the step (decision 0008 section 7)."""
    release = await _lock_release(conn, key, skip=skip)
    return await _lock_step(conn, key, release)


async def _one(conn: AsyncConnection, stmt: Update | Insert, what: str) -> None:
    result = await conn.execute(stmt)
    if result.rowcount != 1:
        raise IllegalTransition(f"{what} changed {result.rowcount} rows, want 1")


async def _write_finish(
    conn: AsyncConnection,
    before: _Locked,
    release: Release,
    step: ReleaseStep,
    next_step: ReleaseStep | None,
) -> None:
    """The end of an attempt in lock order: the release when it changed (building -> built or
    failed), the step (running -> succeeded or failed), and the next attempt when queued."""
    if release.status != before.release.status:
        if before.release.status != ReleaseStatus.BUILDING or release.status not in (
            ReleaseStatus.BUILT,
            ReleaseStatus.FAILED,
        ):
            raise _unwritable("the release status")
        if release.promoted_at != before.release.promoted_at:
            raise _unwritable("promoted_at")
        await _one(
            conn,
            update(releases)
            .where(
                releases.c.project_id == release.project_id,
                releases.c.release_id == release.id,
                releases.c.status == "building",
            )
            .values(
                status=str(release.status),
                container_image_digest=release.container_image_digest,
                failure_code=release.failure_code,
                failure_summary=release.failure_summary,
                updated_at=release.updated_at,
            ),
            f"release {release.id}",
        )
    elif release != before.release:
        raise _unwritable("the release")
    if step.status not in (ReleaseStepStatus.SUCCEEDED, ReleaseStepStatus.FAILED):
        raise _unwritable("the step status")
    await _one(
        conn,
        update(_S)
        .where(
            _S.c.release_id == step.release_id,
            _S.c.release_step_id == step.id,
            _S.c.status == "running",
        )
        .values(
            status=str(step.status),
            finished_at=step.finished_at,
            failure_code=step.failure_code,
            log_ref=step.log_ref,
        ),
        f"step {step.id}",
    )
    if next_step is None:
        return
    if (
        next_step.release_id != release.id
        or next_step.name != ReleaseStepName.BUILD
        or next_step.status != ReleaseStepStatus.PENDING
        or next_step.attempt != step.attempt + 1
    ):
        raise _unwritable("the next attempt")
    await _one(
        conn,
        insert(_S).values(
            release_step_id=next_step.id,
            release_id=next_step.release_id,
            step_name="build",
            attempt=next_step.attempt,
            status="pending",
            started_at=None,
            finished_at=None,
            deadline_at=None,
            failure_code=None,
            log_ref=None,
        ),
        f"next attempt {next_step.id}",
    )
