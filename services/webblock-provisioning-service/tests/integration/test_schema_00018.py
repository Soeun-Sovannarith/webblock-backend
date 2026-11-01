"""Migration 00018: offline reasons, the owner status version and ``repository_id`` (decision 0021;
Notion "5 · Provisioning", "Planned, not yet applied: Migration 00018").

- the upgrade from 00017 keeps every row, backfills the reasons by decision 0021 D4 and each hosting
  command's reason by the key rule, and the guard bodies that name the renamed column still work;
- ``project_hosting``: offline exactly when a reason is held, no NULL or duplicate reason,
  ``project_deleted`` is never removed, ``owner_status_version`` never decreases, and the insert
  shape of an owner-status row (hosting intake only);
- ``commands.hosting_reason``: required exactly for the hosting kinds, fixed by the key exactly as
  the domain's ``hosting_reason_of_key`` fixes it, never changed;
- the retired ``provisioning_payment_intake`` holds nothing, and the administrator script drops it;
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
    REPO_ROOT,
    ROLE_API,
    ROLE_BOOTSTRAP,
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
    fingerprint,
    inbox,
    insert,
    project_state,
    reason_of_key,
    release,
    table_name,
    uid,
    world,
)
from tests.integration.test_schema_00013 import privileges

from provisioning.core.domain import (
    CommandKind,
    DomainError,
    hosting_reason_of_key,
    validate_idempotency_key,
)

type DbFactory = Callable[[int | None], Database]

MIGRATION = "00018_offline_reasons_and_repository_id.sql"
DROP_SCRIPT = REPO_ROOT / "scripts" / "db" / "drop-retired-roles.sql"
APPLIED = "SELECT max(version_id) FROM goose_db_version WHERE is_applied"

H = "provisioning.project_release_state"
C = "provisioning.commands"
R = "provisioning.releases"
IB = "provisioning_ops.source_published_inbox"

INSERT_COMMAND = (
    "INSERT INTO provisioning.commands (command_id, project_id, release_id, kind, idempotency_key,"
    " request_fingerprint, command_number, requested_by_subject, status, created_at, updated_at,"
    " hosting_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'accepted', now(), now(), %s)"
)
INSERT_HOSTING = (
    "INSERT INTO provisioning.project_hosting (project_id, live_release_id, last_command_number,"
    " routing_status, hosting_status, updated_at, offline_reasons, owner_status_version)"
    " VALUES (%s, NULL, 0, 'stable', %s, now(), %s, %s)"
)
SET_STATUS = (
    "UPDATE provisioning.project_hosting SET hosting_status = %s, offline_reasons = %s,"
    " updated_at = now() WHERE project_id = %s"
)
SET_REASONS = (
    "UPDATE provisioning.project_hosting SET offline_reasons = %s, updated_at = now()"
    " WHERE project_id = %s"
)
SET_VERSION = (
    "UPDATE provisioning.project_hosting SET owner_status_version = %s WHERE project_id = %s"
)

ROLES = (
    ROLE_API,
    ROLE_WORKER,
    ROLE_INTAKE,
    ROLE_BUILD,
    ROLE_OPERATOR,
    ROLE_MIGRATOR,
    ROLE_HOSTING_INTAKE,
    ROLE_BOOTSTRAP,
    ROLE_PAYMENT_INTAKE,
    "public",
)


def hosting_command(
    name: str, project: str, number: int, status: str, kind: str, key: str, **over: object
) -> Row:
    return command(name, project, None, number, status, kind=kind, idempotency_key=key, **over)


def scenario() -> list[tuple[str, Row]]:
    """The projects of review focus 1, written with the logical names (decision 0021 D4):

    - ``a``: online with a live release, a billing suspend and its resume (both succeeded);
    - ``b``: offline after a billing suspend keyed ``evt-b`` that succeeded at ``.retry-2``;
    - ``c``: offline after a succeeded ``project-deleted`` suspend that followed a succeeded
      billing suspend;
    - ``d``: online, with a failed ``project-deleted`` suspend and its failed ``.retry-1``;
    - ``e``: offline after a billing suspend, then a failed ``project-deleted`` suspend;
    - ``f``: offline after a billing suspend, then ``project-deleted`` skipped at ``.retry-2``;
    - an inbox row for ``a``.
    """
    return [
        (R, release("a-live", "a", "live")),
        (H, project_state("a", generation=2, active="a-live")),
        (C, hosting_command("a-1", "a", 1, "succeeded", "suspend_hosting", "evt-a")),
        (C, hosting_command("a-2", "a", 2, "succeeded", "resume_hosting", "evt-a-restored")),
        (
            IB,
            inbox(
                "a-event",
                "a",
                "accepted",
                attempts=1,
                release_id=uid("release:a-live"),
                finished_at=at(8),
            ),
        ),
        (H, project_state("b", generation=3, hosting="offline", reasons="{billing}")),
        (C, hosting_command("b-1", "b", 1, "failed", "suspend_hosting", "evt-b")),
        (C, hosting_command("b-2", "b", 2, "failed", "suspend_hosting", "evt-b.retry-1")),
        (C, hosting_command("b-3", "b", 3, "succeeded", "suspend_hosting", "evt-b.retry-2")),
        (H, project_state("c", generation=2, hosting="offline", reasons="{project_deleted}")),
        (C, hosting_command("c-1", "c", 1, "succeeded", "suspend_hosting", "evt-c")),
        (C, hosting_command("c-2", "c", 2, "succeeded", "suspend_hosting", "project-deleted")),
        (H, project_state("d", generation=2)),
        (C, hosting_command("d-1", "d", 1, "failed", "suspend_hosting", "project-deleted")),
        (
            C,
            hosting_command("d-2", "d", 2, "failed", "suspend_hosting", "project-deleted.retry-1"),
        ),
        (H, project_state("e", generation=2, hosting="offline", reasons="{billing}")),
        (C, hosting_command("e-1", "e", 1, "succeeded", "suspend_hosting", "evt-e")),
        (C, hosting_command("e-2", "e", 2, "failed", "suspend_hosting", "project-deleted")),
        (H, project_state("f", generation=3, hosting="offline", reasons="{project_deleted}")),
        (C, hosting_command("f-1", "f", 1, "succeeded", "suspend_hosting", "evt-f")),
        (C, hosting_command("f-2", "f", 2, "failed", "suspend_hosting", "project-deleted")),
        (
            C,
            hosting_command(
                "f-3",
                "f",
                3,
                "skipped",
                "suspend_hosting",
                "project-deleted.retry-2",
                failure_code="hosting_already_offline",
            ),
        ),
    ]


def snapshot(conn: Conn) -> dict[str, list[Row]]:
    """Every row of every table, without the 00018 columns and under the names of 00017."""
    out: dict[str, list[Row]] = {}
    for table in TABLES:
        target = table_name(table, new=True)
        found = [
            cast(Row, r[0])
            for r in rows(conn, f"SELECT to_jsonb(t) FROM {target} AS t")  # noqa: S608
        ]
        normal: list[Row] = []
        for row in found:
            row = dict(row)
            for new_only in ("offline_reasons", "owner_status_version", "hosting_reason"):
                row.pop(new_only, None)
            if "repository_id" in row and "github_repository_id" not in row:
                row["github_repository_id"] = row.pop("repository_id")
            normal.append(row)
        out[table] = sorted(normal, key=lambda r: json.dumps(r, sort_keys=True))
    return out


def reasons_of(conn: Conn) -> dict[str, list[str]]:
    found = rows(
        conn,
        "SELECT project_id, offline_reasons::text[] FROM provisioning.project_hosting",
    )
    return {str(r[0]): list(r[1]) for r in found}


# --- review focus 1: the upgrade ------------------------------------------------------------------


def test_upgrade_from_00017_keeps_rows_and_backfills_reasons(new_db: DbFactory) -> None:
    database = new_db(17)
    seeded = [*world(), *scenario()]
    seed(database, *[insert(table, row, new=True, at_00018=False) for table, row in seeded])
    with database.admin() as conn:
        before = snapshot(conn)
        repository_ids: dict[object, object] = dict(
            rows(conn, "SELECT release_id, github_repository_id FROM provisioning.releases")
        )
        inbox_ids: dict[object, object] = dict(
            rows(
                conn,
                "SELECT generation_request_id, github_repository_id"
                " FROM provisioning_ops.source_published_events",
            )
        )
    database.migrate(18)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 18
        after = snapshot(conn)
        assert set(before) == set(after)
        for table, found in before.items():
            assert found, f"the representative rows hold no row of {table}"
            assert json.dumps(after[table], sort_keys=True) == json.dumps(found, sort_keys=True), (
                f"rows of {table} changed by the upgrade"
            )
        # D4: online {}; offline with a succeeded or skipped project-deleted suspend
        # {project_deleted}; every other offline row {billing}.
        assert reasons_of(conn) == {
            str(uid("project:a")): [],
            str(uid("project:b")): ["billing"],
            str(uid("project:c")): ["project_deleted"],
            str(uid("project:d")): [],
            str(uid("project:e")): ["billing"],
            str(uid("project:f")): ["project_deleted"],
            str(uid("project:p1")): [],
            str(uid("project:p2")): ["billing"],
            str(uid("project:p3")): [],
            str(uid("project:p4")): [],
            str(uid("project:p5")): [],
        }
        assert column(
            conn, "SELECT DISTINCT owner_status_version FROM provisioning.project_hosting"
        ) == [0]
        stored = rows(
            conn,
            "SELECT kind::text, idempotency_key, hosting_reason::text FROM provisioning.commands",
        )
        assert len(stored) == len([t for t, _ in seeded if t == C])
        for kind, key, reason in stored:
            assert reason == reason_of_key(kind, key), (kind, key)
        by_key = {key: reason for kind, key, reason in stored if kind != "deploy"}
        assert by_key["evt-b.retry-2"] == "billing"
        assert by_key["project-deleted.retry-1"] == "project_deleted"
        assert by_key["project-deleted.retry-2"] == "project_deleted"
        assert by_key["evt-a-restored"] == "billing"
        # The rename keeps every value.
        assert dict(rows(conn, "SELECT release_id, repository_id FROM provisioning.releases")) == (
            repository_ids
        )
        assert (
            dict(
                rows(
                    conn,
                    "SELECT generation_request_id, repository_id"
                    " FROM provisioning_ops.source_published_events",
                )
            )
            == inbox_ids
        )
        assert (
            column(
                conn,
                "SELECT table_name::text FROM information_schema.columns"
                " WHERE column_name = 'github_repository_id'"
                " AND table_schema IN ('provisioning', 'provisioning_ops')",
            )
            == []
        )
    # The guard bodies that name the renamed column run on the upgraded rows.
    check_renamed_column_guards(database, uid("release:a-live"), uid("request:p3-failed"))


def check_renamed_column_guards(database: Database, release_id: object, request_id: object) -> None:
    with database.login(ROLE_WORKER) as conn, allowed(conn):
        conn.execute(
            q("UPDATE provisioning.releases SET updated_at = now() WHERE release_id = %s"),
            (release_id,),
        )
    with database.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="releases_identity_immutable", table="releases"):
            conn.execute(
                q(
                    "UPDATE provisioning.releases SET repository_id = repository_id + 1"
                    " WHERE release_id = %s"
                ),
                (release_id,),
            )
        with refused(
            conn, constraint="source_published_events_update_shape", table="source_published_events"
        ):
            conn.execute(
                q(
                    "UPDATE provisioning_ops.source_published_events"
                    " SET repository_id = repository_id + 1 WHERE generation_request_id = %s"
                ),
                (request_id,),
            )
    with database.login(ROLE_OPERATOR) as conn, allowed(conn):
        retried = conn.execute(
            q(
                "UPDATE provisioning_ops.source_published_events SET status = 'pending',"
                " outcome_code = NULL, finished_at = NULL, max_attempts = max_attempts + 1,"
                " next_attempt_at = now(), last_manual_retry_by = 'operator-2',"
                " last_manual_retry_at = now(), updated_at = now()"
                " WHERE generation_request_id = %s AND status = 'failed'"
            ),
            (request_id,),
        )
        assert retried.rowcount == 1


def test_renamed_column_guards_still_work(db: Database) -> None:
    seed(
        db,
        insert(R, release("rn-live", "rn", "live"), new=True),
        insert(H, project_state("rn", active="rn-live"), new=True),
        insert(
            IB,
            inbox(
                "rn-failed",
                "rn",
                "failed",
                attempts=3,
                outcome_code="registry_unavailable",
                finished_at=at(8),
            ),
            new=True,
        ),
    )
    check_renamed_column_guards(db, uid("release:rn-live"), uid("request:rn-failed"))


# --- project_hosting: the reasons -----------------------------------------------------------------

PH = uid("project:ph")


@pytest.fixture
def hosting_db(db: Database) -> Database:
    seed(
        db,
        insert(R, release("ph-live", "ph", "live"), new=True),
        insert(H, project_state("ph", generation=1, active="ph-live"), new=True),
        insert(H, project_state("pd", hosting="offline", reasons="{project_deleted}"), new=True),
        insert(H, project_state("ov", owner_status_version=5), new=True),
    )
    return db


def test_offline_exactly_when_reasons_non_empty(hosting_db: Database) -> None:
    with hosting_db.login(ROLE_WORKER) as conn:
        for status, reasons in (("offline", "{}"), ("online", "{billing}")):
            with refused(
                conn,
                constraint="project_hosting_offline_reasons_match_status",
                table="project_hosting",
            ):
                conn.execute(q(SET_STATUS), (status, reasons, PH))
        for status, reasons in (
            ("offline", "{billing}"),
            ("offline", "{billing,user_suspended}"),
            ("online", "{}"),
        ):
            with allowed(conn):
                conn.execute(q(SET_STATUS), (status, reasons, PH))


def test_reasons_have_no_duplicate_or_null(hosting_db: Database) -> None:
    with hosting_db.login(ROLE_WORKER) as conn:
        for reasons in ("{billing,billing}", "{{billing},{user_suspended}}"):
            with refused(
                conn, constraint="project_hosting_offline_reasons_shape", table="project_hosting"
            ):
                conn.execute(q(SET_STATUS), ("offline", reasons, PH))
        with refused(
            conn, constraint="project_hosting_offline_reasons_shape", table="project_hosting"
        ):
            conn.execute(
                q(
                    "UPDATE provisioning.project_hosting SET hosting_status = 'offline',"
                    " offline_reasons = ARRAY[NULL]::provisioning.offline_reason[]"
                    " WHERE project_id = %s"
                ),
                (PH,),
            )
        with pytest.raises(psycopg.errors.NotNullViolation), conn.transaction():
            conn.execute(q(SET_STATUS), ("online", None, PH))


def test_project_deleted_is_never_removed(hosting_db: Database) -> None:
    deleted = uid("project:pd")
    with hosting_db.login(ROLE_WORKER) as conn:
        for status, reasons in (("offline", "{billing}"), ("online", "{}")):
            with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
                conn.execute(q(SET_STATUS), (status, reasons, deleted))
        with allowed(conn):
            conn.execute(q(SET_REASONS), ("{billing,project_deleted}", deleted))
    # The owner too.
    with hosting_db.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
            conn.execute(q(SET_REASONS), ("{user_suspended}", deleted))


def test_owner_status_version_never_decreases(hosting_db: Database) -> None:
    project = uid("project:ov")
    with hosting_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="project_hosting_reasons_guard", table="project_hosting"):
            conn.execute(q(SET_VERSION), (4, project))
        for version in (5, 6, 9007199254740991):
            with allowed(conn):
                conn.execute(q(SET_VERSION), (version, project))
        with refused(
            conn, constraint="project_hosting_owner_status_version_check", table="project_hosting"
        ):
            conn.execute(q(SET_VERSION), (9007199254740992, project))
    # Only the hosting intake moves the version; the worker cannot.
    with hosting_db.login(ROLE_WORKER) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(q(SET_VERSION), (6, project))


# --- project_hosting: the insert shape ------------------------------------------------------------


def test_hosting_intake_inserts_owner_status_rows(new_db: DbFactory) -> None:
    db = new_db(19)  # 00020 also requires the sub-domain (test_schema_00020)

    def new_row(conn: Conn, status: str, reasons: str, version: int) -> None:
        conn.execute(
            q(INSERT_HOSTING), (uid(f"project:os-{status}-{version}"), status, reasons, version)
        )

    with db.login(ROLE_HOSTING_INTAKE) as conn:
        for status, reasons, version in (
            ("offline", "{user_suspended}", 3),
            ("online", "{}", 2),
        ):
            with allowed(conn):
                new_row(conn, status, reasons, version)
        for status, reasons, version in (
            ("offline", "{billing}", 3),
            ("offline", "{user_suspended,billing}", 3),
            ("offline", "{user_suspended}", 0),
            ("offline", "{project_deleted}", 1),
            # Notion: the hosting intake inserts owner-status rows only, so never version 0.
            ("online", "{}", 0),
        ):
            with refused(conn, constraint="project_hosting_insert_shape", table="project_hosting"):
                new_row(conn, status, reasons, version)
    with db.login(ROLE_INTAKE) as conn:
        with allowed(conn):
            new_row(conn, "online", "{}", 0)
        for status, reasons, version in (("offline", "{user_suspended}", 1), ("online", "{}", 1)):
            with refused(conn, constraint="project_hosting_insert_shape", table="project_hosting"):
                new_row(conn, status, reasons, version)
    # The worker and the API insert no hosting row at all.
    for role in (ROLE_WORKER, ROLE_API):
        with db.login(role) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                new_row(conn, "online", "{}", 0)


# --- commands.hosting_reason ----------------------------------------------------------------------

HR = uid("project:hr")
HR_BUILT = uid("release:hr-built")


@pytest.fixture
def commands_db(new_db: DbFactory) -> Database:
    """A database at 00019: the key rule as 00018 wrote it (00020 adds the admin keys)."""
    db = new_db(19)
    seed(
        db,
        insert(R, release("hr-built", "hr", "built"), new=True),
        insert(H, project_state("hr", generation=1), new=True, at_00020=False),
    )
    return db


def insert_hosting_command(conn: Conn, kind: str, key: str, reason: str | None) -> None:
    conn.execute(
        q(INSERT_COMMAND),
        (uid(f"command:hr-{kind}-{key}"), HR, None, kind, key, fingerprint(key), 1, None, reason),
    )


def test_hosting_reason_follows_the_key(commands_db: Database) -> None:
    with commands_db.login(ROLE_HOSTING_INTAKE) as conn:
        with refused(conn, constraint="commands_hosting_reason_by_key", table="commands"):
            insert_hosting_command(conn, "suspend_hosting", "project-deleted", "billing")
        with allowed(conn):
            insert_hosting_command(conn, "suspend_hosting", "project-deleted", "project_deleted")
        with allowed(conn):
            insert_hosting_command(conn, "suspend_hosting", "owner-status:4", "user_suspended")
        for reason in ("project_deleted", "billing", "user_suspended"):
            with refused(conn, constraint="commands_hosting_reason_by_key", table="commands"):
                insert_hosting_command(conn, "resume_hosting", "project-deleted", reason)
        with refused(conn, constraint="commands_hosting_reason_by_kind", table="commands"):
            insert_hosting_command(conn, "suspend_hosting", "evt-1", None)
    with commands_db.login(ROLE_API) as conn:
        with refused(conn, constraint="commands_hosting_reason_by_kind", table="commands"):
            conn.execute(
                q(INSERT_COMMAND),
                (
                    uid("command:hr-deploy"),
                    HR,
                    HR_BUILT,
                    "deploy",
                    "key-deploy",
                    fingerprint("deploy"),
                    1,
                    "subject-1",
                    "billing",
                ),
            )
        with allowed(conn):
            conn.execute(
                q(INSERT_COMMAND),
                (
                    uid("command:hr-deploy"),
                    HR,
                    HR_BUILT,
                    "deploy",
                    "key-deploy",
                    fingerprint("deploy"),
                    1,
                    "subject-1",
                    None,
                ),
            )


# The edge cases of the Task 2 review, and the bounds of the key rule. ``admin:project-deleted``
# gives billing at 00018 and 00019; migration 00020 makes it admin_suspended (test_schema_00020).
EDGE_KEYS = (
    "evt-1",
    "admin-1",
    "admin:project-deleted",
    "",
    "project-deleted",
    "project-deleted.retry-1",
    "project-deleted.retry-999999999",
    "project-deleted.retry-0",
    "project-deleted.retry-01",
    "project-deleted.retry-1000000000",
    "project-deleted.retry-1.retry-2",
    "xproject-deleted",
    "owner-status:1",
    "owner-status:5.retry-2",
    "owner-status:5.retry-0",
    "owner-status:9007199254740991",
    "owner-status:9007199254740992",
    "owner-status:9999999999999999",
    "owner-status:10000000000000000",
    "owner-status:99999999999999999999",
    "owner-status:99999999999999999999.retry-1",
    "owner-status:0",
    "owner-status:01",
    "owner-status:",
    "owner-status:abc",
    "owner-status:5.retry-1.retry-2",
    "Owner-status:5",
    "evt-1.retry-3",
)


def domain_reason(kind: CommandKind, key: str) -> str | None:
    """What the domain accepts: the reason ``hosting_reason_of_key`` gives a valid key, or None
    when the domain refuses the key or the kind."""
    try:
        validate_idempotency_key(key)
        return str(hosting_reason_of_key(kind, key))
    except DomainError:
        return None


def reason_at_00018(kind: CommandKind, key: str) -> str | None:
    """The domain's rule as 00018 wrote it in the DB: before 00020 an ``admin:`` key is a Payment
    event key like any other, so billing."""
    reason = domain_reason(kind, key)
    return "billing" if reason == "admin_suspended" else reason


@pytest.mark.parametrize("kind", ["suspend_hosting", "resume_hosting"])
def test_hosting_reason_matches_the_domain_key_rule(commands_db: Database, kind: str) -> None:
    """For every edge key and every reason, the DB admits the command exactly when the domain
    gives that reason for that key; a key the domain refuses is admitted with no reason."""
    with commands_db.login(ROLE_HOSTING_INTAKE) as conn:
        for key in EDGE_KEYS:
            expected = reason_at_00018(CommandKind(kind), key)
            assert reason_of_key(kind, key) == expected, key
            for reason in ("billing", "user_suspended", "project_deleted"):
                if reason == expected:
                    with allowed(conn):
                        insert_hosting_command(conn, kind, key, reason)
                else:
                    with refused(
                        conn, constraint="commands_hosting_reason_by_key", table="commands"
                    ):
                        insert_hosting_command(conn, kind, key, reason)


def test_edge_keys_pinned() -> None:
    """The edge cases carried over from the Task 2 review, pinned by value."""
    suspend = CommandKind.SUSPEND_HOSTING
    assert domain_reason(suspend, "admin-1") == "billing"
    assert domain_reason(suspend, "") is None
    assert domain_reason(suspend, "owner-status:5.retry-0") is None
    # Task 9 made the domain give admin_suspended; the 00018 DB rule still gives billing.
    assert domain_reason(suspend, "admin:project-deleted") == "admin_suspended"
    assert reason_at_00018(suspend, "admin:project-deleted") == "billing"
    assert domain_reason(suspend, "project-deleted.retry-0") == "billing"
    assert domain_reason(suspend, "owner-status:9007199254740991") == "user_suspended"
    assert domain_reason(suspend, "owner-status:9007199254740992") is None


def test_hosting_reason_never_changes(commands_db: Database) -> None:
    command_id = uid("command:hr-fixed")
    seed(
        commands_db,
        (
            q(INSERT_COMMAND),
            [
                command_id,
                HR,
                None,
                "suspend_hosting",
                "evt-fixed",
                fingerprint("f"),
                1,
                None,
                "billing",
            ],
        ),
    )
    change = "UPDATE provisioning.commands SET hosting_reason = %s WHERE command_id = %s"
    with commands_db.login(ROLE_WORKER) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(q(change), ("user_suspended", command_id))
    with commands_db.as_role(ROLE_MIGRATOR) as conn:
        with refused(conn, constraint="commands_identity_immutable", table="commands"):
            conn.execute(q(change), ("project_deleted", command_id))
    with commands_db.login(ROLE_WORKER) as conn, allowed(conn):
        conn.execute(
            q(
                "UPDATE provisioning.commands SET status = 'running', updated_at = now()"
                " WHERE command_id = %s"
            ),
            (command_id,),
        )


# --- the retired role -----------------------------------------------------------------------------


def test_payment_intake_role_holds_nothing_and_drop_script_runs(db: Database) -> None:
    with db.admin() as conn:
        assert privileges(conn, ROLE_PAYMENT_INTAKE) == set()
        role_exists = "SELECT count(*) FROM pg_roles WHERE rolname = %s"
        with conn.transaction(force_rollback=True):
            conn.execute(q(DROP_SCRIPT.read_text()))
            assert scalar(conn, role_exists, (ROLE_PAYMENT_INTAKE,)) == 0
            # Idempotent: a second run finds the role gone.
            conn.execute(q(DROP_SCRIPT.read_text()))
        # The role is cluster-wide and later tests still need it.
        assert scalar(conn, role_exists, (ROLE_PAYMENT_INTAKE,)) == 1


def test_drop_script_refuses_below_00018(new_db: DbFactory) -> None:
    database = new_db(17)
    with database.admin() as conn:
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState) as info:
            with conn.transaction():
                conn.execute(q(DROP_SCRIPT.read_text()))
        assert "not 18 or later" in str(info.value)
        assert (
            scalar(conn, "SELECT count(*) FROM pg_roles WHERE rolname = %s", (ROLE_PAYMENT_INTAKE,))
            == 1
        )


def test_00018_refuses_while_payment_intake_holds_a_privilege(new_db: DbFactory) -> None:
    database = new_db(17)
    with database.admin() as conn:
        conn.execute(
            q(f"GRANT SELECT (project_id) ON provisioning.releases TO {ROLE_PAYMENT_INTAKE}")
        )
    with pytest.raises(RuntimeError) as info:
        database.migrate(18)
    assert "provisioning_payment_intake still holds a privilege" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 17
        conn.execute(
            q(f"REVOKE SELECT (project_id) ON provisioning.releases FROM {ROLE_PAYMENT_INTAKE}")  # noqa: S608
        )
    database.migrate(18)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 18


def test_00018_refuses_while_payment_intake_holds_a_database_privilege(
    new_db: DbFactory,
) -> None:
    """CONNECT on a database is a shared object (pg_shdepend dbid 0), which the check covers too."""
    database = new_db(17)
    grant = f"CONNECT ON DATABASE {database.name}"
    with database.admin() as conn:
        conn.execute(q(f"GRANT {grant} TO {ROLE_PAYMENT_INTAKE}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(18)
        assert "provisioning_payment_intake still holds a privilege" in str(info.value)
        with database.admin() as conn:
            assert scalar(conn, APPLIED) == 17
    finally:
        with database.admin() as conn:
            conn.execute(q(f"REVOKE {grant} FROM {ROLE_PAYMENT_INTAKE}"))
    database.migrate(18)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 18


def test_00018_refuses_while_payment_intake_has_a_member(new_db: DbFactory) -> None:
    database = new_db(17)
    with database.login(ROLE_PAYMENT_INTAKE):
        with pytest.raises(RuntimeError) as info:
            database.migrate(18)
    assert "provisioning_payment_intake still has a role membership" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 17


# --- grants ---------------------------------------------------------------------------------------

RENAMED = {
    ("releases", "github_repository_id"): "repository_id",
    ("source_published_events", "github_repository_id"): "repository_id",
}
NEW_COLUMNS = {
    "project_hosting": ("offline_reasons", "owner_status_version"),
    "commands": ("hosting_reason",),
}


def expected_after(
    before: set[tuple[str, str, str]], table_columns: dict[str, set[str]]
) -> set[tuple[str, str, str]]:
    """``before`` under the new column names, plus the new columns wherever the role held the
    privilege on every column of the table (a table-level grant covers a new column)."""
    out = {(t, RENAMED.get((t, c), c), p) for t, c, p in before}
    for table, columns in NEW_COLUMNS.items():
        for priv in {p for t, _, p in before if t == table}:
            held = {c for t, c, p in before if t == table and p == priv}
            if held == table_columns[table]:
                out |= {(table, c, priv) for c in columns}
    return out


def test_exactly_the_listed_grants_are_added(new_db: DbFactory) -> None:
    database = new_db(17)
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
    database.migrate(18)
    with database.admin() as conn:
        after = {r: privileges(conn, r) for r in ROLES}
    hosting_columns = {
        "project_id",
        "live_release_id",
        "last_command_number",
        "routing_status",
        "hosting_status",
        "updated_at",
        "offline_reasons",
        "owner_status_version",
    }
    added = {
        ROLE_WORKER: {("project_hosting", "offline_reasons", "UPDATE")},
        ROLE_HOSTING_INTAKE: {("project_hosting", c, "INSERT") for c in hosting_columns}
        | {("project_hosting", "owner_status_version", "UPDATE")},
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
    assert after[ROLE_PAYMENT_INTAKE] == set()


# --- the migration itself -------------------------------------------------------------------------


def test_there_is_no_down_section_and_no_default() -> None:
    text = (MIGRATIONS_DIR / MIGRATION).read_text()
    assert "-- +goose Up" in text
    assert "-- +goose Down" not in text
    code = "\n".join(line.split("--")[0] for line in text.splitlines()).upper()
    for word in ("DEFAULT", "CASCADE", "DELETE"):
        assert re.search(rf"\b{word}\b", code) is None, word


def test_00018_refuses_other_roles(new_db: DbFactory) -> None:
    database = new_db(17)
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
        assert scalar(conn, APPLIED) == 17
        assert (
            scalar(
                conn,
                "SELECT count(*) FROM pg_type WHERE typname = 'offline_reason'",
            )
            == 0
        )
        assert column(
            conn,
            "SELECT column_name::text FROM information_schema.columns"
            " WHERE table_schema = 'provisioning' AND table_name = 'releases'"
            " AND column_name LIKE '%%repository_id'",
        ) == ["github_repository_id"]


def test_00018_refuses_when_the_hosting_intake_role_is_missing(
    harness: Harness, new_db: DbFactory
) -> None:
    database = new_db(17)
    hidden = "prov_hidden_hosting_intake"
    with harness.connect() as admin:
        admin.execute(q(f"ALTER ROLE {ROLE_HOSTING_INTAKE} RENAME TO {hidden}"))
    try:
        with pytest.raises(RuntimeError) as info:
            database.migrate(18)
    finally:
        with harness.connect() as admin:
            admin.execute(q(f"ALTER ROLE {hidden} RENAME TO {ROLE_HOSTING_INTAKE}"))
    assert f"missing role(s): {ROLE_HOSTING_INTAKE}" in str(info.value)
    with database.admin() as conn:
        assert scalar(conn, APPLIED) == 17


def test_new_functions_follow_the_hygiene(db: Database) -> None:
    with db.admin() as conn:
        found = rows(
            conn,
            "SELECT p.proname::text, p.prosecdef, p.proconfig, p.provolatile::text,"
            " has_function_privilege('public', p.oid, 'EXECUTE')"
            " FROM pg_proc AS p WHERE p.pronamespace = 'provisioning'::regnamespace"
            " AND p.proname IN ('offline_reasons_distinct', 'project_hosting_reasons_guard')"
            " ORDER BY 1",
        )
    assert found == [
        ("offline_reasons_distinct", False, ["search_path=pg_catalog"], "i", False),
        ("project_hosting_reasons_guard", False, ["search_path=pg_catalog"], "v", False),
    ]


@pytest.mark.parametrize("version", [18, 20, None], ids=["at 00018", "at 00020", "latest"])
def test_only_the_writers_of_project_hosting_execute_the_check_function(
    new_db: DbFactory, version: int | None
) -> None:
    """A check calls its function with the rights of the writing role, so each role that inserts
    or updates project_hosting holds EXECUTE on offline_reasons_distinct, and no other role. At
    00020 the operator updates project_hosting (the sub-domain backfill), so it holds it too;
    00021 takes it back with the backfill."""
    db = new_db(version)
    function = "provisioning.offline_reasons_distinct(provisioning.offline_reason[])"
    with db.admin() as conn:
        holders = {
            role
            for role in ROLES
            if scalar(conn, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function))
        }
    writers = {ROLE_API, ROLE_WORKER, ROLE_INTAKE, ROLE_HOSTING_INTAKE, ROLE_MIGRATOR}
    assert holders == (writers | {ROLE_OPERATOR} if version == 20 else writers)
