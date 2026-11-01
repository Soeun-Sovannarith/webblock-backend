"""Helpers of the live recovery tests (task W6-B; decision 0014 section 21 rulings 8 and 9, section
23 item 3). This module holds no test; it needs ``make proto-up proto-run``.

- ``kubectl`` runs the project's pinned kubectl against ``bin/kind-state/proto/kubeconfig`` only,
  after checking that the context is ``<engine>-webblock-proto`` on ``https://127.0.0.1:<port>``
  (CLAUDE.md section 0, the owner's rule). It never reads another kubeconfig or the home
  directory's: ``HOME`` is the stack's own and ``--cache-dir`` is under ``bin/kind-state/``.
- ``Site`` is one project on the stack: build, deploy, promote and the operator rollback, which
  ``command`` sends with the ``prototype-admin`` token (rollback is ``ADMIN`` only, FR-50).
- ``as_superuser`` runs one statement on the stack's own Provisioning database
  (``webblock-provisioning-db``, never the owner's ``postgres``) as its superuser, to make a
  command's deadline pass without waiting for it. The password comes from the run script's file
  and is never printed.
- ``log_mark`` and ``worker_log_since`` read the first worker's JSON log of the current run, and
  ``audit_events`` its audit events (``audit=true``; Loki receives the same lines).
- Replicas (task H5c): ``log_marks`` and ``logs_since`` read every replica's log of a process
  (``logs/latest/worker.log`` and ``worker-2.log`` on a replica run), and ``kill_workers`` kills
  every worker replica through the run script's ``kill-process`` (crash semantics on the host and
  in the cluster; task PK). On the default stack they act on the one worker, as before.
- In cluster mode the logs are copied from the pods by the run script's follower;
  ``worker_log_since`` and ``logs_since`` first wait ``LOG_SETTLE`` seconds there.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg.conninfo import make_conninfo
from tests.e2e._stack import (
    ADMIN,
    PROTO,
    PROV,
    ROOT,
    RUN_SCRIPT,
    Stack,
    docker_port,
    new_project,
    new_repository_id,
    process_logs,
    read_env_file,
    replica_names,
    run_mode,
    until,
)
from tests.integration.kind_safety import expected_context

KUBECONFIG = PROTO / "kubeconfig"
KUBECTL = ROOT / "bin" / "kubectl"
CACHE = PROTO / "kubectl-cache"
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(PROTO / "home")}
WORKER_LOG = PROV / "logs" / "latest" / "worker.log"


def _kubectl_raw(args: list[str], timeout: int = 60) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603  # fixed argument list, no shell
        [str(KUBECTL), "--kubeconfig", str(KUBECONFIG), "--cache-dir", str(CACHE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=ENV,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


def check_context() -> None:
    """The owner's rule: only the project's Kind cluster, only on the loopback address."""
    rc, out = _kubectl_raw(["config", "view", "--minify", "-o", "json"])
    assert rc == 0, out[-500:]
    view = json.loads(out)
    assert view["current-context"] == expected_context(), view["current-context"]
    server = view["clusters"][0]["cluster"]["server"]
    assert server.startswith("https://127.0.0.1:"), server


def kubectl(*args: str, ok: bool = True) -> str:
    check_context()
    rc, out = _kubectl_raw(list(args))
    if ok:
        assert rc == 0, f"kubectl {' '.join(args)} exited {rc}: {out[-1500:]}"
    return out


def kube_json(*args: str) -> Any:
    return json.loads(kubectl(*args, "-o", "json"))


def namespace(project: uuid.UUID) -> str:
    return f"wb-{project}"


def workloads(project: uuid.UUID) -> set[str]:
    """The names of the Deployments in the project's tenant namespace."""
    listed = kube_json("-n", namespace(project), "get", "deployments")
    return {item["metadata"]["name"] for item in listed["items"]}


async def as_superuser(sql: LiteralString, *params: object) -> int:
    """One statement on the stack's Provisioning database as its superuser; the row count.

    Test-only fault injection (lead's ruling, wave 6): it stands in for the passing of time, such
    as a command deadline that would otherwise take fifteen minutes. It touches only the stack's
    own ``webblock-provisioning-db``, never the owner's ``postgres``, and never production code."""
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
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        # The guards of command_dispatch refuse any role but the worker's; the superuser seeds
        # around them as the integration tests do (decision 0008 section 5).
        await conn.execute("SET session_replication_role = replica")
        cur = await conn.execute(sql, params)
        return cur.rowcount


def log_mark() -> int:
    """The worker log's size now: ``worker_log_since`` reads what was written after it."""
    _settle()
    return WORKER_LOG.stat().st_size if WORKER_LOG.exists() else 0


def worker_log_since(mark: int) -> list[str]:
    """The worker's log lines written after ``mark`` (JSON lines, no secrets)."""
    _settle()
    with WORKER_LOG.open("rb") as log:
        log.seek(mark)
        return log.read().decode(errors="replace").splitlines()


