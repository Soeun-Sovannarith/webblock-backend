"""The stores' transaction helper and error mapping on PostgreSQL 18 (prototype task B3).

Timeouts, an unreachable server, concurrency, and the cancellation case of review-port-arch.md
H5: cancelling a task
during ``pg_sleep`` inside a transaction returns the connection to the pool clean and releases the
row lock.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration._pg import ROLE_API, ROLE_MIGRATOR, Database
from tests.integration.test_store_support import (
    HOSTING,
    TIMEOUTS,
    hosting_row,
    role_engine,
    scalar,
    seed_rows,
)

from provisioning.core.application import Contention, Timeout
from provisioning.core.domain import is_error
from provisioning.infrastructure.postgres import (
    DatabaseError,
    EngineSettings,
    Timeouts,
    Transactions,
    database_url,
    open_engine,
)
from provisioning.infrastructure.postgres.schema import commands, project_hosting

pytestmark = pytest.mark.asyncio

ONE = EngineSettings(
    pool_size=1,
    max_overflow=0,
    pool_timeout=timedelta(seconds=10),
    connect_timeout=timedelta(seconds=10),
    idle_in_transaction_timeout=TIMEOUTS.transaction,
)


async def lock(conn: AsyncConnection, project: UUID) -> object:
    stmt = (
        select(project_hosting.c.project_id)
        .where(project_hosting.c.project_id == project)
        .with_for_update()
    )
    return (await conn.execute(stmt)).scalar_one_or_none()


async def sleep(conn: AsyncConnection, seconds: float) -> None:
    await conn.execute(select(func.pg_sleep(seconds)))


async def test_cancel_during_pg_sleep_returns_a_clean_connection_and_frees_the_lock(
    db: Database,
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project)))
    async with role_engine(db, ROLE_API, ONE) as engine:
        tx = Transactions(engine, TIMEOUTS)
        started = asyncio.Event()

        async def hold(conn: AsyncConnection) -> None:
            await lock(conn, project)
            started.set()
            await sleep(conn, 30)

        task = asyncio.create_task(tx.run("hold the lock", hold))
        await started.wait()
        await asyncio.sleep(0.3)  # let pg_sleep start on the server
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # No session of this database is left idle in a transaction, and the row lock is free:
        # a second transaction through the engine's single connection takes it at once.
        assert (
            scalar(
                db,
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND state LIKE 'idle in transaction%%'",
            )
            == 0
        )
        locked = await tx.run("take the lock again", lambda conn: lock(conn, project))
    assert locked == project


async def test_statement_timeout_is_timeout(db: Database) -> None:
    short = Timeouts(
        transaction=timedelta(seconds=5),
        statement=timedelta(milliseconds=200),
        lock=timedelta(milliseconds=100),
    )
    async with role_engine(db, ROLE_API) as engine:
        with pytest.raises(Timeout) as info:
            await Transactions(engine, short).run("sleep", lambda conn: sleep(conn, 2))
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


async def test_transaction_timeout_is_timeout(db: Database) -> None:
    short = Timeouts(
        transaction=timedelta(milliseconds=300),
        statement=timedelta(milliseconds=300),
        lock=timedelta(milliseconds=100),
    )

    async def two_sleeps(conn: AsyncConnection) -> None:
        await sleep(conn, 0.2)
        await sleep(conn, 0.2)

    async with role_engine(db, ROLE_API) as engine:
        with pytest.raises(Timeout):
            await Transactions(engine, short).run("two sleeps", two_sleeps)


async def test_lock_timeout_is_contention(db: Database) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project)))
    short = Timeouts(
        transaction=timedelta(seconds=5),
        statement=timedelta(seconds=2),
        lock=timedelta(milliseconds=100),
    )
    async with role_engine(db, ROLE_API) as engine:
        with db.as_role(ROLE_MIGRATOR) as holder, holder.transaction():
            holder.execute(
                "SELECT 1 FROM provisioning.project_hosting WHERE project_id = %s FOR UPDATE",
                (project,),
            )
            with pytest.raises(Contention):
                await Transactions(engine, short).run("lock", lambda conn: lock(conn, project))


async def test_a_driver_error_carries_no_sql_value_or_cause(db: Database) -> None:
    customer_key = "customer-secret-key-value"
    stmt = insert(commands).values(
        command_id=uuid4(),
        project_id=uuid4(),
        kind="deploy",
        idempotency_key=customer_key,
        request_fingerprint="x" * 64,
        command_number=1,
        status="accepted",
        created_at=func.now(),
        updated_at=func.now(),
    )

    async def bad(conn: AsyncConnection) -> None:
        await conn.execute(stmt)

    async with role_engine(db, ROLE_API) as engine:
        with pytest.raises(DatabaseError) as info:
            await Transactions(engine, TIMEOUTS).run("bad insert", bad)
    text = str(info.value)
    assert customer_key not in text
    assert "INSERT" not in text
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__
    assert info.value.sqlstate is not None
    assert not is_error(info.value, Contention)


async def test_an_unreachable_server_is_a_database_error_without_the_password() -> None:
    url = database_url(
        host="127.0.0.1",
        port=1,
        database="nowhere",
        user="someone",
        password="hunter2-secret",  # noqa: S106 - a fake password for an unreachable server
    )
    engine = open_engine(url, ONE)
    try:
        with pytest.raises((DatabaseError, Timeout)) as info:
            await Transactions(engine, TIMEOUTS).run("unreachable", lambda conn: sleep(conn, 0))
    finally:
        await engine.dispose()
    assert "hunter2" not in str(info.value)
    assert "someone" not in str(info.value)
    assert info.value.__cause__ is None
