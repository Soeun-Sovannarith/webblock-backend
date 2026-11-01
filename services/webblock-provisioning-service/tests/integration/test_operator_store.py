"""The operator's inbox store on PostgreSQL 18 (decision 0014 section 21 ruling 11; the re-drive
of migration 00010, renamed by 00011).

The store runs on a login that is a member of ``provisioning_operator`` alone. It lists the failed
and pending events, reopens a failed one with a larger budget (the update guard admits that only
from an operator), makes a pending one due, and refuses a final one. The worker's login, and the
API's, are refused the re-drive by the grants.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from tests.integration._pg import ROLE_API, ROLE_INTAKE, ROLE_OPERATOR, ROLE_WORKER, Database
from tests.integration.test_store_support import (
    EVENTS,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    Row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    seed_rows,
)

from provisioning.core.application import NotFound, SourceEventStatus
from provisioning.core.domain import InvalidRequester, InvalidSubdomain
from provisioning.infrastructure.postgres import DatabaseError
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.operator_store import (
    InboxState,
    NotRedrivable,
    PgOperatorStore,
    RedriveKind,
)

pytestmark = pytest.mark.asyncio

type DbFactory = Callable[[int | None], Database]

T0 = datetime(2026, 10, 9, 8, tzinfo=UTC)


def event_row(
    project: UUID, status: str, *, attempts: int = 3, max_attempts: int = 3, **over: object
) -> Row:
    final = status != "pending"
    row: Row = {
        "project_id": project,
        "generation_request_id": uuid4(),
        "repository_id": 4242,
        "git_commit_sha": "a" * 40,
        "status": status,
        "attempts": attempts,
        "next_attempt_at": T0 + timedelta(hours=1),
        "outcome_code": "repository_unconfirmed" if status in ("failed", "rejected") else None,
        "release_id": None,
        "mismatched_repeat_count": 0,
        "last_mismatched_repeat_at": None,
        "received_at": T0,
        "updated_at": T0,
        "finished_at": T0 if final else None,
        "max_attempts": max_attempts,
        "last_manual_retry_by": None,
        "last_manual_retry_at": None,
    }
    row.update(over)
    return row


def req(row: Row) -> UUID:
    value = row["generation_request_id"]
    assert isinstance(value, UUID)
    return value


@pytest_asyncio.fixture
async def operator(db: Database) -> AsyncIterator[PgOperatorStore]:
    async with role_engine(db, ROLE_OPERATOR) as engine:
        yield PgOperatorStore(engine, TIMEOUTS)


def state_of(db: Database, row: Row) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT status::text, outcome_code, finished_at IS NULL, attempts, max_attempts,"
        " next_attempt_at <= clock_timestamp(), last_manual_retry_by,"
        " last_manual_retry_at IS NOT NULL"
        " FROM provisioning_ops.source_published_events"
        " WHERE project_id = %s AND generation_request_id = %s",
        row["project_id"],
        row["generation_request_id"],
    )


async def test_the_list_holds_failed_then_pending_rows_with_their_state(
    db: Database, operator: PgOperatorStore
) -> None:
    project = uuid4()
    failed = event_row(project, "failed")
    parked = event_row(project, "pending", received_at=T0 - timedelta(minutes=1))
    waiting = event_row(project, "pending", attempts=1, received_at=T0 - timedelta(minutes=2))
    released = uuid4()
    accepted = event_row(project, "accepted", release_id=released)
    seed_rows(
        db,
        (
            RELEASES,
            release_row(
                project,
                "accepted",
                release_id=released,
                generation_request_id=accepted["generation_request_id"],
            ),
        ),
        (HOSTING, hosting_row(project)),
        (EVENTS, failed),
        (EVENTS, parked),
        (EVENTS, waiting),
        (EVENTS, accepted),
    )
    rows = await operator.list_stuck(10)
    assert [(r.generation_request_id, r.state) for r in rows] == [
        (failed["generation_request_id"], InboxState.FAILED),
        (waiting["generation_request_id"], InboxState.WAITING),
        (parked["generation_request_id"], InboxState.PARKED),
    ]
    assert len(await operator.list_stuck(1)) == 1


async def test_a_failed_event_is_reopened_with_a_larger_budget_and_recorded(
    db: Database, operator: PgOperatorStore
) -> None:
    project = uuid4()
    failed = event_row(project, "failed", attempts=5, max_attempts=3)
    seed_rows(db, (EVENTS, failed))
    done = await operator.redrive(project, req(failed), by="alice", extra_attempts=2)
    assert done.kind is RedriveKind.REOPENED
    assert done.before.status is SourceEventStatus.FAILED
    assert done.after.status is SourceEventStatus.PENDING
    # The budget grows from what the row used (5), not from its old maximum (3).
    assert state_of(db, failed) == ("pending", None, True, 5, 7, True, "alice", True)
    # A second re-drive of the now pending row only makes it due again.
    again = await operator.redrive(project, req(failed), by="alice", extra_attempts=2)
    assert again.kind is RedriveKind.MADE_DUE
    assert state_of(db, failed)[3:5] == (5, 7)


async def test_a_pending_event_is_made_due_now(db: Database, operator: PgOperatorStore) -> None:
    project = uuid4()
    waiting = event_row(project, "pending", attempts=1)
    seed_rows(db, (EVENTS, waiting))
    done = await operator.redrive(project, req(waiting), by="bob", extra_attempts=3)
    assert done.kind is RedriveKind.MADE_DUE
    assert state_of(db, waiting) == ("pending", None, True, 1, 3, True, None, False)


async def test_a_final_or_missing_event_is_refused(db: Database, operator: PgOperatorStore) -> None:
    project = uuid4()
    rejected = event_row(project, "rejected")
    seed_rows(db, (EVENTS, rejected))
    with pytest.raises(NotRedrivable):
        await operator.redrive(project, req(rejected), by="alice", extra_attempts=1)
    with pytest.raises(NotFound):
        await operator.redrive(project, uuid4(), by="alice", extra_attempts=1)
    with pytest.raises(InvalidRequester):
        await operator.redrive(project, req(rejected), by=" alice", extra_attempts=1)


@pytest.mark.parametrize("group", [ROLE_WORKER, ROLE_API, ROLE_INTAKE])
async def test_another_login_is_refused_the_redrive(db: Database, group: str) -> None:
    """The worker's login (and the API's, and the intake's) cannot re-drive: a failed row needs
    the operator's grants and membership, and the row stays failed."""
    project = uuid4()
    failed = event_row(project, "failed")
    seed_rows(db, (EVENTS, failed))
    async with role_engine(db, group) as engine:
        store = PgOperatorStore(engine, TIMEOUTS)
        with pytest.raises(DatabaseError) as raised:
            await store.redrive(project, req(failed), by="mallory", extra_attempts=1)
    assert raised.value.sqlstate == "42501"
    assert state_of(db, failed)[0] == "failed"


