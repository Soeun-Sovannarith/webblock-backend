"""Migration 00022: the database exactly as Notion "5 · Provisioning" (decision 0023 G16).

The seven DIFFERENT findings of the audit
(``.superpowers/sdd/2026-10-25-provisioning-phase-c/db-notion-audit.md`` section 7):

1. the physical column order of ``project_hosting``, ``commands`` and ``source_published_events``
   is Notion's (the three tables are rebuilt; every row, grant, index, check, foreign key, named
   NOT NULL constraint and trigger is kept);
2. the enum ``provisioning.release_step_name`` is in Notion's order;
3. ``usage_outbox.completed_at`` equals the release's ``promoted_at`` (insert guard);
4. a ``sent`` usage event never changes (update guard);
5. a finished release step never changes (update guard);
6. ``skipped`` only for the hosting kinds (check);
7. a finished command never changes, ``updated_at`` included (update guard).

Each finding has its refusal and its allowed path. The migration refuses a database whose rows
already break 3 or 6, runs only as the migrator, refuses when a role is missing, and has no Down
section, no default and no cascade.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from datetime import timedelta
from typing import Final, cast

import psycopg
import pytest
from tests.integration._pg import (
    GOOSE,
    MIGRATIONS_DIR,
    ROLE_BUILD,
    ROLE_MIGRATOR,
    ROLE_WORKER,
    Conn,
    Database,
    Harness,
    allowed,
    column,
    q,
    refused,
    rows,
    scalar,
    seed,
)
from tests.integration._rows import (
    TABLES,
    Row,
    at,
    command,
    insert,
    table_name,
    uid,
    world,
)

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00022_notion_column_order_and_rules.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"

# Notion "5 · Provisioning", fetched 25 October 2026 (the audit, section 1.2; the four tables that
# already matched are listed too, so the test covers every table).
NOTION_COLUMNS: Final[dict[str, list[str]]] = {
    "provisioning.releases": [
        *("release_id", "project_id", "generation_request_id", "repository_id"),
        *("git_commit_sha", "container_image_digest", "status", "failure_code"),
        *("failure_summary", "created_at", "updated_at", "promoted_at"),
    ],
    "provisioning.project_hosting": [
        *("project_id", "live_release_id", "subdomain", "last_command_number"),
        *("routing_status", "hosting_status", "offline_reasons", "owner_status_version"),
        "updated_at",
    ],
    "provisioning.release_steps": [
        *("release_step_id", "release_id", "step_name", "attempt", "status"),
        *("started_at", "finished_at", "deadline_at", "failure_code", "log_ref"),
    ],
    "provisioning.commands": [
        *("command_id", "project_id", "release_id", "kind", "hosting_reason"),
        *("idempotency_key", "request_fingerprint", "command_number", "requested_by_subject"),
        *("status", "failure_code", "created_at", "updated_at"),
    ],
    "provisioning_ops.command_dispatch": [
        *("command_id", "correlation_id", "deadline_at", "next_publish_at", "publish_attempts"),
        *("last_broker_ack_at", "execution_attempts", "claim_number", "claimed_by_worker"),
        *("claim_expires_at", "retry_after", "last_failure_code", "finished_at"),
        *("created_at", "updated_at"),
    ],
    "provisioning_ops.source_published_events": [
        *("project_id", "generation_request_id", "repository_id", "git_commit_sha", "status"),
        *("attempts", "max_attempts", "next_attempt_at", "outcome_code", "release_id"),
        *("mismatched_repeat_count", "last_mismatched_repeat_at", "received_at", "updated_at"),
        *("finished_at", "last_manual_retry_by", "last_manual_retry_at"),
    ],
    "provisioning_ops.usage_outbox": [
        *("release_id", "project_id", "event_key", "completed_at", "status", "attempts"),
        *("next_attempt_at", "last_failure_code", "sent_at", "created_at", "updated_at"),
    ],
}
NOTION_STEP_NAMES: Final = [
    *("build", "candidate", "promotion", "rollback", "access_setup", "observation"),
]
NEW_CHECK: Final = (
    "provisioning.commands",
    "commands_skipped_only_for_hosting",
    "c",
    False,
    False,
    "CHECK (((status <> 'skipped'::provisioning.command_status) OR (kind = ANY "
    "(ARRAY['suspend_hosting'::provisioning.command_kind, "
    "'resume_hosting'::provisioning.command_kind]))))",
)
REPLACED_FUNCTIONS: Final = frozenset(
    {
        "provisioning.commands_terminal_status_final",
        "provisioning.release_steps_update_shape",
        "provisioning_ops.usage_outbox_insert_shape",
        "provisioning_ops.usage_outbox_update_shape",
    }
)

USAGE = "provisioning_ops.usage_outbox"
INSERT_USAGE = (
    "INSERT INTO provisioning_ops.usage_outbox (release_id, project_id, event_key, completed_at,"
    " status, attempts, next_attempt_at, last_failure_code, sent_at, created_at, updated_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def usage_row(
    release_name: str, project: str, completed_seconds: int, *, sent: bool = False
) -> tuple[str, list[object]]:
    """A usage event of ``release_name`` on the seeding path; ``completed_seconds`` is the offset
    of completed_at (the builders' promoted_at is ``at(60)``)."""
    release_id = uid(f"release:{release_name}")
    done = at(completed_seconds)
    return (
        INSERT_USAGE,
        [
            release_id,
            uid(f"project:{project}"),
            f"publish-completed-{release_id}",
            done,
            "sent" if sent else "pending",
            1 if sent else 0,
            done,
            None,
            done + timedelta(seconds=5) if sent else None,
            done,
            done + timedelta(seconds=5) if sent else done,
        ],
    )


def representative(database: Database) -> None:
    """Every table holds rows: the shared world plus one sent and one pending usage event."""
    seed(
        database,
        *[insert(table, row, new=True) for table, row in world()],
        usage_row("p1-superseded", "p1", 60, sent=True),
        usage_row("p1-live", "p1", 60),
    )


# --- catalogue readers ------------------------------------------------------------------------


def columns_in_order(conn: Conn) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for table, name in rows(
        conn,
        "SELECT table_schema || '.' || table_name, column_name::text"
        " FROM information_schema.columns WHERE table_schema IN ('provisioning',"
        " 'provisioning_ops')"
        " ORDER BY 1, ordinal_position",
    ):
        out.setdefault(table, []).append(name)
    return out


def column_shapes(conn: Conn) -> set[tuple[object, ...]]:
    """Every column's type, nullability and default, without its position."""
    return set(
        rows(
            conn,
            "SELECT table_schema || '.' || table_name, column_name::text, data_type::text,"
            " udt_schema || '.' || udt_name, character_maximum_length, is_nullable::text,"
            " column_default FROM information_schema.columns WHERE table_schema IN ('provisioning',"
            " 'provisioning_ops')",
        )
    )


def snapshot(database: Database) -> dict[str, list[Row]]:
    """Every row of every table as JSON keyed by column name, so the order of columns is ignored."""
    out: dict[str, list[Row]] = {}
    with database.admin() as conn:
        for table in (*(table_name(t, new=True) for t in TABLES), USAGE):
            query = f"SELECT to_jsonb(t) FROM {table} AS t"  # noqa: S608 - constant names
            found = [cast(Row, r[0]) for r in rows(conn, query)]
            out[table] = sorted(found, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def grants(conn: Conn) -> dict[str, set[tuple[object, ...]]]:
    """The exact ACL entries of every table, column, schema and function, by name, and the two
    information_schema views the brief names."""
    return {
        "tables": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || c.relname, a.grantor::regrole::text,"
                " a.grantee::regrole::text, a.privilege_type, a.is_grantable"
                " FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace,"
                " aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) AS a"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND c.relkind = 'r'",
            )
        ),
        "columns": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || c.relname, att.attname::text,"
                " a.grantor::regrole::text, a.grantee::regrole::text, a.privilege_type,"
                " a.is_grantable FROM pg_class AS c"
                " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " JOIN pg_attribute AS att ON att.attrelid = c.oid AND att.attnum > 0"
                " AND NOT att.attisdropped, aclexplode(att.attacl) AS a"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND c.relkind = 'r'",
            )
        ),
        "role_table_grants": set(
            rows(
                conn,
                "SELECT grantor::text, grantee::text, table_schema::text, table_name::text,"
                " privilege_type::text, is_grantable::text"
                " FROM information_schema.role_table_grants"
                " WHERE table_schema IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "role_column_grants": set(
            rows(
                conn,
                "SELECT grantor::text, grantee::text, table_schema::text, table_name::text,"
                " column_name::text, privilege_type::text, is_grantable::text"
                " FROM information_schema.role_column_grants WHERE table_schema IN ('provisioning',"
                " 'provisioning_ops')",
            )
        ),
        "schemas": set(
            rows(
                conn,
                "SELECT n.nspname::text, a.grantee::regrole::text, a.privilege_type"
                " FROM pg_namespace AS n, aclexplode(n.nspacl) AS a"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "functions": set(
            rows(
                conn,
                "SELECT p.oid::regprocedure::text, a.grantee::regrole::text, a.privilege_type"
                " FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace,"
                " aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) AS a"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "types": set(
            rows(
                conn,
                "SELECT t.typname::text, a.grantee::regrole::text, a.privilege_type"
                " FROM pg_type AS t JOIN pg_namespace AS n ON n.oid = t.typnamespace,"
                " aclexplode(coalesce(t.typacl, acldefault('T', t.typowner))) AS a"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
            )
        ),
    }


def structure(conn: Conn) -> dict[str, set[tuple[object, ...]]]:
    """Index, constraint (NOT NULL, check, key, foreign key, constraint trigger) and trigger names
    and definitions, by table name, and every table's owner and options."""
    return {
        "indexes": set(
            rows(
                conn,
                "SELECT schemaname::text, tablename::text, indexname::text, indexdef"
                " FROM pg_indexes WHERE schemaname IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "constraints": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || c.relname, k.conname::text, k.contype::text,"
                " k.condeferrable, k.condeferred, pg_get_constraintdef(k.oid)"
                " FROM pg_constraint AS k JOIN pg_class AS c ON c.oid = k.conrelid"
                " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "constraint_flags": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || c.relname, k.conname::text, k.convalidated,"
                " k.connoinherit, k.confupdtype::text, k.confdeltype::text, k.confmatchtype::text"
                " FROM pg_constraint AS k JOIN pg_class AS c ON c.oid = k.conrelid"
                " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
            )
        ),
        "triggers": set(
            rows(
                conn,
                "SELECT c.relname::text, t.tgname::text, t.tgenabled::text,"
                " pg_get_triggerdef(t.oid) FROM pg_trigger AS t"
                " JOIN pg_class AS c ON c.oid = t.tgrelid"
                " JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND NOT t.tgisinternal",
            )
        ),
        "tables": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || c.relname, c.relowner::regrole::text,"
                " c.relrowsecurity, c.relreplident::text, c.reloptions, c.relpersistence::text"
                " FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace"
                " WHERE n.nspname IN ('provisioning',"
                " 'provisioning_ops') AND c.relkind IN ('r', 'S', 'v', 'm')",
            )
        ),
        "types": set(
            rows(
                conn,
                "SELECT n.nspname || '.' || t.typname, t.typtype::text, t.typowner::regrole::text"
                " FROM pg_type AS t JOIN pg_namespace AS n ON n.oid = t.typnamespace"
                " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND t.typtype = 'e'",
            )
        ),
    }


def functions(conn: Conn) -> dict[str, tuple[object, ...]]:
    return {
        str(r[0]): tuple(r[1:])
        for r in rows(
            conn,
            "SELECT n.nspname || '.' || p.proname, pg_get_function_identity_arguments(p.oid),"
            " p.prosrc, p.prosecdef, p.proconfig, p.proowner::regrole::text"
            " FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
        )
    }


# --- finding 1: Notion's column order, nothing else lost -------------------------------------


def test_00022_column_order_is_notions(new_db: DbFactory) -> None:
    with new_db(22).admin() as conn:
        assert scalar(conn, APPLIED) == 22
        assert columns_in_order(conn) == NOTION_COLUMNS


def test_00022_changes_only_the_order_of_columns(new_db: DbFactory) -> None:
    database = new_db(21)
    with database.admin() as conn:
        before = column_shapes(conn)
        order_before = columns_in_order(conn)
    assert order_before != NOTION_COLUMNS  # the three tables differed at 00021
    database.migrate(22)
    with database.admin() as conn:
        assert column_shapes(conn) == before
        assert {t: sorted(c) for t, c in columns_in_order(conn).items()} == {
            t: sorted(c) for t, c in order_before.items()
        }


def test_00022_keeps_every_row(new_db: DbFactory) -> None:
    database = new_db(21)
    representative(database)
    before = snapshot(database)
    for table, found in before.items():
        assert found, f"the representative rows hold no row of {table}"
    database.migrate(22)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 22
    after = snapshot(database)
    for table, found in before.items():
        assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
            f"rows of {table} changed by 00022"
        )


def test_00022_keeps_every_grant(new_db: DbFactory) -> None:
    database = new_db(21)
    with database.admin() as conn:
        before = grants(conn)
    database.migrate(22)
    with database.admin() as conn:
        after = grants(conn)
    for kind, found in before.items():
        assert found, f"no {kind} grant read at 00021"
        assert after[kind] == found, kind


def test_00022_keeps_indexes_checks_and_triggers(new_db: DbFactory) -> None:
    """Every index, constraint and trigger keeps its name and definition; the one addition is the
    check of finding 6. Every function is unchanged except the four guards of findings 3, 4, 5
    and 7, which stay SECURITY INVOKER with search_path pg_catalog."""
    database = new_db(21)
    with database.admin() as conn:
        before = structure(conn)
        functions_before = functions(conn)
    database.migrate(22)
    with database.admin() as conn:
        after = structure(conn)
        functions_after = functions(conn)
    assert after["constraints"] == before["constraints"] | {NEW_CHECK}
    assert NEW_CHECK not in before["constraints"]
    for kind in ("indexes", "triggers", "tables", "types"):
        assert after[kind] == before[kind], kind
    flags = {r for r in after["constraint_flags"] if r[1] != NEW_CHECK[1]}
    assert flags == before["constraint_flags"]
    assert any(t[1] == "project_hosting_generation_has_command" for t in after["triggers"])
    assert functions_after.keys() == functions_before.keys()
    for name, shape in functions_before.items():
        if name in REPLACED_FUNCTIONS:
            assert functions_after[name] != shape, name
            assert functions_after[name][2:] == (False, ["search_path=pg_catalog"], ROLE_MIGRATOR)
        else:
            assert functions_after[name] == shape, name


def test_the_foreign_key_to_commands_still_holds(db: Database) -> None:
    """command_dispatch_command_fk is dropped and added again around the rebuild of commands. No
    role deletes, so the superuser's delete shows the restrict: the command has a dispatch row."""
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.admin() as conn:
        with refused(conn, constraint="command_dispatch_command_fk", sqlstate="23001"):
            conn.execute(
                q("DELETE FROM provisioning.commands WHERE command_id = %s"),
                (uid("command:p1-6"),),
            )


def test_the_deferred_command_number_guard_still_fires(db: Database) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="project_hosting_generation_has_command"):
            conn.execute(
                q(
                    "UPDATE provisioning.project_hosting SET last_command_number = 1,"
                    " updated_at = now() WHERE project_id = %s"
                ),
                (uid("project:p4"),),
            )


