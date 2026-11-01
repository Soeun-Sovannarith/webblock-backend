"""The authentication middleware's handling of non-HTTP scopes (decision 0014 section 17, L3): a
WebSocket scope is closed with 1008 before any route runs; a lifespan scope passes through."""

from __future__ import annotations

import io
import logging
from collections.abc import MutableMapping
from datetime import timedelta
from typing import Any

import pytest
from tests.unit.presentation.fakes import FakeOperations, buses

from provisioning.core.application import Principal
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.presentation.http import Authenticate
from provisioning.presentation.operations import Invoker

Message = MutableMapping[str, Any]


class _Verifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, raw: str) -> Principal:
        self.calls += 1
        raise AssertionError("a non-HTTP scope must not reach the verifier")


def _authenticate(inner: Any, verifier: _Verifier) -> Authenticate:
    invoker = Invoker(
        **buses(FakeOperations()),
        request_timeout=timedelta(seconds=1),
        new_correlation_id=lambda: "corr-1",
        logger=new_logger(io.StringIO(), logging.DEBUG),
        bind_correlation=correlation_scope,
        trace_id=lambda: None,
    )
    return Authenticate(inner, invoker=invoker, verifier=verifier)


@pytest.mark.asyncio
async def test_a_websocket_scope_is_closed_with_1008_before_any_route_runs() -> None:
    reached: list[str] = []
    sent: list[Message] = []
    verifier = _Verifier()

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/mcp",
        "headers": [(b"authorization", b"Bearer " + b"a" * 40)],
    }
    await _authenticate(inner, verifier)(scope, receive, send)
    assert reached == []
    assert verifier.calls == 0
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.asyncio
async def test_a_lifespan_scope_still_passes_through() -> None:
    reached: list[str] = []
    verifier = _Verifier()

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        del message

    await _authenticate(inner, verifier)({"type": "lifespan"}, receive, send)
    assert reached == ["lifespan"]
    assert verifier.calls == 0
