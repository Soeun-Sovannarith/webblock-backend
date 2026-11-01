"""The hosting-command acceptance store (decision 0014 sections 9 and 10.5), which task F2 calls.

B1's ports have no hosting-intake port of their own: ``AcceptanceStore`` already accepts the
hosting kinds. This store is that acceptance on the engine of a login that is a member of
``provisioning_hosting_intake`` alone, restricted to suspend_hosting and resume_hosting, plus the
smallest entry point F2 needs: :meth:`HostingIntakeStore.accept_hosting_command`, keyed by the
Payment event key. The same key with the same kind returns the stored command (a replay, whatever
its status); the same key with the other kind is ``IdempotencyConflict``; a command in flight for
the project is ``OperationInProgress``; a route that is not stable is ``RouteNotStable``; a
project without a hosting row is ``NotFound``.

:meth:`HostingIntakeStore.accept_owner_status` (decision 0021) is the owner-status intake's
store: keyed ``owner-status:<version>``, it inserts the project's hosting row when there is none,
ignores a version that is not newer than the row's whatever its status (decision 0023 G17), and
otherwise records the version and accepts the command in one transaction
(``core.application.hosting.commands.accept_owner_status``).

The database guard admits a hosting command only from this role, except an admin's (reason
``admin_suspended``, key ``admin:<key>``), which only the API's role inserts (migration 00020,
decision 0023 G11). It does not stop this role from inserting a release command (see B3's report),
so this store refuses every other kind, and a key that starts with ``admin:``, before any SQL.

``PgHostingHistory`` (wave 6, task W6-E) is the read side on the same login: the latest hosting
intent of each offline reason of a project and the projects whose failed suspend or resume should
be retried (QA-03, per reason since decision 0021), and whether a project was deleted (decision
0014 section 21 answer 4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from sqlalchemy import (
    Integer,
    Text,
    and_,
    any_,
    cast,
    exists,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.expression import ColumnElement, Subquery

from provisioning.core.application import (
    AcceptanceDecider,
    AcceptCommandParams,
    AcceptedCommand,
    new_acceptance_decider,
)
from provisioning.core.application.hosting.background.hosting_job import DELETION_EVENT_KEY
from provisioning.core.application.hosting.commands.accept_owner_status import owner_status_kind
from provisioning.core.application.hosting.ports import (
    HostingIntent,
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
)
from provisioning.core.domain import (
    ADMIN_KEY_PREFIX,
    MAX_IDEMPOTENCY_KEY_LEN,
    OWNER_STATUS_KEY_PREFIX,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    InvalidCommandTarget,
    OfflineReason,
    ProjectHosting,
    RoutingStatus,
    new_owner_status_hosting,
    owner_status_key,
    parse_command_kind,
    root_key,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.acceptance_store import PgAcceptanceStore, insert_dispatch
from provisioning.infrastructure.postgres.errors import DatabaseError, InvalidArgument
from provisioning.infrastructure.postgres.hosting_insert import insert_hosting_row
from provisioning.infrastructure.postgres.rows import (
    command_of,
    hosting_intent_of,
    hosting_of,
    reasons_value,
)
from provisioning.infrastructure.postgres.schema import commands, project_hosting
from provisioning.infrastructure.postgres.statements import HOSTING_KINDS, IN_FLIGHT
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

MAX_RETRY_SWEEP: Final = 1000


_C = commands
_H = project_hosting


class HostingIntakeStore:
    """``AcceptanceStore`` for the hosting kinds only, on the hosting-intake role's engine."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._store = PgAcceptanceStore(engine, timeouts, insert_dispatch)

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        _require_hosting(request.kind)
        _require_event_key(request.idempotency_key)
        return await self._store.find_command_by_key(request)

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        _require_hosting(params.request.kind)
        _require_event_key(params.request.idempotency_key)
        return await self._store.accept_command(params, decide)

    async def accept_hosting_command(
        self,
        *,
        project_id: UUID,
        kind: CommandKind,
        event_key: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> AcceptedCommand:
        """Accept a suspend or resume command whose idempotency key is the Payment event key:
        the unlocked lookup first (a replay or a conflict writes nothing), then the acceptance
        transaction with the application's decide function."""
        _require_hosting(kind)
        _require_event_key(event_key)
        request = CommandRequest(
            project_id=project_id, kind=kind, release_id=None, idempotency_key=event_key
        )
        stored = await self._store.find_command_by_key(request)
        if stored is not None:
            stored.check_replay(request)
            return AcceptedCommand(command=stored, replayed=True)
        params = AcceptCommandParams(
            request=request,
            command_id=command_id,
            requested_by_subject="",
            now=now,
            correlation_id=correlation_id,
            deadline_at=deadline_at,
        )
        return await self._store.accept_command(params, new_acceptance_decider(params))

    async def accept_owner_status(
        self,
        *,
        project_id: UUID,
        status: OwnerStatus,
        version: int,
        subdomain: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> OwnerStatusResult:
        """``OwnerStatusStore.accept_owner_status`` (decision 0021): one transaction that locks
        the hosting row first. No row: insert the owner-status row with the event's sub-domain
        (a concurrent insert of the same project wins, and the event goes on with that row; a
        row of another project with that sub-domain is ``SubdomainTaken``, FR-54). A version not
        newer than the row's changes nothing, whatever its status (decision 0023 G17): a replay when
        the key ``owner-status:<version>`` holds this event's command, else stale; the key is
        read only after the version check, so a key held by the other kind is stale, never a
        conflict. Otherwise the row takes the version and the command is accepted with the
        application's decide function; a refusal rolls both back."""
        kind = owner_status_kind(status)
        request = CommandRequest(
            project_id=project_id,
            kind=kind,
            release_id=None,
            idempotency_key=owner_status_key(version),
        )
        params = AcceptCommandParams(
            request=request,
            command_id=command_id,
            requested_by_subject="",
            now=now,
            correlation_id=correlation_id,
            deadline_at=deadline_at,
        )
        suspended = status is OwnerStatus.SUSPENDED

        async def first_step(conn: AsyncConnection) -> OwnerStatusResult | None:
            locked = await _lock_hosting(conn, project_id)
            if locked is None:
                if await _insert_owner_status_row(
                    conn,
                    new_owner_status_hosting(
                        project_id, now, suspended=suspended, version=version, subdomain=subdomain
                    ),
                ):
                    return OwnerStatusResult(OwnerStatusOutcome.ROW_INSERTED, None)
                locked = await _lock_hosting(conn, project_id)
                if locked is None:
                    raise DatabaseError("accept owner status", reason="the hosting row vanished")
            if version <= locked.owner_status_version:
                row = (await conn.execute(statements.command_by_key(request))).mappings().first()
                held = None if row is None else command_of(row)
                if held is None or held.kind is not kind:
                    return OwnerStatusResult(OwnerStatusOutcome.STALE, None)
                held.check_replay(request)  # the same event again
                return OwnerStatusResult(OwnerStatusOutcome.REPLAYED, AcceptedCommand(held, True))
            recorded = await conn.execute(
                update(_H)
                .where(
                    _H.c.project_id == project_id,
                    _H.c.owner_status_version == locked.owner_status_version,
                )
                .values(owner_status_version=version)
            )
            if recorded.rowcount != 1:
                raise DatabaseError("accept owner status", reason="the version update missed")
            return None

        answer = await self._store.accept_after(params, new_acceptance_decider(params), first_step)
        if isinstance(answer, OwnerStatusResult):
            return answer
        outcome = OwnerStatusOutcome.REPLAYED if answer.replayed else OwnerStatusOutcome.ACCEPTED
        return OwnerStatusResult(outcome, answer)


async def _lock_hosting(conn: AsyncConnection, project_id: UUID) -> ProjectHosting | None:
    row = (await conn.execute(statements.hosting(project_id).with_for_update())).mappings().first()
    return None if row is None else hosting_of(row)


async def _insert_owner_status_row(conn: AsyncConnection, hosting: ProjectHosting) -> bool:
    """Insert the hosting row an owner-status event makes; False when the project has a row
    already, also when a concurrent first insert of the same project won (Task 12 security
    re-review N1). ``SubdomainTaken`` only when another project's row holds the sub-domain
    (FR-54, TR-53: this login may read ``subdomain``, so it checks; ``insert_hosting_row``)."""
    hosting.validate()
    return await insert_hosting_row(
        conn,
        _insert_hosting(hosting),
        hosting.project_id,
        hosting.subdomain,
        check_holder=True,
    )


def _insert_hosting(hosting: ProjectHosting) -> Insert:
    return (
        pg_insert(_H)
        .values(
            project_id=hosting.project_id,
            live_release_id=None,
            last_command_number=hosting.last_command_number,
            routing_status=hosting.routing_status.value,
            hosting_status=hosting.hosting_status.value,
            updated_at=hosting.updated_at,
            offline_reasons=reasons_value(hosting.offline_reasons),
            owner_status_version=hosting.owner_status_version,
            subdomain=hosting.subdomain,
        )
        .on_conflict_do_nothing(index_elements=[_H.c.project_id])
    )


def _require_hosting(kind: CommandKind) -> None:
    if not parse_command_kind(kind).is_hosting():
        raise InvalidCommandTarget(f"the hosting intake accepts no {kind} command")


def _require_event_key(key: str) -> None:
    """An event key never starts with ``admin:``: that key class is an admin's (decision 0023
    G11), inserted only on the API's login. ``InvalidArgument`` before any SQL."""
    if root_key(key).startswith(ADMIN_KEY_PREFIX):
        raise InvalidArgument("the hosting intake accepts no admin key")


_RETRY_SUFFIX = r"\.retry-([1-9][0-9]{0,8})$"  # the attempt of hosting.split_retry_key


def _latest_per_reason(*where: ColumnElement[bool]) -> Subquery:
    """Hosting commands numbered by recency within ``(project_id, hosting_reason)``: ``recency``
    1 is the latest command of that reason."""
    return (
        select(
            _C.c.project_id,
            _C.c.command_number,
            _C.c.kind,
            _C.c.idempotency_key,
            _C.c.status,
            _C.c.hosting_reason,
            _C.c.updated_at,
            func.row_number()
            .over(
                partition_by=(_C.c.project_id, _C.c.hosting_reason),
                order_by=_C.c.command_number.desc(),
            )
            .label("recency"),
        )
        .where(_C.c.kind.in_(HOSTING_KINDS), *where)
        .subquery("latest")
    )


class PgHostingHistory:
    """``HostingHistory`` and ``DeletionLookup`` on PostgreSQL, on the hosting intake's login
    (SELECT on commands and project_hosting, migration 00013). Read-only transactions."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        async def read(conn: AsyncConnection) -> tuple[HostingIntent, ...]:
            latest = _latest_per_reason(_C.c.project_id == project_id)
            rows = (
                await conn.execute(
                    select(
                        latest.c.kind,
                        latest.c.idempotency_key,
                        latest.c.status,
                        latest.c.hosting_reason,
                        _H.c.offline_reasons,
                        _H.c.owner_status_version,
                    )
                    .join(_H, _H.c.project_id == latest.c.project_id)
                    .where(latest.c.recency == 1)
                    .order_by(latest.c.command_number)
                )
            ).mappings()
            return tuple(hosting_intent_of(row, project_id) for row in rows)

        return await self._tx.run("read latest hosting intents", read, read_only=True)

    async def projects_to_retry(self, *, limit: int, budget: int) -> tuple[UUID, ...]:
        if not 1 <= limit <= MAX_RETRY_SWEEP:
            raise InvalidArgument(f"retry sweep limit must be 1 to {MAX_RETRY_SWEEP}")
        if budget < 1:
            raise InvalidArgument("retry budget must be positive")

        async def read(conn: AsyncConnection) -> tuple[UUID, ...]:
            latest = _latest_per_reason()
            other = _C.alias("in_flight")
            key = latest.c.idempotency_key
            attempt = func.coalesce(cast(func.substring(key, _RETRY_SUFFIX), Integer), 0)
            root = func.regexp_replace(key, _RETRY_SUFFIX, "")
            # The next retry key, as hosting.retry_key builds it; HostingRetry refuses one over
            # MAX_IDEMPOTENCY_KEY_LEN UTF-8 bytes (UNRETRYABLE), so such a project is not listed.
            next_key = func.concat(root, ".retry-", cast(attempt + 1, Text))
            held = latest.c.hosting_reason == any_(_H.c.offline_reasons)
            owner_key = func.concat(OWNER_STATUS_KEY_PREFIX, cast(_H.c.owner_status_version, Text))
            found = await conn.execute(
                select(latest.c.project_id)
                .join(_H, _H.c.project_id == latest.c.project_id)
                .where(
                    latest.c.recency == 1,
                    latest.c.status == CommandStatus.FAILED.value,
                    or_(
                        and_(latest.c.kind == CommandKind.SUSPEND_HOSTING.value, ~held),
                        and_(latest.c.kind == CommandKind.RESUME_HOSTING.value, held),
                    ),
                    or_(
                        latest.c.hosting_reason != OfflineReason.USER_SUSPENDED.value,
                        root == owner_key,
                    ),
                    # An admin's command is never retried through the event intake (0023 G11).
                    latest.c.hosting_reason != OfflineReason.ADMIN_SUSPENDED.value,
                    func.octet_length(next_key) <= MAX_IDEMPOTENCY_KEY_LEN,
                    _H.c.routing_status == RoutingStatus.STABLE.value,
                    attempt < budget,
                    ~exists().where(
                        other.c.project_id == latest.c.project_id,
                        other.c.status.in_(IN_FLIGHT),
                    ),
                )
                .group_by(latest.c.project_id)
                .order_by(func.min(latest.c.updated_at), latest.c.project_id)
                .limit(limit)
            )
            return tuple(found.scalars())

        return await self._tx.run("list hosting retries", read, read_only=True)

    async def project_deleted(self, project_id: UUID) -> bool:
        async def read(conn: AsyncConnection) -> bool:
            found = await conn.execute(
                select(
                    exists().where(
                        _C.c.project_id == project_id,
                        _C.c.kind == CommandKind.SUSPEND_HOSTING.value,
                        _C.c.idempotency_key == DELETION_EVENT_KEY,
                    )
                )
            )
            return bool(found.scalar_one())

        return await self._tx.run("read project deletion", read, read_only=True)
