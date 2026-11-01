"""A singleton lock: a PostgreSQL session-level advisory lock held on a connection of its own for
the holder's lifetime (task H5c; decision 0016 section 6 item 5).

The bootstrap process must run as exactly one replica. Each bootstrap takes the lock on the fixed
key :data:`BOOTSTRAP_LOCK_KEY` before its first round; a second one stands by until the first one's
session ends. A session-level advisory lock is released by the server when its session ends (the
process exited, was killed, or lost its connection), so a crashed holder never keeps it.

- No grant is needed: any login may take an advisory lock.
- The lock lives in one session, so it needs a connection that is that session for its whole life:
  a direct connection, or a session-mode pool. Through PgBouncer in transaction mode it does not
  work (the same caveat as the build hint's LISTEN).
- The connection comes from an engine of its own (the caller opens one), so the rounds' pool never
  loses a connection to it, and it is invalidated at the end, never returned to a pool while it
  could still hold the lock.

A stall is not a lost lock (task HF-A; H-V finding F-1). PostgreSQL keeps a session lock for as
long as the session exists, so a check that does not answer within its time bound (the database
paused or failing over, the network slow) leaves the lock **uncertain**, not lost: the holder is
told (``on_uncertain``) and must run no round, and the same check is awaited on the same session,
never cancelled. Once it answers that the session holds the lock, one more check on the same
session must answer the same, and the holder is told again (``on_held``). Nobody else can have
held the lock in between: the session existed all along.

The check is not cancelled because psycopg answers a cancelled statement by asking the server to
cancel it and, when the query has not ended 5 s later, by closing the connection
(``AsyncConnection.wait``), which ends the session and frees the lock. A new connection would hold
no lock, so none is opened.

The uncertainty is bounded: when no check on the session has answered within ``stall`` of the
first one passing its bound, the lock counts as lost. A connection that never errors and never
answers (a silent network drop) would otherwise keep the holder paused for ever, while the server
may already have ended the session and a standby taken the lock.

Importing this module opens no connection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, LiteralString, NoReturn, Protocol, cast

import psycopg
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection as SaConnection
from sqlalchemy.ext.asyncio import AsyncEngine

from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.tx import Timeouts

BOOTSTRAP_LOCK_KEY: Final = 6_287_675_231_898_391_634
"""The bootstrap's singleton lock: the ASCII bytes of ``WBOOTSTR`` read as one big-endian 64-bit
integer (``0x57424F4F54535452``). In ``pg_locks`` it shows as an ``advisory`` lock with
``classid = 1463963471``, ``objid = 1414747218`` and ``objsubid = 1``."""

_MAX_KEY: Final = 2**63 - 1

# How long a check still in flight is given to end once its connection is closed and it is
# cancelled (it fails at once; the bound only keeps a shutdown from waiting on it).
_DISCARD_WAIT: Final = 1.0

_TRY: Final = "SELECT pg_try_advisory_lock(%s)"
# Whether this session holds the lock: a bigint key shows in pg_locks as classid (high 32 bits),
# objid (low 32 bits) and objsubid 1. pg_locks is readable by every login.
_HELD: Final = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted"
    " AND pid = pg_backend_pid() AND objsubid = 1"
    " AND classid::bigint * 4294967296 + objid::bigint = %s)"
)


class LockLost(Exception):
    """The singleton lock's session failed or no longer holds the lock (or could not be opened),
    or its checks stalled past their bound. The message is fixed text: it never carries driver
    text, a host or a credential."""


@dataclass(frozen=True, slots=True)
class LockTiming:
    """The lock's intervals in seconds (``PgSessionLock`` and ``keep_lock``)."""

    poll: float
    check: float
    statement: float
    stall: float


class LockSession(Protocol):
    """The one session that takes and holds the lock (``_PgSession`` on PostgreSQL)."""

    async def take(self) -> bool:
        """Try the lock once, within the statement bound; ``LockLost`` when the try fails."""
        ...

    def check(self) -> asyncio.Future[bool]:
        """Start one check of whether this session holds the lock and return it, running. No time
        bound cancels it; it fails with ``LockLost`` when the session does."""
        ...

    async def watch(self, seconds: float) -> None:
        """Wait ``seconds`` reading the session; ``LockLost`` at once when it ends."""
        ...


async def keep_lock(
    session: LockSession,
    timing: LockTiming,
    *,
    on_standby: Callable[[], object],
    on_held: Callable[[], object],
    on_uncertain: Callable[[], object],
) -> NoReturn:
    """Take the lock on ``session``, then keep it until cancelled; ``LockLost`` when it is lost.

    1. ``take`` until it grants the lock, calling ``on_standby`` and waiting ``poll`` after each
       refusal. Then ``on_held``.
    2. Every ``check`` seconds of ``watch``, one check. A check answered within ``statement`` that
       the session holds the lock changes nothing; one answered otherwise, or failed, is a lost
       lock.
    3. A check not answered within ``statement``: ``on_uncertain``, and the same check is awaited.
       Once it answers held, one more check is made (the first answer may predate the stall);
       when that one answers held too, ``on_held`` and back to 2. Both answers must come within
       ``stall`` of ``on_uncertain``, or the lock is lost. Nothing else is sent meanwhile: one
       session runs one statement at a time."""
    while not await session.take():
        on_standby()
        await asyncio.sleep(timing.poll)
    on_held()
    while True:
        await session.watch(timing.check)
        check = session.check()
        if await _answered(check, timing.statement):
            _require_held(check)
            continue
        on_uncertain()
        deadline = asyncio.get_running_loop().time() + timing.stall
        for confirming in (False, True):
            if not await _answered(check, deadline - asyncio.get_running_loop().time()):
                raise LockLost("singleton lock: the check stalled past its bound")
            _require_held(check)
            if not confirming:
                check = session.check()
        on_held()


