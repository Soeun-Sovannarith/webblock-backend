"""The end-to-end harness (task G): drives the running prototype (``make proto-up proto-run``)
the way Project, Payment, an operator and a visitor would. This module holds no test.

- Project: a sample-site commit on the local Git server (``scripts/prototype/sample-repo.sh``),
  the repository registered in the prototype repository file, and "source published" through E2's
  simulator.
- An operator or agent: REST (``/v1``) and MCP (``/mcp``) with the development bearer token.
  Rollback is ``ADMIN`` only (FR-50): ``post`` and ``mcp`` take ``ADMIN``, the ``prototype-admin``
  subject, for it.
- Payment: "entitlement ended" and "entitlement restored" through the simulator.
- A visitor: the stack's Gateway with ``Host: <subdomain>.sites.webblock.test``; the Gateway names
  the release that answered in ``X-Webblock-Release`` (decision 0014 section 17). Over HTTPS
  the hostname is also the TLS server name, verified against the stack's local CA
  (``local-ca.crt``).
- Jenkins: the build worker's own settings, read only, to count the builds of a release.
- Replicas (task H5b): on a stack started with ``make proto-run-replicas`` the second API listens
  beside the first (``env/api-2.env``); ``Stack.apis`` lists both, and ``post`` and ``mcp`` take the
  API to call. On the default stack ``apis`` holds the one API and nothing else changes.
- Roles (task H5c): ``replica_names(process)`` is every replica of a process this run started (the
  count the run script's ``start`` recorded), ``Stack.process_all`` stops, kills or starts all of
  them, and ``process_logs(process)`` is every ``logs/latest/<process>[-N].log``. A test that needs
  no worker running stops every worker, not the first one only.
- The service's mode (task PK; decision 0016 section 9): the run script records ``host`` or
  ``cluster`` when it starts (``provisioning/mode``); ``run_mode()`` reads it, and nothing here
  chooses it. The API's address comes from ``endpoints.env`` in both modes, Jenkins' from the
  stack's ``jenkins.env`` and ``stack.env``. After a stand-in file is rewritten,
  ``sync_standins()`` lets the run script copy it into the cluster's ConfigMap and wait for the
  pods (it returns at once on the host).

Release IDs are created by the build worker, so the harness finds a release by its generation
request in the database, read only, as the API's login. Secrets come from the run script's files
and are never printed. Every wait has a deadline; a missing stack fails the run.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, LiteralString

import aio_pika
import httpx2
import mcp_types
import psycopg
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
PROTO = ROOT / "bin" / "kind-state" / "proto"
PROV = PROTO / "provisioning"
TIMINGS = PROV / "timings"
RELEASE_HEADER = "X-Webblock-Release"
SITES = ROOT / "tests" / "e2e" / "sites"
LOKI_QUERY = ROOT / "deploy" / "kind" / "proto-loki-query.sh"
DOCKER = "/usr/bin/docker"
# Task H5c: the replica count the run script's ``start`` recorded, and the processes it may run
# twice (``REPLICATED`` in ``scripts/prototype/run-provisioning.sh``).
REPLICAS_FILE = PROV / "replicas"
PROCESSES = frozenset({"api", "worker", "build_worker", "bootstrap"})
REPLICATED = frozenset({"api", "worker", "build_worker"})
MODE_FILE = PROV / "mode"
RUN_SCRIPT = ROOT / "scripts" / "prototype" / "run-provisioning.sh"
# FR-50: the development token holding the ADMIN role (``tokens.json``); rollback needs it.
ADMIN = "prototype-admin"
# FR-21, FR-22 (go-live plan Task 10): a promote expected to fail runs 3 tries with the 30 s and
# 60 s waits between them, so the test waits up to the command's whole budget, the prototype's
# COMMAND_DEADLINE (15m in scripts/prototype/run-provisioning.sh).
FAILED_PROMOTE_TIMEOUT = 900.0
# The pytest timeout of a test that runs one such promote besides its builds and deploys.
FAILED_PROMOTE_TEST_TIMEOUT = 1500


class StackMissing(RuntimeError):
    """The prototype stack or Provisioning is not running: the e2e suite fails, never skips."""


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise StackMissing(f"{path} is missing; run make proto-up proto-run")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[key] = value
    return values


def docker_port(container: str, port: int) -> int:
    done = subprocess.run(  # noqa: S603  # fixed argument list
        ["/usr/bin/docker", "port", container, f"{port}/tcp"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode != 0 or not done.stdout.strip():
        raise StackMissing(f"container {container} does not publish {port}; run make proto-run")
    return int(done.stdout.splitlines()[0].rsplit(":", 1)[1])


def docker(*args: str, ok: bool = True) -> str:
    """Runs the docker CLI (fixed argument list, no shell). Tests use it only on the stack's own
    containers (``webblock-*``), never on the owner's ``postgres`` and ``rabbitmq``."""
    for index, arg in enumerate(args):
        is_user = index > 0 and args[index - 1] == "-u"  # `exec -u rabbitmq` names a user
        owners = arg in {"postgres", "rabbitmq"} and not is_user
        assert not owners, "the owner's containers are never touched"
    done = subprocess.run(  # noqa: S603  # fixed argument list
        [DOCKER, *args], capture_output=True, text=True, timeout=120, check=False
    )
    if ok:
        assert done.returncode == 0, f"docker {args[0]} exited {done.returncode}: {done.stderr}"
    return done.stdout


