"""The pure ASGI middleware of the public listener, each on its own: panic recovery, the body limit
(declared and counted), and the pass-through of other scope types (lifespan)."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import MutableMapping
from datetime import timedelta
from typing import Any

import pytest
from tests.unit.presentation.fakes import FakeOperations, buses

from provisioning.core.application import Action
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.presentation.http import BodyLimit, RecoverPanics
from provisioning.presentation.operations import Invoker

Message = MutableMapping[str, Any]


def _invoker(log: io.StringIO) -> Invoker:
    return Invoker(
        **buses(FakeOperations()),
        request_timeout=timedelta(seconds=1),
        new_correlation_id=lambda: "corr-1",
        logger=new_logger(log, logging.DEBUG),
        bind_correlation=correlation_scope,
        trace_id=lambda: None,
    )


def _scope(
    headers: list[tuple[bytes, bytes]] | None = None, method: str = "POST"
) -> dict[str, Any]:
    return {"type": "http", "method": method, "path": "/x", "headers": headers or []}


async def _run(app: Any, scope: dict[str, Any], chunks: list[bytes]) -> list[Message]:
    queue: list[Message] = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ] or [{"type": "http.request", "body": b"", "more_body": False}]
    sent: list[Message] = []

    async def receive() -> Message:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _status_and_body(sent: list[Message]) -> tuple[int, dict[str, Any]]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body)


def _content_type(sent: list[Message]) -> bytes:
    start = next(m for m in sent if m["type"] == "http.response.start")
    (value,) = [v for k, v in start["headers"] if k == b"content-type"]
    return bytes(value)


@pytest.mark.asyncio
async def test_a_panic_becomes_500_internal_with_the_detail_only_in_the_log() -> None:
    log = io.StringIO()

    async def boom(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("panic-detail token=abcdef")

    sent = await _run(RecoverPanics(boom, invoker=_invoker(log)), _scope(), [])
    status, body = _status_and_body(sent)
    assert status == 500
    assert _content_type(sent) == b"application/problem+json"
    assert (body["code"], body["status"], body["instance"]) == ("INTERNAL_ERROR", 500, "/x")
    assert "panic-detail" not in json.dumps(body)
    assert "panic-detail" in log.getvalue()
    assert "abcdef" not in log.getvalue()


@pytest.mark.asyncio
async def test_a_panic_after_the_response_started_is_logged_and_the_response_ends() -> None:
    log = io.StringIO()

    async def half(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("late-panic")

    sent = await _run(RecoverPanics(half, invoker=_invoker(log)), _scope(), [])
    assert [m["type"] for m in sent] == ["http.response.start"]
    assert "late-panic" in log.getvalue()


async def _echo(scope: Any, receive: Any, send: Any) -> None:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": json.dumps({"len": len(body)}).encode()})


def _limit(log: io.StringIO, limit: int = 10) -> BodyLimit:
    return BodyLimit(
        _echo, invoker=_invoker(log), limit_of=lambda scope: (limit, Action.DEPLOY_RELEASE)
    )


@pytest.mark.asyncio
async def test_a_body_within_the_limit_is_replayed_whole() -> None:
    sent = await _run(_limit(io.StringIO()), _scope(), [b"12345", b"67890"])
    assert _status_and_body(sent) == (200, {"len": 10})


@pytest.mark.asyncio
async def test_a_declared_length_over_the_limit_is_refused_before_reading() -> None:
    read: list[int] = []

    async def counting(scope: Any, receive: Any, send: Any) -> None:
        read.append(1)

    app = BodyLimit(
        counting,
        invoker=_invoker(io.StringIO()),
        limit_of=lambda scope: (10, Action.DEPLOY_RELEASE),
    )
    sent = await _run(app, _scope([(b"content-length", b"11")]), [b"x" * 11])
    assert _status_and_body(sent) == (
        413,
        {
            "type": "urn:webblock:problem:request-too-large",
            "title": "The request body is larger than the limit for the operation.",
            "status": 413,
            "instance": "/x",
            "code": "REQUEST_TOO_LARGE",
            "requestId": "corr-1",
            "errors": [],
        },
    )
    assert _content_type(sent) == b"application/problem+json"
    assert read == []


@pytest.mark.asyncio
async def test_a_counted_chunked_body_over_the_limit_is_refused() -> None:
    sent = await _run(_limit(io.StringIO()), _scope(), [b"123456", b"78901"])
    assert _status_and_body(sent)[0] == 413


@pytest.mark.asyncio
async def test_a_read_over_the_limit_is_malformed_because_reads_declare_no_413() -> None:
    app = BodyLimit(
        _echo, invoker=_invoker(io.StringIO()), limit_of=lambda scope: (10, Action.GET_RELEASE)
    )
    sent = await _run(app, _scope(method="GET"), [b"x" * 11])
    status, body = _status_and_body(sent)
    assert (status, body["code"]) == (400, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [("body", "INVALID_FORMAT")]


@pytest.mark.asyncio
async def test_an_invalid_content_length_is_malformed() -> None:
    sent = await _run(_limit(io.StringIO()), _scope([(b"content-length", b"1x")]), [b"x"])
    status, body = _status_and_body(sent)
    assert (status, body["code"]) == (400, "VALIDATION_FAILED")
    assert [(e["field"], e["code"]) for e in body["errors"]] == [
        ("Content-Length", "INVALID_FORMAT")
    ]


@pytest.mark.asyncio
async def test_other_scope_types_pass_through() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        del message

    await BodyLimit(inner, invoker=_invoker(io.StringIO()), limit_of=lambda s: (1, None))(
        {"type": "lifespan"}, receive, send
    )
    await RecoverPanics(inner, invoker=_invoker(io.StringIO()))({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan", "lifespan"]
