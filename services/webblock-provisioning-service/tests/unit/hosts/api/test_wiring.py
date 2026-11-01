"""The API process built from its configuration: both listeners served by uvicorn on real sockets,
the public one with every route behind a token and the operational one with /healthz and /readyz
only; a stop request ends both after the requests in progress finish; the process holds no cluster
credential. The PostgreSQL stores are memory doubles here.

Task H2: build_release only checks the pending build step and sends the hint (the API holds no
Jenkins or build setting); in the production profile a mutation over HTTP is refused with
``operation_unavailable`` (``OpenContract``, audited ``open_contract``) and project access denies
every read.

Task H3: production starts with the JWT identity, here against a local HTTPS issuer whose test CA
reaches the verifier through its constructor only; ``read_identity`` builds the verifier the
configuration names, and the development tokens only in the prototype."""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.infrastructure.config.test_api import JWT, PRODUCTION_ACTION_SCOPES
from tests.unit.infrastructure.identity.issuer import (
    ISSUER,
    FakeIssuer,
    claims,
    mint,
    rsa_key,
)
from tests.unit.presentation.apps import BEARER_ALL, dev_identity
from tests.unit.presentation.fakes import PROJECT, RELEASE
from tests.unit.presentation.memory import ALL_SCOPES, SCOPES, MemoryStores

from provisioning.core.application import ADMIN_ROLE, Action, BuildTarget, Deployment, NotFound
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import (
    ReleaseStatus,
    ReleaseStepName,
    new_release_step,
)
from provisioning.hosts.api.wiring import (
    PROTOTYPE_OPERATIONS,
    APIIdentity,
    APIStores,
    build_api,
    read_identity,
)
from provisioning.hosts.shared.stand_ins import StandinNotAvailable
from provisioning.infrastructure.config import APIConfig, api_keys, load_api
from provisioning.infrastructure.identity import JwtVerifier
from provisioning.infrastructure.standins.dev_tokens import PrototypeDevTokenIdentity
from provisioning.infrastructure.telemetry import LoggerAuditLog, new_logger
from provisioning.infrastructure.telemetry.metrics import Metrics
from provisioning.presentation.http import TokenVerifier
from provisioning.presentation.http.middleware import route_templates


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _env(public: int, ops: int) -> dict[str, str]:
    return {
        "PROVISIONING_PROFILE": "prototype",
        "COMMAND_DEADLINE": "15m",
        "HTTP_LISTEN_ADDR": f"127.0.0.1:{public}",
        "OPS_LISTEN_ADDR": f"127.0.0.1:{ops}",
        "SENTRY_DSN": "none",
        "HTTP_REQUEST_TIMEOUT": "10s",
        "HTTP_IDLE_TIMEOUT": "5s",
        "HTTP_SHUTDOWN_GRACE": "10s",
        "HTTP_MAX_HEADER_BYTES": "16384",
        "HTTP_MAX_REQUEST_BODY_BYTES": "1024",
        "HTTP_MAX_IN_FLIGHT": "16",
        "MCP_MAX_REQUEST_BODY_BYTES": "65536",
        "MCP_ALLOWED_ORIGINS": "none",
        "MCP_ALLOWED_HOSTS": "127.0.0.1:*",
        "IDENTITY_MODE": "dev-tokens",
        "AUTH_DEV_TOKENS_FILE": "/unused/tokens.json",
        "AUTH_ACTION_SCOPES": ",".join(f"{a.value}={s}" for a, s in SCOPES.items()),
        "PROJECT_ACCESS_MODE": "prototype-allow",
        "ENTITLEMENT_MODE": "prototype-allow",
        "PG_HOST": "localhost",
        "PG_PORT": "5432",
        "PG_DATABASE": "provisioning",
        "PG_SSLMODE": "disable",
        "PG_USER": "api_login",
        "PG_PASS": "unused",
        "DB_MAX_CONNS": "4",
        "DB_CONNECT_TIMEOUT": "5s",
        "DB_TX_TIMEOUT": "10s",
        "DB_STATEMENT_TIMEOUT": "5s",
        "DB_LOCK_TIMEOUT": "2s",
    }


STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")


class _BuildRequests:
    """The API's build request store: the release's pending build step, and the hints sent."""

    def __init__(self, stores: MemoryStores) -> None:
        self.stores = stores
        self.hints: list[tuple[UUID, UUID]] = []

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        release = self.stores.releases[release_id]
        step = new_release_step(step_id=STEP, release_id=release_id, name=ReleaseStepName.BUILD)
        return BuildTarget(release=release, step=step)

    async def build_wanted(self, project_id: UUID, release_id: UUID) -> None:
        self.hints.append((project_id, release_id))


