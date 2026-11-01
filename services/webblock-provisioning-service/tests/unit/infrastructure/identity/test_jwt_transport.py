"""The JWT verifier behind both transports (decision 0006 sections 3 to 5, decision 0024): the
public app's one authentication middleware wraps /v1/ and /mcp, so a refused token is 401 on both,
keys that cannot be had are 503 identity_unavailable on both, a valid token without the action's
role is 403 ACCESS_DENIED on both, through the real application service and the action policy, and
a valid token without a user ID is 403 ACCESS_DENIED before anything else. Roles are
compared whole: a role that only contains the required one, or is contained in it, is not the role.
"""

from __future__ import annotations

import io
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.infrastructure.identity.issuer import (
    AUDIENCE,
    ISSUER,
    FakeIssuer,
    claims,
    mint,
    rsa_key,
)
from tests.unit.presentation.apps import BASE, Built, build
from tests.unit.presentation.fakes import PROJECT, RELEASE
from tests.unit.presentation.memory import ALL_SCOPES, SCOPES, MemoryStores, memory_operations

from provisioning.core.application import ADMIN_ROLE, Action
from provisioning.core.domain import ReleaseStatus
from provisioning.infrastructure.config.identity import JWTConfig
from provisioning.infrastructure.identity import JwtVerifier
from provisioning.infrastructure.telemetry import new_logger

KEY = rsa_key()
DEPLOY = f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy"
DEPLOYMENT = f"/v1/projects/{PROJECT}/deployment"
DEPLOY_SCOPE = SCOPES[Action.DEPLOY_RELEASE]
READ_SCOPE = SCOPES[Action.GET_DEPLOYMENT]


def _verifier(issuer: FakeIssuer) -> JwtVerifier:
    return JwtVerifier(
        JWTConfig(issuer=ISSUER, audience=AUDIENCE, max_token_lifetime=timedelta(minutes=15)),
        logger=new_logger(io.StringIO(), logging.INFO),
        transport=issuer.transport(),
    )


def _token(roles: Any, **changes: Any) -> str:
    return mint(KEY, claims(now=time.time(), roles=roles, **changes))


@asynccontextmanager
async def _app(tmp_path: Path) -> AsyncIterator[tuple[Built, MemoryStores, FakeIssuer]]:
    del tmp_path
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    issuer = FakeIssuer(keys=[KEY.jwk()])
    built = build(memory_operations(stores), verifier=_verifier(issuer))
    async with built.app.router.lifespan_context(built.app):
        yield built, stores, issuer


@asynccontextmanager
async def _http(built: Built, token: str | None) -> AsyncIterator[httpx2.AsyncClient]:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=built.app), base_url=BASE, headers=headers
    ) as http:
        yield http


async def _tool(built: Built, token: str, name: str, arguments: dict[str, str]) -> Any:
    async with (
        _http(built, token) as http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as sdk,
    ):
        return await sdk.call_tool(name, arguments)


DEPLOY_ARGS = {"projectId": str(PROJECT), "releaseId": str(RELEASE), "idempotencyKey": "k1"}


def _code(response: httpx2.Response) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


@pytest.mark.asyncio
async def test_a_valid_token_with_the_scope_is_served_on_both_transports(tmp_path: Path) -> None:
    """FR-1.1: a valid token with the scope is served on both transports."""
    async with _app(tmp_path) as (built, stores, _):
        token = _token([READ_SCOPE, DEPLOY_SCOPE])
        async with _http(built, token) as http:
            assert (await http.get(DEPLOYMENT)).status_code == 200
        result = await _tool(built, token, "deploy_release", DEPLOY_ARGS)
        assert not result.is_error, result.structured_content
        async with _http(built, token) as http:
            replay = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
        assert replay.status_code == 202, replay.text
    assert [c.requested_by_subject for c in stores.commands] == ["agent:alice"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [
        [READ_SCOPE],
        [DEPLOY_SCOPE + "2"],
        ["x" + DEPLOY_SCOPE],
        [DEPLOY_SCOPE[:-1]],
        ["prov"],
        [f"{READ_SCOPE},{DEPLOY_SCOPE}"],
        [f"{READ_SCOPE} {DEPLOY_SCOPE}"],
        f"{READ_SCOPE} {DEPLOY_SCOPE}",  # a string, not a list: no roles
        [READ_SCOPE, DEPLOY_SCOPE + "x", "x" + DEPLOY_SCOPE],
        None,
    ],
)
async def test_a_token_without_the_actions_role_is_403_on_both_transports(
    tmp_path: Path, roles: Any
) -> None:
    async with _app(tmp_path) as (built, stores, _):
        token = _token(roles)
        async with _http(built, token) as http:
            response = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
        result = await _tool(built, token, "deploy_release", DEPLOY_ARGS)
    assert _code(response) == (403, "ACCESS_DENIED")
    assert result.is_error
    rest_problem = response.json()
    assert rest_problem.pop("instance") == DEPLOY
    assert result.structured_content == rest_problem
    assert stores.commands == []


