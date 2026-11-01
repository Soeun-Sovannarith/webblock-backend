"""The wire uses the revision-19 names (task RN; decision 0016 section 8 ruling 5, following
migration 00011) in camelCase (decision 0023 G3): no field name, schema name or value that the
rename replaced appears in src/contracts/openapi.yaml, in src/contracts/mcp/tools.json or in any
body the REST and MCP transports serve, and every name a client sees is camelCase. And the served
bodies, the REST contract and the tool file describe one surface: every REST success is the
envelope (G2) and validates against the operation's schema, every MCP result is the REST data
(G7), and a body with every optional field set uses every property its schema declares, so the
contract keeps no field the server never sends. Every error response of the contract is an RFC 9457
Problem served as application/problem+json (or application/json, with the same schema, to a client
that accepts only that), and the tool file says an error result carries the same object (decision
0018)."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

import httpx2
import jsonschema  # type: ignore[import-untyped]  # jsonschema (pulled in by mcp) has no stubs in the lock; tests only
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import BASE, build, client, mcp_form
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations

from provisioning.core.application import Action
from provisioning.presentation.operations import OPERATIONS

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "src" / "contracts" / "openapi.yaml"
TOOLS = ROOT / "src" / "contracts" / "mcp" / "tools.json"

OLD_FIELDS: Final = (
    "source_generation_request_id",
    "source_repository_id",
    "source_commit_sha",
    "image_digest",
    "reason_code",
    "fencing_generation",
    "active_release_id",
    "operation_generation",
    "route_state",
    "hosting_state",
    "details_ref",
    "request_hash",
    "requested_by",
)
"""The old wire names of the inventory, plus the renamed columns that were never on the wire."""

OLD_SCHEMAS: Final = ("FencingGeneration", "OperationGeneration", "RouteState", "HostingState")
OLD_VALUE: Final = "serving"


def _word(name: str) -> re.Pattern[str]:
    """``name`` as a whole identifier: ``image_digest`` inside ``container_image_digest`` and
    ``requested_by`` inside ``requested_by_subject`` do not count."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def _contract() -> dict[str, Any]:
    with CONTRACT.open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    return doc


def _camel_keys(value: object) -> None:
    """No object key anywhere in ``value`` has an underscore (decision 0023 G3)."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert "_" not in key, key
            _camel_keys(item)
    elif isinstance(value, list):
        for item in value:
            _camel_keys(item)


def _keys_and_strings(value: object) -> Iterator[str]:
    """Every object key and every string value anywhere in ``value``."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _keys_and_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys_and_strings(item)
    elif isinstance(value, str):
        yield value


@pytest.mark.parametrize("path", [CONTRACT, TOOLS], ids=["openapi.yaml", "tools.json"])
def test_no_old_wire_name_appears_in_the_contract_files(path: Path) -> None:
    text = path.read_text("utf-8")
    for name in (*OLD_FIELDS, *OLD_SCHEMAS):
        assert not _word(name).search(text), (path.name, name)


def test_the_hosting_status_values_are_online_and_offline() -> None:
    schemas = _contract()["components"]["schemas"]
    assert schemas["HostingStatus"]["enum"] == ["online", "offline"]
    for value in _keys_and_strings(_contract()):
        assert OLD_VALUE not in value.split(), value


def test_the_command_kind_values_follow_notion() -> None:
    """Notion "5 · Provisioning" kind order; sync_domains is hidden and never on the wire. The API
    accepts no redeploy yet (decision 0021 D10), but a listed command may carry the kind."""
    schemas = _contract()["components"]["schemas"]
    assert schemas["CommandKind"]["enum"] == [
        "deploy",
        "promote",
        "rollback",
        "suspend_hosting",
        "resume_hosting",
        "redeploy",
    ]


def test_the_deployment_schema_uses_the_revision_19_names_in_camel_case() -> None:
    deployment = _contract()["components"]["schemas"]["Deployment"]
    assert set(deployment["properties"]) == {
        "projectId",
        "liveReleaseId",
        "lastCommandNumber",
        "routingStatus",
        "hostingStatus",
        "inFlightCommand",
        "updatedAt",
    }
    assert deployment["required"] == [
        "projectId",
        "lastCommandNumber",
        "routingStatus",
        "hostingStatus",
        "updatedAt",
    ]


