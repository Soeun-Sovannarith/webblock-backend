"""The API host with PROJECT_ACCESS_MODE=project-service (task PA; decision 0017 section 2): the
composition root builds the Project service's adapter, hands it to the application service and
closes its client on the way out. Served over real sockets against the local stub of the Project
service (``tests/unit/infrastructure/project/stub.py``).

No token (the owner's authentication design, A3, approved 18 October 2026): no ``Authorization``
header leaves the process for the Project service, whatever bearer the caller sent; inside the
mesh the caller is its mTLS identity.

Operators. The operator rollback of QA-07 is ``rollback_release`` through the same gate as every
caller, and the operator CLI (inbox and dead-letter re-drive) never reaches the gate. Rollback is
``ADMIN`` only: a token without the ``ADMIN`` role is refused (403) before any call, the
owner's included. A token whose roles hold ``ADMIN`` rolls back any project without the Project
service being asked (decision 0019 section 7, OD-45); its other actions are asked about like every
caller's."""

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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import httpx2
import pytest
from tests.unit.infrastructure.config.test_api import ENV, PRODUCTION
from tests.unit.infrastructure.project.stub import (
    AccessCheckServer,
    access_check_server,
    allowed,
    denied,
)
from tests.unit.presentation.fakes import PROJECT, RELEASE
from tests.unit.presentation.memory import MemoryStores

from provisioning.core.application import (
    Action,
    BuildTarget,
    Principal,
    ProjectAccessUnavailable,
)
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import ReleaseStatus, RoutingStatus
from provisioning.hosts.api.wiring import (
    APIIdentity,
    APIStores,
    build_api,
    project_access,
)
from provisioning.hosts.shared.runtime import InvalidSettings
from provisioning.infrastructure.config import APIConfig, load_api
from provisioning.infrastructure.config.api import ProjectServiceConfig
from provisioning.infrastructure.standins.dev_tokens import PrototypeDevTokenIdentity
from provisioning.infrastructure.telemetry import LoggerAuditLog, new_logger

OWNER_BEARER = "dev-token-owner-0123456789abcdefghijklmnopqrs"
OPERATOR_BEARER = "dev-token-operator-0123456789abcdefghijklmnop"
ADMIN_BEARER = "dev-token-admin-0123456789abcdefghijklmnopqrs"
USER_BEARER = "dev-token-user-0123456789abcdefghijklmnopqrst"
LOWER_ADMIN_BEARER = "dev-token-lower-admin-0123456789abcdefghijklm"
USER = "0199e7a0-0000-7000-8000-0000000000aa"
ADMIN_USER = "0199e7a0-0000-7000-8000-0000000000bb"
ALL_SCOPES = tuple(f"prov.{a.value}" for a in Action)  # test_api.ENV's AUTH_ACTION_SCOPES
# The production roles of decision 0019 section 3 (.claude/docs/runbooks/identity.md): ADMIN for the
# admin-only actions, rollback included (FR-50), USER for the others.
ROLE_SCOPES = ",".join(f"{a.value}={'ADMIN' if a.is_admin_only() else 'USER'}" for a in Action)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def _env(
    public: int, ops: int, *, production: bool = False, action_scopes: str | None = None
) -> dict[str, str]:
    """test_api's prototype environment, or its production one (the JWT identity, no stand-in
    key), on the given ports; ``action_scopes`` replaces its AUTH_ACTION_SCOPES."""
    base = PRODUCTION if production else ENV
    env = base | {"HTTP_LISTEN_ADDR": f"127.0.0.1:{public}", "OPS_LISTEN_ADDR": f"127.0.0.1:{ops}"}
    if action_scopes is not None:
        env["AUTH_ACTION_SCOPES"] = action_scopes
    return env


async def _ready() -> None:
    return None


async def _until_answers(url: str) -> None:
    """Poll ``url`` until something answers, failing after 10 seconds."""
    deadline = time.monotonic() + 10
    while True:
        try:
            async with httpx2.AsyncClient(timeout=1) as http:
                await http.get(url)
            return
        except httpx2.TransportError:
            if time.monotonic() > deadline:
                raise AssertionError(f"nothing answers at {url}") from None
            await asyncio.sleep(0.02)


