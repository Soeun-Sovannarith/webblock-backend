"""The build worker's poll loop (decision 0014 section 3)."""

import asyncio
import dataclasses
import itertools
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.unit._loop_fakes import MS, eventually, logger_with_handler

from provisioning.core.application import (
    BuildStepKey,
    OverdueBuild,
    PollOutcome,
    PollResult,
    PollRoundParams,
    SweepOutcome,
    SweepResult,
    SweepRoundParams,
)
from provisioning.core.application.builds.background.build_poller import (
    BuildPoller,
    BuildPollerConfig,
)
from provisioning.core.application.builds.background.poll_builds import (
    PendingOutcome,
    PendingStartResult,
)
from provisioning.core.application.builds.ports import PendingBuild
from provisioning.core.application.shared.backoff import InvalidConfig

pytestmark = pytest.mark.asyncio

STEP = BuildStepKey(project_id=UUID(int=1), release_id=UUID(int=2), step_id=UUID(int=3))


def config(**changes: Any) -> BuildPollerConfig:
    base = BuildPollerConfig(
        poll_interval=2 * MS,
        poll=PollRoundParams(limit=10, concurrency=2),
        sweep_interval=5 * MS,
        sweep=SweepRoundParams(limit=10, concurrency=2, transaction_timeout=timedelta(seconds=1)),
    )
    return dataclasses.replace(base, **changes)


class Rounds:
    """Both use cases; records every round and the most rounds of any kind running at once."""

    def __init__(self) -> None:
        self.log: list[str] = []
        self.in_flight = 0
        self.peak = 0
        self.delay = 0.0
        self.poll_errors: list[Exception] = []
        self.poll_results: list[PollResult] = []
        self.sweep_results: list[SweepResult] = []
        self.pending_results: list[PendingStartResult] = []
        self.pending_errors: list[Exception] = []

    async def _round(self, name: str) -> None:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.log.append(f"{name}-start")
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        finally:
            self.log.append(f"{name}-end")
            self.in_flight -= 1

    async def poll_round(self, params: PollRoundParams) -> list[PollResult]:
        await self._round("poll")
        if self.poll_errors:
            raise self.poll_errors.pop(0)
        return self.poll_results

    async def sweep_round(self, params: SweepRoundParams) -> list[SweepResult]:
        await self._round("sweep")
        return self.sweep_results

    async def start_round(self, params: PollRoundParams) -> list[PendingStartResult]:
        await self._round("pending")
        if self.pending_errors:
            raise self.pending_errors.pop(0)
        return self.pending_results


def build(cfg: BuildPollerConfig | None = None) -> tuple[BuildPoller, Rounds, list[str]]:
    rounds = Rounds()
    logger, handler = logger_with_handler("build-poller")
    return (
        BuildPoller(cfg or config(), polls=rounds, sweeps=rounds, pending=rounds, logger=logger),
        rounds,
        handler.lines,
    )


