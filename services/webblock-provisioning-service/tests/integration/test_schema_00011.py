"""Migration 00011, the revision 19 rename (decision 0012), on a real PostgreSQL 18.

What the rename can break, and what is tested:

- the migration itself: from nothing to 00011, a rerun that applies nothing, the refusal when the
  role ``provisioning_hosting_intake`` is missing;
- the upgrade: a database at 00010 with representative rows of every table keeps every row under
  the new names, ``serving`` now ``online``;
- the catalogue: every old name is gone, every new name of the decision exists, nothing else does;
- the grants of every role are unchanged, under the new names;
- every renamed constraint still fires, under its new name, and the three unique indexes the
  stores map errors from keep their names;
- every guard still refuses what it refused before (and admits what it admitted), on a fresh
  database and on the upgraded one, with the hosting-intake role replacing the payment-intake role.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import cast

import psycopg
import pytest
from tests.integration import _catalog_00011 as expected
from tests.integration._pg import (
    ROLE_API,
    ROLE_BUILD,
    ROLE_HOSTING_INTAKE,
    ROLE_INTAKE,
    ROLE_MIGRATOR,
    ROLE_OPERATOR,
    ROLE_PAYMENT_INTAKE,
    ROLE_WORKER,
    Conn,
    Database,
    Harness,
    allowed,
    column,
    migrate_with_backfill,
    q,
    refused,
    rows,
    scalar,
    seed,
)
from tests.integration._rows import (
    COLUMN_RENAMES,
    TABLE_RENAMES,
    TABLES,
    Row,
    at,
    command,
    command_work,
    digest,
    fingerprint,
    inbox,
    insert,
    project_state,
    release,
    rename_row,
    sha,
    table_name,
    uid,
    world,
)

type DbFactory = Callable[[int | None], Database]
type EmptyDbFactory = Callable[[], Database]

SCHEMAS = ("provisioning", "provisioning_ops")

# The six tables by their logical (revision 16) names, as the row builders use them.
R = "provisioning.releases"
S = "provisioning.project_release_state"
C = "provisioning.commands"
ST = "provisioning.release_steps"
W = "provisioning_ops.command_work"
IB = "provisioning_ops.source_published_inbox"


def snapshot(conn: Conn, *, new: bool) -> dict[str, list[Row]]:
    """Every row of the six tables as JSON objects, keyed by the logical table name."""
    out: dict[str, list[Row]] = {}
    for table in TABLES:
        target = table_name(table, new=new)
        query = f"SELECT to_jsonb(t) FROM {target} AS t ORDER BY to_jsonb(t)::text"  # noqa: S608
        out[table] = [cast(Row, r[0]) for r in rows(conn, query)]
    return out


# --- the migration itself ------------------------------------------------------------------------


def test_migrates_from_nothing_to_00011_and_a_rerun_applies_nothing(
    new_empty_db: EmptyDbFactory,
) -> None:
    database = new_empty_db()
    first = database.migrate()
    assert "OK" in first and "00011_rename_for_clarity.sql" in first, first
    with database.admin() as conn:
        sql_text = "SELECT version_id FROM goose_db_version WHERE is_applied ORDER BY version_id"
        assert column(conn, sql_text)[:12] == [0, *range(1, 12)]
    again = database.migrate()
    assert "no migrations to run" in again, again
    with database.admin() as conn:
        assert scalar(conn, "SELECT count(*) FROM goose_db_version WHERE version_id = 11") == 1


def test_migration_refuses_when_the_hosting_intake_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(10)
    hidden = "prov_hidden_hosting_intake"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_HOSTING_INTAKE} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate()
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_HOSTING_INTAKE}"))
    assert f"missing role(s): {ROLE_HOSTING_INTAKE}" in str(info.value)
    assert "bootstrap-roles.sql" in str(info.value)
    # The whole file is one transaction: nothing was renamed, the version is still 10.
    with database.admin() as conn:
        assert scalar(conn, "SELECT to_regclass('provisioning.project_release_state') IS NOT NULL")
        applied = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
        assert scalar(conn, applied) == 10
    # With the role back, the same database migrates.
    database.migrate()
    with database.admin() as conn:
        assert scalar(conn, "SELECT to_regclass('provisioning.project_hosting') IS NOT NULL")


# --- the upgrade ---------------------------------------------------------------------------------


@dataclass
class Upgrade:
    database: Database
    before: dict[str, list[Row]]


@pytest.fixture
def upgrade(new_db: DbFactory) -> Upgrade:
    """A database at 00010 holding the representative rows, snapshotted, then migrated."""
    database = new_db(10)
    seed(database, *[insert(table, row, new=False) for table, row in world()])
    with database.admin() as conn:
        before = snapshot(conn, new=False)
    database.migrate(11)  # pinned: 00018 renames a column again and has its own upgrade test
    return Upgrade(database, before)


def by_content(found: list[Row]) -> list[Row]:
    return sorted(found, key=lambda r: json.dumps(r, sort_keys=True))


def test_upgrade_keeps_every_row_under_the_new_names_and_serving_becomes_online(
    upgrade: Upgrade,
) -> None:
    with upgrade.database.admin() as conn:
        after = snapshot(conn, new=True)
    assert set(after) == set(upgrade.before) == set(TABLES)
    for table in TABLES:
        assert upgrade.before[table], f"the representative rows hold no row of {table}"
        want = by_content([rename_row(table, row, at_00018=False) for row in upgrade.before[table]])
        assert by_content(after[table]) == want, f"rows of {table} changed by the upgrade"
    # Both hosting states are present, so the value rename is really exercised.
    assert {r["hosting_status"] for r in after[S]} == {"online", "offline"}
    assert "serving" not in json.dumps(after)


# --- the catalogue -------------------------------------------------------------------------------


@pytest.fixture
def latest(new_db: DbFactory) -> Generator[Conn]:
    """The catalogue as migration 00011 leaves it (later migrations change it on purpose)."""
    with new_db(11).admin() as conn:
        yield conn


def test_catalogue_holds_exactly_the_revision_19_tables_and_columns(latest: Conn) -> None:
    tables = column(
        latest,
        "SELECT table_schema || '.' || table_name FROM information_schema.tables"
        " WHERE table_schema IN ('provisioning', 'provisioning_ops') AND table_type = 'BASE TABLE'"
        " ORDER BY 1",
    )
    assert tables == sorted(expected.EXPECTED_COLUMNS)
    for table, columns in expected.EXPECTED_COLUMNS.items():
        schema, name = table.split(".")
        found = column(
            latest,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, name),
        )
        assert tuple(cast(Sequence[str], found)) == columns, table


def test_catalogue_holds_exactly_the_revision_19_constraints_indexes_triggers_functions(
    latest: Conn,
) -> None:
    constraints = {
        (r[0], r[1])
        for r in rows(
            latest,
            "SELECT c.conrelid::regclass::text, c.conname::text FROM pg_constraint AS c"
            " JOIN pg_namespace AS n ON n.oid = c.connamespace"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops') AND c.contype <> 'n'",
        )
    }
    assert constraints == expected.EXPECTED_CONSTRAINTS
    indexes = {
        (r[0], r[1])
        for r in rows(
            latest,
            "SELECT schemaname || '.' || tablename, indexname FROM pg_indexes"
            " WHERE schemaname IN ('provisioning', 'provisioning_ops')",
        )
    }
    assert indexes == expected.EXPECTED_INDEXES
    triggers = {
        (r[0], r[1], r[2])
        for r in rows(
            latest,
            "SELECT tgrelid::regclass::text, tgname::text, tgconstraint <> 0 FROM pg_trigger"
            " WHERE NOT tgisinternal AND tgrelid::regclass::text ~ '^provisioning'",
        )
    }
    assert triggers == expected.EXPECTED_TRIGGERS
    functions = {
        r[0]
        for r in rows(
            latest,
            "SELECT n.nspname || '.' || p.proname FROM pg_proc AS p"
            " JOIN pg_namespace AS n ON n.oid = p.pronamespace"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
        )
    }
    assert functions == expected.EXPECTED_FUNCTIONS


def test_every_not_null_constraint_is_named_after_its_table_and_column(latest: Conn) -> None:
    found = rows(
        latest,
        "SELECT c.conrelid::regclass::text, c.conname::text, a.attname::text"
        " FROM pg_constraint AS c"
        " JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]"
        " WHERE c.contype = 'n'"
        " AND c.connamespace IN ('provisioning'::regnamespace, 'provisioning_ops'::regnamespace)",
    )
    not_null_columns = scalar(
        latest,
        "SELECT count(*) FROM pg_attribute AS a JOIN pg_class AS c ON c.oid = a.attrelid"
        " WHERE a.attnotnull AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind = 'r'"
        " AND c.relnamespace IN ('provisioning'::regnamespace, 'provisioning_ops'::regnamespace)",
    )
    assert len(found) == not_null_columns and len(found) > 40
    for table, name, col in found:
        assert name == f"{table.split('.')[1]}_{col}_not_null", (table, name, col)


def test_types_and_enum_labels(latest: Conn) -> None:
    labels = {
        r[0]: r[1]
        for r in rows(
            latest,
            "SELECT n.nspname || '.' || t.typname,"
            " array_agg(e.enumlabel::text ORDER BY e.enumsortorder)"
            " FROM pg_type AS t JOIN pg_namespace AS n ON n.oid = t.typnamespace"
            " JOIN pg_enum AS e ON e.enumtypid = t.oid"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops') GROUP BY 1",
        )
    }
    assert labels == {
        "provisioning.release_status": [
            *("accepted", "building", "built", "candidate_ready", "promoting"),
            *("live", "superseded", "failed", "rolled_back"),
        ],
        "provisioning.release_step_name": [
            *("build", "access_setup", "candidate", "promotion", "observation", "rollback"),
        ],
        "provisioning.release_step_status": ["pending", "running", "succeeded", "failed"],
        "provisioning.routing_status": ["stable", "changing", "uncertain"],
        "provisioning.hosting_status": ["online", "offline"],
        "provisioning.command_kind": [
            *("deploy", "promote", "rollback", "suspend_hosting", "resume_hosting"),
        ],
        "provisioning.command_status": ["accepted", "running", "succeeded", "failed", "skipped"],
        "provisioning_ops.source_event_status": ["pending", "accepted", "rejected", "failed"],
    }
    with pytest.raises(psycopg.errors.InvalidTextRepresentation):
        latest.execute("SELECT 'serving'::provisioning.hosting_status")


OLD_NAME = re.compile(
    r"\b(project_release_state|active_release_id|operation_generation|route_state|hosting_state"
    r"|serving|source_generation_request_id|source_repository_id|source_commit_sha|image_digest"
    r"|fencing_generation|request_hash|requested_by|reason_code|details_ref|command_work"
    r"|lease_token|claimed_by|lease_expires_at|claim_count|next_dispatch_at|last_confirmed_at"
    r"|retry_not_before|last_error_code|source_published_inbox|inbox_status|repository_id"
    r"|commit_sha|conflict_count|last_conflict_at|attempt_budget|last_redriven_by"
    r"|last_redriven_at|provisioning_payment_intake)\b"
)


def test_no_function_body_names_an_old_object(latest: Conn) -> None:
    bodies = rows(
        latest,
        "SELECT p.proname::text, p.prosrc FROM pg_proc AS p"
        " JOIN pg_namespace AS n ON n.oid = p.pronamespace"
        " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
    )
    assert len(bodies) == len(expected.EXPECTED_FUNCTIONS)
    stale = {name: OLD_NAME.findall(body) for name, body in bodies if OLD_NAME.search(body)}
    assert not stale, stale


# --- grants --------------------------------------------------------------------------------------

ROLES = (
    ROLE_MIGRATOR,
    ROLE_API,
    ROLE_WORKER,
    ROLE_INTAKE,
    ROLE_BUILD,
    ROLE_OPERATOR,
    ROLE_PAYMENT_INTAKE,
    ROLE_HOSTING_INTAKE,
    "public",
)
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
# Guard function names of revision 16 -> 19, from decision 0012 section 3.2.
P, PO = "provisioning", "provisioning_ops"
FUNCTION_RENAMES = {
    (P, "project_release_state_generation_step"): "project_hosting_generation_step",
    (P, "project_release_state_generation_has_command"): "project_hosting_generation_has_command",
    (P, "project_release_state_insert_shape"): "project_hosting_insert_shape",
    (PO, "command_work_insert_shape"): "command_dispatch_insert_shape",
    (PO, "command_work_monotonic"): "command_dispatch_monotonic",
    (PO, "source_published_inbox_insert_shape"): "source_published_events_insert_shape",
    (PO, "source_published_inbox_update_shape"): "source_published_events_update_shape",
}
type Held = set[tuple[str, str, str, str, str]]


def privilege_matrix(conn: Conn, *, new: bool) -> Held:
    """Every privilege held by each role, keyed by the logical (revision 16) object names."""
    new_short = {old.split(".")[1]: renamed for old, renamed in TABLE_RENAMES.items()}
    back = {(new_short.get(t, t), c_new): c_old for (t, c_old), c_new in COLUMN_RENAMES.items()}
    held: Held = set()
    for table in TABLES:
        actual = table_name(table, new=new)
        schema, short = actual.split(".")
        columns = column(
            conn,
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            (schema, short),
        )
        for role in ROLES:
            for priv in TABLE_PRIVILEGES:
                if scalar(conn, "SELECT has_table_privilege(%s, %s, %s)", (role, actual, priv)):
                    held.add((role, "table", table, "*", priv))
            for col in cast(Sequence[str], columns):
                logical = back.get((short, col), col) if new else col
                for priv in COLUMN_PRIVILEGES:
                    has = "SELECT has_column_privilege(%s, %s, %s, %s)"
                    if scalar(conn, has, (role, actual, col, priv)):
                        held.add((role, "column", table, logical, priv))
    reverse = {(schema, new_name): old for (schema, old), new_name in FUNCTION_RENAMES.items()}
    functions = cast(
        Sequence[str],
        column(
            conn,
            "SELECT n.nspname || '.' || p.proname FROM pg_proc AS p"
            " JOIN pg_namespace AS n ON n.oid = p.pronamespace"
            " WHERE n.nspname IN ('provisioning', 'provisioning_ops')",
        ),
    )
    for role in ROLES:
        for schema in SCHEMAS:
            for priv in ("USAGE", "CREATE"):
                if scalar(conn, "SELECT has_schema_privilege(%s, %s, %s)", (role, schema, priv)):
                    held.add((role, "schema", schema, "*", priv))
        for function in functions:
            schema, name = function.split(".")
            logical_fn = f"{schema}.{reverse.get((schema, name), name) if new else name}"
            has = "SELECT has_function_privilege(%s, (%s || '()')::regprocedure, 'EXECUTE')"
            if scalar(conn, has, (role, function)):
                held.add((role, "function", logical_fn, "*", "EXECUTE"))
    return held


def test_grants_of_every_role_are_unchanged_under_the_new_names(new_db: DbFactory) -> None:
    database = new_db(10)
    with database.admin() as conn:
        before = privilege_matrix(conn, new=False)
    database.migrate(11)  # 00012 changes the worker's grants on purpose; it has its own test
    with database.admin() as conn:
        after = privilege_matrix(conn, new=True)
    assert before == after
    # The matrix is not vacuous: it holds the grants the roles are known for, and not others.
    assert (ROLE_API, "table", C, "*", "INSERT") in after
    assert (ROLE_WORKER, "column", W, "lease_token", "UPDATE") in after
    assert (ROLE_OPERATOR, "column", IB, "attempt_budget", "UPDATE") in after
    assert (ROLE_INTAKE, "column", R, "image_digest", "INSERT") not in after
    assert (ROLE_HOSTING_INTAKE, "table", C, "*", "INSERT") not in after
    assert not [h for h in after if h[0] == "public" and h[1] in {"table", "column"}]
    assert len(after) > 150


# --- every renamed constraint still fires, under its new name ------------------------------------


@dataclass(frozen=True)
class ConstraintCase:
    constraint: str
    rows: Callable[[], list[tuple[str, Row]]]
    sqlstate: str = "23514"


def one(table: str, row: Row) -> Callable[[], list[tuple[str, Row]]]:
    return lambda: [(table, row)]


def two(first: tuple[str, Row], second: tuple[str, Row]) -> Callable[[], list[tuple[str, Row]]]:
    return lambda: [first, second]


UNIQUE = "23505"
BEYOND_JSON = 2**53  # one above the largest number a JSON number keeps exactly


def rls(constraint: str, status: str = "accepted", **over: object) -> ConstraintCase:
    return ConstraintCase(constraint, one(R, release("x", "px", status, **over)))


def with_columns(row: Row, over: Row) -> Row:
    row.update(over)
    return row


def hosting(constraint: str, **over: object) -> ConstraintCase:
    return ConstraintCase(constraint, one(S, with_columns(project_state("px"), over)))


def cmds(constraint: str, number: int = 1, **over: object) -> ConstraintCase:
    row = with_columns(command("x", "px", "rx", number, "succeeded"), over)
    return ConstraintCase(constraint, one(C, row))


def work(constraint: str, **over: object) -> ConstraintCase:
    return ConstraintCase(constraint, one(W, command_work("x", **over)))


def evt(constraint: str, status: str = "pending", **over: object) -> ConstraintCase:
    return ConstraintCase(constraint, one(IB, inbox("x", "px", status, **over)))


def dup(constraint: str, first: tuple[str, Row], second: tuple[str, Row]) -> ConstraintCase:
    return ConstraintCase(constraint, two(first, second), UNIQUE)


def hosting_cmd(name: str, number: int, kind: str) -> tuple[str, Row]:
    row = command(name, "px", None, number, "succeeded", kind=kind, idempotency_key="event-1")
    return C, row


def deploy(name: str, number: int, status: str, **over: object) -> tuple[str, Row]:
    return C, with_columns(command(name, "px", "rx", number, status), over)


SPE = "source_published_events"
CONSTRAINT_CASES = [
    rls("releases_github_repository_id_check", source_repository_id=0),
    rls("releases_github_repository_id_check", source_repository_id=BEYOND_JSON),
    rls("releases_git_commit_sha_check", source_commit_sha="not-a-sha"),
    rls("releases_container_image_digest_check", "built", image_digest="sha256:short"),
    rls("releases_image_after_build", "built", image_digest=None),
    rls("releases_no_image_before_built", image_digest=digest("x")),
    hosting("project_hosting_last_command_number_check", operation_generation=-1),
    hosting("project_hosting_last_command_number_check", operation_generation=BEYOND_JSON),
    dup("project_hosting_pkey", (S, project_state("px")), (S, project_state("px"))),
    cmds("commands_command_number_check", 0),
    cmds("commands_command_number_check", BEYOND_JSON),
    cmds("commands_request_fingerprint_check", request_hash="zz" * 32),
    cmds("commands_requested_by_subject_by_kind", requested_by=None),
    cmds("commands_requested_by_subject_by_kind", requested_by=""),
    dup("commands_command_number_key", deploy("a", 1, "succeeded"), deploy("b", 1, "succeeded")),
    dup(
        "commands_idempotency_key",
        deploy("a", 1, "succeeded", idempotency_key="same"),
        deploy("b", 2, "succeeded", idempotency_key="same"),
    ),
    dup(
        "commands_hosting_event_key",
        hosting_cmd("a", 1, "suspend_hosting"),
        hosting_cmd("b", 2, "resume_hosting"),
    ),
    dup("commands_one_in_flight", deploy("a", 1, "accepted"), deploy("b", 2, "running")),
    dup("command_dispatch_pkey", (W, command_work("x")), (W, command_work("x"))),
    work("command_dispatch_publish_attempts_check", publish_attempts=-1),
    work("command_dispatch_execution_attempts_check", claim_count=-1),
    work("command_dispatch_claim_number_check", lease_token=-1),
    work("command_dispatch_claimed_by_worker_check", claimed_by="", lease_expires_at=at(5)),
    work("command_dispatch_claim_owner_with_expiry", claimed_by="w-1", lease_expires_at=None),
    work("command_dispatch_correlation_id_check", correlation_id=""),
    work("command_dispatch_last_failure_code_check", last_error_code="BAD CODE"),
    dup(f"{SPE}_pkey", (IB, inbox("x", "px")), (IB, inbox("x", "px"))),
    evt(f"{SPE}_github_repository_id_check", repository_id=0),
    evt(f"{SPE}_git_commit_sha_check", commit_sha="not-a-sha"),
    evt(f"{SPE}_attempts_check", attempts=-1),
    evt(f"{SPE}_max_attempts_check", attempt_budget=0),
    evt(f"{SPE}_mismatched_repeat_count_check", conflict_count=-1),
    evt(f"{SPE}_mismatched_repeat_time_matches_count", conflict_count=1),
    evt(f"{SPE}_manual_retry_by_check", last_redriven_by="", last_redriven_at=at(1)),
    evt(f"{SPE}_manual_retry_together", last_redriven_by="operator-1"),
    evt(f"{SPE}_outcome_code_check", "failed", outcome_code="BAD", finished_at=at(2)),
    evt(f"{SPE}_outcome_matches_status", "failed", finished_at=at(2)),
    evt(f"{SPE}_release_matches_status", "accepted", finished_at=at(2)),
    evt(f"{SPE}_finished_matches_status", finished_at=at(2)),
]


def case_id(case: ConstraintCase) -> str:
    return f"{case.constraint}-{CONSTRAINT_CASES.index(case)}"


@pytest.mark.parametrize("case", CONSTRAINT_CASES, ids=case_id)
def test_renamed_constraint_fires_under_its_new_name(
    new_db: DbFactory, case: ConstraintCase
) -> None:
    """At 00011 (pinned: 00018 renames the repository checks again; test_schema_00018 has them)."""
    statements = [insert(table, row, new=True, at_00018=False) for table, row in case.rows()]
    with pytest.raises(psycopg.Error) as info:
        seed(new_db(11), *statements)
    found = (info.value.sqlstate, info.value.diag.constraint_name)
    assert found == (case.sqlstate, case.constraint), str(info.value)


def test_three_unique_indexes_the_stores_map_errors_from_keep_their_names(latest: Conn) -> None:
    found = rows(
        latest,
        "SELECT i.indexrelid::regclass::text, i.indisunique, i.indpred IS NOT NULL"
        " FROM pg_index AS i WHERE i.indrelid = 'provisioning.commands'::regclass",
    )
    flags = {r[0]: (r[1], r[2]) for r in found}
    for name in (
        "commands_idempotency_key",
        "commands_hosting_event_key",
        "commands_one_in_flight",
    ):
        assert flags[f"provisioning.{name}"] == (True, True), name


# --- the guards, on a fresh database and on the upgraded one -------------------------------------


@pytest.fixture(params=["fresh", "upgraded"])
def world_db(request: pytest.FixtureRequest, new_db: DbFactory) -> Database:
    """The representative rows on a database at 00011: created there, or upgraded from 00010."""
    if request.param == "fresh":
        database = new_db(None)
        seed(database, *[insert(table, row, new=True) for table, row in world()])
    else:
        database = new_db(10)
        seed(database, *[insert(table, row, new=False) for table, row in world()])
        migrate_with_backfill(database)
    return database


def rel(name: str) -> object:
    return uid(f"release:{name}")


def cmd(name: str) -> object:
    return uid(f"command:{name}")


def proj(name: str) -> object:
    return uid(f"project:{name}")


def req(name: str) -> object:
    return uid(f"request:{name}")


def step_id(name: str) -> object:
    return uid(f"step:{name}")


def test_renamed_foreign_keys_fire_under_their_new_names(world_db: Database) -> None:
    fk = "23503"
    with world_db.as_role(ROLE_MIGRATOR) as owner:
        with refused(owner, constraint="project_hosting_live_release_fk", sqlstate=fk):
            owner.execute(
                "UPDATE provisioning.project_hosting SET live_release_id = %s"
                " WHERE project_id = %s",
                (uid("release:nowhere"), proj("p4")),
            )
        with refused(owner, constraint="command_dispatch_command_fk", sqlstate=fk):
            owner.execute(q(NEW_DISPATCH), (cmd("nowhere"), 0, 0, 0))
        with refused(owner, constraint="source_published_events_release_fk", sqlstate=fk):
            owner.execute(
                "UPDATE provisioning_ops.source_published_events"
                " SET status = 'accepted', release_id = %s, finished_at = now(), updated_at = now()"
                " WHERE generation_request_id = %s",
                (uid("release:nowhere"), req("p3-pending")),
            )


type Who = tuple[str, ...] | None  # None: the owner, provisioning_migrator by SET ROLE


@dataclass(frozen=True)
class GuardCase:
    label: str
    guard: str
    who: Who
    sql_text: str
    params: tuple[object, ...]

    @property
    def table(self) -> str:
        """The table in the guard's error: every guard is named after its table."""
        prefixes = (
            *("releases", "commands", "project_hosting", "release_steps"),
            *("command_dispatch", "source_published_events"),
        )
        return max((p for p in prefixes if self.guard.startswith(p + "_")), key=len)


