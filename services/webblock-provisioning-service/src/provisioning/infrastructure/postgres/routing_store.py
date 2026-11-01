"""The routing store: the fenced reads and writes of promote, rollback, suspend_hosting and
resume_hosting (prototype task F2; decision 0004 section 7, decision 0014 sections 8 and 13).

It implements ``core.application.routing.ports.RouteCommandStore`` on the engine of a login that
is a member of ``provisioning_worker`` alone, with SQLAlchemy Core (every value a bound parameter).
The columns it writes are those granted to the worker by 00003, 00004 and 00012:

- ``releases``: status, failure_code, failure_summary, updated_at, and promoted_at when a release
  goes live (00012);
- ``project_hosting``: live_release_id, routing_status, hosting_status (00012) and updated_at;
- ``commands``: status, failure_code, updated_at;
- ``provisioning_ops.command_dispatch``: finished_at and last_failure_code of a terminal command;
- ``provisioning_ops.usage_outbox`` (00016): the pending "publish completed" usage event of a
  release that goes live, inserted in the same transaction as ``confirm_live``, after the release
  row (decision 0014 section 21 answer 2). If the insert fails, the promotion is not committed
  either, and the use case sees the error.

Lock order (revision 19): project_hosting, command_dispatch, commands, releases. ``load_fenced``
takes the first four (the command's release included); the live release and the project's
promoting release are then locked at the releases level too, because an outcome may write them.
Writes follow the same order; the superseded release is written before the release that goes live
(``releases_one_live_per_project``); the usage event row comes last, as a new row it takes no lock
another transaction waits for. No step rows are written (decision 0014 section 8).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import CorruptRow, Fence
from provisioning.core.application.routing.ports import OutcomeDecider
from provisioning.core.domain import (
    CommandStatus,
    IllegalTransition,
    Outcome,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    StaleFence,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.fenced import (
    FencedRows,
    finish_dispatch,
    load_fenced,
    write_command,
)
from provisioning.infrastructure.postgres.rows import reasons_value, release_of
from provisioning.infrastructure.postgres.schema import project_hosting, releases
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions
from provisioning.infrastructure.postgres.usage_outbox import insert_usage_event


class PgRouteCommandStore:
    """``RouteCommandStore`` on PostgreSQL, as ``provisioning_worker``."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        """The three checks and the rows, under the same locks a write takes; writes nothing."""

        async def read(conn: AsyncConnection) -> OutcomeInput:
            return (await _load(conn, fence)).input

        return await self._tx.run("load route command", read)

    async def apply_route_outcome(self, fence: Fence, decide: OutcomeDecider) -> Outcome:
        """One transaction: the three checks, ``decide`` on the loaded rows, then exactly the
        changed rows and columns, in lock order."""

        async def write(conn: AsyncConnection) -> Outcome:
            loaded = await _load(conn, fence)
            out = decide(loaded.input)
            await _write(conn, fence, loaded, out)
            return out

        return await self._tx.run("apply route outcome", write)


class _Loaded:
    """The fenced rows and every release an outcome may write, by ID, as loaded."""

    __slots__ = ("input", "releases")

    def __init__(self, inp: OutcomeInput, loaded: dict[UUID, Release]) -> None:
        self.input = inp
        self.releases = loaded


async def _load(conn: AsyncConnection, fence: Fence) -> _Loaded:
    rows: FencedRows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
    loaded: dict[UUID, Release] = {}
    if rows.release is not None:
        loaded[rows.release.id] = rows.release  # locked by load_fenced
    live: Release | None = None
    live_id = rows.hosting.live_release_id
    if live_id is not None:
        held = loaded.get(live_id)
        if held is None:
            held = await _lock_release(conn, fence.project_id, live_id)
        loaded[held.id] = held
        live = held
    promoting = next((r for r in loaded.values() if r.status == ReleaseStatus.PROMOTING), None)
    if promoting is None:
        found = (
            (
                await conn.execute(
                    select(releases)
                    .where(
                        releases.c.project_id == fence.project_id,
                        releases.c.status == ReleaseStatus.PROMOTING.value,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if found is not None:
            promoting = release_of(found)
            loaded[promoting.id] = promoting
    inp = OutcomeInput(
        hosting=rows.hosting,
        command=rows.command,
        release=rows.release,
        live_release=live,
        promoting_release=promoting,
    )
    return _Loaded(inp, loaded)


async def _lock_release(conn: AsyncConnection, project_id: UUID, release_id: UUID) -> Release:
    found = (
        (await conn.execute(statements.release(project_id, release_id).with_for_update()))
        .mappings()
        .first()
    )
    if found is None:
        raise CorruptRow(f"postgres: release {release_id} of project {project_id} is missing")
    return release_of(found)


async def _write(conn: AsyncConnection, fence: Fence, loaded: _Loaded, out: Outcome) -> None:
    """In lock order: the hosting row, the dispatch row of a terminal command, the command, then
    the releases (the superseded one first), then the usage event of a release that went live.
    The hosting row's foreign key to the live release needs no order: the release exists either
    way."""
    before = loaded.input
    if out.hosting.project_id != fence.project_id or out.command.id != fence.command_id:
        raise CorruptRow("postgres: outcome of another project or command")
    if out.hosting != before.hosting:
        await _write_hosting(conn, fence, before.hosting, out.hosting)
    if out.command.status not in (CommandStatus.ACCEPTED, CommandStatus.RUNNING):
        await finish_dispatch(conn, fence, out.command.updated_at, out.command.failure_code)
    if out.command != before.command:
        await write_command(conn, before.command, out.command)
    for after in (out.superseded_release, out.release):
        if after is not None:
            await _write_changed_release(conn, loaded, after)
    went_live = out.release
    if went_live is not None and went_live.status is ReleaseStatus.LIVE:
        previous = loaded.releases[went_live.id]
        if previous.status is not ReleaseStatus.LIVE:
            await _insert_usage_event(conn, fence, went_live)


async def _write_changed_release(conn: AsyncConnection, loaded: _Loaded, after: Release) -> None:
    """Write ``after`` over the release ``_load`` read, when it changed; an outcome may write
    only a release it loaded."""
    old = loaded.releases.get(after.id)
    if old is None:
        raise CorruptRow(f"postgres: outcome writes release {after.id}, which was not loaded")
    if after != old:
        await _write_release(conn, old, after)


async def _insert_usage_event(conn: AsyncConnection, fence: Fence, release: Release) -> None:
    """The usage event of ``release``, which this transaction made live."""
    if release.promoted_at is None:
        raise CorruptRow(f"postgres: release {release.id} goes live without promoted_at")
    await insert_usage_event(
        conn, project_id=fence.project_id, release_id=release.id, completed_at=release.promoted_at
    )


async def _write_hosting(
    conn: AsyncConnection, fence: Fence, before: ProjectHosting, after: ProjectHosting
) -> None:
    if after.last_command_number != before.last_command_number:
        raise CorruptRow("postgres: an outcome never moves last_command_number")
    result = await conn.execute(
        update(project_hosting)
        .where(
            project_hosting.c.project_id == fence.project_id,
            project_hosting.c.last_command_number == fence.command_number,
        )
        .values(
            live_release_id=after.live_release_id,
            routing_status=after.routing_status.value,
            hosting_status=after.hosting_status.value,
            offline_reasons=reasons_value(after.offline_reasons),
            updated_at=after.updated_at,
        )
    )
    if result.rowcount != 1:
        raise StaleFence(f"project {fence.project_id} left command number {fence.command_number}")


async def _write_release(conn: AsyncConnection, before: Release, after: Release) -> None:
    """Status, failure and updated_at, and promoted_at only when it is set now (it is write-once,
    ``releases_identity_immutable``); conditional on the status loaded."""
    if after.container_image_digest != before.container_image_digest:
        raise CorruptRow(f"postgres: outcome changes the digest of release {after.id}")
    stmt = update(releases).where(
        releases.c.project_id == before.project_id,
        releases.c.release_id == before.id,
        releases.c.status == before.status.value,
    )
    if after.promoted_at != before.promoted_at:
        stmt = stmt.values(promoted_at=after.promoted_at)
    result = await conn.execute(
        stmt.values(
            status=after.status.value,
            failure_code=after.failure_code,
            failure_summary=after.failure_summary,
            updated_at=after.updated_at,
        )
    )
    if result.rowcount != 1:
        raise IllegalTransition(f"release {before.id} is no longer {before.status}")
