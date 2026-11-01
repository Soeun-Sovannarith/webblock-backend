"""The read store (decision 0004 section 6) on the engine of ``provisioning_api``, with
the listing and diagnostics reads of decision 0014 section 21 rulings 6 and 7 (``HistoryStore``).
Each read is one REPEATABLE READ, READ ONLY transaction, so its rows come from one snapshot; every
statement filters on the project, and every list statement carries the limit it is given.

Diagnostics read the "source published" event row through the column grant of migration 00016
(decision 0014 section 23 item 4): only project_id, generation_request_id, status, outcome_code,
release_id, attempts, mismatched_repeat_count, received_at and finished_at, never the repository,
commit or manual-retry columns.
"""

from __future__ import annotations

from typing import Final
from uuid import UUID

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import (
    MAX_RELEASE_STEPS,
    CommandKey,
    CorruptRow,
    Deployment,
    DiagnosticFacts,
    NotFound,
    ReleaseCursor,
    ReleaseKey,
    ReleaseWithSteps,
)
from provisioning.core.domain import Command, InvalidId, Release, ReleaseStep
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.rows import (
    command_of,
    hosting_of,
    release_of,
    source_event_outcome_of,
    step_of,
)
from provisioning.infrastructure.postgres.schema import (
    commands,
    release_steps,
    releases,
    source_published_events,
)
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

# The largest limit a list statement accepts: the service asks for a page (at most 50) plus one.
MAX_READ_LIMIT: Final = 101

_events = source_published_events.c
_SOURCE_EVENT_COLUMNS: Final = (
    _events.project_id,
    _events.generation_request_id,
    _events.status,
    _events.outcome_code,
    _events.release_id,
    _events.attempts,
    _events.mismatched_repeat_count,
    _events.received_at,
    _events.finished_at,
)


