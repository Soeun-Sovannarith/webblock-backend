"""The bootstrap component's read (task G; QA-04 in wave 6): the projects that need a tenant (a
release on its way to a deploy, a live release, or a hosting row naming a live release), each with
its sub-domain (migration 00023, decision 0023 G18a), read as a login of ``provisioning_bootstrap``
alone, in pages, without writing."""

from __future__ import annotations

from uuid import UUID

import pytest
from tests.integration._pg import ROLE_BOOTSTRAP, Database
from tests.integration.test_store_support import (
    HOSTING,
    RELEASES,
    TIMEOUTS,
    hosting_row,
    release_row,
    role_engine,
    seed_rows,
    subdomain_for,
)

from provisioning.core.application.ports.site_cluster import TenantProject
from provisioning.infrastructure.postgres import InvalidArgument
from provisioning.infrastructure.postgres.tenant_projects import PgTenantProjects


def _project(n: int) -> UUID:
    return UUID(f"0190a1b2-0000-7000-8000-{n:012x}")


def _tenant(n: int) -> TenantProject:
    return TenantProject(_project(n), subdomain_for(_project(n)))


@pytest.mark.asyncio
async def test_projects_with_a_release_on_its_way_or_live_are_listed_in_pages(
    db: Database,
) -> None:
    needs = ["accepted", "building", "built", "candidate_ready", "promoting", "live"]
    # Every release has its project's hosting row (the release insert guard checks it).
    rows = [(HOSTING, hosting_row(_project(n))) for n in (1, 2, 3, 4, 5, 6, 10, 11, 12)]
    rows += [(RELEASES, release_row(_project(i + 1), s, name=s)) for i, s in enumerate(needs)]
    # Two releases of one project count once; finished releases alone need no tenant.
    rows.append((RELEASES, release_row(_project(1), "built", name="second")))
    for i, s in enumerate(["superseded", "failed", "rolled_back"]):
        rows.append((RELEASES, release_row(_project(10 + i), s, name=s)))
    seed_rows(db, *rows)
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        store = PgTenantProjects(engine, TIMEOUTS)
        first = await store.projects_needing_tenant(after=None, limit=3)
        rest = await store.projects_needing_tenant(after=first[-1].project_id, limit=3)
        end = await store.projects_needing_tenant(after=rest[-1].project_id, limit=3)
    assert first == [_tenant(1), _tenant(2), _tenant(3)]
    assert rest == [_tenant(4), _tenant(5), _tenant(6)]
    assert end == []


@pytest.mark.asyncio
async def test_a_project_whose_hosting_row_names_a_live_release_is_listed_once(
    db: Database,
) -> None:
    """A hosted site is listed by its hosting row even when no release of it is on its way, so a
    deleted namespace of a site that is only live is rebuilt without a new publish (QA-04)."""
    hosted = release_row(_project(1), "live", name="hosted")
    # The hosting row alone (its release read as superseded here) still lists the project.
    stale = release_row(_project(2), "superseded", name="stale")
    seed_rows(
        db,
        (RELEASES, hosted),
        (HOSTING, hosting_row(_project(1), live=hosted["release_id"])),  # type: ignore[arg-type]
        (RELEASES, stale),
        (HOSTING, hosting_row(_project(2), live=stale["release_id"])),  # type: ignore[arg-type]
        (RELEASES, release_row(_project(3), "failed", name="f")),
        (HOSTING, hosting_row(_project(3))),
    )
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        store = PgTenantProjects(engine, TIMEOUTS)
        everything = await store.projects_needing_tenant(after=None, limit=10)
        after_first = await store.projects_needing_tenant(after=_project(1), limit=10)
    assert everything == [_tenant(1), _tenant(2)]
    assert after_first == [_tenant(2)]


@pytest.mark.asyncio
async def test_each_project_comes_with_its_own_subdomain(db: Database) -> None:
    """Decision 0023 G18a: the bootstrap login reads the sub-domain it labels the namespace with."""
    live = release_row(_project(1), "live", name="live")
    seed_rows(
        db,
        (RELEASES, live),
        (HOSTING, hosting_row(_project(1), live=live["release_id"], subdomain="my-shop")),  # type: ignore[arg-type]
        (RELEASES, release_row(_project(2), "accepted", name="a")),
        (HOSTING, hosting_row(_project(2), subdomain="other-shop")),
    )
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        found = await PgTenantProjects(engine, TIMEOUTS).projects_needing_tenant(
            after=None, limit=10
        )
    assert found == [
        TenantProject(_project(1), "my-shop"),
        TenantProject(_project(2), "other-shop"),
    ]


@pytest.mark.asyncio
async def test_fr54_projects_whose_subdomain_another_row_holds_are_marked_taken(
    db: Database,
) -> None:
    """FR-54.4 (TR-53): the release intake cannot read ``subdomain``, so a release row may hold a
    sub-domain another project's row holds. The bootstrap login reads it, and marks both rows
    taken, in the same statement; a project with its own sub-domain is not marked."""
    seed_rows(
        db,
        (RELEASES, release_row(_project(1), "accepted", name="a")),
        (HOSTING, hosting_row(_project(1), subdomain="victim-shop")),
        (RELEASES, release_row(_project(2), "accepted", name="b")),
        (HOSTING, hosting_row(_project(2), subdomain="victim-shop")),
        (RELEASES, release_row(_project(3), "accepted", name="c")),
        (HOSTING, hosting_row(_project(3), subdomain="own-shop")),
        # A row that needs no tenant still counts as a holder.
        (HOSTING, hosting_row(_project(4), subdomain="own-shop")),
    )
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        store = PgTenantProjects(engine, TIMEOUTS)
        found = await store.projects_needing_tenant(after=None, limit=10)
    assert found == [
        TenantProject(_project(1), "victim-shop", subdomain_taken=True),
        TenantProject(_project(2), "victim-shop", subdomain_taken=True),
        TenantProject(_project(3), "own-shop", subdomain_taken=True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 1001])
async def test_a_page_size_out_of_range_is_refused(db: Database, limit: int) -> None:
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        with pytest.raises(InvalidArgument):
            await PgTenantProjects(engine, TIMEOUTS).projects_needing_tenant(
                after=None, limit=limit
            )


@pytest.mark.asyncio
async def test_has_live_release_reads_the_hosting_row(db: Database) -> None:
    live = release_row(_project(1), "live", name="live")
    seed_rows(
        db,
        (RELEASES, live),
        (HOSTING, hosting_row(_project(1), live=live["release_id"])),  # type: ignore[arg-type]
        (RELEASES, release_row(_project(2), "built", name="b")),
        (HOSTING, hosting_row(_project(2))),
    )
    async with role_engine(db, ROLE_BOOTSTRAP) as engine:
        store = PgTenantProjects(engine, TIMEOUTS)
        assert await store.has_live_release(_project(1)) is True
        assert await store.has_live_release(_project(2)) is False
        assert await store.has_live_release(_project(3)) is False
