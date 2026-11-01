"""The bootstrap is a singleton, enforced (task H5c; decision 0016 section 6 item 5).

Two bootstraps can interleave access_setup and leave a Secret beside a role whose password differs
(``.claude/docs/runbooks/replicas.md``), so a bootstrap runs its rounds only while it holds the
singleton lock: a PostgreSQL session-level advisory lock on ``BOOTSTRAP_LOCK_KEY``, held on a
connection of its own for the process's lifetime (``infrastructure.postgres.session_lock``).

- The lock is taken before the first round. While another bootstrap holds it, this one stands by:
  it runs no round, logs :data:`STANDBY_MESSAGE` at once and again every
  :data:`STANDBY_LOG_INTERVAL`, and reports ``provisioning_bootstrap_standby`` 1 (0 once it holds
  the lock).
- The lock is taken once per process and kept across rounds, never per round.
- A check of the lock that passes its time bound while its connection has not failed (a database
  stall: paused, failing over, slow) leaves the lock **uncertain** (task HF-A; H-V finding F-1).
  The rounds are cancelled at once, even inside a round, and none starts while it is uncertain; it
  logs :data:`UNCERTAIN_MESSAGE`. When checks on the same session confirm the lock, the rounds
  start again and it logs :data:`RESUMED_MESSAGE`. A stall is not a loss: a session lock is held
  for as long as its session exists, and the session was never left.
- When the lock is lost (its session failed or no longer holds it, or the uncertainty outlasted
  :data:`LOCK_STALL_BOUND`), the rounds are cancelled at once, even inside a round, and the error
  ends the process, which exits non-zero (``LoopEnded``) so its supervisor restarts it. Rounds
  never run without the lock.
- Stopping the process cancels the lock's session, which frees the lock for a standby.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Final, NoReturn, Protocol

STANDBY_MESSAGE: Final = "bootstrap standby: another bootstrap holds the lock"
HELD_MESSAGE: Final = "bootstrap holds the singleton lock; its rounds start"
UNCERTAIN_MESSAGE: Final = (
    "bootstrap singleton lock uncertain: a check passed its time bound; rounds paused until a"
    " check on the same session confirms the lock"
)
RESUMED_MESSAGE: Final = "bootstrap singleton lock confirmed on the same session; its rounds resume"

LOCK_POLL: Final = timedelta(seconds=1)
"""How often a standby tries the lock again: the takeover bound after the holder's session ends."""

LOCK_CHECK: Final = timedelta(seconds=5)
"""How often the holder confirms in ``pg_locks`` that its session still holds the lock (a session
the server ends is noticed at once, by the read on its connection)."""

LOCK_STALL_BOUND: Final = timedelta(seconds=60)
"""How long the lock may stay uncertain before it counts as lost and the process exits 1.

- Longer than the stalls the bootstrap should ride out: the e2e suite's 20 s database pause, and a
  CloudNativePG switchover or failover, which normally completes within tens of seconds.
- Short enough that a connection that neither answers nor fails (a silent network drop, no TCP
  reset) is given up within about a minute; the supervisor's restart then takes the lock again or
  stands by.
- Safety does not depend on it: no round runs while the lock is uncertain. It bounds only how long
  tenant rounds are paused for no answer.
- The process uses the longer of this and the statement timeout (``DB_STATEMENT_TIMEOUT``, 10 s
  on the prototype), which bounds the first check."""

STANDBY_LOG_INTERVAL: Final = timedelta(seconds=30)
"""How often a standby repeats :data:`STANDBY_MESSAGE`."""


class SingletonLock(Protocol):
    """Implemented by ``infrastructure.postgres.session_lock.PgSessionLock``. ``on_held`` is
    called when the lock is taken and again each time checks confirm it after ``on_uncertain``."""

    async def hold(
        self,
        *,
        on_standby: Callable[[], object],
        on_held: Callable[[], object],
        on_uncertain: Callable[[], object],
    ) -> NoReturn: ...


class Rounds(Protocol):
    """Implemented by ``core.application.tenants.background.bootstrap_loop.TenantLoop``."""

    async def run(self) -> None: ...


class Singleton:
    """Runs ``rounds`` only while ``lock`` is held and certain (see the module docstring)."""

    __slots__ = (
        "_clock",
        "_current",
        "_held",
        "_interval",
        "_last_log",
        "_lock",
        "_logger",
        "_on_standby",
        "_rounds",
        "_standby",
        "_taken",
    )

    def __init__(
        self,
        lock: SingletonLock,
        rounds: Rounds,
        *,
        logger: logging.Logger,
        standby_log_interval: timedelta,
        on_standby: Callable[[bool], object],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if standby_log_interval <= timedelta(0):
            raise ValueError("bootstrap: the standby log interval must be positive")
        self._lock = lock
        self._rounds = rounds
        self._logger = logger
        self._interval = standby_log_interval.total_seconds()
        self._on_standby = on_standby
        self._clock = clock
        self._last_log: float | None = None
        self._standby: bool | None = None
        self._held = asyncio.Event()
        self._taken = False
        self._current: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Take the lock, then run the rounds while it is held and certain, until cancelled. A
        lost lock cancels the rounds and is raised."""
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._hold())
                group.create_task(self._rounds_while_held())
        except* Exception as errors:
            raise errors.exceptions[0] from None

    async def _hold(self) -> NoReturn:
        try:
            await self._lock.hold(
                on_standby=self._standing_by, on_held=self._confirmed, on_uncertain=self._paused
            )
        except Exception as exc:
            self._logger.error(
                "bootstrap singleton lock lost; its rounds stop and the process exits (error=%s)",
                type(exc).__name__,
            )
            raise

    async def _rounds_while_held(self) -> None:
        """The rounds, started each time the lock is held and cancelled by ``_paused``."""
        while True:
            await self._held.wait()
            if not self._held.is_set():  # paused again before this task woke up
                continue
            rounds = asyncio.create_task(self._rounds.run())
            self._current = rounds
            try:
                await rounds
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the process stops (awaiting ``rounds`` cancelled it too)
                continue  # paused: wait until the lock is confirmed
            finally:
                self._current = None
            return  # the rounds ended on their own (``TenantLoop.run`` never does)

    def _confirmed(self) -> None:
        if not self._taken:
            self._taken = True
            self._report(standby=False)
            self._logger.info(HELD_MESSAGE)
        else:
            self._logger.warning(RESUMED_MESSAGE)
        self._held.set()

    def _paused(self) -> None:
        """Synchronous, so no round can start between the stall and the pause."""
        self._held.clear()
        if self._current is not None:
            self._current.cancel()
        self._logger.warning(UNCERTAIN_MESSAGE)

    def _standing_by(self) -> None:
        now = self._clock()
        if self._last_log is None or now - self._last_log >= self._interval:
            self._logger.warning(STANDBY_MESSAGE)
            self._last_log = now
        self._report(standby=True)

    def _report(self, *, standby: bool) -> None:
        if self._standby is standby:
            return
        self._standby = standby
        with contextlib.suppress(Exception):  # a metric never changes the lock
            self._on_standby(standby)