# --- finding 2: the step names in Notion's order ----------------------------------------------


def test_00022_step_names_are_in_notions_order(new_db: DbFactory) -> None:
    database = new_db(21)
    representative(database)
    database.migrate(22)
    with database.admin() as conn:
        labels = column(
            conn,
            "SELECT e.enumlabel::text FROM pg_enum AS e"
            " WHERE e.enumtypid = 'provisioning.release_step_name'::regtype"
            " ORDER BY e.enumsortorder",
        )
        assert labels == NOTION_STEP_NAMES
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM pg_type WHERE typname LIKE 'release_step_name%%'"
                " OR typname LIKE '\\_release_step_name%%'",
            )
            == 2
        )  # the type and its array type, nothing left over
        assert column(
            conn,
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
            " WHERE attrelid = 'provisioning.release_steps'::regclass AND attname = 'step_name'",
        ) == ["provisioning.release_step_name"]
        ordered = column(
            conn,
            "SELECT step_name::text FROM provisioning.release_steps"
            " GROUP BY step_name ORDER BY step_name",
        )
        assert ordered == ["build", "candidate"]


# --- finding 3: completed_at is the release's promoted_at ------------------------------------


def test_a_usage_event_must_carry_the_releases_promoted_at(db: Database) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="usage_outbox_insert_shape", table="usage_outbox"):
            text, values = usage_row("p1-live", "p1", 61)
            conn.execute(q(text), values)
        with allowed(conn):
            text, values = usage_row("p1-live", "p1", 60)
            conn.execute(q(text), values)