@pytest.mark.parametrize("group", [ROLE_WORKER, ROLE_API])
async def test_the_worker_and_api_logins_cannot_make_a_pending_event_due(
    db: Database, group: str
) -> None:
    """Not even the lighter re-drive (making a pending row due) is open to them. The intake's
    processor may move ``next_attempt_at`` itself, so it is not part of this check."""
    project = uuid4()
    waiting = event_row(project, "pending", attempts=1)
    seed_rows(db, (EVENTS, waiting))
    async with role_engine(db, group) as engine:
        with pytest.raises(DatabaseError) as raised:
            await PgOperatorStore(engine, TIMEOUTS).redrive(
                project, req(waiting), by="mallory", extra_attempts=1
            )
    assert raised.value.sqlstate == "42501"
    unchanged = row_of(
        db,
        "SELECT next_attempt_at FROM provisioning_ops.source_published_events"
        " WHERE project_id = %s AND generation_request_id = %s",
        project,
        req(waiting),
    )
    assert unchanged == (waiting["next_attempt_at"],)


async def test_the_worker_login_cannot_even_list(db: Database) -> None:
    async with role_engine(db, ROLE_WORKER) as engine:
        with pytest.raises(DatabaseError) as raised:
            await PgOperatorStore(engine, TIMEOUTS).list_stuck(10)
    assert raised.value.sqlstate == "42501"