def test_every_response_model_is_camel_case() -> None:
    """Decision 0023 G3: every property and required name of every schema, and every path and
    query parameter, has no underscore. Values (enums, operation IDs) are not names."""
    doc = _contract()
    for name, schema in doc["components"]["schemas"].items():
        for prop in schema.get("properties", {}):
            assert "_" not in prop, (name, prop)
        for prop in schema.get("required", []):
            assert "_" not in prop, (name, prop)
    for name, parameter in doc["components"]["parameters"].items():
        if parameter["in"] in ("path", "query"):
            assert "_" not in parameter["name"], name
    for path in doc["paths"]:
        assert "_" not in path, path
    release = doc["components"]["schemas"]["Release"]
    assert "repositoryId" in release["properties"]
    assert "repositoryId" in release["required"]


def test_the_contract_files_are_version_0_2_0_and_still_draft() -> None:
    info = _contract()["info"]
    assert (info["version"], info["x-status"]) == ("0.2.0", "draft")
    tools = json.loads(TOOLS.read_text("utf-8"))
    assert (tools["version"], tools["status"]) == ("0.2.0", "draft")


def test_every_success_is_the_envelope() -> None:
    """Decision 0023 G2: every 2xx response is {success, data, meta}, application/json only; meta
    carries the seven members and page is null except on the three lists."""
    doc = _contract()
    schemas = doc["components"]["schemas"]
    for path, item in doc["paths"].items():
        for method, op in item.items():
            for status, response in op["responses"].items():
                if int(status) >= 300:
                    continue
                assert list(response["content"]) == ["application/json"], (method, path)
                envelope = _resolve(doc, response["content"]["application/json"]["schema"])
                assert envelope["required"] == ["success", "data", "meta"], (method, path)
                assert envelope["additionalProperties"] is False
                meta = _resolve(doc, envelope["properties"]["meta"])
                assert list(meta["properties"]) == [
                    "requestId",
                    "traceId",
                    "timestamp",
                    "path",
                    "status",
                    "page",
                ]
                assert meta["required"] == ["requestId", "timestamp", "path", "status", "page"]
                is_list = op["operationId"] in (
                    "list_releases",
                    "list_commands",
                    "admin_list_commands",
                )
                page = meta["properties"]["page"]
                want = "#/components/schemas/" + ("Page" if is_list else "NullValue")
                assert page == {"$ref": want}, (method, path)
    assert list(schemas["Page"]["properties"]) == ["limit", "nextCursor"]


# --- served bodies ----------------------------------------------------------------------------


@asynccontextmanager
async def _sdk(http: httpx2.AsyncClient) -> AsyncIterator[Client]:
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as c:
        yield c


TRACE = "0af7651916cd43dd8448eb211c80319c"


def _arguments(action: Action) -> dict[str, str]:
    op = next(o for o in OPERATIONS if o.action is action)
    values = {
        "projectId": str(PROJECT),
        "releaseId": str(RELEASE),
        "commandId": str(COMMAND),
        "idempotencyKey": "k1",
    }
    return {name: values[name] for name in op.required()}


async def _served(action: Action, *, full: bool) -> tuple[int, Any, Any]:
    """The REST status and body and the MCP structured content of ``action``; with every optional
    field (``full``) the request is traced, so that meta carries traceId."""
    op = next(o for o in OPERATIONS if o.action is action)
    path = op.path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)
    async with client(build(FakeOperations(full=full), trace_id=TRACE if full else None)) as http:
        rest = await http.request(op.method, path, headers={"Idempotency-Key": "k1"})
    async with client(build(FakeOperations(full=full))) as http, _sdk(http) as sdk:
        result = await sdk.call_tool(action.value, _arguments(action))
    assert not result.is_error, (action, result)
    return rest.status_code, rest.json(), result.structured_content


def _schema(doc: dict[str, Any], action: Action, status: int) -> dict[str, Any]:
    path, method = next(
        (p, m)
        for p, item in doc["paths"].items()
        for m, o in item.items()
        if o["operationId"] == action.value
    )
    response = doc["paths"][path][method]["responses"][str(status)]
    schema: dict[str, Any] = response["content"]["application/json"]["schema"]
    return schema


