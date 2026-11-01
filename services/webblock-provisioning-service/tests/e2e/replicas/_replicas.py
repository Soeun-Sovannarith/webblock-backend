"""Helpers of the replica suite (task H5b, phase P-H; .claude/docs/runbooks/replicas.md). This
module holds no test. It needs ``make proto-up proto-run-replicas``: two API, worker and build
worker processes and one bootstrap. A missing replica fails the run, never skips it.

- ``require_replicas`` checks that the second replicas run and that no second bootstrap does (only
  ``test_bootstrap_standby`` starts one, and stops it).
- ``Logs`` reads each process's JSON log of the current run (``logs/latest/<name>.log``) from a
  mark, so a test sees only what happened after it started; ``audit`` keeps the ``audit=true``
  lines, ``claims`` the claim numbers a worker logged for one command.
- ``signal_process`` sends a signal to one replica by its pid file (SIGSTOP and SIGCONT for the
  stale-worker test).
- ``inject_command_jobs`` publishes extra copies of a command's job message to the command
  exchange, as an at-least-once broker may deliver them (a dispatcher republish, a redelivery):
  the identifiers come from the command row, the body from the published schema. A copy is never
  trusted by the worker beyond its identifiers, so this is what any duplicate looks like.
- ``site_objects`` and ``foreign_managers`` read the tenant's objects through the project's pinned
  kubectl on the project-local kubeconfig only (``tests.e2e.faults._recovery_support.kubectl``
  checks the context ``<engine>-webblock-proto`` on ``https://127.0.0.1:<port>`` before every call).
"""

from __future__ import annotations

import json
import os
import re
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aio_pika
from tests.e2e._stack import PROV, Stack, StackMissing, read_env_file, run_mode
from tests.e2e.faults._recovery_support import kube_json, namespace

LOGS = PROV / "logs" / "latest"
PIDS = PROV / "pids"
WORKERS = ("worker", "worker-2")
BUILD_WORKERS = ("build_worker", "build_worker-2")
APIS = ("api", "api-2")
WORKER_IDS = {"worker": "prototype-worker-1", "worker-2": "prototype-worker-2"}
WORKER_OF_ID = {worker_id: name for name, worker_id in WORKER_IDS.items()}
FENCE_ANNOTATION = "websites.webblock.internal/command-number"
WORKER_MANAGER = "webblock-provisioning-worker"
JOBS_EXCHANGE = "provisioning.jobs"
JOBS_ROUTING_KEY = "command_job.v1"
JOB_TYPE = "provisioning.command_job"
TERMINAL = {"succeeded", "failed", "skipped"}
# The audit actions that end a command; workload_cleanup also names the command that caused it.
COMMAND_ACTIONS = {"deploy", "promote", "rollback", "suspend_hosting", "resume_hosting"}


def pid_of(name: str) -> int:
    path = PIDS / f"{name}.pid"
    assert path.is_file(), f"{path} is missing; run make proto-run-replicas"
    return int(path.read_text().strip())


def alive(name: str) -> bool:
    path = PIDS / f"{name}.pid"
    if not path.is_file():
        return False
    try:
        os.kill(int(path.read_text().strip()), 0)
    except ProcessLookupError:
        return False
    return True


def require_replicas(stack: Stack) -> None:
    """Two APIs, two workers and two build workers run, and exactly one bootstrap."""
    if run_mode() == "cluster":
        raise StackMissing(
            "the replica suite runs on the host stack only (make proto-run-replicas); this stack "
            "runs the service in the cluster (decision 0016 section 9 ruling 1)"
        )
    assert len(stack.apis) == 2, (
        "the replica suite needs the second API (env/api-2.env); run make proto-run-replicas"
    )
    replicas = (PROV / "replicas").read_text().strip() if (PROV / "replicas").is_file() else "1"
    assert replicas == "2", f"the run recorded {replicas} replica(s); run make proto-run-replicas"
    for name in (*APIS, *WORKERS, *BUILD_WORKERS, "bootstrap"):
        assert alive(name), f"{name} is not running"
    # A second bootstrap stands by on the singleton lock (task H5c); only the standby test starts
    # it, and stops it again.
    assert not (PIDS / "bootstrap-2.pid").exists(), "bootstrap-2 runs only inside its own test"
    ids = {read_env_file(PROV / "env" / f"{w}.env")["WORKER_ID"] for w in WORKERS}
    assert ids == set(WORKER_OF_ID), ids


def signal_process(name: str, sig: signal.Signals) -> None:
    os.kill(pid_of(name), sig)


