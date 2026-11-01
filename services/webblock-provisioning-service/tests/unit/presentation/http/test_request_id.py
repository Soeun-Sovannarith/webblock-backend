"""X-Request-Id (spec E8): a safe inbound value is kept, anything else becomes a UUIDv7; the value
is on every response, is the Problem's requestId and the command's correlation ID, on REST and
MCP, and the same pattern as the event envelope's."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import BASE, build, client
from tests.unit.presentation.fakes import PROJECT, RELEASE, FakeOperations

from provisioning.core.application import Action
from provisioning.infrastructure.telemetry import current_correlation_id
from provisioning.infrastructure.telemetry.correlation import valid_correlation_id
from provisioning.presentation.http.request_id import (
    _FORM,
    REQUEST_ID_HEADER,
    accept_request_id,
)


def _corr(service: FakeOperations, index: int) -> str:
    return str(getattr(service.calls[index][2], "correlation_id"))  # noqa: B009


GOOD = "0192f4a8-9d01-7c44-8e2b-11aa22bb33cc"
DEPLOY = f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy"


@pytest.mark.parametrize("raw", ["a b", "line\nbreak", "x" * 129, "", "é", None])
def test_unsafe_request_id_is_replaced(raw: str | None) -> None:
    rid = accept_request_id(raw)
    assert rid != raw
    assert UUID(rid).version == 7


def test_valid_request_id_is_kept() -> None:
    assert accept_request_id(GOOD) == GOOD
    assert accept_request_id("a.b_c:d-e") == "a.b_c:d-e"


def test_the_pattern_is_the_correlation_pattern() -> None:
    for value in ["", "a", "a:b", "a b", "é", "x" * 128, "x" * 129, "a\n"]:
        assert (_FORM.fullmatch(value) is not None) == valid_correlation_id(value)


@pytest.mark.asyncio
async def test_response_and_problem_carry_the_request_id() -> None:
    async with client(build(FakeOperations(), request_ids=True)) as http:
        ok = await http.get(f"/v1/projects/{PROJECT}/deployment", headers={REQUEST_ID_HEADER: GOOD})
        bad = await http.get("/v1/nothing", headers={REQUEST_ID_HEADER: GOOD})
        generated = await http.get("/v1/nothing", headers={REQUEST_ID_HEADER: "bad id"})
        no_token = await http.get("/v1/nothing", headers={"Authorization": "x"})
    assert ok.headers[REQUEST_ID_HEADER] == GOOD
    assert bad.headers[REQUEST_ID_HEADER] == GOOD
    assert bad.json()["requestId"] == GOOD
    rid = generated.headers[REQUEST_ID_HEADER]
    assert UUID(rid).version == 7
    assert generated.json()["requestId"] == rid
    assert no_token.status_code == 401
    assert UUID(no_token.headers[REQUEST_ID_HEADER]).version == 7
    assert no_token.json()["requestId"] == no_token.headers[REQUEST_ID_HEADER]


@pytest.mark.asyncio
async def test_accepted_command_correlation_id_is_the_request_id() -> None:
    service = FakeOperations()
    async with client(build(service, request_ids=True)) as http:
        await http.post(DEPLOY, headers={"Idempotency-Key": "k1", REQUEST_ID_HEADER: GOOD})
        await http.post(DEPLOY, headers={"Idempotency-Key": "k2"})
    assert _corr(service, 0) == GOOD
    second = _corr(service, 1)
    assert UUID(second).version == 7


@pytest.mark.asyncio
async def test_the_grpc_correlation_context_is_the_request_id() -> None:
    seen: list[str | None] = []

    class Spy(FakeOperations):
        async def handle(self, action: Action, intent: Any) -> object:
            if action is Action.GET_DEPLOYMENT:
                seen.append(current_correlation_id())
            return await super().handle(action, intent)

    async with client(build(Spy(), request_ids=True)) as http:
        await http.get(f"/v1/projects/{PROJECT}/deployment", headers={REQUEST_ID_HEADER: GOOD})
    assert seen == [GOOD]


@pytest.mark.asyncio
async def test_mcp_responses_and_calls_carry_the_request_id() -> None:
    service = FakeOperations()
    async with client(build(service, request_ids=True)) as http:
        unauth = await http.post("/mcp", headers={"Authorization": "x", REQUEST_ID_HEADER: GOOD})
        assert unauth.headers[REQUEST_ID_HEADER] == GOOD
        assert unauth.json()["requestId"] == GOOD
        http.headers[REQUEST_ID_HEADER] = GOOD
        async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as sdk:
            result = await sdk.call_tool(
                "deploy_release",
                {"projectId": str(PROJECT), "releaseId": str(RELEASE), "idempotencyKey": "k1"},
            )
    assert not result.is_error
    assert _corr(service, 0) == GOOD
