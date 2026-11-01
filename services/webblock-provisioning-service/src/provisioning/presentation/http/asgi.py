"""Small ASGI helpers shared by the middleware and the handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from provisioning.presentation.operations import Outcome, request_accept

type Scope = MutableMapping[str, Any]
type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def header_values(scope: Scope, name: bytes) -> list[bytes]:
    """Every value of the header ``name`` (lowercase), in order."""
    return [value for key, value in scope.get("headers", ()) if key.lower() == name]


def outcome_headers(
    outcome: Outcome, body: bytes, accept: str | None = None
) -> list[tuple[bytes, bytes]]:
    """The headers of an outcome's response: its media type (an error is
    ``application/problem+json``, or ``application/json`` for a client that accepts only that;
    decision 0018), the length of ``body``, and the bearer challenge on a 401."""
    headers = [
        (b"content-type", outcome.media_type(accept).encode("ascii")),
        (b"content-length", str(len(body)).encode()),
    ]
    if outcome.status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    return headers


async def send_outcome(send: Send, outcome: Outcome, scope: Scope) -> None:
    """Write an outcome as a complete response; an error's Problem names the request's path as
    its ``instance`` and takes its content type from the request's ``Accept`` header."""
    body = outcome.rest_body(scope.get("path", ""))
    await send(
        {
            "type": "http.response.start",
            "status": outcome.status,
            "headers": outcome_headers(outcome, body, request_accept(scope)),
        }
    )
    await send({"type": "http.response.body", "body": body})


class ResponseTracker:
    """Wraps ``send`` and remembers whether the response has started, so that an error found
    later is answered only when nothing was sent yet."""

    __slots__ = ("_send", "started")

    def __init__(self, send: Send) -> None:
        self._send = send
        self.started = False

    async def __call__(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.started = True
        await self._send(message)