def test_00022_refuses_a_usage_event_that_is_not_the_promotion_time(new_db: DbFactory) -> None:
    database = new_db(21)
    representative(database)
    seed(database, usage_row("p3-promoting", "p3", 60))  # promoted_at is NULL
    with pytest.raises(RuntimeError) as info:
        database.migrate(22)
    text = str(info.value)
    assert "1 usage event(s) have a completed_at that is not the promoted_at" in text
    assert "23514" in text
    still_21(database)


# --- finding 4: a sent usage event never changes ---------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        "attempts = attempts + 1",
        "next_attempt_at = next_attempt_at + interval '1 minute'",
        "last_failure_code = 'publish_failed'",
        "updated_at = updated_at + interval '1 second'",
    ],
)
def test_a_sent_usage_event_never_changes(db: Database, change: str) -> None:
    representative(db)
    statement = f"UPDATE provisioning_ops.usage_outbox SET {change} WHERE release_id = %s"  # noqa: S608
    with db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="usage_outbox_update_shape", table="usage_outbox"):
            conn.execute(q(statement), (uid("release:p1-superseded"),))
        with allowed(conn):
            conn.execute(q(statement), (uid("release:p1-live"),))


# --- finding 5: a finished step never changes -------------------------------------------------


@pytest.mark.parametrize(
    ("role", "step", "change"),
    [
        (ROLE_WORKER, "p1-failed-candidate", "deadline_at = now()"),
        (ROLE_BUILD, "p3-succeeded", "deadline_at = now()"),
        (ROLE_BUILD, "p3-succeeded", "failure_code = 'late_failure'"),
    ],
)
def test_a_finished_step_never_changes(db: Database, role: str, step: str, change: str) -> None:
    """Before 00022 a column still NULL could be set after the step ended."""
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    statement = f"UPDATE provisioning.release_steps SET {change} WHERE release_step_id = %s"  # noqa: S608
    with db.login(role) as conn:
        with refused(conn, constraint="release_steps_update_shape", table="release_steps"):
            conn.execute(q(statement), (uid(f"step:{step}"),))


