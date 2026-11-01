"""Problems on the public listener (decision 0023 G5, G6, G8a): the content type follows Accept,
the two concealment 404s are the same bytes, an unknown route is RESOURCE_NOT_FOUND, the 404 of a
missing release, command or deployment names it once access passed, StoreUnreachable is 503
DEPENDENCY_UNAVAILABLE, ``requestId`` is the X-Request-Id, and a 400 lists the bad fields without
the caller's value."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx2
import pytest
from tests.unit.presentation.apps import build, client
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations
from tests.unit.presentation.memory import MemoryStores, memory_operations

from provisioning.core.application import (
    Action,
    NotFound,
    Principal,
    ProjectAccessDenied,
    StoreUnreachable,
)
from provisioning.core.domain import ReleaseStatus
from provisioning.presentation.operations import OPERATIONS, FieldCode, field_message

UNKNOWN = UUID("0190a1b2-0000-7000-8000-0000000000f1")
NOT_MINE = UUID("0190a1b2-0000-7000-8000-0000000000f2")
DEPLOY = f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy"
DEPLOYMENT = f"/v1/projects/{PROJECT}/deployment"


def _json(response: httpx2.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


def _field(name: str, code: FieldCode) -> dict[str, str]:
    return {"field": name, "code": code.value, "message": field_message(code)}


@pytest.mark.asyncio
async def test_problem_content_type_follows_accept() -> None:
    """Review Focus 5: a client that accepts only application/json gets a Problem as
    application/json and a success as application/json; the default gets problem+json."""
    service = FakeOperations(errors={Action.GET_RELEASE: NotFound("x")})
    release = f"/v1/projects/{PROJECT}/releases/{RELEASE}"
    async with client(build(service)) as http:
        only_json = {"Accept": "application/json"}
        problem = await http.get(release, headers=only_json)
        success = await http.get(DEPLOYMENT, headers=only_json)
        default = await http.get(release)
    assert problem.status_code == 404
    assert problem.headers["content-type"] == "application/json"
    assert success.status_code == 200
    assert success.headers["content-type"] == "application/json"
    assert default.headers["content-type"] == "application/problem+json"
    assert problem.content == default.content


class _Access:
    """Project's answers: an unknown project and one the caller does not manage are both denied,
    each with its own reason; any other project is allowed."""

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        del principal, action
        if project_id == UNKNOWN:
            raise ProjectAccessDenied("the project service denied access: NOT_FOUND")
        if project_id == NOT_MINE:
            raise ProjectAccessDenied("the project service denied access: NOT_MANAGER")


def _concrete(path: str, project: UUID) -> str:
    return path.format(projectId=project, releaseId=RELEASE, commandId=COMMAND)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op",
    [op for op in OPERATIONS if not op.action.is_admin_only()],
    ids=lambda op: op.action.value,
)
async def test_concealment_404s_are_byte_identical(op: Any) -> None:
    """No access and an unknown project answer the same bytes apart from ``instance``, which is
    the path the caller sent; the project that exists has a release and a command. The admin
    paths ask no project: a caller without ADMIN is 403 there before any lookup, for any project
    (tests/unit/presentation/http/test_admin_routes.py)."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT, project_id=NOT_MINE)
    built = build(memory_operations(stores, access=_Access()))
    bodies = []
    async with client(built) as http:
        for project in (UNKNOWN, NOT_MINE):
            response = await http.request(
                op.method, _concrete(op.path, project), headers={"Idempotency-Key": "k1"}
            )
            assert response.status_code == 404, response.text
            body = _json(response)
            assert body.pop("instance") == _concrete(op.path, project)
            assert body["code"] == "PROJECT_NOT_FOUND"
            bodies.append(json.dumps(body).encode())
    assert bodies[0] == bodies[1]
    assert str(NOT_MINE) not in bodies[1].decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "code"),
    [
        (Action.GET_RELEASE, "RELEASE_NOT_FOUND"),
        (Action.GET_RELEASE_DIAGNOSTICS, "RELEASE_NOT_FOUND"),
        (Action.DEPLOY_RELEASE, "RELEASE_NOT_FOUND"),
        (Action.PROMOTE_RELEASE, "RELEASE_NOT_FOUND"),
        (Action.ROLLBACK_RELEASE, "RELEASE_NOT_FOUND"),
        (Action.BUILD_RELEASE, "RELEASE_NOT_FOUND"),
        (Action.GET_COMMAND, "COMMAND_NOT_FOUND"),
        (Action.GET_DEPLOYMENT, "DEPLOYMENT_NOT_FOUND"),
    ],
)
async def test_after_access_a_missing_resource_names_itself(action: Action, code: str) -> None:
    op = next(o for o in OPERATIONS if o.action is action)
    service = FakeOperations(errors={action: NotFound("missing")})
    async with client(build(service)) as http:
        response = await http.request(
            op.method, _concrete(op.path, PROJECT), headers={"Idempotency-Key": "k1"}
        )
    assert response.status_code == 404
    assert _json(response)["code"] == code
    assert _json(response)["type"] == "urn:webblock:problem:" + code.lower().replace("_", "-")


