"""Claims and renewals racing on one command, with real concurrency on PostgreSQL 18 (task H5c).

H5b found that removing one of the claim's serializing layers alone failed no test: the existing
store tests claim one after the other. Here two sessions race on one command. A third session (the
superuser's, test-only) holds the command row ``FOR UPDATE``, so the first racer stops at exactly
the point where the layers matter: after it read the dispatch row, before it locks the command. The
second racer then starts, the test waits until both sessions wait on a lock (``pg_stat_activity``),
and lets the first go.

- The claim's dispatch-row ``FOR UPDATE`` (``lock_dispatch``): with it, the second claim waits on
  the dispatch row and then reads the first one's claim, so it is ``HELD``. Without it, the second
  claim read the dispatch row before the first claimed, and its update finds nothing to change (or,
  with the claim-number condition gone too, claims a second time).
- ``load_fenced``'s claim check under a takeover: a stale renewal that waits on the dispatch row
  while another worker takes the command over is refused with ``ClaimLost`` once it gets the row.

Each test runs the store code the worker runs, on a login that is a member of
``provisioning_worker`` alone.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.rows import TupleRow
from tests.integration._pg import ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    seed_rows,
)

from provisioning.core.application import (
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    ClaimResult,
    Fence,
    WorkRef,
)
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.postgres import PgJobStore, Timeouts
from provisioning.infrastructure.postgres.deploy_store import PgDeployStore

pytestmark = pytest.mark.asyncio

TTL = timedelta(seconds=60)
# Long lock and statement bounds: a racer waits on a row lock for as long as the test holds it.
RACE_TIMEOUTS = Timeouts(
    transaction=timedelta(seconds=30), statement=timedelta(seconds=20), lock=timedelta(seconds=20)
)
CLAIM_ROW = (
    "SELECT claim_number, execution_attempts, claimed_by_worker "
    "FROM provisioning_ops.command_dispatch WHERE command_id = %s"
)


def deploy_in_flight(db: Database, status: str = "accepted", **dispatch: object) -> WorkRef:
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
    return WorkRef(
        command_id=command,
        project_id=project,
        kind=CommandKind.DEPLOY,
        command_number=1,
        release_id=built,
    )


@asynccontextmanager
async def superuser(db: Database) -> AsyncIterator[psycopg.AsyncConnection[TupleRow]]:
    """A test-only superuser session on the test database (a transaction until committed)."""
    conn = await psycopg.AsyncConnection.connect(db.harness.server.conninfo(db.name))
    try:
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def command_locked(
    db: Database, command_id: UUID
) -> AsyncIterator[Callable[[], Awaitable[None]]]:
    """Holds the command row ``FOR UPDATE`` in its own transaction; yields the call that commits
    it (the lock ends when the session closes in any case)."""
    async with superuser(db) as conn:
        cur = await conn.execute(
            "SELECT 1 FROM provisioning.commands WHERE command_id = %s FOR UPDATE", (command_id,)
        )
        assert await cur.fetchall() == [(1,)]
        yield conn.commit


async def wait_for_lock_waiters(db: Database, count: int, within: float = 10.0) -> None:
    """Until ``count`` sessions of the test database wait on a lock (row locks included)."""
    deadline = time.monotonic() + within
    async with superuser(db) as conn:
        await conn.set_autocommit(True)
        while True:
            cur = await conn.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            found = await cur.fetchone()
            assert found is not None
            if int(found[0]) >= count:
                return
            assert time.monotonic() < deadline, f"{count} lock waiters expected, {found[0]} seen"
            await asyncio.sleep(0.01)


async def settled[T](task: asyncio.Task[T]) -> T | BaseException:
    """The task's result, or the exception it raised (so one racer's error does not hide the
    other's outcome)."""
    try:
        return await task
    except Exception as exc:  # the outcome under test
        return exc


def now() -> datetime:
    return datetime.now(UTC)


async def test_two_claims_racing_for_one_command_give_one_claim_and_one_held(db: Database) -> None:
    ref = deploy_in_flight(db)
    async with role_engine(db, ROLE_WORKER) as engine:
        store = PgJobStore(engine, RACE_TIMEOUTS)
        async with command_locked(db, ref.command_id) as release_command:
            first = asyncio.create_task(store.claim(ClaimRequest(ref, "w-a", TTL)))
            try:
                await wait_for_lock_waiters(db, 1)  # the first holds the dispatch row
                second = asyncio.create_task(store.claim(ClaimRequest(ref, "w-b", TTL)))
                try:
                    await wait_for_lock_waiters(db, 2)  # the second waits too
                    await release_command()
                    won, lost = await settled(first), await settled(second)
                finally:
                    second.cancel()
            finally:
                first.cancel()

    assert isinstance(won, ClaimResult), won
    assert won.outcome is ClaimOutcome.CLAIMED, won
    assert won.claim is not None
    assert (won.claim.claimed_by_worker, won.claim.claim_number) == ("w-a", 1)
    # The second claim read the first one's claim under the dispatch row lock: held, not an error.
    assert isinstance(lost, ClaimResult), f"the second claim raised {lost!r}"
    assert lost.outcome is ClaimOutcome.HELD, lost
    assert row_of(db, CLAIM_ROW, ref.command_id) == (1, 1, "w-a")


async def test_a_stale_renewal_racing_a_takeover_is_refused_as_a_lost_claim(
    db: Database,
) -> None:
    """FR-25.1: a stale renewal racing a takeover is refused as a lost claim."""
    ref = deploy_in_flight(
        db,
        status="running",
        claim_number=1,
        execution_attempts=1,
        claimed_by_worker="w-a",
        claim_expires_at=now() - timedelta(seconds=1),
    )
    stale = Fence(
        project_id=ref.project_id, command_id=ref.command_id, command_number=1, claim_number=1
    )
    async with role_engine(db, ROLE_WORKER) as engine:
        jobs = PgJobStore(engine, RACE_TIMEOUTS)
        deploys = PgDeployStore(engine, RACE_TIMEOUTS)
        async with command_locked(db, ref.command_id) as release_command:
            takeover = asyncio.create_task(jobs.claim(ClaimRequest(ref, "w-b", TTL)))
            try:
                await wait_for_lock_waiters(db, 1)  # the takeover holds the dispatch row
                renewal = asyncio.create_task(deploys.renew_claim(stale, TTL))
                try:
                    await wait_for_lock_waiters(db, 2)  # the stale renewal waits for it
                    await release_command()
                    taken, renewed = await settled(takeover), await settled(renewal)
                finally:
                    renewal.cancel()
            finally:
                takeover.cancel()

    assert isinstance(taken, ClaimResult), taken
    assert taken.outcome is ClaimOutcome.CLAIMED, taken
    assert taken.claim is not None
    assert taken.claim.claim_number == 2
    # The stale worker's renewal saw the new claim under the lock and wrote nothing.
    assert isinstance(renewed, ClaimLost), f"the stale renewal ended with {renewed!r}"
    assert row_of(db, CLAIM_ROW, ref.command_id) == (2, 2, "w-b")
    expires = row_of(
        db,
        "SELECT claim_expires_at FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        ref.command_id,
    )
    assert expires == (taken.claim.expires_at,)