class _NoBuilds:
    """build_release is not exercised here: any use is a test failure."""

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        raise AssertionError("build_release is not part of these tests")

    async def build_wanted(self, project_id: UUID, release_id: UUID) -> None:
        raise AssertionError("build_release is not part of these tests")


def _identity() -> PrototypeDevTokenIdentity:
    return PrototypeDevTokenIdentity.from_json(_identity_file().encode())


def _identity_file() -> str:
    return json.dumps(
        {
            "tokens": [
                {
                    "token": OWNER_BEARER,
                    "subject": "prototype-tenant",
                    "scopes": list(ALL_SCOPES),
                    "user_id": USER,
                },
                {
                    "token": OPERATOR_BEARER,
                    "subject": "prototype-operator",
                    "scopes": list(ALL_SCOPES),
                },
                {
                    "token": USER_BEARER,
                    "subject": "platform-user",
                    "scopes": ["USER"],
                    "user_id": USER,
                },
                {
                    "token": ADMIN_BEARER,
                    "subject": "platform-admin",
                    "scopes": [*ALL_SCOPES, "USER", "ADMIN"],
                    "user_id": ADMIN_USER,
                },
                {
                    "token": LOWER_ADMIN_BEARER,
                    "subject": "platform-lower-admin",
                    "scopes": [*ALL_SCOPES, "USER", "admin"],
                    "user_id": ADMIN_USER,
                },
            ]
        }
    )


def _config(
    server: AccessCheckServer,
    public: int,
    ops: int,
    *,
    production: bool = False,
    action_scopes: str | None = None,
) -> APIConfig:
    """The configuration with project-service mode against ``server``. Production refuses a
    loopback URL (finding M-2), so its configuration is loaded with an in-cluster Service URL and
    then pointed at the local stub; only the URL differs from what the loader accepted."""
    url = "http://project.project.svc.cluster.local:8080" if production else server.url()
    env = _env(public, ops, production=production, action_scopes=action_scopes) | {
        "PROJECT_ACCESS_MODE": "project-service",
        "PROJECT_SERVICE_URL": url,
    }
    config = load_api(env.get)
    if production:
        config = dataclasses.replace(config, project_service=ProjectServiceConfig(url=server.url()))
    return config