# --- the sub-domain backfill (decision 0021 D8; between migrations 00020 and 00021) ---------------

AT_BACKFILL = datetime(2026, 10, 24, 9, tzinfo=UTC)


@pytest_asyncio.fixture
async def at_00020(new_db: DbFactory) -> AsyncIterator[tuple[Database, PgOperatorStore]]:
    """A database at 00020, where the operator may still fill a sub-domain."""
    database = new_db(20)
    async with role_engine(database, ROLE_OPERATOR) as engine:
        yield database, PgOperatorStore(engine, TIMEOUTS)


def subdomain_of(db: Database, project: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT subdomain, updated_at FROM provisioning.project_hosting WHERE project_id = %s",
        project,
    )


async def test_set_subdomain_only_once(at_00020: tuple[Database, PgOperatorStore]) -> None:
    db, operator = at_00020
    first, second = sorted((uuid4(), uuid4()))
    seed_rows(
        db,
        (HOSTING, hosting_row(first, subdomain=None)),
        (HOSTING, hosting_row(second, subdomain=None)),
    )
    assert await operator.missing_subdomains(10) == (first, second)
    assert await operator.missing_subdomains(1) == (first,)
    assert await operator.set_subdomain(first, "shop", AT_BACKFILL) is True
    assert subdomain_of(db, first) == ("shop", AT_BACKFILL)
    # Set once: a second value, or the same one, changes nothing.
    assert await operator.set_subdomain(first, "other", AT_BACKFILL + timedelta(hours=1)) is False
    assert await operator.set_subdomain(first, "shop", AT_BACKFILL + timedelta(hours=1)) is False
    assert subdomain_of(db, first) == ("shop", AT_BACKFILL)
    # A project without a hosting row.
    assert await operator.set_subdomain(uuid4(), "shop", AT_BACKFILL) is False
    assert await operator.missing_subdomains(10) == (second,)


async def test_set_subdomain_checks_its_arguments(
    at_00020: tuple[Database, PgOperatorStore],
) -> None:
    db, operator = at_00020
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, subdomain=None)))
    with pytest.raises(InvalidSubdomain):
        await operator.set_subdomain(project, "Shop", AT_BACKFILL)
    for limit in (0, 1001):
        with pytest.raises(InvalidArgument):
            await operator.missing_subdomains(limit)
    assert subdomain_of(db, project)[0] is None


@pytest.mark.parametrize("group", [ROLE_WORKER, ROLE_API, ROLE_INTAKE])
async def test_another_login_cannot_set_a_subdomain(new_db: DbFactory, group: str) -> None:
    db = new_db(20)
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, subdomain=None)))
    async with role_engine(db, group) as engine:
        with pytest.raises(DatabaseError) as raised:
            await PgOperatorStore(engine, TIMEOUTS).set_subdomain(project, "shop", AT_BACKFILL)
    assert raised.value.sqlstate == "42501"
    assert subdomain_of(db, project)[0] is None


async def test_at_00021_the_backfill_commands_are_refused(
    db: Database, operator: PgOperatorStore
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, subdomain="shop")))
    with pytest.raises(DatabaseError) as listed:
        await operator.missing_subdomains(10)
    assert listed.value.sqlstate == "42501"
    with pytest.raises(DatabaseError) as raised:
        await operator.set_subdomain(project, "other", AT_BACKFILL)
    assert raised.value.sqlstate == "42501"
