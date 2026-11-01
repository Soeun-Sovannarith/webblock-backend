"""The admin routes and tools (decision 0023 G9, G10, G13; Notion "Web Block architecture" 2.4 and
5.3: admin pages call admin endpoints for suspend, resume and redeploy, and ADMIN unlocks them).

Under ``/v1/admin/projects/{projectId}``: ``POST hosting/suspend``, ``POST hosting/resume`` and
``POST redeploy`` (202, an ``Idempotency-Key``, no body or ``{}``) and ``GET commands`` (200, paged
like the client list, with ``requestedBySubject``). Only an ``ADMIN`` takes them, on any project and
without the Project access check; anyone else is 403 ``ACCESS_DENIED`` before any store or Project
call, so the answer says nothing about the project (Review Focus 1). The production profile serves
the admin read and refuses the three mutations as it refuses every mutation (task H2)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import (
    ADMIN_SUBJECT,
    BASE,
    BEARER_ADMIN,
    BEARER_ALL,
    CORR,
    build,
    client,
    mcp_form,
)
from tests.unit.presentation.fakes import (
    COMMAND,
    PROJECT,
    RELEASE,
    REQUESTER,
    FakeOperations,
    admin_command_page,
)
from tests.unit.presentation.memory import MemoryStores, memory_operations

from provisioning.core.application import (
    OPERATION_COMMANDS,
    Action,
    AdminListCommandsQuery,
    AuditActorKind,
    Principal,
    ProjectAccessDenied,
)
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import CommandKind, CommandStatus, OfflineReason, ReleaseStatus
from provisioning.hosts.shared.open_contracts import api_operations
from provisioning.presentation.operations import lookup

UNKNOWN = UUID("0190a1b2-0000-7000-8000-0000000000ff")
ADMIN_ACTIONS = (
    Action.SUSPEND_WEBSITE,
    Action.RESUME_WEBSITE,
    Action.REDEPLOY_RELEASE,
    Action.ADMIN_LIST_COMMANDS,
)
MUTATIONS = ADMIN_ACTIONS[:3]
PATHS = {
    Action.SUSPEND_WEBSITE: ("POST", "/v1/admin/projects/{projectId}/hosting/suspend"),
    Action.RESUME_WEBSITE: ("POST", "/v1/admin/projects/{projectId}/hosting/resume"),
    Action.REDEPLOY_RELEASE: ("POST", "/v1/admin/projects/{projectId}/redeploy"),
    Action.ADMIN_LIST_COMMANDS: ("GET", "/v1/admin/projects/{projectId}/commands"),
}
KEY = {"Idempotency-Key": "k1"}


def _path(action: Action, project_id: UUID = PROJECT) -> str:
    return PATHS[action][1].format(projectId=project_id)


async def _call(
    http: httpx2.AsyncClient, action: Action, project_id: UUID = PROJECT, **kwargs: Any
) -> httpx2.Response:
    method = PATHS[action][0]
    headers = KEY if method == "POST" else {}
    return await http.request(method, _path(action, project_id), headers=headers, **kwargs)


@asynccontextmanager
async def _sdk(http: httpx2.AsyncClient) -> AsyncIterator[Client]:
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as c:
        yield c


def _arguments(action: Action, project_id: UUID = PROJECT) -> dict[str, str]:
    if action is Action.ADMIN_LIST_COMMANDS:
        return {"projectId": str(project_id)}
    return {"projectId": str(project_id), "idempotencyKey": "k1"}


def test_the_operations_are_the_admin_paths() -> None:
    for action, (method, path) in PATHS.items():
        op = lookup(action)
        assert op is not None
        assert (op.method, op.path) == (method, path)
        assert op.is_mutation() == (method == "POST")


# --- the shape, against a fake service --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("action", MUTATIONS)
async def test_each_admin_mutation_answers_202_in_the_envelope(action: Action) -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await _call(http, action)
        with_empty_object = await http.post(
            _path(action),
            headers={**KEY, "Content-Type": "application/json"},
            content=b"{}",
        )
        with_a_field = await http.post(
            _path(action),
            headers={**KEY, "Content-Type": "application/json"},
            content=b'{"releaseId":"x"}',
        )
    assert response.status_code == 202, response.text
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "success": True,
        "data": {
            "commandId": str(COMMAND),
            "status": "accepted",
            "statusUrl": f"/v1/projects/{PROJECT}/commands/{COMMAND}",
        },
        "meta": {
            "requestId": CORR,
            "timestamp": response.json()["meta"]["timestamp"],
            "path": _path(action),
            "status": 202,
            "page": None,
        },
    }
    assert with_empty_object.status_code == 202
    assert with_a_field.status_code == 400
    assert [(e["field"], e["code"]) for e in with_a_field.json()["errors"]] == [
        ("body", "INVALID_FORMAT")
    ]
    # One call per accepted request; the refused body never reached the service.
    p = service.calls[0][1]
    sent = OPERATION_COMMANDS[action](p, PROJECT, "k1", CORR, AuditActorKind.USER)
    assert service.calls == [(action, p, sent), (action, p, sent)]


@pytest.mark.asyncio
async def test_the_admin_read_answers_a_page_with_the_requester() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        first = await _call(http, Action.ADMIN_LIST_COMMANDS)
        cursor = first.json()["meta"]["page"]["nextCursor"]
        second = await http.get(
            _path(Action.ADMIN_LIST_COMMANDS), params={"limit": "5", "cursor": cursor}
        )
        old_names = await http.get(_path(Action.ADMIN_LIST_COMMANDS), params={"page_size": "5"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["meta"]["page"] == {"limit": 20, "nextCursor": cursor}
    assert [item.get("requestedBySubject") for item in body["data"]] == [REQUESTER, None]
    assert "requestedBySubject" not in body["data"][1]
    assert second.status_code == 200
    assert second.json()["meta"]["page"]["limit"] == 5
    assert old_names.status_code == 400
    assert old_names.json()["errors"][0]["field"] == "query"
    p = service.calls[0][1]
    assert [request for _, _, request in service.calls] == [
        AdminListCommandsQuery(p, PROJECT, 20, None),
        AdminListCommandsQuery(p, PROJECT, 5, 6),
    ]


@pytest.mark.asyncio
async def test_an_unknown_admin_path_is_resource_not_found() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        for method, path in (
            ("POST", f"/v1/admin/projects/{PROJECT}/hosting/delete"),
            ("GET", f"/v1/admin/projects/{PROJECT}/hosting/suspend"),
            ("POST", f"/v1/admin/projects/{PROJECT}/commands"),
            ("GET", f"/v1/admin/projects/{PROJECT}"),
        ):
            response = await http.request(method, path, headers=KEY)
            assert response.status_code == 404, (method, path)
            assert response.json()["code"] == "RESOURCE_NOT_FOUND"
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ADMIN_ACTIONS)
async def test_each_admin_tool_answers_what_rest_answers(action: Action) -> None:
    rest_service, mcp_service = FakeOperations(), FakeOperations()
    async with client(build(rest_service)) as http:
        rest = await _call(http, action)
    async with client(build(mcp_service)) as http, _sdk(http) as sdk:
        result = await sdk.call_tool(action.value, _arguments(action))
    assert not result.is_error
    assert result.structured_content == mcp_form(rest.json())
    assert mcp_service.calls == rest_service.calls


@pytest.mark.asyncio
async def test_only_the_admin_read_carries_the_requester() -> None:
    """The client command bodies never carry requestedBySubject, even when the service hands them
    an admin view with a requester; the admin read always shows it (on REST and MCP)."""
    admin_view = admin_command_page(full=True).items[0]
    assert admin_view.requested_by_subject == REQUESTER
    service = FakeOperations(
        results={
            Action.GET_COMMAND: lambda _r: admin_view,
            Action.LIST_COMMANDS: lambda _r: replace(
                admin_command_page(full=True), next_after=None
            ),
        }
    )
    client_get = f"/v1/projects/{PROJECT}/commands/{COMMAND}"
    client_list = f"/v1/projects/{PROJECT}/commands"
    async with client(build(service)) as http:
        rest_get = (await http.get(client_get)).json()["data"]
        rest_list = (await http.get(client_list)).json()["data"]
        rest_admin = (await _call(http, Action.ADMIN_LIST_COMMANDS)).json()["data"]
    async with client(build(service)) as http, _sdk(http) as sdk:
        mcp_get = (
            await sdk.call_tool(
                "get_command", {"projectId": str(PROJECT), "commandId": str(COMMAND)}
            )
        ).structured_content
        mcp_list = (
            await sdk.call_tool("list_commands", {"projectId": str(PROJECT)})
        ).structured_content
        mcp_admin = (
            await sdk.call_tool("admin_list_commands", {"projectId": str(PROJECT)})
        ).structured_content
    assert mcp_get is not None and mcp_list is not None and mcp_admin is not None
    for body in (rest_get, mcp_get, *rest_list, *mcp_list["items"]):
        assert "requestedBySubject" not in body
        assert REQUESTER not in json.dumps(body)
    for items in (rest_admin, mcp_admin["items"]):
        assert items[0]["requestedBySubject"] == REQUESTER


# --- the gate, through the real service --------------------------------------------------------


@dataclass
class _Recorder:
    """Records every method called on the wrapped object, then delegates."""

    target: object
    calls: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.target, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            return attr(*args, **kwargs)

        return call


@dataclass
class _RecordingAccess:
    """Project access that records each call and denies, so a call would also show as a 404."""

    calls: list[tuple[UUID, Action]] = field(default_factory=list)

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        self.calls.append((project_id, action))
        raise ProjectAccessDenied("denied")


def _real(
    stores: MemoryStores, access: _RecordingAccess, *, production: bool = False
) -> tuple[Any, _Recorder]:
    """The real Service over recording stores; ``production`` gives it the production profile's
    operations."""
    recorder = _Recorder(stores)
    service = memory_operations(
        recorder,  # type: ignore[arg-type]  # the recorder delegates every store method
        access=access,
        enabled=api_operations(Profile.PRODUCTION if production else Profile.PROTOTYPE),
    )
    return service, recorder


def _without_ids(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in ("requestId", "traceId", "instance")}


@pytest.mark.asyncio
async def test_user_on_admin_path_is_403_before_any_call() -> None:
    """Review Focus 1: a USER (every action's scope, no ADMIN role) on any admin path, for a
    project that exists and one that does not, is 403 ACCESS_DENIED; neither the stores nor the
    Project service is asked, and the two answers are the same bytes apart from requestId,
    traceId and instance, on REST and on MCP."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.LIVE)
    access = _RecordingAccess()
    service, recorder = _real(stores, access)
    for action in ADMIN_ACTIONS:
        rest: list[httpx2.Response] = []
        mcp: list[dict[str, Any]] = []
        async with client(build(service, request_ids=True), token=BEARER_ALL) as http:
            for project_id in (PROJECT, UNKNOWN):
                rest.append(await _call(http, action, project_id))
        async with (
            client(build(service, request_ids=True), token=BEARER_ALL) as http,
            _sdk(http) as sdk,
        ):
            for project_id in (PROJECT, UNKNOWN):
                result = await sdk.call_tool(action.value, _arguments(action, project_id))
                assert result.is_error and result.structured_content is not None
                mcp.append(result.structured_content)
        for response in rest:
            assert response.status_code == 403, (action, response.text)
            assert response.json()["code"] == "ACCESS_DENIED"
            assert response.json()["errors"] == []
        assert rest[0].json()["requestId"] != rest[1].json()["requestId"]
        assert _without_ids(rest[0].json()) == _without_ids(rest[1].json())
        assert rest[0].headers["content-type"] == rest[1].headers["content-type"]
        assert rest[0].json()["instance"] == _path(action, PROJECT)
        assert [(m["status"], m["code"]) for m in mcp] == [(403, "ACCESS_DENIED")] * 2
        assert _without_ids(mcp[0]) == _without_ids(mcp[1]) == _without_ids(rest[0].json())
    assert recorder.calls == []
    assert access.calls == []
    assert stores.commands == []


@pytest.mark.asyncio
async def test_an_admin_acts_on_any_project_without_the_project_check() -> None:
    """An ADMIN suspends, resumes and redeploys a project it does not own; the Project service is
    never asked. The stored hosting key is admin:<key>, the reason admin_suspended and the
    requester the admin's subject; the admin read shows that subject."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.LIVE)
    stores.hosting[PROJECT] = replace(stores.hosting[PROJECT], live_release_id=RELEASE)
    access = _RecordingAccess()
    service, _ = _real(stores, access)
    async with client(build(service), token=BEARER_ADMIN) as http:
        suspended = await _call(http, Action.SUSPEND_WEBSITE)
        assert suspended.status_code == 202, suspended.text
        replayed = await _call(http, Action.SUSPEND_WEBSITE)
        busy = await _call(http, Action.REDEPLOY_RELEASE)
        stores.commands[0] = replace(stores.commands[0], status=CommandStatus.SUCCEEDED)
        redeployed = await _call(http, Action.REDEPLOY_RELEASE)
        listed = await _call(http, Action.ADMIN_LIST_COMMANDS)
        no_project = await _call(http, Action.REDEPLOY_RELEASE, UNKNOWN)
        nothing_listed = await _call(http, Action.ADMIN_LIST_COMMANDS, UNKNOWN)
    assert replayed.json()["data"] == suspended.json()["data"]
    assert (busy.status_code, busy.json()["code"]) == (409, "COMMAND_IN_PROGRESS")
    assert redeployed.status_code == 202, redeployed.text
    suspend, redeploy = stores.commands
    assert (suspend.kind, suspend.idempotency_key) == (CommandKind.SUSPEND_HOSTING, "admin:k1")
    assert suspend.hosting_reason is OfflineReason.ADMIN_SUSPENDED
    assert (redeploy.kind, redeploy.release_id, redeploy.idempotency_key) == (
        CommandKind.REDEPLOY,
        RELEASE,
        "k1",
    )
    assert {suspend.requested_by_subject, redeploy.requested_by_subject} == {ADMIN_SUBJECT}
    assert listed.status_code == 200
    assert [c["kind"] for c in listed.json()["data"]] == ["redeploy", "suspend_hosting"]
    assert {c["requestedBySubject"] for c in listed.json()["data"]} == {ADMIN_SUBJECT}
    assert (no_project.status_code, no_project.json()["code"]) == (422, "REDEPLOY_NOT_ALLOWED")
    assert (nothing_listed.status_code, nothing_listed.json()["data"]) == (200, [])
    assert access.calls == []


@pytest.mark.asyncio
async def test_a_too_long_admin_key_names_the_header_on_rest_and_the_argument_on_mcp() -> None:
    """A suspend or resume key is at most 249 bytes, so the stored admin:<key> keeps 255 (0023
    G11): one byte more is TOO_LONG, named as each transport's client sent it."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.LIVE)
    service, _ = _real(stores, _RecordingAccess())
    long_key = "k" * 250
    for action in (Action.SUSPEND_WEBSITE, Action.RESUME_WEBSITE):
        async with client(build(service), token=BEARER_ADMIN) as http:
            rest = await http.post(_path(action), headers={"Idempotency-Key": long_key})
            fits = await http.post(_path(action), headers={"Idempotency-Key": "k" * 249})
        async with client(build(service), token=BEARER_ADMIN) as http, _sdk(http) as sdk:
            result = await sdk.call_tool(
                action.value, {"projectId": str(PROJECT), "idempotencyKey": long_key}
            )
        assert (rest.status_code, rest.json()["code"]) == (400, "VALIDATION_FAILED")
        assert [(e["field"], e["code"]) for e in rest.json()["errors"]] == [
            ("Idempotency-Key", "TOO_LONG")
        ]
        mcp = result.structured_content
        assert result.is_error and mcp is not None
        assert [(e["field"], e["code"]) for e in mcp["errors"]] == [("idempotencyKey", "TOO_LONG")]
        assert long_key not in rest.text + json.dumps(mcp)
        assert fits.status_code == 202, fits.text
        stores.commands.clear()


@pytest.mark.asyncio
async def test_in_production_only_the_admin_read_is_served() -> None:
    """Decision 0023 G13: production refuses the three admin mutations exactly as it refuses a
    client mutation (503 OPERATION_UNAVAILABLE, nothing stored) and serves the admin read."""
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.LIVE)
    stores.hosting[PROJECT] = replace(stores.hosting[PROJECT], live_release_id=RELEASE)
    access = _RecordingAccess()
    service, recorder = _real(stores, access, production=True)
    async with client(build(service), token=BEARER_ADMIN) as http:
        deploy = await http.post(f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy", headers=KEY)
        refusals = [await _call(http, action) for action in MUTATIONS]
        read = await _call(http, Action.ADMIN_LIST_COMMANDS)
    assert (deploy.status_code, deploy.json()["code"]) == (503, "OPERATION_UNAVAILABLE")
    for action, response in zip(MUTATIONS, refusals, strict=True):
        assert response.status_code == deploy.status_code, action
        assert response.headers["content-type"] == deploy.headers["content-type"]
        assert _without_ids(response.json()) == _without_ids(deploy.json()), action
    assert (read.status_code, read.json()["data"]) == (200, [])
    assert stores.commands == []
    assert recorder.calls == ["list_commands"]
    assert access.calls == []
