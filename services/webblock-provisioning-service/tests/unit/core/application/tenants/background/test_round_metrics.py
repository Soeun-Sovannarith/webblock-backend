"""Task H4: the bootstrap loop reports each round's end (tenants left not ready, or a failed
round) to its observer, the metrics; an observer that raises changes nothing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop

READY = UUID("0190a1b2-0000-7000-8000-000000000001")
CREATING = UUID("0190a1b2-0000-7000-8000-000000000002")


class Projects:
    def __init__(self) -> None:
        self.fail = False

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        if self.fail:
            raise RuntimeError("database down")
        return (
            []
            if after is not None
            else [
                TenantProject(READY, f"site-{READY.hex}"),
                TenantProject(CREATING, f"site-{CREATING.hex}"),
            ]
        )


class Cluster:
    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        return TenantState.READY if project_id == READY else TenantState.CREATING


class Seen(list[int | None]):
    """The rounds observed; ``reached`` is set once ``want`` rounds were seen."""

    def __init__(self) -> None:
        super().__init__()
        self.want = 0
        self.reached = asyncio.Event()

    def add(self, value: int | None) -> None:
        self.append(value)
        if len(self) >= self.want:
            self.reached.set()


def _loop(projects: Projects, seen: Seen, *, broken: bool = False) -> TenantLoop:
    def observe(not_ready: int | None) -> None:
        seen.add(not_ready)
        if broken:
            raise RuntimeError("metrics down")

    return TenantLoop(
        LoopSettings(
            interval=timedelta(milliseconds=1),
            batch_size=10,
            concurrency=2,
            call_timeout=timedelta(seconds=5),
        ),
        projects=projects,
        bootstrap=Cluster(),
        logger=logging.getLogger("test.bootstrap"),
        on_round=observe,
    )


async def _rounds(loop: TenantLoop, seen: Seen, count: int) -> None:
    seen.want = count
    seen.reached.clear()
    task = asyncio.create_task(loop.run())
    async with asyncio.timeout(5):
        await seen.reached.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_each_round_reports_the_tenants_left_not_ready_or_its_failure() -> None:
    projects = Projects()
    seen = Seen()
    loop = _loop(projects, seen)
    await _rounds(loop, seen, 1)
    projects.fail = True
    await _rounds(loop, seen, 2)
    assert seen[:2] == [1, None]


@pytest.mark.asyncio
async def test_an_observer_that_raises_does_not_stop_the_loop() -> None:
    seen = Seen()
    await _rounds(_loop(Projects(), seen, broken=True), seen, 3)
    assert seen[:3] == [1, 1, 1]  # the CREATING tenant stays not ready in every round
