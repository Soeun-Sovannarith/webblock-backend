"""The API's build_release store (decision 0016 section 5 item 2, task H2), apart from the build
store: the API process links this module and never ``build_store`` (finding F-6 of H-V; the
import-linter contract ``api-no-build-system``). ``load_build_target`` is shared with the build
store, which imports it from here.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import BuildTarget, NotFound
from provisioning.core.application.builds.background.start_build import (
    BUILD_WANTED_CHANNEL,
    build_hint,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.rows import release_of, step_of
from provisioning.infrastructure.postgres.schema import release_steps
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_S = release_steps


async def load_build_target(tx: Transactions, project_id: UUID, release_id: UUID) -> BuildTarget:
    """The release and its build step with the highest attempt, read only; reads nothing but
    ``releases`` and ``release_steps``, which ``provisioning_api`` and ``provisioning_build`` may
    both read."""

    async def load(conn: AsyncConnection) -> BuildTarget:
        row = (await conn.execute(statements.release(project_id, release_id))).mappings().first()
        if row is None:
            raise NotFound(f"release {release_id} of project {project_id}")
        release = release_of(row)
        step_row = (
            (
                await conn.execute(
                    select(_S)
                    .where(_S.c.release_id == release_id, _S.c.step_name == "build")
                    .order_by(_S.c.attempt.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        return BuildTarget(release=release, step=None if step_row is None else step_of(step_row))

    return await tx.run("load build target", load, read_only=True)


class PgBuildRequests:
    """The API's build_release store (``BuildRequestStore``; decision 0016 section 5 item 2, task
    H2) on the engine of ``provisioning_api``: it reads the build target and sends the build hint
    (``pg_notify``, which needs no grant), and writes no row."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        return await load_build_target(self._tx, project_id, release_id)

    async def build_wanted(self, project_id: UUID, release_id: UUID) -> None:
        payload = build_hint(project_id, release_id)

        async def notify(conn: AsyncConnection) -> None:
            # Delivered at commit; NOTIFY is refused in a read-only transaction.
            await conn.execute(select(func.pg_notify(BUILD_WANTED_CHANNEL, payload)))

        await self._tx.run("send build hint", notify)