@asynccontextmanager
async def _serving(
    *, production: bool = False, action_scopes: str | None = None
) -> AsyncIterator[tuple[AccessCheckServer, str, MemoryStores]]:
    """The API host served on real sockets. The identity is always the development tokens (the
    production configuration's JWT verifier would need an issuer); everything else is built from
    the configuration of the profile."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    public, ops = _free_port(), _free_port()
    async with access_check_server() as server:
        config = _config(server, public, ops, production=production, action_scopes=action_scopes)
        logger = new_logger(io.StringIO(), logging.INFO)
        async with project_access(config, logger) as access:
            api = build_api(
                config,
                stores=APIStores(
                    acceptance=stores,
                    reads=stores,
                    history=stores,
                    build_requests=_NoBuilds(),
                    ready=_ready,
                ),
                identity=APIIdentity(verifier=_identity()),
                logger=logger,
                audit=LoggerAuditLog(new_logger(io.StringIO(), logging.INFO)),
                access=access,
            )
            stop = asyncio.Event()
            base = f"http://127.0.0.1:{public}"
            async with asyncio.TaskGroup() as tg:
                tg.create_task(api.serve(stop))
                await _until_answers(base + "/v1")
                try:
                    yield server, base, stores
                finally:
                    stop.set()


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


@pytest.mark.asyncio
async def test_every_operation_asks_the_project_service_without_any_token() -> None:
    async with _serving() as (server, base, stores), httpx2.AsyncClient(timeout=10) as http:
        deployment = f"{base}/v1/projects/{PROJECT}/deployment"
        ok = await http.get(deployment, headers=_auth(OWNER_BEARER))
        assert ok.status_code == 200, ok.text
        accepted = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={**_auth(OWNER_BEARER), "Idempotency-Key": "k1"},
        )
        assert accepted.status_code == 202, accepted.text

        server.body = denied("NOT_MANAGER")
        refused = await http.get(deployment, headers=_auth(OWNER_BEARER))
        assert refused.status_code == 404, refused.text

        server.body = denied("PROJECT_SUSPENDED")
        suspended = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={**_auth(OWNER_BEARER), "Idempotency-Key": "k2"},
        )
        assert suspended.status_code == 404, suspended.text

        server.body, server.status = allowed(), 503
        down = await http.get(deployment, headers=_auth(OWNER_BEARER))
        assert down.status_code == 503, down.text
        assert down.json()["code"] == "PROJECT_ACCESS_UNAVAILABLE"
    sent = server.requests
    assert [r.json()["action"] for r in sent] == [
        "get_deployment",
        "deploy_release",
        "get_deployment",
        "deploy_release",
        "get_deployment",
    ]
    for request in sent:
        # The caller's bearer reached Provisioning; no Authorization header reaches the stub.
        assert "authorization" not in request.headers
        assert OWNER_BEARER not in json.dumps(request.headers) + request.body.decode()
        assert request.json()["userId"] == USER
        assert request.json()["projectId"] == str(PROJECT)
    # The request ID is the operation's correlation ID: one per call.
    assert len({r.headers["x-request-id"] for r in sent}) == len(sent)
    assert len(stores.commands) == 1


# What the adapter sends, and nothing else: its own headers, the inner-call identity headers of
# FR-8 (``x-request-id`` and ``x-user-id``; ``traceparent`` only with tracing on, off here) and the
# HTTP client's framing.
SENT_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
        "x-request-id",
        "x-user-id",
    }
)
CALLER_MARK = "CALLER-MARKER-9f3a"


REQUEST_ID = "0192f4a8-9d01-7c44-8e2b-11aa22bb33cc"


@pytest.mark.asyncio
async def test_no_header_of_the_caller_reaches_the_project_service() -> None:
    """Finding M-4 of the PR-3 review: the caller's bearer, cookies, proxy credentials and
    forwarding headers stop at Provisioning. The Project service sees exactly the adapter's own
    header set, on a read and on a mutation, and the caller's values nowhere. FR-8.1: the user
    goes as ``x-user-id``, taken from the verified principal, never from a caller's header."""
    inbound = {
        **_auth(OWNER_BEARER),
        "Cookie": f"session={CALLER_MARK}; JSESSIONID={CALLER_MARK}",
        "Proxy-Authorization": f"Basic {CALLER_MARK}",
        "X-Forwarded-For": "203.0.113.7",
        "X-Forwarded-Host": f"{CALLER_MARK}.example.com",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Client-Cert": f"Hash={CALLER_MARK}",
        "X-User-Id": CALLER_MARK,
        "Traceparent": f"00-{'1' * 32}-{'2' * 16}-01",
        # The one header that is passed on by design (spec E8): a safe request ID.
        "X-Request-Id": REQUEST_ID,
    }
    async with _serving() as (server, base, _), httpx2.AsyncClient(timeout=10) as http:
        read = await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=inbound)
        assert read.status_code == 200, read.text
        deploy = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={**inbound, "Idempotency-Key": "k-headers"},
        )
        assert deploy.status_code == 202, deploy.text
    assert len(server.requests) == 2
    for request in server.requests:
        assert set(request.headers) == SENT_HEADERS
        assert request.headers["x-request-id"] == REQUEST_ID
        assert request.headers["x-user-id"] == USER == json.loads(request.body)["userId"]
        recorded = json.dumps(request.headers) + request.body.decode()
        for value in (CALLER_MARK, OWNER_BEARER, "203.0.113.7", "1" * 32):
            assert value not in recorded


@pytest.mark.asyncio
async def test_an_operators_rollback_is_decided_without_asking_the_project_service() -> None:
    """The QA-07 operator rollback over REST, and every other action of an operator's token:
    without ADMIN the rollback is 403 (FR-50.2); with no user ID a read is denied (404) before
    any call. Nothing reaches the Project service."""
    async with _serving() as (server, base, _), httpx2.AsyncClient(timeout=10) as http:
        rollback = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/rollback",
            headers={**_auth(OPERATOR_BEARER), "Idempotency-Key": "op-1"},
        )
        assert rollback.status_code == 403, rollback.text
        assert rollback.json()["code"] == "ACCESS_DENIED"
        read = await http.get(
            f"{base}/v1/projects/{PROJECT}/deployment", headers=_auth(OPERATOR_BEARER)
        )
        assert read.status_code == 404, read.text
    assert server.requests == []


