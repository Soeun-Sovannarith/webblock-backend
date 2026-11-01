"""The job store and the fenced worker writes on PostgreSQL 18 (prototype task B3).

Dispatch, claims, fences, hooks and worker writes, with revision 19 names
(command_dispatch, claim_number, claimed_by_worker, claim_expires_at, execution_attempts,
next_publish_at, last_broker_ack_at, retry_after, last_failure_code). The store runs on a login
that is a member of provisioning_worker alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
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

from provisioning.core.application import (
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    CommandFinish,
    DeployOutcome,
    Fence,
    WorkRef,
)
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    Failure,
    IllegalTransition,
    StaleFence,
)
from provisioning.infrastructure.postgres import PgJobStore

pytestmark = pytest.mark.asyncio

VISIBILITY = timedelta(seconds=30)
TTL = timedelta(seconds=60)


def now() -> datetime:
    return datetime.now(UTC)


def deploy_in_flight(
    db: Database, status: str = "accepted", **dispatch: object
) -> tuple[WorkRef, UUID]:
    """A built release with a deploy command at number 1 and its dispatch row."""
    project, built, command = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "built", release_id=built)),
        (HOSTING, hosting_row(project, number=1)),
        (
            COMMANDS,
            command_row(project, CommandKind.DEPLOY, 1, status, release=built, command_id=command),
        ),
        (DISPATCH, dispatch_row(command, **dispatch)),
    )
    ref = WorkRef(
        command_id=command,
        project_id=project,
        kind=CommandKind.DEPLOY,
        command_number=1,
        release_id=built,
    )
    return ref, built


# --- dispatch ---------------------------------------------------------------------------------


async def test_take_due_ack_then_redeliver_after_visibility(db: Database) -> None:
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        taken = await store.take_due(10, VISIBILITY)
        assert [d.ref for d in taken] == [ref]
        assert taken[0].publish_attempts == 1
        assert taken[0].correlation_id == "corr-1"
        assert not taken[0].deadline_passed
        # Taken again only after the visibility timeout.
        assert await store.take_due(10, VISIBILITY) == []
        assert await store.record_broker_ack(taken[0], timedelta(microseconds=1))
        # Acknowledged: due again after redeliver_after (1 µs), so a lost job is published again.
        again = await store.take_due(10, VISIBILITY)
    assert [d.ref for d in again] == [ref]
    assert again[0].publish_attempts == 2
    acked = scalar(
        db, "SELECT last_broker_ack_at IS NOT NULL FROM provisioning_ops.command_dispatch"
    )
    assert acked is True


async def test_an_ack_for_an_older_take_changes_nothing(db: Database) -> None:
    deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        first = (await store.take_due(10, timedelta(microseconds=1)))[0]
        second = (await store.take_due(10, VISIBILITY))[0]
        assert second.publish_attempts == 2
        assert not await store.record_broker_ack(first, VISIBILITY)
        assert await store.record_broker_ack(second, VISIBILITY)


async def test_a_publish_failure_is_retried_later(db: Database) -> None:
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        taken = (await store.take_due(10, VISIBILITY))[0]
        assert await store.record_publish_failure(taken, timedelta(hours=1))
        assert await store.take_due(10, VISIBILITY) == []
        assert await store.record_publish_failure(taken, timedelta(microseconds=1))
        again = await store.take_due(10, VISIBILITY)
    assert [d.ref for d in again] == [ref]


async def test_a_dispatch_row_never_published_is_found_again(db: Database) -> None:
    # A command row committed with its dispatch row, and no broker ack ever recorded (the
    # process died between commit and publish): the next take finds it from SQL alone.
    ref, _ = deploy_in_flight(db, publish_attempts=3, next_publish_at=now() - timedelta(seconds=1))
    async with role_engine(db, ROLE_WORKER) as engine:
        taken = await PgJobStore(engine, TIMEOUTS).take_due(10, VISIBILITY)
    assert [(d.ref, d.publish_attempts) for d in taken] == [(ref, 4)]


async def test_count_unfinished_without_dispatch(db: Database) -> None:
    deploy_in_flight(db)
    project, built = uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "built", release_id=built)),
        (HOSTING, hosting_row(project, number=1)),
        (COMMANDS, command_row(project, CommandKind.DEPLOY, 1, "accepted", release=built)),
    )
    async with role_engine(db, ROLE_WORKER) as engine:
        assert await PgJobStore(engine, TIMEOUTS).count_unfinished_without_dispatch() == 1


async def test_a_live_claim_is_not_published(db: Database) -> None:
    """FR-15: a live claim is not published."""
    deploy_in_flight(
        db,
        status="running",
        claim_number=1,
        execution_attempts=1,
        claimed_by_worker="w-1",
        claim_expires_at=now() + timedelta(minutes=5),
        next_publish_at=now() - timedelta(seconds=1),
        publish_attempts=1,
    )
    async with role_engine(db, ROLE_WORKER) as engine:
        taken = await PgJobStore(engine, TIMEOUTS).take_due(10, VISIBILITY)
    assert taken == []
    assert scalar(db, "SELECT publish_attempts FROM provisioning_ops.command_dispatch") == 1


# --- claims -----------------------------------------------------------------------------------


async def test_claim_extend_and_held(db: Database) -> None:
    """FR-15.1: claim extend and held."""
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        result = await store.claim(ClaimRequest(ref, "w-1", TTL))
        assert result.outcome == ClaimOutcome.CLAIMED
        claim = result.claim
        assert claim is not None
        assert (claim.claim_number, claim.execution_attempt) == (1, 1)
        held = await store.claim(ClaimRequest(ref, "w-2", TTL))
        assert held.outcome == ClaimOutcome.HELD
        extended = await store.extend_claim(claim, timedelta(minutes=10))
    assert extended.expires_at > claim.expires_at
    assert extended.claim_number == claim.claim_number
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "running"
    assert row_of(
        db, "SELECT claimed_by_worker, claim_number FROM provisioning_ops.command_dispatch"
    ) == ("w-1", 1)


async def test_an_expired_claim_is_taken_over_and_the_old_claim_cannot_write(db: Database) -> None:
    """FR-25.1: an expired claim is taken over and the old claim cannot write."""
    ref, _ = deploy_in_flight(
        db,
        status="running",
        claim_number=1,
        execution_attempts=1,
        claimed_by_worker="w-dead",
        claim_expires_at=now() - timedelta(seconds=1),
    )
    dead = Claim(
        ref=ref,
        claimed_by_worker="w-dead",
        claim_number=1,
        execution_attempt=1,
        expires_at=now() - timedelta(seconds=1),
        deadline_at=now() + timedelta(hours=1),
    )
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        taken = await store.claim(ClaimRequest(ref, "w-2", TTL))
        assert taken.outcome == ClaimOutcome.CLAIMED
        assert taken.claim is not None
        assert (taken.claim.claim_number, taken.claim.execution_attempt) == (2, 2)
        with pytest.raises(ClaimLost):
            await store.extend_claim(dead, TTL)
        with pytest.raises(ClaimLost):
            await store.release_claim(dead, TTL, "transient_error")
        with pytest.raises(ClaimLost):
            await store.finish_command(
                dead.fence(), CommandFinish(CommandStatus.FAILED, "worker_gone", now())
            )
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "running"
    assert scalar(db, "SELECT claimed_by_worker FROM provisioning_ops.command_dispatch") == "w-2"


async def test_release_on_stop_schedules_the_retry(db: Database) -> None:
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        await store.release_claim(claim, timedelta(hours=1), "worker_stopping")
        backoff = await store.claim(ClaimRequest(ref, "w-2", TTL))
    assert backoff.outcome == ClaimOutcome.BACKOFF
    found = row_of(
        db,
        "SELECT claimed_by_worker, claim_expires_at, last_failure_code,"
        " retry_after > now(), next_publish_at = retry_after"
        " FROM provisioning_ops.command_dispatch",
    )
    assert found == (None, None, "worker_stopping", True, True)


async def test_fr21_5_a_claim_reads_the_last_failure_code_of_its_row(db: Database) -> None:
    """FR-21.5 (TR-57, G12): a claim carries the dispatch row's last failure code, so the next
    try knows how the one before it ended. A first claim finds none."""
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        first = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert first is not None
        assert first.last_failure_code is None
        await store.release_claim(first, timedelta(seconds=30), "route_not_accepted")
        with db.admin() as conn:
            conn.execute(
                "UPDATE provisioning_ops.command_dispatch"
                " SET retry_after = now() - interval '1 second'"
            )
        second = await store.claim(ClaimRequest(ref, "w-2", TTL))
    assert second.outcome == ClaimOutcome.CLAIMED
    assert second.claim is not None
    assert second.claim.execution_attempt == 2
    assert second.claim.last_failure_code == "route_not_accepted"


async def test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try(
    db: Database,
) -> None:
    """FR-21.2 (TR-57, G1): the wait between tries lives in retry_after. A duplicate delivery
    before it is BACKOFF: no claim is made and execution_attempts does not grow."""
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        await store.release_claim(claim, timedelta(seconds=30), "route_not_accepted")
        released = row_of(
            db,
            "SELECT claimed_by_worker, claim_expires_at,"
            " EXTRACT(EPOCH FROM retry_after - now())::float8,"
            " next_publish_at = retry_after, last_failure_code"
            " FROM provisioning_ops.command_dispatch",
        )
        backoff = await store.claim(ClaimRequest(ref, "w-2", TTL))
    holder, expires, wait, publish_at_wait, code = released
    assert (holder, expires, publish_at_wait, code) == (None, None, True, "route_not_accepted")
    assert isinstance(wait, float)
    assert 25.0 < wait <= 30.0
    assert backoff.outcome == ClaimOutcome.BACKOFF
    assert backoff.claim is None
    assert row_of(
        db,
        "SELECT execution_attempts, claim_number, claimed_by_worker"
        " FROM provisioning_ops.command_dispatch",
    ) == (1, 1, None)
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "running"


async def test_tr57_extend_claim_keeps_the_last_failure_code(db: Database) -> None:
    """TR-57 (G12, design review N-5): the heartbeat keeps the claim's last failure code."""
    ref, _ = deploy_in_flight(db, last_failure_code="route_not_accepted")
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        assert claim.last_failure_code == "route_not_accepted"
        extended = await store.extend_claim(claim, timedelta(minutes=10))
    assert extended.expires_at > claim.expires_at
    assert extended.last_failure_code == "route_not_accepted"


