"""Migration 00017: the intake loses the ``sync_domains`` grants of 00015 (decision 0014 section 23,
amendment of 18 October 2026, owner; Notion 5 · Provisioning).

- ``provisioning_intake`` can no longer read commands, steps or the hosting row beyond its key, lock
  or advance the hosting counter, or insert a command or a dispatch row;
- it still runs its release intake: the hosting row ``ON CONFLICT (project_id)``, the release read
  back, the first build step, and its reads of ``releases`` and ``project_hosting.project_id``;
- it holds exactly what it held at 00014, every other role keeps what it held at 00016, and the
  upgrade from 00016 keeps every row;
- the migration runs only as the migrator, refuses without the intake role, and has no Down
  section.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
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
    Conn,
    Database,
    Harness,
    allowed,
    column,
    migrate_with_backfill,
    q,
    scalar,
    seed,
)
from tests.integration._rows import (
    fingerprint,
    insert,
    project_state,
    release,
    sha,
    step,
    uid,
    world,
)
from tests.integration.test_schema_00013 import ACCEPTANCE_PRIVILEGES, privileges
from tests.integration.test_schema_00014 import snapshot

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00017_revoke_intake_command_grants.sql"
PROJECT = uid("project:rv")
ACCEPTED = uid("release:rv-accepted")
NEW_PROJECT = uid("project:rv-new")

INSERT_HOSTING = (
    "INSERT INTO provisioning.project_hosting (project_id, live_release_id, last_command_number,"
    " routing_status, hosting_status, updated_at, offline_reasons, owner_status_version,"
    " subdomain) VALUES (%s, NULL, 0, 'stable', 'online', now(), '{}', 0, 'shop')"
    " ON CONFLICT (project_id) DO NOTHING"
)
INSERT_RELEASE = (
    "INSERT INTO provisioning.releases (release_id, project_id, generation_request_id,"
    " repository_id, git_commit_sha, status, created_at, updated_at)"
    " VALUES (%s, %s, %s, 7, %s, 'accepted', now(), now())"
    " ON CONFLICT (project_id, generation_request_id) DO NOTHING RETURNING *"
)
INSERT_STEP = (
    "INSERT INTO provisioning.release_steps (release_step_id, release_id, step_name, attempt,"
    " status) VALUES (%s, %s, 'build', %s, 'pending')"
)
INSERT_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at)"
    " VALUES (%s, %s, NULL, 'sync_domains', 'evt-rv', %s, 1, NULL, 'accepted', now(), now())"
)
INSERT_DISPATCH = (
    "INSERT INTO provisioning_ops.command_dispatch (command_id, correlation_id, deadline_at,"
    " next_publish_at, publish_attempts, execution_attempts, claim_number, created_at, updated_at)"
    " VALUES (%s, 'corr-rv', now() + interval '1 hour', now(), 0, 0, 0, now(), now())"
)

ROLES = (
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


def seeded(new_db: DbFactory, version: int | None) -> Database:
    """A database at ``version`` with project rv: its hosting row, an accepted release and that
    release's failed first build attempt."""
    database = new_db(version)
    at_00018 = version is None or version >= 18
    seed(
        database,
        insert(
            "provisioning.releases",
            release("rv-accepted", "rv", "accepted"),
            new=True,
            at_00018=at_00018,
        ),
        insert(
            "provisioning.project_release_state",
            project_state("rv"),
            new=True,
            at_00018=at_00018,
        ),
        insert(
            "provisioning.release_steps",
            step(
                "rv-build-1",
                "rv-accepted",
                "build",
                1,
                "failed",
                started_at=None,
                finished_at=None,
                reason_code="build_failed",
            ),
            new=True,
        ),
    )
    return database


@pytest.fixture
def revoked_db(new_db: DbFactory) -> Database:
    return seeded(new_db, None)


