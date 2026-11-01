"""REST and MCP over the real application service (decision 0014 section 21 rulings 3, 6 and 7):
the listings' query parameters, the ten tools, and the audit line of every mutation, written by the
host's ``LoggerAuditLog``, which names the token's subject and never the token."""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.unit.presentation.apps import BASE, BEARER_ALL, BEARER_READ, build, client
from tests.unit.presentation.fakes import PROJECT, RELEASE, T0, FakeOperations
from tests.unit.presentation.memory import MemoryStores, memory_operations

from provisioning.core.application import (
    Action,
    AuditActorKind,
    ListCommandsQuery,
    ListReleasesQuery,
    ServiceAudit,
)
from provisioning.core.domain import ReleaseStatus
from provisioning.infrastructure.telemetry import LoggerAuditLog, new_logger
from provisioning.presentation.operations import OPERATIONS, FieldCode, field_message

LIST_RELEASES = f"/v1/projects/{PROJECT}/releases"
LIST_COMMANDS = f"/v1/projects/{PROJECT}/commands"


class _Clock:
    def now(self) -> datetime:
        return T0


def _audited(stores: MemoryStores) -> tuple[Any, io.StringIO]:
    lines = io.StringIO()
    audit = ServiceAudit(log=LoggerAuditLog(new_logger(lines, logging.INFO)), clock=_Clock())
    return build(memory_operations(stores, audit=audit)), lines


def _audit_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in stream.getvalue().splitlines() if line]
    return [r for r in records if r.get("audit") is True]


# --- listings over REST ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listings_page_through_the_real_service() -> None:
    """FR-38.1: the listings page through the real service."""
    stores = MemoryStores()
    for n in range(5):
        stores.add_release(UUID(int=0x1000 + n), ReleaseStatus.BUILT)
    built, _ = _audited(stores)
    async with client(built) as http:
        for n in range(3):
            response = await http.post(
                f"/v1/projects/{PROJECT}/releases/{UUID(int=0x1000 + n)}/deploy",
                headers={"Idempotency-Key": f"k{n}"},
            )
            assert response.status_code in (202, 409)
        first = (await http.get(LIST_RELEASES, params={"limit": "2"})).json()
        assert len(first["data"]) == 2
        assert first["meta"]["page"]["limit"] == 2
        second = (
            await http.get(
                LIST_RELEASES,
                params={"limit": "2", "cursor": first["meta"]["page"]["nextCursor"]},
            )
        ).json()
        third = (
            await http.get(
                LIST_RELEASES,
                params={"limit": "2", "cursor": second["meta"]["page"]["nextCursor"]},
            )
        ).json()
        # The nextCursor round trip ends with null on the last page (decision 0023 G4).
        assert third["meta"]["page"] == {"limit": 2, "nextCursor": None}
        assert third["meta"]["path"] == LIST_RELEASES
        ids = [i["releaseId"] for page in (first, second, third) for i in page["data"]]
        assert len(ids) == 5 == len(set(ids))
        commands = (await http.get(LIST_COMMANDS)).json()
        assert [c["commandNumber"] for c in commands["data"]] == [1]
        assert commands["meta"]["page"] == {"limit": 20, "nextCursor": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "field", "code"),
    [
        ("limit=0", "limit", FieldCode.OUT_OF_RANGE),
        ("limit=51", "limit", FieldCode.OUT_OF_RANGE),
        ("limit=x", "limit", FieldCode.INVALID_FORMAT),
        ("limit", "query", FieldCode.INVALID_FORMAT),
        ("limit=1&limit=2", "query", FieldCode.INVALID_FORMAT),
        ("cursor=%21", "cursor", FieldCode.INVALID_FORMAT),
        ("other=1", "query", FieldCode.INVALID_FORMAT),
        ("limit=1&" + "x" * 600, "query", FieldCode.INVALID_FORMAT),
        ("cursor=" + "%FF", "cursor", FieldCode.INVALID_FORMAT),
    ],
)
async def test_invalid_query_parameters_are_malformed(
    query: str, field: str, code: FieldCode
) -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.get(f"{LIST_COMMANDS}?{query}")
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"
    # A bad limit or cursor names its field; an unknown or repeated parameter is the field
    # query, its name never echoed.
    assert response.json()["errors"] == [
        {"field": field, "code": code.value, "message": field_message(code)}
    ]
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["page_size=5", "page_token=Yw", "page_size=5&page_token=Yw"])
async def test_old_paging_names_are_refused(query: str) -> None:
    """Review Focus 4 (lead ruling): the old names are unknown parameters, 400 VALIDATION_FAILED
    with the field query; the name is never echoed."""
    service = FakeOperations()
    async with client(build(service)) as http:
        for path in (LIST_RELEASES, LIST_COMMANDS):
            response = await http.get(f"{path}?{query}")
            assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_FAILED")
            assert [(e["field"], e["code"]) for e in response.json()["errors"]] == [
                ("query", "INVALID_FORMAT")
            ]
            assert "page_" not in response.text
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    ["Yw", "!!", "r" * 201, "cgAAAAAAAAAB", "YwAAAAAAAAAA"],
)
async def test_bad_cursor_is_a_field_error(cursor: str) -> None:
    """Review Focus 4: a malformed cursor, or one of the other list, is 400 VALIDATION_FAILED
    with the field cursor, and never reaches the service."""
    service = FakeOperations()
    async with client(build(service)) as http:
        for path in (LIST_RELEASES, LIST_COMMANDS):
            response = await http.get(path, params={"cursor": cursor})
            assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_FAILED")
            assert [(e["field"], e["code"]) for e in response.json()["errors"]] == [
                ("cursor", "INVALID_FORMAT")
            ]
            assert cursor not in response.text
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_query_on_an_operation_that_takes_none_is_malformed() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.get(f"/v1/projects/{PROJECT}/deployment?limit=1")
        assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_FAILED")
        assert [e["field"] for e in response.json()["errors"]] == ["query"]
        assert "limit" not in response.text
        response = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy?x=1",
            headers={"Idempotency-Key": "k"},
        )
        assert (response.status_code, response.json()["code"]) == (400, "VALIDATION_FAILED")
        assert [e["field"] for e in response.json()["errors"]] == ["query"]
    assert service.calls == []


