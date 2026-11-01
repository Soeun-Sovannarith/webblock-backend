"""FR-54 (TR-53): the bootstrap makes no tenant for a project whose sub-domain another project's
hosting row also holds. The release intake's login cannot read ``subdomain`` (migration 00022), so
the bootstrap, which may read it (migration 00023), is where a release row's taken sub-domain is
caught: no namespace is labelled with it, so the route-shape admission policy admits no route for
it. The refusal is logged at error with the project ID only, never the sub-domain."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop

TAKEN = UUID("0190a1b2-0000-7000-8000-000000000001")
OWN = UUID("0190a1b2-0000-7000-8000-000000000002")


class Projects:
    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        if after is not None:
            return []
        return [
            TenantProject(TAKEN, "victim-shop", subdomain_taken=True),
            TenantProject(OWN, "own-shop"),
        ]


class Cluster:
    def __init__(self) -> None:
        self.ensured: list[UUID] = []

    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        self.ensured.append(project_id)
        return TenantState.READY


def _loop(cluster: Cluster) -> TenantLoop:
    return TenantLoop(
        LoopSettings(
            interval=timedelta(seconds=5),
            batch_size=10,
            concurrency=2,
            call_timeout=timedelta(seconds=5),
        ),
        projects=Projects(),
        bootstrap=cluster,
        logger=logging.getLogger("test.bootstrap"),
    )


@pytest.mark.asyncio
async def test_fr54_a_taken_subdomain_gets_no_tenant_and_is_logged_at_error_without_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-54.4: no tenant for the project whose sub-domain another row holds; the other project of
    the page is ensured as usual; one error line with the project ID and no sub-domain."""
    cluster = Cluster()
    with caplog.at_level(logging.INFO, logger="test.bootstrap"):
        result = await _loop(cluster).round()
    assert cluster.ensured == [OWN]
    assert result.ready == (OWN,)
    assert result.pending == (TAKEN,)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    line = errors[0].getMessage()
    assert str(TAKEN) in line
    assert "victim-shop" not in line


@pytest.mark.asyncio
async def test_fr54_a_taken_subdomain_is_never_remembered_as_ready() -> None:
    """FR-54.4: it is checked again every round, so it stays refused until an operator fixes it."""
    cluster = Cluster()
    loop = _loop(cluster)
    await loop.round()
    await loop.round()
    assert not loop.is_known_ready(TAKEN)
    assert TAKEN not in cluster.ensured
