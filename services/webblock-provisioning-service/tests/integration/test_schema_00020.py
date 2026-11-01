"""Migration 00020: the admin reason, the redeploy guards and the nullable sub-domain (decision 0021
D1; Notion "5 · Provisioning", section "The next Provisioning migration").

- review focus 4: an admin key ``admin:<key>`` is ``admin_suspended``, only the API inserts that
  reason (with a requester), and only the hosting intake inserts the system reasons; an admin key
  never takes a system key such as ``project-deleted``;
- ``redeploy``: only from the API, with a requester, naming the project's live release while
  hosting is online and routing stable; it shares the client key class;
- ``project_hosting.subdomain``: a DNS label, required on every insert, NULL on a row upgraded
  from 00019, set once by the operator and never changed after;
- the key rule at 00020 matches the domain's ``hosting_reason_of_key``;
- exactly the listed grants are added; the migration runs only as the migrator.
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
    command,
    fingerprint,
    insert,
    project_state,
    release,
    table_name,
    uid,
    world,
)
from tests.integration.test_schema_00013 import privileges
from tests.integration.test_schema_00018 import EDGE_KEYS, domain_reason

from provisioning.core.domain import CommandKind

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00020_admin_redeploy_and_subdomain.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"

H = "provisioning.project_release_state"
C = "provisioning.commands"
R = "provisioning.releases"

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

INSERT_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at,"
    " hosting_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted', now(), now(), %s)"
)
INSERT_HOSTING = (
    "INSERT INTO provisioning.project_hosting (project_id, live_release_id, last_command_number,"
    " routing_status, hosting_status, updated_at, offline_reasons, owner_status_version, subdomain)"
    " VALUES (%s, NULL, 0, 'stable', %s, now(), %s, %s, %s)"
)
SET_SUBDOMAIN = (
    "UPDATE provisioning.project_hosting SET subdomain = %s, updated_at = now()"
    " WHERE project_id = %s"
)


def new_command(
    conn: Conn,
    project: object,
    kind: str,
    key: str,
    *,
    release_id: object = None,
    subject: str | None = None,
    reason: str | None = None,
    number: int = 1,
) -> None:
    conn.execute(
        q(INSERT_COMMAND),
        (
            uid(f"command:{project}-{kind}-{key}-{number}"),
            project,
            release_id,
            kind,
            key,
            fingerprint(f"{kind}-{key}"),
            number,
            subject,
            reason,
        ),
    )


# --- review focus 4: admin keys and the reasons ---------------------------------------------------

AD = uid("project:ad")


@pytest.fixture
def admin_db(db: Database) -> Database:
    seed(
        db,
        insert(R, release("ad-live", "ad", "live"), new=True),
        insert(H, project_state("ad", generation=1, active="ad-live"), new=True),
    )
    return db


def test_admin_key_cannot_take_a_system_key(admin_db: Database) -> None:
    with admin_db.login(ROLE_API) as conn, conn.transaction():
        new_command(
            conn,
            AD,
            "suspend_hosting",
            "admin:project-deleted",
            subject="admin-1",
            reason="admin_suspended",
        )
    # The worker finishes it and the next command takes number 2.
    seed(
        admin_db,
        (
            "UPDATE provisioning.commands SET status = 'succeeded' WHERE project_id = %s",
            [AD],
        ),
        (
            "UPDATE provisioning.project_hosting SET last_command_number = 2,"
            " hosting_status = 'offline', offline_reasons = '{admin_suspended}'"
            " WHERE project_id = %s",
            [AD],
        ),
    )
    with admin_db.login(ROLE_HOSTING_INTAKE) as conn, conn.transaction():
        new_command(
            conn, AD, "suspend_hosting", "project-deleted", reason="project_deleted", number=2
        )
    with admin_db.admin() as conn:
        assert rows(
            conn,
            "SELECT idempotency_key, hosting_reason::text, requested_by_subject, command_number"
            " FROM provisioning.commands WHERE project_id = %s ORDER BY command_number",
            (AD,),
        ) == [
            ("admin:project-deleted", "admin_suspended", "admin-1", 1),
            ("project-deleted", "project_deleted", None, 2),
        ]


def test_only_the_api_inserts_admin_reason(admin_db: Database) -> None:
    for kind in ("suspend_hosting", "resume_hosting"):
        with admin_db.login(ROLE_HOSTING_INTAKE) as conn:
            for subject in (None, "admin-1"):
                with refused(conn, constraint="commands_insert_shape", table="commands"):
                    new_command(
                        conn, AD, kind, "admin:k", subject=subject, reason="admin_suspended"
                    )
            with allowed(conn):
                new_command(conn, AD, kind, "evt-1", reason="billing")
        with admin_db.login(ROLE_API) as conn:
            system = [("evt-1", "billing"), ("owner-status:3", "user_suspended")]
            if kind == "suspend_hosting":
                system.append(("project-deleted", "project_deleted"))
            for key, reason in system:
                for subject in (None, "admin-1"):
                    with refused(conn, constraint="commands_insert_shape", table="commands"):
                        new_command(conn, AD, kind, key, subject=subject, reason=reason)
            # An admin hosting command needs its requester, not empty.
            for subject in (None, ""):
                with refused(
                    conn, constraint="commands_requested_by_subject_by_kind", table="commands"
                ):
                    new_command(
                        conn, AD, kind, "admin:k", subject=subject, reason="admin_suspended"
                    )
            with allowed(conn):
                new_command(conn, AD, kind, "admin:k", subject="admin-1", reason="admin_suspended")
    # A system hosting command still has no requester.
    with admin_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="commands_requested_by_subject_by_kind", table="commands"):
            new_command(conn, AD, "suspend_hosting", "evt-1", subject="admin-1", reason="billing")


ALL_REASONS = ("billing", "user_suspended", "project_deleted", "admin_suspended")
ADMIN_KEYS = (
    "admin:",
    "admin:k",
    "admin:k.retry-2",
    "admin:project-deleted",
    "admin:project-deleted.retry-1",
    "admin:owner-status:5",
    "admin:owner-status:",
    "Admin:k",
    "xadmin:k",
)


@pytest.mark.parametrize("kind", ["suspend_hosting", "resume_hosting"])
def test_hosting_reason_matches_the_domain_key_rule(admin_db: Database, kind: str) -> None:
    """For every edge key and every reason, the DB admits the command exactly when the domain
    gives that reason for that key; admin_suspended is written by the API with a requester, the
    other reasons by the hosting intake without one."""
    with admin_db.login(ROLE_HOSTING_INTAKE) as intake, admin_db.login(ROLE_API) as api:
        for key in (*EDGE_KEYS, *ADMIN_KEYS):
            expected = domain_reason(CommandKind(kind), key)
            for reason in ALL_REASONS:
                conn, subject = (api, "admin-1") if reason == "admin_suspended" else (intake, None)
                if reason == expected:
                    with allowed(conn):
                        new_command(conn, AD, kind, key, subject=subject, reason=reason)
                else:
                    with refused(
                        conn, constraint="commands_hosting_reason_by_key", table="commands"
                    ):
                        new_command(conn, AD, kind, key, subject=subject, reason=reason)


def test_the_admin_keys_follow_the_domain() -> None:
    suspend, resume = CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING
    assert domain_reason(suspend, "admin:project-deleted") == "admin_suspended"
    assert domain_reason(resume, "admin:project-deleted.retry-1") == "admin_suspended"
    assert domain_reason(suspend, "admin:") == "admin_suspended"
    assert domain_reason(suspend, "Admin:k") == "billing"
    assert domain_reason(suspend, "admin-1") == "billing"


# --- redeploy ------------------------------------------------------------------------------------

RD = uid("project:rd")
RD_LIVE = uid("release:rd-live")


@pytest.fixture
def redeploy_db(db: Database) -> Database:
    seed(
        db,
        insert(R, release("rd-live", "rd", "live"), new=True),
        insert(R, release("rd-built", "rd", "built"), new=True),
        insert(H, project_state("rd", generation=1, active="rd-live"), new=True),
        insert(R, release("rd-off-live", "rd-off", "live"), new=True),
        insert(
            H,
            project_state(
                "rd-off", generation=1, active="rd-off-live", hosting="offline", reasons="{billing}"
            ),
            new=True,
        ),
        insert(R, release("rd-unc-live", "rd-unc", "live"), new=True),
        insert(
            H,
            project_state("rd-unc", generation=1, active="rd-unc-live", route="uncertain"),
            new=True,
        ),
        insert(R, release("rd-chg-live", "rd-chg", "live"), new=True),
        insert(
            H,
            project_state("rd-chg", generation=1, active="rd-chg-live", route="changing"),
            new=True,
        ),
        insert(R, release("rd-new-built", "rd-new", "built"), new=True),
        insert(H, project_state("rd-new", generation=1), new=True),
    )
    return db


def test_redeploy_insert_rules(redeploy_db: Database) -> None:
    def redeploy(conn: Conn, project: str, target: str, subject: str | None = "admin-1") -> None:
        new_command(
            conn,
            uid(f"project:{project}"),
            "redeploy",
            "key-redeploy",
            release_id=uid(f"release:{target}"),
            subject=subject,
        )

    with redeploy_db.login(ROLE_API) as conn:
        with allowed(conn):
            redeploy(conn, "rd", "rd-live")
        for project, target in (
            ("rd", "rd-built"),  # not the live release
            ("rd-off", "rd-off-live"),  # offline
            ("rd-unc", "rd-unc-live"),  # uncertain route
            ("rd-chg", "rd-chg-live"),  # changing route
            ("rd-new", "rd-new-built"),  # no live release at all
        ):
            with refused(conn, constraint="commands_insert_shape", table="commands"):
                redeploy(conn, project, target)
        for subject in (None, ""):
            with refused(
                conn, constraint="commands_requested_by_subject_by_kind", table="commands"
            ):
                redeploy(conn, "rd", "rd-live", subject)
        # It names a release: without one it is not the live release (the guard runs first).
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            new_command(conn, RD, "redeploy", "key-redeploy", subject="admin-1")
    # The worker holds no insert privilege on commands; a member of another inserting role is
    # refused by the guard.
    with redeploy_db.login(ROLE_WORKER) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            redeploy(conn, "rd", "rd-live")
    with redeploy_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            redeploy(conn, "rd", "rd-live")


def test_redeploy_shares_the_client_key_class(redeploy_db: Database) -> None:
    seed(
        redeploy_db,
        insert(C, command("rd-1", "rd", "rd-built", 1, "failed", idempotency_key="k-1"), new=True),
        (
            "UPDATE provisioning.project_hosting SET last_command_number = 2 WHERE project_id = %s",
            [RD],
        ),
    )
    with redeploy_db.login(ROLE_API) as conn:
        with refused(conn, constraint="commands_idempotency_key", sqlstate="23505"):
            new_command(
                conn, RD, "redeploy", "k-1", release_id=RD_LIVE, subject="admin-1", number=2
            )
        with allowed(conn):
            new_command(
                conn, RD, "redeploy", "k-2", release_id=RD_LIVE, subject="admin-1", number=2
            )
    with redeploy_db.admin() as conn:
        assert column(
            conn,
            "SELECT pg_get_indexdef('provisioning.commands_idempotency_key'::regclass)",
        ) == [
            "CREATE UNIQUE INDEX commands_idempotency_key ON provisioning.commands USING btree"
            " (project_id, idempotency_key) WHERE (kind = ANY (ARRAY['deploy'::provisioning"
            ".command_kind, 'promote'::provisioning.command_kind, 'rollback'::provisioning"
            ".command_kind, 'redeploy'::provisioning.command_kind]))"
        ]


# --- the sub-domain -------------------------------------------------------------------------------


def test_subdomain_is_set_once(new_db: DbFactory) -> None:
    database = new_db(19)
    seed(database, insert(H, project_state("old"), new=True, at_00020=False))
    database.migrate(20)
    old, shop = uid("project:old"), uid("project:shop")
    with database.login(ROLE_INTAKE) as conn, conn.transaction():
        conn.execute(q(INSERT_HOSTING), (shop, "online", "{}", 0, "shop"))
    for role in (ROLE_WORKER, ROLE_INTAKE, ROLE_HOSTING_INTAKE, ROLE_API, ROLE_BUILD):
        with database.login(role) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(q(SET_SUBDOMAIN), ("other", shop))
    with database.login(ROLE_OPERATOR) as conn:
        for project, value in ((shop, "other"), (shop, None)):
            with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
                conn.execute(q(SET_SUBDOMAIN), (value, project))
        with refused(conn, constraint="project_hosting_subdomain_check", table="project_hosting"):
            conn.execute(q(SET_SUBDOMAIN), ("Not-A-Label", old))
        with conn.transaction():
            conn.execute(q(SET_SUBDOMAIN), ("old-site", old))
        with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
            conn.execute(q(SET_SUBDOMAIN), ("again", old))
        # The same value is no change.
        with allowed(conn):
            conn.execute(q(SET_SUBDOMAIN), ("old-site", old))
    # The owner cannot change a set sub-domain either.
    with database.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
            conn.execute(q(SET_SUBDOMAIN), ("owner-change", shop))
    with database.admin() as conn:
        assert dict(
            rows(conn, "SELECT project_id, subdomain FROM provisioning.project_hosting")
        ) == {old: "old-site", shop: "shop"}


def test_every_hosting_insert_names_a_subdomain(db: Database) -> None:
    def new_row(conn: Conn, name: str, value: str | None, *, version: int = 0) -> None:
        status, reasons = ("offline", "{user_suspended}") if version else ("online", "{}")
        conn.execute(
            q(INSERT_HOSTING), (uid(f"project:sd-{name}"), status, reasons, version, value)
        )

    with db.login(ROLE_INTAKE) as conn:
        with refused(conn, constraint="project_hosting_insert_shape", table="project_hosting"):
            new_row(conn, "none", None)
        for value in ("", "-a", "a-", "A", "a.b", "a_b"):
            with refused(
                conn, constraint="project_hosting_subdomain_check", table="project_hosting"
            ):
                new_row(conn, f"bad-{value}", value)
        with pytest.raises(psycopg.errors.StringDataRightTruncation), conn.transaction():
            new_row(conn, "long", "a" * 64)
        for value in ("a", "shop-1", "a" * 63):
            with allowed(conn):
                new_row(conn, f"ok-{value}", value)
    with db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="project_hosting_insert_shape", table="project_hosting"):
            new_row(conn, "os-none", None, version=2)
        with allowed(conn):
            new_row(conn, "os-ok", "shop", version=2)


# --- the upgrade ----------------------------------------------------------------------------------


def snapshot(conn: Conn) -> dict[str, list[Row]]:
    """Every row of every table, without the 00020 column."""
    out: dict[str, list[Row]] = {}
    for table in TABLES:
        target = table_name(table, new=True)
        query = f"SELECT to_jsonb(t) - 'subdomain' FROM {target} AS t"  # noqa: S608
        found = [cast(Row, r[0]) for r in rows(conn, query)]
        out[table] = sorted(found, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def test_upgrade_from_00019_keeps_rows(new_db: DbFactory) -> None:
    database = new_db(19)
    seed(database, *[insert(table, row, new=True, at_00020=False) for table, row in world()])
    with database.admin() as conn:
        before = snapshot(conn)
    database.migrate(20)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 20
        after = snapshot(conn)
        for table, found in before.items():
            assert found, f"the representative rows hold no row of {table}"
            assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
                f"rows of {table} changed by 00020"
            )
        assert column(conn, "SELECT DISTINCT subdomain FROM provisioning.project_hosting") == [None]
        # Every check, the new and the replaced ones, holds on the kept rows.
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM pg_constraint WHERE NOT convalidated"
                " AND connamespace IN ('provisioning'::regnamespace,"
                " 'provisioning_ops'::regnamespace)",
            )
            == 0
        )


def test_00020_refuses_an_old_hosting_command_with_an_admin_key(new_db: DbFactory) -> None:
    """Before 00020 a Payment event key such as ``admin:k`` is billing; 00020 makes it
    admin_suspended with a requester. Such a row must stop the migration with a message that names
    the cause, not a bare check violation, and change nothing."""
    database = new_db(19)
    seed(
        database,
        insert(R, release("ak-live", "ak", "live"), new=True),
        insert(H, project_state("ak", generation=1, active="ak-live"), new=True, at_00020=False),
        insert(
            C,
            command(
                "ak-1", "ak", None, 1, "succeeded", "suspend_hosting", idempotency_key="admin:k"
            ),
            new=True,
        ),
    )
    with pytest.raises(RuntimeError) as info:
        database.migrate(20)
    text = str(info.value)
    assert "1 hosting command(s) have a key that starts with admin:" in text
    assert "23514" in text
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 19
        assert rows(
            conn, "SELECT idempotency_key, hosting_reason::text FROM provisioning.commands"
        ) == [("admin:k", "billing")]


def test_00020_refuses_an_old_redeploy_command(new_db: DbFactory) -> None:
    """No redeploy can exist before 00020 (the API and the worker refuse it, D10); one that does
    was never checked against the live release, so 00020 stops with a message that names it."""
    database = new_db(19)
    seed(
        database,
        insert(R, release("rk-live", "rk", "live"), new=True),
        insert(H, project_state("rk", generation=1, active="rk-live"), new=True, at_00020=False),
        insert(C, command("rk-1", "rk", "rk-live", 1, "accepted", "redeploy"), new=True),
    )
    with pytest.raises(RuntimeError) as info:
        database.migrate(20)
    text = str(info.value)
    assert "1 redeploy command(s) exist from before migration 00020" in text
    assert "23514" in text
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 19
        assert rows(
            conn, "SELECT kind::text, idempotency_key, status::text FROM provisioning.commands"
        ) == [("redeploy", "key-rk-1", "accepted")]


# --- grants and the migration itself --------------------------------------------------------------

NEW_COLUMNS = {"project_hosting": ("subdomain",)}


def expected_after(
    before: set[tuple[str, str, str]], table_columns: dict[str, set[str]]
) -> set[tuple[str, str, str]]:
    """``before`` plus the new column wherever the role held the privilege on every column of the
    table (a table-level grant covers a new column)."""
    out = set(before)
    for table, columns in NEW_COLUMNS.items():
        for priv in {p for t, _, p in before if t == table}:
            held = {c for t, c, p in before if t == table and p == priv}
            if held == table_columns[table]:
                out |= {(table, c, priv) for c in columns}
    return out


def test_exactly_the_listed_grants_are_added(new_db: DbFactory) -> None:
    database = new_db(19)
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in ROLES}
        table_columns: dict[str, set[str]] = {}
        for table, name in rows(
            conn,
            "SELECT c.relname::text, a.attname::text FROM pg_class AS c"
            " JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped"
            " WHERE c.relkind = 'r' AND c.relnamespace IN"
            " ('provisioning'::regnamespace, 'provisioning_ops'::regnamespace)",
        ):
            table_columns.setdefault(table, set()).add(name)
    database.migrate(20)
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in ROLES}
    added = {
        ROLE_OPERATOR: {
            ("provisioning", "*", "USAGE"),
            ("project_hosting", "project_id", "SELECT"),
            ("project_hosting", "subdomain", "SELECT"),
            ("project_hosting", "subdomain", "UPDATE"),
            ("project_hosting", "updated_at", "UPDATE"),
        },
    }
    for role in ROLES:
        assert after[role] == expected_after(before[role], table_columns) | added.get(
            role, set()
        ), role
    assert not {
        p
        for role in ROLES
        for p in after[role]
        if p[2] in {"DELETE", "TRUNCATE"} and role != ROLE_MIGRATOR
    }


def test_the_operator_executes_the_check_function(new_db: DbFactory) -> None:
    """At 00020 the operator updates project_hosting, so its checks run offline_reasons_distinct
    with the operator's rights (the Task 3 carry-over). 00021 revokes it with the backfill."""
    function = "provisioning.offline_reasons_distinct(provisioning.offline_reason[])"
    db = new_db(20)
    with db.admin() as conn:
        assert scalar(
            conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (ROLE_OPERATOR, function)
        )


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "DELETE", "TRUNCATE"):
        assert re.search(rf"\b{word}\b", code) is None, word