def test_a_running_step_still_finishes(db: Database) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.login(ROLE_BUILD) as conn, allowed(conn):
        conn.execute(
            q(
                "UPDATE provisioning.release_steps SET status = 'succeeded', finished_at = now()"
                " WHERE release_step_id = %s"
            ),
            (uid("step:p3-running"),),
        )


# --- finding 6: skipped only for the hosting kinds -------------------------------------------


@pytest.mark.parametrize("kind", ["deploy", "promote", "rollback", "redeploy"])
def test_only_a_hosting_command_is_skipped(db: Database, kind: str) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    row = command("p4-skipped", "p4", "p4-accepted", 1, "skipped", kind=kind)
    with pytest.raises(psycopg.errors.CheckViolation) as info:
        seed(db, insert("provisioning.commands", row, new=True))
    assert info.value.diag.constraint_name == "commands_skipped_only_for_hosting"


@pytest.mark.parametrize("kind", ["suspend_hosting", "resume_hosting"])
def test_a_hosting_command_may_be_skipped(db: Database, kind: str) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    row = command("p4-skipped", "p4", None, 1, "skipped", kind=kind)
    seed(db, insert("provisioning.commands", row, new=True))


def test_00022_refuses_a_skipped_command_of_another_kind(new_db: DbFactory) -> None:
    database = new_db(21)
    representative(database)
    row = command("p4-skipped", "p4", "p4-accepted", 1, "skipped")
    seed(database, insert("provisioning.commands", row, new=True))
    with pytest.raises(RuntimeError) as info:
        database.migrate(22)
    text = str(info.value)
    assert "1 command(s) are skipped but are not a hosting command" in text
    assert "23514" in text
    still_21(database)


