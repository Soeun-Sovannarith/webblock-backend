"""Migration 00021: ``project_hosting.subdomain`` NOT NULL after the operator's backfill (decision
0021 D1 and D8; Notion "5 · Provisioning", section "The next Provisioning migration").

- review focus 5: while any hosting row has no sub-domain, 00021 refuses with a message that names
  the operator command, and changes nothing; after the backfill it passes;
- at 00021 the column is NOT NULL and every backfill grant of 00020 is revoked, so the operator
  holds exactly its 00019 rights (the inbox manual retry); every other privilege is unchanged;
- every row is kept;
- the migration runs only as the migrator, refuses when the operator role is missing, and has no
  Down section and no default.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import cast

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
    Harness,
    column,
    q,
    rows,
    scalar,
    seed,
)
from tests.integration._rows import TABLES, Row, insert, project_state, table_name, uid, world
from tests.integration.test_schema_00013 import privileges

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00021_subdomain_not_null.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
H = "provisioning.project_release_state"
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
NULLABLE = (
    "SELECT is_nullable::text FROM information_schema.columns WHERE table_schema = 'provisioning'"
    " AND table_name = 'project_hosting' AND column_name = 'subdomain'"
)
SET_SUBDOMAIN = (
    "UPDATE provisioning.project_hosting SET subdomain = %s, updated_at = now()"
    " WHERE project_id = %s"
)
BACKFILL_ALL = (
    "UPDATE provisioning.project_hosting"
    " SET subdomain = 'site-' || substr(md5(project_id::text), 1, 12) WHERE subdomain IS NULL"
)
OLD, SET = uid("project:old"), uid("project:set")


def at_20_with_one_null_row(new_db: DbFactory) -> Database:
    """A database at 00020 holding one row from before 00020 (no sub-domain) and one set."""
    database = new_db(20)
    seed(
        database,
        insert(H, project_state("old", subdomain=None), new=True),
        insert(H, project_state("set", subdomain="shop"), new=True),
    )
    return database


def test_00021_refuses_while_a_row_has_no_subdomain(new_db: DbFactory) -> None:
    database = at_20_with_one_null_row(new_db)
    with database.admin() as conn:
        before = rows(conn, "SELECT to_jsonb(h) FROM provisioning.project_hosting AS h ORDER BY 1")
    with pytest.raises(RuntimeError) as info:
        database.migrate(21)
    text = str(info.value)
    assert (
        '1 hosting row(s) have no subdomain; run the operator command "subdomain set" for each'
        " (docs/runbooks/operator.md)"
    ) in text
    assert "23502" in text
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 20
        assert column(conn, NULLABLE) == ["YES"]
        after = rows(conn, "SELECT to_jsonb(h) FROM provisioning.project_hosting AS h ORDER BY 1")
        assert after == before
    # The operator's update grant is still there: the backfill can go on.
    with database.login(ROLE_OPERATOR) as conn, conn.transaction():
        conn.execute(q(SET_SUBDOMAIN), ("old-site", OLD))


def test_00021_passes_after_backfill(new_db: DbFactory) -> None:
    database = at_20_with_one_null_row(new_db)
    with database.login(ROLE_OPERATOR) as conn, conn.transaction():
        conn.execute(q(SET_SUBDOMAIN), ("old-site", OLD))
    database.migrate(21)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 21
        assert column(conn, NULLABLE) == ["NO"]
        assert dict(
            rows(conn, "SELECT project_id, subdomain FROM provisioning.project_hosting")
        ) == {OLD: "old-site", SET: "shop"}
    with database.login(ROLE_OPERATOR) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(q(SET_SUBDOMAIN), ("again", OLD))
        # The read goes too: "subdomain missing" runs only between 00020 and 00021.
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                q("SELECT project_id FROM provisioning.project_hosting WHERE subdomain IS NULL")
            )
    # The column itself now refuses a NULL, even on the seeding path that skips the guards.
    with pytest.raises(psycopg.errors.NotNullViolation):
        seed(database, insert(H, project_state("new", subdomain=None), new=True))


def snapshot(database: Database) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    with database.admin() as conn:
        for table in TABLES:
            target = table_name(table, new=True)
            query = f"SELECT to_jsonb(t) FROM {target} AS t"  # noqa: S608
            found = [cast(Row, r[0]) for r in rows(conn, query)]
            out[table] = sorted(found, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def test_upgrade_from_00020_keeps_rows(new_db: DbFactory) -> None:
    database = new_db(20)
    seed(database, *[insert(table, row, new=True) for table, row in world()], BACKFILL_ALL)
    before = snapshot(database)
    database.migrate(21)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 21
    after = snapshot(database)
    for table, found in before.items():
        assert found, f"the representative rows hold no row of {table}"
        assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
            f"rows of {table} changed by 00021"
        )


CHECK_FUNCTION = "provisioning.offline_reasons_distinct(provisioning.offline_reason[])"


def operator_rights(database: Database) -> tuple[set[tuple[str, str, str]], bool]:
    """The operator's table, column and schema privileges, and EXECUTE on the check function."""
    with database.admin() as conn:
        execute = scalar(
            conn,
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (ROLE_OPERATOR, CHECK_FUNCTION),
        )
        return privileges(conn, ROLE_OPERATOR), bool(execute)


def test_the_operator_is_back_to_its_00019_rights(new_db: DbFactory) -> None:
    """The backfill grants of 00020 are temporary (decision 0021): 00021 takes all of them back,
    so the operator holds exactly what it held at 00019 (the inbox manual retry). Every other role
    is unchanged by 00021."""
    at_19 = operator_rights(new_db(19))
    database = new_db(20)
    with database.admin() as conn:
        others_before = {r: privileges(conn, r) for r in ROLES if r != ROLE_OPERATOR}
    assert operator_rights(database) != at_19  # 00020 granted the backfill
    database.migrate(21)
    assert operator_rights(database) == at_19
    assert at_19[1] is False
    assert ("provisioning", "*", "USAGE") not in at_19[0]
    with database.admin() as conn:
        assert {r: privileges(conn, r) for r in ROLES if r != ROLE_OPERATOR} == others_before


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "DELETE", "TRUNCATE"):
        assert re.search(rf"\b{word}\b", code) is None, word


def test_00021_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(20)
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
        assert scalar(conn, APPLIED) == 20
        assert column(conn, NULLABLE) == ["YES"]


def test_00021_refuses_when_the_operator_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(20)
    hidden = "prov_hidden_operator"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_OPERATOR} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(21)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_OPERATOR}"))
    assert f"missing role(s): {ROLE_OPERATOR}" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 20
