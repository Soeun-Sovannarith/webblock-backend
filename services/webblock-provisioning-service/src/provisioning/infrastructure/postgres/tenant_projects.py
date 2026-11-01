"""The bootstrap component's one read (task G; decision 0014 sections 3 and 10.1): the projects
that need a tenant. A project needs one while a release is on its way to a deploy (so the tenant
exists before the first deploy), and while it is hosted: it has a live release, or its hosting row
names one (QA-04, wave 6: a deleted namespace of a site that is only live is rebuilt by the
bootstrap loop's recheck without a new publish; the reconciler then re-applies the workload and
the route). Each project comes with its ``project_hosting.subdomain``, which the bootstrap writes
into the tenant namespace as a label for the route-shape admission policy (decision 0023 G18a),
and whether another project's hosting row holds the same sub-domain (FR-54, TR-53). The release
intake's login cannot read ``subdomain``, so this read is where a release row's taken sub-domain is
caught; the bootstrap then labels no namespace with it.
The read uses only the columns ``provisioning_bootstrap`` may select: ``releases (project_id,
status)`` and ``project_hosting (project_id, live_release_id)`` (migration 00015) and
``project_hosting (subdomain)`` (migration 00023). Every release has a hosting row (the release
insert guard checks it), so the read starts from ``project_hosting``.

It runs on the engine of a login that is a member of ``provisioning_bootstrap`` alone, in a
REPEATABLE READ, READ ONLY transaction, and writes nothing. Whether a tenant is ready is not
stored in the database (revision 19 has no such column); the bootstrap loop remembers the tenants
it found ready.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application.ports.site_cluster import TenantProject
from provisioning.core.domain import ReleaseStatus
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.schema import project_hosting, releases
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

MAX_PAGE: Final = 1000

# A release in one of these statuses may be deployed or promoted next, or is served, so its
# project needs a tenant. Superseded, failed and rolled-back releases alone need none.
NEEDS_TENANT: Final = (
    ReleaseStatus.ACCEPTED,
    ReleaseStatus.BUILDING,
    ReleaseStatus.BUILT,
    ReleaseStatus.CANDIDATE_READY,
    ReleaseStatus.PROMOTING,
    ReleaseStatus.LIVE,
)


def projects_needing_tenant_statement(after: UUID | None, limit: int) -> Select[Any]:
    """Hosting rows above ``after``, ascending by project ID, at most ``limit``, as (project ID,
    sub-domain, sub-domain taken): those that name a live release, or whose project has a release
    in ``NEEDS_TENANT``. Taken: another project's hosting row holds the sub-domain (FR-54)."""
    # The subquery names its one column: a bare EXISTS would read SELECT *, which needs every
    # column of releases, and the bootstrap may read only project_id and status.
    with_release = (
        select(releases.c.project_id)
        .where(
            releases.c.project_id == project_hosting.c.project_id,
            releases.c.status.in_(NEEDS_TENANT),
        )
        .exists()
    )
    other = project_hosting.alias("other_hosting")
    taken = (
        select(other.c.project_id)
        .where(
            other.c.subdomain == project_hosting.c.subdomain,
            other.c.project_id != project_hosting.c.project_id,
        )
        .exists()
        .label("subdomain_taken")
    )
    statement = select(project_hosting.c.project_id, project_hosting.c.subdomain, taken).where(
        or_(project_hosting.c.live_release_id.is_not(None), with_release)
    )
    if after is not None:
        statement = statement.where(project_hosting.c.project_id > after)
    return statement.order_by(project_hosting.c.project_id).limit(limit)


class PgTenantProjects:
    """Pages of the projects that need a tenant, with their sub-domains, ordered by ID."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]:
        """Up to ``limit`` (1 to MAX_PAGE) projects with an ID above ``after``, ascending, each
        with its sub-domain."""
        if not 1 <= limit <= MAX_PAGE:
            raise InvalidArgument(f"limit must be 1 to {MAX_PAGE}")
        statement = projects_needing_tenant_statement(after, limit)

        async def read(conn: AsyncConnection) -> list[TenantProject]:
            return [
                TenantProject(
                    project_id=row.project_id,
                    subdomain=row.subdomain,
                    subdomain_taken=row.subdomain_taken,
                )
                for row in await conn.execute(statement)
            ]

        return await self._tx.run("projects needing a tenant", read, read_only=True)

    async def has_live_release(self, project_id: UUID) -> bool:
        """Whether the project's hosting row names a live release (access_setup's rule, decision
        0011 section 5): False when it has none or no hosting row."""
        statement = select(project_hosting.c.live_release_id).where(
            project_hosting.c.project_id == project_id
        )

        async def read(conn: AsyncConnection) -> bool:
            live = (await conn.execute(statement)).scalar_one_or_none()
            return live is not None

        return await self._tx.run("project has a live release", read, read_only=True)