type LogMarks = dict[Path, int]


def log_marks(process: str = "worker") -> LogMarks:
    """The size now of every replica's log of ``process``: ``logs_since`` reads what each one
    wrote after it. A replica log that appears later is read from its start."""
    _settle()
    return {log: log.stat().st_size for log in process_logs(process)}


def logs_since(marks: LogMarks, process: str = "worker") -> list[str]:
    """Every replica's log lines of ``process`` written after ``marks``, one file after the other
    (first replica first; lines are not merged by time across files)."""
    _settle()
    lines: list[str] = []
    for log in process_logs(process):
        with log.open("rb") as handle:
            handle.seek(marks.get(log, 0))
            lines += handle.read().decode(errors="replace").splitlines()
    return lines


def audit_events(lines: list[str]) -> list[dict[str, Any]]:
    """The audit events (``audit=true``) among ``lines``."""
    out: list[dict[str, Any]] = []
    for line in lines:
        if '"audit"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("audit") is True:
            out.append(entry)
    return out


LOG_SETTLE = 3.0
"""Seconds the harness waits in cluster mode before it reads a log: the follower's lag behind the
pods (scripts/prototype/follow_logs.py lists them every second)."""


def _settle() -> None:
    if run_mode() == "cluster":
        time.sleep(LOG_SETTLE)


def kill_worker() -> None:
    """SIGKILL the first worker at once, as a crash, and leave it down until start-process (the run
    script's kill-process: on the host the process; in the cluster the pod's process, frozen from
    its node, scaled to 0 and killed)."""
    _kill("worker")


def kill_workers() -> None:
    """kill_worker for every worker replica the run script started, one right after the other."""
    for name in replica_names("worker"):
        _kill(name)


def _kill(name: str) -> None:
    done = subprocess.run(  # noqa: S603  # fixed argument list
        ["/usr/bin/bash", str(RUN_SCRIPT), "kill-process", name],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, (
        f"kill-process {name} exited {done.returncode}: {done.stderr[-1500:]}"
    )


class Site:
    """One project on the stack, as Project and an operator drive it."""

    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.id = new_project()
        self.repository_id = new_repository_id()
        self.name = f"rec-{str(self.id)[-12:]}"
        stack.register(self.id, self.repository_id, self.name)

    async def build(self, version: str, variant: str = "site") -> uuid.UUID:
        """Commit the sample-site ``variant`` (``scripts/prototype/sample-repo.sh``), publish it
        and wait until its release is built."""
        sha = await self.stack.commit(self.name, variant, version)
        request = await self.stack.publish(self.id, self.repository_id, sha)
        release = await self.stack.release_of(request)
        body = await self.stack.wait_release(self.id, release, {"built", "failed"}, 400)
        assert body["status"] == "built", body
        return release

    async def command(self, release: uuid.UUID, action: str, within: float = 300) -> dict[str, Any]:
        """Send ``action`` and wait until its command has finished. A rollback goes with the
        ``ADMIN`` token (FR-50); the wait reads with the operator's."""
        command_id = await self.accept(release, action)
        return await self.stack.wait_command(self.id, command_id, within)

    async def accept(self, release: uuid.UUID, action: str) -> str:
        """Send ``action`` and return the accepted command's ID; a rollback goes with the
        ``ADMIN`` token (FR-50)."""
        subject = ADMIN if action == "rollback" else None
        cmd = await self.stack.post(self.id, release, action, uuid.uuid4().hex, subject=subject)
        assert cmd.status_code == 202, cmd.body
        return cmd.command_id

    async def live(self, version: str) -> uuid.UUID:
        release = await self.build(version)
        assert (await self.command(release, "deploy"))["status"] == "succeeded"
        done = await self.command(release, "promote")
        assert done["status"] == "succeeded", done
        return release

    async def routing(self) -> str:
        rows = await self.stack.query(
            "SELECT routing_status::text FROM provisioning.project_hosting WHERE project_id = %s",
            self.id,
        )
        return str(rows[0][0])

    async def wait_routing(self, wanted: str, within: float = 60, interval: float = 0.05) -> str:
        return await until(
            f"routing {wanted} of project {self.id}",
            self.routing,
            lambda value: value == wanted,
            within,
            interval,
        )

    async def answering(self) -> str | None:
        """The release that answers the site's hostname now (its header), or None."""
        status, names = await self.stack.visit_named(self.id, "/")
        return names[0] if status == 200 and names else None


async def eventually[T](what: str, probe: Any, done: Any, within: float = 120) -> T:
    """``until`` with a one-second pause, for cluster state the worker converges to."""
    result: T = await until(what, probe, done, within, 1.0)
    return result


def monotonic() -> float:
    return time.monotonic()


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)
