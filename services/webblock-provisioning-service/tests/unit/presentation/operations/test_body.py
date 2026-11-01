"""Input decoding: a mutation's body is absent, empty or the empty JSON object; an MCP tool's
arguments are a flat object of the operation's inputs with string values; strict JSON refuses
duplicate keys, invalid UTF-8 and trailing data."""

from __future__ import annotations

from typing import Any

import pytest

from provisioning.core.application import Action
from provisioning.presentation.operations import (
    MalformedRequest,
    Operation,
    check_empty_body,
    decode_arguments,
    lookup,
    parse_json_strict,
)


def _op(action: Action) -> Operation:
    op = lookup(action)
    assert op is not None
    return op


@pytest.mark.parametrize("body", [b"", b"{}", b" { } ", b"\n{\t}\r\n"])
def test_check_empty_body_accepts(body: bytes) -> None:
    check_empty_body(body)


@pytest.mark.parametrize(
    "body",
    [
        b"{} {}",
        b"[]",
        b"null",
        b'{"a":1}',
        b'{"expected_generation":3}',
        b"{",
        b"\xff",
        b'"{}"',
        b"{}x",
        b"0",
    ],
)
def test_check_empty_body_refuses(body: bytes) -> None:
    with pytest.raises(MalformedRequest):
        check_empty_body(body)


def test_decode_arguments_accepts_the_tool_inputs() -> None:
    op = _op(Action.DEPLOY_RELEASE)
    args = {"projectId": "p", "releaseId": "r", "idempotencyKey": "k"}
    assert decode_arguments(op, args) == args
    assert decode_arguments(op, None) == {}
    assert decode_arguments(op, {}) == {}
    assert decode_arguments(op, {"projectId": "p"}) == {"projectId": "p"}


@pytest.mark.parametrize(
    "args",
    [
        {"projectId": "p", "extra": "x"},
        {"projectId": 1},
        {"projectId": True},
        {"projectId": None},
        {"projectId": ["p"]},
        {"projectId": {"id": "p"}},
        {"projectId": 1.5},
    ],
)
def test_decode_arguments_refuses_everything_else(args: dict[str, Any]) -> None:
    with pytest.raises(MalformedRequest):
        decode_arguments(_op(Action.DEPLOY_RELEASE), args)


def test_a_read_takes_no_idempotency_key() -> None:
    with pytest.raises(MalformedRequest):
        decode_arguments(
            _op(Action.GET_RELEASE),
            {"projectId": "p", "releaseId": "r", "idempotencyKey": "k"},
        )


def test_build_release_takes_no_idempotency_key() -> None:
    with pytest.raises(MalformedRequest):
        decode_arguments(
            _op(Action.BUILD_RELEASE),
            {"projectId": "p", "releaseId": "r", "idempotencyKey": "k"},
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":{"b":1,"b":2}}',
        b'[{"a":1,"a":1}]',
        b"\xff\xfe",
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b"{} trailing",
        b"",
    ],
)
def test_strict_json_refuses(raw: bytes) -> None:
    with pytest.raises(MalformedRequest):
        parse_json_strict(raw)


def test_strict_json_parses_nested_values() -> None:
    assert parse_json_strict(b'{"a":[1,{"b":"c"}],"d":null}') == {"a": [1, {"b": "c"}], "d": None}