async def test_claim_outcomes_for_wrong_references(db: Database) -> None:
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        missing = WorkRef(uuid4(), ref.project_id, CommandKind.DEPLOY, 1, ref.release_id)
        assert (await store.claim(ClaimRequest(missing, "w", TTL))).outcome == ClaimOutcome.NO_WORK
        stale = WorkRef(ref.command_id, ref.project_id, CommandKind.DEPLOY, 2, ref.release_id)
        outcome = (await store.claim(ClaimRequest(stale, "w", TTL))).outcome
        assert outcome == ClaimOutcome.STALE_COMMAND_NUMBER
        other = WorkRef(ref.command_id, uuid4(), CommandKind.DEPLOY, 1, ref.release_id)
        assert (await store.claim(ClaimRequest(other, "w", TTL))).outcome == ClaimOutcome.MISMATCH


# --- fencing and outcomes ---------------------------------------------------------------------


async def test_a_stale_fence_is_refused(db: Database) -> None:
    """FR-25.1: a stale fence is refused."""
    ref, _ = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        stale = Fence(ref.project_id, ref.command_id, 2, claim.claim_number)
        with pytest.raises(StaleFence):
            await store.finish_command(stale, CommandFinish(CommandStatus.FAILED, "x_y", now()))
        with pytest.raises(StaleFence):
            await store.apply_deploy_outcome(stale, DeployOutcome(at=now()))
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "running"


