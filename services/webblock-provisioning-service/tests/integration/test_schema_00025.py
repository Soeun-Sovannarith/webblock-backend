"""TR-12: migration 00025 drops the five indexes Notion "5 · Provisioning" does not show
(constitution III.3: no extra index; audit ``ai/reviews/db-notion-audit-cqrs-layout.md`` section 7).

- before 00025 the five exist; after it none does;
- the indexes that hold a rule Notion states (one live and one promoting release per project, one
  active attempt per release and stage, one command in flight, the two key classes) still exist,
  and every other index, constraint, trigger, function, table and grant is unchanged;
- every row is kept;
- the migration runs only as the migrator and has no Down section.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable

from tests.integration._pg import GOOSE, MIGRATIONS_DIR, Conn, Database, rows, scalar
from tests.integration.test_schema_00022 import (
    functions,
    grants,
    representative,
    snapshot,
    structure,
)

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00025_drop_extra_indexes.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
DROPPED = (
    ("provisioning", "commands", "commands_domain_event_key"),
    ("provisioning", "release_steps", "release_steps_running_deadline"),
    ("provisioning_ops", "command_dispatch", "command_dispatch_due"),
    ("provisioning_ops", "source_published_events", "source_published_events_due"),
    ("provisioning_ops", "usage_outbox", "usage_outbox_due"),
)
NOTION_RULE_INDEXES = (
    "provisioning.releases_one_live_per_project",
    "provisioning.releases_one_promoting_per_project",
    "provisioning.release_steps_one_active_attempt",
    "provisioning.commands_one_in_flight",
    "provisioning.commands_idempotency_key",
    "provisioning.commands_hosting_event_key",
)


def present(conn: Conn, names: tuple[str, ...]) -> set[str]:
    """The names among ``names`` (``schema.index``) that are indexes in this database."""
    found = rows(
        conn,
        "SELECT schemaname || '.' || indexname FROM pg_indexes"
        " WHERE schemaname || '.' || indexname = ANY(%s::text[])",
        [list(names)],
    )
    return {str(r[0]) for r in found}


DROPPED_NAMES = tuple(f"{schema}.{name}" for schema, _, name in DROPPED)


def test_tr12_the_five_extra_indexes_are_gone_after_migrate_up(db: Database) -> None:
    """TR-12: after ``goose up`` none of the five indexes Notion does not show exists."""
    with db.admin() as conn:
        assert scalar(conn, APPLIED) == 25
        assert present(conn, DROPPED_NAMES) == set()


def test_tr12_00025_drops_exactly_the_five(new_db: DbFactory) -> None:
    """TR-12: the five exist at 00023 and are the only indexes 00025 removes; it adds none."""
    database = new_db(23)
    with database.admin() as conn:
        assert present(conn, DROPPED_NAMES) == set(DROPPED_NAMES)
        before = structure(conn)["indexes"]
    database.migrate(25)
    with database.admin() as conn:
        after = structure(conn)["indexes"]
    removed = {(schema, table, name) for schema, table, name, _ in before - after}
    assert removed == set(DROPPED)
    assert after <= before


def test_tr12_the_indexes_notion_rules_need_still_exist(db: Database) -> None:
    """TR-12: the unique indexes that hold Notion's rules (one live and one promoting release, one
    active attempt, one command in flight, the client and hosting key classes) are kept."""
    with db.admin() as conn:
        assert present(conn, NOTION_RULE_INDEXES) == set(NOTION_RULE_INDEXES)


def test_tr12_00025_changes_nothing_else(new_db: DbFactory) -> None:
    """TR-12: constraints, triggers, tables, types, functions and every grant are unchanged."""
    database = new_db(23)
    with database.admin() as conn:
        before, functions_before, grants_before = structure(conn), functions(conn), grants(conn)
    database.migrate(25)
    with database.admin() as conn:
        after, functions_after, grants_after = structure(conn), functions(conn), grants(conn)
    for kind in ("constraints", "constraint_flags", "triggers", "tables", "types"):
        assert before[kind], f"no {kind} read at 00023"
        assert after[kind] == before[kind], kind
    assert functions_after == functions_before
    for kind, found in grants_before.items():
        assert found, f"no {kind} grant read at 00023"
        assert grants_after[kind] == found, kind


def test_tr12_upgrade_from_00023_keeps_rows(new_db: DbFactory) -> None:
    """TR-12: every row of every table is kept."""
    database = new_db(23)
    representative(database)
    before = snapshot(database)
    for table, found in before.items():
        assert found, f"the representative rows hold no row of {table}"
    database.migrate(25)
    after = snapshot(database)
    for table, found in before.items():
        assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
            f"rows of {table} changed by 00025"
        )


def test_tr12_00025_has_no_down_section_and_drops_only_indexes() -> None:
    """TR-12: forward-only; the only changes are five plain ``DROP INDEX`` statements."""
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("CASCADE", "IF EXISTS", "CREATE", "ALTER", "GRANT", "REVOKE", "DELETE", "UPDATE"):
        assert re.search(rf"\b{word}\b", code) is None, word
    drops = re.findall(r"DROP INDEX (\w+)\.(\w+);", code)
    assert sorted(drops) == sorted((s.upper(), n.upper()) for s, _, n in DROPPED)
    assert code.count("DROP ") == len(DROPPED)


def test_tr12_00025_refuses_other_roles(new_db: DbFactory) -> None:
    """TR-12: run as any role but the migrator, 00025 stops with 42501 and drops nothing."""
    database = new_db(23)
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
        assert scalar(conn, APPLIED) == 23
        assert present(conn, DROPPED_NAMES) == set(DROPPED_NAMES)
