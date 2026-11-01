"""The bootstrap's singleton lock on PostgreSQL 18 (task H5c): a session-level advisory lock held on
a connection of its own (``infrastructure.postgres.session_lock``).

Both lockers are logins that are members of ``provisioning_bootstrap`` alone: an advisory lock
needs no grant. Each test has its own database, and advisory locks are per database, so the real
key is used.

Task HF-A (H-V finding F-1) adds the stall: a check that passes its time bound leaves the lock
uncertain, not lost, and a check on the same session confirms it afterwards (see the section
below for how the stall is made).
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_BOOTSTRAP, Database
from tests.integration.test_store_support import ENGINE, TIMEOUTS, role_engine

from provisioning.infrastructure.postgres import Timeouts, database_url, open_engine
from provisioning.infrastructure.postgres.session_lock import (
    BOOTSTRAP_LOCK_KEY,
    LockLost,
    PgSessionLock,
)

pytestmark = pytest.mark.asyncio

POLL = timedelta(milliseconds=100)
HOLDERS = (
    "SELECT pid FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1"
    " AND classid::bigint * 4294967296 + objid::bigint = %s"
)


class Locker:
    """One ``hold`` running as a task, with what its callbacks reported."""

    def __init__(
        self,
        engine: AsyncEngine,
        check: timedelta,
        timeouts: Timeouts = TIMEOUTS,
        stall: timedelta = timedelta(seconds=60),
    ) -> None:
        self.lock = PgSessionLock(
            engine, timeouts, key=BOOTSTRAP_LOCK_KEY, poll=POLL, check=check, stall=stall
        )
        self.standbys = 0
        self.held = asyncio.Event()
        self.helds = 0
        self.uncertain = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(
            self.lock.hold(
                on_standby=self._standby, on_held=self._held, on_uncertain=self.uncertain.set
            )
        )

    def _standby(self) -> None:
        self.standbys += 1

    def _held(self) -> None:
        self.helds += 1
        self.held.set()

    async def stop(self) -> None:
        assert self.task is not None
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task


@asynccontextmanager
async def lockers(
    db: Database, check: timedelta = timedelta(seconds=1)
) -> AsyncIterator[tuple[Locker, Locker]]:
    async with role_engine(db, ROLE_BOOTSTRAP) as one, role_engine(db, ROLE_BOOTSTRAP) as two:
        first, second = Locker(one, check), Locker(two, check)
        try:
            yield first, second
        finally:
            for locker in (first, second):
                if locker.task is not None and not locker.task.done():
                    locker.task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await locker.task


def holders(db: Database) -> list[object]:
    with db.admin() as conn:
        return [row[0] for row in conn.execute(HOLDERS, (BOOTSTRAP_LOCK_KEY,)).fetchall()]


async def eventually(what: str, done: Callable[[], bool], within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not done():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.02)


async def timed(wait: Awaitable[object], within: float) -> float:
    started = time.monotonic()
    async with asyncio.timeout(within):
        await wait
    return time.monotonic() - started


async def test_the_second_locker_stands_by_and_takes_over_when_the_first_session_ends(
    db: Database,
) -> None:
    async with lockers(db) as (first, second):
        first.start()
        await timed(first.held.wait(), 10)
        assert first.standbys == 0
        second.start()
        await eventually("three standby polls", lambda: second.standbys >= 3)
        assert not second.held.is_set()
        assert len(holders(db)) == 1

        await first.stop()  # its connection is closed: the session ends, the server frees the lock
        took = await timed(second.held.wait(), 5)
        assert took < 1.0, f"the standby took over after {took:.2f}s (poll 0.1s)"
        assert len(holders(db)) == 1
        standbys = second.standbys
        await asyncio.sleep(0.3)
        assert second.standbys == standbys  # once held, it stands by no more


async def test_a_terminated_lock_session_is_lost_at_once_and_the_standby_takes_over(
    db: Database,
) -> None:
    # A check interval far longer than the test: the loss is noticed by the session's read, not
    # by the periodic check.
    async with lockers(db, check=timedelta(seconds=60)) as (first, second):
        first.start()
        await timed(first.held.wait(), 10)
        second.start()
        await eventually("a standby poll", lambda: second.standbys >= 1)
        (pid,) = holders(db)
        with db.admin() as conn:
            conn.execute("SELECT pg_terminate_backend(%s)", (pid,))
        assert first.task is not None
        with pytest.raises(LockLost):
            await timed(first.task, 5)
        await timed(second.held.wait(), 5)
        (new_pid,) = holders(db)
        assert new_pid != pid


# --- Task HF-A (H-V finding F-1): a stalled check is uncertain, not lost -----------------------
#
# The stall: the holder reaches the server through a TCP relay in this process, and the test makes
# the relay hold every byte (both ways, on its open connections and on new ones), then pass them
# on. That is what `docker pause` of the database does to a client (the e2e test
# `test_qa_database_paused`): the statement is sent and nothing comes back, and the server, which
# has not read it, applies no timeout of its own. A blocking lock on what the check reads, or
# `pg_sleep` through a test seam, would instead stall inside the server, where the session's own
# timeouts end the statement with an error, a different fault. The standby talks to the server
# directly, so the server answers it throughout: while the holder's session exists, its try is
# refused.

STALL_TIMEOUTS = Timeouts(
    transaction=timedelta(seconds=10), statement=timedelta(seconds=1), lock=timedelta(seconds=1)
)


class StallingRelay:
    """A TCP relay to the test server whose bytes can be held (``stall``) and let go (``resume``).
    Held bytes are passed on, in order, when it resumes; a close is passed on after them."""

    def __init__(self, host: str, port: int) -> None:
        self._target = (host, port)
        self._flowing = asyncio.Event()
        self._flowing.set()
        self._server: asyncio.Server | None = None
        self._pipes: set[asyncio.Task[None]] = set()

    async def start(self) -> int:
        server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self._server = server
        return int(server.sockets[0].getsockname()[1])

    def stall(self) -> None:
        self._flowing.clear()

    def resume(self) -> None:
        self._flowing.set()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
        for pipe in list(self._pipes):
            pipe.cancel()
        await asyncio.gather(*self._pipes, return_exceptions=True)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await self._flowing.wait()
        up_reader, up_writer = await asyncio.open_connection(*self._target)
        for source, target in ((reader, up_writer), (up_reader, writer)):
            pipe = asyncio.create_task(self._pipe(source, target))
            self._pipes.add(pipe)
            pipe.add_done_callback(self._pipes.discard)

    async def _pipe(self, source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
        try:
            while data := await source.read(65536):
                await self._flowing.wait()
                target.write(data)
                await target.drain()
            await self._flowing.wait()  # a close is held like the bytes before it
        except ConnectionError:
            pass
        finally:
            target.close()


@asynccontextmanager
async def relayed_lockers(
    db: Database, *, stall: timedelta
) -> AsyncIterator[tuple[StallingRelay, Locker, Locker]]:
    """The first locker through a ``StallingRelay`` (statement bound 1 s, check every 0.2 s, the
    given stall bound), the second straight to the server; both fresh logins of
    ``provisioning_bootstrap``."""
    server = db.harness.server
    relay = StallingRelay(server.host, server.port)
    port = await relay.start()
    role = f"prov_login_{secrets.token_hex(6)}"
    password = secrets.token_hex(16)
    with db.admin() as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                sql.Identifier(role), sql.Literal(password), sql.Identifier(ROLE_BOOTSTRAP)
            )
        )
    relayed = open_engine(
        database_url(host="127.0.0.1", port=port, database=db.name, user=role, password=password),
        ENGINE,
    )
    try:
        async with role_engine(db, ROLE_BOOTSTRAP) as direct:
            first = Locker(relayed, timedelta(milliseconds=200), STALL_TIMEOUTS, stall)
            second = Locker(direct, timedelta(seconds=1))
            try:
                yield relay, first, second
            finally:
                relay.resume()
                for locker in (first, second):
                    if locker.task is not None and not locker.task.done():
                        locker.task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, LockLost):
                            await locker.task
    finally:
        await relayed.dispose()
        await relay.close()
        with db.admin() as admin:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


async def test_a_stalled_check_leaves_the_lock_with_its_holder_and_the_standby_never_takes_it(
    db: Database,
) -> None:
    async with relayed_lockers(db, stall=timedelta(seconds=30)) as (relay, first, second):
        first.start()
        await timed(first.held.wait(), 10)
        (pid,) = holders(db)
        second.start()
        await eventually("a standby poll", lambda: second.standbys >= 1)

        relay.stall()  # 4 s and more: four of the holder's statement bounds
        await timed(first.uncertain.wait(), 5)
        polls = second.standbys
        stalled_until = time.monotonic() + 4
        while time.monotonic() < stalled_until:
            assert first.task is not None
            assert not first.task.done()  # uncertain, not lost
            assert holders(db) == [pid]  # the server keeps the holder's session and its lock
            assert not second.held.is_set()
            await asyncio.sleep(0.1)
        assert second.standbys > polls + 10  # the standby kept trying, and was refused
        assert first.helds == 1  # never confirmed while stalled

        relay.resume()
        await eventually("the holder confirmed on the same session", lambda: first.helds == 2, 5)
        assert holders(db) == [pid]  # the same session: nobody else held the lock in between
        await asyncio.sleep(1.5)  # more checks, each answered in time
        assert first.helds == 2
        assert first.task is not None
        assert not first.task.done()
        assert not second.held.is_set()
        assert holders(db) == [pid]


async def test_a_stall_past_the_bound_is_lost_and_the_standby_takes_over_once_it_ends(
    db: Database,
) -> None:
    async with relayed_lockers(db, stall=timedelta(seconds=2)) as (relay, first, second):
        first.start()
        await timed(first.held.wait(), 10)
        (pid,) = holders(db)
        second.start()
        await eventually("a standby poll", lambda: second.standbys >= 1)

        relay.stall()
        assert first.task is not None
        started = time.monotonic()
        with pytest.raises(LockLost, match="stalled past its bound"):
            await timed(first.task, 10)
        took = time.monotonic() - started
        assert 2.0 <= took < 6.0, took  # the check interval and the statement bound, then 2 s
        assert first.helds == 1
        assert holders(db) == [pid]  # its close is held by the relay too
        assert not second.held.is_set()

        relay.resume()  # the server now reads the holder's close: the session ends
        await timed(second.held.wait(), 5)
        (new_pid,) = holders(db)
        assert new_pid != pid
