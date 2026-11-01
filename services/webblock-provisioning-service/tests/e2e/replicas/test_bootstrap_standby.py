"""The bootstrap is a singleton, enforced (task H5c; decision 0016 section 6 item 5).

A second bootstrap (``start-process bootstrap-2``, its env file written by the replica run) stands
by on the singleton lock: it logs the standby line, reports ``provisioning_bootstrap_standby`` 1
and runs no round. A live site's tenant namespace deleted while both run is rebuilt by the holder
alone: one credential rotation, and the Secret ``catalog-runtime`` logs in to the catalog (the
interleaving that two unlocked bootstraps could produce would leave a Secret whose password the role
does not have). The first bootstrap is then killed (SIGKILL); the second takes over within
``TAKEOVER_BOUND`` and rebuilds the namespace deleted once more, again with one rotation and a
Secret that logs in. At the end the first bootstrap is started again (it stands by) and the second
is stopped, so the first holds the lock again and the stack is as the suite found it.

Credentials are read with the project's pinned kubectl on the project-local kubeconfig only (the
context is checked before every call) and are never printed. Needs ``make proto-up
proto-run-replicas``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid

import httpx2
import psycopg
import pytest
from tests.e2e._stack import PROTO, PROV, Stack, Timer, read_env_file, until
from tests.e2e.faults._recovery_support import (
    LogMarks,
    kubectl,
    log_marks,
    namespace,
    sleep,
)
from tests.e2e.faults.test_hosting_deletion import live_site
from tests.e2e.replicas._replicas import alive, require_replicas

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

STANDBY = "provisioning_bootstrap_standby"
ROUNDS = 'provisioning_bootstrap_rounds_total{result="completed"}'
STANDBY_LINE = "bootstrap standby: another bootstrap holds the lock"
HELD_LINE = "bootstrap holds the singleton lock"
ROTATED = "catalog_credential_rotated"
TAKEOVER_BOUND = 15.0  # seconds: the lock poll is 1 s; the server ends a killed session at once
REBUILD_BOUND = 120.0  # seconds: 10 rounds of 1 s, one ensure and access_setup, and slack


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


def ops_of(name: str) -> str:
    return "http://" + read_env_file(PROV / "env" / f"{name}.env")["OPS_LISTEN_ADDR"]


async def metric(stack: Stack, name: str, sample: str) -> float | None:
    """One sample of ``name``'s /metrics, or None when it is not exposed (or nothing answers)."""
    async with stack.http() as http:
        try:
            r = await http.get(ops_of(name) + "/metrics")
        except httpx2.TransportError:  # nothing answers (the process is down or starting)
            return None
    if r.status_code != 200:
        return None
    for line in r.text.splitlines():
        key, _, value = line.rpartition(" ")
        if key == sample:
            return float(value)
    return None


def lines_of(marks: LogMarks, name: str) -> list[str]:
    """``logs/latest/<name>.log`` written after ``marks``."""
    path = PROV / "logs" / "latest" / f"{name}.log"
    if not path.is_file():
        return []
    with path.open("rb") as log:
        log.seek(marks.get(path, 0))
        return log.read().decode(errors="replace").splitlines()


def rotations(marks: LogMarks, name: str, project: uuid.UUID) -> int:
    count = 0
    for line in lines_of(marks, name):
        if '"audit"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and entry.get("audit") is True
            and entry.get("action") == ROTATED
            and entry.get("project_id") == str(project)
        ):
            assert entry.get("result") == "succeeded", entry
            count += 1
    return count


def exists(kind: str, name: str, ns: str | None = None) -> bool:
    where = ("-n", ns) if ns is not None else ()
    out = kubectl("get", kind, name, *where, "--ignore-not-found", "-o", "name")
    return bool(out.strip())


async def delete_and_wait_rebuilt(project: uuid.UUID) -> None:
    ns = namespace(project)
    await asyncio.to_thread(kubectl, "delete", "namespace", ns, "--wait=true", "--timeout=180s")

    async def rebuilt() -> bool:
        return await asyncio.to_thread(
            lambda: (
                exists("namespace", ns)
                and exists("rolebinding", "webblock-tenant-worker", ns)
                and exists("secret", "catalog-runtime", ns)
            )
        )

    await until(f"the tenant {ns} rebuilt", rebuilt, bool, REBUILD_BOUND, 1)


