"""The deploy store's fenced reads and writes on PostgreSQL 18 (prototype task F1; decision 0014
sections 3 and 12).

Every store runs on a login that is a member of provisioning_worker alone; one test shows that a
provisioning_build login cannot spend a release's candidate attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_BUILD, ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
    Row,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
    step_row,
)

from provisioning.core.application import ClaimLost, Fence
from provisioning.core.application.deploys.ports import CandidateAttemptStart
from provisioning.core.domain import (
    MAX_CANDIDATE_ATTEMPTS,
    CandidateFailureEffect,
    CandidatePlanKind,
    CommandKind,
    IllegalTransition,
    ReleaseStepName,
    ReleaseStepStatus,
    StaleFence,
    deploy_failure_summary,
)
from provisioning.infrastructure.postgres import DatabaseError
from provisioning.infrastructure.postgres.deploy_store import PgDeployStore

pytestmark = pytest.mark.asyncio

TIMEOUT = timedelta(minutes=5)
TTL = timedelta(minutes=2)
STATE = "SELECT status::text FROM provisioning.releases WHERE release_id = %s"
COMMAND = "SELECT status::text, failure_code FROM provisioning.commands WHERE command_id = %s"
DISPATCH_END = (
    "SELECT finished_at IS NOT NULL, last_failure_code FROM provisioning_ops.command_dispatch "
    "WHERE command_id = %s"
)
STEPS_OF = (
    "SELECT count(*) FROM provisioning.release_steps WHERE release_id = %s "
    "AND step_name = 'candidate'"
)


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Seeded:
    fence: Fence
    project: UUID
    release: UUID
    command: UUID


def deploy_running(
    db: Database,
    *,
    number: int = 1,
    project: UUID | None = None,
    steps: tuple[Row, ...] = (),
    extra: tuple[tuple[str, Row], ...] = (),
    hosting: Row | None = None,
    release_status: str = "built",
) -> Seeded:
    """A built release with a running deploy command at ``number``, claimed (claim number 1)."""
    project = project or uuid4()
    release, command = uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, release_status, release_id=release, name="new")),
        (HOSTING, hosting or hosting_row(project, number=number)),
        (
            COMMANDS,
            command_row(
                project, CommandKind.DEPLOY, number, "running", release=release, command_id=command
            ),
        ),
        (
            DISPATCH,
            dispatch_row(
                command,
                claim_number=1,
                claimed_by_worker="worker-1",
                claim_expires_at=now() + timedelta(minutes=1),
                execution_attempts=1,
            ),
        ),
        *extra,
        *((STEPS, {**s, "release_id": release}) for s in steps),
    )
    fence = Fence(project_id=project, command_id=command, command_number=number, claim_number=1)
    return Seeded(fence, project, release, command)


def candidate(attempt: int, status: str, **over: object) -> Row:
    started = now() - timedelta(minutes=30)
    times: Row = {}
    if status != "pending":
        times = {"started_at": started, "deadline_at": started + timedelta(minutes=5)}
    if status in ("succeeded", "failed"):
        times["finished_at"] = started + timedelta(minutes=1)
    if status == "failed":
        times["failure_code"] = "candidate_not_ready"
    return step_row(uuid4(), attempt, status, name="candidate", **{**times, **over})


def bump_fence(db: Database, project: UUID, number: int) -> None:
    """A newer command was accepted: the project's command number moves past the fence (seeded
    through B2's path, so the guard that ties the number to a command row is skipped)."""
    with db.admin() as admin, admin.transaction():
        admin.execute("SET LOCAL session_replication_role = replica")
        admin.execute(
            "UPDATE provisioning.project_hosting SET last_command_number = %s "
            "WHERE project_id = %s",
            (number, project),
        )


def start() -> CandidateAttemptStart:
    return CandidateAttemptStart(step_id=uuid4(), timeout=TIMEOUT)


# --- reads and the claim -----------------------------------------------------------------------


