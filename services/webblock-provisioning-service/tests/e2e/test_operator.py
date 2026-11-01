"""The operator runbook rehearsed live (decision 0014 section 21 ruling 11; the operator runbook;
task W6-E): park an event, see it listed, re-drive it, see the release accepted; the worker's
login is refused the re-drive; each re-drive is an audit line.

Parking. The prototype's repository registry is a file stand-in that always answers, so the
processor never fails a row by itself. The test parks one as an outage of Project's registry
would leave it: an event for a registered repository is written to the inbox as ``failed``
(``repository_unconfirmed``, its budget spent) by the database owner, in the seeding path the
integration tests use (decision 0008 section 5). Everything after that is the real system: the
operator CLI on its own login, the update guard, the build worker's processor and the registry.

Needs ``make proto-up proto-run``. Secrets come from the run script's files and are never printed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from tests.e2e._stack import (
    PROV,
    ROOT,
    Stack,
    docker_port,
    new_project,
    new_repository_id,
    read_env_file,
    until,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

RUN = ROOT / "scripts" / "prototype" / "run-provisioning.sh"


@dataclass(frozen=True)
class Run:
    code: int
    out: str
    err: str


async def operator(*args: str, as_worker: bool = False) -> Run:
    command = "operator-as-worker" if as_worker else "operator"
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(RUN),
        command,
        *args,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(120):
        out, err = await proc.communicate()
    assert proc.returncode is not None
    return Run(proc.returncode, out.decode(), err.decode())


async def park(project: uuid.UUID, request: uuid.UUID, repository_id: int, sha: str) -> None:
    """The failed row an outage of the registry leaves, written by the database owner."""
    prov = read_env_file(PROV / "provisioning.env")
    dsn = make_conninfo(
        host="127.0.0.1",
        port=docker_port("webblock-provisioning-db", 5432),
        dbname="provisioning",
        user="postgres",
        password=prov["PG_SUPERUSER_PASSWORD"],
        sslmode="disable",
        connect_timeout=10,
    )
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "INSERT INTO provisioning_ops.source_published_events (project_id,"
            " generation_request_id, repository_id, git_commit_sha, status, attempts,"
            " next_attempt_at, outcome_code, release_id, mismatched_repeat_count,"
            " last_mismatched_repeat_at, received_at, updated_at, finished_at, max_attempts,"
            " last_manual_retry_by, last_manual_retry_at) VALUES (%s, %s, %s, %s, 'failed', 10,"
            " now(), 'repository_unconfirmed', NULL, 0, NULL, now() - interval '1 hour', now(),"
            " now(), 10, NULL, NULL)",
            (project, request, repository_id, sha),
        )


async def test_a_parked_event_is_listed_redriven_and_its_release_accepted() -> None:
    stack = Stack()
    project, repository_id, request = new_project(), new_repository_id(), uuid.uuid7()
    name = f"e2e-op-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "v1")
    await park(project, request, repository_id, sha)

    listed = await operator("list", "--limit", "1000")
    assert listed.code == 0, listed.err
    lines = [line.split("\t") for line in listed.out.splitlines()]
    assert lines[0][:3] == ["state", "project_id", "generation_request_id"]
    mine = [line for line in lines[1:] if line[1] == str(project)]
    assert mine and mine[0][0] == "failed" and mine[0][2] == str(request), mine
    assert mine[0][5] == "repository_unconfirmed"

    # The worker's login is refused the re-drive by the database, and the row stays failed.
    refused = await operator(
        "redrive",
        "--project",
        str(project),
        "--generation-request",
        str(request),
        "--by",
        "mallory",
        as_worker=True,
    )
    assert refused.code == 1, (refused.out, refused.err)
    assert "permission_denied" in refused.err
    rows = await stack.query(
        "SELECT status::text FROM provisioning_ops.source_published_events"
        " WHERE project_id = %s AND generation_request_id = %s",
        project,
        request,
    )
    assert rows == [("failed",)]

    done = await operator(
        "redrive",
        "--project",
        str(project),
        "--generation-request",
        str(request),
        "--by",
        "operator-e2e",
    )
    assert done.code == 0, done.err
    assert done.out.startswith("reopened ") and "max_attempts=13" in done.out

    # The build worker's processor takes the row again; the registry confirms; the release exists.
    release = await stack.release_of(request, within=120)
    rows = await until(
        "the event accepted",
        lambda: stack.query(
            "SELECT status::text, release_id FROM provisioning_ops.source_published_events"
            " WHERE project_id = %s AND generation_request_id = %s",
            project,
            request,
        ),
        lambda r: bool(r) and r[0][0] == "accepted",
        60,
    )
    assert rows == [("accepted", release)]

    # One audit line per re-drive attempt, refused or done, with the operator as the actor.
    audit = [
        json.loads(line)
        for line in (PROV / "logs" / "latest" / "operator.log").read_text().splitlines()
        if line.startswith("{") and '"audit"' in line
    ]
    mine_audit = [a for a in audit if a.get("project_id") == str(project)]
    assert [(a["actor"], a["result"], a.get("failure_code")) for a in mine_audit] == [
        ("mallory", "refused", "permission_denied"),
        ("operator-e2e", "succeeded", None),
    ]
    assert all(a["action"] == "inbox_redrive" and a["audit"] is True for a in mine_audit)
