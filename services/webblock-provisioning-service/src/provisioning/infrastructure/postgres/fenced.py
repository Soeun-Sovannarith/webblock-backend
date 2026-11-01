"""The three checks of every fenced worker write (decision 0004 section 7), public within the
postgres package so that the deploy and routing stores (F1, F2) reuse them.

Lock order: project_hosting, provisioning_ops.command_dispatch, commands, releases,
release_steps. A transaction may skip a level and never goes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from provisioning.core.application import ClaimLost, CorruptRow, Fence
from provisioning.core.domain import (
    Command,
    CommandStatus,
    IllegalTransition,
    ProjectHosting,
    ProjectMismatch,
    Release,
    StaleFence,
    check_fence,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.rows import Row, command_of, hosting_of, release_of
from provisioning.infrastructure.postgres.schema import command_dispatch, commands, project_hosting


@dataclass(frozen=True, slots=True)
class DispatchRow:
    """The locked dispatch row's columns a decision needs, and the database clock read with it."""

    command_id: UUID
    deadline_at: datetime
    claim_number: int
    claimed_by_worker: str | None
    claim_expires_at: datetime | None
    retry_after: datetime | None
    last_failure_code: str | None
    finished_at: datetime | None
    db_now: datetime


@dataclass(frozen=True, slots=True)
class FencedRows:
    """The rows a fenced write loaded under its locks, validated."""

    hosting: ProjectHosting
    dispatch: DispatchRow
    command: Command
    release: Release | None = None
    live_release: Release | None = None


async def lock_dispatch(conn: AsyncConnection, command_id: UUID) -> DispatchRow | None:
    """Level 2: the dispatch row FOR UPDATE, with the database clock read in the same statement."""
    stmt = (
        select(command_dispatch, statements.clock().label("db_now"))
        .where(command_dispatch.c.command_id == command_id)
        .with_for_update(of=command_dispatch)
    )
    row = (await conn.execute(stmt)).mappings().first()
    return None if row is None else _dispatch_of(row)


async def lock_command(conn: AsyncConnection, command_id: UUID) -> Row | None:
    """Level 3: the command row FOR UPDATE, unvalidated (the claim maps a defect to CORRUPT)."""
    stmt = select(commands).where(commands.c.command_id == command_id).with_for_update()
    return (await conn.execute(stmt)).mappings().first()


async def load_fenced(
    conn: AsyncConnection, fence: Fence, want: CommandStatus, *, with_release: bool
) -> FencedRows:
    """The three checks in lock order, inside the caller's transaction:

    1. the hosting row FOR UPDATE where last_command_number equals the fence (else
       ``StaleFence``);
    2. the dispatch row FOR UPDATE with the fence's claim_number (else ``ClaimLost``);
    3. the command FOR UPDATE, of the fence's project and number, in status ``want`` (else
       ``IllegalTransition``).

    With ``with_release`` it then locks the command's release (level 4) and reads the live release
    without a lock. Every row is validated on load."""
    fence.validate()
    locked = (
        (
            await conn.execute(
                select(project_hosting)
                .where(
                    project_hosting.c.project_id == fence.project_id,
                    project_hosting.c.last_command_number == fence.command_number,
                )
                .with_for_update()
            )
        )
        .mappings()
        .first()
    )
    if locked is None:
        raise StaleFence(
            f"project {fence.project_id} is not at command number {fence.command_number}"
        )
    hosting = hosting_of(locked)
    dispatch = await lock_dispatch(conn, fence.command_id)
    if dispatch is None:
        raise ClaimLost(f"no dispatch row for command {fence.command_id}")
    if dispatch.claim_number != fence.claim_number:
        raise ClaimLost(
            f"command {fence.command_id} holds claim {dispatch.claim_number}, "
            f"not {fence.claim_number}"
        )
    row = await lock_command(conn, fence.command_id)
    if row is None:  # the dispatch row's foreign key makes this impossible
        raise CorruptRow(f"postgres: dispatch row of command {fence.command_id} has no command")
    command = command_of(row)
    if command.project_id != fence.project_id:
        raise ProjectMismatch(f"command {fence.command_id}")
    check_fence(command.command_number, hosting.last_command_number)
    if command.status != want:
        raise IllegalTransition(f"command {fence.command_id} is {command.status}, not {want}")
    if not with_release:
        return FencedRows(hosting=hosting, dispatch=dispatch, command=command)
    release: Release | None = None
    if command.release_id is not None:
        found = (
            (
                await conn.execute(
                    statements.release(fence.project_id, command.release_id).with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if found is None:
            raise CorruptRow(f"postgres: release of command {fence.command_id} is missing")
        release = release_of(found)
    live: Release | None = None
    if hosting.live_release_id is not None:
        if release is not None and release.id == hosting.live_release_id:
            live = release
        else:
            found = (
                (await conn.execute(statements.release(fence.project_id, hosting.live_release_id)))
                .mappings()
                .first()
            )
            if found is None:
                raise CorruptRow(f"postgres: live release of project {fence.project_id} missing")
            live = release_of(found)
    return FencedRows(
        hosting=hosting, dispatch=dispatch, command=command, release=release, live_release=live
    )


async def write_command(conn: AsyncConnection, before: Command, after: Command) -> None:
    """The command's new status, failure code and updated_at, conditional on the status it was
    loaded in; no row is ``IllegalTransition``."""
    result = await conn.execute(statements.update_command_status(before, after))
    if result.rowcount != 1:
        raise IllegalTransition(f"command {before.id} is no longer {before.status}")


async def write_release(conn: AsyncConnection, before: Release, after: Release) -> None:
    """The release's new status and failure, conditional on the status it was loaded in."""
    result = await conn.execute(statements.update_release_status(before, after))
    if result.rowcount != 1:
        raise IllegalTransition(f"release {before.id} is no longer {before.status}")


async def finish_dispatch(
    conn: AsyncConnection, fence: Fence, at: datetime, failure_code: str | None
) -> None:
    """Level 2 write of the transaction that gives a command its terminal status: finished_at,
    and the failure code as last_failure_code when there is one; conditional on the claim the
    fence checked."""
    result = await conn.execute(
        update(command_dispatch)
        .where(
            command_dispatch.c.command_id == fence.command_id,
            command_dispatch.c.claim_number == fence.claim_number,
            command_dispatch.c.finished_at.is_(None),
        )
        .values(
            finished_at=at,
            last_failure_code=func.coalesce(failure_code, command_dispatch.c.last_failure_code),
            updated_at=statements.clock(),
        )
    )
    if result.rowcount != 1:
        raise IllegalTransition(f"the dispatch row of command {fence.command_id} is finished")


def _dispatch_of(row: Row) -> DispatchRow:
    def utc(value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    deadline = utc(row["deadline_at"])
    now = utc(row["db_now"])
    if deadline is None or now is None:
        raise CorruptRow("postgres: dispatch row without a deadline or clock")
    return DispatchRow(
        command_id=row["command_id"],
        deadline_at=deadline,
        claim_number=row["claim_number"],
        claimed_by_worker=row["claimed_by_worker"],
        claim_expires_at=utc(row["claim_expires_at"]),
        retry_after=utc(row["retry_after"]),
        last_failure_code=row["last_failure_code"],
        finished_at=utc(row["finished_at"]),
        db_now=now,
    )
