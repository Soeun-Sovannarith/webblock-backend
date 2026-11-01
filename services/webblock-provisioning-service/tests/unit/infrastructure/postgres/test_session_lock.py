"""The singleton lock's settings and key (task H5c); its behaviour on PostgreSQL is in
``tests/integration/test_session_lock.py``."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from provisioning.infrastructure.postgres import InvalidArgument, Timeouts
from provisioning.infrastructure.postgres.session_lock import (
    BOOTSTRAP_LOCK_KEY,
    LockLost,
    LockTiming,
    PgSessionLock,
    keep_lock,
)

TIMEOUTS = Timeouts(
    transaction=timedelta(seconds=10), statement=timedelta(seconds=5), lock=timedelta(seconds=2)
)
SECOND = timedelta(seconds=1)
STALL = timedelta(seconds=60)


def engine() -> AsyncEngine:  # opens no connection
    return create_async_engine("postgresql+psycopg://u:p@127.0.0.1:1/x")


def test_the_bootstrap_key_is_the_documented_constant() -> None:
    assert BOOTSTRAP_LOCK_KEY == int.from_bytes(b"WBOOTSTR", "big") == 0x57424F4F54535452
    assert (BOOTSTRAP_LOCK_KEY >> 32, BOOTSTRAP_LOCK_KEY & 0xFFFFFFFF) == (1463963471, 1414747218)


@pytest.mark.parametrize("key", [0, -1, 2**63])
def test_a_key_outside_the_positive_64_bit_range_is_refused(key: int) -> None:
    with pytest.raises(InvalidArgument, match="key"):
        PgSessionLock(engine(), TIMEOUTS, key=key, poll=SECOND, check=SECOND, stall=STALL)


@pytest.mark.parametrize(
    ("poll", "check"), [(timedelta(0), SECOND), (SECOND, timedelta(0)), (-SECOND, SECOND)]
)
def test_the_intervals_must_be_positive(poll: timedelta, check: timedelta) -> None:
    with pytest.raises(InvalidArgument, match="intervals"):
        PgSessionLock(
            engine(), TIMEOUTS, key=BOOTSTRAP_LOCK_KEY, poll=poll, check=check, stall=STALL
        )


# --- Task HF-A (H-V F-1): a stalled check is uncertain, not lost --------------------------------
#
# ``keep_lock`` is the lock's logic over a ``LockSession``; ``PgSessionLock`` runs it over its own
# psycopg session (tests/integration/test_session_lock.py). Here the session is a fake whose checks
# the test answers, so a stall is a check that has no answer yet.


class FakeSession:
    """``take`` grants at once; ``watch`` returns at once; each ``check`` is a future the test
    answers (``answer``), or that answers itself when ``auto`` holds a value."""

    def __init__(self) -> None:
        self.checks: list[asyncio.Future[bool]] = []
        self.auto: bool | None = True
        self.watches = 0

    async def take(self) -> bool:
        return True

    def check(self) -> asyncio.Future[bool]:
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        if self.auto is not None:
            future.set_result(self.auto)
        self.checks.append(future)
        return future

    async def watch(self, seconds: float) -> None:
        self.watches += 1
        await asyncio.sleep(0)


class Events:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.seen: list[str] = []
        self.session = session
        self.checks_when_held: list[int] = []

    def standby(self) -> None:
        self.seen.append("standby")

    def held(self) -> None:
        self.seen.append("held")
        if self.session is not None:
            self.checks_when_held.append(len(self.session.checks))

    def uncertain(self) -> None:
        self.seen.append("uncertain")


TIMING = LockTiming(poll=0.01, check=0.01, statement=0.05, stall=0.5)


def keeping(
    session: FakeSession, events: Events, timing: LockTiming = TIMING
) -> asyncio.Task[None]:
    return asyncio.create_task(
        keep_lock(
            session,
            timing,
            on_standby=events.standby,
            on_held=events.held,
            on_uncertain=events.uncertain,
        )
    )


async def until(what: str, done: Callable[[], bool], within: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + within
    while not done():
        assert asyncio.get_running_loop().time() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.001)


async def stopped(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_check_past_its_time_bound_is_uncertain_and_a_later_check_confirms_it() -> None:
    session = FakeSession()
    events = Events(session)
    task = keeping(session, events)
    await until("the first checks", lambda: len(session.checks) >= 2)
    session.auto = None  # the next check stalls
    await until("a stalled check", lambda: not session.checks[-1].done())
    stalled = session.checks[-1]
    await until("uncertain", lambda: "uncertain" in events.seen)
    assert events.seen == ["held", "uncertain"]
    assert not task.done()  # uncertain is not lost
    count = len(session.checks)
    await asyncio.sleep(0.2)  # four statement bounds: still the same check, never a second at once
    assert len(session.checks) == count and not task.done()

    session.auto = True
    stalled.set_result(True)  # the stalled check answers: one more check, then confirmed
    await until("held again", lambda: events.seen.count("held") == 2)
    assert events.seen == ["held", "uncertain", "held"]
    assert events.checks_when_held[-1] == count + 1  # one re-check, on the same session
    watches = session.watches
    await until("watching again", lambda: session.watches > watches + 2)
    assert events.seen == ["held", "uncertain", "held"]
    await stopped(task)


@pytest.mark.asyncio
async def test_a_stall_past_the_bound_is_a_lost_lock() -> None:
    session, events = FakeSession(), Events()
    session.auto = None
    task = keeping(session, events)
    started = asyncio.get_running_loop().time()
    with pytest.raises(LockLost, match="stalled past its bound"):
        async with asyncio.timeout(5):
            await task
    took = asyncio.get_running_loop().time() - started
    assert 0.5 <= took < 2.0, took  # statement bound, then the stall bound (0.5 s)
    assert events.seen == ["held", "uncertain"]
    assert len(session.checks) == 1


@pytest.mark.asyncio
async def test_a_stalled_re_check_still_counts_against_the_same_bound() -> None:
    session, events = FakeSession(), Events()
    session.auto = None
    task = keeping(session, events)
    await until("uncertain", lambda: "uncertain" in events.seen)
    session.checks[0].set_result(True)  # answered, but the re-check stalls too
    with pytest.raises(LockLost, match="stalled past its bound"):
        async with asyncio.timeout(5):
            await task
    assert events.seen == ["held", "uncertain"]  # never resumed
    assert len(session.checks) == 2


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (False, "no longer holds"),
        (LockLost("singleton lock: the check failed"), "the check failed"),
        (RuntimeError("driver text"), "the check failed"),
    ],
)
@pytest.mark.asyncio
async def test_a_stalled_check_that_answers_not_held_or_fails_is_a_lost_lock(
    answer: bool | Exception, message: str
) -> None:
    session, events = FakeSession(), Events()
    session.auto = None
    task = keeping(session, events)
    await until("uncertain", lambda: "uncertain" in events.seen)
    if isinstance(answer, Exception):
        session.checks[0].set_exception(answer)
    else:
        session.checks[0].set_result(answer)
    with pytest.raises(LockLost, match=message) as lost:
        async with asyncio.timeout(5):
            await task
    assert "driver text" not in str(lost.value)
    assert events.seen == ["held", "uncertain"]


@pytest.mark.asyncio
async def test_a_check_answered_in_time_as_not_held_is_lost_at_once() -> None:
    session, events = FakeSession(), Events()
    session.auto = False
    with pytest.raises(LockLost, match="no longer holds"):
        async with asyncio.timeout(5):
            await keeping(session, events)
    assert events.seen == ["held"]


@pytest.mark.asyncio
async def test_a_session_that_ends_while_watching_is_lost_and_never_uncertain() -> None:
    session, events = FakeSession(), Events()

    async def ended(seconds: float) -> None:
        raise LockLost("singleton lock: the session ended")

    session.watch = ended  # type: ignore[method-assign]
    with pytest.raises(LockLost, match="session ended"):
        async with asyncio.timeout(5):
            await keeping(session, events)
    assert events.seen == ["held"]


def test_the_stall_bound_must_be_at_least_the_statement_bound() -> None:
    with pytest.raises(InvalidArgument, match="stall"):
        PgSessionLock(
            engine(),
            TIMEOUTS,
            key=BOOTSTRAP_LOCK_KEY,
            poll=SECOND,
            check=SECOND,
            stall=TIMEOUTS.statement - SECOND,
        )