def session(database: Database, who: Who) -> AbstractContextManager[Conn]:
    return database.as_role(ROLE_MIGRATOR) if who is None else database.login(*who)


def g(label: str, guard: str, who: Who, sql_text: str, *params: object) -> GuardCase:
    return GuardCase(label, guard, who, sql_text, params)


def upd(table: str, assignments: str, key: str) -> str:
    return f"UPDATE {table} SET {assignments} WHERE {key} = %s"  # noqa: S608 - constants only


NEW_RELEASE = (
    "INSERT INTO provisioning.releases (release_id, project_id, generation_request_id,"
    " repository_id, git_commit_sha, container_image_digest, status, created_at, updated_at,"
    " promoted_at) VALUES (%s, %s, %s, 7, %s, %s, %s, now(), now(), %s)"
)
# The columns provisioning_intake may insert into releases (migration 00008).
NEW_RELEASE_INTAKE = (
    "INSERT INTO provisioning.releases (release_id, project_id, generation_request_id,"
    " repository_id, git_commit_sha, status, created_at, updated_at)"
    " VALUES (%s, %s, %s, 7, %s, %s, now(), now())"
)
NEW_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at,"
    " hosting_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), %s)"
)
NEW_STEP = (
    "INSERT INTO provisioning.release_steps (release_step_id, release_id, step_name, attempt,"
    " status) VALUES (%s, %s, %s, %s, %s)"
)
NEW_DISPATCH = (
    "INSERT INTO provisioning_ops.command_dispatch (command_id, correlation_id, deadline_at,"
    " next_publish_at, publish_attempts, execution_attempts, claim_number, created_at, updated_at)"
    " VALUES (%s, 'corr', now(), now(), %s, %s, %s, now(), now())"
)
NEW_EVENT = (
    "INSERT INTO provisioning_ops.source_published_events (project_id, generation_request_id,"
    " repository_id, git_commit_sha, status, attempts, next_attempt_at,"
    " mismatched_repeat_count, received_at, updated_at, max_attempts, last_manual_retry_by,"
    " last_manual_retry_at) VALUES (%s, %s, 9, %s, %s, %s, now(), 0, now(), now(), 3, %s, %s)"
)
# At the latest migration: the columns of 00018 and the sub-domain of 00020 that every insert names.
NEW_HOSTING = (
    "INSERT INTO provisioning.project_hosting (project_id, live_release_id, last_command_number,"
    " routing_status, hosting_status, updated_at, offline_reasons, owner_status_version,"
    " subdomain) VALUES (%s, %s, %s, %s, %s, now(), '{}', 0, 'ghost')"
)
REL = "provisioning.releases"
CMD = "provisioning.commands"
HOST = "provisioning.project_hosting"
STEP = "provisioning.release_steps"
DISP = "provisioning_ops.command_dispatch"
EVT = "provisioning_ops.source_published_events"
GHOST = uid("project:ghost")
ANY = uid("anything")
RETRY = (
    "status = 'pending', outcome_code = NULL, finished_at = NULL, max_attempts = 4,"
    " last_manual_retry_by = 'operator-2', last_manual_retry_at = now()"
)
KEY = "generation_request_id"