async def test_load_reads_the_rows_and_the_last_candidate_attempt(db: Database) -> None:
    s = deploy_running(db, steps=(candidate(1, "failed"), candidate(2, "failed")))
    async with role_engine(db, ROLE_WORKER) as engine:
        facts = await PgDeployStore(engine, TIMEOUTS).load_deploy_facts(s.fence)
    assert facts.command.id == s.command
    assert facts.release.id == s.release
    assert facts.hosting.last_command_number == 1
    assert facts.last_candidate_step is not None
    assert (facts.last_candidate_step.name, facts.last_candidate_step.attempt) == (
        ReleaseStepName.CANDIDATE,
        2,
    )


async def test_renew_claim_extends_the_claim_on_the_database_clock(db: Database) -> None:
    s = deploy_running(db)
    before = now()
    async with role_engine(db, ROLE_WORKER) as engine:
        await PgDeployStore(engine, TIMEOUTS).renew_claim(s.fence, TTL)
    expires = scalar(
        db,
        "SELECT claim_expires_at FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        s.command,
    )
    assert isinstance(expires, datetime)
    assert expires >= before + TTL - timedelta(seconds=5)


async def test_a_stale_fence_is_refused_and_nothing_is_written(db: Database) -> None:
    """FR-25.1: a stale fence is refused and nothing is written."""
    s = deploy_running(db, number=1)
    stale = Fence(project_id=s.project, command_id=s.command, command_number=1, claim_number=1)
    bump_fence(db, s.project, 2)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        with pytest.raises(StaleFence):
            await store.load_deploy_facts(stale)
        with pytest.raises(StaleFence):
            await store.renew_claim(stale, TTL)
        with pytest.raises(StaleFence):
            await store.begin_candidate_attempt(stale, start())
        with pytest.raises(StaleFence):
            await store.return_replaced_candidates(stale)
    assert scalar(db, STEPS_OF, s.release) == 0
    assert row_of(db, COMMAND, s.command) == ("running", None)


async def test_a_lost_claim_is_refused_and_nothing_is_written(db: Database) -> None:
    """FR-25.1: a lost claim is refused and nothing is written."""
    s = deploy_running(db)
    lost = Fence(project_id=s.project, command_id=s.command, command_number=1, claim_number=2)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        with pytest.raises(ClaimLost):
            await store.renew_claim(lost, TTL)
        with pytest.raises(ClaimLost):
            await store.begin_candidate_attempt(lost, start())
    assert scalar(db, STEPS_OF, s.release) == 0


async def test_a_finished_command_is_refused(db: Database) -> None:
    s = deploy_running(db)
    with db.admin() as admin, admin.transaction():
        admin.execute("SET LOCAL session_replication_role = replica")
        admin.execute(
            "UPDATE provisioning.commands SET status = 'failed', failure_code = 'x' "
            "WHERE command_id = %s",
            (s.command,),
        )
    async with role_engine(db, ROLE_WORKER) as engine:
        with pytest.raises(IllegalTransition):
            await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())


# --- beginning an attempt ----------------------------------------------------------------------


async def test_begin_starts_attempt_one_with_its_deadline(db: Database) -> None:
    s = deploy_running(db)
    request = start()
    async with role_engine(db, ROLE_WORKER) as engine:
        attempt = await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, request)
    assert attempt.kind is CandidatePlanKind.START
    assert attempt.step is not None
    assert (attempt.step.id, attempt.step.attempt, attempt.step.status) == (
        request.step_id,
        1,
        ReleaseStepStatus.RUNNING,
    )
    status, attempt_no, started, deadline = row_of(
        db,
        "SELECT status::text, attempt, started_at, deadline_at FROM provisioning.release_steps "
        "WHERE release_step_id = %s",
        request.step_id,
    )
    assert (status, attempt_no) == ("running", 1)
    assert isinstance(started, datetime)
    assert deadline == started + TIMEOUT
    assert attempt.step.deadline_at == deadline


async def test_begin_resumes_an_unexpired_attempt(db: Database) -> None:
    running = candidate(1, "running", deadline_at=now() + timedelta(minutes=3))
    s = deploy_running(db, steps=(running,))
    async with role_engine(db, ROLE_WORKER) as engine:
        attempt = await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())
    assert attempt.kind is CandidatePlanKind.RESUME
    assert attempt.step is not None
    assert attempt.step.id == running["release_step_id"]
    assert scalar(db, STEPS_OF, s.release) == 1


