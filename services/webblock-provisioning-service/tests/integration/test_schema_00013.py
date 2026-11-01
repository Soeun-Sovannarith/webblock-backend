"""Migration 00013: the hosting-intake role may accept a suspend or resume command.

Grants only (decision 0014 section 9). A login that is a member of ``provisioning_hosting_intake``
and nothing else gets exactly the command-acceptance privileges ``provisioning_api`` holds. The
tests show that such a login can accept a hosting command (the command, its dispatch row, and
``last_command_number`` + 1), what the insert guard does for a ``deploy`` from that login, that an
API login still cannot insert a hosting command, that no other role's privileges change, and that
Down revokes exactly what Up granted.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import psycopg
import pytest
from tests.integration._pg import (
    GOOSE,
    MIGRATIONS_DIR,
    ROLE_API,
    ROLE_BUILD,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_WORKER,
    Conn,
    Database,
    q,
    refused,
    scalar,
    seed,
)
from tests.integration._rows import (
    fingerprint,
    insert,
    project_state,
    reason_of_key,
    release,
    uid,
)

type DbFactory = Callable[[int | None], Database]

PROJECT = uid("project:hi")
BUILT = uid("release:hi-built")
COMMAND = uid("command:hi-suspend")

INSERT_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at,"
    " hosting_reason) VALUES (%s, %s, %s, %s, %s, %s, 1, %s, 'accepted', now(), now(), %s)"
)
INSERT_DISPATCH = (
    "INSERT INTO provisioning_ops.command_dispatch (command_id, correlation_id, deadline_at,"
    " next_publish_at, publish_attempts, execution_attempts, claim_number, created_at, updated_at)"
    " VALUES (%s, 'corr-hi', now() + interval '1 hour', now(), 0, 0, 0, now(), now())"
)
ADVANCE = (
    "UPDATE provisioning.project_hosting SET last_command_number = 1, updated_at = now()"
    " WHERE project_id = %s AND last_command_number = 0"
)
LOCK = (
    "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s FOR UPDATE"
)


@pytest.fixture
def hosting_db(new_db: DbFactory) -> Database:
    database = new_db(None)
    seed(
        database,
        insert("provisioning.releases", release("hi-built", "hi", "built"), new=True),
        insert("provisioning.project_release_state", project_state("hi"), new=True),
    )
    return database


def accept(conn: Conn, kind: str, release_id: object, subject: object) -> None:
    """The acceptance writes, in the order and under the lock the store uses."""
    conn.execute(q(LOCK), (PROJECT,))
    conn.execute(q(ADVANCE), (PROJECT,))
    conn.execute(
        q(INSERT_COMMAND),
        (
            COMMAND,
            PROJECT,
            release_id,
            kind,
            "evt-1",
            fingerprint("hi"),
            subject,
            reason_of_key(kind, "evt-1"),
        ),
    )
    conn.execute(q(INSERT_DISPATCH), (COMMAND,))


def test_hosting_intake_login_accepts_a_suspend_command(hosting_db: Database) -> None:
    with hosting_db.login(ROLE_HOSTING_INTAKE) as conn:
        with conn.transaction():
            accept(conn, "suspend_hosting", None, None)
        number = scalar(
            conn,
            "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s",
            (PROJECT,),
        )
        assert number == 1
        assert scalar(conn, "SELECT kind::text FROM provisioning.commands") == "suspend_hosting"
    with hosting_db.admin() as admin:
        assert scalar(admin, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 1


def test_hosting_intake_login_deploy_command_guard(hosting_db: Database) -> None:
    """A deploy inserted by a hosting-intake login is refused at any command number.

    Migration 00013 left a gap that B3 reported: commands_insert_shape tied the hosting kinds to
    provisioning_hosting_intake but no other kind to provisioning_api. Migration 00015 (decision
    0014 section 16 item 2) closes it, so on the latest schema the right command number no longer
    admits the deploy. The gap itself is shown at version 13 in test_schema_00014.py.
    """
    with hosting_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            conn.execute(
                q(INSERT_COMMAND),
                (COMMAND, PROJECT, BUILT, "deploy", "key-1", fingerprint("hi"), "subject-1", None),
            )
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, "deploy", BUILT, "subject-1")


def test_api_login_still_cannot_insert_a_hosting_command(hosting_db: Database) -> None:
    with hosting_db.login(ROLE_API) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, "resume_hosting", None, None)


def test_hosting_intake_login_cannot_write_beyond_acceptance(hosting_db: Database) -> None:
    with hosting_db.login(ROLE_HOSTING_INTAKE) as conn:
        for statement in (
            "UPDATE provisioning.project_hosting SET routing_status = 'changing'"
            " WHERE project_id = %s",
            "UPDATE provisioning.commands SET status = 'running' WHERE project_id = %s",
            "UPDATE provisioning.releases SET status = 'failed' WHERE project_id = %s",
            "DELETE FROM provisioning.commands WHERE project_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(q(statement), (PROJECT,))


def privileges(conn: Conn, role: str) -> set[tuple[str, str, str]]:
    """Every table, column and schema privilege of ``role`` on the service's two schemas."""
    columns = conn.execute(
        q(
            "SELECT c.relname::text, a.attname::text, p.priv FROM pg_class AS c"
            " JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
            " CROSS JOIN (VALUES ('SELECT'), ('INSERT'), ('UPDATE'), ('REFERENCES')) AS p (priv)"
            " WHERE c.relkind = 'r' AND c.relnamespace IN"
            " ('provisioning'::regnamespace, 'provisioning_ops'::regnamespace)"
            " AND has_column_privilege(%s, c.oid, a.attnum, p.priv)"
        ),
        (role,),
    ).fetchall()
    tables = conn.execute(
        q(
            "SELECT c.relname::text, '*', p.priv FROM pg_class AS c"
            " CROSS JOIN (VALUES ('DELETE'), ('TRUNCATE'), ('TRIGGER')) AS p (priv)"
            " WHERE c.relkind = 'r' AND c.relnamespace IN"
            " ('provisioning'::regnamespace, 'provisioning_ops'::regnamespace)"
            " AND has_table_privilege(%s, c.oid, p.priv)"
        ),
        (role,),
    ).fetchall()
    schemas = conn.execute(
        q(
            "SELECT n.nspname::text, '*', p.priv FROM pg_namespace AS n"
            " CROSS JOIN (VALUES ('USAGE'), ('CREATE')) AS p (priv)"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops')"
            " AND has_schema_privilege(%s, n.oid, p.priv)"
        ),
        (role,),
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in [*columns, *tables, *schemas]}


