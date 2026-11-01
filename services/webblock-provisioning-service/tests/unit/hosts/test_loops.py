"""Task G's host-level pieces: the bootstrap loop, the recording hosting intake of the worker,
and the stop order of ``run_until_stopped``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.unit._buses import intake_bus

from provisioning.core.application import (
    AcceptedCommand,
    AcceptHostingEventCommand,
    OperationInProgress,
)
from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop
from provisioning.core.domain import CommandKind, CommandRequest, new_command
from provisioning.hosts.shared.runtime import LoopEnded, run_until_stopped
from provisioning.hosts.worker.payments import RecordingHostingEvents
from provisioning.infrastructure.standins import PrototypePaymentEventRecord

LOG = logging.getLogger("test")


def _project(n: int) -> UUID:
    return UUID(f"0190a1b2-0000-7000-8000-{n:012x}")


SETTINGS = LoopSettings(
    interval=timedelta(milliseconds=10),
    batch_size=2,
    concurrency=2,
    call_timeout=timedelta(seconds=1),
)


class _Projects:
    def __init__(self, ids: list[UUID]) -> None:
        self.ids = sorted(ids)
        self.pages: list[tuple[UUID | None, int]] = []

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        self.pages.append((after, limit))
        rest = [p for p in self.ids if after is None or p > after]
        return [TenantProject(p, subdomain_of(p)) for p in rest[:limit]]


def subdomain_of(project_id: UUID) -> str:
    return f"site-{project_id.hex}"


class _Bootstrap:
    def __init__(self, states: dict[UUID, TenantState | Exception]) -> None:
        self.states = states
        self.calls: list[UUID] = []
        self.subdomains: dict[UUID, str] = {}
        self.in_flight = 0
        self.peak = 0
        self.hang: set[UUID] = set()

    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        self.calls.append(project_id)
        self.subdomains[project_id] = subdomain
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if project_id in self.hang:
                await asyncio.sleep(10)
            state = self.states.get(project_id, TenantState.READY)
            if isinstance(state, Exception):
                raise state
            return state
        finally:
            self.in_flight -= 1


@pytest.mark.asyncio
async def test_a_round_walks_every_page_and_remembers_ready_tenants() -> None:
    ids = [_project(n) for n in range(1, 6)]
    projects, bootstrap = _Projects(ids), _Bootstrap({})
    loop = TenantLoop(SETTINGS, projects=projects, bootstrap=bootstrap, logger=LOG)
    result = await loop.round()
    assert sorted(result.ready) == ids
    assert [after for after, _ in projects.pages] == [None, ids[1], ids[3]]
    assert bootstrap.peak <= SETTINGS.concurrency
    # The next round asks the cluster about no project already found ready.
    bootstrap.calls.clear()
    again = await loop.round()
    assert again.ready == () and bootstrap.calls == []


@pytest.mark.asyncio
async def test_each_tenant_is_ensured_with_its_own_subdomain() -> None:
    """Decision 0023 G18a: the sub-domain read with the project goes into its namespace."""
    ids = [_project(n) for n in range(1, 4)]
    bootstrap = _Bootstrap({})
    loop = TenantLoop(SETTINGS, projects=_Projects(ids), bootstrap=bootstrap, logger=LOG)
    await loop.round()
    assert bootstrap.subdomains == {p: subdomain_of(p) for p in ids}


@pytest.mark.asyncio
async def test_a_tenant_not_ready_failed_or_timed_out_is_ensured_again_next_round() -> None:
    p1, p2, p3, p4 = (_project(n) for n in range(1, 5))
    bootstrap = _Bootstrap(
        {p1: TenantState.CREATING, p2: TenantState.TERMINATING, p3: RuntimeError("403")}
    )
    bootstrap.hang.add(p4)
    settings = LoopSettings(
        interval=timedelta(milliseconds=10),
        batch_size=4,
        concurrency=4,
        call_timeout=timedelta(milliseconds=100),
    )
    loop = TenantLoop(
        settings, projects=_Projects([p1, p2, p3, p4]), bootstrap=bootstrap, logger=LOG
    )
    result = await loop.round()
    assert result.ready == ()
    assert sorted(result.pending) == [p1, p2, p3, p4]
    bootstrap.states = {}
    bootstrap.hang.clear()
    assert sorted((await loop.round()).ready) == [p1, p2, p3, p4]


@pytest.mark.asyncio
async def test_the_after_ready_hook_runs_before_a_tenant_is_remembered() -> None:
    p1 = _project(1)
    failures = [RuntimeError("catalog down")]
    seen: list[UUID] = []

    async def after_ready(project_id: UUID) -> None:
        seen.append(project_id)
        if failures:
            raise failures.pop()

    loop = TenantLoop(
        SETTINGS,
        projects=_Projects([p1]),
        bootstrap=_Bootstrap({}),
        logger=LOG,
        after_ready=after_ready,
    )
    assert (await loop.round()).pending == (p1,)
    assert not loop.is_known_ready(p1)
    assert (await loop.round()).ready == (p1,)
    assert seen == [p1, p1]


def test_loop_settings_are_checked() -> None:
    with pytest.raises(ValueError):
        LoopSettings(timedelta(0), 1, 1, timedelta(seconds=1)).validate()
    with pytest.raises(ValueError):
        LoopSettings(timedelta(seconds=1), 1, 2, timedelta(seconds=1)).validate()


# --- the recording hosting events --------------------------------------------------------------


class _Intake:
    def __init__(self, record: PrototypePaymentEventRecord) -> None:
        self.record = record
        self.seen: list[tuple[str, bool | None]] = []
        self.err: Exception | None = None

    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand:
        self.seen.append((event_key, await self.record.entitlement_after(project_id, "pay-1")))
        if self.err is not None:
            raise self.err
        command = new_command(
            command_id=_project(99),
            request=CommandRequest(
                project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
            ),
            requested_by_subject=None,
            command_number=1,
            now=datetime(2026, 10, 8, tzinfo=UTC),
        )
        return AcceptedCommand(command=command, replayed=False)


@pytest.mark.asyncio
async def test_each_payment_event_is_recorded_before_its_command_is_accepted() -> None:
    record = PrototypePaymentEventRecord(max_projects=10)
    intake = _Intake(record)
    recording = RecordingHostingEvents(intake_bus(hosting=intake), record)
    p = _project(1)
    await recording.send(AcceptHostingEventCommand("pay-1", p, CommandKind.SUSPEND_HOSTING))
    # Recorded by its key before the command is accepted; nothing after it yet.
    assert intake.seen == [("pay-1", None)]
    intake.err = OperationInProgress()
    with pytest.raises(OperationInProgress):
        await recording.send(AcceptHostingEventCommand("pay-2", p, CommandKind.RESUME_HOSTING))
    # A refused event (retried later) is still recorded: a restore after the suspend's event.
    assert intake.seen == [("pay-1", None), ("pay-2", True)]
    assert await record.entitlement_after(p, "pay-1") is True
    assert await record.entitlement_after(p, "pay-2") is None


# --- run_until_stopped -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_work_stops_before_the_connections() -> None:
    order: list[str] = []
    stop = asyncio.Event()

    async def connection() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            order.append("connection")

    async def work() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            order.append("work")

    async def stopper() -> None:
        await asyncio.sleep(0.05)
        stop.set()

    async with asyncio.TaskGroup() as tg:
        tg.create_task(stopper())
        await run_until_stopped(stop, first=(connection,), then=(work,), logger=LOG)
    assert order == ["work", "connection"]


@pytest.mark.asyncio
async def test_a_loop_that_ends_on_its_own_stops_the_process_with_an_error() -> None:
    stop = asyncio.Event()

    async def broken() -> None:
        raise RuntimeError("boom")

    async def forever() -> None:
        await asyncio.sleep(10)

    with pytest.raises(LoopEnded):
        await run_until_stopped(stop, first=(forever,), then=(broken,), logger=LOG)