async def test_begin_fails_an_expired_attempt_and_starts_the_next(db: Database) -> None:
    expired = candidate(1, "running")
    s = deploy_running(db, steps=(expired,))
    async with role_engine(db, ROLE_WORKER) as engine:
        attempt = await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())
    assert attempt.kind is CandidatePlanKind.START
    assert attempt.step is not None
    assert attempt.step.attempt == 2
    assert row_of(
        db,
        "SELECT status::text, failure_code FROM provisioning.release_steps "
        "WHERE release_step_id = %s",
        expired["release_step_id"],
    ) == ("failed", "deadline_exceeded")


async def test_begin_after_a_succeeded_attempt_starts_the_next(db: Database) -> None:
    s = deploy_running(db, steps=(candidate(1, "succeeded"),))
    async with role_engine(db, ROLE_WORKER) as engine:
        attempt = await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())
    assert attempt.step is not None
    assert attempt.step.attempt == 2


async def test_begin_with_no_attempt_left_fails_the_release_and_the_command(db: Database) -> None:
    steps = tuple(candidate(n, "failed") for n in range(1, MAX_CANDIDATE_ATTEMPTS + 1))
    s = deploy_running(db, steps=steps)
    async with role_engine(db, ROLE_WORKER) as engine:
        attempt = await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())
    assert attempt.kind is CandidatePlanKind.EXHAUSTED
    assert attempt.step is None
    assert row_of(
        db,
        "SELECT status::text, failure_code, failure_summary FROM provisioning.releases "
        "WHERE release_id = %s",
        s.release,
    ) == (
        "failed",
        "candidate_attempts_exhausted",
        deploy_failure_summary("candidate_attempts_exhausted"),
    )
    assert row_of(db, COMMAND, s.command) == ("failed", "candidate_attempts_exhausted")
    assert row_of(db, DISPATCH_END, s.command) == (True, "candidate_attempts_exhausted")
    assert scalar(db, STEPS_OF, s.release) == MAX_CANDIDATE_ATTEMPTS


async def test_a_build_login_cannot_begin_a_candidate_attempt(db: Database) -> None:
    s = deploy_running(db)
    async with role_engine(db, ROLE_BUILD) as engine:
        with pytest.raises(DatabaseError):
            await PgDeployStore(engine, TIMEOUTS).begin_candidate_attempt(s.fence, start())
    assert scalar(db, STEPS_OF, s.release) == 0


# --- ending an attempt -------------------------------------------------------------------------


async def test_a_ready_candidate_makes_the_release_candidate_ready(db: Database) -> None:
    """FR-17.1: a ready candidate makes the release candidate ready."""
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(s.fence, start())
        assert attempt.step is not None
        assert await store.end_candidate_attempt(s.fence, attempt.step.id, None) is None
    assert row_of(db, STATE, s.release) == ("candidate_ready",)
    assert row_of(db, COMMAND, s.command) == ("succeeded", None)
    assert row_of(db, DISPATCH_END, s.command) == (True, None)
    assert (
        scalar(
            db,
            "SELECT status::text FROM provisioning.release_steps WHERE release_step_id = %s",
            attempt.step.id,
        )
        == "succeeded"
    )


async def test_a_failed_candidate_fails_the_command_and_keeps_the_release_built(
    db: Database,
) -> None:
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(s.fence, start())
        assert attempt.step is not None
        failure = await store.end_candidate_attempt(s.fence, attempt.step.id, "image_pull_failed")
    assert failure is not None
    assert failure.effect is CandidateFailureEffect.COMMAND_FAILS
    assert row_of(db, STATE, s.release) == ("built",)
    assert row_of(db, COMMAND, s.command) == ("failed", "image_pull_failed")
    assert row_of(db, DISPATCH_END, s.command) == (True, "image_pull_failed")
    assert row_of(
        db,
        "SELECT status::text, failure_code FROM provisioning.release_steps "
        "WHERE release_step_id = %s",
        attempt.step.id,
    ) == ("failed", "image_pull_failed")