class PgReadStore:
    """``ReadStore`` and ``HistoryStore`` on PostgreSQL."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
        if key.project_id.int == 0 or key.release_id.int == 0:
            raise InvalidId("read release needs project and release IDs")

        async def read(conn: AsyncConnection) -> ReleaseWithSteps:
            release = await _release(conn, key)
            step_rows = (
                (
                    await conn.execute(
                        statements.release_steps_of(
                            key.project_id, key.release_id, MAX_RELEASE_STEPS + 1
                        )
                    )
                )
                .mappings()
                .all()
            )
            if len(step_rows) > MAX_RELEASE_STEPS:
                raise CorruptRow(
                    f"postgres: release {release.id} has more than {MAX_RELEASE_STEPS} steps"
                )
            steps = tuple(step_of(r) for r in step_rows)
            _check_steps(release, steps)
            return ReleaseWithSteps(release=release, steps=steps)

        return await self._tx.run("read release", read, read_only=True)

    async def read_command(self, key: CommandKey) -> Command:
        if key.project_id.int == 0 or key.command_id.int == 0:
            raise InvalidId("read command needs project and command IDs")

        async def read(conn: AsyncConnection) -> Command:
            row = (
                (await conn.execute(statements.command(key.project_id, key.command_id)))
                .mappings()
                .first()
            )
            if row is None:
                raise NotFound(f"command {key.command_id} of project {key.project_id}")
            return command_of(row)

        return await self._tx.run("read command", read, read_only=True)

    async def read_deployment(self, project_id: UUID) -> Deployment:
        if project_id.int == 0:
            raise InvalidId("read deployment needs a project ID")

        async def read(conn: AsyncConnection) -> Deployment:
            row = (await conn.execute(statements.hosting(project_id))).mappings().first()
            if row is None:
                raise NotFound(f"project {project_id} has no hosting row")
            hosting = hosting_of(row)
            in_flight = (
                (await conn.execute(statements.command_in_flight(project_id))).mappings().first()
            )
            return Deployment(
                hosting=hosting, in_flight=None if in_flight is None else command_of(in_flight)
            )

        return await self._tx.run("read deployment", read, read_only=True)

    async def list_releases(
        self, project_id: UUID, *, limit: int, after: ReleaseCursor | None
    ) -> tuple[Release, ...]:
        if project_id.int == 0:
            raise InvalidId("list releases needs a project ID")
        _check_limit(limit)
        query = select(releases).where(releases.c.project_id == project_id)
        if after is not None:
            query = query.where(
                tuple_(releases.c.created_at, releases.c.release_id)
                < tuple_(after.created_at, after.release_id)
            )
        query = query.order_by(releases.c.created_at.desc(), releases.c.release_id.desc()).limit(
            limit
        )

        async def read(conn: AsyncConnection) -> tuple[Release, ...]:
            rows = (await conn.execute(query)).mappings().all()
            return tuple(release_of(r) for r in rows)

        return await self._tx.run("list releases", read, read_only=True)

    async def list_commands(
        self, project_id: UUID, *, limit: int, after: int | None
    ) -> tuple[Command, ...]:
        if project_id.int == 0:
            raise InvalidId("list commands needs a project ID")
        _check_limit(limit)
        query = _commands_of(project_id, limit, after)

        async def read(conn: AsyncConnection) -> tuple[Command, ...]:
            rows = (await conn.execute(query)).mappings().all()
            return tuple(command_of(r) for r in rows)

        return await self._tx.run("list commands", read, read_only=True)

    async def read_diagnostics(
        self, key: ReleaseKey, *, step_limit: int, command_limit: int
    ) -> DiagnosticFacts:
        if key.project_id.int == 0 or key.release_id.int == 0:
            raise InvalidId("read diagnostics needs project and release IDs")
        _check_limit(step_limit)
        _check_limit(command_limit)
        steps_query = (
            select(release_steps)
            .join(releases, releases.c.release_id == release_steps.c.release_id)
            .where(
                releases.c.project_id == key.project_id,
                release_steps.c.release_id == key.release_id,
            )
            .order_by(release_steps.c.step_name.desc(), release_steps.c.attempt.desc())
            .limit(step_limit)
        )
        commands_query = _commands_of(key.project_id, command_limit, None)

        async def read(conn: AsyncConnection) -> DiagnosticFacts:
            release = await _release(conn, key)
            steps = tuple(step_of(r) for r in (await conn.execute(steps_query)).mappings().all())
            _check_steps(release, steps)
            found = tuple(
                command_of(r) for r in (await conn.execute(commands_query)).mappings().all()
            )
            event = (
                (
                    await conn.execute(
                        select(*_SOURCE_EVENT_COLUMNS).where(
                            _events.project_id == key.project_id,
                            _events.generation_request_id == release.generation_request_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            return DiagnosticFacts(
                release=release,
                steps=steps,
                commands=found,
                source_event=None if event is None else source_event_outcome_of(event),
            )

        return await self._tx.run("read diagnostics", read, read_only=True)


async def _release(conn: AsyncConnection, key: ReleaseKey) -> Release:
    row = (
        (await conn.execute(statements.release(key.project_id, key.release_id))).mappings().first()
    )
    if row is None:
        raise NotFound(f"release {key.release_id} of project {key.project_id}")
    return release_of(row)


def _check_steps(release: Release, steps: tuple[ReleaseStep, ...]) -> None:
    for step in steps:
        if step.release_id != release.id:
            raise CorruptRow(f"postgres: step {step.id} is not of release {release.id}")


def _check_limit(limit: int) -> None:
    """A limit outside 1 to MAX_READ_LIMIT is a caller's defect: no statement is unbounded."""
    if not 1 <= limit <= MAX_READ_LIMIT:
        raise ValueError(f"postgres: read limit {limit} outside 1 to {MAX_READ_LIMIT}")


def _commands_of(project_id: UUID, limit: int, after: int | None) -> Select[tuple[object, ...]]:
    query = select(commands).where(commands.c.project_id == project_id)
    if after is not None:
        query = query.where(commands.c.command_number < after)
    return query.order_by(commands.c.command_number.desc()).limit(limit)
