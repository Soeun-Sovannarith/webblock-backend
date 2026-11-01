"""Migrations 00014 and 00015: the owner-approved database changes of decision 0014 section 16.

00014 adds the ``sync_domains`` value to ``command_kind`` (it must commit before anything uses it);
00015 does the rest:

- the ``sync_domains`` command: no release, no requester, its own key index, inserted only by a
  member of ``provisioning_intake``, which receives the command-acceptance privileges;
- the guard gap: ``deploy``, ``promote`` and ``rollback`` only by a member of ``provisioning_api``;
- ``promotion`` and ``rollback`` steps, written only by a member of ``provisioning_worker``;
- the read-only role ``provisioning_bootstrap``.

Each guard is shown refusing and allowing, the privileges that changed are compared with the
privileges before (nothing else changed), and a database at 00013 with representative rows keeps
every row through the upgrade.

Migration 00017 revokes the intake's command-acceptance privileges again (decision 0014 section 23,
amendment of 18 October 2026; ``test_schema_00017.py``), so every test here in which a member of
``provisioning_intake`` accepts or is refused a command runs at 00016, the last version with them.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import cast

import psycopg
import pytest
from tests.integration._pg import (
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
    column,
    has_column,
    q,
    refused,
    rows,
    scalar,
    seed,
)
from tests.integration._rows import (
    TABLES,
    Row,
    fingerprint,
    insert,
    project_state,
    reason_of_key,
    release,
    table_name,
    uid,
    world,
)
from tests.integration.test_schema_00013 import ACCEPTANCE_PRIVILEGES, privileges

type DbFactory = Callable[[int | None], Database]

PROJECT = uid("project:dm")
BUILT = uid("release:dm-built")
CANDIDATE = uid("release:dm-candidate")

INSERT_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted', now(), now())"
)
# From 00018 a command also names its offline reason (decision 0021).
INSERT_COMMAND_00018 = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at,"
    " hosting_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted', now(), now(), %s)"
)
INSERT_DISPATCH = (
    "INSERT INTO provisioning_ops.command_dispatch (command_id, correlation_id, deadline_at,"
    " next_publish_at, publish_attempts, execution_attempts, claim_number, created_at, updated_at)"
    " VALUES (%s, 'corr-dm', now() + interval '1 hour', now(), 0, 0, 0, now(), now())"
)
LOCK = (
    "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s FOR UPDATE"
)
ADVANCE = (
    "UPDATE provisioning.project_hosting SET last_command_number = %s, updated_at = now()"
    " WHERE project_id = %s AND last_command_number = %s"
)
INSERT_STEP = (
    "INSERT INTO provisioning.release_steps (release_step_id, release_id, step_name, attempt,"
    " status) VALUES (%s, %s, %s, %s, 'pending')"
)
START_STEP = (
    "UPDATE provisioning.release_steps SET status = 'running', started_at = now(),"
    " deadline_at = now() + interval '1 minute' WHERE release_step_id = %s"
)
FAIL_STEP = (
    "UPDATE provisioning.release_steps SET status = 'failed', started_at = now(),"
    " finished_at = now() WHERE release_step_id = %s"
)


# The last version at which provisioning_intake holds the command-acceptance privileges of 00015;
# 00017 revokes them.
WITH_INTAKE_ACCEPTANCE = 16


def seeded(new_db: DbFactory, version: int | None) -> Database:
    """A database at ``version`` with one project, a built and a candidate release."""
    database = new_db(version)
    at_00018 = version is None or version >= 18
    seed(
        database,
        *[
            insert(table, row, new=True, at_00018=at_00018)
            for table, row in (
                ("provisioning.releases", release("dm-built", "dm", "built")),
                ("provisioning.releases", release("dm-candidate", "dm", "candidate_ready")),
                ("provisioning.project_release_state", project_state("dm")),
            )
        ],
    )
    return database


@pytest.fixture
def base_db(new_db: DbFactory) -> Database:
    """A database at the latest migration with one project, a built and a candidate release."""
    return seeded(new_db, None)


@pytest.fixture
def intake_db(new_db: DbFactory) -> Database:
    """As ``base_db``, at 00016: the intake still holds the 00015 acceptance privileges."""
    return seeded(new_db, WITH_INTAKE_ACCEPTANCE)


def accept(
    conn: Conn,
    kind: str,
    *,
    number: int = 1,
    key: str = "evt-1",
    command_id: object | None = None,
) -> None:
    """The acceptance writes in the order and under the lock the stores use."""
    no_release = kind in {"suspend_hosting", "resume_hosting", "sync_domains"}
    command = command_id or uid(f"command:dm-{kind}-{number}-{key}")
    conn.execute(q(LOCK), (PROJECT,))
    conn.execute(q(ADVANCE), (number, PROJECT, number - 1))
    values: tuple[object, ...] = (
        command,
        PROJECT,
        None if no_release else BUILT,
        kind,
        key,
        fingerprint(f"dm-{kind}-{number}-{key}"),
        number,
        None if no_release else "subject-dm",
    )
    if has_column(conn, "provisioning.commands", "hosting_reason"):
        conn.execute(q(INSERT_COMMAND_00018), (*values, reason_of_key(kind, key)))
    else:
        conn.execute(q(INSERT_COMMAND), values)
    conn.execute(q(INSERT_DISPATCH), (command,))


# --- who inserts which kind ----------------------------------------------------------------------


def test_intake_login_accepts_a_sync_domains_command(intake_db: Database) -> None:
    with intake_db.login(ROLE_INTAKE) as conn:
        with conn.transaction():
            accept(conn, "sync_domains")
        number = scalar(
            conn,
            "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s",
            (PROJECT,),
        )
        assert number == 1
        assert scalar(conn, "SELECT kind::text FROM provisioning.commands") == "sync_domains"
    with intake_db.admin() as admin:
        assert scalar(admin, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 1


@pytest.mark.parametrize("kind", ["deploy", "promote", "rollback"])
def test_intake_login_is_refused_the_client_kinds(intake_db: Database, kind: str) -> None:
    with intake_db.login(ROLE_INTAKE) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, kind)


@pytest.mark.parametrize("kind", ["suspend_hosting", "resume_hosting"])
def test_intake_login_is_refused_the_hosting_kinds(intake_db: Database, kind: str) -> None:
    with intake_db.login(ROLE_INTAKE) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, kind)


def test_api_login_is_refused_sync_domains(base_db: Database) -> None:
    with base_db.login(ROLE_API) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, "sync_domains")


@pytest.mark.parametrize("kind", ["deploy", "promote", "rollback"])
def test_api_login_still_accepts_the_client_kinds(base_db: Database, kind: str) -> None:
    with base_db.login(ROLE_API) as conn:
        with conn.transaction(force_rollback=True):
            accept(conn, kind)
            assert scalar(conn, "SELECT kind::text FROM provisioning.commands") == kind


@pytest.mark.parametrize("kind", ["deploy", "promote", "rollback", "sync_domains"])
def test_hosting_intake_login_is_refused_every_other_kind(base_db: Database, kind: str) -> None:
    with base_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="commands_insert_shape", table="commands"):
            accept(conn, kind)


@pytest.mark.parametrize("kind", ["suspend_hosting", "resume_hosting"])
def test_hosting_intake_login_still_accepts_the_hosting_kinds(base_db: Database, kind: str) -> None:
    with base_db.login(ROLE_HOSTING_INTAKE) as conn:
        with conn.transaction(force_rollback=True):
            accept(conn, kind)
            assert scalar(conn, "SELECT kind::text FROM provisioning.commands") == kind


def test_the_guard_gap_exists_at_00013_and_is_closed_at_latest(new_db: DbFactory) -> None:
    """The gap B3 found: at 00013 a hosting-intake login could insert a deploy."""
    for version, closed in ((13, False), (None, True)):
        database = new_db(version)
        at_00018 = version is None
        seed(
            database,
            insert(
                "provisioning.releases",
                release("dm-built", "dm", "built"),
                new=True,
                at_00018=at_00018,
            ),
            insert(
                "provisioning.project_release_state",
                project_state("dm"),
                new=True,
                at_00018=at_00018,
            ),
        )
        with database.login(ROLE_HOSTING_INTAKE) as conn:
            if closed:
                with refused(conn, constraint="commands_insert_shape", table="commands"):
                    accept(conn, "deploy")
            else:
                with conn.transaction(force_rollback=True):
                    accept(conn, "deploy")


def test_sync_domains_names_no_release_and_has_no_requester(base_db: Database) -> None:
    """The two check constraints treat it like the hosting kinds and still bind the other kinds.

    The inserts run in a superuser session with the guards skipped (decision 0008 section 5), so
    that the check constraints, not the insert guard, are what refuses.
    """
    cases = (
        ("sync_domains", BUILT, None, "commands_release_by_kind"),
        ("sync_domains", None, "subject-dm", "commands_requested_by_subject_by_kind"),
        ("deploy", None, "subject-dm", "commands_release_by_kind"),
        ("deploy", BUILT, None, "commands_requested_by_subject_by_kind"),
    )
    with base_db.admin() as conn:
        for kind, release_id, subject, constraint in cases:
            with pytest.raises(psycopg.errors.CheckViolation) as info:
                with conn.transaction():
                    conn.execute(q("SET LOCAL session_replication_role = replica"))
                    conn.execute(
                        q(INSERT_COMMAND),
                        (
                            uid("command:dm-shape"),
                            PROJECT,
                            release_id,
                            kind,
                            "key-9",
                            fingerprint("shape"),
                            1,
                            subject,
                        ),
                    )
            assert info.value.diag.constraint_name == constraint, (kind, str(info.value))
        # The allowed shapes are stored (one transaction each: one command in flight per project).
        for kind, release_id, subject in (
            ("sync_domains", None, None),
            ("deploy", BUILT, "subject-dm"),
        ):
            with conn.transaction(force_rollback=True):
                conn.execute(q("SET LOCAL session_replication_role = replica"))
                conn.execute(
                    q(INSERT_COMMAND),
                    (
                        uid(f"command:dm-ok-{kind}"),
                        PROJECT,
                        release_id,
                        kind,
                        f"ok-{kind}",
                        fingerprint(f"ok-{kind}"),
                        1,
                        subject,
                    ),
                )


# --- the domain event key ------------------------------------------------------------------------


def test_a_duplicate_domain_event_key_hits_the_index_and_another_key_does_not(
    new_db: DbFactory,
) -> None:
    database = new_db(WITH_INTAKE_ACCEPTANCE)
    first = uid("command:dm-first-domain")
    seed(
        database,
        insert(
            "provisioning.releases", release("dm-built", "dm", "built"), new=True, at_00018=False
        ),
        insert(
            "provisioning.project_release_state",
            project_state("dm", generation=1),
            new=True,
            at_00018=False,
        ),
        (
            "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind,"
            " idempotency_key, request_fingerprint, command_number, requested_by_subject, status,"
            " created_at, updated_at) VALUES (%s, %s, NULL, 'sync_domains', 'evt-1', %s, 1, NULL,"
            " 'succeeded', now(), now())",
            (first, PROJECT, fingerprint("first")),
        ),
    )
    with database.login(ROLE_INTAKE) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation) as info:
            with conn.transaction():
                accept(conn, "sync_domains", number=2, key="evt-1")
        assert info.value.diag.constraint_name == "commands_domain_event_key"
        # The original is the one command holding the key, so a store replays it.
        assert (
            scalar(
                conn,
                "SELECT command_id FROM provisioning.commands WHERE project_id = %s"
                " AND idempotency_key = 'evt-1' AND kind = 'sync_domains'",
                (PROJECT,),
            )
            == first
        )
        # A different key creates another command.
        with conn.transaction():
            accept(conn, "sync_domains", number=2, key="evt-2")
        assert (
            scalar(conn, "SELECT count(*) FROM provisioning.commands WHERE kind = 'sync_domains'")
            == 2
        )


def test_the_domain_key_class_is_separate_from_the_client_and_hosting_classes(
    new_db: DbFactory,
) -> None:
    """The same key as a sync_domains event, a suspend event and a deploy never collides."""
    database = new_db(None)
    seed(
        database,
        insert("provisioning.releases", release("dm-built", "dm", "built"), new=True),
        insert("provisioning.project_release_state", project_state("dm", generation=1), new=True),
        (
            "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind,"
            " idempotency_key, request_fingerprint, command_number, requested_by_subject, status,"
            " created_at, updated_at) VALUES (%s, %s, NULL, 'sync_domains', 'same', %s, 1, NULL,"
            " 'succeeded', now(), now())",
            (uid("command:dm-d"), PROJECT, fingerprint("d")),
        ),
    )
    with database.login(ROLE_HOSTING_INTAKE) as conn, conn.transaction():
        accept(conn, "suspend_hosting", number=2, key="same")
    with database.admin() as admin:
        admin.execute(
            q(
                "UPDATE provisioning.commands SET status = 'succeeded'"
                " WHERE kind = 'suspend_hosting'"
            )
        )
    with database.login(ROLE_API) as conn, conn.transaction():
        accept(conn, "deploy", number=3, key="same")


# --- promotion and rollback steps ----------------------------------------------------------------


@pytest.mark.parametrize("step_name", ["promotion", "rollback"])
def test_worker_writes_promotion_and_rollback_steps(base_db: Database, step_name: str) -> None:
    first = uid(f"step:dm-{step_name}-1")
    second = uid(f"step:dm-{step_name}-2")
    with base_db.login(ROLE_WORKER) as conn:
        conn.execute(q(INSERT_STEP), (first, CANDIDATE, step_name, 1))
        conn.execute(q(START_STEP), (first,))
        conn.execute(
            q("UPDATE provisioning.release_steps SET status = 'failed' WHERE release_step_id = %s"),
            (first,),
        )
        # Attempt 2 follows a failed attempt 1.
        conn.execute(q(INSERT_STEP), (second, CANDIDATE, step_name, 2))
        assert column(
            conn,
            "SELECT status::text FROM provisioning.release_steps WHERE step_name = %s"
            " ORDER BY attempt",
            (step_name,),
        ) == ["failed", "pending"]


@pytest.mark.parametrize("step_name", ["promotion", "rollback"])
@pytest.mark.parametrize("role", [ROLE_BUILD, ROLE_INTAKE])
def test_other_roles_do_not_write_promotion_and_rollback_steps(
    intake_db: Database, role: str, step_name: str
) -> None:
    with intake_db.login(role) as conn:
        # Both roles hold INSERT on release_steps (00008), so the privilege check passes and the
        # guard refuses: a promotion or rollback step is written only by a member of
        # provisioning_worker. Hence 23514, release_steps_insert_shape, not 42501.
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-x"), CANDIDATE, step_name, 1))


@pytest.mark.parametrize("step_name", ["promotion", "rollback"])
def test_the_step_guard_refuses_a_non_worker_update_of_a_promotion_step(
    base_db: Database, step_name: str
) -> None:
    step_id = uid(f"step:dm-{step_name}-own")
    seed(
        base_db,
        (
            "INSERT INTO provisioning.release_steps (release_step_id, release_id, step_name,"
            " attempt, status) VALUES (%s, %s, %s, 1, 'pending')",
            (step_id, CANDIDATE, step_name),
        ),
    )
    with base_db.login(ROLE_BUILD) as conn:
        with refused(conn, constraint="release_steps_update_shape", table="release_steps"):
            conn.execute(q(START_STEP), (step_id,))
    with base_db.login(ROLE_WORKER) as conn:
        conn.execute(q(START_STEP), (step_id,))


def test_the_step_guard_still_refuses_what_it_refused(base_db: Database) -> None:
    with base_db.login(ROLE_WORKER) as conn:
        # access_setup and observation steps are still not written.
        for step_name in ("access_setup", "observation"):
            with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
                conn.execute(q(INSERT_STEP), (uid("step:dm-no"), CANDIDATE, step_name, 1))
        # The worker still writes no build step.
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-no"), BUILT, "build", 1))
        # An attempt above 10, and an attempt 2 without a finished attempt 1.
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-no"), CANDIDATE, "promotion", 11))
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-no"), CANDIDATE, "rollback", 2))
        # The candidate step still needs a built release.
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-no"), CANDIDATE, "candidate", 1))
        conn.execute(q(INSERT_STEP), (uid("step:dm-ok"), BUILT, "candidate", 1))
    with base_db.login(ROLE_BUILD) as conn:
        # The build role still writes a build step on an accepted or building release only.
        with refused(conn, constraint="release_steps_insert_shape", table="release_steps"):
            conn.execute(q(INSERT_STEP), (uid("step:dm-no"), BUILT, "build", 1))


# --- the bootstrap role --------------------------------------------------------------------------


def test_bootstrap_reads_exactly_its_columns_and_writes_nothing(base_db: Database) -> None:
    with base_db.login(ROLE_BOOTSTRAP) as conn:
        assert column(
            conn, "SELECT status::text FROM provisioning.releases ORDER BY status::text"
        ) == ["built", "candidate_ready"]
        assert column(conn, "SELECT project_id FROM provisioning.releases") == [PROJECT, PROJECT]
        assert column(conn, "SELECT live_release_id FROM provisioning.project_hosting") == [None]
        for statement in (
            "SELECT * FROM provisioning.releases",
            "SELECT release_id FROM provisioning.releases",
            "SELECT container_image_digest FROM provisioning.releases",
            "SELECT * FROM provisioning.project_hosting",
            "SELECT last_command_number FROM provisioning.project_hosting",
            "SELECT routing_status FROM provisioning.project_hosting",
            "SELECT * FROM provisioning.commands",
            "SELECT * FROM provisioning.release_steps",
            "SELECT * FROM provisioning_ops.command_dispatch",
            "SELECT * FROM provisioning_ops.source_published_events",
            "UPDATE provisioning.releases SET status = 'failed'",
            "UPDATE provisioning.project_hosting SET live_release_id = NULL",
            "INSERT INTO provisioning.release_steps (release_step_id) VALUES (gen_random_uuid())",
            "DELETE FROM provisioning.releases",
            "TRUNCATE provisioning.releases",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(q(statement))


def test_bootstrap_holds_exactly_its_listed_privileges(base_db: Database) -> None:
    with base_db.admin() as conn:
        assert privileges(conn, ROLE_BOOTSTRAP) == {
            ("provisioning", "*", "USAGE"),
            ("releases", "project_id", "SELECT"),
            ("releases", "status", "SELECT"),
            ("project_hosting", "project_id", "SELECT"),
            ("project_hosting", "live_release_id", "SELECT"),
            ("project_hosting", "subdomain", "SELECT"),  # 00023 (decision 0023 G18a)
        }


# --- exactly the listed privileges changed -------------------------------------------------------


def test_exactly_the_listed_privileges_changed(new_db: DbFactory) -> None:
    database = new_db(13)
    roles = (
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
    with database.admin() as conn:
        before = {r: privileges(conn, r) for r in roles}
    database.migrate(15)
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in roles}
        api = privileges(conn, ROLE_API)
    assert before[ROLE_BOOTSTRAP] == set()
    assert after[ROLE_BOOTSTRAP] != set()
    # provisioning_intake gains the acceptance list and nothing else.
    assert after[ROLE_INTAKE] - before[ROLE_INTAKE] == ACCEPTANCE_PRIVILEGES - before[ROLE_INTAKE]
    assert before[ROLE_INTAKE] <= after[ROLE_INTAKE]
    assert ACCEPTANCE_PRIVILEGES <= after[ROLE_INTAKE]
    assert after[ROLE_INTAKE] - before[ROLE_INTAKE] <= api
    # Every other role keeps exactly what it had.
    for role in roles:
        if role not in {ROLE_INTAKE, ROLE_BOOTSTRAP}:
            assert after[role] == before[role], role


def test_the_intake_role_gains_only_privileges_the_api_role_holds(intake_db: Database) -> None:
    with intake_db.admin() as conn:
        assert privileges(conn, ROLE_INTAKE) >= ACCEPTANCE_PRIVILEGES


# --- the migration itself ------------------------------------------------------------------------


def test_the_migration_refuses_when_the_bootstrap_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(14)
    hidden = "prov_hidden_bootstrap"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_BOOTSTRAP} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate()
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_BOOTSTRAP}"))
    assert f"missing role(s): {ROLE_BOOTSTRAP}" in str(info.value)
    assert "bootstrap-roles.sql" in str(info.value)
    with database.admin() as conn:
        applied = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"
        assert scalar(conn, applied) == 14
        assert scalar(conn, "SELECT to_regclass('provisioning.commands_domain_event_key') IS NULL")
    database.migrate(15)  # 00025 drops the index again
    with database.admin() as conn:
        assert scalar(
            conn, "SELECT to_regclass('provisioning.commands_domain_event_key') IS NOT NULL"
        )


def test_the_migrations_run_only_as_the_migrator(new_db: DbFactory) -> None:
    database = new_db(13)
    harness = database.harness
    # goose connects as the superuser with ``-c role=provisioning_migrator``; without that setting
    # the current role is not the migrator, and 00014 stops before it changes anything.
    import subprocess

    from tests.integration._pg import GOOSE, MIGRATIONS_DIR

    env = {
        "PATH": "/usr/bin:/bin",
        "GOOSE_DRIVER": "postgres",
        "GOOSE_DBSTRING": harness.server.goose_url(database.name).split("&options=")[0],
        "GOOSE_MIGRATION_DIR": str(MIGRATIONS_DIR),
    }
    done = subprocess.run(  # noqa: S603 - fixed argv built from constants, no shell
        [str(GOOSE), "up"], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert done.returncode != 0
    assert "must run as role provisioning_migrator" in done.stdout + done.stderr
    with database.admin() as conn:
        assert scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied") == 13


def test_there_is_no_down_section() -> None:
    from tests.integration._pg import MIGRATIONS_DIR

    for name in ("00014_sync_domains_kind.sql", "00015_sync_domains_and_bootstrap_role.sql"):
        text = (MIGRATIONS_DIR / name).read_text()
        assert "-- +goose Up" in text
        assert "-- +goose Down" not in text, name


def test_the_enum_ends_with_sync_domains(new_db: DbFactory) -> None:
    # Pinned at 00015: 00019 adds redeploy before sync_domains (test_schema_00019).
    with new_db(15).admin() as conn:
        assert column(
            conn,
            "SELECT e.enumlabel::text FROM pg_enum AS e JOIN pg_type AS t ON t.oid = e.enumtypid"
            " WHERE t.typname = 'command_kind' ORDER BY e.enumsortorder",
        ) == ["deploy", "promote", "rollback", "suspend_hosting", "resume_hosting", "sync_domains"]


# --- the upgrade ---------------------------------------------------------------------------------


def snapshot(conn: Conn) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for table in TABLES:
        target = table_name(table, new=True)
        query = f"SELECT to_jsonb(t) FROM {target} AS t ORDER BY to_jsonb(t)::text"  # noqa: S608
        out[table] = [cast(Row, r[0]) for r in rows(conn, query)]
    return out


def test_upgrade_from_00013_keeps_every_row(new_db: DbFactory) -> None:
    database = new_db(13)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    with database.admin() as conn:
        before = snapshot(conn)
    database.migrate(15)
    with database.admin() as conn:
        after = snapshot(conn)
        version = scalar(conn, "SELECT max(version_id) FROM goose_db_version WHERE is_applied")
    assert version == 15
    assert set(before) == set(after) == set(TABLES)
    for table in TABLES:
        assert before[table], f"the representative rows hold no row of {table}"
        assert json.dumps(after[table], sort_keys=True) == json.dumps(
            before[table], sort_keys=True
        ), f"rows of {table} changed by the upgrade"


def test_an_upgraded_database_accepts_a_sync_domains_command(new_db: DbFactory) -> None:
    database = new_db(13)
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in world()])
    database.migrate(WITH_INTAKE_ACCEPTANCE)
    # p4 holds an accepted release and a stable, online state row with no command.
    project = uid("project:p4")
    with database.login(ROLE_INTAKE) as conn, conn.transaction():
        conn.execute(q(LOCK), (project,))
        number = cast(
            int,
            scalar(
                conn,
                "SELECT last_command_number FROM provisioning.project_hosting"
                " WHERE project_id = %s",
                (project,),
            ),
        )
        conn.execute(q(ADVANCE), (number + 1, project, number))
        conn.execute(
            q(INSERT_COMMAND),
            (
                uid("command:p4-domains"),
                project,
                None,
                "sync_domains",
                "evt-p4",
                fingerprint("p4-domains"),
                number + 1,
                None,
            ),
        )
