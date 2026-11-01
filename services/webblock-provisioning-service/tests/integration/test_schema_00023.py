"""Migration 00023: ``provisioning_bootstrap`` may read ``project_hosting.subdomain`` (owner
decision 0023 G18a, 25 October 2026), so the bootstrap can write the sub-domain into the tenant
namespace for the route-shape admission policy (0023 G18).

- before 00023 the bootstrap login cannot read the column; after it, it can, and it still reads no
  other column of the hosting row and writes nothing;
- exactly one privilege changes: the bootstrap's SELECT on ``subdomain``; every other role keeps
  exactly what it had;
- every row is kept;
- the migration runs only as the migrator, refuses when the bootstrap role is missing, and has no
  Down section and no default.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable

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
    scalar,
    seed,
)
from tests.integration._rows import insert, project_state
from tests.integration.test_schema_00013 import privileges
from tests.integration.test_schema_00022 import representative, snapshot

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00023_bootstrap_reads_subdomain.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
H = "provisioning.project_release_state"
READ_SUBDOMAIN = "SELECT subdomain FROM provisioning.project_hosting"
ROLES = (
    ROLE_API,
    ROLE_BOOTSTRAP,
    ROLE_BUILD,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_WORKER,
    "provisioning_payment_intake",
    "public",
)
STILL_REFUSED = (
    "SELECT * FROM provisioning.project_hosting",
    "SELECT last_command_number FROM provisioning.project_hosting",
    "SELECT routing_status FROM provisioning.project_hosting",
    "SELECT hosting_status FROM provisioning.project_hosting",
    "SELECT offline_reasons FROM provisioning.project_hosting",
    "SELECT owner_status_version FROM provisioning.project_hosting",
    "SELECT updated_at FROM provisioning.project_hosting",
    "SELECT * FROM provisioning.commands",
    "UPDATE provisioning.project_hosting SET subdomain = 'other'",
    "UPDATE provisioning.project_hosting SET updated_at = now()",
    "INSERT INTO provisioning.project_hosting (project_id) VALUES (gen_random_uuid())",
    "DELETE FROM provisioning.project_hosting",
    "TRUNCATE provisioning.project_hosting",
)


def at_22_with_a_site(new_db: DbFactory) -> Database:
    database = new_db(22)
    seed(database, insert(H, project_state("shop", subdomain="shop"), new=True))
    return database


def test_the_bootstrap_reads_the_subdomain_only_after_00023(new_db: DbFactory) -> None:
    database = at_22_with_a_site(new_db)
    with (
        database.login(ROLE_BOOTSTRAP) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        conn.execute(q(READ_SUBDOMAIN))
    database.migrate(23)
    with database.login(ROLE_BOOTSTRAP) as conn:
        assert column(conn, READ_SUBDOMAIN) == ["shop"]
        assert column(conn, "SELECT live_release_id FROM provisioning.project_hosting") == [None]


def test_the_bootstrap_still_reads_nothing_else_and_writes_nothing(new_db: DbFactory) -> None:
    database = at_22_with_a_site(new_db)
    database.migrate(23)
    with database.login(ROLE_BOOTSTRAP) as conn:
        for statement in STILL_REFUSED:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(q(statement))


def test_exactly_one_privilege_changes(new_db: DbFactory) -> None:
    database = new_db(22)
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in ROLES}
    database.migrate(23)
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in ROLES}
    assert after[ROLE_BOOTSTRAP] - before[ROLE_BOOTSTRAP] == {
        ("project_hosting", "subdomain", "SELECT")
    }
    assert before[ROLE_BOOTSTRAP] <= after[ROLE_BOOTSTRAP]
    for role in ROLES:
        if role != ROLE_BOOTSTRAP:
            assert after[role] == before[role], role


def test_upgrade_from_00022_keeps_rows(new_db: DbFactory) -> None:
    database = new_db(22)
    representative(database)
    before = snapshot(database)
    for table, found in before.items():
        assert found, f"the representative rows hold no row of {table}"
    database.migrate(23)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 23
    after = snapshot(database)
    for table, found in before.items():
        assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
            f"rows of {table} changed by 00023"
        )


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "DELETE", "TRUNCATE", "REVOKE", "ALTER"):
        assert re.search(rf"\b{word}\b", code) is None, word
    assert code.count("GRANT ") == 1


def test_00023_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(22)
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
        assert scalar(conn, APPLIED) == 22


def test_00023_refuses_when_the_bootstrap_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(22)
    hidden = "prov_hidden_bootstrap"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_BOOTSTRAP} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(23)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_BOOTSTRAP}"))
    assert f"missing role(s): {ROLE_BOOTSTRAP}" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 22