class _GatedReads(MemoryStores):
    """Reads that wait for ``gate`` once ``armed``, to hold a request in progress."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.armed = False

    async def read_deployment(self, project_id: UUID) -> Deployment:
        if self.armed:
            self.entered.set()
            await self.gate.wait()
        return await super().read_deployment(project_id)


async def _until(probe: Callable[[], Awaitable[bool]], what: str) -> None:
    """Poll ``probe`` until it is true, failing after 10 seconds."""
    deadline = time.monotonic() + 10
    while not await probe():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting until {what}")
        await asyncio.sleep(0.02)


async def _answers(url: str) -> bool:
    try:
        async with httpx2.AsyncClient(timeout=1) as http:
            await http.get(url)
    except httpx2.TransportError:
        return False
    return True


async def _refuses(url: str) -> bool:
    return not await _answers(url)


def _build(
    stores: MemoryStores,
    public: int,
    ops: int,
    ready: Callable[[], Awaitable[None]],
    audit_lines: io.StringIO | None = None,
    *,
    config: APIConfig | None = None,
    builds: _BuildRequests | None = None,
    verifier: TokenVerifier | None = None,
    metrics: Metrics | None = None,
) -> Any:
    audit_lines = audit_lines if audit_lines is not None else io.StringIO()
    config = config if config is not None else load_api(_env(public, ops).get)
    return build_api(
        config,
        stores=APIStores(
            acceptance=stores,
            reads=stores,
            history=stores,
            build_requests=builds if builds is not None else _BuildRequests(stores),
            ready=ready,
        ),
        identity=APIIdentity(verifier=verifier if verifier is not None else dev_identity()),
        logger=new_logger(io.StringIO(), logging.INFO),
        audit=LoggerAuditLog(new_logger(audit_lines, logging.INFO)),
        metrics=metrics,
    )


_STAND_INS = ("AUTH_DEV_TOKENS_FILE", "PROJECT_ACCESS_MODE", "ENTITLEMENT_MODE")
KEY = rsa_key()


def _production_env(public: int, ops: int) -> dict[str, str]:
    """The production environment: no stand-in key, the JWT identity of decision 0024 on the fake
    issuer of the tests, the admin-only actions mapped to ADMIN (FR-50)."""
    env = {k: v for k, v in _env(public, ops).items() if k not in _STAND_INS}
    return (
        env
        | JWT
        | {
            "PROVISIONING_PROFILE": "production",
            "IDENTITY_MODE": "jwt",
            "AUTH_ACTION_SCOPES": PRODUCTION_ACTION_SCOPES,
            "AUTH_ISSUER": ISSUER,
        }
    )


class _Clock:
    """Wall time plus ``ahead`` seconds: moves the library's 30 s between key set fetches."""

    def __init__(self) -> None:
        self.ahead = 0.0

    def __call__(self) -> float:
        return time.time() + self.ahead


def _production_verifier(
    config: APIConfig, issuer: FakeIssuer, clock: _Clock | None = None
) -> JwtVerifier:
    """The verifier ``read_identity`` would build, on the fake issuer's transport (the library's
    test-only seam; no configuration key carries trust)."""
    assert config.identity.jwt is not None
    return JwtVerifier(
        config.identity.jwt,
        logger=new_logger(io.StringIO(), logging.INFO),
        clock=clock if clock is not None else time.time,
        transport=issuer.transport(),
    )


def _bearer(**changes: Any) -> dict[str, str]:
    """A token of an ADMIN holding every scope: only an ADMIN reaches a rollback's operation
    gate (FR-50)."""
    roles = [*ALL_SCOPES, ADMIN_ROLE]
    token = mint(KEY, claims(now=time.time(), roles=roles, **changes))
    return {"Authorization": f"Bearer {token}"}


async def _ready() -> None:
    return None


