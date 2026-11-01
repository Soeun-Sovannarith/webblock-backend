"""Row builders and the revision 19 name maps for the schema tests (prototype task B2).

Rows are written with the *logical* names: the table and column names of revision 16, which exist at
migration 00010. :func:`insert` writes the same row under the names of the schema under test, so one
row set seeds a database at 00010 (``new=False``) and one at 00011 (``new=True``) alike. The maps
below are typed from decision 0012 section 3.1, not derived from the migration, so a test that uses
them checks the migration against the decision.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

type Row = dict[str, object]

# schema.table of revision 16 -> table of revision 19 (same schema)
TABLE_RENAMES: Final[dict[str, str]] = {
    "provisioning.project_release_state": "project_hosting",
    "provisioning_ops.command_work": "command_dispatch",
    "provisioning_ops.source_published_inbox": "source_published_events",
}
TABLES: Final[tuple[str, ...]] = (
    "provisioning.releases",
    "provisioning.project_release_state",
    "provisioning.commands",
    "provisioning.release_steps",
    "provisioning_ops.command_work",
    "provisioning_ops.source_published_inbox",
)

# (table of revision 16 without schema, column of revision 16) -> column of revision 19
COLUMN_RENAMES: Final[dict[tuple[str, str], str]] = {
    ("project_release_state", "active_release_id"): "live_release_id",
    ("project_release_state", "operation_generation"): "last_command_number",
    ("project_release_state", "route_state"): "routing_status",
    ("project_release_state", "hosting_state"): "hosting_status",
    ("releases", "source_generation_request_id"): "generation_request_id",
    ("releases", "source_repository_id"): "github_repository_id",
    ("releases", "source_commit_sha"): "git_commit_sha",
    ("releases", "image_digest"): "container_image_digest",
    ("commands", "fencing_generation"): "command_number",
    ("commands", "request_hash"): "request_fingerprint",
    ("commands", "requested_by"): "requested_by_subject",
    ("release_steps", "reason_code"): "failure_code",
    ("release_steps", "details_ref"): "log_ref",
    ("command_work", "lease_token"): "claim_number",
    ("command_work", "claimed_by"): "claimed_by_worker",
    ("command_work", "lease_expires_at"): "claim_expires_at",
    ("command_work", "claim_count"): "execution_attempts",
    ("command_work", "next_dispatch_at"): "next_publish_at",
    ("command_work", "last_confirmed_at"): "last_broker_ack_at",
    ("command_work", "retry_not_before"): "retry_after",
    ("command_work", "last_error_code"): "last_failure_code",
    ("source_published_inbox", "repository_id"): "github_repository_id",
    ("source_published_inbox", "commit_sha"): "git_commit_sha",
    ("source_published_inbox", "conflict_count"): "mismatched_repeat_count",
    ("source_published_inbox", "last_conflict_at"): "last_mismatched_repeat_at",
    ("source_published_inbox", "attempt_budget"): "max_attempts",
    ("source_published_inbox", "last_redriven_by"): "last_manual_retry_by",
    ("source_published_inbox", "last_redriven_at"): "last_manual_retry_at",
}


# (table of revision 19 without schema, column of revision 19) -> column of revision 25 (migration
# 00018, decision 0021; Notion "5 · Provisioning")
REV25_COLUMN_RENAMES: Final[dict[tuple[str, str], str]] = {
    ("releases", "github_repository_id"): "repository_id",
    ("source_published_events", "github_repository_id"): "repository_id",
}

# Logical columns that exist only from migration 00018: written at 00018 and later, left out below.
AT_00018_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "project_release_state": frozenset({"offline_reasons", "owner_status_version"}),
    "commands": frozenset({"hosting_reason"}),
}


def table_name(table: str, *, new: bool) -> str:
    """``schema.table`` of a logical ``schema.table``, under the names of the schema under test."""
    if not new:
        return table
    schema = table.split(".")[0]
    return f"{schema}.{TABLE_RENAMES.get(table, table.split('.')[1])}"


def column_name(table: str, column: str, *, new: bool, at_00018: bool = True) -> str:
    """The column of a logical column: revision 16 names, or revision 19 names when ``new``, with
    the 00018 renames when also ``at_00018``."""
    short = table.split(".")[1]
    if not new:
        return column
    renamed = COLUMN_RENAMES.get((short, column), column)
    if not at_00018:
        return renamed
    return REV25_COLUMN_RENAMES.get((TABLE_RENAMES.get(table, short), renamed), renamed)


# Logical columns that exist only from migration 00020 (NOT NULL from 00021).
AT_00020_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "project_release_state": frozenset({"subdomain"}),
}


def _present(
    table: str, column: str, *, new: bool, at_00018: bool, at_00020: bool | None = None
) -> bool:
    """Whether a logical column exists in the schema under test. ``at_00020`` is by default
    ``at_00018``: a test that seeds a database at 00018 or 00019 passes ``at_00020=False``."""
    short = table.split(".")[1]
    if at_00020 is None:
        at_00020 = at_00018
    if column in AT_00020_COLUMNS.get(short, frozenset()):
        return new and at_00020
    return (new and at_00018) or column not in AT_00018_COLUMNS.get(short, frozenset())


def rename_row(table: str, row: Row, *, at_00018: bool = True, at_00020: bool | None = None) -> Row:
    """A row of revision 16 under the names of revision 19 (the value ``serving`` -> ``online``),
    with the 00018 renames and columns when ``at_00018``."""
    out: Row = {}
    for column, value in row.items():
        if not _present(table, column, new=True, at_00018=at_00018, at_00020=at_00020):
            continue
        out[column_name(table, column, new=True, at_00018=at_00018)] = (
            "online" if (column, value) == ("hosting_state", "serving") else value
        )
    return out


# --- values ----------------------------------------------------------------------------------

T0: Final = datetime(2026, 10, 6, 9, 0, 0, tzinfo=UTC)
_NS: Final = uuid.UUID("5b1a7c0e-3c1d-4f0a-9b8e-0f2f6f0b4a11")


def uid(name: str) -> uuid.UUID:
    """A stable UUID for a readable name."""
    return uuid.uuid5(_NS, name)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def sha(name: str) -> str:
    return hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()


def digest(name: str) -> str:
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


def fingerprint(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


# --- builders (logical names) ----------------------------------------------------------------


def release(name: str, project: str, status: str, **over: object) -> Row:
    row: Row = {
        "release_id": uid(f"release:{name}"),
        "project_id": uid(f"project:{project}"),
        "source_generation_request_id": uid(f"request:{name}"),
        "source_repository_id": 1000 + sum(name.encode()),
        "source_commit_sha": sha(name),
        "image_digest": digest(name)
        if status in {"built", "candidate_ready", "promoting", "live", "superseded", "rolled_back"}
        else None,
        "status": status,
        "failure_code": "test_failure" if status in {"failed", "rolled_back"} else None,
        "failure_summary": None,
        "created_at": at(0),
        "updated_at": at(0),
        "promoted_at": at(60) if status in {"live", "superseded"} else None,
    }
    row.update(over)
    return row


def project_state(
    project: str,
    *,
    generation: int = 0,
    active: str | None = None,
    route: str = "stable",
    hosting: str = "serving",
    reasons: str | None = None,
    owner_status_version: int = 0,
    **over: object,
) -> Row:
    """A hosting row. ``reasons`` is the array literal of ``offline_reasons`` (00018): ``{}`` when
    online; an offline row names the reason its test implies, so it must be given. The sub-domain
    (00020) is ``site-<project>`` unless given (``subdomain=None`` for a row from before 00020)."""
    if reasons is None:
        if hosting == "offline":
            raise ValueError("an offline hosting row needs its offline reasons")
        reasons = "{}"
    row: Row = {
        "project_id": uid(f"project:{project}"),
        "active_release_id": uid(f"release:{active}") if active else None,
        "operation_generation": generation,
        "route_state": route,
        "hosting_state": hosting,
        "updated_at": at(0),
        "offline_reasons": reasons,
        "owner_status_version": owner_status_version,
        "subdomain": f"site-{project}",
    }
    row.update(over)
    return row


def reason_of_key(kind: str, key: str) -> str | None:
    """The ``hosting_reason`` a command of ``kind`` with ``key`` holds at 00018: the key rule of
    decision 0021, typed here from the decision rather than imported, so a test checks the
    migration against it. An empty key, a key with the reserved owner-status prefix and no valid
    version, and a resume keyed project-deleted have none (the DB refuses them)."""
    if kind not in {"suspend_hosting", "resume_hosting"} or key == "":
        return None
    if re.fullmatch(r"project-deleted(\.retry-[1-9][0-9]{0,8})?", key):
        return None if kind == "resume_hosting" else "project_deleted"
    if key.startswith("owner-status:"):
        found = re.fullmatch(r"owner-status:([1-9][0-9]{0,15})(\.retry-[1-9][0-9]{0,8})?", key)
        if found is None or int(found[1]) > 9007199254740991:
            return None
        return "user_suspended"
    return "billing"


def command(
    name: str,
    project: str,
    release_name: str | None,
    number: int,
    status: str,
    kind: str = "deploy",
    **over: object,
) -> Row:
    hosting = kind in {"suspend_hosting", "resume_hosting"}
    row: Row = {
        "command_id": uid(f"command:{name}"),
        "project_id": uid(f"project:{project}"),
        "release_id": None if hosting else uid(f"release:{release_name}"),
        "kind": kind,
        "idempotency_key": f"key-{name}",
        "request_hash": fingerprint(f"request:{name}"),
        "fencing_generation": number,
        "requested_by": None if hosting else f"subject-{name}",
        "status": status,
        "failure_code": "test_failure" if status in {"failed", "skipped"} else None,
        "created_at": at(0),
        "updated_at": at(0),
    }
    row.update(over)
    if "hosting_reason" not in over:
        row["hosting_reason"] = reason_of_key(kind, str(row["idempotency_key"]))
    return row


def step(
    name: str, release_name: str, step_name: str, attempt: int, status: str, **over: object
) -> Row:
    row: Row = {
        "release_step_id": uid(f"step:{name}"),
        "release_id": uid(f"release:{release_name}"),
        "step_name": step_name,
        "attempt": attempt,
        "status": status,
        "started_at": None,
        "finished_at": None,
        "deadline_at": None,
        "reason_code": None,
        "details_ref": None,
    }
    row.update(over)
    return row


def command_work(command_name: str, **over: object) -> Row:
    row: Row = {
        "command_id": uid(f"command:{command_name}"),
        "correlation_id": f"corr-{command_name}",
        "deadline_at": at(3600),
        "next_dispatch_at": at(10),
        "publish_attempts": 0,
        "last_confirmed_at": None,
        "claim_count": 0,
        "lease_token": 0,
        "claimed_by": None,
        "lease_expires_at": None,
        "retry_not_before": None,
        "last_error_code": None,
        "finished_at": None,
        "created_at": at(0),
        "updated_at": at(0),
    }
    row.update(over)
    return row


def inbox(name: str, project: str, status: str = "pending", **over: object) -> Row:
    row: Row = {
        "project_id": uid(f"project:{project}"),
        "generation_request_id": uid(f"request:{name}"),
        "repository_id": 2000 + sum(name.encode()),
        "commit_sha": sha(name),
        "status": status,
        "attempts": 0,
        "next_attempt_at": at(5),
        "outcome_code": None,
        "release_id": None,
        "conflict_count": 0,
        "last_conflict_at": None,
        "received_at": at(0),
        "updated_at": at(0),
        "finished_at": None,
        "attempt_budget": 3,
        "last_redriven_by": None,
        "last_redriven_at": None,
    }
    row.update(over)
    return row


def insert(
    table: str, row: Row, *, new: bool, at_00018: bool = True, at_00020: bool | None = None
) -> tuple[str, list[object]]:
    """The INSERT of a logical row, under the names of revision 19 if ``new``, else 16; with the
    columns and renames of migration 00018 when ``new`` and ``at_00018`` (a test that seeds a
    database below 00018 passes ``at_00018=False``), and the 00020 sub-domain unless
    ``at_00020=False`` (a test that seeds at 00018 or 00019)."""
    kept = {
        c: v
        for c, v in row.items()
        if _present(table, c, new=new, at_00018=at_00018, at_00020=at_00020)
    }
    columns = ", ".join(column_name(table, c, new=new, at_00018=at_00018) for c in kept)
    marks = ", ".join(["%s"] * len(kept))
    values = [
        "online" if new and (c, v) == ("hosting_state", "serving") else v for c, v in kept.items()
    ]
    target = table_name(table, new=new)
    # S608: the names are constants of this module; the values are parameters
    text = f"INSERT INTO {target} ({columns}) VALUES ({marks})"  # noqa: S608
    return text, values


# --- the representative row set --------------------------------------------------------------


def world() -> list[tuple[str, Row]]:
    """Representative rows of every table, in every status, written with the logical names.

    Written through the seeding path (guards skipped, check constraints and unique indexes still
    apply). Projects:

    - ``p1``: a live release with its history (superseded, failed, rolled back, a later built one),
      hosting commands, and a deploy running with a claimed dispatch row;
    - ``p2``: a candidate with a promote accepted, its route uncertain and hosting offline;
    - ``p3``: releases before and during the build, one promoting with the route changing, steps in
      every status, inbox events in every status;
    - ``p4``: an accepted release without a step (for the insert guards);
    - ``p5``: a built release with a deploy accepted and no dispatch row yet.
    """
    rows: list[tuple[str, Row]] = []
    r = "provisioning.releases"
    s = "provisioning.project_release_state"
    c = "provisioning.commands"
    st = "provisioning.release_steps"
    w = "provisioning_ops.command_work"
    ib = "provisioning_ops.source_published_inbox"

    # p1
    rows += [
        (r, release("p1-live", "p1", "live")),
        (r, release("p1-superseded", "p1", "superseded")),
        (
            r,
            release(
                "p1-failed", "p1", "failed", failure_summary="the candidate did not become ready"
            ),
        ),
        (r, release("p1-rolled-back", "p1", "rolled_back")),
        (r, release("p1-built", "p1", "built", created_at=at(100), updated_at=at(100))),
        (s, project_state("p1", generation=6, active="p1-live")),
        (c, command("p1-1", "p1", "p1-superseded", 1, "succeeded")),
        (c, command("p1-2", "p1", "p1-rolled-back", 2, "failed", kind="promote")),
        (c, command("p1-3", "p1", "p1-rolled-back", 3, "succeeded", kind="rollback")),
        (c, command("p1-4", "p1", None, 4, "skipped", kind="suspend_hosting")),
        (c, command("p1-5", "p1", None, 5, "succeeded", kind="resume_hosting")),
        (c, command("p1-6", "p1", "p1-built", 6, "running")),
        (
            w,
            command_work(
                "p1-6",
                publish_attempts=1,
                last_confirmed_at=at(12),
                claim_count=1,
                lease_token=1,
                claimed_by="worker-1",
                lease_expires_at=at(300),
                next_dispatch_at=at(300),
            ),
        ),
        (
            w,
            command_work(
                "p1-1",
                publish_attempts=2,
                last_confirmed_at=at(12),
                claim_count=1,
                lease_token=1,
                retry_not_before=at(30),
                last_error_code="transient_error",
                finished_at=at(40),
            ),
        ),
        (
            st,
            step(
                "p1-failed-candidate",
                "p1-failed",
                "candidate",
                1,
                "failed",
                started_at=at(10),
                finished_at=at(30),
                reason_code="readiness_timeout",
                details_ref="candidate-log/1",
            ),
        ),
    ]
    # p2
    rows += [
        (r, release("p2-candidate", "p2", "candidate_ready")),
        (r, release("p2-built", "p2", "built")),
        (
            s,
            project_state(
                "p2", generation=2, route="uncertain", hosting="offline", reasons="{billing}"
            ),
        ),
        (c, command("p2-1", "p2", "p2-built", 1, "failed")),
        (c, command("p2-2", "p2", "p2-candidate", 2, "accepted", kind="promote")),
        (w, command_work("p2-2")),
    ]
    # p3
    rows += [
        (r, release("p3-accepted", "p3", "accepted")),
        (r, release("p3-building", "p3", "building")),
        (r, release("p3-promoting", "p3", "promoting")),
        (s, project_state("p3", route="changing")),
        (st, step("p3-pending", "p3-accepted", "build", 1, "pending")),
        (
            st,
            step(
                "p3-running",
                "p3-building",
                "build",
                2,
                "running",
                started_at=at(10),
                deadline_at=at(600),
                details_ref="https://jenkins.invalid/job/site/2/",
            ),
        ),
        (
            st,
            step(
                "p3-succeeded",
                "p3-promoting",
                "build",
                1,
                "succeeded",
                started_at=at(10),
                finished_at=at(20),
                details_ref="https://jenkins.invalid/job/site/1/",
            ),
        ),
        (ib, inbox("p3-pending", "p3", "pending", attempts=1)),
        # the event that produced release p3-accepted: same project, request and release
        (
            ib,
            inbox(
                "p3-accepted",
                "p3",
                "accepted",
                attempts=1,
                release_id=uid("release:p3-accepted"),
                finished_at=at(8),
            ),
        ),
        (
            ib,
            inbox(
                "p3-rejected",
                "p3",
                "rejected",
                attempts=1,
                outcome_code="repository_mismatch",
                conflict_count=2,
                last_conflict_at=at(9),
                finished_at=at(8),
            ),
        ),
        (
            ib,
            inbox(
                "p3-failed",
                "p3",
                "failed",
                attempts=3,
                outcome_code="registry_unavailable",
                finished_at=at(8),
                last_redriven_by="operator-1",
                last_redriven_at=at(7),
            ),
        ),
    ]
    # p4
    rows += [
        (r, release("p4-accepted", "p4", "accepted")),
        (s, project_state("p4")),
    ]
    # p5
    rows += [
        (r, release("p5-built", "p5", "built")),
        (s, project_state("p5", generation=1)),
        (c, command("p5-1", "p5", "p5-built", 1, "accepted")),
    ]
    return rows
