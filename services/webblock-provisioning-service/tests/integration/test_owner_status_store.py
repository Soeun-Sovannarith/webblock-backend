"""The owner-status intake on PostgreSQL at the latest migration, on a login of
``provisioning_hosting_intake`` alone (decision 0021 D9; Notion "5 · Provisioning", "User
suspension" and "Out-of-order events")."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import (
    ROLE_BOOTSTRAP,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    Database,
    seed,
)
from tests.integration._rows import sha
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    at,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
)
from tests.unit.core.application.routing.background.test_routing_support import FakeIds

from provisioning.core.application import (
    AcceptOwnerStatusCommand,
    AcceptOwnerStatusHandler,
    InboxEntry,
    IntakeParams,
    NullAuditLog,
    OperationInProgress,
    OwnerStatus,
    OwnerStatusOutcome,
    OwnerStatusResult,
    ReleaseAcceptanceParams,
    ReleaseAcceptanceResult,
    SubdomainTaken,
    TakeInboxParams,
    accept_new_release,
)
from provisioning.core.application.hosting.background.retry import HostingRetry, RetryOutcome
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    AcceptHostingEventHandler,
)
from provisioning.core.application.hosting.ports import HostingIntent
from provisioning.core.application.ports.site_cluster import TenantProject
from provisioning.core.domain import CommandKind, SourcePublished
from provisioning.infrastructure.postgres import HostingIntakeStore, PgReleaseIntakeStore
from provisioning.infrastructure.postgres.hosting_intake import PgHostingHistory
from provisioning.infrastructure.postgres.tenant_projects import PgTenantProjects

pytestmark = pytest.mark.asyncio


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@pytest_asyncio.fixture
async def engine(db: Database) -> AsyncIterator[AsyncEngine]:
    async with role_engine(db, ROLE_HOSTING_INTAKE) as found:
        yield found


@pytest.fixture
def intake(engine: AsyncEngine) -> AcceptOwnerStatusHandler:
    return AcceptOwnerStatusHandler(
        store=HostingIntakeStore(engine, TIMEOUTS),
        clock=_Clock(),
        ids=FakeIds(),
        command_deadline=timedelta(hours=1),
        audit=NullAuditLog(),
        logger=logging.getLogger("test.owner_status"),
    )


def hosting_of(db: Database, project: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT hosting_status::text, offline_reasons::text[], owner_status_version,"
        " last_command_number FROM provisioning.project_hosting WHERE project_id = %s",
        project,
    )


def subdomain_of(db: Database, project: UUID) -> object:
    return scalar(
        db, "SELECT subdomain FROM provisioning.project_hosting WHERE project_id = %s", project
    )


def hosting_rows(db: Database, project: UUID) -> object:
    return scalar(
        db, "SELECT count(*) FROM provisioning.project_hosting WHERE project_id = %s", project
    )


def hosting_commands(db: Database, project: UUID) -> object:
    return scalar(
        db,
        "SELECT count(*) FROM provisioning.commands WHERE project_id = %s"
        " AND kind IN ('suspend_hosting', 'resume_hosting')",
        project,
    )


async def test_an_owner_suspend_is_accepted_with_its_version(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=2)))
    result = await intake.handle(
        AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 3, "shop")
    )
    assert result.outcome is OwnerStatusOutcome.ACCEPTED
    assert result.accepted is not None
    command = result.accepted.command
    assert row_of(
        db,
        "SELECT kind::text, idempotency_key, hosting_reason::text, command_number,"
        " requested_by_subject FROM provisioning.commands WHERE command_id = %s",
        command.id,
    ) == ("suspend_hosting", "owner-status:3", "user_suspended", 3, None)
    assert (
        scalar(
            db,
            "SELECT correlation_id FROM provisioning_ops.command_dispatch WHERE command_id = %s",
            command.id,
        )
        == "owner-status:3"
    )
    assert hosting_of(db, project) == ("online", [], 3, 3)  # the worker takes it offline


async def test_owner_status_version_never_decreases(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))
    newer = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.ACTIVE, 3, "shop"))
    assert newer.outcome is OwnerStatusOutcome.ACCEPTED
    older = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 2, "shop"))
    assert older.outcome is OwnerStatusOutcome.STALE and older.accepted is None
    assert hosting_of(db, project) == ("online", [], 3, 1)
    assert hosting_commands(db, project) == 1


async def test_no_hosting_row_inserts_one_and_no_command(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-29.3: no hosting row inserts one and no command."""
    suspended, active = uuid4(), uuid4()
    first = await intake.handle(
        AcceptOwnerStatusCommand(suspended, OwnerStatus.SUSPENDED, 1, "shop-a")
    )
    assert first.outcome is OwnerStatusOutcome.ROW_INSERTED and first.accepted is None
    assert hosting_of(db, suspended) == ("offline", ["user_suspended"], 1, 0)
    second = await intake.handle(AcceptOwnerStatusCommand(active, OwnerStatus.ACTIVE, 4, "shop-b"))
    assert second.outcome is OwnerStatusOutcome.ROW_INSERTED
    assert hosting_of(db, active) == ("online", [], 4, 0)
    # Notion: the event carries the sub-domain, so the row it inserts holds it (migration 00020).
    assert subdomain_of(db, suspended) == "shop-a"
    assert subdomain_of(db, active) == "shop-b"
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 0
    # The same event again: the row holds its version, so it changes nothing.
    again = await intake.handle(
        AcceptOwnerStatusCommand(suspended, OwnerStatus.SUSPENDED, 1, "shop")
    )
    assert again.outcome is OwnerStatusOutcome.STALE
    # An older event after the row an event inserted is ignored too.
    older = await intake.handle(AcceptOwnerStatusCommand(active, OwnerStatus.SUSPENDED, 3, "shop"))
    assert older.outcome is OwnerStatusOutcome.STALE
    assert hosting_of(db, active) == ("online", [], 4, 0)


