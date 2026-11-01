"""The MCP endpoint (decision 0006 section 4; decision 0013 section 9 S3): stateless Streamable HTTP
mounted at /mcp behind the same authentication, the HTTP-level answers as RFC 9457 Problems with
instance /mcp (405 has no body; decision 0018), and every tool through the SDK's own client reaching
the same use case as REST."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from tests.unit.presentation.apps import BASE, TOOLS, build, client, mcp_form
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations

from provisioning.core.application import Action, NotFound, OperationInProgress
from provisioning.presentation.operations import OPERATIONS

MCP = "/mcp"
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
INITIALIZE = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "1"},
        },
    }
).encode()


def _error(
    response: httpx2.Response,
    status: int,
    code: str,
    media_type: str = "application/json",
) -> None:
    """A Problem whose instance is /mcp. The default content type is the one JSON_HEADERS asks for:
    its Accept (application/json, text/event-stream) allows only application/json (spec 6.3)."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == media_type
    body = response.json()
    assert (body["code"], body["status"], body["instance"]) == (code, status, MCP)
    assert body["type"] == "urn:webblock:problem:" + code.lower().replace("_", "-")


@asynccontextmanager
async def _sdk(http: httpx2.AsyncClient, mode: str = "legacy") -> AsyncIterator[Client]:
    async with Client(streamable_http_client(f"{BASE}{MCP}", http_client=http), mode=mode) as c:
        yield c


def _arguments(action: Action) -> dict[str, str]:
    values = {
        "projectId": str(PROJECT),
        "releaseId": str(RELEASE),
        "commandId": str(COMMAND),
        "idempotencyKey": "k1",
    }
    # The operation's own inputs, without the optional paging ones.
    op = next(o for o in OPERATIONS if o.action is action)
    names = [n for n in op.inputs if n in values]
    return {n: values[n] for n in names}


# --- HTTP-level answers ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "DELETE", "PUT"])
async def test_methods_other_than_post_are_405_without_a_body(method: str) -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.request(method, MCP)
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert response.content == b""


@pytest.mark.asyncio
async def test_the_endpoint_requires_a_token() -> None:
    async with client(build(FakeOperations()), token=None) as http:
        response = await http.post(MCP, headers=JSON_HEADERS, content=INITIALIZE)
    _error(response, 401, "UNAUTHENTICATED")
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accept", "media_type"),
    [
        ("application/json", "application/json"),
        ("application/json, text/event-stream", "application/json"),
        ("application/problem+json", "application/problem+json"),
    ],
)
async def test_the_401_at_mcp_follows_accept(accept: str, media_type: str) -> None:
    async with client(build(FakeOperations()), token=None) as http:
        response = await http.post(
            MCP, headers={**JSON_HEADERS, "Accept": accept}, content=INITIALIZE
        )
    _error(response, 401, "UNAUTHENTICATED", media_type)
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Content-Type": "text/plain"},
        {"Content-Type": ""},
        {"Accept": "application/json"},
        {"Accept": "text/event-stream"},
        {"Host": "evil.example"},
    ],
)
async def test_bad_headers_are_malformed_in_the_error_shape(headers: dict[str, str]) -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.post(MCP, headers={**JSON_HEADERS, **headers}, content=INITIALIZE)
    # An Accept of text/event-stream alone allows neither Problem type: the default, problem+json.
    only_events = headers.get("Accept") == "text/event-stream"
    _error(
        response,
        400,
        "VALIDATION_FAILED",
        "application/problem+json" if only_events else "application/json",
    )
    (name,) = headers
    assert [(e["field"], e["code"]) for e in response.json()["errors"]] == [
        (name, "INVALID_FORMAT")
    ]
    assert "evil" not in response.text


@pytest.mark.asyncio
async def test_an_allowed_origin_and_wildcard_accept_are_served() -> None:
    built = build(FakeOperations(), allowed_origins=("https://app.example",))
    async with client(built) as http:
        response = await http.post(
            MCP,
            headers={**JSON_HEADERS, "Origin": "https://app.example", "Accept": "*/*"},
            content=INITIALIZE,
        )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_two_origin_headers_are_malformed() -> None:
    built = build(FakeOperations(), allowed_origins=("https://app.example",))
    async with client(built) as http:
        response = await http.post(
            MCP,
            headers=[
                *JSON_HEADERS.items(),
                ("Origin", "https://app.example"),
                ("Origin", "https://app.example"),
            ],
            content=INITIALIZE,
        )
    _error(response, 400, "VALIDATION_FAILED")


@pytest.mark.asyncio
async def test_an_over_limit_body_is_413_in_the_error_shape() -> None:
    async with client(build(FakeOperations(), mcp_body_limit=64)) as http:
        response = await http.post(MCP, headers=JSON_HEADERS, content=INITIALIZE)
    _error(response, 413, "REQUEST_TOO_LARGE")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"[" + INITIALIZE + b"]",
        b"  [" + INITIALIZE + b"]",
        b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"ping"}',
        b"\xff",
        b"not json",
    ],
    ids=["batch", "batch after space", "duplicate key", "invalid utf-8", "not json"],
)
async def test_a_batch_or_malformed_json_is_refused(body: bytes) -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.post(MCP, headers=JSON_HEADERS, content=body)
    _error(response, 400, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in response.json()["errors"]] == [
        ("body", "INVALID_FORMAT")
    ]