def _uncertain_route(stores: MemoryStores) -> None:
    """The state a rollback is accepted in: the route uncertain, the release still promoting."""
    stores.releases[RELEASE] = replace(stores.releases[RELEASE], status=ReleaseStatus.PROMOTING)
    stores.hosting[PROJECT] = replace(
        stores.hosting[PROJECT], routing_status=RoutingStatus.UNCERTAIN
    )


def _rollback_url(base: str) -> str:
    return f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/rollback"


@pytest.mark.asyncio
@pytest.mark.parametrize("scopes", [None, ROLE_SCOPES], ids=["prototype scopes", "role scopes"])
async def test_an_admin_rolls_back_a_project_they_do_not_own_without_asking(
    scopes: str | None,
) -> None:
    """FR-50.1 (decision 0019 section 7, OD-45): a token whose roles hold ADMIN rolls back a
    project the Project service would deny it (NOT_MANAGER): 202, the Project service receives
    nothing, and the command records the admin's subject."""
    async with (
        _serving(action_scopes=scopes) as (server, base, stores),
        httpx2.AsyncClient(timeout=10) as http,
    ):
        _uncertain_route(stores)
        server.body = denied("NOT_MANAGER")
        accepted = await http.post(
            _rollback_url(base), headers={**_auth(ADMIN_BEARER), "Idempotency-Key": "adm-1"}
        )
        assert accepted.status_code == 202, accepted.text
    assert server.requests == []
    [command] = stores.commands
    assert command.requested_by_subject == "platform-admin"
    assert str(command.id) == accepted.json()["data"]["commandId"]


@pytest.mark.asyncio
async def test_the_status_url_of_an_admins_rollback_is_concealed_from_a_non_owner() -> None:
    """Review finding M-4, documented and unchanged: the reads keep the owner check, so for an
    ADMIN who does not own the project the 202's status URL (get_command) and get_deployment
    answer 404 after the Project service's NOT_MANAGER."""
    async with _serving() as (server, base, stores), httpx2.AsyncClient(timeout=10) as http:
        _uncertain_route(stores)
        server.body = denied("NOT_MANAGER")
        admin = _auth(ADMIN_BEARER)
        accepted = await http.post(
            _rollback_url(base), headers={**admin, "Idempotency-Key": "adm-url"}
        )
        assert accepted.status_code == 202, accepted.text
        status = await http.get(base + accepted.json()["data"]["statusUrl"], headers=admin)
        assert status.status_code == 404, status.text
        deployment = await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=admin)
        assert deployment.status_code == 404, deployment.text
    assert [r.json()["action"] for r in server.requests] == ["get_command", "get_deployment"]
    assert len(stores.commands) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [True, False], ids=["owner", "not the owner"])