@pytest.mark.asyncio
async def test_the_query_reaches_the_service() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        assert (await http.get(LIST_RELEASES, params={"limit": "3"})).status_code == 200
        assert (await http.get(LIST_COMMANDS)).status_code == 200
    p = service.calls[0][1]
    assert [request for _, _, request in service.calls] == [
        ListReleasesQuery(principal=p, project_id=PROJECT, page_size=3, after=None),
        ListCommandsQuery(principal=p, project_id=PROJECT, page_size=20, after=None),
    ]


@pytest.mark.asyncio
async def test_a_reader_may_list() -> None:
    async with client(build(FakeOperations()), token=BEARER_READ) as http:
        assert (await http.get(LIST_RELEASES)).status_code == 200
        assert (await http.get(LIST_COMMANDS)).status_code == 200


# --- the ten tools ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_lists_fourteen_tools_and_pages_with_an_integer_size() -> None:
    service = FakeOperations()
    built = build(service)
    async with (
        client(built) as http,
        Client(streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy") as session,
    ):
        tools = (await session.list_tools()).tools
        assert [t.name for t in tools] == [op.action.value for op in OPERATIONS]
        assert len(tools) == 14
        result = await session.call_tool("list_commands", {"projectId": str(PROJECT), "limit": 4})
        assert not result.is_error
        assert result.structured_content is not None
        assert list(result.structured_content) == ["items", "page"]
        assert result.structured_content["page"]["limit"] == 4
    p = service.calls[0][1]
    assert [request for _, _, request in service.calls] == [
        ListCommandsQuery(principal=p, project_id=PROJECT, page_size=4, after=None)
    ]


@pytest.mark.asyncio
async def test_rest_mutations_are_users_and_mcp_mutations_are_agents() -> None:
    service = FakeOperations()
    built = build(service)
    async with client(built) as http:
        await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy", headers={"Idempotency-Key": "k"}
        )
        async with Client(
            streamable_http_client(f"{BASE}/mcp", http_client=http), mode="legacy"
        ) as session:
            await session.call_tool(
                "promote_release",
                {"projectId": str(PROJECT), "releaseId": str(RELEASE), "idempotencyKey": "k"},
            )
            await session.call_tool(
                "build_release", {"projectId": str(PROJECT), "releaseId": str(RELEASE)}
            )
    assert service.actor_kinds == [
        AuditActorKind.USER,
        AuditActorKind.AGENT,
        AuditActorKind.AGENT,
    ]


# --- audit lines -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_mutation_writes_one_audit_line_with_the_subject_and_never_the_token() -> None:
    # Planted defect "a token in an audit line": an actor taken from the Authorization header
    # instead of the verified subject puts the bearer token into the line.
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    built, lines = _audited(stores)
    async with client(built) as http:
        accepted = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy", headers={"Idempotency-Key": "k"}
        )
        conflicted = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/promote", headers={"Idempotency-Key": "k"}
        )
        busy = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/promote", headers={"Idempotency-Key": "k2"}
        )
        await http.get(LIST_COMMANDS)  # a read: no audit line
        refused = await http.post(
            f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy",
            headers={"Idempotency-Key": "k3", "Authorization": f"Bearer {BEARER_READ}"},
        )
    assert [r.status_code for r in (accepted, conflicted, busy, refused)] == [202, 422, 409, 403]
    records = _audit_lines(lines)
    assert [(r["action"], r["result"], r.get("failure_code")) for r in records] == [
        ("deploy", "accepted", None),
        ("promote", "refused", "idempotency_conflict"),
        ("promote", "refused", "operation_in_progress"),
        ("deploy", "refused", "action_denied"),
    ]
    assert [r["actor"] for r in records] == ["alice", "alice", "alice", "reader"]
    assert {r["actor_kind"] for r in records} == {"user"}
    assert records[0]["command_id"] == accepted.json()["data"]["commandId"]
    assert all(r["project_id"] == str(PROJECT) for r in records)
    text = lines.getvalue()
    for token in (BEARER_ALL, BEARER_READ):
        assert token not in text
        assert token[:16] not in text


def test_httpx2_is_the_client() -> None:
    assert httpx2.AsyncClient is not None
    assert Action.LIST_RELEASES in {op.action for op in OPERATIONS}