def denied(conn: Conn, statement: str, params: tuple[object, ...] | None = None) -> None:
    """``statement`` fails for lack of privilege (SQLSTATE 42501); the transaction rolls back."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute(q(statement), params)


# --- what the intake can no longer do -----------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        ("SELECT * FROM provisioning.commands", None),
        ("SELECT command_id FROM provisioning.commands", None),
        ("SELECT * FROM provisioning.release_steps", None),
        ("SELECT status FROM provisioning.release_steps", None),
        ("SELECT * FROM provisioning.project_hosting", None),
        ("SELECT last_command_number FROM provisioning.project_hosting", None),
        ("SELECT routing_status FROM provisioning.project_hosting", None),
        (
            "SELECT project_id FROM provisioning.project_hosting WHERE project_id = %s FOR UPDATE",
            (PROJECT,),
        ),
        (
            "UPDATE provisioning.project_hosting SET last_command_number = 1, updated_at = now()"
            " WHERE project_id = %s",
            (PROJECT,),
        ),
        (INSERT_COMMAND, (uid("command:rv"), PROJECT, fingerprint("rv"))),
        ("SELECT * FROM provisioning_ops.command_dispatch", None),
        (INSERT_DISPATCH, (uid("command:rv"),)),
    ],
    ids=[
        "read-commands",
        "read-a-command-column",
        "read-steps",
        "read-a-step-column",
        "read-the-hosting-row",
        "read-the-counter",
        "read-the-routing-status",
        "lock-the-hosting-row",
        "advance-the-counter",
        "insert-a-sync-domains-command",
        "read-dispatch",
        "insert-a-dispatch-row",
    ],
)
def test_the_intake_is_refused_what_00015_granted(
    revoked_db: Database, statement: str, params: tuple[object, ...] | None
) -> None:
    with revoked_db.login(ROLE_INTAKE) as conn:
        denied(conn, statement, params)
    with revoked_db.admin() as admin:
        assert scalar(admin, "SELECT count(*) FROM provisioning.commands") == 0
        assert scalar(admin, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 0
        counter = (
            "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s"
        )
        assert scalar(admin, counter, (PROJECT,)) == 0


def test_a_later_build_attempt_from_the_intake_is_refused_again(new_db: DbFactory) -> None:
    """At 00016 the intake's SELECT on release_steps let the step guard read attempt 1, so the
    intake could insert attempt 2. After 00017 the guard's read is refused, as before 00015; later
    attempts belong to provisioning_build."""
    second = uid("step:rv-build-2")
    before = seeded(new_db, 16)
    with before.login(ROLE_INTAKE) as conn, allowed(conn):
        conn.execute(q(INSERT_STEP), (second, ACCEPTED, 2))
    after = seeded(new_db, None)
    with after.login(ROLE_INTAKE) as conn:
        denied(conn, INSERT_STEP, (second, ACCEPTED, 2))
    with after.login(ROLE_BUILD) as conn, allowed(conn):
        conn.execute(q(INSERT_STEP), (second, ACCEPTED, 2))


# --- what the intake still does ------------------------------------------------------------------


def test_the_intake_still_reads_releases_and_the_hosting_key(revoked_db: Database) -> None:
    with revoked_db.login(ROLE_INTAKE) as conn:
        assert column(conn, "SELECT status::text FROM provisioning.releases") == ["accepted"]
        found = conn.execute(q("SELECT * FROM provisioning.releases")).fetchall()
        assert len(found) == 1
        assert column(conn, "SELECT project_id FROM provisioning.project_hosting") == [PROJECT]


@pytest.mark.parametrize("project", [NEW_PROJECT, PROJECT], ids=["new-project", "known-project"])
def test_the_intake_still_accepts_a_release(revoked_db: Database, project: UUID) -> None:
    """The writes of the release intake (PgReleaseIntakeStore._insert_release), in its order: the
    hosting row ON CONFLICT (project_id), the release read back, its first build step. The guards
    that run read releases and project_hosting.project_id only."""
    release_id = uid(f"release:rv-intake-{project}")
    with revoked_db.login(ROLE_INTAKE) as conn:
        with conn.transaction():
            conn.execute(q(INSERT_HOSTING), (project,))
            stored = conn.execute(
                q(INSERT_RELEASE), (release_id, project, uid(f"request:{project}"), sha("rv"))
            ).fetchall()
            assert len(stored) == 1
            conn.execute(q(INSERT_STEP), (uid(f"step:rv-intake-{project}"), release_id, 1))
        # A repeat of the same key inserts nothing and is not an error.
        with conn.transaction():
            conn.execute(q(INSERT_HOSTING), (project,))
            again = conn.execute(
                q(INSERT_RELEASE),
                (uid("release:rv-other"), project, uid(f"request:{project}"), sha("rv")),
            ).fetchall()
            assert again == []
    with revoked_db.admin() as admin:
        assert (
            scalar(
                admin,
                "SELECT count(*) FROM provisioning.release_steps WHERE release_id = %s",
                (release_id,),
            )
            == 1
        )
        assert (
            scalar(
                admin,
                "SELECT count(*) FROM provisioning.project_hosting WHERE project_id = %s",
                (project,),
            )
            == 1
        )


# --- exactly the listed privileges changed -------------------------------------------------------

LOST = {
    ("commands", "command_id", "SELECT"),
    ("commands", "command_id", "INSERT"),
    ("command_dispatch", "command_id", "INSERT"),
    ("project_hosting", "last_command_number", "SELECT"),
    ("project_hosting", "last_command_number", "UPDATE"),
    ("project_hosting", "updated_at", "UPDATE"),
    ("release_steps", "status", "SELECT"),
}
KEPT = {
    ("provisioning", "*", "USAGE"),
    ("provisioning_ops", "*", "USAGE"),
    ("releases", "release_id", "SELECT"),
    ("releases", "status", "SELECT"),
    ("releases", "container_image_digest", "SELECT"),
    ("releases", "git_commit_sha", "INSERT"),
    ("project_hosting", "project_id", "SELECT"),
    ("project_hosting", "project_id", "INSERT"),
    ("release_steps", "attempt", "INSERT"),
    ("source_published_events", "status", "UPDATE"),
}


def test_exactly_the_listed_privileges_changed(new_db: DbFactory) -> None:
    at_00014 = new_db(14)
    with at_00014.admin() as conn:
        intake_at_00014 = privileges(conn, ROLE_INTAKE)
    database = new_db(16)
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in ROLES}
    database.migrate(17)  # pinned: a later migration that changes grants has its own test
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in ROLES}
    lost = before[ROLE_INTAKE] - after[ROLE_INTAKE]
    # The intake loses exactly what 00015 added, and gains nothing.
    assert after[ROLE_INTAKE] <= before[ROLE_INTAKE]
    assert lost == ACCEPTANCE_PRIVILEGES - intake_at_00014
    assert after[ROLE_INTAKE] == intake_at_00014
    assert LOST <= lost
    assert KEPT <= after[ROLE_INTAKE]
    assert not {p for p in after[ROLE_INTAKE] if p[0] == "commands"}
    assert not {p for p in after[ROLE_INTAKE] if p[0] == "command_dispatch"}
    assert {p for p in after[ROLE_INTAKE] if p[0] == "project_hosting" and p[2] == "SELECT"} == {
        ("project_hosting", "project_id", "SELECT")
    }
    # Every other role keeps exactly what it had.
    for role in ROLES:
        if role != ROLE_INTAKE:
            assert after[role] == before[role], role


# --- the migration itself ------------------------------------------------------------------------


def test_there_is_no_down_section() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text


def test_the_migration_runs_only_as_the_migrator(new_db: DbFactory) -> None:
    database = new_db(16)
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
        assert scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied") == 16
        assert scalar(
            conn,
            "SELECT has_table_privilege(%s, 'provisioning.commands', 'INSERT')",
            (ROLE_INTAKE,),
        )


def test_the_migration_refuses_when_the_intake_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(16)
    hidden = "prov_hidden_intake"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_INTAKE} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(17)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_INTAKE}"))
    assert f"missing role(s): {ROLE_INTAKE}" in str(info.value)
    assert "bootstrap-roles.sql" in str(info.value)
    applied = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
    insert_commands = "SELECT has_table_privilege(%s, 'provisioning.commands', 'INSERT')"
    with database.admin() as conn:
        assert scalar(conn, applied) == 16
        assert scalar(conn, insert_commands, (ROLE_INTAKE,))
    database.migrate(17)
    with database.admin() as conn:
        assert scalar(conn, applied) == 17
        assert not scalar(conn, insert_commands, (ROLE_INTAKE,))


# --- the upgrade ---------------------------------------------------------------------------------


def test_upgrade_from_00016_keeps_every_row(new_db: DbFactory) -> None:
    database = new_db(16)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    with database.admin() as conn:
        before = snapshot(conn)
    database.migrate(17)
    with database.admin() as conn:
        after = snapshot(conn)
        version = scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied")
    assert version == 17
    assert set(before) == set(after)
    for table, found in before.items():
        assert found, f"the representative rows hold no row of {table}"
        assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
            f"rows of {table} changed by the upgrade"
        )


def test_an_upgraded_database_refuses_the_intake_a_sync_domains_command(
    new_db: DbFactory,
) -> None:
    database = new_db(16)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    migrate_with_backfill(database)
    # p4 holds an accepted release and a stable, online state row with no command.
    project = uid("project:p4")
    with database.login(ROLE_INTAKE) as conn:
        denied(
            conn,
            "SELECT last_command_number FROM provisioning.project_hosting"
            " WHERE project_id = %s FOR UPDATE",
            (project,),
        )
        denied(conn, INSERT_COMMAND, (uid("command:p4-domains"), project, fingerprint("p4")))