async def test_row_inserted_by_owner_status_survives_release_intake(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    project = uuid4()
    inserted = await intake.handle(
        AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 1, "shop")
    )
    assert inserted.outcome is OwnerStatusOutcome.ROW_INSERTED
    event = SourcePublished(
        project_id=project, generation_request_id=uuid4(), repository_id=77, git_commit_sha=sha("c")
    )
    take = TakeInboxParams(batch_size=10, max_per_project=5, visibility=timedelta(seconds=30))
    async with role_engine(db, ROLE_INTAKE) as release_engine:
        releases = PgReleaseIntakeStore(release_engine, TIMEOUTS, max_attempts=5)
        await releases.intake(IntakeParams(event, 10))
        (entry,) = (await releases.take_due(take)).entries
        result = await releases.accept_release(
            ReleaseAcceptanceParams(entry.take(), uuid4(), uuid4()),
            lambda facts: accept_new_release(facts, subdomain="other"),
        )
    assert result.created
    assert hosting_of(db, project) == ("offline", ["user_suspended"], 1, 0)
    assert subdomain_of(db, project) == "shop"  # copied once, never changed


async def test_owner_status_and_its_command_commit_together(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """A command in flight refuses the acceptance; the version is not recorded without it, so
    the event can be redelivered and applied later."""
    project, built, deploy = uuid4(), uuid4(), uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "built", release_id=built)),
        (HOSTING, hosting_row(project, number=1, owner_status_version=1)),
        (
            COMMANDS,
            command_row(
                project, CommandKind.DEPLOY, 1, "running", release=built, command_id=deploy
            ),
        ),
        (DISPATCH, dispatch_row(deploy)),
    )
    with pytest.raises(OperationInProgress) as raised:
        await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 2, "shop"))
    assert raised.value.command_id == deploy
    assert hosting_of(db, project) == ("online", [], 1, 1)
    assert hosting_commands(db, project) == 0


async def test_replay_returns_the_stored_command(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))
    first = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 5, "shop"))
    again = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 5, "shop"))
    assert again.outcome is OwnerStatusOutcome.REPLAYED
    assert first.accepted is not None and again.accepted is not None
    assert again.accepted.replayed and again.accepted.command.id == first.accepted.command.id
    assert hosting_commands(db, project) == 1
    assert hosting_of(db, project)[2] == 5


async def test_same_version_other_status_is_stale(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """Decision 0023 G17 (Notion: "an older event changes nothing, so the newest wins"): the
    version is checked on the locked row first, so "active" at the version of a stored suspend
    is stale, not a conflict on the key ``owner-status:5``, and it writes nothing."""
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))
    first = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 5, "shop"))
    assert first.outcome is OwnerStatusOutcome.ACCEPTED
    before = hosting_of(db, project)
    other = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.ACTIVE, 5, "shop"))
    assert other == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    assert hosting_of(db, project) == before
    assert hosting_commands(db, project) == 1


