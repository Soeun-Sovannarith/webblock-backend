"""Migration 00019: the enum values ``admin_suspended`` and ``redeploy`` only (decision 0021 D1;
Notion "5 · Provisioning", section "The next Provisioning migration").

- the two values are added in Notion's order: ``admin_suspended`` last in ``offline_reason``,
  ``redeploy`` before ``sync_domains`` in ``command_kind`` (decision 0021, lead ruling on Task 8);
- nothing else changes: every table, column, constraint, index, trigger, function and privilege is
  as at 00018, and every row is kept (the 00014 row snapshot);
- the migration runs only as the migrator, refuses when the API role is missing, and has no Down
  section and no default.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import cast

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
    q,
    rows,
    scalar,
    seed,
)
from tests.integration._rows import TABLES, Row, insert, table_name, world
from tests.integration.test_schema_00013 import privileges

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00019_admin_reason_and_redeploy_values.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
ROLES = (
    ROLE_API,
    ROLE_BOOTSTRAP,
    ROLE_BUILD,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_WORKER,
)
COMMAND_KINDS_AT_18 = [
    "deploy",
    "promote",
    "rollback",
    "suspend_hosting",
    "resume_hosting",
    "sync_domains",
]
COMMAND_KINDS_AT_19 = [
    "deploy",
    "promote",
    "rollback",
    "suspend_hosting",
    "resume_hosting",
    "redeploy",
    "sync_domains",
]
OFFLINE_REASONS_AT_18 = ["billing", "user_suspended", "project_deleted"]
OFFLINE_REASONS_AT_19 = ["billing", "user_suspended", "project_deleted", "admin_suspended"]


def enum_labels(conn: Conn) -> dict[str, list[str]]:
    """Every enum of the two schemas with its labels in sort order."""
    return {
        cast(str, name): cast(list[str], labels)
        for name, labels in rows(
            conn,
            "SELECT t.typnamespace::regnamespace::text || '.' || t.typname::text,"
            " array_agg(e.enumlabel::text ORDER BY e.enumsortorder)"
            " FROM pg_type AS t JOIN pg_enum AS e ON e.enumtypid = t.oid"
            " WHERE t.typnamespace::regnamespace::text"
            " IN ('provisioning', 'provisioning_ops') GROUP BY 1",
        )
    }


def catalog(conn: Conn) -> dict[str, object]:
    """Everything but the enum labels that a migration could change: columns, constraints,
    indexes, triggers, function bodies and every role's privileges."""
    return {
        "columns": rows(
            conn,
            "SELECT c.relname::text, a.attname::text, format_type(a.atttypid, a.atttypmod),"
            " a.attnotnull, a.atthasdef FROM pg_class AS c"
            " JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
            " WHERE c.relnamespace::regnamespace::text"
            " IN ('provisioning', 'provisioning_ops') ORDER BY 1, 2",
        ),
        "constraints": rows(
            conn,
            "SELECT conrelid::regclass::text, conname::text, pg_get_constraintdef(oid)"
            " FROM pg_constraint WHERE connamespace::regnamespace::text"
            " IN ('provisioning', 'provisioning_ops')"
            " ORDER BY 1, 2",
        ),
        "indexes": rows(
            conn,
            "SELECT schemaname::text, indexname::text, indexdef FROM pg_indexes"
            " WHERE schemaname IN ('provisioning', 'provisioning_ops') ORDER BY 1, 2",
        ),
        "triggers": rows(
            conn,
            "SELECT tgrelid::regclass::text, tgname::text, pg_get_triggerdef(oid)"
            " FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class"
            " WHERE relnamespace::regnamespace::text"
            " IN ('provisioning', 'provisioning_ops')) ORDER BY 1, 2",
        ),
        "functions": rows(
            conn,
            "SELECT p.oid::regprocedure::text, p.prosrc, p.prosecdef, p.proconfig"
            " FROM pg_proc AS p WHERE p.pronamespace::regnamespace::text"
            " IN ('provisioning', 'provisioning_ops') ORDER BY 1",
        ),
        "privileges": {role: sorted(privileges(conn, role)) for role in ROLES},
    }


def snapshot(conn: Conn) -> dict[str, list[Row]]:
    """Every row of every table (the 00014 helper)."""
    out: dict[str, list[Row]] = {}
    for table in TABLES:
        target = table_name(table, new=True)
        query = f"SELECT to_jsonb(t) FROM {target} AS t ORDER BY to_jsonb(t)::text"  # noqa: S608
        out[table] = [cast(Row, r[0]) for r in rows(conn, query)]
    return out


def test_00019_adds_only_two_enum_values(new_db: DbFactory) -> None:
    database = new_db(18)
    seed(database, *[insert(table, row, new=True, at_00020=False) for table, row in world()])
    with database.admin() as conn:
        labels_before = enum_labels(conn)
        catalog_before = catalog(conn)
        rows_before = snapshot(conn)
    assert labels_before["provisioning.command_kind"] == COMMAND_KINDS_AT_18
    assert labels_before["provisioning.offline_reason"] == OFFLINE_REASONS_AT_18
    database.migrate(19)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 19
        labels_after = enum_labels(conn)
        catalog_after = catalog(conn)
        rows_after = snapshot(conn)
    assert labels_after == {
        **labels_before,
        "provisioning.command_kind": COMMAND_KINDS_AT_19,
        "provisioning.offline_reason": OFFLINE_REASONS_AT_19,
    }
    for part, found in catalog_before.items():
        assert catalog_after[part] == found, f"{part} changed by 00019"
    for table in TABLES:
        assert rows_before[table], f"the representative rows hold no row of {table}"
        assert json.dumps(rows_after[table], sort_keys=True) == json.dumps(
            rows_before[table], sort_keys=True
        ), f"rows of {table} changed by 00019"


def test_00019_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(18)
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
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 18
        labels = enum_labels(conn)
    assert labels["provisioning.command_kind"] == COMMAND_KINDS_AT_18
    assert labels["provisioning.offline_reason"] == OFFLINE_REASONS_AT_18


def test_00019_refuses_when_the_api_role_is_missing(harness: Harness, new_db: DbFactory) -> None:
    database = new_db(18)
    hidden = "prov_hidden_api"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_API} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(19)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_API}"))
    assert f"missing role(s): {ROLE_API}" in str(info.value)
    assert "bootstrap-roles.sql" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 18
        assert enum_labels(conn)["provisioning.offline_reason"] == OFFLINE_REASONS_AT_18


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "DELETE", "GRANT", "CREATE", "DROP"):
        assert re.search(rf"\b{word}\b", code) is None, word
    assert len(re.findall(r"\bALTER TYPE\b", code)) == 2
