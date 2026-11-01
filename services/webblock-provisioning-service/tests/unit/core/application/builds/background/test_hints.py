"""The build worker's hint listener loop (task H2): each payload reaches ``WantedBuilds``; a failed
connection is retried after a growing, bounded backoff that starts again from the base once a
connection was ready; the start loop keeps running throughout."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application import BuildDispatch, BuildStarted
from provisioning.core.application.builds.background.hints import HintLoop
from provisioning.core.application.builds.background.start_build import WantedBuilds, build_hint
from provisioning.core.application.shared.backoff import Backoff

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
BACKOFF = Backoff(base=timedelta(seconds=1), max=timedelta(seconds=4))


class _Starter:
    def __init__(self) -> None:
        self.starts: list[tuple[UUID, UUID]] = []
        self.started = asyncio.Event()

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.starts.append((project_id, release_id))
        self.started.set()
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=release_id,
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


class _Listener:
    """Each call runs the next script: ``"fail"`` raises at once, ``"ready-fail"`` reports ready
    then raises, ``"serve"`` reports ready, delivers the payloads and waits forever."""

    def __init__(self, scripts: list[str], payloads: list[str]) -> None:
        self.scripts = scripts
        self.payloads = payloads
        self.calls = 0

    async def listen(
        self, on_payload: Callable[[str], object], on_ready: Callable[[], object]
    ) -> None:
        script = self.scripts[self.calls]
        self.calls += 1
        if script == "fail":
            raise OSError("connection refused, with text that must not be logged")
        on_ready()
        if script == "ready-fail":
            raise OSError("server closed the connection")
        for payload in self.payloads:
            on_payload(payload)
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_the_loop_reconnects_with_backoff_and_delivers_hints(
    caplog: pytest.LogCaptureFixture,
) -> None:
    starter = _Starter()
    listener = _Listener(
        ["fail", "fail", "ready-fail", "fail", "serve"], ["junk", build_hint(PROJECT, RELEASE)]
    )
    delays: list[timedelta] = []

    async def sleep(delay: timedelta) -> None:
        delays.append(delay)

    loop = HintLoop(
        listener=listener,
        wanted=WantedBuilds(starter=starter, queue_size=4, logger=logging.getLogger("h")),
        reconnect=BACKOFF,
        logger=logging.getLogger("h"),
        sleep=sleep,
    )
    with caplog.at_level(logging.INFO, logger="h"):
        async with asyncio.TaskGroup() as tg:
            task = tg.create_task(loop.run())
            async with asyncio.timeout(5):
                await starter.started.wait()
            task.cancel()
    assert starter.starts == [(PROJECT, RELEASE)]
    assert listener.calls == 5
    # Failures 1 and 2 grow the delay; the connection that was ready resets it, so the next
    # failure waits about the base again (each delay is jittered into (d/2, d]).
    assert len(delays) == 4
    assert timedelta(seconds=0.5) < delays[0] <= timedelta(seconds=1)
    assert timedelta(seconds=1) < delays[1] <= timedelta(seconds=2)
    assert timedelta(seconds=0.5) < delays[2] <= timedelta(seconds=1)
    assert timedelta(seconds=0.5) < delays[3] <= timedelta(seconds=1) * 2
    assert "build hint listener ready" in caplog.text
    assert "malformed build hint dropped" in caplog.text
    assert "must not be logged" not in caplog.text
    assert "error=OSError" in caplog.text


def test_the_reconnect_backoff_must_be_valid() -> None:
    with pytest.raises(ValueError, match="backoff"):
        HintLoop(
            listener=_Listener([], []),
            wanted=WantedBuilds(starter=_Starter(), queue_size=1, logger=logging.getLogger("h")),
            reconnect=Backoff(base=timedelta(0), max=timedelta(seconds=1)),
            logger=logging.getLogger("h"),
        )