async def test_older_version_is_stale(db: Database, intake: AcceptOwnerStatusHandler) -> None:
    """Version 2 (suspend) then version 3 (resume) were applied; a version 2 "active" finds the
    suspend's key but is older than the row's version 3, so it is stale and writes nothing."""
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))
    suspend = await intake.handle(
        AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 2, "shop")
    )
    assert suspend.outcome is OwnerStatusOutcome.ACCEPTED
    seed(
        db,
        (
            "UPDATE provisioning.commands SET status = 'succeeded' WHERE project_id = %s",
            [project],
        ),
    )
    resume = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.ACTIVE, 3, "shop"))
    assert resume.outcome is OwnerStatusOutcome.ACCEPTED
    before = hosting_of(db, project)
    older = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.ACTIVE, 2, "shop"))
    assert older == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    older = await intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 1, "shop"))
    assert older == OwnerStatusResult(OwnerStatusOutcome.STALE, None)
    assert hosting_of(db, project) == before
    assert hosting_commands(db, project) == 2


async def test_concurrent_events_on_a_new_project_keep_the_newest(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """Two events race to insert the row: one inserts it, the other then follows the stale
    rule on the locked row, so the newest version wins whichever order they land in."""
    project = uuid4()
    results = await asyncio.gather(
        intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 1, "shop")),
        intake.handle(AcceptOwnerStatusCommand(project, OwnerStatus.ACTIVE, 2, "shop")),
    )
    outcomes = {r.outcome for r in results}
    assert OwnerStatusOutcome.ROW_INSERTED in outcomes
    assert hosting_of(db, project)[2] == 2


class _NewerVersionAfterRead(PgHostingHistory):
    """The history read, then event 4's command finished before the retry is accepted."""

    def __init__(self, engine: AsyncEngine, db: Database, project: UUID) -> None:
        super().__init__(engine, TIMEOUTS)
        self._db = db
        self._project = project

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        intents = await super().latest_hosting_intents(project_id)
        seed_rows(
            self._db,
            (
                COMMANDS,
                command_row(
                    self._project, CommandKind.RESUME_HOSTING, 2, "succeeded", key="owner-status:4"
                ),
            ),
        )
        with self._db.admin() as conn:
            conn.execute(
                "UPDATE provisioning.project_hosting SET owner_status_version = 4,"
                " last_command_number = 2 WHERE project_id = %s",
                (self._project,),
            )
        return intents


async def test_a_stale_owner_status_retry_is_refused_under_the_lock(
    db: Database, engine: AsyncEngine
) -> None:
    """Task 5 review: the retry read version 3 as current, version 4 landed before the
    acceptance; the decision on the locked row refuses ``owner-status:3.retry-1``."""
    project = uuid4()
    failed = uuid4()
    seed_rows(
        db,
        (HOSTING, hosting_row(project, number=1, owner_status_version=3)),
        (
            COMMANDS,
            command_row(
                project,
                CommandKind.SUSPEND_HOSTING,
                1,
                "failed",
                key="owner-status:3",
                command_id=failed,
            ),
        ),
        (DISPATCH, dispatch_row(failed, finished_at=at(1))),
    )
    retry = HostingRetry(
        history=_NewerVersionAfterRead(engine, db, project),
        intake=AcceptHostingEventHandler(
            store=HostingIntakeStore(engine, TIMEOUTS),
            clock=_Clock(),
            ids=FakeIds(),
            command_deadline=timedelta(hours=1),
        ),
    )
    result = await retry.retry(project)
    assert result.outcome is RetryOutcome.NOT_NEEDED
    assert hosting_commands(db, project) == 2  # the failed one and event 4's; no retry
    assert (
        scalar(
            db,
            "SELECT count(*) FROM provisioning.commands WHERE idempotency_key LIKE %s",
            "owner-status:3.retry-%",
        )
        == 0
    )


