"""The listings and diagnostics on the wire (decision 0014 section 21 rulings 6 and 7; decision 0023
G4): the bodies, the opaque cursor and the limit, and the operation path that joins them."""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.unit.presentation.fakes import (
    COMMAND,
    OTHER,
    PRINCIPAL,
    PROJECT,
    RELEASE,
    T0,
    FakeOperations,
    buses,
    command_page,
    diagnostics_view,
    release_page,
)

from provisioning.core.application import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Action,
    ListCommandsQuery,
    ListReleasesQuery,
    ReleaseCursor,
)
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.presentation.operations import (
    Invoker,
    MalformedRequest,
    Page,
    command_cursor,
    command_list_body,
    decode_arguments,
    diagnostics_body,
    field_errors,
    lookup,
    parse_command_cursor,
    parse_limit,
    parse_release_cursor,
    release_cursor,
    release_list_body,
)


def test_diagnostics_body_carries_codes_counts_and_times() -> None:
    body = diagnostics_body(diagnostics_view(full=True))
    assert body["release"] == {
        "status": "rolled_back",
        "failureCode": "promotion_failed",
        "failureRetryable": False,
        "createdAt": "2026-10-08T12:00:00.123456Z",
        "updatedAt": "2026-10-08T12:00:05Z",
        "promotedAt": "2026-10-08T12:00:05Z",
    }
    assert body["steps"][0]["failureCode"] == "build_failed"
    assert body["steps"][0]["failureRetryable"] is False
    assert [c["commandNumber"] for c in body["commands"]] == [7, 7]
    assert body["sourceEvent"] == {
        "status": "failed",
        "outcomeCode": "repository_mismatch",
        "attempts": 3,
        "mismatchedRepeatCount": 1,
        "receivedAt": "2026-10-08T12:00:00.123456Z",
        "finishedAt": "2026-10-08T12:00:05Z",
    }
    assert body["stepsTruncated"] is True and body["commandsTruncated"] is True
    # Redacted: no failure text, log, key, requester, repository or commit.
    text = json.dumps(body).lower()
    for word in ("failuresummary", "logref", "idempotency", "requestedby", "commit", "github"):
        assert word not in text


def test_release_list_body() -> None:
    items, page = release_list_body(release_page(full=True), limit=2)
    first, second = items
    assert set(first) == {
        "releaseId",
        "projectId",
        "gitCommitSha",
        "containerImageDigest",
        "status",
        "failureCode",
        "failureRetryable",
        "createdAt",
        "updatedAt",
        "promotedAt",
    }
    assert set(second) == {
        "releaseId",
        "projectId",
        "gitCommitSha",
        "status",
        "createdAt",
        "updatedAt",
    }
    assert page.limit == 2
    assert parse_release_cursor(page.next_cursor) == ReleaseCursor(created_at=T0, release_id=OTHER)
    assert release_list_body(release_page(full=False), limit=20) == ([], Page(20, None))


def test_command_list_body() -> None:
    items, page = command_list_body(command_page(full=True), limit=2)
    assert [item["commandNumber"] for item in items] == [7, 6]
    assert parse_command_cursor(page.next_cursor) == 6
    assert command_list_body(command_page(full=False), limit=20) == ([], Page(20, None))


# --- page inputs ----------------------------------------------------------------------------


def test_limit() -> None:
    assert parse_limit(None) == DEFAULT_PAGE_SIZE == 20
    assert parse_limit("1") == 1
    assert parse_limit(str(MAX_PAGE_SIZE)) == MAX_PAGE_SIZE == 50


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("", "INVALID_FORMAT"),
        ("007", "INVALID_FORMAT"),
        ("+5", "INVALID_FORMAT"),
        (" 5", "INVALID_FORMAT"),
        ("5 ", "INVALID_FORMAT"),
        ("1e1", "INVALID_FORMAT"),
        ("\u0663", "INVALID_FORMAT"),
        ("x", "INVALID_FORMAT"),
        ("0", "OUT_OF_RANGE"),
        ("51", "OUT_OF_RANGE"),
        ("-1", "OUT_OF_RANGE"),
        ("999", "OUT_OF_RANGE"),
        ("9" * 40, "OUT_OF_RANGE"),
    ],
)
def test_limit_refusals_name_the_field(value: str, code: str) -> None:
    with pytest.raises(MalformedRequest) as err:
        parse_limit(value)
    assert err.value.__cause__ is None
    assert [
        (e.field, e.code.value) for e in field_errors(err.value, key_field="Idempotency-Key")
    ] == [("limit", code)]


@pytest.mark.parametrize(
    "cursor",
    [
        ReleaseCursor(created_at=T0, release_id=RELEASE),
        ReleaseCursor(created_at=datetime(1969, 12, 31, tzinfo=UTC), release_id=OTHER),
        ReleaseCursor(created_at=T0 + timedelta(microseconds=1), release_id=PROJECT),
    ],
)
def test_release_cursors_round_trip(cursor: ReleaseCursor) -> None:
    text = release_cursor(cursor)
    assert len(text) <= 200
    assert parse_release_cursor(text) == cursor


@pytest.mark.parametrize("number", [1, 2, 7, (1 << 53) - 1])
def test_command_cursors_round_trip(number: int) -> None:
    assert parse_command_cursor(command_cursor(number)) == number


