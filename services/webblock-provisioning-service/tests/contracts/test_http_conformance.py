"""The served REST surface against src/contracts/openapi.yaml (decision 0013 section 9 S5): the
routes are the contract's operations plus /mcp and nothing else, FastAPI's own normalised operations
equal the contract's, every route requires a token, and every success and error body validates
against the operation's response schema, and its content type is one the response declares:
application/json for a success, application/problem+json (or application/json, for a client that
accepts only that) for an error, both with the Problem schema (decision 0018)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx2
import jsonschema  # type: ignore[import-untyped]  # jsonschema (pulled in by mcp) has no stubs in the lock; tests only
import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from tests.unit.presentation.apps import build, client
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations, command_view

from provisioning.core.application import (
    Action,
    ActionDenied,
    BuildInProgress,
    Contention,
    DependencyUnavailable,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    ProjectAccessDenied,
    ReleaseNotBuildable,
    StoreUnreachable,
    Timeout,
)
from provisioning.core.domain import (
    CommandKind,
    HostingOffline,
    IdempotencyConflict,
    RouteNotStable,
)
from provisioning.presentation.http import served_routes
from provisioning.presentation.operations import OPERATIONS

CONTRACT = Path(__file__).resolve().parents[2] / "src" / "contracts" / "openapi.yaml"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def _contract() -> dict[str, Any]:
    with CONTRACT.open(encoding="utf-8") as f:
        doc: dict[str, Any] = yaml.safe_load(f)
    return doc


def _operations(doc: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (method.upper(), path, op["operationId"])
        for path, item in doc["paths"].items()
        for method, op in item.items()
    }


def test_the_routes_are_the_contract_plus_mcp_and_nothing_else() -> None:
    app = build(FakeOperations()).app
    want = {(m, p) for m, p, _ in _operations(_contract())} | {("*", "/mcp")}
    assert set(served_routes(app)) == want
    assert len(served_routes(app)) == len(want)


def test_fastapi_normalised_operations_equal_the_contracts() -> None:
    app = build(FakeOperations()).app
    served = {
        (method.upper(), path, op["operationId"])
        for path, item in app.openapi()["paths"].items()
        for method, op in item.items()
    }
    assert served == _operations(_contract())


def _concrete(path: str) -> str:
    return path.format(projectId=PROJECT, releaseId=RELEASE, commandId=COMMAND)


@pytest.mark.asyncio
async def test_every_route_requires_a_token() -> None:
    service = FakeOperations()
    async with client(build(service), token=None) as http:
        for op in OPERATIONS:
            response = await http.request(op.method, _concrete(op.path))
            assert response.status_code == 401, op.action
        assert (await http.post("/mcp", content=b"{}")).status_code == 401
        assert (await http.get("/anything")).status_code == 401
    assert service.calls == []


@pytest.mark.asyncio
async def test_no_other_method_is_served_on_a_contract_path() -> None:
    doc = _contract()
    async with client(build(FakeOperations())) as http:
        for path, item in doc["paths"].items():
            declared = {m.upper() for m in item}
            for method in METHODS:
                if method in declared:
                    continue
                response = await http.request(method, _concrete(path))
                assert response.status_code == 404, (method, path)


# --- bodies against the contract's schemas ---------------------------------------------------


def _declared(
    doc: dict[str, Any], action: Action, status: int
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """The media types and the one schema the contract declares for ``action`` and ``status``.
    An error lists ``application/problem+json`` and ``application/json`` with the same schema."""
    path, method = next(
        (p, m)
        for p, item in doc["paths"].items()
        for m, o in item.items()
        if o["operationId"] == action.value
    )
    response = doc["paths"][path][method]["responses"][str(status)]
    while "$ref" in response:
        response = doc["components"]["responses"][response["$ref"].rsplit("/", 1)[1]]
    content = response["content"]
    first, *others = [item["schema"] for item in content.values()]
    assert all(other == first for other in others), (action, status)
    schema: dict[str, Any] = first
    return tuple(content), schema


def _validator(doc: dict[str, Any], action: Action, status: int) -> jsonschema.Draft7Validator:
    _, schema = _declared(doc, action, status)
    root = {**schema, "components": doc["components"]}
    return jsonschema.Draft7Validator(root, format_checker=jsonschema.FormatChecker())


def _conforms(doc: dict[str, Any], action: Action, response: httpx2.Response) -> None:
    """The response's content type is a declared media type and its body the declared schema."""
    media_types, _ = _declared(doc, action, response.status_code)
    assert response.headers["content-type"] in media_types, (action, response.status_code)
    _validator(doc, action, response.status_code).validate(response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [True, False], ids=["every optional field", "no optional field"])