async def test_fr54_a_subdomain_another_project_holds_is_subdomain_taken_and_writes_nothing(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-54.1 (TR-53; Task 12 security review S2): an event that would give a second hosting
    row a sub-domain another project holds is refused as ``SubdomainTaken``, a permanent refusal,
    and nothing is written. The check is the code's: no index refuses it."""
    owner, intruder = uuid4(), uuid4()
    first = await intake.handle(
        AcceptOwnerStatusCommand(owner, OwnerStatus.ACTIVE, 1, "victim-shop")
    )
    assert first.outcome is OwnerStatusOutcome.ROW_INSERTED
    with pytest.raises(SubdomainTaken) as raised:
        await intake.handle(
            AcceptOwnerStatusCommand(intruder, OwnerStatus.ACTIVE, 1, "victim-shop")
        )
    assert "victim-shop" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert scalar(db, "SELECT count(*) FROM provisioning.project_hosting") == 1
    assert subdomain_of(db, owner) == "victim-shop"
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 0


async def test_fr54_no_unique_index_holds_the_subdomain(db: Database) -> None:
    """FR-54 (TR-53; owner rule of 27 October 2026): the database matches Notion "5 ·
    Provisioning" exactly, so the only unique index of ``project_hosting`` is its primary key on
    ``project_id``; the reverted migration 00024's index on ``subdomain`` does not exist."""
    with db.admin() as conn:
        found = conn.execute(
            "SELECT i.indexrelid::regclass::text, array_agg(a.attname::text ORDER BY a.attname)"
            " FROM pg_index i JOIN pg_attribute a"
            " ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)"
            " WHERE i.indrelid = 'provisioning.project_hosting'::regclass AND i.indisunique"
            " GROUP BY 1"
        ).fetchall()
    assert [tuple(r) for r in found] == [("provisioning.project_hosting_pkey", ["project_id"])]


# --- FR-54 races (Task 12 security re-review N1): inserts of one sub-domain at once -----------

_RACE_ROUNDS = 12  # projects per test
_BARRIER_KEY = 7_340_012


def _install_insert_barrier(db: Database) -> None:
    """A test-only BEFORE INSERT trigger on the hosting table that waits on a shared advisory
    lock. The test holds the lock exclusively until every insert of a round waits, either on it
    or on the sub-domain lock (TR-53) behind the one that does, then lets them all go at once."""
    with db.admin() as conn:
        conn.execute(
            "CREATE FUNCTION provisioning.test_insert_barrier() RETURNS trigger"
            " LANGUAGE plpgsql AS $$ BEGIN"
            f" PERFORM pg_advisory_xact_lock_shared({_BARRIER_KEY}); RETURN NEW; END $$"
        )
        conn.execute("GRANT EXECUTE ON FUNCTION provisioning.test_insert_barrier() TO PUBLIC")
        conn.execute(
            "CREATE TRIGGER test_insert_barrier BEFORE INSERT ON provisioning.project_hosting"
            " FOR EACH ROW EXECUTE FUNCTION provisioning.test_insert_barrier()"
        )


async def _release_together(db: Database, waiters: int, *calls: Awaitable[object]) -> list[object]:
    """Run ``calls`` concurrently; once ``waiters`` advisory lock requests of this database wait
    (the barrier, or the sub-domain lock behind it), all go on. Each call's result or exception,
    in order."""
    with db.admin() as gate:
        gate.execute("SELECT pg_advisory_lock(%s)", (_BARRIER_KEY,))
        try:
            running = asyncio.gather(*calls, return_exceptions=True)
            for _ in range(1000):
                found = gate.execute(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                    " AND database = (SELECT oid FROM pg_database"
                    " WHERE datname = current_database())"
                ).fetchone()
                if found is not None and found[0] >= waiters:
                    break
                await asyncio.sleep(0.005)
            else:
                pytest.fail("the inserts never reached the barrier")
        finally:
            gate.execute("SELECT pg_advisory_unlock(%s)", (_BARRIER_KEY,))
        return list(await running)


async def _taken_entries(
    releases: PgReleaseIntakeStore, project: UUID, count: int
) -> tuple[InboxEntry, ...]:
    """``count`` source-published events of ``project``, stored and taken."""
    for _ in range(count):
        event = SourcePublished(
            project_id=project,
            generation_request_id=uuid4(),
            repository_id=77,
            git_commit_sha=sha("c"),
        )
        await releases.intake(IntakeParams(event, 10))
    take = TakeInboxParams(batch_size=50, max_per_project=count, visibility=timedelta(seconds=30))
    entries = tuple(
        e for e in (await releases.take_due(take)).entries if e.event.project_id == project
    )
    assert len(entries) == count
    return entries


async def _accept_new(
    releases: PgReleaseIntakeStore, entry: InboxEntry, subdomain: str
) -> ReleaseAcceptanceResult:
    return await releases.accept_release(
        ReleaseAcceptanceParams(entry.take(), uuid4(), uuid4()),
        lambda facts: accept_new_release(facts, subdomain=subdomain),
    )


def _only_business_refusals(results: list[object]) -> None:
    """Only business refusals: a release intake's row can make the second owner-status event
    a command while the first one's is in flight (``OperationInProgress``)."""
    errors = [type(r).__name__ for r in results if isinstance(r, BaseException)]
    assert set(errors) <= {"OperationInProgress"}


async def test_fr54_concurrent_first_inserts_of_one_project_are_never_subdomain_taken(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-54.2 (TR-53; re-review N1, decision 0023 G17): inserts of one new project's hosting
    row at once, owner-status events and release events mixed, are never ``SubdomainTaken``: the
    sub-domain lock lets one insert at a time, the next one finds the project's own row, and
    every one goes on with the row that won."""
    _install_insert_barrier(db)
    async with role_engine(db, ROLE_INTAKE) as release_engine:
        releases = PgReleaseIntakeStore(release_engine, TIMEOUTS, max_attempts=5)
        for n in range(_RACE_ROUNDS):
            project, subdomain = uuid4(), f"race-{n}"
            first, second = await _taken_entries(releases, project, 2)
            results = await _release_together(
                db,
                4,
                intake.handle(
                    AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 1, subdomain)
                ),
                intake.handle(
                    AcceptOwnerStatusCommand(project, OwnerStatus.SUSPENDED, 2, subdomain)
                ),
                _accept_new(releases, first, subdomain),
                _accept_new(releases, second, subdomain),
            )
            _only_business_refusals(results)
            assert subdomain_of(db, project) == subdomain
            assert (
                scalar(
                    db, "SELECT count(*) FROM provisioning.releases WHERE project_id = %s", project
                )
                == 2
            )


async def test_fr54_concurrent_first_inserts_of_two_projects_leave_one_holder(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-54.3 (TR-53; re-review N1): owner-status events of two projects race for one
    sub-domain. Exactly one row holds it; the other event is ``SubdomainTaken`` and writes
    nothing. Without the sub-domain lock both checks could pass together."""
    _install_insert_barrier(db)
    for n in range(_RACE_ROUNDS):
        first, second, subdomain = uuid4(), uuid4(), f"contested-{n}"
        results = await _release_together(
            db,
            2,
            intake.handle(AcceptOwnerStatusCommand(first, OwnerStatus.ACTIVE, 1, subdomain)),
            intake.handle(AcceptOwnerStatusCommand(second, OwnerStatus.ACTIVE, 1, subdomain)),
        )
        taken = [r for r in results if isinstance(r, SubdomainTaken)]
        assert len(taken) == 1
        assert [r for r in results if isinstance(r, BaseException) and r not in taken] == []
        holders = scalar(
            db, "SELECT count(*) FROM provisioning.project_hosting WHERE subdomain = %s", subdomain
        )
        assert holders == 1
        loser = second if isinstance(results[1], SubdomainTaken) else first
        assert hosting_rows(db, loser) == 0


async def test_fr54_an_owner_status_event_after_a_release_row_is_subdomain_taken(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-54.1 (TR-53): a row the release intake stored holds its sub-domain too; the hosting
    intake, which may read ``subdomain``, refuses another project's event for it."""
    owner, intruder = uuid4(), uuid4()
    async with role_engine(db, ROLE_INTAKE) as release_engine:
        releases = PgReleaseIntakeStore(release_engine, TIMEOUTS, max_attempts=5)
        (entry,) = await _taken_entries(releases, owner, 1)
        await _accept_new(releases, entry, "victim-shop")
    with pytest.raises(SubdomainTaken):
        await intake.handle(
            AcceptOwnerStatusCommand(intruder, OwnerStatus.ACTIVE, 1, "victim-shop")
        )
    assert hosting_rows(db, intruder) == 0
    assert subdomain_of(db, owner) == "victim-shop"


async def test_fr54_a_release_row_with_a_taken_subdomain_gets_no_tenant(
    db: Database, intake: AcceptOwnerStatusHandler
) -> None:
    """FR-54.4 (TR-53): the release intake's login may read ``project_id`` only (migration 00022),
    so it cannot see who holds a sub-domain and stores the row. The bootstrap, which may read
    ``subdomain`` (migration 00023), then marks the project taken, so it labels no namespace with
    the sub-domain and the route-shape admission policy admits no route for it."""
    owner, intruder = uuid4(), uuid4()
    first = await intake.handle(
        AcceptOwnerStatusCommand(owner, OwnerStatus.ACTIVE, 1, "victim-shop")
    )
    assert first.outcome is OwnerStatusOutcome.ROW_INSERTED
    async with role_engine(db, ROLE_INTAKE) as release_engine:
        releases = PgReleaseIntakeStore(release_engine, TIMEOUTS, max_attempts=5)
        (entry,) = await _taken_entries(releases, intruder, 1)
        await _accept_new(releases, entry, "victim-shop")
    assert subdomain_of(db, intruder) == "victim-shop"
    async with role_engine(db, ROLE_BOOTSTRAP) as bootstrap_engine:
        found = await PgTenantProjects(bootstrap_engine, TIMEOUTS).projects_needing_tenant(
            after=None, limit=10
        )
    assert TenantProject(intruder, "victim-shop", subdomain_taken=True) in found