# --- finding 7: a finished command never changes ----------------------------------------------


@pytest.mark.parametrize("name", ["p1-1", "p1-2", "p1-4"])
def test_a_finished_command_never_changes(db: Database, name: str) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.login(ROLE_WORKER) as conn:
        with refused(conn, constraint="commands_terminal_status_final", table="commands"):
            conn.execute(
                q("UPDATE provisioning.commands SET updated_at = now() WHERE command_id = %s"),
                (uid(f"command:{name}"),),
            )


def test_a_running_command_still_finishes(db: Database) -> None:
    seed(db, *[insert(table, row, new=True) for table, row in world()])
    with db.login(ROLE_WORKER) as conn, allowed(conn):
        conn.execute(
            q(
                "UPDATE provisioning.commands SET status = 'succeeded', updated_at = now()"
                " WHERE command_id = %s"
            ),
            (uid("command:p1-6"),),
        )


# --- the migration rules ----------------------------------------------------------------------


def still_21(database: Database) -> None:
    """A refused upgrade changed nothing: still at 00021, in the old column order."""
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 21
        assert columns_in_order(conn)["provisioning.commands"][-1] == "hosting_reason"


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "TRUNCATE", "SET NULL"):
        assert re.search(rf"\b{word}\b", code) is None, word
    # Every foreign key blocks deletes.
    assert code.count("FOREIGN KEY") == code.count("ON DELETE RESTRICT") == 4


def test_00022_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(21)
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
    assert "42501" in done.stdout + done.stderr
    still_21(database)


def test_00022_refuses_when_a_role_is_missing(harness: Harness, new_db: DbFactory) -> None:
    database = new_db(21)
    hidden = "prov_hidden_worker"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_WORKER} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(22)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_WORKER}"))
    assert f"missing role(s): {ROLE_WORKER}" in str(info.value)
    still_21(database)
