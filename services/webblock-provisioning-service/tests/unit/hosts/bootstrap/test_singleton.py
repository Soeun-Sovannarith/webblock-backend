"""Task H5c: the bootstrap runs its rounds only while it holds the singleton lock.

The lock here is a fake with the shape of ``infrastructure.postgres.session_lock.PgSessionLock``;
the rounds are the real ``TenantLoop`` over fake projects and a fake cluster.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

import pytest

from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop
from provisioning.hosts.bootstrap.singleton import (
    RESUMED_MESSAGE,
    STANDBY_MESSAGE,
    UNCERTAIN_MESSAGE,
    Singleton,
)
from provisioning.hosts.shared.runtime import EXIT_FAILURE, LoopEnded, run_until_stopped
from provisioning.infrastructure.postgres.session_lock import LockLost, LockTiming, keep_lock

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
LOGGER = "test.bootstrap.singleton"

pytestmark = pytest.mark.asyncio


class Projects:
    """Counts the rounds (one read per round); ``block`` holds a round inside its read."""

    def __init__(self) -> None:
        self.rounds = 0
        self.block: asyncio.Event | None = None
        self.cancelled = 0

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        self.rounds += 1
        if self.block is not None:
            try:
                await self.block.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
        return [TenantProject(PROJECT, f"site-{PROJECT.hex}")]


class Cluster:
    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        return TenantState.CREATING  # never remembered: every round asks again


class FakeLock:
    """``hold``: ``standbys`` standby reports (the clock advanced before each), then waits for
    ``grant``; once held, waits for ``lose`` and raises ``LockLost``."""

    def __init__(self, clock: list[float], standbys: tuple[float, ...] = ()) -> None:
        self.clock = clock
        self.standbys = standbys
        self.grant = asyncio.Event()
        self.lose = asyncio.Event()
        self.calls = 0
        self.cancelled = 0
        self.held = 0

    async def hold(
        self,
        *,
        on_standby: Callable[[], object],
        on_held: Callable[[], object],
        on_uncertain: Callable[[], object],
    ) -> NoReturn:
        self.calls += 1
        try:
            for at in self.standbys:
                self.clock[0] = at
                on_standby()
                await asyncio.sleep(0)
            await self.grant.wait()
            self.held += 1
            on_held()
            await self.lose.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        raise LockLost("singleton lock: the session ended")


def rounds(projects: Projects) -> TenantLoop:
    return TenantLoop(
        LoopSettings(
            interval=timedelta(milliseconds=1),
            batch_size=10,
            concurrency=1,
            call_timeout=timedelta(seconds=5),
        ),
        projects=projects,
        bootstrap=Cluster(),
        logger=logging.getLogger(LOGGER),
    )


def singleton(lock: FakeLock, projects: Projects, standby: list[bool]) -> Singleton:
    return Singleton(
        lock,
        rounds(projects),
        logger=logging.getLogger(LOGGER),
        standby_log_interval=timedelta(seconds=30),
        on_standby=standby.append,
        clock=lambda: lock.clock[0],
    )


async def until(what: str, done: Callable[[], bool]) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not done():
        assert asyncio.get_running_loop().time() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.001)


async def stopped(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_no_round_runs_before_the_lock_is_held(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    lock, projects = FakeLock([0.0], standbys=(0.0, 10.0, 31.0, 40.0)), Projects()
    standby: list[bool] = []
    task = asyncio.create_task(singleton(lock, projects, standby).run())
    await until("the lock waiting for its grant", lambda: lock.clock[0] == 40.0)
    await asyncio.sleep(0.05)  # the rounds would have run many times by now
    assert projects.rounds == 0
    # The standby line at the start and again once the interval passed (0 s, 31 s), never more.
    lines = [r.getMessage() for r in caplog.records if r.getMessage() == STANDBY_MESSAGE]
    assert lines == [STANDBY_MESSAGE, STANDBY_MESSAGE]
    assert STANDBY_MESSAGE == "bootstrap standby: another bootstrap holds the lock"
    assert standby == [True]

    lock.grant.set()
    await until("two rounds once the lock is held", lambda: projects.rounds >= 2)
    assert standby == [True, False]
    await stopped(task)
    assert lock.cancelled == 1  # stopping the process ends the lock's session


async def test_the_lock_is_taken_once_and_kept_across_rounds() -> None:
    lock, projects = FakeLock([0.0]), Projects()
    standby: list[bool] = []
    lock.grant.set()
    task = asyncio.create_task(singleton(lock, projects, standby).run())
    await until("five rounds", lambda: projects.rounds >= 5)
    # One lock for the process's life: never released and taken again between rounds.
    assert (lock.calls, lock.held, lock.cancelled) == (1, 1, 0)
    assert standby == [False]
    await stopped(task)
    assert lock.cancelled == 1


async def test_a_lost_lock_stops_the_rounds_at_once_and_the_process_exits_non_zero() -> None:
    lock, projects = FakeLock([0.0]), Projects()
    standby: list[bool] = []
    lock.grant.set()
    projects.block = asyncio.Event()  # the first round stays inside its read
    guard = singleton(lock, projects, standby)
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_until_stopped(stop, first=(), then=(guard.run,), logger=logging.getLogger(LOGGER))
    )
    await until("a round in progress", lambda: projects.rounds == 1)
    lock.lose.set()
    with pytest.raises(LoopEnded, match=r"Singleton\.run"):  # __main__ exits EXIT_FAILURE on it
        async with asyncio.timeout(5):
            await task
    assert projects.cancelled == 1  # the round in progress was cancelled, not finished
    projects.block.set()
    await asyncio.sleep(0.05)
    assert projects.rounds == 1  # and no round ran after the loss


async def test_a_lock_that_fails_while_standing_by_never_runs_a_round() -> None:
    lock, projects = FakeLock([0.0], standbys=(0.0,)), Projects()
    standby: list[bool] = []

    async def broken(
        *,
        on_standby: Callable[[], object],
        on_held: Callable[[], object],
        on_uncertain: Callable[[], object],
    ) -> NoReturn:
        on_standby()
        raise LockLost("singleton lock: the try failed")

    lock.hold = broken  # type: ignore[method-assign]  # the standby's connection fails
    with pytest.raises(LockLost):
        async with asyncio.timeout(5):
            await singleton(lock, projects, standby).run()
    assert projects.rounds == 0


# --- Task HF-A (H-V F-1): a stalled check pauses the rounds; it does not end the process ---------
#
# The lock here is the real ``keep_lock`` over a fake session whose checks the test answers: a
# stall is a check without an answer yet (a database container paused, as in
# ``tests/e2e/faults/test_qa_database_paused.py``).


class StallingSession:
    """``take`` grants at once and ``watch`` returns at once; a check answers ``True`` at once
    unless ``stall`` is set, in which case it waits for the test (``answer``)."""

    def __init__(self) -> None:
        self.stall = False
        self.pending: list[asyncio.Future[bool]] = []

    async def take(self) -> bool:
        return True

    def check(self) -> asyncio.Future[bool]:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        if self.stall:
            self.pending.append(future)
        else:
            future.set_result(True)
        return future

    async def watch(self, seconds: float) -> None:
        await asyncio.sleep(0.005)

    def answer(self, held: bool = True) -> None:
        self.stall = False
        for future in self.pending:
            if not future.done():
                future.set_result(held)


class SessionLock:
    """A ``SingletonLock`` with the real lock logic over a ``StallingSession``."""

    def __init__(self, session: StallingSession, stall: float = 5.0) -> None:
        self.session = session
        self.timing = LockTiming(poll=0.01, check=0.005, statement=0.05, stall=stall)

    async def hold(
        self,
        *,
        on_standby: Callable[[], object],
        on_held: Callable[[], object],
        on_uncertain: Callable[[], object],
    ) -> NoReturn:
        await keep_lock(
            self.session,
            self.timing,
            on_standby=on_standby,
            on_held=on_held,
            on_uncertain=on_uncertain,
        )


def guarded(
    lock: SessionLock, projects: Projects
) -> tuple[Singleton, asyncio.Event, asyncio.Task[None]]:
    guard = Singleton(
        lock,
        rounds(projects),
        logger=logging.getLogger(LOGGER),
        standby_log_interval=timedelta(seconds=30),
        on_standby=lambda standby: None,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_until_stopped(stop, first=(), then=(guard.run,), logger=logging.getLogger(LOGGER))
    )
    return guard, stop, task


async def test_a_stalled_check_pauses_the_rounds_and_a_confirming_check_resumes_them(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    session, projects = StallingSession(), Projects()
    _, stop, task = guarded(SessionLock(session), projects)
    await until("rounds", lambda: projects.rounds >= 2)
    projects.block = asyncio.Event()  # the next round stays inside its read
    entered = projects.rounds
    await until("a round in progress", lambda: projects.rounds == entered + 1)
    session.stall = True
    await until("the round in progress cancelled", lambda: projects.cancelled == 1)
    paused_at = projects.rounds
    projects.block = None
    await asyncio.sleep(0.3)  # six statement bounds: still uncertain, and no round started
    assert projects.rounds == paused_at
    assert not task.done()  # the process does not exit

    session.answer(True)
    await until("rounds again", lambda: projects.rounds >= paused_at + 2)
    assert not task.done()
    messages = [r.getMessage() for r in caplog.records]
    assert UNCERTAIN_MESSAGE in messages
    assert RESUMED_MESSAGE in messages
    assert messages.index(UNCERTAIN_MESSAGE) < messages.index(RESUMED_MESSAGE)
    assert not any("lost" in m for m in messages)
    stop.set()
    async with asyncio.timeout(5):
        await task  # a requested stop is a clean end (no LoopEnded)


async def test_no_round_starts_while_the_lock_is_uncertain() -> None:
    session, projects = StallingSession(), Projects()
    _, stop, task = guarded(SessionLock(session), projects)
    await until("rounds", lambda: projects.rounds >= 3)
    for _ in range(3):  # three stalls in a row, each answered
        session.stall = True
        await until(
            "the stall seen", lambda: bool(session.pending) and not session.pending[-1].done()
        )
        await asyncio.sleep(0.1)  # past the statement bound: uncertain
        frozen = projects.rounds
        await asyncio.sleep(0.2)  # rounds run every millisecond when allowed
        assert projects.rounds == frozen
        session.answer(True)

        def resumed(paused_at: int = frozen) -> bool:
            return projects.rounds >= paused_at + 2

        await until("rounds again", resumed)
    stop.set()
    async with asyncio.timeout(5):
        await task


async def test_a_stall_past_the_bound_ends_the_process_with_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=LOGGER)
    session, projects = StallingSession(), Projects()
    _, _, task = guarded(SessionLock(session, stall=0.3), projects)
    await until("rounds", lambda: projects.rounds >= 2)
    session.stall = True
    with pytest.raises(LoopEnded, match=r"Singleton\.run"):  # __main__ exits EXIT_FAILURE on it
        async with asyncio.timeout(5):
            await task
    assert EXIT_FAILURE == 1
    stopped_at = projects.rounds
    messages = [r.getMessage() for r in caplog.records]
    assert UNCERTAIN_MESSAGE in messages and RESUMED_MESSAGE not in messages
    assert any("singleton lock lost" in m for m in messages)
    session.answer(True)  # an answer after the process gave up changes nothing
    await asyncio.sleep(0.05)
    assert projects.rounds == stopped_at
