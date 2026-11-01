"""PgDeployStore: the deploy use case's ``DeployStore`` on PostgreSQL (prototype task F1;
decisions 0011 sections 4 and 6, 0014 sections 3, 11 and 12).

Connected as a login that is a member of ``provisioning_worker`` alone (00010 lets only that role
insert and move ``candidate`` steps). Every method is one transaction of :class:`Transactions`
that first makes the three checks of :func:`load_fenced` (``StaleFence``, ``ClaimLost``,
``IllegalTransition`` for a command that is not running), then applies exactly one domain
function from ``provisioning.core.domain.rules.candidate`` (or ``return_replaced_candidate``) and
writes the rows it returns, each conditional on the status it was loaded in. Times are the
transaction's one reading of the database clock. SQLAlchemy Core only; every value is bound.

Lock order (decision 0004 section 7): project_hosting, command_dispatch, commands, releases,
release_steps.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Insert, Update, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from provisioning.core.application import CorruptRow, Fence
from provisioning.core.application.deploys.ports import (
    CandidateAttempt,
    CandidateAttemptStart,
    DeployFacts,
)
from provisioning.core.domain import (
    CandidateFailure,
    CandidatePlanKind,
    CommandStatus,
    IllegalTransition,
    Outcome,
    OutcomeInput,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReplacedCandidateInput,
    end_candidate_attempt,
    exhaust_candidate_attempts,
    plan_candidate_attempt,
    return_replaced_candidate,
    start_candidate_attempt,
)
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.fenced import (
    FencedRows,
    finish_dispatch,
    load_fenced,
    write_command,
    write_release,
)
from provisioning.infrastructure.postgres.rows import release_of, step_of
from provisioning.infrastructure.postgres.schema import command_dispatch, release_steps, releases
from provisioning.infrastructure.postgres.tx import Timeouts, Transactions

_S = release_steps
_CANDIDATE = "candidate"


class PgDeployStore:
    """The fenced reads and writes of a deploy (``DeployStore``)."""

    __slots__ = ("_tx",)

    def __init__(self, engine: AsyncEngine, timeouts: Timeouts) -> None:
        self._tx = Transactions(engine, timeouts)

    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        """The rows under the three checks; writes nothing (the locks end with the
        transaction)."""
        fence.validate()

        async def load(conn: AsyncConnection) -> DeployFacts:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            release = _release(rows)
            return DeployFacts(
                hosting=rows.hosting,
                command=rows.command,
                release=release,
                last_candidate_step=await _last_candidate_step(conn, release.id, lock=False),
            )

        return await self._tx.run("load deploy facts", load)

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        """The three checks, then claim_expires_at = database clock + ``ttl``, conditional on the
        claim number and an unfinished dispatch row."""
        fence.validate()
        if ttl <= timedelta(0):
            raise InvalidArgument("the claim renewal needs a positive ttl")

        async def renew(conn: AsyncConnection) -> None:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=False)
            now = rows.dispatch.db_now
            await _one(
                conn,
                update(command_dispatch)
                .where(
                    command_dispatch.c.command_id == fence.command_id,
                    command_dispatch.c.claim_number == fence.claim_number,
                    command_dispatch.c.finished_at.is_(None),
                )
                .values(claim_expires_at=now + ttl, updated_at=now),
                f"claim of command {fence.command_id}",
            )

        await self._tx.run("renew claim", renew)

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        """``plan_candidate_attempt`` on the locked release and its last candidate step: the
        leftover attempt it ends is written first; START inserts the step as pending and starts it
        (deadline = database clock + ``start.timeout``); RESUME writes nothing more; EXHAUSTED
        writes ``exhaust_candidate_attempts`` (the command and the release failed)."""
        fence.validate()
        if start.timeout <= timedelta(0):
            raise InvalidArgument("a candidate attempt needs a positive timeout")

        async def begin(conn: AsyncConnection) -> CandidateAttempt:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            release = _release(rows)
            now = rows.dispatch.db_now
            last = await _last_candidate_step(conn, release.id, lock=True)
            plan = plan_candidate_attempt(release, last, start.step_id, now)
            if plan.ended is not None and last is not None:
                await _write_step(conn, last, plan.ended)
            if plan.kind is CandidatePlanKind.EXHAUSTED:
                await _write_outcome(conn, rows, exhaust_candidate_attempts(_input(rows), now))
                return CandidateAttempt(plan.kind)
            if plan.step is None:  # the domain sets it for START and RESUME
                raise CorruptRow("postgres: a candidate plan without its step")
            if plan.kind is CandidatePlanKind.RESUME:
                return CandidateAttempt(plan.kind, plan.step)
            pending = plan.step
            await _one(
                conn,
                insert(_S).values(
                    release_step_id=pending.id,
                    release_id=pending.release_id,
                    step_name=_CANDIDATE,
                    attempt=pending.attempt,
                    status=str(pending.status),
                ),
                f"candidate step {pending.id}",
            )
            running = start_candidate_attempt(release, pending, now + start.timeout, now)
            await _write_step(conn, pending, running)
            return CandidateAttempt(plan.kind, running)

        return await self._tx.run("begin candidate attempt", begin)

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        """Every other candidate_ready release of the project back to built
        (``return_replaced_candidate``), in release ID order; their IDs."""
        fence.validate()

        async def replace(conn: AsyncConnection) -> tuple[UUID, ...]:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            own = _release(rows)
            found = (
                await conn.execute(
                    select(releases)
                    .where(
                        releases.c.project_id == fence.project_id,
                        releases.c.release_id != own.id,
                        releases.c.status == str(ReleaseStatus.CANDIDATE_READY),
                    )
                    .order_by(releases.c.release_id)
                    .with_for_update()
                )
            ).mappings()
            returned: list[UUID] = []
            for row in list(found):
                before = release_of(row)
                after = return_replaced_candidate(
                    ReplacedCandidateInput(
                        hosting=rows.hosting, command=rows.command, release=before
                    ),
                    rows.dispatch.db_now,
                )
                await write_release(conn, before, after)
                returned.append(before.id)
            return tuple(returned)

        return await self._tx.run("return replaced candidates", replace)

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        """``end_candidate_attempt`` on the locked rows and the candidate step ``step_id`` of the
        command's release (another step is ``IllegalTransition``): the step, then the outcome it
        returns (none for a retried failure)."""
        fence.validate()

        async def end(conn: AsyncConnection) -> CandidateFailure | None:
            rows = await load_fenced(conn, fence, CommandStatus.RUNNING, with_release=True)
            release = _release(rows)
            found = (
                (
                    await conn.execute(
                        select(_S)
                        .where(
                            _S.c.release_id == release.id,
                            _S.c.release_step_id == step_id,
                            _S.c.step_name == _CANDIDATE,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if found is None:
                raise IllegalTransition(f"no candidate step {step_id} of release {release.id}")
            before = step_of(found)
            result = end_candidate_attempt(_input(rows), before, failure_code, rows.dispatch.db_now)
            await _write_step(conn, before, result.step)
            if result.outcome is not None:
                await _write_outcome(conn, rows, result.outcome)
            return result.failure

        return await self._tx.run("end candidate attempt", end)


def _release(rows: FencedRows) -> Release:
    if rows.release is None:  # a deploy command always names its release
        raise IllegalTransition(f"command {rows.command.id} names no release")
    return rows.release


def _input(rows: FencedRows) -> OutcomeInput:
    return OutcomeInput(
        hosting=rows.hosting,
        command=rows.command,
        release=rows.release,
        live_release=rows.live_release,
    )


async def _last_candidate_step(
    conn: AsyncConnection, release_id: UUID, *, lock: bool
) -> ReleaseStep | None:
    stmt = (
        select(_S)
        .where(_S.c.release_id == release_id, _S.c.step_name == _CANDIDATE)
        .order_by(_S.c.attempt.desc())
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    row = (await conn.execute(stmt)).mappings().first()
    return None if row is None else step_of(row)


async def _write_step(conn: AsyncConnection, before: ReleaseStep, after: ReleaseStep) -> None:
    """The step's moved columns, conditional on the status it was loaded in."""
    await _one(
        conn,
        update(_S)
        .where(
            _S.c.release_id == before.release_id,
            _S.c.release_step_id == before.id,
            _S.c.step_name == _CANDIDATE,
            _S.c.status == str(before.status),
        )
        .values(
            status=str(after.status),
            started_at=after.started_at,
            finished_at=after.finished_at,
            deadline_at=after.deadline_at,
            failure_code=after.failure_code,
        ),
        f"candidate step {before.id}",
    )


