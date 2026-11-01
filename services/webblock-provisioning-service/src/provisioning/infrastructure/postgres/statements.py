"""SQLAlchemy Core statements shared by several stores (decision 0014 section 11).

Every value is a bound parameter; status and kind lists that equal a partial index predicate are
written as constants of the statement. Revision 19 names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, Update, func, select, update
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncConnection

from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    Release,
)
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.schema import (
    commands,
    project_hosting,
    release_steps,
    releases,
)

CLIENT_KINDS: tuple[str, ...] = ("deploy", "promote", "rollback", "redeploy")
HOSTING_KINDS: tuple[str, ...] = ("suspend_hosting", "resume_hosting")
IN_FLIGHT: tuple[str, ...] = ("accepted", "running")


def clock() -> ColumnElement[datetime]:
    """The database clock: ``clock_timestamp()`` (decision 0008 section 6)."""
    return func.clock_timestamp(type_=TIMESTAMP(timezone=True))


async def read_clock(conn: AsyncConnection) -> datetime:
    """The transaction's one reading of the database clock (decision 0008 section 6), in UTC.
    Every time the transaction writes or compares is derived from it."""
    value = (await conn.execute(select(clock()))).scalar_one()
    if not isinstance(value, datetime):
        raise TypeError("clock_timestamp() returned no datetime")
    return value.astimezone(UTC)


def command_by_key(request: CommandRequest) -> Select[tuple[object, ...]]:
    """The command holding the request's key in its key namespace (client or hosting kinds, two
    of the partial unique indexes of ``commands``). ``sync_domains`` has no key class (migration
    ``00025`` drops its index) and Provisioning accepts no such command since custom domains were
    removed (decision 0014 section 23): a request of that kind is ``InvalidArgument``, before any
    SQL. A redeploy shares the client key class and an admin hosting command (key ``admin:<key>``)
    the hosting one (decision 0023 G9, G11); the hosting intake refuses a Payment key that starts
    with ``admin:`` itself (``hosting_intake``)."""
    kind = CommandKind(request.kind)
    if kind is CommandKind.SYNC_DOMAINS:
        raise InvalidArgument("sync_domains commands are not accepted (custom domains removed)")
    if not kind.is_hosting():
        return client_command_by_key(request.project_id, request.idempotency_key)
    return select(commands).where(
        commands.c.project_id == request.project_id,
        commands.c.idempotency_key == request.idempotency_key,
        commands.c.kind.in_(HOSTING_KINDS),
    )


def client_command_by_key(project_id: UUID, key: str) -> Select[tuple[object, ...]]:
    """The command holding ``key`` in the client key class (deploy, promote, rollback and
    redeploy; ``commands_idempotency_key``), whatever release it names."""
    return select(commands).where(
        commands.c.project_id == project_id,
        commands.c.idempotency_key == key,
        commands.c.kind.in_(CLIENT_KINDS),
    )


def command_in_flight(project_id: UUID) -> Select[tuple[object, ...]]:
    return select(commands).where(
        commands.c.project_id == project_id, commands.c.status.in_(IN_FLIGHT)
    )


def command(project_id: UUID, command_id: UUID) -> Select[tuple[object, ...]]:
    return select(commands).where(
        commands.c.project_id == project_id, commands.c.command_id == command_id
    )


def release(project_id: UUID, release_id: UUID) -> Select[tuple[object, ...]]:
    return select(releases).where(
        releases.c.project_id == project_id, releases.c.release_id == release_id
    )


def hosting(project_id: UUID) -> Select[tuple[object, ...]]:
    return select(project_hosting).where(project_hosting.c.project_id == project_id)


def live_release(project_id: UUID) -> Select[tuple[object, ...]]:
    """The release the hosting row names as live, read without a lock."""
    return (
        select(releases)
        .join(
            project_hosting,
            (releases.c.project_id == project_hosting.c.project_id)
            & (releases.c.release_id == project_hosting.c.live_release_id),
        )
        .where(project_hosting.c.project_id == project_id)
    )


def release_steps_of(project_id: UUID, release_id: UUID, limit: int) -> Select[tuple[object, ...]]:
    """The release's steps by step name (enum order) then attempt, scoped to the project."""
    return (
        select(release_steps)
        .join(releases, releases.c.release_id == release_steps.c.release_id)
        .where(releases.c.project_id == project_id, release_steps.c.release_id == release_id)
        .order_by(release_steps.c.step_name, release_steps.c.attempt)
        .limit(limit)
    )


def update_command_status(before: Command, after: Command) -> Update:
    """The domain's new status, conditional on the status the command was loaded in."""
    return (
        update(commands)
        .where(
            commands.c.command_id == before.id,
            commands.c.project_id == before.project_id,
            commands.c.status == str(before.status),
        )
        .values(
            status=str(after.status), failure_code=after.failure_code, updated_at=after.updated_at
        )
    )


def update_release_status(before: Release, after: Release) -> Update:
    """The domain's new status and failure, conditional on the status loaded."""
    return (
        update(releases)
        .where(
            releases.c.project_id == before.project_id,
            releases.c.release_id == before.id,
            releases.c.status == str(before.status),
        )
        .values(
            status=str(after.status),
            failure_code=after.failure_code,
            failure_summary=after.failure_summary,
            updated_at=after.updated_at,
        )
    )
