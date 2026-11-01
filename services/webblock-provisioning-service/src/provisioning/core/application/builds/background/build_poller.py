"""The build worker's poll loop (decision 0014 section 3, "Build").

One loop calls ``PollBuilds.poll_round`` and then ``StartPendingBuilds.start_round`` (the pending
attempts nothing started, QA-10) every ``poll_interval``, and the build deadline sweep
(``BuildAttempts.sweep_round``) every ``sweep_interval``. All run in the same task, one after the
other, so no two rounds ever overlap: a slow round delays the next one instead of running beside
it. A round's failure is logged and the loop goes on.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from provisioning.core.application import (
    PollOutcome,
    PollResult,
    PollRoundParams,
    SweepOutcome,
    SweepResult,
    SweepRoundParams,
)
from provisioning.core.application.builds.background.poll_builds import (
    PendingOutcome,
    PendingStartResult,
)
from provisioning.core.application.ports.metrics import BuildRoundMetrics, NoMetrics
from provisioning.core.application.shared.backoff import InvalidConfig
from provisioning.core.domain import CODE_DEADLINE_EXCEEDED


class BuildPolling(Protocol):
    """``PollBuilds.poll_round``."""

    async def poll_round(self, params: PollRoundParams) -> list[PollResult]: ...


class PendingStarting(Protocol):
    """``StartPendingBuilds.start_round``."""

    async def start_round(self, params: PollRoundParams) -> list[PendingStartResult]: ...


class BuildSweeping(Protocol):
    """``BuildAttempts.sweep_round``."""

    async def sweep_round(self, params: SweepRoundParams) -> list[SweepResult]: ...


@dataclass(frozen=True, slots=True)
class BuildPollerConfig:
    """Every value required."""

    poll_interval: timedelta
    poll: PollRoundParams
    sweep_interval: timedelta
    sweep: SweepRoundParams

    def validate(self) -> None:
        """``InvalidConfig`` for a non-positive interval or invalid round parameters."""
        if self.poll_interval <= timedelta(0) or self.sweep_interval <= timedelta(0):
            raise InvalidConfig("build poll and sweep intervals must be positive")
        try:
            self.poll.validate()
            self.sweep.validate()
        except ValueError as exc:
            raise InvalidConfig(str(exc)) from None


# The round names of the build round metric (task H4) and a round's own failure.
ROUND_POLL = "poll"
ROUND_PENDING = "pending"
ROUND_SWEEP = "sweep"
ROUND_FAILED = "round_failed"
_ERROR = "error"


class BuildPoller:
    __slots__ = ("_config", "_logger", "_metrics", "_pending", "_polls", "_sweeps")

    def __init__(
        self,
        config: BuildPollerConfig,
        *,
        polls: BuildPolling,
        sweeps: BuildSweeping,
        pending: PendingStarting,
        logger: logging.Logger,
        metrics: BuildRoundMetrics | None = None,
    ) -> None:
        """``metrics`` counts each round's results by outcome (task H4); it changes nothing."""
        config.validate()
        self._config = config
        self._polls = polls
        self._pending = pending
        self._sweeps = sweeps
        self._logger = logger
        self._metrics: BuildRoundMetrics = metrics if metrics is not None else NoMetrics()

    def _measure(self, round_name: str, outcome: str) -> None:
        with contextlib.suppress(Exception):  # a measurement never changes a round
            self._metrics.build_round_result(round_name, outcome)

    async def poll_once(self) -> None:
        """One poll round, then one round of pending starts, their results logged; a failure is
        logged, not raised."""
        await self._poll()
        await self._start_pending()

    async def _start_pending(self) -> None:
        try:
            results = await self._pending.start_round(self._config.poll)
        except Exception as exc:  # the next round runs on schedule
            self._logger.warning("pending build round failed (error=%s)", type(exc).__name__)
            self._measure(ROUND_PENDING, ROUND_FAILED)
            return
        for result in results:
            self._measure(ROUND_PENDING, result.outcome.value)
            step = result.build.step
            fields = (
                f"project_id={step.project_id} release_id={step.release_id} "
                f"step_id={step.step_id} attempt={result.build.attempt} status={result.outcome}"
            )
            if result.outcome is PendingOutcome.ERROR:
                error = "unknown" if result.error is None else type(result.error).__name__
                self._logger.warning(
                    "pending build attempt not started (%s error=%s)", fields, error
                )
            elif result.outcome is PendingOutcome.STALE:
                self._logger.debug("pending build attempt already handled (%s)", fields)
            else:
                self._logger.info("pending build attempt started (%s)", fields)

    async def _poll(self) -> None:
        try:
            results = await self._polls.poll_round(self._config.poll)
        except Exception as exc:  # the next round runs on schedule
            self._logger.warning("build poll round failed (error=%s)", type(exc).__name__)
            self._measure(ROUND_POLL, ROUND_FAILED)
            return
        for result in results:
            self._measure(ROUND_POLL, result.outcome.value)
            fields = (
                f"project_id={result.step.project_id} release_id={result.step.release_id} "
                f"step_id={result.step.step_id} attempt={result.attempt} status={result.outcome}"
            )
            if result.outcome is PollOutcome.ERROR:
                error = "unknown" if result.error is None else type(result.error).__name__
                self._logger.warning("build poll of a step failed (%s error=%s)", fields, error)
            elif result.outcome is PollOutcome.WAITING:
                self._logger.debug("build still running (%s)", fields)
            else:
                self._logger.info("build step polled (%s)", fields)

    async def sweep_once(self) -> None:
        """One sweep round, its results logged; a failure is logged, not raised."""
        try:
            results = await self._sweeps.sweep_round(self._config.sweep)
        except Exception as exc:  # the next round runs on schedule
            self._logger.warning("build sweep round failed (error=%s)", type(exc).__name__)
            self._measure(ROUND_SWEEP, ROUND_FAILED)
            return
        for result in results:
            failed = result.error is not None or result.outcome is None
            self._measure(ROUND_SWEEP, _ERROR if failed else str(result.outcome))
            step = result.candidate.step
            fields = (
                f"project_id={step.project_id} release_id={step.release_id} "
                f"step_id={step.step_id} attempt={result.candidate.attempt}"
            )
            if result.error is not None:
                self._logger.warning(
                    "sweep of a build step failed; it is found again while overdue (%s error=%s)",
                    fields,
                    type(result.error).__name__,
                )
            elif result.outcome is SweepOutcome.FAILED:
                self._logger.info(
                    "build attempt failed: its deadline passed "
                    "(%s status=%s failure_code=%s cancelled=%s)",
                    fields,
                    result.outcome,
                    CODE_DEADLINE_EXCEEDED,
                    result.cancelled,
                )
            else:
                self._logger.debug(
                    "overdue build step skipped (%s status=%s)", fields, result.outcome
                )

    async def run(self) -> None:
        """Poll and sweep at once, then each on its own interval, until cancelled."""
        loop = asyncio.get_running_loop()
        poll_every = self._config.poll_interval.total_seconds()
        sweep_every = self._config.sweep_interval.total_seconds()
        next_poll = next_sweep = loop.time()
        while True:
            if loop.time() >= next_poll:
                await self.poll_once()
                next_poll = loop.time() + poll_every
            if loop.time() >= next_sweep:
                await self.sweep_once()
                next_sweep = loop.time() + sweep_every
            await asyncio.sleep(max(0.0, min(next_poll, next_sweep) - loop.time()))
