"""Migration 00012: the release worker may write what promote, suspend and resume change.

Grants only (decision 0014 section 8). The tests show the writes the worker needs now succeed for a
login that is a member of ``provisioning_worker`` and nothing else, that no other column opened,
that no other role gained anything, and that the guards still hold for the newly granted columns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

import psycopg
import pytest
from tests.integration._pg import (
    ROLE_API,
    ROLE_BUILD,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_WORKER,
    Conn,
    Database,
    column,
    q,
    refused,
    scalar,
    seed,
)
from tests.integration._rows import insert, project_state, release, uid

type DbFactory = Callable[[int | None], Database]

REL = "provisioning.releases"
HOST = "provisioning.project_hosting"
LIVE, NEXT, OTHER, MISSING = (uid(f"release:{n}") for n in ("live", "next", "other", "none"))
PROJECT = uid("project:pw")


def promotion_rows() -> list[tuple[str, dict[str, object]]]:
    """A project with a live release, a candidate ready to promote, and a second candidate."""
    return [
        ("provisioning.releases", release("live", "pw", "live")),
        ("provisioning.releases", release("next", "pw", "candidate_ready")),
        ("provisioning.releases", release("other", "pw", "candidate_ready")),
        ("provisioning.project_release_state", project_state("pw", generation=2, active="live")),
    ]


@pytest.fixture
def promotion_db(new_db: DbFactory) -> Database:
    database = new_db(None)
    seed(database, *[insert(t, r, new=True) for t, r in promotion_rows()])
    return database


def update(table: str, assignments: str, key: str) -> str:
    return f"UPDATE {table} SET {assignments} WHERE {key} = %s"  # noqa: S608 - constants only


def test_worker_login_can_write_a_promotion(promotion_db: Database) -> None:
    with promotion_db.login(ROLE_WORKER) as conn, conn.transaction(force_rollback=True):
        set_status = "status = %s, updated_at = now()"
        conn.execute(q(update(REL, set_status, "release_id")), ("superseded", LIVE))
        conn.execute(q(update(REL, set_status, "release_id")), ("promoting", NEXT))
        conn.execute(
            q(update(HOST, "routing_status = 'changing', updated_at = now()", "project_id")),
            (PROJECT,),
        )
        live = "status = 'live', promoted_at = now(), updated_at = now()"
        conn.execute(q(update(REL, live, "release_id")), (NEXT,))
        point = "live_release_id = %s, routing_status = 'stable', updated_at = now()"
        conn.execute(q(update(HOST, point, "project_id")), (NEXT, PROJECT))
        assert scalar(conn, "SELECT live_release_id FROM provisioning.project_hosting") == NEXT
        status = scalar(
            conn, "SELECT status FROM provisioning.releases WHERE release_id = %s", (NEXT,)
        )
        assert status == "live"


def test_worker_login_can_write_suspend_and_resume(promotion_db: Database) -> None:
    with promotion_db.login(ROLE_WORKER) as conn, conn.transaction(force_rollback=True):
        # From 00018 the reasons move with the status (offline exactly when a reason is held).
        for value, reasons in (("offline", "{billing}"), ("online", "{}")):
            conn.execute(
                q(
                    update(
                        HOST,
                        "hosting_status = %s, offline_reasons = %s, updated_at = now()",
                        "project_id",
                    )
                ),
                (value, reasons, PROJECT),
            )
            found = scalar(conn, "SELECT hosting_status FROM provisioning.project_hosting")
            assert found == value


def update_columns(conn: Conn, table: str) -> set[str]:
    schema, name = table.split(".")
    cols = cast(
        Sequence[str],
        column(
            conn,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, name),
        ),
    )
    return {
        c
        for c in cols
        if scalar(
            conn, "SELECT has_column_privilege(%s, %s, %s, 'UPDATE')", (ROLE_WORKER, table, c)
        )
    }


def test_worker_gains_exactly_the_three_columns_and_promoted_at(db: Database) -> None:
    with db.admin() as conn:
        # offline_reasons from 00018 (decision 0021).
        assert update_columns(conn, HOST) == {
            *("live_release_id", "routing_status", "hosting_status", "updated_at"),
            "offline_reasons",
        }
        assert update_columns(conn, REL) == {
            *("status", "failure_code", "failure_summary", "updated_at", "promoted_at"),
        }
        for table in (HOST, REL):
            assert not scalar(
                conn, "SELECT has_table_privilege(%s, %s, 'INSERT')", (ROLE_WORKER, table)
            )
            assert not scalar(
                conn, "SELECT has_table_privilege(%s, %s, 'DELETE')", (ROLE_WORKER, table)
            )


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        (HOST, "last_command_number = last_command_number + 1"),
        (HOST, "project_id = project_id"),
        (REL, "container_image_digest = 'sha256:' || repeat('a', 64)"),
        (REL, "git_commit_sha = repeat('a', 40)"),
        (REL, "created_at = now()"),
    ],
)
def test_worker_still_cannot_write_other_columns(
    promotion_db: Database, table: str, assignment: str
) -> None:
    key = "project_id" if table == HOST else "release_id"
    value = PROJECT if table == HOST else NEXT
    with promotion_db.login(ROLE_WORKER) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(q(update(table, assignment, key)), (value,))


def all_column_privileges(conn: Conn, role: str) -> set[tuple[str, str, str]]:
    found = conn.execute(
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
    return {(r[0], r[1], r[2]) for r in found}


def test_no_other_role_gains_anything(new_db: DbFactory) -> None:
    database = new_db(11)
    roles = (ROLE_API, ROLE_INTAKE, ROLE_BUILD, ROLE_OPERATOR, ROLE_MIGRATOR, "public")
    with database.admin() as conn:
        before = {r: all_column_privileges(conn, r) for r in roles}
        worker_before = all_column_privileges(conn, ROLE_WORKER)
    database.migrate(12)
    with database.admin() as conn:
        after = {r: all_column_privileges(conn, r) for r in roles}
        worker_after = all_column_privileges(conn, ROLE_WORKER)
    assert before == after
    assert worker_after - worker_before == {
        ("releases", "promoted_at", "UPDATE"),
        ("project_hosting", "live_release_id", "UPDATE"),
        ("project_hosting", "routing_status", "UPDATE"),
        ("project_hosting", "hosting_status", "UPDATE"),
    }
    assert worker_before <= worker_after


def test_guards_still_hold_for_the_granted_columns(promotion_db: Database) -> None:
    with promotion_db.login(ROLE_WORKER) as conn:
        # a second live release for the project is refused by the unique index
        with refused(conn, constraint="releases_one_live_per_project", sqlstate="23505"):
            live = "status = 'live', promoted_at = now(), updated_at = now()"
            conn.execute(q(update(REL, live, "release_id")), (NEXT,))
        # promoted_at is write-once
        with refused(conn, constraint="releases_identity_immutable", table="releases"):
            conn.execute(q(update(REL, "promoted_at = now()", "release_id")), (LIVE,))
        # the live release must exist: the composite foreign key
        with refused(conn, constraint="project_hosting_live_release_fk", sqlstate="23503"):
            conn.execute(q(update(HOST, "live_release_id = %s", "project_id")), (MISSING, PROJECT))
        # a status that does not match promoted_at is refused by the check constraint
        with refused(conn, constraint="releases_promoted_at_matches_status", sqlstate="23514"):
            conn.execute(q(update(REL, "status = 'live'", "release_id")), (OTHER,))