def test_replaced_functions_follow_the_hygiene(db: Database) -> None:
    with db.admin() as conn:
        found = rows(
            conn,
            "SELECT p.proname::text, p.prosecdef, p.proconfig,"
            " has_function_privilege('public', p.oid, 'EXECUTE')"
            " FROM pg_proc AS p WHERE p.pronamespace = 'provisioning'::regnamespace"
            " AND p.proname IN ('commands_insert_shape', 'project_hosting_insert_shape',"
            " 'project_hosting_reasons_guard') ORDER BY 1",
        )
    assert found == [
        (name, False, ["search_path=pg_catalog"], False)
        for name in (
            "commands_insert_shape",
            "project_hosting_insert_shape",
            "project_hosting_reasons_guard",
        )
    ]


def test_00020_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(19)
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
        assert scalar(conn, APPLIED) == 19
        assert (
            column(
                conn,
                "SELECT column_name::text FROM information_schema.columns"
                " WHERE table_schema = 'provisioning' AND table_name = 'project_hosting'"
                " AND column_name = 'subdomain'",
            )
            == []
        )


def test_00020_refuses_when_the_operator_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(19)
    hidden = "prov_hidden_operator"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_OPERATOR} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(20)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_OPERATOR}"))
    assert f"missing role(s): {ROLE_OPERATOR}" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 19
