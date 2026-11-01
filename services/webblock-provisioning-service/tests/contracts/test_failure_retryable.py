"""``failure_retryable`` (task H5a; decision 0015 section 4 step 1): wherever a release, step or
command carries a failure code (``failure_code``, a step's too since task RN), the body carries
``failure_retryable``, the catalogue's flag for that code, and only then. The REST contract
declares it on exactly those schemas, and the MCP read tools return the same bodies as REST (the
tool file declares no output schema, lead's ruling)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import BASE, build, client, mcp_form
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations

from provisioning.core.application import Action
from provisioning.core.domain import failure_retryable
from provisioning.presentation.operations import OPERATIONS

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "src" / "contracts" / "openapi.yaml"
TOOLS = ROOT / "src" / "contracts" / "mcp" / "tools.json"
CODE_FIELDS = ("failureCode",)
READS = (
    Action.GET_RELEASE,
    Action.GET_COMMAND,
    Action.LIST_RELEASES,
    Action.LIST_COMMANDS,
    Action.GET_RELEASE_DIAGNOSTICS,
)


def _schemas() -> dict[str, Any]:
    with CONTRACT.open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    schemas: dict[str, Any] = doc["components"]["schemas"]
    return schemas


def test_the_contract_declares_failure_retryable_beside_every_failure_code() -> None:
    with_code = set()
    for name, schema in _schemas().items():
        properties = schema.get("properties", {})
        has_code = any(field in properties for field in CODE_FIELDS)
        has_flag = "failureRetryable" in properties
        assert has_code == has_flag, name
        if has_code:
            with_code.add(name)
            flag = properties["failureRetryable"]
            assert flag["type"] == "boolean", name
            assert "failureRetryable" not in schema.get("required", []), name
            assert "Present exactly when" in flag["description"], name
    assert with_code == {
        "ReleaseStep",
        "Release",
        "Command",
        "AdminCommand",
        "ReleaseSummary",
        "DiagnosticRelease",
    }


def _objects(body: Any) -> Iterator[dict[str, Any]]:
    if isinstance(body, dict):
        yield body
        for value in body.values():
            yield from _objects(value)
    elif isinstance(body, list):
        for item in body:
            yield from _objects(item)


def _check_flags(body: Any) -> int:
    """Every object's flag against its code; the number of codes seen."""
    seen = 0
    for obj in _objects(body):
        codes = [obj[field] for field in CODE_FIELDS if field in obj]
        assert len(codes) <= 1, obj
        if codes:
            seen += 1
            assert obj["failureRetryable"] is failure_retryable(codes[0]), obj
        else:
            assert "failureRetryable" not in obj, obj
    return seen


@asynccontextmanager
async def _sdk(service: FakeOperations) -> AsyncIterator[Client]:
    async with (
        client(build(service)) as http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as sdk,
    ):
        yield sdk


def _arguments(action: Action) -> dict[str, str]:
    op = next(o for o in OPERATIONS if o.action is action)
    values = {"projectId": str(PROJECT), "releaseId": str(RELEASE), "commandId": str(COMMAND)}
    return {name: values[name] for name in op.required()}


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [True, False], ids=["with codes", "without codes"])
@pytest.mark.parametrize("action", READS)
async def test_rest_and_mcp_carry_the_flag_exactly_with_a_code(action: Action, full: bool) -> None:
    op = next(o for o in OPERATIONS if o.action is action)
    path = op.path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)
    async with client(build(FakeOperations(full=full))) as http:
        rest = await http.request(op.method, path)
    async with _sdk(FakeOperations(full=full)) as sdk:
        result = await sdk.call_tool(action.value, _arguments(action))

    assert rest.status_code == 200
    assert not result.is_error
    assert result.structured_content == mcp_form(rest.json())
    seen = _check_flags(rest.json())
    assert (seen > 0) is full, (action, seen)


def test_the_flag_follows_the_catalogue_in_the_fakes() -> None:
    """The fakes' codes cover both values and an unlisted code, so the checks above are not
    vacuous."""
    assert failure_retryable("candidate_not_ready") is True
    assert failure_retryable("build_failed") is False
    assert failure_retryable("promotion_failed") is False  # not in the catalogue


def test_the_read_tools_that_list_fields_name_the_flag() -> None:
    tools = {t["name"]: t["description"] for t in json.loads(TOOLS.read_text("utf-8"))["tools"]}
    for name in ("get_release_diagnostics", "list_releases", "list_commands"):
        assert "failureRetryable" in tools[name], name
