"""The stores' transaction helper (decision 0004 section 10; review-port-arch.md H5).

Every store runs each operation through :meth:`Transactions.run`:

1. A connection from the role's engine, then BEGIN (READ COMMITTED, or REPEATABLE READ READ ONLY
   for reads).
2. As the first statement, ``lock_timeout`` and ``statement_timeout`` set locally with bound
   values (``set_config(..., true)``), so a pooled connection never keeps them.
3. The operation, bounded by the transaction timeout (``asyncio.timeout``).
4. COMMIT outside any cancellable scope: it runs as its own task under ``asyncio.shield`` with the
   statement timeout as its bound. A cancelled caller waits for the commit to settle, then sees its
   cancellation. A commit whose outcome is unknown (its bound passed) is ``Timeout``; acceptance
   resolves it through idempotent replay.
5. On any error or cancellation, ROLLBACK, shielded and bounded, and the connection goes back to
   the pool; a connection SQLAlchemy invalidated is replaced.

Errors: a ``DBAPIError`` becomes the typed error of :func:`map_db_error`, a passed deadline
``Timeout``, a pool that has no connection in time ``Timeout``, every other SQLAlchemy error
``DatabaseError``; each is raised ``from None``. Domain and application errors raised by the
operation pass through unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import bindparam, func, select
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from provisioning.core.application import Timeout
from provisioning.infrastructure.postgres.errors import DatabaseError, map_db_error

# PostgreSQL's lock_timeout and statement_timeout are integers of milliseconds.
_MAX_SETTING = timedelta(milliseconds=2**31 - 1)
_MS = timedelta(milliseconds=1)


class InvalidTimeouts(ValueError):
    """Timeouts out of range or out of order; the message names the timeout, never a value."""


@dataclass(frozen=True, slots=True)
class Timeouts:
    """The bounds of every transaction (decision 0004 section 10; required configuration).

    Each is a whole number of milliseconds and lock <= statement <= transaction: ``transaction``
    bounds BEGIN to COMMIT (passing it is ``Timeout``), ``statement`` is the statement_timeout
    (``Timeout``), ``lock`` the lock_timeout (``Contention``)."""

    transaction: timedelta
    statement: timedelta
    lock: timedelta

    def validate(self) -> None:
        for name, value in (
            ("transaction", self.transaction),
            ("statement", self.statement),
            ("lock", self.lock),
        ):
            if value < _MS:
                raise InvalidTimeouts(f"{name} timeout must be at least 1ms")
            if value % _MS != timedelta(0):
                raise InvalidTimeouts(f"{name} timeout must be a whole number of milliseconds")
        if self.statement > _MAX_SETTING:
            raise InvalidTimeouts("statement timeout is above PostgreSQL's maximum")
        if self.lock > self.statement:
            raise InvalidTimeouts("lock timeout is longer than the statement timeout")
        if self.statement > self.transaction:
            raise InvalidTimeouts("statement timeout is longer than the transaction timeout")


def _setting(value: timedelta) -> str:
    return f"{value // _MS}ms"


class Transactions:
    """Runs store operations in bounded transactions on one role's engine."""

    __slots__ = ("_engine", "_lock", "_statement", "_timeouts")

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        timeouts.validate()
        self._engine = engine
        self._timeouts = timeouts
        self._lock = _setting(timeouts.lock)
        self._statement = _setting(timeouts.statement)

    @property
    def timeouts(self) -> Timeouts:
        return self._timeouts

    async def run[T](
        self,
        op: str,
        fn: Callable[[AsyncConnection], Awaitable[T]],
        *,
        read_only: bool = False,
    ) -> T:
        """Run ``fn`` in one transaction and commit when it returns; see the module docstring."""
        try:
            return await self._run(op, fn, read_only)
        except DBAPIError as exc:
            raise map_db_error(op, exc) from None
        except TimeoutError, PoolTimeout:
            raise Timeout(f"postgres: {op}: deadline passed") from None
        except SQLAlchemyError:
            raise DatabaseError(op, reason="sqlalchemy error") from None

    async def _run[T](
        self, op: str, fn: Callable[[AsyncConnection], Awaitable[T]], read_only: bool
    ) -> T:
        async with asyncio.timeout(self._timeouts.transaction.total_seconds()):
            conn = await self._engine.connect()
        try:
            if read_only:
                await conn.execution_options(
                    isolation_level="REPEATABLE READ", postgresql_readonly=True
                )
            else:
                await conn.execution_options(isolation_level="READ COMMITTED")
            trans = await conn.begin()
            try:
                async with asyncio.timeout(self._timeouts.transaction.total_seconds()):
                    await conn.execute(
                        select(
                            func.set_config("lock_timeout", bindparam("lock", self._lock), True),
                            func.set_config(
                                "statement_timeout", bindparam("statement", self._statement), True
                            ),
                        )
                    )
                    result = await fn(conn)
            except BaseException:
                await self._shielded(trans.rollback())
                raise
            await self._commit(op, trans)
            return result
        finally:
            await self._shielded(conn.close())

    async def _commit(self, op: str, trans: AsyncTransaction) -> None:
        """COMMIT as a shielded task bounded by the statement timeout (H5)."""
        task = asyncio.ensure_future(self._bounded(trans.commit()))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True  # settle the commit first; the caller's cancellation follows
        exc = task.exception()
        if cancelled:
            raise asyncio.CancelledError
        if exc is None:
            return
        if isinstance(exc, TimeoutError):
            raise Timeout(f"postgres: {op}: commit outcome unknown") from None
        raise exc

    async def _bounded(self, step: Coroutine[Any, Any, None]) -> None:
        async with asyncio.timeout(self._timeouts.statement.total_seconds()):
            await step

    async def _shielded(self, step: Coroutine[Any, Any, None]) -> None:
        """Run a cleanup step to its end even when the caller is cancelled, bounded by the
        statement timeout, then deliver the caller's cancellation. The step's own failure is
        dropped: the caller already holds the error that caused the cleanup, and a connection that
        failed to roll back is invalidated and replaced by the pool."""
        task = asyncio.ensure_future(self._bounded(step))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        with contextlib.suppress(BaseException):
            task.result()
        if cancelled:
            raise asyncio.CancelledError
