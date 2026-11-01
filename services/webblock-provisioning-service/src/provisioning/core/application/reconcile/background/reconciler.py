"""The reconciler's loop on the release worker (decision 0014 section 21 ruling 9; task W6-B).

One task calls ``Reconciler.round`` at once and then every ``RECONCILE_INTERVAL`` after the
previous round ended, so rounds never overlap. A round that fails (the database unreachable) is
logged by type and the next one runs on schedule; a round is bounded by ``round_timeout``, so a
hung call cannot stop the loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from provisioning.core.application.reconcile.background.reconcile import RoundReport
from provisioning.core.application.shared.backoff import InvalidConfig


class Reconciling(Protocol):
    """``Reconciler.round``."""

    async def round(self) -> RoundReport: ...


@dataclass(frozen=True, slots=True)
class ReconcilerLoopConfig:
    """``interval`` is RECONCILE_INTERVAL; ``round_timeout`` bounds one round."""

    interval: timedelta
    round_timeout: timedelta

    def validate(self) -> None:
        """``InvalidConfig`` for a non-positive value."""
        if self.interval <= timedelta(0) or self.round_timeout <= timedelta(0):
            raise InvalidConfig("the reconcile interval and round timeout must be positive")


class ReconcilerLoop:
    """Runs reconcile rounds until cancelled."""

    __slots__ = ("_config", "_logger", "_reconciler")

    def __init__(
        self, reconciler: Reconciling, config: ReconcilerLoopConfig, *, logger: logging.Logger
    ) -> None:
        config.validate()
        self._reconciler = reconciler
        self._config = config
        self._logger = logger

    async def run(self) -> None:
        """Round, pause, round, ... until cancelled; a failed or timed-out round is logged."""
        while True:
            await self.round_once()
            await asyncio.sleep(self._config.interval.total_seconds())

    async def round_once(self) -> RoundReport | None:
        """One bounded round; None when it failed."""
        try:
            async with asyncio.timeout(self._config.round_timeout.total_seconds()):
                return await self._reconciler.round()
        except Exception as exc:  # a failed round must not end the loop
            self._logger.warning("reconcile round failed (error=%s)", type(exc).__name__)
            return None
