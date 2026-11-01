"""The reconciler's reads (decision 0014 section 21 ruling 9; task W6-B) on the engine of
``provisioning_worker``, which reads all four business tables (migration 00003).

It implements ``core.application.reconcile.ports.ReconcileStore`` with SQLAlchemy Core; every
value is a bound parameter. Each method is one short READ ONLY transaction that takes no lock: the
reconciler never writes SQL, and it re-reads a project (``still_idle``) right before each cluster
write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application.reconcile.ports import ProjectRecord
from provisioning.core.domain import (
    DomainError,
    ReleaseStatus,
    RoutingStatus,
    parse_release_status,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.rows import corrupt, hosting_of
from provisioning.infrastructure.postgres.schema import commands, project_hosting, releases
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

MAX_IDS: Final = 1000
"""The most IDs one ``IN`` list carries; longer lists are read in chunks."""


class PgReconcileStore:
    """``ReconcileStore`` on PostgreSQL, as ``provisioning_worker``."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def projects_after(self, after: UUID | None, limit: int) -> tuple[ProjectRecord, ...]:
        """Up to ``limit`` hosting rows above ``after`` in project ID order, each with its live
        release's digest and whether it has an accepted or running command."""
        if not 1 <= limit <= MAX_IDS:
            raise InvalidArgument(f"the batch must be 1 to {MAX_IDS}")
        h = project_hosting
        unfinished = (
            select(commands.c.command_id)
            .where(
                commands.c.project_id == h.c.project_id,
                commands.c.status.in_(statements.IN_FLIGHT),
            )
            .exists()
        )
        stmt = (
            select(h, releases.c.container_image_digest, unfinished.label("unfinished"))
            .select_from(
                h.outerjoin(
                    releases,
                    (releases.c.release_id == h.c.live_release_id)
                    & (releases.c.project_id == h.c.project_id),
                )
            )
            .order_by(h.c.project_id)
            .limit(limit)
        )
        if after is not None:
            stmt = stmt.where(h.c.project_id > after)

        async def read(conn: AsyncConnection) -> tuple[ProjectRecord, ...]:
            out: list[ProjectRecord] = []
            for row in (await conn.execute(stmt)).mappings():
                hosting = hosting_of(row)
                digest = row["container_image_digest"]
                if hosting.live_release_id is not None and digest is None:
                    raise corrupt(
                        f"hosting row of project {hosting.project_id}",
                        ValueError("the live release has no image digest"),
                    )
                out.append(
                    ProjectRecord(
                        project_id=hosting.project_id,
                        last_command_number=hosting.last_command_number,
                        routing_status=hosting.routing_status,
                        hosting_status=hosting.hosting_status,
                        live_release_id=hosting.live_release_id,
                        live_release_digest=digest if hosting.live_release_id else None,
                        unfinished_command=bool(row["unfinished"]),
                        subdomain=hosting.subdomain,
                    )
                )
            return tuple(out)

        return await self._tx.run("reconcile: read projects", read, read_only=True)

    async def known_projects(self, project_ids: Sequence[UUID]) -> frozenset[UUID]:
        """The IDs among ``project_ids`` with a hosting row, read in chunks of ``MAX_IDS``."""
        ids = list(dict.fromkeys(project_ids))
        if not ids:
            return frozenset()

        async def read(conn: AsyncConnection) -> frozenset[UUID]:
            found: set[UUID] = set()
            for start in range(0, len(ids), MAX_IDS):
                chunk = ids[start : start + MAX_IDS]
                rows = await conn.execute(
                    select(project_hosting.c.project_id).where(
                        project_hosting.c.project_id.in_(chunk)
                    )
                )
                found.update(rows.scalars())
            return frozenset(found)

        return await self._tx.run("reconcile: known projects", read, read_only=True)

    async def release_statuses(
        self, project_id: UUID, release_ids: Sequence[UUID]
    ) -> Mapping[UUID, ReleaseStatus]:
        """The status of each of the project's releases among ``release_ids``."""
        ids = list(dict.fromkeys(release_ids))
        if not ids:
            return {}
        if len(ids) > MAX_IDS:
            raise InvalidArgument(f"at most {MAX_IDS} releases at once")

        async def read(conn: AsyncConnection) -> Mapping[UUID, ReleaseStatus]:
            rows = await conn.execute(
                select(releases.c.release_id, releases.c.status).where(
                    releases.c.project_id == project_id, releases.c.release_id.in_(ids)
                )
            )
            out: dict[UUID, ReleaseStatus] = {}
            for release_id, status in rows:
                try:
                    out[release_id] = parse_release_status(status)
                except DomainError as exc:
                    raise corrupt(f"release {release_id}", exc) from None
            return out

        return await self._tx.run("reconcile: release statuses", read, read_only=True)

    async def still_idle(self, project_id: UUID, last_command_number: int) -> bool:
        """Stable, still at ``last_command_number``, and no accepted or running command."""
        h = project_hosting
        unfinished = (
            select(commands.c.command_id)
            .where(
                commands.c.project_id == project_id,
                commands.c.status.in_(statements.IN_FLIGHT),
            )
            .exists()
        )
        stmt = select(h.c.routing_status, h.c.last_command_number, unfinished).where(
            h.c.project_id == project_id
        )

        async def read(conn: AsyncConnection) -> bool:
            row = (await conn.execute(stmt)).first()
            if row is None:
                return False
            routing, number, busy = row
            return (
                str(routing) == RoutingStatus.STABLE.value
                and number == last_command_number
                and not busy
            )

        return await self._tx.run("reconcile: still idle", read, read_only=True)