@pytest.mark.asyncio
async def test_a_refused_token_is_401_on_both_transports(tmp_path: Path) -> None:
    """FR-1.2: a refused token is 401 on both transports."""
    async with _app(tmp_path) as (built, stores, _):
        for token in (
            _token(DEPLOY_SCOPE, iss=ISSUER + "x"),
            _token(DEPLOY_SCOPE, exp=int(time.time()) - 3600, iat=int(time.time()) - 3700),
            mint(rsa_key(seed="forger"), claims(now=time.time(), roles=[DEPLOY_SCOPE])),
        ):
            async with _http(built, token) as http:
                rest = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
                mcp = await http.post(
                    "/mcp",
                    content=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                )
            assert _code(rest) == (401, "UNAUTHENTICATED")
            assert _code(mcp) == (401, "UNAUTHENTICATED")
            # The same Problem but for the path each request named (decision 0018).
            assert (rest.json().pop("instance"), mcp.json().pop("instance")) == (DEPLOY, "/mcp")
            assert {**rest.json(), "instance": "/mcp"} == mcp.json()
            assert rest.headers["www-authenticate"] == "Bearer"
    assert stores.commands == []


@pytest.mark.asyncio
async def test_keys_that_cannot_be_had_are_503_on_both_transports(tmp_path: Path) -> None:
    """FR-1.3: keys that cannot be had are 503 on both transports."""
    async with _app(tmp_path) as (built, stores, issuer):
        issuer.status = 503
        token = _token(DEPLOY_SCOPE)
        async with _http(built, token) as http:
            rest = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
            mcp = await http.post(
                "/mcp",
                content=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
    assert _code(rest) == (503, "IDENTITY_UNAVAILABLE")
    assert _code(mcp) == (503, "IDENTITY_UNAVAILABLE")
    assert stores.commands == []


USER_ID = "0199e7a0-0000-7000-8000-0000000000aa"
SUSPEND = f"/v1/admin/projects/{PROJECT}/hosting/suspend"
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class _CountingAccess:
    """The Project access check, counting its calls; it allows."""

    def __init__(self) -> None:
        self.calls = 0

    async def check_access(self, principal: Any, project_id: Any, action: Any) -> None:
        del principal, project_id, action
        self.calls += 1


@asynccontextmanager
async def _counted_app(
    tmp_path: Path,
) -> AsyncIterator[tuple[Built, MemoryStores, _CountingAccess]]:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    del tmp_path
    access = _CountingAccess()
    built = build(
        memory_operations(stores, access=access), verifier=_verifier(FakeIssuer([KEY.jwk()]))
    )
    async with built.app.router.lifespan_context(built.app):
        yield built, stores, access


@pytest.mark.asyncio
async def test_fr2_1_a_token_with_a_user_id_reads_a_release(tmp_path: Path) -> None:
    """FR-2.1: a valid token with a ``user_id`` UUID reads a release (200), asking Project."""
    async with _counted_app(tmp_path) as (built, _, access):
        token = _token(list(ALL_SCOPES), user_id=USER_ID)
        async with _http(built, token) as http:
            response = await http.get(f"/v1/projects/{PROJECT}/releases/{RELEASE}")
    assert response.status_code == 200, response.text
    assert access.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_id",
    [
        None,
        "",
        "not-a-uuid",
        USER_ID.upper(),
        "00000000-0000-0000-0000-000000000000",
        f"{{{USER_ID}}}",
        42,
    ],
)
async def test_fr2_2_a_token_without_a_user_id_is_403_on_every_route_and_mcp(
    tmp_path: Path, user_id: Any
) -> None:
    """FR-2.2: a valid token with no (valid) ``user_id`` answers 403 ``ACCESS_DENIED`` on a read,
    an admin action and ``/mcp``; Project is never called and nothing is stored."""
    async with _counted_app(tmp_path) as (built, stores, access):
        token = _token([*ALL_SCOPES, ADMIN_ROLE], user_id=user_id)
        async with _http(built, token) as http:
            read = await http.get(f"/v1/projects/{PROJECT}/releases/{RELEASE}")
            admin = await http.post(SUSPEND, headers={"Idempotency-Key": "k1"})
            mcp = await http.post("/mcp", content=b"{}", headers=MCP_HEADERS)
    for response in (read, admin, mcp):
        assert _code(response) == (403, "ACCESS_DENIED"), response.text
    assert access.calls == 0
    assert stores.commands == []


@pytest.mark.asyncio
async def test_fr2_2_a_refused_token_without_a_user_id_is_still_401(tmp_path: Path) -> None:
    """FR-2.2: the token check comes first, so a refused token is 401, not 403."""
    async with _counted_app(tmp_path) as (built, _, access):
        token = _token(list(ALL_SCOPES), user_id=None, iss=ISSUER + "x")
        async with _http(built, token) as http:
            response = await http.get(f"/v1/projects/{PROJECT}/releases/{RELEASE}")
    assert _code(response) == (401, "UNAUTHENTICATED")
    assert access.calls == 0
