"""The build worker's hint listener loop (task H2; decision 0016 section 5 item 2).

The API sends a PostgreSQL notification when build_release finds a pending build attempt.
``HintLoop`` keeps one LISTEN connection open (``PgBuildHintListener``) and hands each payload to
``WantedBuilds``, which starts the release at once. When the connection fails it reconnects after
a bounded, jittered backoff (BUILD_HINT_RECONNECT_BASE to BUILD_HINT_RECONNECT_MAX), which starts
again from the base once a connection was ready. A hint sent while the listener is down is lost;
the build poller's grace scan (``StartPendingBuilds``, BUILD_PENDING_GRACE) still starts that
attempt, so the hint only makes a start prompt and is never the record.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from provisioning.core.application.builds.background.start_build import WantedBuilds
from provisioning.core.application.shared.backoff import Backoff


class HintListener(Protocol):
    """``PgBuildHintListener.listen``: returns only by cancellation, raises when the connection
    fails."""

    async def listen(
        self, on_payload: Callable[[str], object], on_ready: Callable[[], object]
    ) -> None: ...


async def _sleep(delay: timedelta) -> None:
    await asyncio.sleep(delay.total_seconds())


class HintLoop:
    """Listen for build hints until cancelled, reconnecting with backoff."""

    __slots__ = ("_backoff", "_listener", "_logger", "_sleep", "_wanted")

    def __init__(
        self,
        *,
        listener: HintListener,
        wanted: WantedBuilds,
        reconnect: Backoff,
        logger: logging.Logger,
        sleep: Callable[[timedelta], Awaitable[None]] = _sleep,
    ) -> None:
        reconnect.validate()
        self._listener = listener
        self._wanted = wanted
        self._backoff = reconnect
        self._logger = logger
        self._sleep = sleep

    async def run(self) -> None:
        """Run the listener and the start loop until cancelled."""
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._wanted.run())
            await self._listen()

    async def _listen(self) -> None:
        failures = 0
        while True:
            ready = False

            def on_ready() -> None:
                nonlocal ready, failures
                ready, failures = True, 0
                self._logger.info("build hint listener ready")

            try:
                await self._listener.listen(self._wanted.offer, on_ready)
                error = "returned"
            except Exception as exc:  # reconnect; the grace scan covers what is missed
                error = type(exc).__name__
            failures += 1
            delay = self._backoff.delay(failures)
            self._logger.warning(
                "build hint listener stopped; reconnecting, and the grace scan starts what it "
                "misses (error=%s was_ready=%s retry_in=%s)",
                error,
                ready,
                delay,
            )
            await self._sleep(delay)