@pytest.mark.asyncio
async def test_unknown_route_is_resource_not_found() -> None:
    async with client(build(FakeOperations())) as http:
        unknown = await http.get(f"/v1/projects/{PROJECT}/nothing-here")
        wrong_method = await http.delete(DEPLOYMENT)
        outside = await http.get("/anything")
    for response in (unknown, wrong_method, outside):
        assert response.status_code == 404
        body = _json(response)
        assert body["code"] == "RESOURCE_NOT_FOUND"
        assert body["errors"] == []
    assert _json(unknown)["instance"] == f"/v1/projects/{PROJECT}/nothing-here"


@pytest.mark.asyncio
async def test_store_unreachable_is_503_dependency_unavailable() -> None:
    service = FakeOperations(errors={Action.GET_DEPLOYMENT: StoreUnreachable("db down")})
    async with client(build(service)) as http:
        response = await http.get(DEPLOYMENT)
    assert response.status_code == 503
    assert _json(response)["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "db down" not in response.text


@pytest.mark.asyncio
async def test_request_id_is_the_x_request_id() -> None:
    service = FakeOperations(errors={Action.GET_DEPLOYMENT: NotFound("x")})
    async with client(build(service, request_ids=True)) as http:
        sent = await http.get(DEPLOYMENT, headers={"X-Request-Id": "req-abc.1"})
        made = await http.get(DEPLOYMENT)
    assert _json(sent)["requestId"] == "req-abc.1"
    assert _json(made)["requestId"] == made.headers["x-request-id"]
    assert "traceId" not in _json(made)


CALLER_VALUE = "caller-value-zq"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "headers", "content", "errors"),
    [
        (
            f"/v1/projects/{CALLER_VALUE}/releases/{RELEASE}/deploy",
            {"Idempotency-Key": "k1"},
            b"",
            [_field("projectId", FieldCode.INVALID_FORMAT)],
        ),
        (
            f"/v1/projects/{PROJECT}/releases/{str(RELEASE).upper()}/deploy",
            {"Idempotency-Key": "k1"},
            b"",
            [_field("releaseId", FieldCode.INVALID_FORMAT)],
        ),
        (DEPLOY, {}, b"", [_field("Idempotency-Key", FieldCode.REQUIRED)]),
        (DEPLOY, {"Idempotency-Key": ""}, b"", [_field("Idempotency-Key", FieldCode.REQUIRED)]),
        (
            DEPLOY,
            {"Idempotency-Key": CALLER_VALUE + "x" * 256},
            b"",
            [_field("Idempotency-Key", FieldCode.TOO_LONG)],
        ),
        (
            DEPLOY,
            {"Idempotency-Key": CALLER_VALUE + "‮"},
            b"",
            [_field("Idempotency-Key", FieldCode.INVALID_FORMAT)],
        ),
        (
            DEPLOY,
            {"Idempotency-Key": "k1", "Content-Type": "application/json"},
            b'{"' + CALLER_VALUE.encode() + b'":1}',
            [_field("body", FieldCode.INVALID_FORMAT)],
        ),
        (
            DEPLOY + f"?{CALLER_VALUE}=1",
            {"Idempotency-Key": "k1"},
            b"",
            [_field("query", FieldCode.INVALID_FORMAT)],
        ),
        (
            f"/v1/projects/{PROJECT}/releases?page_size=10&{CALLER_VALUE}=1",
            {},
            b"",
            [_field("query", FieldCode.INVALID_FORMAT)],
        ),
    ],
)
async def test_a_400_lists_the_bad_field_without_its_value(
    path: str, headers: dict[str, str], content: bytes, errors: list[dict[str, str]]
) -> None:
    service = FakeOperations()
    async with client(build(service, body_limit=1024)) as http:
        method = "GET" if "/releases?" in path else "POST"
        response = await http.request(
            method,
            path,
            headers=[(k.encode("ascii"), v.encode("utf-8")) for k, v in headers.items()],
            content=content,
        )
    assert response.status_code == 400, response.text
    body = _json(response)
    assert body["code"] == "VALIDATION_FAILED"
    assert body["errors"] == errors
    assert CALLER_VALUE not in json.dumps({k: v for k, v in body.items() if k != "instance"})
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_repeated_idempotency_key_is_invalid_format() -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.post(
            DEPLOY, headers=[("Idempotency-Key", "a"), ("Idempotency-Key", "b")]
        )
    assert response.status_code == 400
    assert _json(response)["errors"] == [_field("Idempotency-Key", FieldCode.INVALID_FORMAT)]