def replica_count() -> int:
    """The replica count of this run (``provisioning/replicas``, written by ``start``); 1 when
    nothing was recorded, as the run script reads it."""
    if not REPLICAS_FILE.is_file():
        return 1
    text = REPLICAS_FILE.read_text().strip()
    if text not in {"1", "2"}:
        raise StackMissing(f"{REPLICAS_FILE} holds neither 1 nor 2")
    return int(text)


def replica_names(process: str) -> list[str]:
    """Every replica of ``process`` this run started, first replica first: ``["worker"]`` on the
    default stack, ``["worker", "worker-2"]`` on a replica run."""
    if process not in PROCESSES:
        raise ValueError(f"unknown process {process!r}")
    if process in REPLICATED and replica_count() == 2:
        return [process, f"{process}-2"]
    return [process]


def process_logs(process: str) -> list[Path]:
    """Every replica's log of ``process`` in the current run: ``logs/latest/<process>.log`` and
    ``<process>-<n>.log``, whichever exist (``worker`` never matches ``build_worker.log``)."""
    if process not in PROCESSES:
        raise ValueError(f"unknown process {process!r}")
    latest = PROV / "logs" / "latest"
    if not latest.is_dir():
        return []
    pattern = re.compile(re.escape(process) + r"(-[0-9]+)?\.log")
    return sorted(p for p in latest.iterdir() if pattern.fullmatch(p.name))


def run_mode() -> str:
    """``host`` (the four processes on the host; also when nothing is recorded) or ``cluster``
    (pods of the Helm chart; task PK), as the run script's ``start`` recorded it."""
    if not MODE_FILE.is_file():
        return "host"
    mode = MODE_FILE.read_text().strip()
    if mode not in {"host", "cluster"}:
        raise StackMissing(f"{MODE_FILE} holds neither host nor cluster")
    return mode