def _resolve(doc: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = doc["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _properties(
    doc: dict[str, Any], schema: dict[str, Any], body: object, where: str
) -> tuple[set[str], set[str]]:
    """(declared, used): the property paths of every object schema that ``body`` reaches, and the
    paths ``body`` sets. An array item's path is shared by all its items."""
    schema = _resolve(doc, schema)
    declared: set[str] = set()
    used: set[str] = set()
    if schema.get("type") == "object" and isinstance(body, dict):
        for name, sub in schema.get("properties", {}).items():
            path = f"{where}.{name}"
            declared.add(path)
            if name in body:
                used.add(path)
                d, u = _properties(doc, sub, body[name], path)
                declared, used = declared | d, used | u
    elif schema.get("type") == "array" and isinstance(body, list):
        for item in body:
            d, u = _properties(doc, schema["items"], item, f"{where}[]")
            declared, used = declared | d, used | u
    return declared, used


def _unused(doc: dict[str, Any], schema: dict[str, Any], body: object, where: str) -> set[str]:
    """The declared properties that ``body`` never sets, anywhere it reaches."""
    declared, used = _properties(doc, schema, body, where)
    return declared - used


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [True, False], ids=["every optional field", "no optional field"])
@pytest.mark.parametrize("action", list(Action))
async def test_no_served_body_carries_an_old_name(action: Action, full: bool) -> None:
    _, rest, mcp = await _served(action, full=full)
    for body in (rest, mcp):
        _camel_keys(body)
        for text in _keys_and_strings(body):
            assert text not in OLD_FIELDS, (action, text)
            assert text != OLD_VALUE, (action, text)
        raw = json.dumps(body)
        for name in OLD_FIELDS:
            assert not _word(name).search(raw), (action, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_both_transports_serve_the_contracts_schema_and_all_of_it(action: Action) -> None:
    doc = _contract()
    missing: set[str] = set()
    for full in (True, False):
        status, rest, mcp = await _served(action, full=full)
        assert mcp == mcp_form(rest), action
        schema = _schema(doc, action, status)
        validator = jsonschema.Draft7Validator(
            {**schema, "components": doc["components"]}, format_checker=jsonschema.FormatChecker()
        )
        validator.validate(rest)
        envelope = _resolve(doc, schema)
        if isinstance(rest["data"], list):
            mcp_schema: dict[str, Any] = {
                "type": "object",
                "additionalProperties": False,
                "required": ["items", "page"],
                "properties": {
                    "items": envelope["properties"]["data"],
                    "page": {"$ref": "#/components/schemas/Page"},
                },
            }
        else:
            mcp_schema = envelope["properties"]["data"]
        jsonschema.Draft7Validator(
            {**mcp_schema, "components": doc["components"]},
            format_checker=jsonschema.FormatChecker(),
        ).validate(mcp)
        if full:
            missing = _unused(doc, schema, rest, action.value)
    assert missing == set(), action


# --- the error shape (decision 0018) ----------------------------------------------------------


PROBLEM_CONTENT: Final = {
    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}},
    "application/json": {"schema": {"$ref": "#/components/schemas/Problem"}},
}


def test_every_error_response_is_a_problem() -> None:
    doc = _contract()
    assert "Error" not in doc["components"]["schemas"]
    for path, item in doc["paths"].items():
        for method, op in item.items():
            errors = [status for status in op["responses"] if int(status) >= 400]
            assert errors, (method, path)
            for status in errors:
                response = op["responses"][status]
                while "$ref" in response:
                    response = doc["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
                assert response["content"] == PROBLEM_CONTENT, (method, path, status)


def test_the_problem_schema_is_the_decisions() -> None:
    problem = _contract()["components"]["schemas"]["Problem"]
    assert problem["type"] == "object"
    assert problem["additionalProperties"] is False
    assert problem["required"] == ["type", "title", "status", "code", "requestId", "errors"]
    # Notion 2.2's members in their order (decision 0023 G5).
    assert list(problem["properties"]) == [
        "type",
        "title",
        "status",
        "instance",
        "code",
        "requestId",
        "traceId",
        "errors",
        "commandId",
    ]
    assert problem["properties"]["type"]["pattern"] == "^urn:webblock:problem:[a-z0-9-]+$"
    assert problem["properties"]["code"]["pattern"] == "^[A-Z][A-Z0-9_]{0,99}$"
    assert problem["properties"]["commandId"] == {"$ref": "#/components/schemas/Uuid"}
    assert problem["properties"]["requestId"] == {"$ref": "#/components/schemas/RequestId"}
    field = _contract()["components"]["schemas"]["FieldError"]
    assert field["required"] == ["field", "code", "message"]
    assert field["properties"]["code"]["enum"] == [
        "REQUIRED",
        "INVALID_FORMAT",
        "OUT_OF_RANGE",
        "TOO_LONG",
    ]


def test_the_tool_file_says_an_error_result_is_the_problem() -> None:
    description = json.loads(TOOLS.read_text("utf-8"))["description"]
    for phrase in ("RFC 9457 Problem Details", "schema Problem", "without instance"):
        assert phrase in description, phrase


def test_the_tool_file_names_the_same_operations_as_the_contract() -> None:
    tools = {t["name"] for t in json.loads(TOOLS.read_text("utf-8"))["tools"]}
    operations = {
        op["operationId"] for item in _contract()["paths"].values() for op in item.values()
    }
    assert tools == operations == {a.value for a in Action}