async def _answered(check: asyncio.Future[bool], seconds: float) -> bool:
    """Whether ``check`` answered within ``seconds``; it is never cancelled here."""
    if not check.done() and seconds > 0:
        await asyncio.wait((check,), timeout=seconds)
    return check.done()


def _require_held(check: asyncio.Future[bool]) -> None:
    """``LockLost`` unless the answered ``check`` says the session holds the lock."""
    if check.cancelled():
        raise LockLost("singleton lock: the check failed")
    error = check.exception()
    if isinstance(error, LockLost):
        raise LockLost(str(error)) from None
    if error is not None:
        raise LockLost("singleton lock: the check failed") from None
    if not check.result():
        raise LockLost("singleton lock: the session no longer holds the lock")


class PgSessionLock:
    """Takes and keeps one session-level advisory lock (see the module docstring)."""

    __slots__ = ("_connect", "_engine", "_key", "_timing")

    def __init__(
        self,
        engine: AsyncEngine,
        timeouts: Timeouts,
        *,
        key: int,
        poll: timedelta,
        check: timedelta,
        stall: timedelta,
    ) -> None:
        timeouts.validate()
        if not 1 <= key <= _MAX_KEY:
            raise InvalidArgument("session lock: the key must be a positive 64-bit integer")
        if poll <= timedelta(0) or check <= timedelta(0):
            raise InvalidArgument("session lock: the poll and check intervals must be positive")
        if stall < timeouts.statement:
            raise InvalidArgument("session lock: the stall bound must be at least the statement's")
        self._engine = engine
        self._key = key
        self._timing = LockTiming(
            poll=poll.total_seconds(),
            check=check.total_seconds(),
            statement=timeouts.statement.total_seconds(),
            stall=stall.total_seconds(),
        )
        self._connect = timeouts.transaction.total_seconds()

    async def hold(
        self,
        *,
        on_standby: Callable[[], object],
        on_held: Callable[[], object],
        on_uncertain: Callable[[], object],
    ) -> NoReturn:
        """``keep_lock`` on a connection of its own, outside any transaction (each try and check
        is bounded by the statement timeout; see ``keep_lock``).

        Cancelling it, or a lost lock, invalidates and closes the connection, so the session ends
        and the server frees the lock; a check still in flight is cancelled after that."""
        try:
            async with asyncio.timeout(self._connect):
                conn = await self._engine.connect()
        except SQLAlchemyError, OSError, TimeoutError:
            raise LockLost("singleton lock: could not open its connection") from None
        session: _PgSession | None = None
        try:
            session = _PgSession(await self._driver(conn), self._key, self._timing.statement)
            await keep_lock(
                session,
                self._timing,
                on_standby=on_standby,
                on_held=on_held,
                on_uncertain=on_uncertain,
            )
        finally:
            # The connection is closed before a check in flight is cancelled: cancelled first, the
            # check would make psycopg wait on the stalled session (its own cancel request, then
            # up to 5 s more) before the session could end.
            await asyncio.shield(conn.invalidate())
            await asyncio.shield(conn.close())
            if session is not None:
                await asyncio.shield(session.discard())

    async def _driver(self, conn: SaConnection) -> psycopg.AsyncConnection[Any]:
        try:
            raw = await conn.get_raw_connection()
            driver = cast("psycopg.AsyncConnection[Any]", raw.driver_connection)
            async with asyncio.timeout(self._timing.statement):
                await driver.rollback()
                await driver.set_autocommit(True)
        except SQLAlchemyError, psycopg.Error, TimeoutError:
            raise LockLost("singleton lock: could not prepare its session") from None
        return driver


class _PgSession:
    """``LockSession`` on one psycopg connection in autocommit."""

    __slots__ = ("_driver", "_in_flight", "_key", "_statement")

    def __init__(self, driver: psycopg.AsyncConnection[Any], key: int, statement: float) -> None:
        self._driver = driver
        self._key = key
        self._statement = statement
        self._in_flight: asyncio.Task[bool] | None = None

    async def take(self) -> bool:
        """Bounded by cancellation, as before task HF-A: a try that stalls ends the process (it
        runs no round yet), and its supervisor starts it again."""
        try:
            async with asyncio.timeout(self._statement):
                return await self._ask(_TRY, "try")
        except TimeoutError:
            raise LockLost("singleton lock: the try failed") from None

    def check(self) -> asyncio.Future[bool]:
        self._in_flight = asyncio.ensure_future(self._ask(_HELD, "check"))
        return self._in_flight

    async def watch(self, seconds: float) -> None:
        """Wait ``seconds`` reading the connection: a session the server ends (terminated,
        server shut down) fails the read at once. Any notification is ignored."""
        try:
            async for _ in self._driver.notifies(timeout=seconds):
                pass
        except psycopg.Error:
            raise LockLost("singleton lock: the session ended") from None

    async def discard(self) -> None:
        """Once the connection is closed: cancel a check still in flight and collect it."""
        task = self._in_flight
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.wait((task,), timeout=_DISCARD_WAIT)
        if task.done() and not task.cancelled():
            task.exception()  # retrieved: a closed connection's failure is expected here

    async def _ask(self, query: LiteralString, what: str) -> bool:
        try:
            cur = await self._driver.execute(query, (self._key,))
            row = await cur.fetchone()
        except psycopg.Error:
            raise LockLost(f"singleton lock: the {what} failed") from None
        if row is None:
            raise LockLost(f"singleton lock: the {what} returned no row")
        return bool(row[0])