def sync_standins() -> None:
    """After a stand-in file was rewritten on the host: ``run-provisioning.sh standins-sync``,
    which in cluster mode returns once every pod that mounts ConfigMap provisioning-standins reads
    the new files (decision 0016 section 9 ruling 3), and on the host at once."""
    done = subprocess.run(  # noqa: S603  # fixed argument list
        ["/usr/bin/bash", str(RUN_SCRIPT), "standins-sync"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, f"standins-sync exited {done.returncode}: {done.stderr[-1500:]}"


async def until[T](
    what: str,
    probe: Callable[[], Awaitable[T]],
    done: Callable[[T], bool],
    within: float,
    interval: float = 1.0,
) -> T:
    """Poll ``probe`` until ``done`` holds; AssertionError naming ``what`` and the last value."""
    deadline = time.monotonic() + within
    last: T | None = None
    while True:
        last = await probe()
        if done(last):
            return last
        if time.monotonic() > deadline:
            raise AssertionError(
                f"timed out after {within:.0f}s waiting for {what}; last: {last!r}"
            )
        await asyncio.sleep(interval)


@dataclass
class Timer:
    """Named durations of one flow, written to bin/kind-state/proto/provisioning/timings/."""

    name: str
    marks: dict[str, float] = field(default_factory=dict)

    def mark(self, label: str) -> None:
        self.marks[label] = time.time()

    def between(self, a: str, b: str) -> float:
        return self.marks[b] - self.marks[a]

    def save(self, extra: dict[str, Any] | None = None) -> None:
        TIMINGS.mkdir(parents=True, exist_ok=True)
        (TIMINGS / f"{self.name}.json").write_text(
            json.dumps({"marks": self.marks, **(extra or {})}, indent=2)
        )


@dataclass
class ToolResult:
    """One MCP tool call: whether the result is an error, and its JSON body (for an error, the
    RFC 9457 Problem without instance; decision 0018)."""

    is_error: bool
    body: dict[str, Any]


class McpCaller:
    """The SDK client of one MCP session, reduced to ``call``."""

    def __init__(self, sdk: Client) -> None:
        self._sdk = sdk

    async def tool_names(self) -> list[str]:
        return [t.name for t in (await self._sdk.list_tools()).tools]

    async def call(self, tool: str, **arguments: Any) -> ToolResult:
        result = await self._sdk.call_tool(tool, arguments)
        body: dict[str, Any]
        if result.structured_content is not None:
            body = dict(result.structured_content)
        else:
            first = result.content[0]
            assert isinstance(first, mcp_types.TextContent), type(first)
            body = json.loads(first.text)
        return ToolResult(is_error=bool(result.is_error), body=body)


@dataclass
class Command:
    command_id: str
    status_code: int
    body: dict[str, Any]


class Stack:
    """The running prototype and Provisioning, as seen from the host."""

    def __init__(self) -> None:
        stack = read_env_file(PROTO / "stack.env")
        prov = read_env_file(PROV / "provisioning.env")
        self.gateway = f"http://{stack['PROTO_GATEWAY_ADDRESS']}"
        self.gateway_https = stack["PROTO_GATEWAY_HTTPS_URL"].rstrip("/")
        self.local_ca = PROTO / "local-ca.crt"
        self.domain = stack["PROTO_SITE_HOST_SUFFIX"]
        self.git_base = stack["PROTO_GIT_URL_BASE"]
        endpoints = read_env_file(PROV / "endpoints.env")
        self.api = endpoints["API_URL"].rstrip("/")
        # Task H5b: the second API of a replica run, listening beside the first.
        self.apis = [self.api]
        if "API2_URL" in endpoints:
            self.apis.append(endpoints["API2_URL"].rstrip("/"))
        self._token = prov["TOKEN_PASSWORD"]
        self._dsn = make_conninfo(
            host="127.0.0.1",
            port=docker_port("webblock-provisioning-db", 5432),
            dbname="provisioning",
            user="prov_api_login",
            password=prov["API_PASSWORD"],
            sslmode="disable",
            connect_timeout=10,
        )
        # The worker's login, for measurements on provisioning_ops only (read only).
        self._ops_dsn = make_conninfo(
            host="127.0.0.1",
            port=docker_port("webblock-provisioning-db", 5432),
            dbname="provisioning",
            user="prov_worker_login",
            password=prov["WORKER_PASSWORD"],
            sslmode="disable",
            connect_timeout=10,
        )
        jenkins = read_env_file(PROTO / "jenkins.env")
        self._jenkins = jenkins["JENKINS_URL"].rstrip("/") + "".join(
            f"/job/{segment}" for segment in stack["PROTO_JENKINS_JOB"].split("/")
        )
        self._jenkins_auth = (jenkins["JENKINS_USER"], jenkins["JENKINS_API_TOKEN"])
        mq_port = docker_port("webblock-rabbitmq", 5672)
        self._amqp = f"amqp://provisioning:{prov['MQ_PASSWORD']}@127.0.0.1:{mq_port}/"
        self.repositories = PROV / "repositories.json"

    def amqp_url(self) -> str:
        """The prototype broker's URL with its password (never printed)."""
        return self._amqp

    # --- the auth header (never printed) ---------------------------------------------------------

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def token_for(self, subject: str) -> str:
        """The development token of ``subject`` from the run script's file (never printed)."""
        tokens = json.loads((PROV / "tokens.json").read_text())["tokens"]
        for entry in tokens:
            if entry["subject"] == subject:
                return str(entry["token"])
        raise StackMissing(f"tokens.json has no token of {subject}")

    def auth_as(self, subject: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_for(subject)}"}

    @asynccontextmanager
    async def mcp(
        self, subject: str | None = None, api: str | None = None
    ) -> AsyncIterator[McpCaller]:
        """An MCP session on ``/mcp`` with the operator's token, or with ``subject``'s; on the
        first API, or on ``api`` (one of ``apis``)."""
        headers = self.auth() if subject is None else self.auth_as(subject)
        base = self.api if api is None else api
        async with (
            httpx2.AsyncClient(headers=headers, timeout=60, trust_env=False) as http,
            Client(streamable_http_client(f"{base}/mcp", http_client=http), mode="legacy") as sdk,
        ):
            yield McpCaller(sdk)

    def http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=30, trust_env=False, follow_redirects=False)

    # --- Project's side ---------------------------------------------------------------------------

    async def commit(self, name: str, variant: str, version: str) -> str:
        out = await run(
            ["bash", str(ROOT / "scripts" / "prototype" / "sample-repo.sh"), name, variant, version]
        )
        sha = out.strip().splitlines()[-1]
        assert len(sha) == 40, sha
        return sha

    def register(self, project: uuid.UUID, repository_id: int, name: str) -> None:
        """Adds the project's repository to the prototype repository file (atomic rewrite), then
        syncs it into the cluster in cluster mode."""
        doc = json.loads(self.repositories.read_text())
        entries = [e for e in doc["repositories"] if e["project_id"] != str(project)]
        entries.append(
            {
                "repository_id": repository_id,
                "project_id": str(project),
                # A DNS label unique to the project (decision 0021 D8); the site answers at
                # <subdomain>.<site domain> (decision 0023 G18).
                "subdomain": subdomain_of(project),
                "git_url": f"{self.git_base}/{name}.git",
            }
        )
        tmp = self.repositories.with_suffix(f".{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps({"repositories": entries}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.repositories)
        sync_standins()

    async def commit_dir(self, name: str, directory: Path, version: str) -> str:
        """A new commit in the bare repository ``name`` whose tree is ``directory`` (a sample
        site with a Dockerfile, such as ``tests/e2e/sites/qa-spoof``), ``@VERSION@`` replaced in
        index.html. The stock ``sample-repo.sh`` knows only its own variant names."""
        return await asyncio.to_thread(_commit_dir, name, directory, version)

    async def publish_raw(self, exchange: str, routing_key: str, body: bytes) -> None:
        """Publishes ``body`` as it is to ``exchange`` (confirmed, mandatory): for events that
        the simulator will not build, such as a malformed one."""
        connection = await aio_pika.connect_robust(self._amqp, timeout=15)
        async with connection:
            channel = await connection.channel(publisher_confirms=True)
            target = await channel.get_exchange(exchange, ensure=False)
            await target.publish(
                aio_pika.Message(body=body, content_type="application/json"),
                routing_key=routing_key,
                mandatory=True,
                timeout=15,
            )

    async def simulate(self, *args: str) -> str:
        """Runs E2's simulator; returns the printed event key."""
        out = await run(
            [sys.executable, "-m", "provisioning.hosts.devtools.simulate", *args],
            env={"WEBBLOCK_SIMULATOR_AMQP_URL": self._amqp},
        )
        line = out.strip().splitlines()[-1]
        assert line.startswith("event_key="), line
        return line.removeprefix("event_key=")

    async def publish(
        self, project: uuid.UUID, repository_id: int, sha: str, request: uuid.UUID | None = None
    ) -> uuid.UUID:
        args = [
            "source-published",
            "--project",
            str(project),
            "--repository-id",
            str(repository_id),
        ]
        args += ["--commit", sha]
        if request is not None:
            args += ["--generation-request", str(request)]
        return uuid.UUID(await self.simulate(*args))

    # --- a visitor over HTTPS ---------------------------------------------------------------------

    async def visit_https(
        self, hostname: str, path: str = "/", headers: dict[str, str] | None = None
    ) -> httpx2.Response | None:
        """One HTTPS request to the Gateway for ``hostname`` (TLS server name and Host),
        verified against the local CA only; None when nothing answers."""
        tls = ssl.create_default_context(cafile=str(self.local_ca))
        async with httpx2.AsyncClient(
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            verify=tls,
            limits=httpx2.Limits(max_keepalive_connections=0),
        ) as http:
            try:
                return await http.get(
                    self.gateway_https + path,
                    headers={"Host": hostname, **(headers or {})},
                    extensions={"sni_hostname": hostname},
                )
            except httpx2.TransportError:
                return None

    # --- database reads (read only, as the API's login) -------------------------------------------

    async def query(self, sql: LiteralString, *params: object) -> list[tuple[Any, ...]]:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            await conn.set_read_only(True)
            cur = await conn.execute(sql, params)
            return list(await cur.fetchall())

    async def notify(self, channel: LiteralString, payload: str) -> None:
        """``pg_notify(channel, payload)`` as the API's login (any login may notify; task H5b
        sends a malformed build hint to see which build workers listen)."""
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute("SELECT pg_notify(%s, %s)", (channel, payload))

    async def query_ops(self, sql: LiteralString, *params: object) -> list[tuple[Any, ...]]:
        async with await psycopg.AsyncConnection.connect(self._ops_dsn) as conn:
            await conn.set_read_only(True)
            cur = await conn.execute(sql, params)
            return list(await cur.fetchall())

    async def admin_query(self, sql: LiteralString, *params: object) -> list[tuple[Any, ...]]:
        """A read-only query as the stack's own database superuser (test-only; the stack's
        ``webblock-provisioning-db``, never the owner's ``postgres``), for tables the API's login
        cannot read, such as the inbox's columns."""
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
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            await conn.set_read_only(True)
            cur = await conn.execute(sql, params)
            return list(await cur.fetchall())

    async def audit(self, project: uuid.UUID) -> list[dict[str, Any]]:
        """The project's audit events in Loki (all processes), oldest first."""
        query = (
            '{job="webblock-provisioning"} | json | audit="true" | project_id="'
            + str(project)
            + '"'
        )
        out = await run(["bash", str(LOKI_QUERY), "--since", "1h", "--limit", "500", query])
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    async def release_of(self, request: uuid.UUID, within: float = 120) -> uuid.UUID:
        rows = await until(
            f"the release of generation request {request}",
            lambda: self.query(
                "SELECT release_id FROM provisioning.releases WHERE generation_request_id = %s",
                request,
            ),
            lambda r: len(r) > 0,
            within,
        )
        assert len(rows) == 1, rows
        return uuid.UUID(str(rows[0][0]))

    # --- REST -------------------------------------------------------------------------------------

    async def envelope(self, path: str) -> dict[str, Any]:
        """The whole 200 success body of ``GET path`` (decision 0023 G2): success, data, meta."""
        async with self.http() as http:
            r = await http.get(self.api + path, headers=self.auth())
        assert r.status_code == 200, (path, r.status_code, r.text)
        assert r.headers["content-type"] == "application/json", r.headers
        body: dict[str, Any] = r.json()
        assert body["success"] is True and body["meta"]["status"] == 200, body
        assert body["meta"]["path"] == path.split("?", 1)[0], body
        return body

    async def get(self, path: str) -> dict[str, Any]:
        """The ``data`` object of ``GET path``."""
        body = await self.envelope(path)
        assert body["meta"]["page"] is None, body
        data: dict[str, Any] = body["data"]
        return data

    async def page(self, path: str) -> dict[str, Any]:
        """One page of a list as MCP returns it (decision 0023 G7): ``{"items": <data>, "page":
        <meta.page>}``."""
        body = await self.envelope(path)
        assert isinstance(body["data"], list) and body["meta"]["page"] is not None, body
        return {"items": body["data"], "page": body["meta"]["page"]}

    async def release(self, project: uuid.UUID, release: uuid.UUID) -> dict[str, Any]:
        return await self.get(f"/v1/projects/{project}/releases/{release}")

    async def deployment(self, project: uuid.UUID) -> dict[str, Any]:
        return await self.get(f"/v1/projects/{project}/deployment")

    async def command(self, project: uuid.UUID, command_id: str) -> dict[str, Any]:
        return await self.get(f"/v1/projects/{project}/commands/{command_id}")

    async def post(
        self,
        project: uuid.UUID,
        release: uuid.UUID,
        action: str,
        key: str | None,
        api: str | None = None,
        subject: str | None = None,
    ) -> Command:
        """``POST .../releases/<release>/<action>`` on the first API, or on ``api``; with the
        operator's token, or with ``subject``'s (``ADMIN`` for a rollback, FR-50)."""
        headers = self.auth() if subject is None else self.auth_as(subject)
        if key is not None:
            headers["Idempotency-Key"] = key
        base = self.api if api is None else api
        async with self.http() as http:
            r = await http.post(
                f"{base}/v1/projects/{project}/releases/{release}/{action}", headers=headers
            )
        answer: dict[str, Any] = r.json() if r.content else {}
        # A success is the envelope; its data is the accepted command (or, for build, the
        # release). An error is the Problem, kept whole.
        body: dict[str, Any] = answer["data"] if answer.get("success") is True else answer
        return Command(
            command_id=str(body.get("commandId", "")), status_code=r.status_code, body=body
        )

    async def wait_release(
        self, project: uuid.UUID, release: uuid.UUID, statuses: set[str], within: float
    ) -> dict[str, Any]:
        return await until(
            f"release {release} in {sorted(statuses)}",
            lambda: self.release(project, release),
            lambda b: b["status"] in statuses,
            within,
        )

    async def wait_command(
        self, project: uuid.UUID, command_id: str, within: float
    ) -> dict[str, Any]:
        return await until(
            f"command {command_id} finished",
            lambda: self.command(project, command_id),
            lambda b: b["status"] in {"succeeded", "failed", "skipped"},
            within,
        )

    # --- the visitor ------------------------------------------------------------------------------

    def host(self, project: uuid.UUID) -> str:
        """The site's hostname ``<subdomain>.<site domain>`` (decision 0023 G18)."""
        return f"{subdomain_of(project)}.{self.domain}"

    async def visit(self, project: uuid.UUID, path: str = "/") -> tuple[int, str]:
        async with self.http() as http:
            try:
                r = await http.get(self.gateway + path, headers={"Host": self.host(project)})
            except httpx2.TransportError as exc:
                return 0, type(exc).__name__
        return r.status_code, r.text

    async def visit_when_available(
        self, project: uuid.UUID, path: str = "/", within: float = 60
    ) -> tuple[int, str]:
        """``visit``, repeated every 2 s for at most ``within`` seconds while the site answers 503.

        The sample shop answers 503 (never 200) while it cannot read its catalog, which can happen
        briefly after a promotion on a busy node. Any other status, and a 503 that lasts the whole
        time, is returned as it is, so the caller's assertion shows the status and the body."""
        deadline = time.monotonic() + within
        while True:
            status, body = await self.visit(project, path)
            if status != 503 or time.monotonic() >= deadline:
                return status, body
            await asyncio.sleep(2)

    async def visit_named(self, project: uuid.UUID, path: str = "/") -> tuple[int, list[str]]:
        """The status and the release header values of one visit; (0, []) when nothing answers."""
        async with self.http() as http:
            try:
                r = await http.get(self.gateway + path, headers={"Host": self.host(project)})
            except httpx2.TransportError:
                return 0, []
        return r.status_code, r.headers.get_list(RELEASE_HEADER)

    # --- Jenkins (read only) ---------------------------------------------------------------------

    async def jenkins_builds(self, release: uuid.UUID) -> int:
        """How many builds of the job carry ``RELEASE_ID=<release>``, queued ones included."""
        tree = "builds[number,actions[parameters[name,value]]]{0,200}"
        async with self.http() as http:
            r = await http.get(
                f"{self._jenkins}/api/json", params={"tree": tree}, auth=self._jenkins_auth
            )
            q = await http.get(
                f"{self._jenkins.split('/job/')[0]}/queue/api/json",
                params={"tree": "items[task[url],actions[parameters[name,value]]]"},
                auth=self._jenkins_auth,
            )
        assert r.status_code == 200, ("jenkins builds", r.status_code)
        assert q.status_code == 200, ("jenkins queue", q.status_code)

        def names_release(entry: dict[str, Any]) -> bool:
            return any(
                p.get("name") == "RELEASE_ID" and p.get("value") == str(release)
                for action in entry.get("actions") or []
                if isinstance(action, dict)
                for p in action.get("parameters") or []
            )

        builds = [b for b in r.json().get("builds") or [] if names_release(b)]
        queued = [i for i in q.json().get("items") or [] if names_release(i)]
        return len(builds) + len(queued)

    # --- the run script ---------------------------------------------------------------------------

    async def process(self, action: str, name: str) -> None:
        """``action`` on one process ``name`` (``worker``, or a replica such as ``worker-2``)."""
        await run(["bash", str(RUN_SCRIPT), action, name])

    async def process_all(self, action: str, process: str) -> None:
        """``action`` on every replica of ``process`` (task H5c). ``stop-process`` and
        ``kill-process`` act on all of them at once, so no replica keeps working while another
        stops; ``start-process`` starts them in order. On the default stack this is ``process``."""
        names = replica_names(process)
        if action == "start-process":
            for name in names:
                await self.process(action, name)
            return
        if action not in {"stop-process", "kill-process"}:
            raise ValueError(f"unknown action {action!r}")
        await asyncio.gather(*(self.process(action, name) for name in names))


def _commit_dir(name: str, directory: Path, version: str) -> str:
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "HOME": str(PROTO / "home"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    prefix = [
        "git",
        "-c",
        "user.name=WebBlock e2e",
        "-c",
        "user.email=e2e@webblock.test",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "init.defaultBranch=main",
    ]

    def g(*args: str, cwd: Path | None = None) -> str:
        done = subprocess.run(  # noqa: S603  # fixed argument list, no shell
            [*prefix, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        return done.stdout.strip()

    root = PROTO / "git"
    root.mkdir(exist_ok=True)
    bare = root / f"{name}.git"
    if not bare.is_dir():
        g("init", "--quiet", "--bare", str(bare))
    assert (directory / "Dockerfile").is_file(), directory
    work = Path(tempfile.mkdtemp(prefix=".e2e-work.", dir=PROTO))
    try:
        repo = work / "repo"
        g("clone", "--quiet", str(bare), str(repo))
        g("rm", "-rq", "--ignore-unmatch", "--", ".", cwd=repo)
        shutil.copytree(directory, repo, dirs_exist_ok=True)
        index = repo / "index.html"
        if index.is_file():
            index.write_text(index.read_text().replace("@VERSION@", version))
        g("add", "-A", cwd=repo)
        g("commit", "--quiet", "--allow-empty", "-m", f"e2e {directory.name} {version}", cwd=repo)
        g("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=repo)
        g("--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")
        g("--git-dir", str(bare), "update-server-info")
        sha = g("rev-parse", "HEAD", cwd=repo)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    assert len(sha) == 40, sha
    return sha


async def run(argv: list[str], env: dict[str, str] | None = None) -> str:
    """Runs a command without a shell; its standard output, or AssertionError with its errors."""
    full = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(PROTO / "home")}
    full |= env or {}
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=ROOT,
        env=full,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(300):
        out, err = await proc.communicate()
    if proc.returncode != 0:
        what = argv[1] if len(argv) > 1 else argv[0]
        raise AssertionError(f"{what} exited {proc.returncode}: {err.decode()[-2000:]}")
    return out.decode()


def new_project() -> uuid.UUID:
    return uuid.uuid7()


def subdomain_of(project: uuid.UUID) -> str:
    """The sub-domain ``register`` gives a test project; its hosting row copies it."""
    return f"site-{project.hex}"


def new_repository_id() -> int:
    return 100_000 + secrets.randbelow(900_000_000)