async def test_a_refused_candidate_fails_the_release(db: Database) -> None:
    """FR-17.2: a refused candidate fails the release."""
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(s.fence, start())
        assert attempt.step is not None
        failure = await store.end_candidate_attempt(s.fence, attempt.step.id, "candidate_refused")
    assert failure is not None
    assert failure.effect is CandidateFailureEffect.RELEASE_FAILS
    assert row_of(
        db,
        "SELECT status::text, failure_code, failure_summary FROM provisioning.releases "
        "WHERE release_id = %s",
        s.release,
    ) == ("failed", "candidate_refused", deploy_failure_summary("candidate_refused"))
    assert row_of(db, COMMAND, s.command) == ("failed", "candidate_refused")


async def test_a_transient_end_writes_only_the_step(db: Database) -> None:
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(s.fence, start())
        assert attempt.step is not None
        failure = await store.end_candidate_attempt(s.fence, attempt.step.id, "cluster_unavailable")
    assert failure is not None
    assert failure.effect is CandidateFailureEffect.RETRY
    assert row_of(db, COMMAND, s.command) == ("running", None)
    assert row_of(db, DISPATCH_END, s.command) == (False, None)
    assert row_of(db, STATE, s.release) == ("built",)


async def test_an_end_under_a_stale_fence_writes_nothing(db: Database) -> None:
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(s.fence, start())
        assert attempt.step is not None
        bump_fence(db, s.project, 2)
        with pytest.raises(StaleFence):
            await store.end_candidate_attempt(s.fence, attempt.step.id, None)
    assert row_of(db, STATE, s.release) == ("built",)
    assert row_of(db, COMMAND, s.command) == ("running", None)
    assert (
        scalar(
            db,
            "SELECT status::text FROM provisioning.release_steps WHERE release_step_id = %s",
            attempt.step.id,
        )
        == "running"
    )


async def test_ending_an_unknown_step_is_refused(db: Database) -> None:
    s = deploy_running(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        with pytest.raises(IllegalTransition):
            await PgDeployStore(engine, TIMEOUTS).end_candidate_attempt(s.fence, uuid4(), None)


# --- replaced candidates -----------------------------------------------------------------------


async def test_a_replaced_candidate_returns_to_built(db: Database) -> None:
    """FR-17.4: a replaced candidate returns to built."""
    project, older = uuid4(), uuid4()
    s = deploy_running(
        db,
        project=project,
        extra=(
            (
                RELEASES,
                release_row(project, "candidate_ready", release_id=older, name="older"),
            ),
        ),
    )
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        assert await store.return_replaced_candidates(s.fence) == (older,)
        assert await store.return_replaced_candidates(s.fence) == ()
    assert row_of(db, STATE, older) == ("built",)
    assert row_of(db, STATE, s.release) == ("built",)
    assert row_of(db, COMMAND, s.command) == ("running", None)


async def test_candidate_ready_and_back_to_built_across_two_deploys(db: Database) -> None:
    first = deploy_running(db, number=1)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgDeployStore(engine, TIMEOUTS)
        attempt = await store.begin_candidate_attempt(first.fence, start())
        assert attempt.step is not None
        await store.end_candidate_attempt(first.fence, attempt.step.id, None)
        assert row_of(db, STATE, first.release) == ("candidate_ready",)

        # A newer deploy of the project (command number 2) replaces the candidate.
        newer, command = uuid4(), uuid4()
        seed_rows(
            db,
            (RELEASES, release_row(first.project, "built", release_id=newer, name="newer")),
            (
                COMMANDS,
                command_row(
                    first.project,
                    CommandKind.DEPLOY,
                    2,
                    "running",
                    release=newer,
                    command_id=command,
                ),
            ),
            (
                DISPATCH,
                dispatch_row(
                    command,
                    claim_number=1,
                    claimed_by_worker="worker-1",
                    claim_expires_at=now() + timedelta(minutes=1),
                    execution_attempts=1,
                ),
            ),
        )
        bump_fence(db, first.project, 2)
        second = Fence(
            project_id=first.project, command_id=command, command_number=2, claim_number=1
        )
        assert await store.return_replaced_candidates(second) == (first.release,)
        # The replaced release can be deployed again later: its succeeded attempt is followed by
        # a next one (decision 0011 section 12).
        with pytest.raises(StaleFence):
            await store.return_replaced_candidates(first.fence)
    assert row_of(db, STATE, first.release) == ("built",)
