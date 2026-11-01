"""The job store of decision 0005 and the fenced worker writes of decision 0004 section 7, on the
engine of ``provisioning_worker``. It implements ``JobStore`` (the dispatcher's take, records and
orphan count; the consumer's claim, heartbeat and release) and ``WorkerStore``.

Claim and dispatch arithmetic uses the database clock: each transaction reads it once and derives
every time it writes or compares from it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import (
    MAX_CLAIMED_BY_WORKER_LEN,
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    ClaimResult,
    CommandFinish,
    CorruptRow,
    DeployOutcome,
    DueDispatch,
    Fence,
    WorkRef,
    ref_of,
)
from provisioning.core.domain import (
    CODE_DEADLINE_EXCEEDED,
    CommandKind,
    CommandStatus,
    DomainError,
    Failure,
    IllegalTransition,
    InvalidFailureCode,
    Outcome,
    OutcomeInput,
    ReleaseStepStatus,
    RoutingStatus,
    StepInput,
    classify_deploy_failure,
    deploy_succeeded,
    fail_without_route_change,
    parse_command_kind,
    skip_hosting_command,
    validate_code,
)
from provisioning.infrastructure.postgres import statements
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.fenced import (
    FencedRows,
    finish_dispatch,
    load_fenced,
    lock_command,
    lock_dispatch,
    write_command,
    write_release,
)
from provisioning.infrastructure.postgres.rows import command_of, step_of
from provisioning.infrastructure.postgres.schema import (
    command_dispatch,
    commands,
    project_hosting,
    release_steps,
)
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_MICROSECOND = timedelta(microseconds=1)
_MAX_INT32 = 2**31 - 1

# Fails a candidate attempt an earlier claim left unfinished.
STALE_CANDIDATE_CODE = CODE_DEADLINE_EXCEEDED


def _duration(name: str, value: timedelta) -> timedelta:
    if value < _MICROSECOND:
        raise InvalidArgument(f"{name} must be at least one microsecond")
    return value


def _worker(name: str) -> str:
    if not 1 <= len(name) <= MAX_CLAIMED_BY_WORKER_LEN:
        raise InvalidArgument(
            f"worker identity must be 1 to {MAX_CLAIMED_BY_WORKER_LEN} characters"
        )
    return name


def _attempts(due: DueDispatch) -> int:
    if not 1 <= due.publish_attempts <= _MAX_INT32:
        raise InvalidArgument("publish attempts out of range")
    return due.publish_attempts


class PgJobStore:
    """``JobStore`` and ``WorkerStore`` on PostgreSQL."""

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    # --- dispatch -------------------------------------------------------------------------------

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]:
        """Up to ``limit`` due dispatch rows of unfinished commands, oldest first, locked SKIP
        LOCKED so concurrent dispatchers get disjoint batches. A row under a live claim is not
        returned: its next_publish_at moves to the claim's expiry and its attempts stay. Every
        other row moves by ``visibility`` and counts one publish attempt."""
        if not 1 <= limit <= _MAX_INT32:
            raise InvalidArgument("batch size out of range")
        _duration("visibility", visibility)

        async def take(conn: AsyncConnection) -> list[DueDispatch]:
            now = await statements.read_clock(conn)
            w = command_dispatch
            due = (
                select(w.c.command_id)
                .where(w.c.finished_at.is_(None), w.c.next_publish_at <= now)
                .order_by(w.c.next_publish_at, w.c.command_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            ids: list[UUID] = list((await conn.execute(due)).scalars())
            if not ids:
                return []
            live = w.c.claim_expires_at > now
            taken = (
                await conn.execute(
                    update(w)
                    .where(w.c.command_id.in_(ids))
                    .values(
                        next_publish_at=case((live, w.c.claim_expires_at), else_=now + visibility),
                        publish_attempts=case(
                            (live, w.c.publish_attempts), else_=w.c.publish_attempts + 1
                        ),
                        updated_at=now,
                    )
                    .returning(
                        w.c.command_id,
                        w.c.correlation_id,
                        w.c.publish_attempts,
                        w.c.claim_number,
                        w.c.execution_attempts,
                        w.c.deadline_at,
                        func.coalesce(w.c.claim_expires_at > now, False).label("claim_live"),
                    )
                )
            ).mappings()
            by_id = {row["command_id"]: row for row in taken}
            cmds = (
                await conn.execute(
                    select(
                        commands.c.command_id,
                        commands.c.project_id,
                        commands.c.kind,
                        commands.c.command_number,
                        commands.c.release_id,
                    ).where(commands.c.command_id.in_(ids))
                )
            ).mappings()
            refs = {row["command_id"]: row for row in cmds}
            out: list[DueDispatch] = []
            for command_id in ids:
                row = by_id[command_id]
                if row["claim_live"]:
                    continue
                c = refs[command_id]
                ref = WorkRef(
                    command_id=command_id,
                    project_id=c["project_id"],
                    kind=_kind(c["kind"], command_id),
                    command_number=c["command_number"],
                    release_id=c["release_id"],
                )
                try:
                    ref.validate()
                except DomainError as exc:
                    raise CorruptRow(f"postgres: command {command_id}: {exc}") from None
                deadline = row["deadline_at"]
                out.append(
                    DueDispatch(
                        ref=ref,
                        correlation_id=row["correlation_id"],
                        publish_attempts=row["publish_attempts"],
                        claim_number=row["claim_number"],
                        execution_attempts=row["execution_attempts"],
                        deadline_at=deadline,
                        deadline_passed=deadline < now,
                    )
                )
            return out

        return await self._tx.run("take due dispatch rows", take)

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        due.ref.validate()
        _duration("redeliver_after", redeliver_after)
        attempts = _attempts(due)

        async def record(conn: AsyncConnection) -> bool:
            now = await statements.read_clock(conn)
            w = command_dispatch
            result = await conn.execute(
                update(w)
                .where(
                    w.c.command_id == due.ref.command_id,
                    w.c.publish_attempts == attempts,
                    w.c.finished_at.is_(None),
                    w.c.claimed_by_worker.is_(None) | (w.c.claim_expires_at <= now),
                )
                .values(
                    last_broker_ack_at=now, next_publish_at=now + redeliver_after, updated_at=now
                )
            )
            return result.rowcount == 1

        return await self._tx.run("record broker ack", record)

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool:
        due.ref.validate()
        _duration("retry_after", retry_after)
        attempts = _attempts(due)

        async def record(conn: AsyncConnection) -> bool:
            now = await statements.read_clock(conn)
            w = command_dispatch
            result = await conn.execute(
                update(w)
                .where(
                    w.c.command_id == due.ref.command_id,
                    w.c.publish_attempts == attempts,
                    w.c.finished_at.is_(None),
                )
                .values(next_publish_at=now + retry_after, updated_at=now)
            )
            return result.rowcount == 1

        return await self._tx.run("record publish failure", record)

    async def count_unfinished_without_dispatch(self) -> int:
        async def count(conn: AsyncConnection) -> int:
            has_dispatch = (
                select(command_dispatch.c.command_id)
                .where(command_dispatch.c.command_id == commands.c.command_id)
                .exists()
            )
            stmt = (
                select(func.count())
                .select_from(commands)
                .where(commands.c.status.in_(statements.IN_FLIGHT), ~has_dispatch)
            )
            value = (await conn.execute(stmt)).scalar_one()
            return int(value)

        return await self._tx.run("count unfinished commands without dispatch", count)

    # --- claims ---------------------------------------------------------------------------------

    async def claim(self, request: ClaimRequest) -> ClaimResult:
        """One short transaction that locks the dispatch row, then the command, and takes no
        project lock (decision 0005 section 4). The outcome is decided on the locked rows with one
        database instant, in this order: no dispatch row; a corrupt command; a mismatch; another
        command number; a finished command; a counter that does not hold the command's number
        (corrupt); a live claim (held); a retry time not passed (backoff); otherwise claimed."""
        request.ref.validate()
        _worker(request.claimed_by_worker)
        ttl = _duration("ttl", request.ttl)

        async def claim(conn: AsyncConnection) -> ClaimResult:
            ref = request.ref
            dispatch = await lock_dispatch(conn, ref.command_id)
            if dispatch is None:
                return ClaimResult(ClaimOutcome.NO_WORK)
            row = await lock_command(conn, ref.command_id)
            if row is None:
                return ClaimResult(ClaimOutcome.NO_WORK)
            try:
                cmd = command_of(row)
            except CorruptRow:
                return ClaimResult(ClaimOutcome.CORRUPT)
            if (
                cmd.project_id != ref.project_id
                or cmd.kind != ref.kind
                or cmd.release_id != ref.release_id
            ):
                return ClaimResult(ClaimOutcome.MISMATCH)
            if cmd.command_number != ref.command_number:
                return ClaimResult(ClaimOutcome.STALE_COMMAND_NUMBER)
            if cmd.status not in (CommandStatus.ACCEPTED, CommandStatus.RUNNING):
                return ClaimResult(ClaimOutcome.FINISHED)
            last: int | None = (
                await conn.execute(
                    select(project_hosting.c.last_command_number).where(
                        project_hosting.c.project_id == cmd.project_id
                    )
                )
            ).scalar_one_or_none()
            if last is None or last != cmd.command_number:
                return ClaimResult(ClaimOutcome.CORRUPT)
            now = dispatch.db_now
            if (
                dispatch.claimed_by_worker is not None
                and dispatch.claim_expires_at is not None
                and dispatch.claim_expires_at > now
            ):
                return ClaimResult(ClaimOutcome.HELD)
            if dispatch.retry_after is not None and dispatch.retry_after > now:
                return ClaimResult(ClaimOutcome.BACKOFF)
            w = command_dispatch
            claimed = (
                (
                    await conn.execute(
                        update(w)
                        .where(w.c.command_id == cmd.id, w.c.claim_number == dispatch.claim_number)
                        .values(
                            claim_number=w.c.claim_number + 1,
                            execution_attempts=w.c.execution_attempts + 1,
                            claimed_by_worker=request.claimed_by_worker,
                            claim_expires_at=now + ttl,
                            next_publish_at=now + ttl,
                            updated_at=now,
                        )
                        .returning(w.c.claim_number, w.c.execution_attempts, w.c.claim_expires_at)
                    )
                )
                .mappings()
                .one()
            )
            if cmd.status == CommandStatus.ACCEPTED:
                await write_command(conn, cmd, cmd.transition(CommandStatus.RUNNING, "", now))
            return ClaimResult(
                ClaimOutcome.CLAIMED,
                claim=Claim(
                    ref=ref_of(cmd),
                    claimed_by_worker=request.claimed_by_worker,
                    claim_number=claimed["claim_number"],
                    execution_attempt=claimed["execution_attempts"],
                    expires_at=claimed["claim_expires_at"],
                    deadline_at=dispatch.deadline_at,
                    last_failure_code=dispatch.last_failure_code,
                ),
                deadline_passed=dispatch.deadline_at < now,
            )

        return await self._tx.run("claim", claim)

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim:
        """The heartbeat: one statement on the dispatch row, conditional on the claim number and
        the worker; ``ClaimLost`` when either moved or the command finished."""
        _check_claim(claim)
        _duration("ttl", ttl)

        async def extend(conn: AsyncConnection) -> Claim:
            now = await statements.read_clock(conn)
            w = command_dispatch
            expires = (
                await conn.execute(
                    update(w)
                    .where(
                        w.c.command_id == claim.ref.command_id,
                        w.c.claim_number == claim.claim_number,
                        w.c.claimed_by_worker == claim.claimed_by_worker,
                        w.c.finished_at.is_(None),
                    )
                    .values(claim_expires_at=now + ttl, updated_at=now)
                    .returning(w.c.claim_expires_at)
                )
            ).scalar_one_or_none()
            if expires is None:
                raise ClaimLost(
                    f"command {claim.ref.command_id} claim {claim.claim_number} is lost"
                )
            return Claim(
                ref=claim.ref,
                claimed_by_worker=claim.claimed_by_worker,
                claim_number=claim.claim_number,
                execution_attempt=claim.execution_attempt,
                expires_at=expires,
                deadline_at=claim.deadline_at,
                last_failure_code=claim.last_failure_code,
            )

        return await self._tx.run("extend claim", extend)

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        """Give the command back: under the dispatch row lock, a claim number or worker that no
        longer matches is ``ClaimLost``; a finished command changes nothing; otherwise the claim
        is cleared and retry_after and next_publish_at are set from the database clock."""
        _check_claim(claim)
        _duration("retry_after", retry_after)
        validate_code(failure_code)

        async def release(conn: AsyncConnection) -> None:
            dispatch = await lock_dispatch(conn, claim.ref.command_id)
            if dispatch is None:
                raise ClaimLost(f"no dispatch row for command {claim.ref.command_id}")
            if (
                dispatch.claim_number != claim.claim_number
                or dispatch.claimed_by_worker != claim.claimed_by_worker
            ):
                raise ClaimLost(
                    f"command {claim.ref.command_id} claim {claim.claim_number} is lost"
                )
            status: str = (
                await conn.execute(
                    select(commands.c.status).where(commands.c.command_id == claim.ref.command_id)
                )
            ).scalar_one()
            if dispatch.finished_at is not None or status not in statements.IN_FLIGHT:
                return
            now = dispatch.db_now
            w = command_dispatch
            result = await conn.execute(
                update(w)
                .where(
                    w.c.command_id == claim.ref.command_id,
                    w.c.claim_number == claim.claim_number,
                    w.c.claimed_by_worker == claim.claimed_by_worker,
                    w.c.finished_at.is_(None),
                )
                .values(
                    claimed_by_worker=None,
                    claim_expires_at=None,
                    retry_after=now + retry_after,
                    next_publish_at=now + retry_after,
                    last_failure_code=failure_code,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise ClaimLost(
                    f"command {claim.ref.command_id} claim {claim.claim_number} is lost"
                )

        await self._tx.run("release claim", release)

    # --- fenced writes (WorkerStore) ------------------------------------------------------------

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None:
        """running -> failed (``fail_without_route_change``, release untouched) or running ->
        skipped (``skip_hosting_command``). Both refuse a route that is changing."""
        if finish.status not in (CommandStatus.FAILED, CommandStatus.SKIPPED):
            raise IllegalTransition(f"a command is finished failed or skipped, not {finish.status}")
        fence.validate()

        async def write(conn: AsyncConnection) -> None:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            failure = Failure(code=finish.failure_code)
            if finish.status == CommandStatus.SKIPPED:
                out = skip_hosting_command(_input(rows), failure, finish.at)
            else:
                out = fail_without_route_change(_input(rows), failure, False, finish.at)
            await _write_outcome(conn, rows, out, fence, finish.at)

        await self._tx.run("finish command", write)

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        """accepted -> failed for a command never claimed, after the three checks with status
        accepted; also requires the deadline passed by the database clock and a route that is not
        changing."""
        fence.validate()

        async def write(conn: AsyncConnection) -> None:
            rows = await load_fenced(conn, fence, CommandStatus.ACCEPTED, with_release=False)
            if rows.hosting.routing_status == RoutingStatus.CHANGING:
                raise IllegalTransition(f"project {fence.project_id} routing is changing")
            if not rows.dispatch.deadline_at < rows.dispatch.db_now:
                raise IllegalTransition(
                    f"the deadline of command {fence.command_id} has not passed"
                )
            failed = rows.command.transition(CommandStatus.FAILED, failure_code, at)
            await _write_outcome(
                conn, rows, Outcome(hosting=rows.hosting, command=failed), fence, at
            )

        await self._tx.run("fail unclaimed command", write)

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None:
        """built -> candidate_ready with the command succeeded (``deploy_succeeded``), or built
        -> failed with the command failed (``fail_without_route_change`` with release_failed) for
        a code the deploy classification marks as failing the release. A candidate attempt an
        earlier claim left unfinished is failed in the same transaction."""
        fence.validate()
        if (
            outcome.failure is not None
            and not classify_deploy_failure(outcome.failure.code).fails_release
        ):
            raise InvalidFailureCode(f"{outcome.failure.code} does not fail a release")

        async def write(conn: AsyncConnection) -> None:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            if outcome.failure is None:
                out = deploy_succeeded(_input(rows), outcome.at)
            else:
                if rows.command.kind != CommandKind.DEPLOY:
                    raise IllegalTransition(f"a deploy outcome for a {rows.command.kind} command")
                out = fail_without_route_change(_input(rows), outcome.failure, True, outcome.at)
            await _write_outcome(conn, rows, out, fence, outcome.at)
            if rows.release is not None:
                await _fail_stale_candidate_steps(conn, rows.release.id, rows.dispatch.db_now)

        await self._tx.run("apply deploy outcome", write)


def _kind(value: object, command_id: UUID) -> CommandKind:
    try:
        return parse_command_kind(value)
    except DomainError:
        raise CorruptRow(f"postgres: command {command_id} has an unknown kind") from None


def _check_claim(claim: Claim) -> None:
    claim.ref.validate()
    if claim.claim_number < 1:
        raise InvalidArgument("a claim has a claim number of at least 1")
    _worker(claim.claimed_by_worker)


def _input(rows: FencedRows) -> OutcomeInput:
    return OutcomeInput(
        hosting=rows.hosting,
        command=rows.command,
        release=rows.release,
        live_release=rows.live_release,
    )


async def _write_outcome(
    conn: AsyncConnection, rows: FencedRows, out: Outcome, fence: Fence, at: datetime
) -> None:
    """Write exactly the rows and columns the outcome changed, in lock order: the dispatch row's
    finish, the command, the release. An outcome that changes what this store does not write
    (the hosting row, a superseded release, a digest or promoted_at) is an internal error."""
    if out.superseded_release is not None or out.hosting != rows.hosting:
        raise CorruptRow("postgres: outcome changes rows the worker store does not write")
    if out.command.status in (CommandStatus.ACCEPTED, CommandStatus.RUNNING):
        raise CorruptRow("postgres: outcome does not end the command")
    await finish_dispatch(conn, fence, at, out.command.failure_code)
    await write_command(conn, rows.command, out.command)
    if out.release is None:
        return
    before = rows.release
    if (
        before is None
        or before.container_image_digest != out.release.container_image_digest
        or before.promoted_at != out.release.promoted_at
    ):
        raise CorruptRow(
            "postgres: outcome changes release columns the worker store does not write"
        )
    await write_release(conn, before, out.release)


async def _fail_stale_candidate_steps(
    conn: AsyncConnection, release_id: object, now: datetime
) -> None:
    """Fail every unfinished candidate attempt of the release, at the database clock."""
    s = release_steps
    locked = (
        await conn.execute(
            select(s)
            .where(
                s.c.release_id == release_id,
                s.c.step_name == "candidate",
                s.c.status.in_(("pending", "running")),
            )
            .order_by(s.c.attempt)
            .with_for_update()
        )
    ).mappings()
    for row in list(locked):
        before = step_of(row)
        after = before.transition(
            ReleaseStepStatus.FAILED, StepInput(failure_code=STALE_CANDIDATE_CODE), now
        )
        result = await conn.execute(
            update(s)
            .where(
                s.c.release_id == before.release_id,
                s.c.release_step_id == before.id,
                s.c.step_name == "candidate",
                s.c.status == str(before.status),
            )
            .values(
                status=str(after.status),
                started_at=after.started_at,
                finished_at=after.finished_at,
                failure_code=after.failure_code,
            )
        )
        if result.rowcount != 1:
            raise IllegalTransition(f"candidate step {before.id} is no longer {before.status}")
