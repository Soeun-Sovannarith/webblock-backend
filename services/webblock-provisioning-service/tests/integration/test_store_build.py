"""The build store on PostgreSQL 18 (prototype task B3).

Start, result, sweep, stale and race cases, with revision 19 names (failure_code and log_ref on the
step, container_image_digest on the release), plus the prototype's three new methods
(load_build_target, record_log_ref, scan_running; decision 0014), and wave 6's pending scan and the
handle in the overdue scan (QA-10, OD-51). The store runs on a login of provisioning_build alone and
uses the database clock.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.integration._pg import ROLE_BUILD, Database
from tests.integration._rows import digest, sha
from tests.integration.test_store_support import (
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
    step_row,
)

from provisioning.core.application import (
    BuildAttemptParams,
    BuildStepKey,
    NotFound,
    StartBuildParams,
    SweepOutcome,
)
from provisioning.core.domain import (
    BuildAttemptPolicy,
    BuildResult,
    IllegalTransition,
    ReleaseStatus,
    ReleaseStepStatus,
    ResultMismatch,
)
from provisioning.infrastructure.postgres.build_store import PgBuildStore
from provisioning.infrastructure.postgres.errors import InvalidArgument

pytestmark = pytest.mark.asyncio

DURATION = timedelta(minutes=20)
POLICY = BuildAttemptPolicy(max_attempts=2)


def now() -> datetime:
    return datetime.now(UTC)


def accepted_release(db: Database) -> BuildStepKey:
    project, release, step = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "accepted", release_id=release, name="b")),
        (HOSTING, hosting_row(project)),
        (STEPS, step_row(release, 1, "pending", release_step_id=step)),
    )
    return BuildStepKey(project, release, step)


def result(
    key: BuildStepKey,
    attempt: int = 1,
    *,
    commit: str = "b",
    container_image_digest: str | None = None,
    failure_code: str = "",
    log_ref: str = "",
) -> BuildResult:
    return BuildResult(
        project_id=key.project_id,
        release_id=key.release_id,
        step_id=key.step_id,
        attempt=attempt,
        git_commit_sha=sha(commit),
        repository_id=4242,
        container_image_digest=container_image_digest,
        failure_code=failure_code,
        log_ref=log_ref,
    )


async def test_start_attempt_moves_release_and_step(db: Database) -> None:
    key = accepted_release(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        write = await store.start_attempt(StartBuildParams(key, DURATION))
        with pytest.raises(IllegalTransition):
            await store.start_attempt(StartBuildParams(key, DURATION))
        with pytest.raises(NotFound):
            await store.start_attempt(
                StartBuildParams(BuildStepKey(uuid4(), key.release_id, key.step_id), DURATION)
            )
    assert write.release.status == ReleaseStatus.BUILDING
    assert write.step.status == ReleaseStepStatus.RUNNING
    assert write.step.started_at is not None
    assert write.step.deadline_at == write.step.started_at + DURATION
    assert scalar(db, "SELECT status::text FROM provisioning.releases") == "building"
    stored = row_of(
        db, "SELECT status::text, started_at, deadline_at FROM provisioning.release_steps"
    )
    assert stored == ("running", write.step.started_at, write.step.deadline_at)


async def test_record_log_ref_only_while_running_and_unset(db: Database) -> None:
    key = accepted_release(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        assert not await store.record_log_ref(key, "jenkins-site-1")  # still pending
        await store.start_attempt(StartBuildParams(key, DURATION))
        assert await store.record_log_ref(key, "jenkins-site-1")
        assert not await store.record_log_ref(key, "jenkins-site-2")  # write-once
        target = await store.load_build_target(key.project_id, key.release_id)
        running = await store.scan_running(10)
    assert scalar(db, "SELECT log_ref FROM provisioning.release_steps") == "jenkins-site-1"
    assert target.step is not None
    assert target.step.log_ref == "jenkins-site-1"
    assert target.release.status == ReleaseStatus.BUILDING
    assert [(b.step, b.log_ref, b.git_commit_sha, b.repository_id) for b in running.builds] == [
        (key, "jenkins-site-1", sha("b"), 4242)
    ]
    assert running.now >= running.builds[0].started_at


async def test_load_build_target_of_another_project_is_not_found(db: Database) -> None:
    key = accepted_release(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        with pytest.raises(NotFound):
            await store.load_build_target(uuid4(), key.release_id)
        target = await store.load_build_target(key.project_id, key.release_id)
    assert target.step is not None
    assert target.step.status == ReleaseStepStatus.PENDING


async def test_apply_a_successful_result(db: Database) -> None:
    """FR-11.1, FR-11.2: apply a successful result."""
    key = accepted_release(db)
    attempt = BuildAttemptParams(POLICY, uuid4())
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        await store.start_attempt(StartBuildParams(key, DURATION))
        with pytest.raises(ResultMismatch):
            await store.apply_result(
                result(key, container_image_digest=digest("x"), commit="other"),
                attempt,
            )
        write = await store.apply_result(
            result(key, container_image_digest=digest("x"), log_ref="jenkins-site-1"), attempt
        )
    assert write.release.status == ReleaseStatus.BUILT
    assert write.next_step is None
    assert row_of(db, "SELECT status::text, container_image_digest FROM provisioning.releases") == (
        "built",
        digest("x"),
    )
    assert row_of(db, "SELECT status::text, log_ref FROM provisioning.release_steps") == (
        "succeeded",
        "jenkins-site-1",
    )


async def test_failed_results_queue_the_next_attempt_then_exhaust(db: Database) -> None:
    """FR-12.1, FR-12.2: failed results queue the next attempt then exhaust."""
    key = accepted_release(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        await store.start_attempt(StartBuildParams(key, DURATION))
        first = await store.apply_result(
            result(key, failure_code="build_failed"), BuildAttemptParams(POLICY, uuid4())
        )
        assert first.release.status == ReleaseStatus.BUILDING
        assert first.next_step is not None
        assert first.next_step.attempt == 2
        key2 = BuildStepKey(key.project_id, key.release_id, first.next_step.id)
        await store.start_attempt(StartBuildParams(key2, DURATION))
        last = await store.apply_result(
            result(key2, attempt=2, failure_code="build_failed"),
            BuildAttemptParams(POLICY, uuid4()),
        )
    assert last.release.status == ReleaseStatus.FAILED
    assert last.next_step is None
    assert row_of(db, "SELECT status::text, failure_code FROM provisioning.releases") == (
        "failed",
        "build_attempts_exhausted",
    )
    assert (
        scalar(db, "SELECT count(*) FROM provisioning.release_steps WHERE status = 'failed'") == 2
    )


async def test_the_sweeper_fails_an_overdue_step(db: Database) -> None:
    """FR-12.3: the sweeper fails an overdue step."""
    project, release, step = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "building", release_id=release, name="b")),
        (HOSTING, hosting_row(project)),
        (
            STEPS,
            step_row(
                release,
                1,
                "running",
                release_step_id=step,
                started_at=now() - timedelta(hours=1),
                deadline_at=now() - timedelta(minutes=1),
            ),
        ),
    )
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        overdue = await store.scan_overdue(10)
        assert [o.step for o in overdue] == [BuildStepKey(project, release, step)]
        outcome = await store.sweep_overdue(overdue[0], BuildAttemptParams(POLICY, uuid4()))
        stale = await store.sweep_overdue(overdue[0], BuildAttemptParams(POLICY, uuid4()))
    assert outcome == SweepOutcome.FAILED
    assert stale == SweepOutcome.STALE
    assert (
        scalar(
            db,
            "SELECT failure_code FROM provisioning.release_steps WHERE release_step_id = %s",
            step,
        )
        == "deadline_exceeded"
    )
    assert (
        scalar(db, "SELECT count(*) FROM provisioning.release_steps WHERE status = 'pending'") == 1
    )


async def test_the_sweeper_skips_a_release_another_transaction_holds(db: Database) -> None:
    project, release, step = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "building", release_id=release, name="b")),
        (HOSTING, hosting_row(project)),
        (
            STEPS,
            step_row(
                release,
                1,
                "running",
                release_step_id=step,
                started_at=now() - timedelta(hours=1),
                deadline_at=now() - timedelta(minutes=1),
            ),
        ),
    )
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        overdue = (await store.scan_overdue(10))[0]
        with db.admin() as holder, holder.transaction():
            holder.execute(
                "SELECT 1 FROM provisioning.releases WHERE release_id = %s FOR UPDATE", (release,)
            )
            busy = await store.sweep_overdue(overdue, BuildAttemptParams(POLICY, uuid4()))
    assert busy == SweepOutcome.BUSY
    assert scalar(db, "SELECT status::text FROM provisioning.release_steps") == "running"


async def test_concurrent_starts_of_one_attempt_start_it_once(db: Database) -> None:
    key = accepted_release(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        results = await asyncio.gather(
            *(store.start_attempt(StartBuildParams(key, DURATION)) for _ in range(4)),
            return_exceptions=True,
        )
    started = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, BaseException)]
    assert len(started) == 1
    assert all(isinstance(e, IllegalTransition) for e in refused), refused


async def test_the_overdue_scan_carries_the_handle_to_cancel(db: Database) -> None:
    project, release, step = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "building", release_id=release, name="b")),
        (HOSTING, hosting_row(project)),
        (
            STEPS,
            step_row(
                release,
                1,
                "running",
                release_step_id=step,
                started_at=now() - timedelta(hours=1),
                deadline_at=now() - timedelta(minutes=1),
                log_ref="jenkins/build/7",
            ),
        ),
    )
    async with role_engine(db, ROLE_BUILD) as engine:
        overdue = await PgBuildStore(engine, TIMEOUTS).scan_overdue(10)
    assert [(o.step.step_id, o.log_ref) for o in overdue] == [(step, "jenkins/build/7")]


async def test_qa10_the_pending_scan_finds_attempts_nothing_started(db: Database) -> None:
    """A first attempt is pending since its release's acceptance, a later one since the attempt
    before it ended; only those older than the grace, of accepted or building releases, oldest
    first, as provisioning_build."""
    t = now()
    first = BuildStepKey(uuid4(), uuid4(), uuid4())  # accepted 10 minutes ago, never started
    retry = BuildStepKey(uuid4(), uuid4(), uuid4())  # attempt 1 failed 2 minutes ago
    fresh = BuildStepKey(uuid4(), uuid4(), uuid4())  # accepted 5 seconds ago
    seed_rows(
        db,
        (
            RELEASES,
            release_row(
                first.project_id,
                "accepted",
                release_id=first.release_id,
                updated_at=t - timedelta(minutes=10),
            ),
        ),
        (
            RELEASES,
            release_row(
                retry.project_id,
                "building",
                release_id=retry.release_id,
                updated_at=t - timedelta(hours=1),
            ),
        ),
        (
            RELEASES,
            release_row(
                fresh.project_id,
                "accepted",
                release_id=fresh.release_id,
                updated_at=t - timedelta(seconds=5),
            ),
        ),
        (HOSTING, hosting_row(first.project_id)),
        (HOSTING, hosting_row(retry.project_id)),
        (HOSTING, hosting_row(fresh.project_id)),
        (STEPS, step_row(first.release_id, 1, "pending", release_step_id=first.step_id)),
        (
            STEPS,
            step_row(
                retry.release_id,
                1,
                "failed",
                started_at=t - timedelta(hours=1),
                finished_at=t - timedelta(minutes=2),
                deadline_at=t,
                failure_code="build_failed",
            ),
        ),
        (STEPS, step_row(retry.release_id, 2, "pending", release_step_id=retry.step_id)),
        (STEPS, step_row(fresh.release_id, 1, "pending", release_step_id=fresh.step_id)),
    )
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        scan = await store.scan_pending(10, timedelta(seconds=30))
        limited = await store.scan_pending(1, timedelta(seconds=30))
        later = await store.scan_pending(10, timedelta(minutes=5))
        with pytest.raises(InvalidArgument):
            await store.scan_pending(10, timedelta(0))
    assert [(b.step, b.attempt) for b in scan.builds] == [(first, 1), (retry, 2)]
    assert scan.builds[0].pending_since == t - timedelta(minutes=10)
    assert scan.builds[1].pending_since == t - timedelta(minutes=2)
    assert scan.now >= t
    assert [b.step for b in limited.builds] == [first]
    assert [b.step for b in later.builds] == [first]


async def test_the_pending_scan_skips_releases_past_building_and_running_steps(
    db: Database,
) -> None:
    t = now()
    running = accepted_release(db)
    gone = BuildStepKey(uuid4(), uuid4(), uuid4())
    seed_rows(
        db,
        (
            RELEASES,
            release_row(
                gone.project_id,
                "failed",
                release_id=gone.release_id,
                updated_at=t - timedelta(hours=1),
            ),
        ),
        (HOSTING, hosting_row(gone.project_id)),
        (STEPS, step_row(gone.release_id, 1, "pending", release_step_id=gone.step_id)),
    )
    async with role_engine(db, ROLE_BUILD) as engine:
        store = PgBuildStore(engine, TIMEOUTS)
        await store.start_attempt(StartBuildParams(running, DURATION))
        scan = await store.scan_pending(10, timedelta(seconds=1))
    assert scan.builds == ()