@pytest.mark.asyncio
async def test_the_process_serves_both_listeners_and_stops_on_request() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    public, ops = _free_port(), _free_port()
    api = _build(stores, public, ops, _ready)
    stop = asyncio.Event()
    base, ops_base = f"http://127.0.0.1:{public}", f"http://127.0.0.1:{ops}"
    async with asyncio.TaskGroup() as tg:
        serving = tg.create_task(api.serve(stop))
        await _until(lambda: _answers(ops_base + "/healthz"), "the operational listener answers")
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        auth = {"Authorization": f"Bearer {BEARER_ALL}"}
        async with httpx2.AsyncClient(timeout=5) as http:
            deployment = await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=auth)
            assert deployment.status_code == 200, deployment.text
            assert deployment.json()["data"]["hostingStatus"] == "online"
            assert "server" not in deployment.headers
            accepted = await http.post(
                f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
                headers={**auth, "Idempotency-Key": "k1"},
            )
            assert accepted.status_code == 202, accepted.text
            assert (await http.get(f"{base}/healthz", headers=auth)).status_code == 404
            assert (await http.get(f"{base}/healthz")).status_code == 401
            docs = await http.get(f"{base}/docs")  # the prototype profile serves Swagger UI
            assert docs.status_code == 200 and "swagger-ui-dist@" in docs.text
            contract = await http.get(f"{base}/openapi.yaml")
            assert contract.status_code == 200 and contract.content.startswith(b"openapi:")
            assert (await http.get(f"{ops_base}/readyz")).text == "ready\n"
            assert (await http.get(f"{ops_base}/v1/projects")).status_code == 404
            async with (
                httpx2.AsyncClient(headers=auth, timeout=5) as mcp_http,
                Client(
                    streamable_http_client(f"{base}/mcp", http_client=mcp_http), mode="legacy"
                ) as sdk,
            ):
                tools = (await sdk.list_tools()).tools
                assert [t.name for t in tools] == [a.value for a in Action]
                result = await sdk.call_tool("get_deployment", {"projectId": str(PROJECT)})
                assert not result.is_error
                assert (
                    result.structured_content
                    == (
                        await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=auth)
                    ).json()["data"]
                )
        stop.set()
    assert serving.done() and serving.exception() is None
    assert await _refuses(ops_base + "/healthz")
    assert await _refuses(base + "/v1")
    assert len(stores.commands) == 1


@pytest.mark.asyncio
async def test_a_stop_lets_the_request_in_progress_finish() -> None:
    stores = _GatedReads()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    public, ops = _free_port(), _free_port()
    api = _build(stores, public, ops, _ready)
    stop = asyncio.Event()
    base = f"http://127.0.0.1:{public}"
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        stores.armed = True
        async with httpx2.AsyncClient(timeout=10) as http:
            in_progress = tg.create_task(
                http.get(
                    f"{base}/v1/projects/{PROJECT}/deployment",
                    headers={"Authorization": f"Bearer {BEARER_ALL}"},
                )
            )
            await asyncio.wait_for(stores.entered.wait(), timeout=10)
            stop.set()
            await _until(lambda: _refuses(base + "/v1"), "the public listener stops accepting")
            stores.gate.set()
            response = await in_progress
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_readyz_reports_the_stores_readiness() -> None:
    async def not_ready() -> None:
        raise NotFound("database")

    public, ops = _free_port(), _free_port()
    api = _build(MemoryStores(), public, ops, not_ready)
    stop = asyncio.Event()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        url = f"http://127.0.0.1:{ops}/readyz"
        await _until(lambda: _answers(url), "the operational listener answers")
        async with httpx2.AsyncClient(timeout=5) as http:
            response = await http.get(url)
        stop.set()
    assert (response.status_code, response.text) == (503, "not ready\n")


def test_every_operation_is_enabled_in_the_prototype() -> None:
    # The four admin-only actions (0023 G13) included.
    assert tuple(PROTOTYPE_OPERATIONS) == tuple(Action)


def test_the_api_holds_no_cluster_broker_jenkins_or_build_setting() -> None:
    for key in api_keys():
        for word in (
            "KUBE",
            "CLUSTER",
            "RABBIT",
            "AMQP",
            "BROKER",
            "JENKINS",
            "BUILD",
            "REPOSITOR",
        ):
            assert word not in key, key