# fmt: off
REFUSED: list[GuardCase] = [
    # releases
    g("release commit sha", "releases_identity_immutable", None,
      upd(REL, "git_commit_sha = %s", "release_id"), sha("other"), rel("p1-live")),
    g("release github repository", "releases_identity_immutable", None,
      upd(REL, "repository_id = 5", "release_id"), rel("p1-live")),
    g("release generation request", "releases_identity_immutable", None,
      upd(REL, "generation_request_id = %s", "release_id"), ANY, rel("p1-live")),
    g("recorded image digest", "releases_identity_immutable", None,
      upd(REL, "container_image_digest = %s", "release_id"), digest("other"), rel("p1-built")),
    g("failed release reopened", "releases_terminal_status_final", None,
      upd(REL, "status = 'built', failure_code = NULL, failure_summary = NULL", "release_id"),
      rel("p1-failed")),
    g("release inserted live", "releases_insert_shape", None, NEW_RELEASE,
      ANY, proj("p4"), req("new"), sha("new"), digest("new"), "live", None),
    g("release inserted with a digest", "releases_insert_shape", None, NEW_RELEASE,
      ANY, proj("p4"), req("new"), sha("new"), digest("new"), "accepted", None),
    g("release for a project without a row", "releases_insert_shape", None, NEW_RELEASE,
      ANY, GHOST, req("new"), sha("new"), None, "accepted", None),
    g("build skips building", "releases_build_transition", (ROLE_BUILD,),
      upd(REL, "status = 'built', container_image_digest = %s, updated_at = now()", "release_id"),
      digest("new"), rel("p3-accepted")),
    g("build promotes a built release", "releases_build_transition", (ROLE_BUILD,),
      upd(REL, "status = 'promoting', updated_at = now()", "release_id"), rel("p1-built")),
    # commands
    g("command idempotency key", "commands_identity_immutable", None,
      upd(CMD, "idempotency_key = 'other'", "command_id"), cmd("p1-1")),
    g("command number", "commands_identity_immutable", None,
      upd(CMD, "command_number = 99", "command_id"), cmd("p1-1")),
    g("command requester", "commands_identity_immutable", None,
      upd(CMD, "requested_by_subject = 'someone-else'", "command_id"), cmd("p1-1")),
    g("command fingerprint", "commands_identity_immutable", None,
      upd(CMD, "request_fingerprint = %s", "command_id"), fingerprint("other"), cmd("p1-1")),
    g("succeeded command reopened", "commands_terminal_status_final", None,
      upd(CMD, "status = 'running'", "command_id"), cmd("p1-1")),
    g("command with the wrong number", "commands_insert_shape", None, NEW_COMMAND,
      ANY, proj("p2"), rel("p2-built"), "deploy", "new-key", fingerprint("n"), 3, "s", "accepted",
      None),
    g("command inserted running", "commands_insert_shape", None, NEW_COMMAND,
      ANY, proj("p2"), rel("p2-built"), "deploy", "new-key", fingerprint("n"), 2, "s", "running",
      None),
    g("suspend by the API alone", "commands_insert_shape", (ROLE_API,), NEW_COMMAND,
      ANY, proj("p4"), None, "suspend_hosting", "event-1", fingerprint("n"), 1, None, "accepted",
      "billing"),
    g("resume by the API alone", "commands_insert_shape", (ROLE_API,), NEW_COMMAND,
      ANY, proj("p4"), None, "resume_hosting", "event-2", fingerprint("n"), 1, None, "accepted",
      "billing"),
    g("suspend by a payment-intake member", "commands_insert_shape",
      (ROLE_API, ROLE_PAYMENT_INTAKE), NEW_COMMAND,
      ANY, proj("p4"), None, "suspend_hosting", "event-1", fingerprint("n"), 1, None, "accepted",
      "billing"),
    # project_hosting
    g("counter skips a number", "project_hosting_generation_step", None,
      upd(HOST, "last_command_number = last_command_number + 2", "project_id"), proj("p2")),
    g("counter goes down", "project_hosting_generation_step", None,
      upd(HOST, "last_command_number = 0", "project_id"), proj("p2")),
    g("project id changes", "project_hosting_generation_step", None,
      upd(HOST, "project_id = %s", "project_id"), ANY, proj("p4")),
    g("row starts at 1", "project_hosting_insert_shape", None, NEW_HOSTING,
      GHOST, None, 1, "stable", "online"),
    g("row starts uncertain", "project_hosting_insert_shape", None, NEW_HOSTING,
      GHOST, None, 0, "uncertain", "online"),
    g("row starts offline", "project_hosting_insert_shape", None, NEW_HOSTING,
      GHOST, None, 0, "stable", "offline"),
    g("row starts with a live release", "project_hosting_insert_shape", None, NEW_HOSTING,
      GHOST, rel("p1-live"), 0, "stable", "online"),
    # release_steps
    g("candidate step by the owner", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p2-built"), "candidate", 1, "pending"),
    g("build step attempt 11", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p4-accepted"), "build", 11, "pending"),
    g("access_setup step", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p4-accepted"), "access_setup", 1, "pending"),
    g("step inserted running", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p4-accepted"), "build", 1, "running"),
    g("second attempt after a pending one", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p3-accepted"), "build", 2, "pending"),
    g("build step on a built release", "release_steps_insert_shape", None, NEW_STEP,
      ANY, rel("p2-built"), "build", 1, "pending"),
    g("worker updates a build step", "release_steps_update_shape", (ROLE_WORKER,),
      upd(STEP, "status = 'failed', started_at = now(), finished_at = now()", "release_step_id"),
      step_id("p3-pending")),
    g("step skips running", "release_steps_update_shape", None,
      upd(STEP, "status = 'succeeded'", "release_step_id"), step_id("p3-pending")),
    g("log reference rewritten", "release_steps_update_shape", None,
      upd(STEP, "log_ref = 'other'", "release_step_id"), step_id("p3-succeeded")),
    g("failure code rewritten", "release_steps_update_shape", (ROLE_WORKER,),
      upd(STEP, "failure_code = 'other'", "release_step_id"), step_id("p1-failed-candidate")),
    g("finished step reopened", "release_steps_update_shape", None,
      upd(STEP, "status = 'running'", "release_step_id"), step_id("p3-succeeded")),
    # command_dispatch
    g("dispatch row with a claim number", "command_dispatch_insert_shape", None, NEW_DISPATCH,
      cmd("p5-1"), 0, 0, 1),
    g("dispatch row with attempts", "command_dispatch_insert_shape", (ROLE_API,), NEW_DISPATCH,
      cmd("p5-1"), 0, 2, 0),
    g("dispatch row for a failed command", "command_dispatch_insert_shape", None, NEW_DISPATCH,
      cmd("p1-2"), 0, 0, 0),
    g("claim number goes down", "command_dispatch_monotonic", None,
      upd(DISP, "claim_number = 0", "command_id"), cmd("p1-6")),
    g("execution attempts go down", "command_dispatch_monotonic", None,
      upd(DISP, "execution_attempts = 0", "command_id"), cmd("p1-6")),
    g("publish attempts go down", "command_dispatch_monotonic", (ROLE_WORKER,),
      upd(DISP, "publish_attempts = 0", "command_id"), cmd("p1-6")),
    g("finish time rewritten", "command_dispatch_monotonic", (ROLE_WORKER,),
      upd(DISP, "finished_at = now()", "command_id"), cmd("p1-1")),
    # source_published_events
    g("event inserted accepted", "source_published_events_insert_shape", None, NEW_EVENT,
      proj("p4"), ANY, sha("e"), "accepted", 0, None, None),
    g("event inserted with an attempt", "source_published_events_insert_shape", (ROLE_INTAKE,),
      NEW_EVENT, proj("p4"), ANY, sha("e"), "pending", 1, None, None),
    g("event inserted with a manual retry", "source_published_events_insert_shape", None,
      NEW_EVENT, proj("p4"), ANY, sha("e"), "pending", 0, "operator-1", None),
    g("event commit sha", "source_published_events_update_shape", None,
      upd(EVT, "git_commit_sha = %s", KEY), sha("other"), req("p3-pending")),
    g("event repository", "source_published_events_update_shape", None,
      upd(EVT, "repository_id = 1", KEY), req("p3-pending")),
    g("rejected event reopened", "source_published_events_update_shape", None,
      upd(EVT, "status = 'pending', outcome_code = NULL, finished_at = NULL", KEY),
      req("p3-rejected")),
    g("mismatched repeats go down", "source_published_events_update_shape", None,
      upd(EVT, "mismatched_repeat_count = 0", KEY), req("p3-rejected")),
    g("attempts go down", "source_published_events_update_shape", None,
      upd(EVT, "attempts = 0", KEY), req("p3-pending")),
    g("budget raised without a retry", "source_published_events_update_shape", None,
      upd(EVT, "max_attempts = 9", KEY), req("p3-pending")),
    g("manual retry by a non-operator", "source_published_events_update_shape", None,
      upd(EVT, RETRY, KEY), req("p3-failed")),
    g("manual retry without raising the budget", "source_published_events_update_shape",
      (ROLE_OPERATOR,), upd(EVT, RETRY.replace("max_attempts = 4,", "max_attempts = 3,"), KEY),
      req("p3-failed")),
]
# fmt: on


@pytest.mark.parametrize("case", REFUSED, ids=lambda c: c.label)
def test_guard_still_refuses(world_db: Database, case: GuardCase) -> None:
    with session(world_db, case.who) as conn:
        with refused(conn, constraint=case.guard, table=case.table):
            conn.execute(q(case.sql_text), case.params)


def test_counter_moved_without_a_command_is_refused_at_commit(world_db: Database) -> None:
    with world_db.as_role(ROLE_MIGRATOR) as owner:
        guard = "project_hosting_generation_has_command"
        with refused(owner, constraint=guard, table="project_hosting"):
            owner.execute(
                upd(HOST, "last_command_number = last_command_number + 1", "project_id"),
                (proj("p4"),),
            )


def test_counter_moved_with_its_command_commits_and_hosting_intake_inserts_hosting_commands(
    world_db: Database,
) -> None:
    bump = upd(HOST, "last_command_number = 1, updated_at = now()", "project_id")
    # api + hosting_intake: a suspend_hosting command, as Payment's intake will insert it
    with world_db.login(ROLE_API, ROLE_HOSTING_INTAKE) as conn, allowed(conn):
        conn.execute(q(bump), (proj("p4"),))
        suspend = (ANY, proj("p4"), None, "suspend_hosting", "event-1", fingerprint("n"), 1)
        conn.execute(q(NEW_COMMAND), (*suspend, None, "accepted", "billing"))
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
    # api alone: an ordinary deploy command with its dispatch row
    with world_db.login(ROLE_API) as conn, allowed(conn):
        conn.execute(q(bump), (proj("p4"),))
        deploy = (ANY, proj("p4"), rel("p4-accepted"), "deploy", "k", fingerprint("n"), 1)
        conn.execute(q(NEW_COMMAND), (*deploy, "subject-1", "accepted", None))
        conn.execute(q(NEW_DISPATCH), (ANY, 0, 0, 0))
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_other_paths_the_guards_admit(world_db: Database) -> None:
    # the build role: accepted -> building, building -> built with a digest, a step started
    with world_db.login(ROLE_BUILD) as conn:
        with allowed(conn):
            conn.execute(
                q(upd(REL, "status = 'building', updated_at = now()", "release_id")),
                (rel("p3-accepted"),),
            )
        with allowed(conn):
            built = "status = 'built', container_image_digest = %s, updated_at = now()"
            conn.execute(
                q(upd(REL, built, "release_id")), (digest("p3-building"), rel("p3-building"))
            )
        with allowed(conn):
            start = "status = 'running', started_at = now(), deadline_at = now() + interval '5 min'"
            conn.execute(q(upd(STEP, start, "release_step_id")), (step_id("p3-pending"),))
    # the intake role: a build step, and a project's initial row followed by its release
    with world_db.login(ROLE_INTAKE) as conn, allowed(conn):
        conn.execute(q(NEW_STEP), (ANY, rel("p4-accepted"), "build", 1, "pending"))
        conn.execute(q(NEW_HOSTING), (GHOST, None, 0, "stable", "online"))
        first = (uid("release:ghost"), GHOST, req("ghost"), sha("ghost"), "accepted")
        conn.execute(q(NEW_RELEASE_INTAKE), first)
    # the worker role: a candidate step on a built release
    with world_db.login(ROLE_WORKER) as conn, allowed(conn):
        conn.execute(q(NEW_STEP), (ANY, rel("p2-built"), "candidate", 1, "pending"))
    # the operator role: a manual retry that raises the budget and records who and when
    with world_db.login(ROLE_OPERATOR) as conn, allowed(conn):
        retry = RETRY + ", next_attempt_at = now(), updated_at = now()"
        conn.execute(q(upd(EVT, retry, KEY)), (req("p3-failed"),))
    # the intake role: a new event, and an event decided (pending -> failed)
    with world_db.login(ROLE_INTAKE) as conn, allowed(conn):
        conn.execute(q(NEW_EVENT), (proj("p4"), ANY, sha("e"), "pending", 0, None, None))
        decided = (
            "status = 'failed', outcome_code = 'registry_unavailable', attempts = attempts + 1,"
            " finished_at = now(), updated_at = now()"
        )
        conn.execute(q(upd(EVT, decided, KEY)), (req("p3-pending"),))