async def secret_logs_in(project: uuid.UUID) -> bool:
    """Whether the Secret ``catalog-runtime``'s login and password log in to the catalog (read with
    the project's kubectl; never printed)."""
    raw = await asyncio.to_thread(
        kubectl,
        "-n",
        namespace(project),
        "get",
        "secret",
        "catalog-runtime",
        "-o",
        "jsonpath={.data.PGUSER} {.data.PGPASSWORD}",
    )
    try:
        user_b64, password_b64 = raw.split()
        user = base64.b64decode(user_b64).decode()
        password = base64.b64decode(password_b64).decode()
    except ValueError:
        return False
    env = read_env_file(PROTO / "stack.env")
    try:
        conn = await psycopg.AsyncConnection.connect(
            host=env["PROTO_CATALOG_HOST_ON_HOST"],
            port=int(env["PROTO_CATALOG_PORT_ON_HOST"]),
            dbname=env["PROTO_CATALOG_DATABASE"],
            user=user,
            password=password,
            sslmode="disable",
            connect_timeout=10,
        )
    except psycopg.Error:
        return False
    async with conn:
        cur = await conn.execute("SELECT current_user")
        row = await cur.fetchone()
    return row is not None and row[0] == user


async def test_a_second_bootstrap_stands_by_and_takes_over_when_the_first_dies(
    stack: Stack,
) -> None:
    timer = Timer("h5c-bootstrap-standby")
    project, _ = await live_site(stack, "h5c-standby")
    assert await metric(stack, "bootstrap", STANDBY) == 0.0  # the one bootstrap holds the lock
    marks = log_marks("bootstrap")
    await stack.process("start-process", "bootstrap-2")
    try:
        # The standby: it says so, reports it, and runs no round.
        await until(
            "bootstrap-2 standing by",
            lambda: metric(stack, "bootstrap-2", STANDBY),
            lambda value: value == 1.0,
            60,
        )
        await sleep(5)  # five lock polls, and many rounds of the holder
        assert any(STANDBY_LINE in line for line in lines_of(marks, "bootstrap-2"))
        assert await metric(stack, "bootstrap-2", ROUNDS) is None
        assert await metric(stack, "bootstrap-2", STANDBY) == 1.0
        assert await metric(stack, "bootstrap", STANDBY) == 0.0

        # Both run; the holder alone rebuilds a deleted namespace: one rotation, a Secret that
        # logs in.
        await delete_and_wait_rebuilt(project)
        await until("the Secret logging in", lambda: secret_logs_in(project), bool, 60, 2)
        assert rotations(marks, "bootstrap", project) == 1
        assert rotations(marks, "bootstrap-2", project) == 0
        assert await metric(stack, "bootstrap-2", ROUNDS) is None

        # The holder dies; the standby takes over within the bound.
        timer.mark("killed")
        await stack.process("kill-process", "bootstrap")
        await until(
            "bootstrap-2 holding the lock",
            lambda: metric(stack, "bootstrap-2", STANDBY),
            lambda value: value == 0.0,
            TAKEOVER_BOUND,
            0.2,
        )
        timer.mark("took_over")
        assert any(HELD_LINE in line for line in lines_of(marks, "bootstrap-2"))
        await until(
            "bootstrap-2's first round",
            lambda: metric(stack, "bootstrap-2", ROUNDS),
            lambda value: value is not None and value >= 1,
            30,
        )

        # The new holder rebuilds the namespace deleted once more: again one rotation.
        await delete_and_wait_rebuilt(project)
        await until("the Secret logging in again", lambda: secret_logs_in(project), bool, 60, 2)
        assert rotations(marks, "bootstrap", project) == 1
        assert rotations(marks, "bootstrap-2", project) == 1
    finally:
        # Back as the suite found it: the first bootstrap holds the lock, no second one runs.
        await stack.process("start-process", "bootstrap")
        await stack.process("stop-process", "bootstrap-2")
    await until(
        "bootstrap holding the lock again",
        lambda: metric(stack, "bootstrap", STANDBY),
        lambda value: value == 0.0,
        TAKEOVER_BOUND + 30,
        0.5,
    )
    assert alive("bootstrap") and not alive("bootstrap-2")
    took = timer.between("killed", "took_over")
    timer.save({"takeover_seconds": took})
    assert took < TAKEOVER_BOUND, took
