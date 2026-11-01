"""Shared helpers of the store tests (prototype task B3). This module holds no test.

- :func:`role_engine` opens a store engine (SQLAlchemy, psycopg) for a fresh LOGIN role that is a
  member of exactly one service role, on one test database, and drops the role afterwards.
- The builders write rows under the revision 19 names with values the domain accepts (real
  request fingerprints, valid digests and SHAs), so a store can load what was seeded.
- Seeding follows B2's seeding path (decision 0008 section 5) with identifiers from an allow-list.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID, uuid4

from psycopg import sql
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import Database, q
from tests.integration._rows import digest, sha

from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    compute_request_fingerprint,
    hosting_reason_of_key,
)
from provisioning.infrastructure.postgres import EngineSettings, Timeouts, database_url, open_engine

type Row = dict[str, object]

TIMEOUTS = Timeouts(
    transaction=timedelta(seconds=10), statement=timedelta(seconds=5), lock=timedelta(seconds=2)
)
ENGINE = EngineSettings(
    pool_size=4,
    max_overflow=2,
    pool_timeout=timedelta(seconds=10),
    connect_timeout=timedelta(seconds=10),
    idle_in_transaction_timeout=TIMEOUTS.transaction,  # as the hosts set it (DB_TX_TIMEOUT)
)
T0 = datetime(2026, 10, 6, 9, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@asynccontextmanager
async def role_engine(
    database: Database, group: str, settings: EngineSettings = ENGINE
) -> AsyncIterator[AsyncEngine]:
    """An engine of a fresh login that is a member of ``group`` alone."""
    role = f"prov_login_{secrets.token_hex(6)}"
    password = secrets.token_hex(16)
    with database.admin() as admin:
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                sql.Identifier(role), sql.Literal(password), sql.Identifier(group)
            )
        )
    try:
        server = database.harness.server
        url = database_url(
            host=server.host,
            port=server.port,
            database=database.name,
            user=role,
            password=password,
        )
        engine = open_engine(url, settings)
        try:
            yield engine
        finally:
            await engine.dispose()
    finally:
        with database.admin() as admin:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def insert(table: str, row: Row) -> sql.Composed:
    """The INSERT of a row written with the revision 19 column names. The table must be one of
    the six mirrored tables (an allow-list) and every name is quoted as an identifier."""
    if table not in _TABLES:
        raise ValueError("not a mirrored table")
    schema_name, table_name = table.split(".")
    return sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema_name),
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(c) for c in row),
        sql.SQL(", ").join(sql.Placeholder() for _ in row),
    )


def seed_rows(database: Database, *rows: tuple[str, Row]) -> None:
    """B2's seeding path (decision 0008 section 5): one superuser transaction with
    ``session_replication_role = replica``, so guards and foreign keys are skipped while check
    constraints and unique indexes still apply."""
    with database.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        for table, row in rows:
            conn.execute(insert(table, row), list(row.values()))


# --- builders (revision 19 names) ------------------------------------------------------------

RELEASES = "provisioning.releases"
HOSTING = "provisioning.project_hosting"
COMMANDS = "provisioning.commands"
STEPS = "provisioning.release_steps"
DISPATCH = "provisioning_ops.command_dispatch"
EVENTS = "provisioning_ops.source_published_events"
_TABLES = frozenset({RELEASES, HOSTING, COMMANDS, STEPS, DISPATCH, EVENTS})

_IMAGE_STATUSES = {"built", "candidate_ready", "promoting", "live", "superseded", "rolled_back"}


def release_row(
    project: UUID, status: str, *, release_id: UUID | None = None, name: str = "r", **over: object
) -> Row:
    row: Row = {
        "release_id": release_id or uuid4(),
        "project_id": project,
        "generation_request_id": uuid4(),
        "repository_id": 4242,
        "git_commit_sha": sha(name),
        "container_image_digest": digest(name) if status in _IMAGE_STATUSES else None,
        "status": status,
        "failure_code": "test_failure" if status in {"failed", "rolled_back"} else None,
        "failure_summary": None,
        "created_at": at(0),
        "updated_at": at(0),
        "promoted_at": at(60) if status in {"live", "superseded"} else None,
    }
    row.update(over)
    return row


class Derived:
    """The marker of a value the builder derives."""


DERIVED: Final = Derived()


def subdomain_for(project: UUID) -> str:
    """The sub-domain the test builders give a project: a DNS label from its whole ID, so two
    projects never share one (FR-54 refuses a taken one; IDs that share a prefix differ)."""
    return f"site-{project.hex}"


def hosting_row(
    project: UUID,
    *,
    number: int = 0,
    live: UUID | None = None,
    routing: str = "stable",
    hosting: str = "online",
    reasons: str | None = None,
    owner_status_version: int = 0,
    subdomain: str | Derived | None = DERIVED,
) -> Row:
    """A hosting row (migration 00018 and 00020 columns included). ``reasons`` is the array
    literal of ``offline_reasons``; by default ``{}`` when online and ``{billing}`` when offline (a
    Payment suspend, the cause these tests imply). ``subdomain`` is by default a label made from
    the project ID (NOT NULL from 00021); ``None`` is a row from before 00020."""
    if reasons is None:
        reasons = "{billing}" if hosting == "offline" else "{}"
    if isinstance(subdomain, Derived):
        subdomain = subdomain_for(project)
    return {
        "project_id": project,
        "live_release_id": live,
        "last_command_number": number,
        "routing_status": routing,
        "hosting_status": hosting,
        "updated_at": at(0),
        "offline_reasons": reasons,
        "owner_status_version": owner_status_version,
        "subdomain": subdomain,
    }


def command_row(
    project: UUID,
    kind: CommandKind,
    number: int,
    status: str,
    *,
    release: UUID | None = None,
    key: str | None = None,
    command_id: UUID | None = None,
    **over: object,
) -> Row:
    key = key or f"key-{uuid4().hex[:8]}"
    fingerprint = compute_request_fingerprint(
        CommandRequest(project_id=project, kind=kind, release_id=release, idempotency_key=key)
    )
    row: Row = {
        "command_id": command_id or uuid4(),
        "project_id": project,
        "release_id": release,
        "kind": str(kind),
        "idempotency_key": key,
        "request_fingerprint": fingerprint,
        "command_number": number,
        "requested_by_subject": None if kind.names_no_release() else "subject-1",
        "status": status,
        "failure_code": "test_failure" if status in {"failed", "skipped"} else None,
        "created_at": at(0),
        "updated_at": at(0),
        "hosting_reason": (hosting_reason_of_key(kind, key).value if kind.is_hosting() else None),
    }
    row.update(over)
    return row


def dispatch_row(command_id: UUID, **over: object) -> Row:
    row: Row = {
        "command_id": command_id,
        "correlation_id": "corr-1",
        "deadline_at": datetime.now(UTC) + timedelta(hours=1),
        "next_publish_at": at(0),
        "publish_attempts": 0,
        "last_broker_ack_at": None,
        "execution_attempts": 0,
        "claim_number": 0,
        "claimed_by_worker": None,
        "claim_expires_at": None,
        "retry_after": None,
        "last_failure_code": None,
        "finished_at": None,
        "created_at": at(0),
        "updated_at": at(0),
    }
    row.update(over)
    return row


def step_row(
    release: UUID, attempt: int, status: str, *, name: str = "build", **over: object
) -> Row:
    row: Row = {
        "release_step_id": uuid4(),
        "release_id": release,
        "step_name": name,
        "attempt": attempt,
        "status": status,
        "started_at": None,
        "finished_at": None,
        "deadline_at": None,
        "failure_code": None,
        "log_ref": None,
    }
    row.update(over)
    return row


def scalar(database: Database, query: str, *params: object) -> object:
    with database.admin() as conn:
        found = conn.execute(q(query), params).fetchall()
    assert len(found) == 1, f"expected one row, got {len(found)}"
    return found[0][0]


def row_of(database: Database, query: str, *params: object) -> tuple[object, ...]:
    with database.admin() as conn:
        found = conn.execute(q(query), params).fetchall()
    assert len(found) == 1, f"expected one row, got {len(found)}"
    return tuple(found[0])
