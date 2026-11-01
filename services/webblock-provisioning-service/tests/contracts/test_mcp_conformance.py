"""REST and MCP agree (decision 0006 sections 3 and 4): src/contracts/mcp/tools.json is what the
SDK's own client lists, and for every operation and every error class the two transports give the
same body (for an error, the same RFC 9457 Problem, which REST alone completes with instance;
decision 0018; for a success, the REST data without the envelope, decision 0023 G7), reaching the
same use case with the same request."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import jsonschema  # type: ignore[import-untyped]  # jsonschema (pulled in by mcp) has no stubs in the lock; tests only
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import BASE, build, client, mcp_form
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations

from provisioning.core.application import (
    Action,
    ActionDenied,
    BuildInProgress,
    Contention,
    LimitReached,
    NotFound,
    OperationInProgress,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    ReleaseNotBuildable,
    Timeout,
)
from provisioning.core.domain import HostingOffline, IdempotencyConflict
from provisioning.presentation.operations import OPERATIONS

TOOLS = Path(__file__).resolve().parents[2] / "src" / "contracts" / "mcp" / "tools.json"
CONTRACT = Path(__file__).resolve().parents[2] / "src" / "contracts" / "openapi.yaml"
IN_FLIGHT = UUID("0190a1b2-0000-7000-8000-0000000000c9")


@asynccontextmanager
async def _sdk(http: httpx2.AsyncClient) -> AsyncIterator[Client]:
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as c:
        yield c


def _arguments(action: Action) -> dict[str, str]:
    op = next(o for o in OPERATIONS if o.action is action)
    values = {
        "projectId": str(PROJECT),
        "releaseId": str(RELEASE),
        "commandId": str(COMMAND),
        "idempotencyKey": "k1",
    }
    return {name: values[name] for name in op.required()}


async def _both(
    action: Action, error: BaseException | None
) -> tuple[httpx2.Response, Any, FakeOperations, FakeOperations]:
    op = next(o for o in OPERATIONS if o.action is action)
    errors = {} if error is None else {action: error}
    rest_service, mcp_service = (
        FakeOperations(errors=dict(errors)),
        FakeOperations(errors=dict(errors)),
    )
    path = op.path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)
    async with client(build(rest_service)) as http:
        rest = await http.request(op.method, path, headers={"Idempotency-Key": "k1"})
    async with client(build(mcp_service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool(action.value, _arguments(action))
    return rest, result, rest_service, mcp_service


@pytest.mark.asyncio
async def test_the_sdk_client_lists_the_tool_file() -> None:
    spec = json.loads(TOOLS.read_text("utf-8"))["tools"]
    async with client(build(FakeOperations())) as http, _sdk(http) as sdk:
        tools = (await sdk.list_tools()).tools
    assert [(t.name, t.description, t.input_schema) for t in tools] == [
        (s["name"], s["description"], s["inputSchema"]) for s in spec
    ]


_CLASSES: list[BaseException | None] = [
    None,
    NotFound("x"),
    ProjectAccessDenied("x"),
    ActionDenied("x"),
    IdempotencyConflict("x"),
    HostingOffline("x"),
    OperationInProgress(IN_FLIGHT),
    LimitReached("x"),
    Contention("x"),
    Timeout("x"),
    ProjectAccessUnavailable("x"),
    ReleaseNotBuildable("x"),
    BuildInProgress("x"),
    RuntimeError("anything"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_rest_and_mcp_agree_on_every_error_class(action: Action) -> None:
    op = next(o for o in OPERATIONS if o.action is action)
    path = op.path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)
    for error in _CLASSES:
        rest, result, rest_service, mcp_service = await _both(action, error)
        body = rest.json()
        if rest.status_code >= 400:
            assert body.pop("instance") == path, (action, error)
        else:
            # A success: MCP has no envelope; it gets the REST data (decision 0023 G7).
            body = mcp_form(body)
        assert result.structured_content == body, (action, error)
        (content,) = result.content
        assert json.loads(getattr(content, "text")) == body, (action, error)  # noqa: B009  # TextContent
        assert result.is_error == (rest.status_code >= 400), (action, error)
        assert mcp_service.calls == rest_service.calls, (action, error)


def _problem_validator() -> jsonschema.Draft7Validator:
    with CONTRACT.open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    root = {"$ref": "#/components/schemas/Problem", "components": doc["components"]}
    return jsonschema.Draft7Validator(root, format_checker=jsonschema.FormatChecker())


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_every_error_result_is_the_contracts_problem_without_instance(action: Action) -> None:
    validator = _problem_validator()
    for error in _CLASSES:
        if error is None:
            continue
        rest, result, _, _ = await _both(action, error)
        assert result.is_error, (action, error)
        problem = result.structured_content
        assert problem is not None, (action, error)
        validator.validate(problem)
        assert "instance" not in problem, (action, error)
        assert problem["status"] == rest.status_code, (action, error)


@pytest.mark.asyncio
async def test_only_command_in_progress_carries_a_command_id() -> None:
    for error in _CLASSES:
        rest, result, _, _ = await _both(Action.DEPLOY_RELEASE, error)
        carries = "commandId" in rest.json() and rest.status_code == 409
        assert carries == isinstance(error, OperationInProgress), error
        if carries:
            assert result.structured_content["commandId"] == str(IN_FLIGHT)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_the_not_found_cases_are_byte_identical_on_each_transport(action: Action) -> None:
    """An unknown project and one without access (G6): the same bytes on each transport."""
    rest_answers: set[tuple[bytes, tuple[tuple[str, str], ...]]] = set()
    mcp_bodies: set[bytes] = set()
    for error in (
        ProjectAccessDenied("the project service denied access: NOT_FOUND"),
        ProjectAccessDenied("the project service denied access: NOT_MANAGER"),
    ):
        rest, result, _, _ = await _both(action, error)
        kept = {k: v for k, v in rest.headers.items() if k != "x-request-id"}
        rest_answers.add((rest.content, tuple(sorted(kept.items()))))
        (content,) = result.content
        mcp_bodies.add(getattr(content, "text").encode())  # noqa: B009  # TextContent
    assert len(rest_answers) == 1
    assert len(mcp_bodies) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_no_response_carries_the_key_or_the_requester(action: Action) -> None:
    for error in (None, IdempotencyConflict("x")):
        rest, result, _, _ = await _both(action, error)
        for text in (rest.text, json.dumps(result.structured_content)):
            assert "k1" not in text
            assert "alice" not in text
