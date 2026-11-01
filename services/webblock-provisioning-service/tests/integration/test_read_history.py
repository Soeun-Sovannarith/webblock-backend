"""The listing and diagnostics reads of ``PgReadStore`` on PostgreSQL 18, as a login of
``provisioning_api`` (decision 0014 section 21 rulings 6 and 7, section 23 item 4).

The diagnostics read of the "source published" event row needs the column grant of migration 00016
(task W6-E); on a schema without it the read is refused by PostgreSQL, which these tests report as
a failure, never a skip.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_API, Database
from tests.integration.test_store_support import (
    COMMANDS,
    EVENTS,
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
    at,
    command_row,
    hosting_row,
    release_row,
    role_engine,
    seed_rows,
    step_row,
)

from provisioning.core.application import (
    NotFound,
    ReleaseCursor,
    ReleaseKey,
    SourceEventStatus,
)
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.postgres import PgReadStore

pytestmark = pytest.mark.asyncio

LOG_REF = "builds/site-job/7"  # an opaque reference, as the domain requires


def _releases(db: Database, project: UUID, count: int, *, offset: int = 0) -> list[UUID]:
    ids = [uuid4() for _ in range(count)]
    seed_rows(
        db,
        *(
            (
                RELEASES,
                release_row(
                    project,
                    "failed",
                    release_id=release_id,
                    created_at=at(offset + n),
                    updated_at=at(offset + n),
                ),
            )
            for n, release_id in enumerate(ids)
        ),
    )
    return ids


def _commands(db: Database, project: UUID, release: UUID, count: int) -> list[UUID]:
    ids = [uuid4() for _ in range(count)]
    seed_rows(
        db,
        *(
            (
                COMMANDS,
                command_row(
                    project, CommandKind.DEPLOY, n + 1, "failed", release=release, command_id=cid
                ),
            )
            for n, cid in enumerate(ids)
        ),
    )
    return ids


def _project(db: Database) -> UUID:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=50)))
    return project


async def test_list_releases_pages_newest_first_within_the_project(db: Database) -> None:
    """FR-38.1: list releases pages newest first within the project."""
    project, other = _project(db), _project(db)
    mine = _releases(db, project, 5)
    _releases(db, other, 3)
    async with role_engine(db, ROLE_API) as engine:
        reads = PgReadStore(engine, TIMEOUTS)
        first = await reads.list_releases(project, limit=3, after=None)
        cursor = ReleaseCursor(created_at=first[-1].created_at, release_id=first[-1].id)
        rest = await reads.list_releases(project, limit=3, after=cursor)
        none = await reads.list_releases(uuid4(), limit=3, after=None)
    assert [r.id for r in first] == [mine[4], mine[3], mine[2]]
    assert [r.id for r in rest] == [mine[1], mine[0]]
    assert none == ()


async def test_list_releases_breaks_a_time_tie_by_release_id(db: Database) -> None:
    project = _project(db)
    ids = sorted((uuid4() for _ in range(3)), reverse=True)
    seed_rows(
        db,
        *(
            (RELEASES, release_row(project, "accepted", release_id=i, created_at=at(1)))
            for i in ids
        ),
    )
    async with role_engine(db, ROLE_API) as engine:
        reads = PgReadStore(engine, TIMEOUTS)
        first = await reads.list_releases(project, limit=1, after=None)
        cursor = ReleaseCursor(created_at=at(1), release_id=first[0].id)
        rest = await reads.list_releases(project, limit=5, after=cursor)
    assert [r.id for r in (*first, *rest)] == ids


async def test_list_commands_pages_by_command_number(db: Database) -> None:
    project, other = _project(db), _project(db)
    (release,) = _releases(db, project, 1)
    (other_release,) = _releases(db, other, 1)
    ids = _commands(db, project, release, 4)
    _commands(db, other, other_release, 2)
    async with role_engine(db, ROLE_API) as engine:
        reads = PgReadStore(engine, TIMEOUTS)
        first = await reads.list_commands(project, limit=3, after=None)
        rest = await reads.list_commands(project, limit=3, after=first[-1].command_number)
    assert [c.id for c in first] == [ids[3], ids[2], ids[1]]
    assert [c.id for c in rest] == [ids[0]]


async def test_a_limit_outside_the_bound_is_refused_before_any_sql(db: Database) -> None:
    project = _project(db)
    async with role_engine(db, ROLE_API) as engine:
        reads = PgReadStore(engine, TIMEOUTS)
        for limit in (0, 102):
            with pytest.raises(ValueError):
                await reads.list_commands(project, limit=limit, after=None)
            with pytest.raises(ValueError):
                await reads.read_diagnostics(
                    ReleaseKey(project, uuid4()), step_limit=limit, command_limit=5
                )


def _event_row(project: UUID, release: UUID, generation_request: UUID) -> dict[str, object]:
    return {
        "project_id": project,
        "generation_request_id": generation_request,
        "repository_id": 4242,
        "git_commit_sha": "a" * 40,
        "status": "accepted",
        "attempts": 2,
        "next_attempt_at": at(5),
        "outcome_code": None,
        "release_id": release,
        "mismatched_repeat_count": 1,
        "last_mismatched_repeat_at": at(4),
        "received_at": at(1),
        "updated_at": at(5),
        "finished_at": at(5),
        "max_attempts": 10,
        "last_manual_retry_by": None,
        "last_manual_retry_at": None,
    }


async def test_diagnostics_are_bounded_scoped_and_carry_the_event_outcome(db: Database) -> None:
    """FR-41.1, FR-41.2: diagnostics are bounded scoped and carry the event outcome."""
    project, other = _project(db), _project(db)
    release, generation_request = uuid4(), uuid4()
    seed_rows(
        db,
        (
            RELEASES,
            release_row(
                project,
                "failed",
                release_id=release,
                generation_request_id=generation_request,
                failure_summary="build output with a secret",
            ),
        ),
        *(
            (
                STEPS,
                step_row(
                    release,
                    n,
                    "failed",
                    started_at=at(n),
                    finished_at=at(n) + timedelta(seconds=1),
                    failure_code="build_failed",
                    log_ref=LOG_REF,
                ),
            )
            for n in range(1, 31)
        ),
        (EVENTS, _event_row(project, release, generation_request)),
    )
    _commands(db, project, release, 15)
    (other_release,) = _releases(db, other, 1)
    _commands(db, other, other_release, 3)
    async with role_engine(db, ROLE_API) as engine:
        reads = PgReadStore(engine, TIMEOUTS)
        facts = await reads.read_diagnostics(
            ReleaseKey(project, release), step_limit=21, command_limit=11
        )
        with pytest.raises(NotFound):
            await reads.read_diagnostics(
                ReleaseKey(other, release), step_limit=21, command_limit=11
            )
    assert facts.release.id == release
    assert [s.attempt for s in facts.steps] == list(range(30, 9, -1))
    assert [c.command_number for c in facts.commands] == list(range(15, 4, -1))
    assert all(c.project_id == project for c in facts.commands)
    event = facts.source_event
    assert event is not None
    assert (event.status, event.release_id, event.attempts, event.mismatched_repeat_count) == (
        SourceEventStatus.ACCEPTED,
        release,
        2,
        1,
    )
    assert event.received_at == at(1) and event.finished_at == at(5)


async def test_diagnostics_without_an_event_row(db: Database) -> None:
    project = _project(db)
    (release,) = _releases(db, project, 1)
    async with role_engine(db, ROLE_API) as engine:
        facts = await PgReadStore(engine, TIMEOUTS).read_diagnostics(
            ReleaseKey(project, release), step_limit=21, command_limit=11
        )
    assert facts.source_event is None
    assert facts.steps == ()