ACCEPTANCE_PRIVILEGES = (
    {("provisioning", "*", "USAGE"), ("provisioning_ops", "*", "USAGE")}
    | {
        (table, column, "SELECT")
        for table, columns in (
            (
                "releases",
                (
                    "release_id",
                    "project_id",
                    "generation_request_id",
                    "github_repository_id",
                    "git_commit_sha",
                    "container_image_digest",
                    "status",
                    "failure_code",
                    "failure_summary",
                    "created_at",
                    "updated_at",
                    "promoted_at",
                ),
            ),
            (
                "project_hosting",
                (
                    "project_id",
                    "live_release_id",
                    "last_command_number",
                    "routing_status",
                    "hosting_status",
                    "updated_at",
                ),
            ),
            (
                "release_steps",
                (
                    "release_step_id",
                    "release_id",
                    "step_name",
                    "attempt",
                    "status",
                    "started_at",
                    "finished_at",
                    "deadline_at",
                    "failure_code",
                    "log_ref",
                ),
            ),
            (
                "commands",
                (
                    "command_id",
                    "project_id",
                    "release_id",
                    "kind",
                    "idempotency_key",
                    "request_fingerprint",
                    "command_number",
                    "requested_by_subject",
                    "status",
                    "failure_code",
                    "created_at",
                    "updated_at",
                ),
            ),
        )
        for column in columns
    }
    | {
        ("commands", column, "INSERT")
        for column in (
            "command_id",
            "project_id",
            "release_id",
            "kind",
            "idempotency_key",
            "request_fingerprint",
            "command_number",
            "requested_by_subject",
            "status",
            "failure_code",
            "created_at",
            "updated_at",
        )
    }
    | {
        ("project_hosting", "last_command_number", "UPDATE"),
        ("project_hosting", "updated_at", "UPDATE"),
    }
    | {
        ("command_dispatch", column, "INSERT")
        for column in (
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
        )
    }
)


def test_hosting_intake_gains_exactly_the_acceptance_privileges(new_db: DbFactory) -> None:
    database = new_db(12)
    with database.admin() as conn:
        before = privileges(conn, ROLE_HOSTING_INTAKE)
    database.migrate(13)
    with database.admin() as conn:
        after = privileges(conn, ROLE_HOSTING_INTAKE)
        api = privileges(conn, ROLE_API)
    assert before == set()
    assert after == ACCEPTANCE_PRIVILEGES
    # The same acceptance privileges the API role holds (decision 0014 section 9).
    assert after <= api


def test_no_other_role_gains_anything(new_db: DbFactory) -> None:
    database = new_db(12)
    roles = (
        ROLE_API,
        ROLE_WORKER,
        ROLE_INTAKE,
        ROLE_BUILD,
        ROLE_OPERATOR,
        ROLE_MIGRATOR,
        "provisioning_payment_intake",
        "public",
    )
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in roles}
    database.migrate(13)
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in roles}
    assert before == after


def test_down_revokes_exactly_what_up_granted(new_db: DbFactory) -> None:
    database = new_db(12)
    roles = (ROLE_HOSTING_INTAKE, ROLE_API, ROLE_WORKER, ROLE_INTAKE, ROLE_BUILD, "public")
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in roles}
    database.migrate(13)
    env = {
        "PATH": "/usr/bin:/bin",
        "GOOSE_DRIVER": "postgres",
        "GOOSE_DBSTRING": database.harness.server.goose_url(database.name),
        "GOOSE_MIGRATION_DIR": str(MIGRATIONS_DIR),
    }
    done = subprocess.run(  # noqa: S603 - fixed argv built from constants, no shell
        [str(GOOSE), "down-to", "12"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in roles}
        version = scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied")
    assert version == 12
    assert before == after