def _raw_cursor(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.mark.parametrize(
    "token",
    [
        "",
        "!!!",
        "a" * 201,
        command_cursor(5),  # a command cursor for the releases
        _raw_cursor(b"r" + bytes(8) + bytes(16)),  # the nil release
        _raw_cursor(b"r" + bytes(8) + RELEASE.bytes + b"x"),  # too long
        release_cursor(ReleaseCursor(created_at=T0, release_id=RELEASE)) + "=",
        "cg",
    ],
)
def test_release_cursor_refusals(token: str) -> None:
    with pytest.raises(MalformedRequest) as err:
        parse_release_cursor(token)
    assert err.value.__cause__ is None
    assert token not in str(err.value) or token == ""
    assert [
        (e.field, e.code.value) for e in field_errors(err.value, key_field="Idempotency-Key")
    ] == [("cursor", "INVALID_FORMAT")]


@pytest.mark.parametrize(
    "token",
    [
        release_cursor(ReleaseCursor(created_at=T0, release_id=RELEASE)),
        _raw_cursor(b"c" + (0).to_bytes(8, "big")),
        _raw_cursor(b"c" + (-3).to_bytes(8, "big", signed=True)),
        _raw_cursor(b"c" + (1 << 53).to_bytes(8, "big")),
        "Yw",
    ],
)
def test_command_cursor_refusals(token: str) -> None:
    with pytest.raises(MalformedRequest) as err:
        parse_command_cursor(token)
    assert [
        (e.field, e.code.value) for e in field_errors(err.value, key_field="Idempotency-Key")
    ] == [("cursor", "INVALID_FORMAT")]


def test_absent_cursors_are_the_first_page() -> None:
    assert parse_release_cursor(None) is None
    assert parse_command_cursor(None) is None


def test_tool_arguments_take_an_integer_limit() -> None:
    op = lookup(Action.LIST_COMMANDS)
    assert op is not None
    assert decode_arguments(op, {"projectId": str(PROJECT), "limit": 5}) == {
        "projectId": str(PROJECT),
        "limit": "5",
    }
    for bad in (True, "5", 5.0, None):
        with pytest.raises(MalformedRequest):
            decode_arguments(op, {"projectId": str(PROJECT), "limit": bad})
    with pytest.raises(MalformedRequest):
        decode_arguments(op, {"projectId": str(PROJECT), "cursor": 5})
    for old in ("page_size", "page_token"):
        with pytest.raises(MalformedRequest) as err:
            decode_arguments(op, {"projectId": str(PROJECT), old: "5"})
        assert [e.field for e in field_errors(err.value, key_field="Idempotency-Key")] == [
            "arguments"
        ]


# --- the operation path ---------------------------------------------------------------------


def _invoker(service: FakeOperations) -> Invoker:
    return Invoker(
        **buses(service),
        request_timeout=timedelta(seconds=5),
        new_correlation_id=lambda: "corr-1",
        logger=new_logger(io.StringIO(), logging.DEBUG),
        bind_correlation=correlation_scope,
        trace_id=lambda: None,
    )


def _body(raw: bytes) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(raw)
    return out


@pytest.mark.asyncio
async def test_a_listing_passes_the_page_inputs_to_the_service() -> None:
    service = FakeOperations()
    token = release_cursor(ReleaseCursor(created_at=T0, release_id=RELEASE))
    out = await _invoker(service).invoke(
        PRINCIPAL,
        Action.LIST_RELEASES,
        {"projectId": str(PROJECT), "limit": "7", "cursor": token},
    )
    assert out.status == 200, out.body
    out2 = await _invoker(service).invoke(
        PRINCIPAL, Action.LIST_COMMANDS, {"projectId": str(PROJECT)}
    )
    assert out2.status == 200
    assert [request for _, _, request in service.calls] == [
        ListReleasesQuery(
            principal=PRINCIPAL,
            project_id=PROJECT,
            page_size=7,
            after=ReleaseCursor(created_at=T0, release_id=RELEASE),
        ),
        ListCommandsQuery(
            principal=PRINCIPAL, project_id=PROJECT, page_size=DEFAULT_PAGE_SIZE, after=None
        ),
    ]
    assert _body(out2.body)["items"][0]["commandId"] == str(COMMAND)
    assert _body(out2.body)["page"]["limit"] == DEFAULT_PAGE_SIZE
    rest = _body(out.rest_body("/v1/x"))
    assert rest["meta"]["page"]["limit"] == 7
    assert rest["data"] == _body(out.body)["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs", "errors"),
    [
        ({"limit": "0"}, [("limit", "OUT_OF_RANGE")]),
        ({"limit": "x"}, [("limit", "INVALID_FORMAT")]),
        ({"cursor": "!"}, [("cursor", "INVALID_FORMAT")]),
        ({"cursor": command_cursor(3)}, [("cursor", "INVALID_FORMAT")]),
        ({"releaseId": str(RELEASE)}, []),
        ({"page_size": "5"}, []),
        ({"page_token": "Yw"}, []),
    ],
)
async def test_invalid_page_inputs_are_malformed_and_reach_no_service(
    inputs: dict[str, str], errors: list[tuple[str, str]]
) -> None:
    service = FakeOperations()
    out = await _invoker(service).invoke(
        PRINCIPAL, Action.LIST_RELEASES, {"projectId": str(PROJECT), **inputs}
    )
    assert (out.status, out.code) == (400, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in _body(out.body)["errors"]] == errors
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_page_of_another_project_is_never_returned() -> None:
    service = FakeOperations()
    service.results[Action.LIST_COMMANDS] = lambda request: command_page(OTHER, full=True)
    service.results[Action.LIST_RELEASES] = lambda request: release_page(OTHER, full=True)
    for action in (Action.LIST_COMMANDS, Action.LIST_RELEASES):
        out = await _invoker(service).invoke(PRINCIPAL, action, {"projectId": str(PROJECT)})
        assert (out.status, out.code) == (500, "INTERNAL_ERROR")
        assert str(OTHER) not in out.body.decode()