async def _write_outcome(conn: AsyncConnection, rows: FencedRows, out: Outcome) -> None:
    """The outcome's rows in lock order: the dispatch row's finish, the command, the release. A
    deploy outcome changes neither the hosting row nor another release."""
    if out.superseded_release is not None or out.hosting != rows.hosting:
        raise CorruptRow("postgres: a deploy outcome changes rows the deploy store does not write")
    if out.command.status in (CommandStatus.ACCEPTED, CommandStatus.RUNNING):
        raise CorruptRow("postgres: a deploy outcome that does not end the command")
    at: datetime = out.command.updated_at
    await finish_dispatch(conn, _fence(rows), at, out.command.failure_code)
    await write_command(conn, rows.command, out.command)
    if out.release is not None:
        before = _release(rows)
        if before.container_image_digest != out.release.container_image_digest or (
            before.promoted_at != out.release.promoted_at
        ):
            raise CorruptRow("postgres: a deploy outcome changes the digest or promoted_at")
        await write_release(conn, before, out.release)


def _fence(rows: FencedRows) -> Fence:
    return Fence(
        project_id=rows.command.project_id,
        command_id=rows.command.id,
        command_number=rows.command.command_number,
        claim_number=rows.dispatch.claim_number,
    )


async def _one(conn: AsyncConnection, stmt: Update | Insert, what: str) -> None:
    """Run a conditional write that must change exactly one row (else ``IllegalTransition``)."""
    result = await conn.execute(stmt)
    if result.rowcount != 1:
        raise IllegalTransition(f"{what} changed {result.rowcount} rows, want 1")
