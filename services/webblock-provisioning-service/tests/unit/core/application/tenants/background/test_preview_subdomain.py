"""Owner ruling PREVIEW1: the bootstrap makes no tenant for a project whose sub-domain ends in
``-preview`` (``shop-preview`` is ``shop``'s preview host) or has 56 to 63 characters (its preview
host would not be one DNS label). Without a tenant its namespace is never labelled with that
sub-domain, so the route-shape admission policy admits no route for it. The refusal is logged at
error with the project ID only, never the sub-domain, every round, and the rows are kept, as for a
taken sub-domain; the database keeps its own check (Notion "5 · Provisioning")."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest

from provisioning.core.application.ports.site_cluster import TenantProject, TenantState
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop

SHOP = UUID("0190a1b2-0000-7000-8000-000000000001")
SHOP_PREVIEW = UUID("0190a1b2-0000-7000-8000-000000000002")
PREVIEW = UUID("0190a1b2-0000-7000-8000-000000000003")
PREVIEW_SHOP = UUID("0190a1b2-0000-7000-8000-000000000004")
SHOP_PREVIEWS = UUID("0190a1b2-0000-7000-8000-000000000005")
LONG = UUID("0190a1b2-0000-7000-8000-000000000006")


class Projects:
    """The rows of one round, never changed by the loop (it has no write)."""

    def __init__(self, rows: list[TenantProject]) -> None:
        self.rows = tuple(rows)

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        if after is not None:
            return []
        return list(self.rows)


class Cluster:
    def __init__(self) -> None:
        self.ensured: dict[UUID, str] = {}

    async def ensure_tenant(
        self,
        project_id: UUID,
        *,
        subdomain: str,
        on_created: Callable[[UUID], None] | None = None,
    ) -> TenantState:
        self.ensured[project_id] = subdomain
        return TenantState.READY


def _loop(cluster: Cluster, projects: Projects) -> TenantLoop:
    return TenantLoop(
        LoopSettings(
            interval=timedelta(seconds=5),
            batch_size=10,
            concurrency=2,
            call_timeout=timedelta(seconds=5),
        ),
        projects=projects,
        bootstrap=cluster,
        logger=logging.getLogger("test.bootstrap.preview"),
    )


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_fr55_1_preview_alone_or_not_at_the_end_gets_its_tenant(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-55.1: ``preview``, ``preview-shop`` and ``shop-previews`` each get a tenant."""
    cluster = Cluster()
    rows = [
        TenantProject(PREVIEW, "preview"),
        TenantProject(PREVIEW_SHOP, "preview-shop"),
        TenantProject(SHOP_PREVIEWS, "shop-previews"),
    ]
    with caplog.at_level(logging.INFO, logger="test.bootstrap.preview"):
        result = await _loop(cluster, Projects(rows)).round()
    assert cluster.ensured == {
        PREVIEW: "preview",
        PREVIEW_SHOP: "preview-shop",
        SHOP_PREVIEWS: "shop-previews",
    }
    assert set(result.ready) == {PREVIEW, PREVIEW_SHOP, SHOP_PREVIEWS}
    assert result.pending == ()
    assert _errors(caplog) == []


@pytest.mark.parametrize("with_shop", [True, False], ids=["shop-there", "shop-absent"])
@pytest.mark.asyncio
async def test_fr55_2_a_dash_preview_subdomain_gets_no_tenant_and_one_error_without_it(
    caplog: pytest.LogCaptureFixture, *, with_shop: bool
) -> None:
    """FR-55.2: no tenant for ``shop-preview``, whether ``shop`` is hosted or not; one error line
    with its project ID and no sub-domain; the rows are unchanged; the round's other project gets
    its tenant."""
    rows = [TenantProject(SHOP_PREVIEW, "shop-preview"), TenantProject(PREVIEW, "preview")]
    if with_shop:
        rows.append(TenantProject(SHOP, "shop"))
    projects = Projects(rows)
    before = projects.rows
    cluster = Cluster()
    with caplog.at_level(logging.INFO, logger="test.bootstrap.preview"):
        result = await _loop(cluster, projects).round()
    assert SHOP_PREVIEW not in cluster.ensured
    assert SHOP_PREVIEW in result.pending
    assert set(result.ready) == ({PREVIEW, SHOP} if with_shop else {PREVIEW})
    assert projects.rows == before
    (line,) = _errors(caplog)
    assert str(SHOP_PREVIEW) in line
    assert "reason=preview_suffix" in line
    assert "shop-preview" not in line
    assert not any("shop-preview" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_fr55_3_a_refused_subdomain_is_never_remembered_as_ready(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-55.3: the next round refuses it again, with its error line again."""
    cluster = Cluster()
    loop = _loop(cluster, Projects([TenantProject(SHOP_PREVIEW, "shop-preview")]))
    with caplog.at_level(logging.INFO, logger="test.bootstrap.preview"):
        await loop.round()
        await loop.round()
    assert not loop.is_known_ready(SHOP_PREVIEW)
    assert cluster.ensured == {}
    assert len(_errors(caplog)) == 2


@pytest.mark.parametrize("length", [56, 60, 63])
@pytest.mark.asyncio
async def test_fr55_4_a_subdomain_of_56_to_63_characters_is_refused_and_55_is_not(
    caplog: pytest.LogCaptureFixture, length: int
) -> None:
    """FR-55.4: refused as in FR-55.2; a 55-character sub-domain gets its tenant."""
    too_long = "a" * length
    longest = "b" * 55
    cluster = Cluster()
    rows = [TenantProject(LONG, too_long), TenantProject(SHOP, longest)]
    with caplog.at_level(logging.INFO, logger="test.bootstrap.preview"):
        result = await _loop(cluster, Projects(rows)).round()
    assert cluster.ensured == {SHOP: longest}
    assert result.ready == (SHOP,)
    assert result.pending == (LONG,)
    (line,) = _errors(caplog)
    assert str(LONG) in line
    assert "reason=subdomain_too_long" in line
    assert too_long not in line
