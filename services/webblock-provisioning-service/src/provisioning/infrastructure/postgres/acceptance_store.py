"""The acceptance store (decision 0004 section 4) and the acceptance hook that
inserts the dispatch row (decision 0005 section 2).

It runs on the engine of ``provisioning_api`` (deploy, promote, rollback, redeploy, and an
admin's suspend_hosting and resume_hosting with reason ``admin_suspended``, decision 0023 G9 and
G11) or, through :class:`~provisioning.infrastructure.postgres.hosting_intake.HostingIntakeStore`,
of ``provisioning_hosting_intake`` (the other suspend_hosting and resume_hosting commands). Clock
rule (revision 19): the command's times are the application clock (``params.now``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import VARCHAR, Select, bindparam, insert, literal, null, select, update
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import (
    MAX_CORRELATION_ID_LEN,
    AcceptanceDecider,
    AcceptanceDecision,
    AcceptanceFacts,
    AcceptanceHook,
    AcceptCommandParams,
    AcceptedCommand,
    AcceptRedeployParams,
    Contention,
    CorruptRow,
    InvalidCorrelationId,
    NewDispatch,
    NotFound,
    ref_of,
)
from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    InvalidId,
    Release,
    compute_request_fingerprint,
    validate_idempotency_key,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import DatabaseError, is_unique_violation
from provisioning.infrastructure.postgres.rows import command_of, hosting_of, release_of
from provisioning.infrastructure.postgres.schema import command_dispatch, commands, project_hosting
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

# The unique indexes a concurrent acceptance can win past the checks under the lock (decision 0004
# section 4); a violation of one of them reloads the facts and decides again.
# commands_command_number_key is not among them: this path cannot produce it, so it is internal.
RACED_INDEXES = frozenset(
    {"commands_idempotency_key", "commands_hosting_event_key", "commands_one_in_flight"}
)


class _Raced(Exception):
    """Inside one attempt: a unique index of RACED_INDEXES refused the insert."""


type FirstStep[T] = Callable[[AsyncConnection], Awaitable[T | None]]
"""A step ``PgAcceptanceStore.accept_after`` runs first in the acceptance transaction."""


async def _nothing_first(conn: AsyncConnection) -> None:
    del conn


@dataclass(frozen=True, slots=True)
class _Acceptance:
    """One acceptance, whatever the kind: the command ID and dispatch values of the command it
    may insert, how its facts are loaded under the lock, and the request an inserted command must
    be, from those facts (None when no command may be inserted)."""

    project_id: UUID
    command_id: UUID
    correlation_id: str
    deadline_at: datetime
    load: Callable[[AsyncConnection], Awaitable[AcceptanceFacts]]
    expected: Callable[[AcceptanceFacts], CommandRequest | None]


async def insert_dispatch(conn: AsyncConnection, work: NewDispatch) -> None:
    """The acceptance hook (``AcceptanceHook[AsyncConnection]``): insert the fresh dispatch row
    of a newly accepted command in the acceptance transaction, due at once. The insert names the
    command by its whole reference and requires it accepted, so a reference that does not match
    the inserted command is an internal error; the insert guard refuses anything but a fresh row
    in any case."""
    work.ref.validate()
    if not 1 <= len(work.correlation_id) <= MAX_CORRELATION_ID_LEN:
        raise InvalidCorrelationId(f"1 to {MAX_CORRELATION_ID_LEN} characters")
    if not work.deadline_at > work.accepted_at:
        raise DatabaseError("insert dispatch row", reason="the deadline is not after acceptance")
    ref = work.ref
    accepted_at = bindparam("accepted_at", work.accepted_at, type_=TIMESTAMP(timezone=True))
    source = select(
        commands.c.command_id,
        bindparam("correlation_id", work.correlation_id, type_=VARCHAR(128)),
        bindparam("deadline_at", work.deadline_at, type_=TIMESTAMP(timezone=True)),
        accepted_at,
        literal(0),
        null(),
        literal(0),
        literal(0),
        null(),
        null(),
        null(),
        null(),
        null(),
        accepted_at,
        accepted_at,
    ).where(
        commands.c.command_id == ref.command_id,
        commands.c.project_id == ref.project_id,
        commands.c.kind == str(ref.kind),
        commands.c.command_number == ref.command_number,
        commands.c.release_id.is_not_distinct_from(ref.release_id),
        commands.c.status == "accepted",
    )
    stmt = insert(command_dispatch).from_select(
        [
            "command_id",
            "correlation_id",
            "deadline_at",
            "next_publish_at",
            "publish_attempts",
            "last_broker_ack_at",
            "execution_attempts",
            "claim_number",
            "claimed_by_worker",
            "claim_expires_at",
            "retry_after",
            "last_failure_code",
            "finished_at",
            "created_at",
            "updated_at",
        ],
        source,
    )
    result = await conn.execute(stmt)
    if result.rowcount != 1:
        raise DatabaseError(
            "insert dispatch row", reason=f"command {ref.command_id} is not an accepted command"
        )


class PgAcceptanceStore:
    """``AcceptanceStore`` on PostgreSQL. ``hook`` writes the dispatch row of each inserted
    command inside the acceptance transaction (:func:`insert_dispatch` in production)."""

    def __init__(
        self,
        engine: AsyncEngine,
        timeouts: Timeouts,
        hook: AcceptanceHook[AsyncConnection],
    ) -> None:
        self._tx = Transactions(engine, timeouts)
        self._hook = hook

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        compute_request_fingerprint(request)  # the domain's error for an invalid request

        async def read(conn: AsyncConnection) -> Command | None:
            row = (await conn.execute(statements.command_by_key(request))).mappings().first()
            return None if row is None else command_of(row)

        return await self._tx.run("find command by key", read, read_only=True)

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        return await self.accept_after(params, decide, _nothing_first)

    async def accept_after[T](
        self,
        params: AcceptCommandParams,
        decide: AcceptanceDecider,
        first_step: FirstStep[T],
    ) -> T | AcceptedCommand:
        """``accept_command`` with ``first_step`` run at the start of the same transaction, before
        the facts are loaded: an answer that is not None ends the transaction with that answer
        (it commits what the step wrote) and nothing is accepted; None goes on with the
        acceptance, so the step's writes and the command commit or roll back together. The step
        runs again when the acceptance raced, in a new transaction."""
        compute_request_fingerprint(params.request)
        request = params.request

        async def load(conn: AsyncConnection) -> AcceptanceFacts:
            return await _load_facts(
                conn, request.project_id, statements.command_by_key(request), request.release_id
            )

        acceptance = _Acceptance(
            project_id=request.project_id,
            command_id=params.command_id,
            correlation_id=params.correlation_id,
            deadline_at=params.deadline_at,
            load=load,
            expected=lambda _facts: request,
        )
        return await self._accept(acceptance, decide, first_step)

    async def accept_redeploy(
        self, params: AcceptRedeployParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        """``RedeployStore.accept_redeploy`` (decision 0023 G9): the acceptance transaction of
        ``accept_command`` with the key looked up in the client key class and the live release
        read under the lock; an inserted command must be a redeploy of that live release. The
        insert guard (``commands_insert_shape``, migration 00020) checks the same again."""
        if params.project_id.int == 0:
            raise InvalidId("redeploy needs a project ID")
        validate_idempotency_key(params.idempotency_key)
        project, key = params.project_id, params.idempotency_key

        async def load(conn: AsyncConnection) -> AcceptanceFacts:
            return await _load_facts(conn, project, statements.client_command_by_key(project, key))

        def expected(facts: AcceptanceFacts) -> CommandRequest | None:
            live = facts.hosting.live_release_id
            if live is None:
                return None
            return CommandRequest(
                project_id=project, kind=CommandKind.REDEPLOY, release_id=live, idempotency_key=key
            )

        acceptance = _Acceptance(
            project_id=project,
            command_id=params.command_id,
            correlation_id=params.correlation_id,
            deadline_at=params.deadline_at,
            load=load,
            expected=expected,
        )
        return await self._accept(acceptance, decide, _nothing_first)

    async def _accept[T](
        self, acceptance: _Acceptance, decide: AcceptanceDecider, first_step: FirstStep[T]
    ) -> T | AcceptedCommand:
        try:
            return await self._attempt(acceptance, decide, first_step, first=True)
        except _Raced:
            pass
        try:
            return await self._attempt(acceptance, decide, first_step, first=False)
        except _Raced:
            raise Contention("postgres: accept command: raced twice") from None

    async def _attempt[T](
        self,
        acceptance: _Acceptance,
        decide: AcceptanceDecider,
        first_step: FirstStep[T],
        *,
        first: bool,
    ) -> T | AcceptedCommand:
        async def attempt(conn: AsyncConnection) -> T | AcceptedCommand:
            answer = await first_step(conn)
            if answer is not None:
                return answer
            facts = await acceptance.load(conn)
            decision = decide(facts)
            cmd = _check_decision(decision, acceptance, facts)
            if not decision.insert:
                return AcceptedCommand(command=cmd, replayed=True)
            if not first:
                raise _Raced
            await _insert(conn, facts, cmd)
            await self._hook(
                conn,
                NewDispatch(
                    ref=ref_of(cmd),
                    correlation_id=acceptance.correlation_id,
                    accepted_at=cmd.created_at,
                    deadline_at=acceptance.deadline_at,
                ),
            )
            return AcceptedCommand(command=cmd, replayed=False)

        return await self._tx.run("accept command", attempt)


async def _load_facts(
    conn: AsyncConnection,
    project: UUID,
    by_key_statement: Select[tuple[object, ...]],
    release_id: UUID | None = None,
) -> AcceptanceFacts:
    """Lock the hosting row, then read each fact in its own statement, so each takes its
    snapshot after the lock was granted. ``release_id`` is the release the request names (the
    target), None for a hosting kind and a redeploy."""
    locked = (await conn.execute(statements.hosting(project).with_for_update())).mappings().first()
    if locked is None:
        raise NotFound(f"project {project} has no hosting row")
    hosting = hosting_of(locked)
    by_key = (await conn.execute(by_key_statement)).mappings().first()
    in_flight = (await conn.execute(statements.command_in_flight(project))).mappings().first()
    target: Release | None = None
    if release_id is not None:
        row = (await conn.execute(statements.release(project, release_id))).mappings().first()
        target = None if row is None else release_of(row)
    live_row = (await conn.execute(statements.live_release(project))).mappings().first()
    live = None if live_row is None else release_of(live_row)
    if (hosting.live_release_id is not None) != (live is not None):
        raise CorruptRow(f"postgres: project {project} names a live release that was not read")
    return AcceptanceFacts(
        hosting=hosting,
        by_key=None if by_key is None else command_of(by_key),
        in_flight=None if in_flight is None else command_of(in_flight),
        target=target,
        live=live,
    )


def _check_decision(
    decision: AcceptanceDecision, acceptance: _Acceptance, facts: AcceptanceFacts
) -> Command:
    """Refuse, as an internal error, a decision a correct decide function cannot return."""
    op = "accept command: decision"
    cmd = decision.command
    if cmd.project_id != acceptance.project_id:
        raise DatabaseError(
            op, reason=f"command {cmd.id} is not of project {acceptance.project_id}"
        )
    if not decision.insert:
        if facts.by_key is None or cmd.id != facts.by_key.id:
            raise DatabaseError(op, reason=f"replay of command {cmd.id}, which holds no key")
        return cmd
    last = facts.hosting.last_command_number
    if cmd.id != acceptance.command_id:
        raise DatabaseError(op, reason=f"insert of command {cmd.id}, want {acceptance.command_id}")
    request = acceptance.expected(facts)
    if request is None:
        raise DatabaseError(op, reason=f"insert of command {cmd.id} with nothing to name")
    if (
        cmd.kind != request.kind
        or cmd.idempotency_key != request.idempotency_key
        or cmd.release_id != request.release_id
    ):
        raise DatabaseError(op, reason=f"insert of command {cmd.id} is not the request's command")
    if cmd.status != CommandStatus.ACCEPTED:
        raise DatabaseError(op, reason=f"insert of command {cmd.id} in status {cmd.status}")
    if last >= MAX_COMMAND_NUMBER or cmd.command_number != last + 1:
        raise DatabaseError(op, reason=f"insert of command {cmd.id} at {cmd.command_number}")
    return cmd


async def _insert(conn: AsyncConnection, facts: AcceptanceFacts, cmd: Command) -> None:
    """Advance the counter from the value read, then insert the command as accepted; each must
    change exactly one row. A unique violation of RACED_INDEXES is ``_Raced``."""
    op = "accept command: insert"
    project: UUID = facts.hosting.project_id
    advanced = await conn.execute(
        update(project_hosting)
        .where(
            project_hosting.c.project_id == project,
            project_hosting.c.last_command_number == facts.hosting.last_command_number,
        )
        .values(last_command_number=cmd.command_number, updated_at=cmd.created_at)
    )
    if advanced.rowcount != 1:
        raise DatabaseError(op, reason=f"counter update of project {project} missed")
    try:
        inserted = await conn.execute(
            insert(commands).values(
                command_id=cmd.id,
                project_id=cmd.project_id,
                release_id=cmd.release_id,
                kind=str(cmd.kind),
                idempotency_key=cmd.idempotency_key,
                request_fingerprint=cmd.request_fingerprint,
                command_number=cmd.command_number,
                requested_by_subject=cmd.requested_by_subject,
                status="accepted",
                failure_code=None,
                created_at=cmd.created_at,
                updated_at=cmd.created_at,
                hosting_reason=None if cmd.hosting_reason is None else cmd.hosting_reason.value,
            )
        )
    except Exception as exc:
        if is_unique_violation(exc, RACED_INDEXES):
            raise _Raced from None
        raise
    if inserted.rowcount != 1:
        raise DatabaseError(op, reason=f"insert of command {cmd.id} stored no row")
