"""The reconciler's loop (task W6-B): rounds never overlap, a failed or hung round is logged and
the loop goes on, and cancelling stops it."""

import asyncio
from datetime import timedelta

import pytest
from tests.unit._loop_fakes import logger_with_handler

from provisioning.core.application.reconcile.background.reconcile import RoundReport
from provisioning.core.application.reconcile.background.reconciler import (
    ReconcilerLoop,
    ReconcilerLoopConfig,
)
from provisioning.core.application.shared.backoff import InvalidConfig

MS = timedelta(milliseconds=1)


class Rounds:
    def __init__(self, *outcomes: Exception | float | None, until: int = 0) -> None:
        self.outcomes = list(outcomes)
        self.until = until
        self.enough = asyncio.Event()
        self.calls = 0
        self.running = 0
        self.overlap = False

    async def round(self) -> RoundReport:
        self.calls += 1
        if self.calls >= self.until:
            self.enough.set()
        self.running += 1
        self.overlap = self.overlap or self.running > 1
        try:
            outcome = self.outcomes.pop(0) if self.outcomes else None
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, float):
                await asyncio.sleep(outcome)
            return RoundReport()
        finally:
            self.running -= 1


def loop(rounds: Rounds, interval: timedelta = MS, timeout: timedelta = 50 * MS) -> ReconcilerLoop:
    logger, _ = logger_with_handler("reconciler")
    return ReconcilerLoop(
        rounds, ReconcilerLoopConfig(interval=interval, round_timeout=timeout), logger=logger
    )


@pytest.mark.asyncio
async def test_a_failed_and_a_hung_round_are_logged_and_the_loop_goes_on() -> None:
    rounds = Rounds(ConnectionError("db down"), 5.0, None, until=4)
    logger, handler = logger_with_handler("reconciler-log")
    run = ReconcilerLoop(
        rounds, ReconcilerLoopConfig(interval=MS, round_timeout=30 * MS), logger=logger
    )
    task = asyncio.create_task(run.run())
    async with asyncio.timeout(2):
        await rounds.enough.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not rounds.overlap
    assert "ConnectionError" in handler.text()
    assert "TimeoutError" in handler.text()


@pytest.mark.asyncio
async def test_one_round_returns_its_report_or_none_when_it_failed() -> None:
    assert await loop(Rounds(None)).round_once() == RoundReport()
    assert await loop(Rounds(ValueError("x"))).round_once() is None


@pytest.mark.parametrize(
    ("interval", "timeout"), [(timedelta(0), MS), (MS, timedelta(0))], ids=["interval", "timeout"]
)
def test_non_positive_settings_are_refused(interval: timedelta, timeout: timedelta) -> None:
    with pytest.raises(InvalidConfig):
        loop(Rounds(), interval, timeout)