class Logs:
    """The processes' JSON logs from the moment this object was made."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self._marks = {name: _size(name) for name in names}

    def lines(self, name: str) -> list[str]:
        path = LOGS / f"{name}.log"
        with path.open("rb") as log:
            log.seek(self._marks[name])
            return log.read().decode(errors="replace").splitlines()

    def entries(self, name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in self.lines(name):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    def audit(self, name: str) -> list[dict[str, Any]]:
        return [e for e in self.entries(name) if e.get("audit") is True]

    def about(self, name: str, command_id: str) -> list[str]:
        """The messages that name ``command_id`` (``command_id=<id>`` in the message text)."""
        needle = f"command_id={command_id}"
        return [str(e.get("msg", "")) for e in self.entries(name) if needle in str(e.get("msg"))]

    def claims(self, name: str, command_id: str) -> set[int]:
        """The claim numbers ``name`` logged for ``command_id``: every line written under a claim
        carries ``claim_number=<n>``."""
        found: set[int] = set()
        for message in self.about(name, command_id):
            match = re.search(r"\bclaim_number=(\d+)\b", message)
            if match:
                found.add(int(match.group(1)))
        return found

    def ended(self, names: tuple[str, ...], command_id: str) -> list[tuple[str, dict[str, Any]]]:
        """The worker audit events that ended ``command_id``, with the log they are in."""
        return [
            (name, e)
            for name in names
            for e in self.audit(name)
            if e.get("command_id") == command_id
            and e.get("action") in COMMAND_ACTIONS
            and e.get("result") in TERMINAL
        ]


def _size(name: str) -> int:
    path = LOGS / f"{name}.log"
    return path.stat().st_size if path.exists() else 0


@dataclass(frozen=True)
class CommandRow:
    command_id: uuid.UUID
    project_id: uuid.UUID
    release_id: uuid.UUID | None
    kind: str
    command_number: int


async def command_row(stack: Stack, command_id: str) -> CommandRow:
    rows = await stack.query(
        "SELECT command_id, project_id, release_id, kind::text, command_number "
        "FROM provisioning.commands WHERE command_id = %s",
        uuid.UUID(command_id),
    )
    assert len(rows) == 1, rows
    c, p, r, k, n = rows[0]
    release = None if r is None else uuid.UUID(str(r))
    return CommandRow(uuid.UUID(str(c)), uuid.UUID(str(p)), release, str(k), int(n))


async def dispatch_row(stack: Stack, command_id: str) -> dict[str, Any]:
    """The command's dispatch row (``provisioning_ops.command_dispatch``), read as the worker."""
    rows = await stack.query_ops(
        "SELECT claim_number, execution_attempts, claimed_by_worker, finished_at IS NOT NULL "
        "FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        uuid.UUID(command_id),
    )
    assert len(rows) == 1, rows
    claim, attempts, by, finished = rows[0]
    return {"claim_number": claim, "attempts": attempts, "by": by, "finished": finished}


async def inject_command_jobs(stack: Stack, row: CommandRow, copies: int) -> None:
    """``copies`` command-job messages for ``row``, each with a fresh message ID (as every
    dispatcher publish has), published with confirms."""
    connection = await aio_pika.connect_robust(stack.amqp_url(), timeout=15)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange(JOBS_EXCHANGE, ensure=False)
        for _ in range(copies):
            message_id = str(uuid.uuid4())
            doc: dict[str, Any] = {
                "schema_version": 1,
                "message_id": message_id,
                "message_type": JOB_TYPE,
                "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "correlation_id": f"h5b-duplicate-{message_id}",
                "project_id": str(row.project_id),
                "command_id": str(row.command_id),
                "fencing_generation": row.command_number,
                "kind": row.kind,
            }
            if row.release_id is not None:
                doc["release_id"] = str(row.release_id)
            await exchange.publish(
                aio_pika.Message(
                    body=json.dumps(doc, separators=(",", ":")).encode(),
                    content_type="application/json",
                    type=JOB_TYPE,
                    message_id=message_id,
                    correlation_id=doc["correlation_id"],
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=JOBS_ROUTING_KEY,
                mandatory=True,
                timeout=15,
            )


def site_object(project: uuid.UUID, kind: str, name: str) -> dict[str, Any]:
    found: dict[str, Any] = kube_json(
        "-n", namespace(project), "get", kind, name, "--show-managed-fields"
    )
    return found


def fence_of(obj: dict[str, Any]) -> int:
    value = (obj["metadata"].get("annotations") or {}).get(FENCE_ANNOTATION)
    assert value is not None and str(value).isdigit(), (obj["metadata"]["name"], value)
    return int(value)


def foreign_managers(obj: dict[str, Any]) -> set[str]:
    """Field managers other than the worker's that own a field of the object itself: status
    subresource entries and entries owning only metadata annotations (the Deployment
    controller's revision) are left out, as the worker's own inventory does."""
    found: set[str] = set()
    for entry in obj["metadata"].get("managedFields") or []:
        manager = entry.get("manager")
        if manager == WORKER_MANAGER or entry.get("subresource"):
            continue
        fields = entry.get("fieldsV1") or {}
        if set(fields) == {"f:metadata"} and set(fields["f:metadata"]) == {"f:annotations"}:
            continue
        found.add(str(manager))
    return found


def apply_managers(obj: dict[str, Any]) -> set[str]:
    return {
        str(e.get("manager"))
        for e in obj["metadata"].get("managedFields") or []
        if e.get("operation") == "Apply"
    }


def single_writer(project: uuid.UUID, kind: str, name: str, fence: int) -> dict[str, Any]:
    """The object exists, only the worker's field manager applies it, no foreign manager owns a
    field of it, and its fence is ``fence``; returns it."""
    obj = site_object(project, kind, name)
    assert apply_managers(obj) == {WORKER_MANAGER}, (kind, name, obj["metadata"]["managedFields"])
    assert foreign_managers(obj) == set(), (kind, name, obj["metadata"]["managedFields"])
    assert fence_of(obj) == fence, (kind, name, fence_of(obj), fence)
    return obj
