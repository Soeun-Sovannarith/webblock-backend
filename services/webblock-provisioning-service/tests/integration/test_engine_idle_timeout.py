"""A session stalled inside a transaction is ended by PostgreSQL 18 (task H5b, lead's ruling).

The stall is what a paused process (SIGSTOP, a VM or GC pause) leaves: a transaction that took a
row lock and then sends nothing, with no client-side timeout running. Through an engine whose
``idle_in_transaction_timeout`` is 500 ms:

- right away another session cannot take the lock (Contention);
- after the timeout the server has ended the stalled session, the other session takes the lock,
  and no session of the database is left idle in a transaction;
- the stalled connection, when its process runs again, finds its session gone: nothing it sends is
  committed.

A session idle outside a transaction (autocommit, as the build hint listener's LISTEN connection)
is not ended by it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration._pg import ROLE_WORKER, Database
from tests.integration.test_store_support import (
    HOSTING,
    hosting_row,
    role_engine,
    scalar,
    seed_rows,
)

from provisioning.core.application import Contention
from provisioning.infrastructure.postgres import EngineSettings, Timeouts, Transactions
from provisioning.infrastructure.postgres.schema import project_hosting

pytestmark = pytest.mark.asyncio

STALL = EngineSettings(
    pool_size=1,
    max_overflow=0,
    pool_timeout=timedelta(seconds=10),
    connect_timeout=timedelta(seconds=10),
    idle_in_transaction_timeout=timedelta(milliseconds=500),
)
QUICK = Timeouts(
    transaction=timedelta(seconds=5),
    statement=timedelta(seconds=2),
    lock=timedelta(milliseconds=200),
)


async def lock(conn: AsyncConnection, project: UUID) -> object:
    stmt = (
        select(project_hosting.c.project_id)
        .where(project_hosting.c.project_id == project)
        .with_for_update()
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


def idle_in_transaction(db: Database) -> int:
    return int(
        cast(
            "Any",
            scalar(
                db,
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND state LIKE 'idle in transaction%%'",
            ),
        )
    )


async def test_a_stalled_transaction_is_ended_and_its_row_lock_freed(db: Database) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project)))
    async with (
        role_engine(db, ROLE_WORKER, STALL) as stalled_engine,
        role_engine(db, ROLE_WORKER) as other_engine,
    ):
        stalled = await stalled_engine.connect()
        try:
            await stalled.begin()
            assert await lock(stalled, project) == project
            # The "paused" process sends nothing from here on.
            other = Transactions(other_engine, QUICK)
            with pytest.raises(Contention):
                await other.run("lock while stalled", lambda conn: lock(conn, project))

            await asyncio.sleep(1.5)
            assert await other.run("lock after", lambda conn: lock(conn, project)) == project
            assert idle_in_transaction(db) == 0

            # Resumed, the stalled connection finds its session ended.
            with pytest.raises(DBAPIError):
                await lock(stalled, project)
        finally:
            await stalled.invalidate()
            await stalled.close()


async def test_a_session_idle_outside_a_transaction_is_kept(db: Database) -> None:
    async with role_engine(db, ROLE_WORKER, STALL) as engine:
        conn = await engine.connect()
        try:
            raw = await conn.get_raw_connection()
            driver = cast("psycopg.AsyncConnection[Any]", raw.driver_connection)
            await driver.rollback()
            await driver.set_autocommit(True)
            await driver.execute("LISTEN h5b_probe")
            await asyncio.sleep(1.5)
            cur = await driver.execute("SELECT 1")
            assert await cur.fetchone() == (1,)
        finally:
            await conn.invalidate()
            await conn.close()