async def test_finish_command_failed_finishes_the_dispatch_row(db: Database) -> None:
    ref, built = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        await store.finish_command(
            claim.fence(), CommandFinish(CommandStatus.FAILED, "budget_exhausted", now())
        )
        with pytest.raises(IllegalTransition):
            await store.finish_command(
                claim.fence(), CommandFinish(CommandStatus.FAILED, "budget_exhausted", now())
            )
        # A finished command is not taken again.
        assert await store.take_due(10, VISIBILITY) == []
    assert row_of(db, "SELECT status::text, failure_code FROM provisioning.commands") == (
        "failed",
        "budget_exhausted",
    )
    assert row_of(
        db,
        "SELECT finished_at IS NOT NULL, last_failure_code FROM provisioning_ops.command_dispatch",
    ) == (
        True,
        "budget_exhausted",
    )
    assert (
        scalar(db, "SELECT status::text FROM provisioning.releases WHERE release_id = %s", built)
        == "built"
    )


async def test_apply_deploy_outcome_success_and_failure(db: Database) -> None:
    ref, built = deploy_in_flight(db)
    # An unfinished candidate attempt an earlier claim left behind.
    seed_rows(db, (STEPS, step_row(built, 1, "pending", name="candidate")))
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref, "w-1", TTL))).claim
        assert claim is not None
        await store.apply_deploy_outcome(claim.fence(), DeployOutcome(at=now()))
    assert (
        scalar(db, "SELECT status::text FROM provisioning.releases WHERE release_id = %s", built)
        == "candidate_ready"
    )
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "succeeded"
    assert (
        scalar(db, "SELECT finished_at IS NOT NULL FROM provisioning_ops.command_dispatch") is True
    )
    assert row_of(db, "SELECT status::text, failure_code FROM provisioning.release_steps") == (
        "failed",
        "deadline_exceeded",
    )

    ref2, built2 = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        claim = (await store.claim(ClaimRequest(ref2, "w-1", TTL))).claim
        assert claim is not None
        failure = Failure(code="candidate_attempts_exhausted", summary="readiness timed out")
        await store.apply_deploy_outcome(claim.fence(), DeployOutcome(at=now(), failure=failure))
    assert row_of(
        db,
        "SELECT status::text, failure_code, failure_summary FROM provisioning.releases"
        " WHERE release_id = %s",
        built2,
    ) == ("failed", "candidate_attempts_exhausted", "readiness timed out")


async def test_fail_unclaimed_command_after_its_deadline(db: Database) -> None:
    """FR-15.2: fail unclaimed command after its deadline."""
    deploy_in_flight(db, deadline_at=now() - timedelta(seconds=1))
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        due = (await store.take_due(10, VISIBILITY))[0]
        assert due.deadline_passed
        await store.fail_unclaimed_command(due.fence(), "deadline_exceeded", now())
    assert row_of(db, "SELECT status::text, failure_code FROM provisioning.commands") == (
        "failed",
        "deadline_exceeded",
    )


async def test_fail_unclaimed_before_its_deadline_is_refused(db: Database) -> None:
    deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, TIMEOUTS)
        due = (await store.take_due(10, VISIBILITY))[0]
        with pytest.raises(IllegalTransition):
            await store.fail_unclaimed_command(due.fence(), "deadline_exceeded", now())
    assert scalar(db, "SELECT status::text FROM provisioning.commands") == "accepted"