def test_building_the_api_loads_no_kubernetes_client() -> None:
    src = Path(__file__).resolve().parents[4] / "src"
    code = (
        "import sys\n"
        "import provisioning.hosts.api.wiring\n"
        "import provisioning.hosts.api.__main__\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('kubernetes', 'kubernetes_asyncio', 'aio_pika', 'aiormq')"
        " or m.startswith('provisioning.infrastructure.kubernetes'))\n"
        "print(json.dumps(bad))\n"
    )
    result = subprocess.run(  # noqa: S603  # fixed argument list: the test interpreter and code
        [sys.executable, "-c", "import json\n" + code],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


@pytest.mark.asyncio
async def test_build_release_sends_the_hint_and_starts_nothing_in_the_api() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.ACCEPTED)
    builds = _BuildRequests(stores)
    public, ops = _free_port(), _free_port()
    api = _build(stores, public, ops, _ready, builds=builds)
    stop = asyncio.Event()
    base = f"http://127.0.0.1:{public}"
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        async with httpx2.AsyncClient(
            timeout=5, headers={"Authorization": f"Bearer {BEARER_ALL}"}
        ) as http:
            response = await http.post(f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/build")
        stop.set()
    assert response.status_code == 202, response.text
    assert response.json()["data"]["releaseId"] == str(RELEASE)
    assert (
        response.json()["data"]["status"] == "accepted"
    )  # the build worker, not the API, starts it
    assert builds.hints == [(PROJECT, RELEASE)]


@pytest.mark.asyncio
async def test_in_production_every_mutation_is_refused_and_reads_are_denied(
    tmp_path: Path,
) -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    builds = _BuildRequests(stores)
    public, ops = _free_port(), _free_port()
    audit_lines = io.StringIO()
    issuer = FakeIssuer([KEY.jwk()])
    config = load_api(_production_env(public, ops).get)
    assert config.profile is Profile.PRODUCTION
    api = _build(
        stores,
        public,
        ops,
        _ready,
        audit_lines,
        config=config,
        builds=builds,
        verifier=_production_verifier(config, issuer),
    )
    stop = asyncio.Event()
    base = f"http://127.0.0.1:{public}"
    refusals: dict[str, httpx2.Response] = {}
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        async with httpx2.AsyncClient(timeout=5, headers=_bearer()) as http:
            for action in ("deploy", "promote", "rollback", "build"):
                refusals[action] = await http.post(
                    f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/{action}",
                    headers={"Idempotency-Key": "k1"} if action != "build" else {},
                )
            read = await http.get(f"{base}/v1/projects/{PROJECT}/deployment")
            unauthenticated = await http.get(
                f"{base}/v1/projects/{PROJECT}/deployment", headers={"Authorization": ""}
            )
            refused = await http.get(
                f"{base}/v1/projects/{PROJECT}/deployment",
                headers=_bearer(aud="another-service"),
            )
            docs = await http.get(f"{base}/docs", headers={"Authorization": ""})
            contract = await http.get(f"{base}/openapi.yaml", headers={"Authorization": ""})
            dev_token = await http.get(
                f"{base}/v1/projects/{PROJECT}/deployment",
                headers={"Authorization": f"Bearer {BEARER_ALL}"},
            )
            stop.set()
    for action, response in refusals.items():
        assert response.status_code == 503, (action, response.text)
        assert response.json()["code"] == "OPERATION_UNAVAILABLE", action
    assert read.status_code == 404  # project access is closed: denied, concealed as not found
    assert read.json()["code"] == "PROJECT_NOT_FOUND"
    for response in (unauthenticated, refused, dev_token, docs, contract):
        assert (response.status_code, response.json()["code"]) == (401, "UNAUTHENTICATED")
    assert stores.commands == []
    assert builds.hints == []
    records = [json.loads(line) for line in audit_lines.getvalue().splitlines()]
    assert sorted(r["action"] for r in records) == sorted(
        ["deploy", "promote", "rollback", "build_release"]
    )
    assert {(r["result"], r["failure_code"]) for r in records} == {("refused", "open_contract")}


@pytest.mark.asyncio
async def test_in_production_an_issuer_that_is_down_is_503_until_keys_arrive(
    tmp_path: Path,
) -> None:
    """The API starts with the JWKS endpoint down (nothing is fetched at start-up) and answers 503
    identity_unavailable, never 401, until a fetch succeeds."""
    stores = MemoryStores()
    public, ops = _free_port(), _free_port()
    issuer = FakeIssuer([KEY.jwk()], status=503)
    clock = _Clock()
    config = load_api(_production_env(public, ops).get)
    api = _build(
        stores,
        public,
        ops,
        _ready,
        config=config,
        verifier=_production_verifier(config, issuer, clock),
    )
    stop = asyncio.Event()
    base = f"http://127.0.0.1:{public}"
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        async with httpx2.AsyncClient(timeout=5, headers=_bearer()) as http:
            down = await http.get(f"{base}/v1/projects/{PROJECT}/deployment")
            issuer.status = 200
            clock.ahead = 31  # past the library's 30 s between fetches
            up = await http.get(f"{base}/v1/projects/{PROJECT}/deployment")
        stop.set()
    assert (down.status_code, down.json()["code"]) == (503, "IDENTITY_UNAVAILABLE")
    assert (up.status_code, up.json()["code"]) == (404, "PROJECT_NOT_FOUND")
    assert (issuer.discovery_hits, issuer.jwks_hits) == (2, 1)


def test_read_identity_builds_the_verifier_the_configuration_names(tmp_path: Path) -> None:
    logger = new_logger(io.StringIO(), logging.INFO)
    public, ops = _free_port(), _free_port()
    jwt_config = load_api(_production_env(public, ops).get)
    assert isinstance(read_identity(jwt_config, logger).verifier, JwtVerifier)
    prototype_jwt = dataclasses.replace(jwt_config, profile=Profile.PROTOTYPE)
    assert isinstance(read_identity(prototype_jwt, logger).verifier, JwtVerifier)
    tokens = tmp_path / "tokens.json"
    tokens.write_text(
        json.dumps({"tokens": [{"token": "t" * 40, "subject": "alice", "scopes": []}]})
    )
    dev = load_api((_env(public, ops) | {"AUTH_DEV_TOKENS_FILE": str(tokens)}).get)
    assert isinstance(read_identity(dev, logger).verifier, PrototypeDevTokenIdentity)
    # A production configuration that named the development tokens anyway (the loader refuses
    # it first) still gets no stand-in.
    forced = dataclasses.replace(dev, profile=Profile.PRODUCTION)
    with pytest.raises(StandinNotAvailable, match="IDENTITY_MODE"):
        read_identity(forced, logger)


@pytest.mark.asyncio
async def test_the_process_audits_mutations_and_serves_the_listings() -> None:
    # The wiring gives the service a real audit log (LoggerAuditLog), not none, and the history
    # store, so the listings and diagnostics answer (decision 0014 section 21 rulings 3, 6, 7).
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    public, ops = _free_port(), _free_port()
    audit_lines = io.StringIO()
    api = _build(stores, public, ops, _ready, audit_lines)
    stop = asyncio.Event()
    base = f"http://127.0.0.1:{public}"
    auth = {"Authorization": f"Bearer {BEARER_ALL}"}
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        async with httpx2.AsyncClient(timeout=5, headers=auth) as http:
            accepted = await http.post(
                f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
                headers={"Idempotency-Key": "k1"},
            )
            releases = await http.get(f"{base}/v1/projects/{PROJECT}/releases")
            commands = await http.get(f"{base}/v1/projects/{PROJECT}/commands")
            diagnostics = await http.get(
                f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/diagnostics"
            )
        stop.set()
    assert accepted.status_code == 202
    assert [r["releaseId"] for r in releases.json()["data"]] == [str(RELEASE)]
    assert [c["commandId"] for c in commands.json()["data"]] == [
        accepted.json()["data"]["commandId"]
    ]
    assert diagnostics.status_code == 200
    assert (
        diagnostics.json()["data"]["commands"][0]["commandId"]
        == accepted.json()["data"]["commandId"]
    )
    (record,) = [json.loads(line) for line in audit_lines.getvalue().splitlines()]
    assert record["audit"] is True
    assert (record["action"], record["result"], record["actor"]) == ("deploy", "accepted", "alice")
    assert BEARER_ALL not in audit_lines.getvalue()


@pytest.mark.asyncio
async def test_metrics_are_served_on_the_operational_listener_and_never_on_the_public_one() -> None:
    """Task H4: /metrics answers only on OPS_LISTEN_ADDR; on the public listener it is an unknown
    route behind the token (401 without one, 404 with one). Public requests are counted by route
    template, never by their raw path."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    public, ops = _free_port(), _free_port()
    metrics = Metrics(routes=route_templates())
    api = _build(stores, public, ops, _ready, metrics=metrics)
    stop = asyncio.Event()
    base, ops_base = f"http://127.0.0.1:{public}", f"http://127.0.0.1:{ops}"
    auth = {"Authorization": f"Bearer {BEARER_ALL}"}
    async with asyncio.TaskGroup() as tg:
        tg.create_task(api.serve(stop))
        await _until(lambda: _answers(ops_base + "/healthz"), "the operational listener answers")
        await _until(lambda: _answers(base + "/v1"), "the public listener answers")
        async with httpx2.AsyncClient(timeout=5) as http:
            assert (await http.get(f"{base}/metrics")).status_code == 401
            assert (await http.get(f"{base}/metrics", headers=auth)).status_code == 404
            deployment = await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=auth)
            assert deployment.status_code == 200
            scraped = await http.get(f"{ops_base}/metrics")
        stop.set()
    assert scraped.status_code == 200
    text = scraped.text
    assert (
        'provisioning_http_requests_total{method="GET",route="/v1/projects/{projectId}/'
        'deployment",status_class="2xx"} 1.0'
    ) in text
    assert 'route="other",status_class="4xx"}' in text
    assert str(PROJECT) not in text
