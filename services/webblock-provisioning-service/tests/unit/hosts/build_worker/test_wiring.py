"""The build worker's parts per profile (task H2). Built with unconnected engines; nothing connects.

- Prototype: the "source published" consumer and its subscription, the inbox processor, the build
  poller (polls, pending starts after the grace, the deadline sweep) and the hint listener, after
  the broker connection.
- Production: the poller alone, starting nothing (``NoPendingStarts``), with no broker connection
  and no hint listener; the repository registry and locator are closed (OD-01).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.hosts.envs import build_worker_env, production

from provisioning.core.application import NullAuditLog, PollRoundParams, RegisteredRepository
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildStatus,
)
from provisioning.core.application.shared.profile import ContractNotDecided
from provisioning.hosts.build_worker.wiring import (
    BuildWorkerProcess,
    NoPendingStarts,
    build_build_worker,
)
from provisioning.hosts.shared.open_contracts import ClosedRepositories
from provisioning.hosts.shared.stand_ins import Repositories
from provisioning.infrastructure.config.build_worker import load_build_worker

REPOSITORIES = Path("/project/bin/kind-state/proto/provisioning/repositories.json")
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")


class _Jenkins:
    async def start(self, request: BuildRequest) -> BuildHandle:
        raise AssertionError("not expected")

    async def find(self, request: BuildRequest) -> BuildHandle | None:
        raise AssertionError("not expected")

    async def status(self, handle: BuildHandle) -> BuildStatus:
        raise AssertionError("not expected")

    async def cancel(self, handle: BuildHandle) -> None:
        raise AssertionError("not expected")


class _Repositories:
    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        return None

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        return "http://git.test/x.git"


def _build(env: dict[str, str], repositories: Repositories) -> BuildWorkerProcess:
    config = load_build_worker(env.get)
    engine = create_async_engine("postgresql+psycopg://unused@127.0.0.1:1/unused")
    return build_build_worker(
        config,
        intake_engine=engine,
        build_engine=engine,
        build_system=_Jenkins(),
        repositories=repositories,
        audit=NullAuditLog(),
    )


def test_the_prototype_build_worker_runs_intake_poller_and_hints() -> None:
    process = _build(build_worker_env(REPOSITORIES), _Repositories())
    assert process.parts == ("events", "consumer", "processor", "poller", "hints")
    assert len(process.first) == 1  # the broker connection
    assert len(process.then) == len(process.parts)


def test_the_production_build_worker_runs_the_poller_alone() -> None:
    process = _build(production(build_worker_env(REPOSITORIES)), ClosedRepositories())
    assert process.parts == ("poller",)
    assert process.first == ()
    assert len(process.then) == 1


@pytest.mark.asyncio
async def test_production_starts_no_pending_build_and_the_repositories_are_closed() -> None:
    assert await NoPendingStarts().start_round(PollRoundParams(limit=10, concurrency=1)) == []
    closed = ClosedRepositories()
    with pytest.raises(ContractNotDecided) as registry:
        await closed.registered_repository(PROJECT)
    with pytest.raises(ContractNotDecided) as locator:
        await closed.repository_url(PROJECT, 4242)
    assert (registry.value.port, registry.value.contract) == ("repository registry", "OD-01")
    assert (locator.value.port, locator.value.contract) == ("repository locator", "OD-01")
