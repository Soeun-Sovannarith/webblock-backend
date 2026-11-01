"""Migration 00016: the usage outbox and the diagnostic read of the inbox (decision 0014 section 21
answer 2, section 23 item 4; the only schema change of wave 6).

- ``provisioning_ops.usage_outbox``: its insert guard (a pending, fresh row for a live release) and
  update guard (the event never changes, attempts never decrease, sent is final), its checks and
  its grants (the worker reads, inserts and updates exactly the publisher's columns; nobody
  deletes);
- ``provisioning_api`` may run W6-D's diagnostic query on ``source_published_events`` and read
  nothing else of it (not the commit, not the repository);
- exactly those privileges changed; the upgrade from 00015 keeps every row; the migration runs only
  as the migrator and has no Down section.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import psycopg
import pytest
from tests.integration._pg import (
    GOOSE,
    MIGRATIONS_DIR,
    ROLE_API,
    ROLE_BOOTSTRAP,
    ROLE_BUILD,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_WORKER,
    Database,
    migrate_with_backfill,
    q,
    refused,
    scalar,
    seed,
)
from tests.integration._rows import at, inbox, insert, project_state, release, uid, world
from tests.integration.test_schema_00013 import privileges
from tests.integration.test_schema_00014 import snapshot

type DbFactory = Callable[[int | None], Database]

LIVE = uid("release:uo-live")
BUILT = uid("release:uo-built")
PROJECT = uid("project:uo")
AT = datetime(2026, 10, 9, 10, tzinfo=UTC)

INSERT_EVENT = (
    "INSERT INTO provisioning_ops.usage_outbox (release_id, project_id, event_key, completed_at,"
    " status, attempts, next_attempt_at, last_failure_code, sent_at, created_at, updated_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

DIAGNOSTIC_QUERY = (
    "SELECT status, outcome_code, release_id, attempts, mismatched_repeat_count, received_at,"
    " finished_at FROM provisioning_ops.source_published_events"
    " WHERE project_id = %s AND generation_request_id = %s"
)


def event_values(
    release_id: UUID = LIVE,
    *,
    status: str = "pending",
    attempts: int = 0,
    failure: str | None = None,
    sent_at: datetime | None = None,
    key: str | None = None,
) -> tuple[object, ...]:
    return (
        release_id,
        PROJECT,
        key or f"publish-completed-{release_id}",
        AT,
        status,
        attempts,
        AT,
        failure,
        sent_at,
        AT,
        AT,
    )


@pytest.fixture
def outbox_db(new_db: DbFactory) -> Database:
    database = new_db(None)
    seed(
        database,
        # completed_at is the release's promoted_at (00022).
        insert("provisioning.releases", release("uo-live", "uo", "live", promoted_at=AT), new=True),
        insert("provisioning.releases", release("uo-built", "uo", "built"), new=True),
        insert(
            "provisioning.project_release_state", project_state("uo", active="uo-live"), new=True
        ),
    )
    return database


# --- the insert guard --------------------------------------------------------------------------


def test_the_worker_inserts_a_fresh_event_of_a_live_release(outbox_db: Database) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        conn.execute(q(INSERT_EVENT), event_values())
    with outbox_db.admin() as conn:
        assert scalar(conn, "SELECT status::text FROM provisioning_ops.usage_outbox") == "pending"


@pytest.mark.parametrize(
    "values",
    [
        event_values(status="sent", sent_at=AT),
        event_values(attempts=1),
        event_values(failure="publish_failed"),
        event_values(BUILT),
    ],
    ids=["sent", "attempted", "failed-before", "not-live"],
)
def test_the_insert_guard_refuses_what_is_not_a_fresh_event_of_a_live_release(
    outbox_db: Database, values: tuple[object, ...]
) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="usage_outbox_insert_shape", table="usage_outbox"):
            conn.execute(q(INSERT_EVENT), values)


def test_the_key_must_be_the_releases_key(outbox_db: Database) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="usage_outbox_event_key_check", table="usage_outbox"):
            conn.execute(q(INSERT_EVENT), event_values(key=f"publish-completed-{BUILT}"))


def test_a_release_of_another_project_is_refused_by_the_foreign_key(outbox_db: Database) -> None:
    other = uid("project:uo-other")
    values = list(event_values())
    values[1] = other
    with outbox_db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="usage_outbox_release_fk", sqlstate="23503"):
            conn.execute(q(INSERT_EVENT), values)


def test_one_event_per_release(outbox_db: Database) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        conn.execute(q(INSERT_EVENT), event_values())
        with refused(conn, constraint="usage_outbox_pkey", sqlstate="23505"):
            conn.execute(q(INSERT_EVENT), event_values())


# --- the update guard --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE provisioning_ops.usage_outbox SET attempts = attempts - 1",
        "UPDATE provisioning_ops.usage_outbox SET status = 'pending', sent_at = NULL",
        "UPDATE provisioning_ops.usage_outbox SET sent_at = sent_at + interval '1 second'",
        "UPDATE provisioning_ops.usage_outbox SET completed_at = completed_at + interval '1 s'",
    ],
)
def test_the_update_guard_keeps_the_event_and_a_sent_row_final(
    outbox_db: Database, statement: str
) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        conn.execute(q(INSERT_EVENT), event_values())
        conn.execute(
            q(
                "UPDATE provisioning_ops.usage_outbox SET attempts = 2,"
                " status = 'sent', sent_at = now(), updated_at = now()"
            )
        )
    # Owner, so the column grants do not hide the guard.
    with outbox_db.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="usage_outbox_update_shape", table="usage_outbox"):
            conn.execute(q(statement))


def test_the_worker_takes_fails_and_sends_a_row(outbox_db: Database) -> None:
    with outbox_db.login(ROLE_WORKER) as conn:
        conn.execute(q(INSERT_EVENT), event_values())
        conn.execute(
            q(
                "UPDATE provisioning_ops.usage_outbox SET attempts = attempts + 1,"
                " next_attempt_at = now(), last_failure_code = 'publish_failed', updated_at = now()"
            )
        )
        conn.execute(
            q(
                "UPDATE provisioning_ops.usage_outbox SET status = 'sent', sent_at = now(),"
                " last_failure_code = NULL, updated_at = now()"
            )
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(q("UPDATE provisioning_ops.usage_outbox SET event_key = 'x'"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(q("DELETE FROM provisioning_ops.usage_outbox"))


@pytest.mark.parametrize("role", [ROLE_API, ROLE_HOSTING_INTAKE, ROLE_INTAKE, ROLE_OPERATOR])
def test_no_other_role_reads_or_writes_the_outbox(outbox_db: Database, role: str) -> None:
    with outbox_db.login(role) as conn:
        for statement, params in (
            ("SELECT * FROM provisioning_ops.usage_outbox", None),
            (INSERT_EVENT, event_values()),
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(q(statement), params)


# --- the diagnostic read of the inbox ------------------------------------------------------------


def test_the_api_runs_the_diagnostic_query_and_reads_nothing_else(outbox_db: Database) -> None:
    row = inbox("uo-event", "uo", "failed", outcome_code="repository_unconfirmed", finished_at=AT)
    seed(outbox_db, insert("provisioning_ops.source_published_inbox", row, new=True))
    with outbox_db.login(ROLE_API) as conn:
        found = conn.execute(
            q(DIAGNOSTIC_QUERY), (row["project_id"], row["generation_request_id"])
        ).fetchall()
        assert [(r[0], r[1], r[3]) for r in found] == [("failed", "repository_unconfirmed", 0)]
        for column in (
            "git_commit_sha",
            "repository_id",
            "next_attempt_at",
            "updated_at",
            "last_mismatched_repeat_at",
            "max_attempts",
            "last_manual_retry_by",
            "last_manual_retry_at",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(
                        q(
                            f"SELECT {column} FROM provisioning_ops.source_published_events"  # noqa: S608 - a fixed column name
                        )
                    )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute(q("UPDATE provisioning_ops.source_published_events SET attempts = 0"))


# --- exactly the listed privileges changed -------------------------------------------------------

WORKER_GAINS = (
    {
        ("usage_outbox", column, "SELECT")
        for column in (
            "release_id",
            "project_id",
            "event_key",
            "completed_at",
            "status",
            "attempts",
            "next_attempt_at",
            "last_failure_code",
            "sent_at",
            "created_at",
            "updated_at",
        )
    }
    | {
        ("usage_outbox", column, "INSERT")
        for column in (
            "release_id",
            "project_id",
            "event_key",
            "completed_at",
            "status",
            "attempts",
            "next_attempt_at",
            "last_failure_code",
            "sent_at",
            "created_at",
            "updated_at",
        )
    }
    | {
        ("usage_outbox", column, "UPDATE")
        for column in (
            "status",
            "attempts",
            "next_attempt_at",
            "last_failure_code",
            "sent_at",
            "updated_at",
        )
    }
)
API_GAINS = {
    ("source_published_events", column, "SELECT")
    for column in (
        "project_id",
        "generation_request_id",
        "status",
        "outcome_code",
        "release_id",
        "attempts",
        "mismatched_repeat_count",
        "received_at",
        "finished_at",
    )
}


def test_exactly_the_listed_privileges_changed(new_db: DbFactory) -> None:
    database = new_db(15)
    roles = (
        ROLE_API,
        ROLE_WORKER,
        ROLE_INTAKE,
        ROLE_BUILD,
        ROLE_OPERATOR,
        ROLE_MIGRATOR,
        ROLE_HOSTING_INTAKE,
        ROLE_BOOTSTRAP,
        "provisioning_payment_intake",
        "public",
    )
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in roles}
    database.migrate(16)  # 00017 changes the intake's grants on purpose; it has its own test
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in roles}
    assert after[ROLE_WORKER] - before[ROLE_WORKER] == WORKER_GAINS
    assert after[ROLE_API] - before[ROLE_API] == API_GAINS
    for role in roles:
        assert before[role] <= after[role], role
        if role not in {ROLE_WORKER, ROLE_API, ROLE_MIGRATOR}:
            assert after[role] == before[role], role
    # Nobody but the owner may delete or truncate the outbox.
    for role in roles:
        if role != ROLE_MIGRATOR:
            assert ("usage_outbox", "*", "DELETE") not in after[role], role
            assert ("usage_outbox", "*", "TRUNCATE") not in after[role], role


# --- the migration itself ------------------------------------------------------------------------


def test_there_is_no_down_section() -> None:
    text = (MIGRATIONS_DIR / "00016_usage_outbox.sql").read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text


def test_the_migration_runs_only_as_the_migrator(new_db: DbFactory) -> None:
    database = new_db(15)
    env = {
        "PATH": "/usr/bin:/bin",
        "GOOSE_DRIVER": "postgres",
        "GOOSE_DBSTRING": database.harness.server.goose_url(database.name).split("&options=")[0],
        "GOOSE_MIGRATION_DIR": str(MIGRATIONS_DIR),
    }
    done = subprocess.run(  # noqa: S603 - fixed argv built from constants, no shell
        [str(GOOSE), "up"], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode != 0
    assert "must run as role provisioning_migrator" in done.stdout + done.stderr
    with database.admin() as conn:
        assert scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied") == 15
        assert scalar(conn, "SELECT to_regclass('provisioning_ops.usage_outbox') IS NULL")


def test_upgrade_from_00015_keeps_every_row(new_db: DbFactory) -> None:
    database = new_db(15)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    with database.admin() as conn:
        before = snapshot(conn)
    database.migrate(16)
    with database.admin() as conn:
        after = snapshot(conn)
        version = scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied")
        assert scalar(conn, "SELECT count(*) FROM provisioning_ops.usage_outbox") == 0
    assert version == 16
    for table, rows in before.items():
        assert rows, f"the representative rows hold no row of {table}"
        assert json.dumps(after[table], sort_keys=True) == json.dumps(rows, sort_keys=True), (
            f"rows of {table} changed by the upgrade"
        )


def test_an_upgraded_database_takes_an_event_for_its_live_release(new_db: DbFactory) -> None:
    database = new_db(15)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    migrate_with_backfill(database)
    live = uid("release:p1-live")
    with database.login(ROLE_WORKER) as conn:
        conn.execute(
            q(INSERT_EVENT),
            (
                live,
                uid("project:p1"),
                f"publish-completed-{live}",
                at(60),  # the release's promoted_at (00022)
                "pending",
                0,
                AT,
                None,
                None,
                AT,
                AT,
            ),
        )
    with database.admin() as conn:
        assert cast(int, scalar(conn, "SELECT count(*) FROM provisioning_ops.usage_outbox")) == 1