async def test_polls_and_sweeps_never_overlap() -> None:
    poller, rounds, _ = build()
    rounds.delay = 0.004
    task = asyncio.create_task(poller.run())
    await eventually(
        lambda: rounds.log.count("poll-end") >= 4 and rounds.log.count("sweep-end") >= 2
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rounds.peak == 1
    # Every start is followed by its own end before the next start.
    starts_and_ends = [entry.split("-")[1] for entry in rounds.log]
    assert all(a != b for a, b in itertools.pairwise(starts_and_ends))


async def test_each_round_kind_keeps_its_own_interval() -> None:
    poller, rounds, _ = build(config(poll_interval=2 * MS, sweep_interval=timedelta(hours=1)))
    task = asyncio.create_task(poller.run())
    await eventually(lambda: rounds.log.count("poll-end") >= 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rounds.log.count("sweep-start") == 1  # once at start, then not before an hour


async def test_a_failed_round_is_logged_and_the_loop_goes_on() -> None:
    poller, rounds, lines = build()
    rounds.poll_errors = [ConnectionError("pw=hunter2"), RuntimeError("x")]
    task = asyncio.create_task(poller.run())
    await eventually(lambda: rounds.log.count("poll-end") >= 4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sum("build poll round failed" in line for line in lines) == 2


async def test_round_results_are_logged_with_ids_only() -> None:
    poller, rounds, lines = build()
    rounds.poll_results = [
        PollResult(step=STEP, attempt=1, outcome=PollOutcome.SUCCEEDED),
        PollResult(
            step=STEP, attempt=2, outcome=PollOutcome.ERROR, error=ConnectionError("pw=hunter2")
        ),
    ]
    rounds.sweep_results = [
        SweepResult(
            candidate=OverdueBuild(
                step=STEP, attempt=1, deadline_at=datetime(2026, 1, 1, tzinfo=UTC)
            ),
            outcome=SweepOutcome.FAILED,
        )
    ]
    await poller.poll_once()
    await poller.sweep_once()
    text = "\n".join(lines)
    assert "hunter2" not in text
    assert str(STEP.release_id) in text and "succeeded" in text and "failed" in text


@pytest.mark.parametrize(
    "changes",
    [
        {"poll_interval": timedelta(0)},
        {"sweep_interval": timedelta(0)},
        {"poll": PollRoundParams(limit=0, concurrency=1)},
        {
            "sweep": SweepRoundParams(
                limit=1, concurrency=2, transaction_timeout=timedelta(seconds=1)
            )
        },
    ],
)
async def test_the_config_refuses_bad_values(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidConfig):
        config(**changes).validate()
    with pytest.raises(InvalidConfig):
        build(config(**changes))


async def test_qa10_every_poll_round_is_followed_by_a_pending_round() -> None:
    poller, rounds, _ = build(config(sweep_interval=timedelta(hours=1)))
    task = asyncio.create_task(poller.run())
    await eventually(lambda: rounds.log.count("pending-end") >= 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    polls = [e for e in rounds.log if e.endswith("-start") and not e.startswith("sweep")]
    assert polls[:6] == ["poll-start", "pending-start"] * 3
    assert rounds.peak == 1


async def test_a_failed_pending_round_is_logged_and_the_loop_goes_on() -> None:
    poller, rounds, lines = build()
    rounds.pending_errors = [ConnectionError("pw=hunter2")]
    await poller.poll_once()
    await poller.poll_once()
    text = "\n".join(lines)
    assert sum("pending build round failed" in line for line in lines) == 1
    assert "hunter2" not in text
    assert rounds.log.count("pending-end") == 2


async def test_pending_results_are_logged_with_ids_only() -> None:
    poller, rounds, lines = build()
    pending = PendingBuild(step=STEP, attempt=2, pending_since=datetime(2026, 1, 1, tzinfo=UTC))
    rounds.pending_results = [
        PendingStartResult(build=pending, outcome=PendingOutcome.STARTED),
        PendingStartResult(
            build=pending, outcome=PendingOutcome.ERROR, error=ConnectionError("pw=hunter2")
        ),
    ]
    await poller.poll_once()
    text = "\n".join(lines)
    assert "hunter2" not in text
    assert "pending build attempt started" in text
    assert "attempt=2" in text and str(STEP.release_id) in text
    assert "ConnectionError" in text


async def test_a_sweep_that_cancelled_a_build_says_so() -> None:
    poller, rounds, lines = build()
    rounds.sweep_results = [
        SweepResult(
            candidate=OverdueBuild(
                step=STEP, attempt=1, deadline_at=datetime(2026, 1, 1, tzinfo=UTC), log_ref="x/1"
            ),
            outcome=SweepOutcome.FAILED,
            cancelled=False,
        )
    ]
    await poller.sweep_once()
    assert any("cancelled=False" in line for line in lines)


class RoundMetrics:
    """Task H4's ``BuildRoundMetrics``: the results counted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def build_round_result(self, round_name: str, outcome: str) -> None:
        self.calls.append((round_name, outcome))


async def test_every_round_result_is_counted_by_round_and_outcome() -> None:
    rounds = Rounds()
    metrics = RoundMetrics()
    logger, _ = logger_with_handler("build-poller-metrics")
    poller = BuildPoller(
        config(), polls=rounds, sweeps=rounds, pending=rounds, logger=logger, metrics=metrics
    )
    overdue = OverdueBuild(step=STEP, attempt=1, deadline_at=datetime(2026, 1, 1, tzinfo=UTC))
    pending = PendingBuild(step=STEP, attempt=2, pending_since=datetime(2026, 1, 1, tzinfo=UTC))
    rounds.poll_results = [
        PollResult(STEP, 1, PollOutcome.UNAVAILABLE),
        PollResult(STEP, 1, PollOutcome.SUCCEEDED),
    ]
    rounds.pending_results = [PendingStartResult(build=pending, outcome=PendingOutcome.STARTED)]
    rounds.sweep_results = [
        SweepResult(candidate=overdue, outcome=SweepOutcome.FAILED),
        SweepResult(candidate=overdue, error=RuntimeError("tx")),
    ]
    await poller.poll_once()
    await poller.sweep_once()
    rounds.poll_errors = [RuntimeError("down")]
    rounds.pending_errors = [RuntimeError("down")]
    await poller.poll_once()
    assert metrics.calls == [
        ("poll", "unavailable"),
        ("poll", "succeeded"),
        ("pending", "started"),
        ("sweep", "failed"),
        ("sweep", "error"),
        ("poll", "round_failed"),
        ("pending", "round_failed"),
    ]