@pytest.mark.parametrize("action", list(Action))
async def test_success_bodies_validate(action: Action, full: bool) -> None:
    doc = _contract()
    op = next(o for o in OPERATIONS if o.action is action)
    async with client(build(FakeOperations(full=full))) as http:
        response = await http.request(
            op.method, _concrete(op.path), headers={"Idempotency-Key": "k1"}
        )
    assert response.status_code in (200, 202)
    _conforms(doc, action, response)


@pytest.mark.asyncio
async def test_a_redeploy_command_body_validates() -> None:
    """A redeploy is never accepted in Phase A (decision 0021 D10), but the kind is in the contract,
    so a command read that carries it still conforms."""
    doc = _contract()
    op = next(o for o in OPERATIONS if o.action is Action.GET_COMMAND)
    redeploy = replace(command_view(full=True), kind=CommandKind.REDEPLOY)
    service = FakeOperations(results={Action.GET_COMMAND: lambda _: redeploy})
    async with client(build(service)) as http:
        response = await http.request(op.method, _concrete(op.path))
    assert response.status_code == 200
    assert response.json()["data"]["kind"] == "redeploy"
    _conforms(doc, Action.GET_COMMAND, response)


_ERRORS: list[BaseException] = [
    NotFound("x"),
    StoreUnreachable("x"),
    DependencyUnavailable("x", deadline=True),
    DependencyUnavailable("x", upstream_reason="PAYMENT_PAUSED"),
    ProjectAccessDenied("x"),
    HostingOffline("x"),
    Timeout("x"),
    ActionDenied("x"),
    IdempotencyConflict("x"),
    RouteNotStable("x"),
    OperationInProgress(UUID("0190a1b2-0000-7000-8000-0000000000c9")),
    LimitReached("x"),
    Contention("x"),
    DependencyUnavailable("x"),
    OperationDisabled("x"),
    ReleaseNotBuildable("x"),
    BuildInProgress("x"),
    RuntimeError("anything"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_error_bodies_validate_with_a_declared_status(action: Action) -> None:
    doc = _contract()
    op = next(o for o in OPERATIONS if o.action is action)
    for error in _ERRORS:
        async with client(build(FakeOperations(errors={action: error}))) as http:
            response = await http.request(
                op.method, _concrete(op.path), headers={"Idempotency-Key": "k1"}
            )
        assert op.declares(response.status_code), (action, error, response.status_code)
        _conforms(doc, action, response)


@pytest.mark.asyncio
async def test_an_error_for_accept_json_is_the_declared_application_json() -> None:
    """The default client sends ``*/*``, which gets application/problem+json; this pins the other
    branch: ``Accept: application/json`` gets application/json, which the contract declares for
    the response with the Problem schema (decision 0018, spec 6.3 of the Project service)."""
    doc = _contract()
    op = next(o for o in OPERATIONS if o.action is Action.DEPLOY_RELEASE)
    async with client(build(FakeOperations(errors={op.action: NotFound("x")}))) as http:
        response = await http.request(
            op.method,
            _concrete(op.path),
            headers={"Idempotency-Key": "k1", "Accept": "application/json"},
        )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    media_types, schema = _declared(doc, op.action, 404)
    assert "application/json" in media_types
    assert schema == {"$ref": "#/components/schemas/Problem"}
    _conforms(doc, op.action, response)


@pytest.mark.asyncio
async def test_transport_errors_validate() -> None:
    doc = _contract()
    op = next(o for o in OPERATIONS if o.action is Action.DEPLOY_RELEASE)
    async with client(build(FakeOperations())) as http:
        for headers, content in (
            ({}, b""),
            ({"Idempotency-Key": "k"}, b'{"a":1}'),
            ({"Idempotency-Key": "k", "Content-Type": "application/json"}, b"{" + b" " * 70 + b"}"),
        ):
            response = await http.post(_concrete(op.path), headers=headers, content=content)
            assert response.status_code in (400, 413)
            _conforms(doc, Action.DEPLOY_RELEASE, response)


def test_the_validator_rejects_a_wrong_body() -> None:
    # The schema check is not vacuous: the old flat Error body, an extra member, a detail member
    # and a type outside the URN namespace all fail it.
    validator = _validator(_contract(), Action.GET_DEPLOYMENT, 404)
    problem = {
        "type": "urn:webblock:problem:deployment-not-found",
        "title": "The project has no deployment.",
        "status": 404,
        "instance": "/v1/projects/p/deployment",
        "code": "DEPLOYMENT_NOT_FOUND",
        "requestId": "r-1",
        "errors": [],
    }
    validator.validate(problem)
    field = {"field": "projectId", "code": "INVALID_FORMAT", "message": "m"}
    validator.validate({**problem, "errors": [field], "traceId": "0" * 32})
    no_errors = {k: v for k, v in problem.items() if k != "errors"}
    no_request_id = {k: v for k, v in problem.items() if k != "requestId"}
    for wrong in (
        {"code": "DEPLOYMENT_NOT_FOUND", "message": "Not found."},
        {**problem, "extra": 1},
        {**problem, "detail": "release r is missing"},
        {**problem, "type": "https://example.com/not-found"},
        {**problem, "code": "not_found"},
        {**problem, "correlation_id": "r-1"},
        {**problem, "traceId": "not-a-trace"},
        {**problem, "errors": [{**field, "code": "BAD"}]},
        {**problem, "errors": [{**field, "value": "x"}]},
        no_errors,
        no_request_id,
    ):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(wrong)
    with pytest.raises(jsonschema.ValidationError):
        _validator(_contract(), Action.BUILD_RELEASE, 202).validate({"release_id": "x"})


def test_httpx2_is_the_client() -> None:
    assert httpx2.AsyncClient is not None


def test_the_envelope_validator_rejects_a_wrong_success() -> None:
    """The success schemas are not vacuous (decision 0023 G2, G4): a bare body, a missing meta
    member, a page on an object, a list without its page, a snake_case field and a timestamp
    without milliseconds all fail."""
    doc = _contract()
    meta = {
        "requestId": "r-1",
        "timestamp": "2026-10-25T01:02:03.045Z",
        "path": "/v1/projects/p/deployment",
        "status": 200,
        "page": None,
    }
    deployment = {
        "projectId": str(PROJECT),
        "lastCommandNumber": 0,
        "routingStatus": "stable",
        "hostingStatus": "online",
        "updatedAt": "2026-10-25T01:02:03Z",
    }
    good = {"success": True, "data": deployment, "meta": meta}
    validator = _validator(doc, Action.GET_DEPLOYMENT, 200)
    validator.validate(good)
    validator.validate({**good, "meta": {**meta, "traceId": "0" * 32}})
    no_path = {k: v for k, v in meta.items() if k != "path"}
    for wrong in (
        deployment,
        {**good, "success": False},
        {**good, "meta": no_path},
        {**good, "meta": {**meta, "page": {"limit": 20, "nextCursor": None}}},
        {**good, "meta": {**meta, "timestamp": "2026-10-25T01:02:03Z"}},
        {**good, "meta": {**meta, "status": 404}},
        {**good, "data": {**deployment, "project_id": str(PROJECT)}},
        {**good, "extra": 1},
    ):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(wrong)
    lists = _validator(doc, Action.LIST_COMMANDS, 200)
    page = {"limit": 20, "nextCursor": None}
    lists.validate({"success": True, "data": [], "meta": {**meta, "page": page}})
    lists.validate(
        {"success": True, "data": [], "meta": {**meta, "page": {**page, "nextCursor": "Yw"}}}
    )
    for wrong_page in (None, {"limit": 20}, {**page, "nextCursor": ""}, {**page, "limit": 51}):
        with pytest.raises(jsonschema.ValidationError):
            lists.validate({"success": True, "data": [], "meta": {**meta, "page": wrong_page}})
