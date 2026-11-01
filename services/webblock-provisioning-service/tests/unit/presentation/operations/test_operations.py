"""The operation table: the contract's fourteen operations in contract order (the four admin ones of
decision 0023 G9 last), each with its method, path, inputs and exact status set, and the inputs
equal to the MCP tool's input schema."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from tests.unit.presentation.fakes import COMMAND, PROJECT

from provisioning.core.application import Action
from provisioning.presentation.operations import OPERATIONS, Code, lookup, status_url

CONTRACTS = Path(__file__).resolve().parents[4] / "src" / "contracts"


def _openapi() -> dict[str, Any]:
    with (CONTRACTS / "openapi.yaml").open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    return doc


def _tools() -> list[dict[str, Any]]:
    doc: dict[str, Any] = json.loads((CONTRACTS / "mcp" / "tools.json").read_text("utf-8"))
    return list(doc["tools"])


def _resolve(doc: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = doc
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def test_operations_match_the_rest_contract() -> None:
    doc = _openapi()
    seen: list[str] = []
    for path, item in doc["paths"].items():
        for method, operation in item.items():
            seen.append(operation["operationId"])
            op = lookup(Action(operation["operationId"]))
            assert op is not None
            assert op.method == method.upper()
            assert op.path == path
            statuses = {int(s) for s in operation["responses"]}
            assert set(op.statuses) == statuses, op.action
            params = [_resolve(doc, p) for p in operation.get("parameters", [])]
            path_params = [p["name"] for p in params if p["in"] == "path"]
            assert path_params == re.findall(r"\{([A-Za-z]+)\}", path)
            inputs = list(path_params)
            if any(p["name"] == "Idempotency-Key" for p in params):
                inputs.append("idempotencyKey")
            query = [p["name"] for p in params if p["in"] == "query"]
            assert all(not p.get("required", False) for p in params if p["in"] == "query")
            inputs.extend(query)
            assert list(op.inputs) == inputs, op.action
            assert op.optional == frozenset(query), op.action
    assert seen == [op.action.value for op in OPERATIONS]
    assert [op.action for op in OPERATIONS] == list(Action)


def test_operations_match_the_tool_input_schemas() -> None:
    tools = {t["name"]: t for t in _tools()}
    assert list(tools) == [op.action.value for op in OPERATIONS]
    for op in OPERATIONS:
        schema = tools[op.action.value]["inputSchema"]
        assert sorted(schema["properties"]) == sorted(op.inputs)
        assert sorted(schema["required"]) == sorted(op.required())
        assert schema["additionalProperties"] is False
        if op.optional:
            # The listings (decision 0023 G4): the limit is an integer, the cursor a string.
            assert schema["properties"]["limit"]["type"] == "integer"
            assert schema["properties"]["cursor"]["type"] == "string"


def test_status_sets() -> None:
    # Notion's table (decision 0023 G6): 422 for business rules, 504 for deadlines, no 429.
    mutation = {202, 400, 401, 403, 404, 409, 413, 422, 500, 503, 504}
    read = {200, 400, 401, 403, 404, 409, 500, 503, 504}  # 409: CONCURRENT_CHANGE only
    want = {
        Action.DEPLOY_RELEASE: mutation,
        Action.PROMOTE_RELEASE: mutation,
        Action.ROLLBACK_RELEASE: mutation,
        Action.GET_RELEASE: read,
        Action.GET_COMMAND: read,
        Action.GET_DEPLOYMENT: read,
        Action.GET_RELEASE_DIAGNOSTICS: read,
        Action.BUILD_RELEASE: mutation,
        Action.LIST_RELEASES: read,
        Action.LIST_COMMANDS: read,
        Action.SUSPEND_WEBSITE: mutation,
        Action.RESUME_WEBSITE: mutation,
        Action.REDEPLOY_RELEASE: mutation,
        Action.ADMIN_LIST_COMMANDS: read,
    }
    for op in OPERATIONS:
        assert set(op.statuses) == want[op.action], op.action
        assert op.declares(500)
        assert op.is_mutation() == (op.method == "POST")
        assert op.answers(409, "CONCURRENT_CHANGE")
        assert op.answers(409, "COMMAND_IN_PROGRESS") == op.is_mutation()
        assert not op.answers(429, "PUBLISH_LIMIT_REACHED")


def test_not_found_codes_name_what_the_operation_reads() -> None:
    """Decision 0023 G6: once access passed, a missing resource names itself.

    FR-37.2, FR-39.2, FR-40.2, FR-41.2."""
    want = {
        Action.GET_COMMAND: Code.COMMAND_NOT_FOUND,
        Action.GET_DEPLOYMENT: Code.DEPLOYMENT_NOT_FOUND,
        Action.LIST_RELEASES: Code.PROJECT_NOT_FOUND,
        Action.LIST_COMMANDS: Code.PROJECT_NOT_FOUND,
        Action.SUSPEND_WEBSITE: Code.PROJECT_NOT_FOUND,
        Action.RESUME_WEBSITE: Code.PROJECT_NOT_FOUND,
        Action.REDEPLOY_RELEASE: Code.PROJECT_NOT_FOUND,
        Action.ADMIN_LIST_COMMANDS: Code.PROJECT_NOT_FOUND,
    }
    for op in OPERATIONS:
        assert op.not_found is want.get(op.action, Code.RELEASE_NOT_FOUND), op.action


def test_lookup_of_every_action() -> None:
    """Every action has its operation, the four admin ones included (decision 0023 G9)."""
    for action in Action:
        op = lookup(action)
        assert op is not None
        assert op.action is action


def test_status_url_is_built_from_identifiers() -> None:
    assert status_url(PROJECT, COMMAND) == f"/v1/projects/{PROJECT}/commands/{COMMAND}"


def test_input_names_are_camel_case() -> None:
    """Decision 0023 G3: path parameters and tool arguments are camelCase."""
    names = {name for op in OPERATIONS for name in op.inputs}
    assert names == {"projectId", "releaseId", "commandId", "idempotencyKey", "limit", "cursor"}
    for op in OPERATIONS:
        assert "_" not in op.path, op.action


def test_the_admin_operations_are_under_the_admin_prefix() -> None:
    """Decision 0023 G9: the four admin operations are under /v1/admin/projects/{projectId};
    rollback, admin-only too (FR-50), keeps its client path."""
    for op in OPERATIONS:
        admin = op.path.startswith("/v1/admin/projects/{projectId}/")
        on_admin_path = op.action.is_admin_only() and op.action is not Action.ROLLBACK_RELEASE
        assert admin == on_admin_path, op.action
