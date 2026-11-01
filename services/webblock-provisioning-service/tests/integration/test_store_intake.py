"""The release-intake store on PostgreSQL 18 (prototype task B3).

Storing, acceptance and races, with revision 19 names (source_published_events, repository_id,
git_commit_sha, mismatched_repeat_count, max_attempts). The store runs on a login of
provisioning_intake alone, and uses the database clock (decision 0008 section 6).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_INTAKE, Database
from tests.integration._rows import sha
from tests.integration.test_store_support import (
    TIMEOUTS,
    role_engine,
    row_of,
    scalar,
)

from provisioning.core.application import (
    InboxTake,
    IntakeOutcome,
    IntakeParams,
    ReleaseAcceptanceDecision,
    ReleaseAcceptanceFacts,
    ReleaseAcceptanceParams,
    SourceEventStatus,
    TakeInboxParams,
    accept_new_release,
    reject_release,
)
from provisioning.core.domain import SourcePublished
from provisioning.infrastructure.postgres import PgReleaseIntakeStore

pytestmark = pytest.mark.asyncio


def accept_shop(facts: ReleaseAcceptanceFacts) -> ReleaseAcceptanceDecision:
    """The acceptance of a new release whose repository lookup answered the sub-domain shop."""
    return accept_new_release(facts, subdomain="shop")


TAKE = TakeInboxParams(batch_size=10, max_per_project=5, visibility=timedelta(seconds=30))


def event(project: UUID | None = None, *, repo: int = 77, commit: str = "c1") -> SourcePublished:
    return SourcePublished(
        project_id=project or uuid4(),
        generation_request_id=uuid4(),
        repository_id=repo,
        git_commit_sha=sha(commit),
    )


async def test_intake_stores_then_duplicate_then_mismatched_repeat(db: Database) -> None:
    """FR-10.1, FR-10.2: intake stores then duplicate then mismatched repeat."""
    ev = event()
    other_source = SourcePublished(
        project_id=ev.project_id,
        generation_request_id=ev.generation_request_id,
        repository_id=ev.repository_id,
        git_commit_sha=sha("another"),
    )
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        assert await s.intake(IntakeParams(ev, 10)) == IntakeOutcome.STORED
        assert await s.intake(IntakeParams(ev, 10)) == IntakeOutcome.DUPLICATE
        assert await s.intake(IntakeParams(other_source, 10)) == IntakeOutcome.MISMATCHED_REPEAT
    found = row_of(
        db,
        "SELECT git_commit_sha, mismatched_repeat_count, last_mismatched_repeat_at IS NOT NULL,"
        " max_attempts, status::text FROM provisioning_ops.source_published_events",
    )
    assert found == (ev.git_commit_sha, 1, True, 5, "pending")


async def test_intake_over_capacity(db: Database) -> None:
    project = uuid4()
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        assert await s.intake(IntakeParams(event(project), 2)) == IntakeOutcome.STORED
        assert await s.intake(IntakeParams(event(project), 2)) == IntakeOutcome.STORED
        assert await s.intake(IntakeParams(event(project), 2)) == IntakeOutcome.OVER_CAPACITY
    assert scalar(db, "SELECT count(*) FROM provisioning_ops.source_published_events") == 2


async def test_concurrent_intake_of_one_event_stores_one_row(db: Database) -> None:
    ev = event()
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        results = await asyncio.gather(*(s.intake(IntakeParams(ev, 10)) for _ in range(5)))
    assert sorted(results) == sorted([IntakeOutcome.STORED] + [IntakeOutcome.DUPLICATE] * 4)
    assert scalar(db, "SELECT count(*) FROM provisioning_ops.source_published_events") == 1


async def test_take_accept_and_redelivered_event(db: Database) -> None:
    """FR-9.1, FR-10.1: take accept and redelivered event."""
    ev = event()
    release_id, step_id = uuid4(), uuid4()
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        await s.intake(IntakeParams(ev, 10))
        batch = await s.take_due(TAKE)
        assert len(batch.entries) == 1
        entry = batch.entries[0]
        assert entry.attempts == 1
        assert entry.event == ev
        assert entry.next_attempt_at == batch.now + TAKE.visibility
        assert (await s.take_due(TAKE)).entries == ()
        result = await s.accept_release(
            ReleaseAcceptanceParams(entry.take(), release_id, step_id), accept_shop
        )
        assert result.status == SourceEventStatus.ACCEPTED
        assert result.created
        assert result.release_id == release_id
        # The same event delivered again: a duplicate, nothing more is written.
        assert await s.intake(IntakeParams(ev, 10)) == IntakeOutcome.DUPLICATE
        # The old take is superseded once the row is finished.
        again = await s.accept_release(
            ReleaseAcceptanceParams(entry.take(), uuid4(), uuid4()), accept_shop
        )
        assert again.superseded
    assert row_of(
        db,
        "SELECT status::text, git_commit_sha, created_at = updated_at FROM provisioning.releases",
    ) == ("accepted", ev.git_commit_sha, True)
    assert row_of(
        db, "SELECT step_name::text, attempt, status::text FROM provisioning.release_steps"
    ) == (
        "build",
        1,
        "pending",
    )
    assert row_of(
        db,
        "SELECT last_command_number, hosting_status::text, subdomain"
        " FROM provisioning.project_hosting",
    ) == (0, "online", "shop")
    assert row_of(
        db, "SELECT status::text, release_id FROM provisioning_ops.source_published_events"
    ) == ("accepted", release_id)


async def test_a_newer_take_supersedes_an_older_one(db: Database) -> None:
    ev = event()
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        await s.intake(IntakeParams(ev, 10))
        short = TakeInboxParams(
            batch_size=10, max_per_project=5, visibility=timedelta(microseconds=1)
        )
        old = (await s.take_due(short)).entries[0]
        new = (await s.take_due(TAKE)).entries[0]
        assert new.attempts == 2
        stale = await s.accept_release(
            ReleaseAcceptanceParams(old.take(), uuid4(), uuid4()), accept_shop
        )
        assert stale.superseded
        assert not await s.reject(old.take(), "repository_mismatch")
        assert not await s.backoff(old.take(), timedelta(seconds=1))
        assert not await s.fail(old.take(), "registry_unavailable")
        assert await s.backoff(new.take(), timedelta(hours=1))
        assert await s.reject(new.take(), "repository_mismatch")
    assert row_of(
        db, "SELECT status::text, outcome_code FROM provisioning_ops.source_published_events"
    ) == (
        "rejected",
        "repository_mismatch",
    )
    assert scalar(db, "SELECT count(*) FROM provisioning.releases") == 0


async def test_a_rejection_decision_finishes_the_row(db: Database) -> None:
    ev = event()
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        await s.intake(IntakeParams(ev, 10))
        entry = (await s.take_due(TAKE)).entries[0]
        result = await s.accept_release(
            ReleaseAcceptanceParams(entry.take(), uuid4(), uuid4()),
            lambda _facts: reject_release("repository_not_registered"),
        )
    assert result.status == SourceEventStatus.REJECTED
    assert result.outcome_code == "repository_not_registered"
    assert row_of(
        db,
        "SELECT status::text, finished_at IS NOT NULL"
        " FROM provisioning_ops.source_published_events",
    ) == (
        "rejected",
        True,
    )


async def test_fail_ends_a_taken_row(db: Database) -> None:
    async with role_engine(db, ROLE_INTAKE) as engine:
        s = PgReleaseIntakeStore(engine, TIMEOUTS, max_attempts=5)
        await s.intake(IntakeParams(event(), 10))
        entry = (await s.take_due(TAKE)).entries[0]
        assert await s.fail(entry.take(), "registry_unavailable")
        assert not await s.fail(
            InboxTake(entry.event.project_id, entry.event.generation_request_id, 1),
            "registry_unavailable",
        )
    assert (
        scalar(db, "SELECT status::text FROM provisioning_ops.source_published_events") == "failed"
    )
