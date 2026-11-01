"""The acceptance, read and hosting-intake stores on PostgreSQL 18 (prototype task B3).

Acceptance, races, reads and round trips, with revision 19 names. Each store runs on a login that is
a member of exactly one role: provisioning_api for acceptance and reads, provisioning_hosting_intake
for the Payment-event commands.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from tests.integration._pg import ROLE_API, ROLE_HOSTING_INTAKE, Database
from tests.integration.test_store_support import (
    COMMANDS,
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
    at,
    command_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
    step_row,
)

from provisioning.core.application import (
    AcceptanceDecision,
    AcceptanceFacts,
    AcceptCommandParams,
    AcceptedCommand,
    CommandKey,
    Contention,
    CorruptRow,
    NotFound,
    OperationInProgress,
    ReleaseKey,
    new_acceptance_decider,
)
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    CommandStatus,
    IdempotencyConflict,
    InvalidCommandTarget,
    OfflineReason,
    RouteNotStable,
    is_error,
)
from provisioning.infrastructure.postgres import (
    DatabaseError,
    HostingIntakeStore,
    PgAcceptanceStore,
    PgReadStore,
    insert_dispatch,
)

type DbFactory = Callable[[int | None], Database]

pytestmark = pytest.mark.asyncio


def params(
    project: UUID,
    kind: CommandKind,
    release: UUID | None,
    key: str,
    *,
    subject: str = "subject-1",
) -> AcceptCommandParams:
    return AcceptCommandParams(
        request=CommandRequest(
            project_id=project, kind=kind, release_id=release, idempotency_key=key
        ),
        command_id=uuid4(),
        requested_by_subject=subject,
        now=at(100),
        correlation_id="corr-accept",
        deadline_at=at(100) + timedelta(hours=1),
    )


def built_project(db: Database, *, routing: str = "stable") -> tuple[UUID, UUID]:
    project, built = uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "built", release_id=built)),
        (HOSTING, hosting_row(project, routing=routing)),
    )
    return project, built


async def accept(store: PgAcceptanceStore, p: AcceptCommandParams) -> object:
    return await store.accept_command(p, new_acceptance_decider(p))


async def test_accept_inserts_command_dispatch_row_and_advances_the_counter(db: Database) -> None:
    """FR-14.1: accept inserts command dispatch row and advances the counter."""
    project, built = built_project(db)
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        accepted = await store.accept_command(p, new_acceptance_decider(p))
    assert not accepted.replayed
    assert accepted.command.id == p.command_id
    assert accepted.command.command_number == 1
    assert accepted.command.status == CommandStatus.ACCEPTED
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 1
    stored = row_of(
        db,
        "SELECT status::text, command_number, requested_by_subject FROM provisioning.commands"
        " WHERE command_id = %s",
        p.command_id,
    )
    assert stored == ("accepted", 1, "subject-1")
    dispatch = row_of(
        db,
        "SELECT correlation_id, publish_attempts, claim_number, next_publish_at, deadline_at"
        " FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        p.command_id,
    )
    assert dispatch == ("corr-accept", 0, 0, at(100), at(100) + timedelta(hours=1))


async def test_same_key_and_request_replays_the_original(db: Database) -> None:
    """FR-14.2: same key and request replays the original."""
    project, built = built_project(db)
    first = params(project, CommandKind.DEPLOY, built, "key-1")
    again = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        original = await store.accept_command(first, new_acceptance_decider(first))
        replay = await store.accept_command(again, new_acceptance_decider(again))
        found = await store.find_command_by_key(again.request)
    assert replay.replayed
    assert replay.command == original.command
    assert found == original.command
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 1
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 1


async def test_same_key_with_another_request_is_a_conflict(db: Database) -> None:
    """FR-14.5: same key with another request is a conflict."""
    project, built = built_project(db)
    other = uuid4()
    seed_rows(db, (RELEASES, release_row(project, "built", release_id=other, name="o")))
    first = params(project, CommandKind.DEPLOY, built, "key-1")
    second = params(project, CommandKind.DEPLOY, other, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        await store.accept_command(first, new_acceptance_decider(first))
        with pytest.raises(IdempotencyConflict):
            await store.accept_command(second, new_acceptance_decider(second))
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 1


async def test_a_second_command_in_flight_is_refused_as_busy(db: Database) -> None:
    """FR-14.3: a second command in flight is refused as busy."""
    project, built = built_project(db)
    first = params(project, CommandKind.DEPLOY, built, "key-1")
    second = params(project, CommandKind.DEPLOY, built, "key-2")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        accepted = await store.accept_command(first, new_acceptance_decider(first))
        with pytest.raises(OperationInProgress) as info:
            await store.accept_command(second, new_acceptance_decider(second))
    assert info.value.command_id == accepted.command.id


async def test_concurrent_acceptors_exactly_one_wins(db: Database) -> None:
    project, built = built_project(db)
    many = [params(project, CommandKind.DEPLOY, built, f"key-{i}") for i in range(6)]
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        results = await asyncio.gather(
            *(store.accept_command(p, new_acceptance_decider(p)) for p in many),
            return_exceptions=True,
        )
    won = [r for r in results if not isinstance(r, BaseException)]
    lost = [r for r in results if isinstance(r, BaseException)]
    assert len(won) == 1
    assert all(isinstance(e, OperationInProgress) for e in lost), lost
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 1
    assert scalar(db, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 1
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 1


async def test_concurrent_replays_of_one_key_store_one_command(db: Database) -> None:
    project, built = built_project(db)
    many = [params(project, CommandKind.DEPLOY, built, "key-same") for _ in range(6)]
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        results = await asyncio.gather(
            *(store.accept_command(p, new_acceptance_decider(p)) for p in many)
        )
    assert sum(1 for r in results if not r.replayed) == 1
    assert len({r.command.id for r in results}) == 1


async def test_an_insert_that_races_past_the_checks_reloads_the_facts(db: Database) -> None:
    """The hosting row lock serializes acceptors, so a key can appear between the checks and the
    insert only through a writer that skips the lock. Such a writer is simulated: the first call
    of the decide function commits a command under the same key from another session. The insert
    then hits commands_idempotency_key, and the store reloads the facts and decides again, which
    replays the command that won."""
    project, built = built_project(db)
    p = params(project, CommandKind.DEPLOY, built, "key-raced")
    winner = uuid4()
    decide = new_acceptance_decider(p)
    calls = 0

    def racing(facts: AcceptanceFacts) -> AcceptanceDecision:
        nonlocal calls
        calls += 1
        if calls == 1:
            seed_rows(
                db,
                (
                    COMMANDS,
                    command_row(
                        project,
                        CommandKind.DEPLOY,
                        99,
                        "succeeded",
                        release=built,
                        key="key-raced",
                        command_id=winner,
                    ),
                ),
            )
        return decide(facts)

    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        accepted = await store.accept_command(p, racing)
    assert calls == 2
    assert accepted.replayed
    assert accepted.command.id == winner
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 0


async def test_a_project_without_hosting_row_is_not_found(db: Database) -> None:
    p = params(uuid4(), CommandKind.DEPLOY, uuid4(), "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        with pytest.raises(NotFound):
            await store.accept_command(p, new_acceptance_decider(p))


async def test_a_refusal_of_the_decider_writes_nothing(db: Database) -> None:
    project, built = built_project(db, routing="uncertain")
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        with pytest.raises(RouteNotStable):
            await store.accept_command(p, new_acceptance_decider(p))
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 0
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 0


async def test_a_corrupt_stored_row_is_corrupt_row_without_driver_text(db: Database) -> None:
    project, built = built_project(db)
    # A stored command whose fingerprint does not match its columns (seeded past the guards).
    seed_rows(
        db,
        (
            COMMANDS,
            command_row(
                project,
                CommandKind.DEPLOY,
                1,
                "succeeded",
                release=built,
                key="key-1",
                request_fingerprint="0" * 64,
            ),
        ),
    )
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        with pytest.raises(CorruptRow) as info:
            await store.find_command_by_key(p.request)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__


# --- reads ------------------------------------------------------------------------------------


async def test_reads_return_rows_of_the_project_only(db: Database) -> None:
    project, built = built_project(db)
    seed_rows(
        db,
        (STEPS, step_row(built, 1, "succeeded", started_at=at(1), finished_at=at(2))),
        (STEPS, step_row(built, 1, "pending", name="candidate")),
    )
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        accept_store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        accepted = await accept_store.accept_command(p, new_acceptance_decider(p))
        reads = PgReadStore(engine, TIMEOUTS)
        release = await reads.read_release(ReleaseKey(project, built))
        command = await reads.read_command(CommandKey(project, accepted.command.id))
        deployment = await reads.read_deployment(project)
        with pytest.raises(NotFound):
            await reads.read_release(ReleaseKey(uuid4(), built))
        with pytest.raises(NotFound):
            await reads.read_command(CommandKey(uuid4(), accepted.command.id))
        with pytest.raises(NotFound):
            await reads.read_deployment(uuid4())
    assert release.release.id == built
    assert [(s.name, s.attempt) for s in release.steps] == [("build", 1), ("candidate", 1)]
    assert command == accepted.command
    assert deployment.in_flight == accepted.command
    assert deployment.hosting.last_command_number == 1


async def test_more_steps_than_allowed_is_corrupt(db: Database) -> None:
    project, built = built_project(db)
    seed_rows(
        db,
        *(
            (
                STEPS,
                step_row(built, i, "failed", started_at=at(1), finished_at=at(2), failure_code="x"),
            )
            for i in range(1, 102)
        ),
    )
    async with role_engine(db, ROLE_API) as engine:
        with pytest.raises(CorruptRow):
            await PgReadStore(engine, TIMEOUTS).read_release(ReleaseKey(project, built))


# --- hosting intake ---------------------------------------------------------------------------


def hosting_project(db: Database) -> UUID:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project)))
    return project


async def test_hosting_intake_accepts_a_suspend_keyed_by_the_event(db: Database) -> None:
    project = hosting_project(db)
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        store = HostingIntakeStore(engine, TIMEOUTS)
        first = await store.accept_hosting_command(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            event_key="pay-evt-1",
            command_id=uuid4(),
            now=at(100),
            correlation_id="corr-pay",
            deadline_at=at(100) + timedelta(hours=1),
        )
        again = await store.accept_hosting_command(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            event_key="pay-evt-1",
            command_id=uuid4(),
            now=at(200),
            correlation_id="corr-pay-2",
            deadline_at=at(200) + timedelta(hours=1),
        )
        with pytest.raises(IdempotencyConflict):
            await store.accept_hosting_command(
                project_id=project,
                kind=CommandKind.RESUME_HOSTING,
                event_key="pay-evt-1",
                command_id=uuid4(),
                now=at(300),
                correlation_id="corr-pay-3",
                deadline_at=at(300) + timedelta(hours=1),
            )
    assert not first.replayed
    assert again.replayed
    assert again.command == first.command
    assert first.command.kind == CommandKind.SUSPEND_HOSTING
    assert first.command.requested_by_subject is None
    assert scalar(db, "SELECT last_command_number FROM provisioning.project_hosting") == 1
    assert scalar(db, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 1


async def test_hosting_command_is_stored_with_its_reason(db: Database) -> None:
    """The key fixes the reason (decision 0021): ``project-deleted`` is stored and read back as
    ``project_deleted``, a Payment key as ``billing``."""
    project = hosting_project(db)
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        store = HostingIntakeStore(engine, TIMEOUTS)
        accepted = await store.accept_hosting_command(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            event_key="project-deleted",
            command_id=uuid4(),
            now=at(100),
            correlation_id="corr-deleted",
            deadline_at=at(100) + timedelta(hours=1),
        )
        replay = await store.accept_hosting_command(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            event_key="project-deleted",
            command_id=uuid4(),
            now=at(200),
            correlation_id="corr-deleted-2",
            deadline_at=at(200) + timedelta(hours=1),
        )
    assert accepted.command.hosting_reason is OfflineReason.PROJECT_DELETED
    assert replay.replayed
    assert replay.command.hosting_reason is OfflineReason.PROJECT_DELETED
    stored = row_of(
        db,
        "SELECT hosting_reason::text FROM provisioning.commands WHERE command_id = %s",
        accepted.command.id,
    )
    assert stored == ("project_deleted",)
    async with role_engine(db, ROLE_API) as engine:
        read = await PgReadStore(engine, TIMEOUTS).read_command(
            CommandKey(project, accepted.command.id)
        )
    assert read.hosting_reason is OfflineReason.PROJECT_DELETED


async def test_a_client_command_is_stored_without_a_reason(db: Database) -> None:
    project, built = built_project(db)
    p = params(project, CommandKind.DEPLOY, built, "project-deleted")
    async with role_engine(db, ROLE_API) as engine:
        accepted = await accept(PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch), p)
    assert isinstance(accepted, AcceptedCommand)
    assert accepted.command.hosting_reason is None
    stored = row_of(
        db, "SELECT hosting_reason FROM provisioning.commands WHERE command_id = %s", p.command_id
    )
    assert stored == (None,)


async def test_hosting_intake_refuses_a_release_command(db: Database) -> None:
    project, built = built_project(db)
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        store = HostingIntakeStore(engine, TIMEOUTS)
        with pytest.raises(InvalidCommandTarget):
            await store.accept_command(p, new_acceptance_decider(p))
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 0


async def test_hosting_command_while_a_deploy_is_in_flight_is_busy(db: Database) -> None:
    project, built = built_project(db)
    p = params(project, CommandKind.DEPLOY, built, "key-1")
    async with role_engine(db, ROLE_API) as engine:
        await PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch).accept_command(
            p, new_acceptance_decider(p)
        )
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        with pytest.raises(OperationInProgress):
            await HostingIntakeStore(engine, TIMEOUTS).accept_hosting_command(
                project_id=project,
                kind=CommandKind.SUSPEND_HOSTING,
                event_key="pay-evt-1",
                command_id=uuid4(),
                now=at(100),
                correlation_id="corr-pay",
                deadline_at=at(100) + timedelta(hours=1),
            )


async def test_api_role_cannot_accept_a_hosting_command(db: Database) -> None:
    project = hosting_project(db)
    p = params(project, CommandKind.SUSPEND_HOSTING, None, "pay-evt-1", subject="")
    async with role_engine(db, ROLE_API) as engine:
        store = PgAcceptanceStore(engine, TIMEOUTS, insert_dispatch)
        with pytest.raises(DatabaseError) as info:
            await store.accept_command(p, new_acceptance_decider(p))
    text = str(info.value)
    assert "commands_insert_shape" in text
    assert "pay-evt-1" not in text
    assert not is_error(info.value, Contention)
    assert info.value.__cause__ is None