# --- tools through the SDK's own client -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_the_sdk_client_lists_exactly_the_tools_of_the_file(mode: str) -> None:
    file_tools = json.loads(TOOLS)["tools"]
    async with client(build(FakeOperations())) as http, _sdk(http, mode) as sdk:
        listed = (await sdk.list_tools()).tools
    assert [t.name for t in listed] == [t["name"] for t in file_tools]
    for tool, spec in zip(listed, file_tools, strict=True):
        assert tool.description == spec["description"]
        assert tool.input_schema == spec["inputSchema"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
@pytest.mark.parametrize("action", list(Action))
async def test_each_tool_reaches_the_same_use_case_as_rest(mode: str, action: Action) -> None:
    rest_service, mcp_service = FakeOperations(), FakeOperations()
    op = next(o for o in OPERATIONS if o.action is action)
    path = op.path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)
    async with client(build(rest_service)) as http:
        rest = await http.request(op.method, path, headers={"Idempotency-Key": "k1"})
    async with client(build(mcp_service)) as http, _sdk(http, mode) as sdk:
        result = await sdk.call_tool(action.value, _arguments(action))
    assert not result.is_error
    # The tool result is the REST data, no envelope (decision 0023 G7).
    assert result.structured_content == mcp_form(rest.json())
    assert "meta" not in result.structured_content
    (text,) = result.content
    assert json.loads(getattr(text, "text")) == mcp_form(rest.json())  # noqa: B009  # TextContent
    assert mcp_service.calls == rest_service.calls
    assert mcp_service.calls[0][1].subject == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [(NotFound("x"), "RELEASE_NOT_FOUND"), (OperationInProgress(COMMAND), "COMMAND_IN_PROGRESS")],
)
async def test_a_failure_is_a_tool_result_marked_as_an_error_with_the_rest_body(
    error: Exception, code: str
) -> None:
    rest_service = FakeOperations(errors={Action.DEPLOY_RELEASE: error})
    mcp_service = FakeOperations(errors={Action.DEPLOY_RELEASE: error})
    async with client(build(rest_service)) as http:
        rest = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy", headers={"Idempotency-Key": "k1"}
        )
    async with client(build(mcp_service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool("deploy_release", _arguments(Action.DEPLOY_RELEASE))
    assert result.is_error
    rest_problem = rest.json()
    assert rest_problem.pop("instance") == f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy"
    assert result.structured_content == rest_problem
    assert result.structured_content is not None
    assert result.structured_content["code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "errors"),
    [
        # An unknown argument is the field "arguments": its name is never echoed.
        (
            {"projectId": str(PROJECT), "releaseId": str(RELEASE), "caller_zq": "x"},
            [("arguments", "INVALID_FORMAT")],
        ),
        ({"projectId": str(PROJECT), "releaseId": 5}, [("releaseId", "INVALID_FORMAT")]),
        ({"projectId": str(PROJECT)}, [("releaseId", "REQUIRED")]),
        (
            {"projectId": "not-a-uuid", "releaseId": str(RELEASE)},
            [("projectId", "INVALID_FORMAT")],
        ),
        (
            {"projectId": str(PROJECT), "releaseId": str(RELEASE), "idempotencyKey": "k"},
            [("arguments", "INVALID_FORMAT")],
        ),
        # The old snake_case names are unknown arguments (decision 0023 G3).
        (
            {"project_id": str(PROJECT), "release_id": str(RELEASE)},
            [("arguments", "INVALID_FORMAT")],
        ),
    ],
)
async def test_bad_arguments_are_refused_without_calling_the_application(
    arguments: dict[str, Any], errors: list[tuple[str, str]]
) -> None:
    service = FakeOperations()
    async with client(build(service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool("build_release", arguments)
    assert result.is_error
    problem = result.structured_content
    assert problem is not None
    assert problem["code"] == "VALIDATION_FAILED"
    assert [(e["field"], e["code"]) for e in problem["errors"]] == errors
    assert "caller_zq" not in json.dumps(problem)
    assert "not-a-uuid" not in json.dumps(problem)
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_missing_key_on_a_mutation_tool_is_a_required_field() -> None:
    service = FakeOperations()
    async with client(build(service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool(
            "deploy_release", {"projectId": str(PROJECT), "releaseId": str(RELEASE)}
        )
    assert result.structured_content is not None
    assert result.structured_content["code"] == "VALIDATION_FAILED"
    assert [(e["field"], e["code"]) for e in result.structured_content["errors"]] == [
        ("idempotencyKey", "REQUIRED")
    ]
    assert service.calls == []


@pytest.mark.asyncio
async def test_http_answers_name_mcp_and_tool_results_name_nothing() -> None:
    service = FakeOperations(errors={Action.DEPLOY_RELEASE: NotFound("x")})
    async with client(build(service), token=None) as http:
        anonymous = await http.post(MCP, headers=JSON_HEADERS, content=INITIALIZE)
    async with client(build(service)) as http:
        refused = await http.post(
            MCP, headers={**JSON_HEADERS, "Origin": "https://evil.example"}, content=INITIALIZE
        )
        wrong_method = await http.get(MCP)
    async with client(build(service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool("deploy_release", _arguments(Action.DEPLOY_RELEASE))
    _error(anonymous, 401, "UNAUTHENTICATED")
    _error(refused, 400, "VALIDATION_FAILED")
    assert (wrong_method.status_code, wrong_method.content) == (405, b"")
    assert "content-type" not in wrong_method.headers
    assert result.is_error
    assert result.structured_content is not None
    assert "instance" not in result.structured_content
    assert result.structured_content["status"] == 404
    (text,) = result.content
    assert "instance" not in getattr(text, "text")  # noqa: B009  # TextContent's field


@pytest.mark.asyncio
async def test_an_unknown_tool_is_a_protocol_error_and_calls_nothing() -> None:
    service = FakeOperations()
    async with client(build(service)) as http, _sdk(http) as sdk:
        with pytest.raises(MCPError):
            await sdk.call_tool("create_release", {})
    assert service.calls == []