async def test_fr50_2_a_token_without_admin_may_not_roll_back(owner: bool) -> None:
    """FR-50.2: a token without ADMIN that holds the rollback permission (the prototype's
    scopes) is refused with 403 ACCESS_DENIED, the owner too: the Project service is not asked
    and nothing is stored."""
    async with _serving() as (server, base, stores), httpx2.AsyncClient(timeout=10) as http:
        _uncertain_route(stores)
        if not owner:
            server.body = denied("NOT_MANAGER")
        refused = await http.post(
            _rollback_url(base), headers={**_auth(OWNER_BEARER), "Idempotency-Key": "usr-1"}
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCESS_DENIED"
    assert server.requests == []
    assert stores.commands == []


@pytest.mark.asyncio
async def test_a_user_role_may_not_roll_back_with_the_role_scopes() -> None:
    """FR-50.2: with the production roles, USER does not hold rollback_release: refused (403)
    before the project is asked. The same token's read is asked about, so the role scopes are in
    force."""
    async with (
        _serving(action_scopes=ROLE_SCOPES) as (server, base, stores),
        httpx2.AsyncClient(timeout=10) as http,
    ):
        _uncertain_route(stores)
        read = await http.get(
            f"{base}/v1/projects/{PROJECT}/deployment", headers=_auth(USER_BEARER)
        )
        assert read.status_code == 200, read.text
        refused = await http.post(
            _rollback_url(base), headers={**_auth(USER_BEARER), "Idempotency-Key": "usr-2"}
        )
        assert refused.status_code == 403, refused.text
    assert [r.json()["action"] for r in server.requests] == ["get_deployment"]
    assert stores.commands == []


@pytest.mark.asyncio
async def test_a_lower_case_admin_role_does_not_count() -> None:
    """FR-50.2: role names are case-sensitive (A5): ``admin`` is not ADMIN, so the rollback is
    403 ACCESS_DENIED and the Project service is not asked."""
    async with _serving() as (server, base, stores), httpx2.AsyncClient(timeout=10) as http:
        _uncertain_route(stores)
        server.body = denied("NOT_MANAGER")
        refused = await http.post(
            _rollback_url(base),
            headers={**_auth(LOWER_ADMIN_BEARER), "Idempotency-Key": "low-1"},
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["code"] == "ACCESS_DENIED"
    assert server.requests == []
    assert stores.commands == []


@pytest.mark.asyncio
async def test_an_admins_other_actions_keep_the_owner_check() -> None:
    """Only the admin-only actions skip the check: an ADMIN's reads and deploy are asked about,
    and the Project service's NOT_MANAGER conceals the project from them."""
    async with (
        _serving(action_scopes=ROLE_SCOPES) as (server, base, stores),
        httpx2.AsyncClient(timeout=10) as http,
    ):
        server.body = denied("NOT_MANAGER")
        admin = _auth(ADMIN_BEARER)
        read = await http.get(f"{base}/v1/projects/{PROJECT}/deployment", headers=admin)
        assert read.status_code == 404, read.text
        release = await http.get(f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}", headers=admin)
        assert release.status_code == 404, release.text
        deploy = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={**admin, "Idempotency-Key": "adm-2"},
        )
        assert deploy.status_code == 404, deploy.text
    assert [r.json()["action"] for r in server.requests] == [
        "get_deployment",
        "get_release",
        "deploy_release",
    ]
    for request in server.requests:
        assert request.json()["userId"] == ADMIN_USER
    assert stores.commands == []


@pytest.mark.asyncio
async def test_production_still_refuses_an_admins_rollback() -> None:
    """Production refuses every mutation (``hosts.shared.open_contracts``): an ADMIN's rollback is
    503 before the skip, the Project service is not asked and no command is accepted.

    FR-6.2."""
    async with (
        _serving(production=True) as (server, base, stores),
        httpx2.AsyncClient(timeout=10) as http,
    ):
        _uncertain_route(stores)
        refused = await http.post(
            _rollback_url(base), headers={**_auth(ADMIN_BEARER), "Idempotency-Key": "adm-3"}
        )
        assert refused.status_code == 503, refused.text
        assert refused.json()["code"] == "OPERATION_UNAVAILABLE"
    assert server.requests == []
    assert stores.commands == []


def test_the_operator_cli_never_loads_the_project_service_adapter() -> None:
    """The operator CLI (inbox and dead-letter re-drive, decision 0014 section 21 ruling 11)
    works on its own database login and never passes the operation gate: it does not even load
    the adapter or the API host."""
    probe = (
        "import sys\n"
        "import provisioning.hosts.operator.__main__\n"
        "import provisioning.hosts.operator.cli\n"
        "import provisioning.hosts.operator.dlq_cli\n"
        "loaded = [m for m in sys.modules if m.startswith(("
        "'provisioning.infrastructure.project', 'provisioning.hosts.api'))]\n"
        "assert loaded == [], loaded\n"
    )
    result = subprocess.run(  # noqa: S603  # fixed argument list: the interpreter and a probe
        [sys.executable, "-I", "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# --- the composition root ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_adapters_client_is_closed_on_the_way_out() -> None:
    owner = Principal(subject="prototype-tenant", scopes=ALL_SCOPES, user_id=UUID(USER))
    async with access_check_server() as server:
        config = _config(server, _free_port(), _free_port())
        async with project_access(config, new_logger(io.StringIO(), logging.INFO)) as access:
            await access.check_access(owner, PROJECT, Action.GET_RELEASE)
        with pytest.raises(ProjectAccessUnavailable):
            await access.check_access(owner, PROJECT, Action.GET_RELEASE)
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_the_stand_in_modes_build_the_stand_in() -> None:
    config = load_api(_env(_free_port(), _free_port()).get)
    owner = Principal(subject="alice", scopes=ALL_SCOPES)
    async with project_access(config, new_logger(io.StringIO(), logging.INFO)) as access:
        await access.check_access(owner, PROJECT, Action.DEPLOY_RELEASE)  # prototype-allow


@pytest.mark.asyncio
async def test_production_builds_the_adapter_and_sends_no_token() -> None:
    """Authentication design A3 (owner, 18 October 2026): production asks the Project service.
    A read is decided by it; a mutation is still refused before any call while production
    enables none (``hosts.shared.open_contracts``).

    FR-6.1, FR-6.2."""
    async with (
        _serving(production=True) as (server, base, stores),
        httpx2.AsyncClient(timeout=10) as http,
    ):
        deployment = f"{base}/v1/projects/{PROJECT}/deployment"
        ok = await http.get(deployment, headers=_auth(OWNER_BEARER))
        assert ok.status_code == 200, ok.text
        server.body = denied("NOT_MANAGER")
        refused = await http.get(deployment, headers=_auth(OWNER_BEARER))
        assert refused.status_code == 404, refused.text
        mutation = await http.post(
            f"{base}/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={**_auth(OWNER_BEARER), "Idempotency-Key": "k1"},
        )
        assert mutation.status_code == 503, mutation.text
        assert mutation.json()["code"] == "OPERATION_UNAVAILABLE"
    assert [r.json()["action"] for r in server.requests] == ["get_deployment", "get_deployment"]
    for request in server.requests:
        assert "authorization" not in request.headers
        assert request.json()["userId"] == USER
    assert stores.commands == []


@pytest.mark.asyncio
async def test_production_without_the_mode_keeps_the_closed_adapter() -> None:
    config = load_api(_env(_free_port(), _free_port(), production=True).get)
    owner = Principal(subject="alice", scopes=ALL_SCOPES, user_id=UUID(USER))
    async with project_access(config, new_logger(io.StringIO(), logging.INFO)) as access:
        with pytest.raises(Exception) as err:
            await access.check_access(owner, PROJECT, Action.GET_RELEASE)
    assert type(err.value).__name__ == "ProjectAccessClosed"


@pytest.mark.asyncio
async def test_a_production_configuration_forced_to_the_mode_builds_the_adapter() -> None:
    """The profile no longer decides: the mode does (``_uses_project_service``)."""
    async with access_check_server() as server:
        config = _config(server, _free_port(), _free_port())
        forced = dataclasses.replace(config, profile=Profile.PRODUCTION)
        owner = Principal(subject="alice", scopes=ALL_SCOPES, user_id=UUID(USER))
        async with project_access(forced, new_logger(io.StringIO(), logging.INFO)) as access:
            await access.check_access(owner, PROJECT, Action.GET_RELEASE)
    [request] = server.requests
    assert "authorization" not in request.headers


def test_build_api_refuses_the_project_service_mode_without_the_adapter() -> None:
    server = AccessCheckServer(port=1)
    config = _config(server, _free_port(), _free_port())
    stores = MemoryStores()
    with pytest.raises(InvalidSettings, match="PROJECT_ACCESS_MODE"):
        build_api(
            config,
            stores=APIStores(
                acceptance=stores,
                reads=stores,
                history=stores,
                build_requests=_NoBuilds(),
                ready=_ready,
            ),
            identity=APIIdentity(verifier=_identity()),
            logger=new_logger(io.StringIO(), logging.INFO),
            audit=LoggerAuditLog(new_logger(io.StringIO(), logging.INFO)),
        )


def test_the_process_exits_2_when_a_service_token_file_is_still_set(tmp_path: Path) -> None:
    """A stale environment from before the authentication design names the removed key: the
    process refuses it by name at start-up, never printing its path, instead of ignoring it."""
    tokens = tmp_path / "tokens.json"
    tokens.write_text(_identity_file())
    env = _env(_free_port(), _free_port()) | {
        "AUTH_DEV_TOKENS_FILE": str(tokens),
        "PROJECT_ACCESS_MODE": "project-service",
        "PROJECT_SERVICE_URL": "http://127.0.0.1:1",
        "PROJECT_SERVICE_TOKEN_FILE": str(tmp_path / "project-service-token"),
        "PG_PORT": str(_free_port()),
    }
    result = subprocess.run(  # fixed argument list: the test interpreter and the module
        [sys.executable, "-m", "provisioning.hosts.api"],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    assert "PROJECT_SERVICE_TOKEN_FILE" in result.stderr
    assert str(tmp_path) not in result.stderr
