"""The public listener's middleware (decision 0006
section 7; decision 0013 section 9 A3): security headers, panic recovery, the in-flight cap, the
per-request timeout and the body-size limit; and task H4's request metrics (``RequestMetrics``),
which the API host wraps around the whole public app. uvicorn has no read or write timeouts and
answers its own ``limit_concurrency`` with a plain-text 503, so the service enforces these itself,
each answer an RFC 9457 Problem (decision 0018). Slow-client limits are the gateway's (A3)."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import Final

from provisioning.core.application import Action, Overloaded, Timeout
from provisioning.core.application.ports.metrics import HTTPMetrics
from provisioning.presentation.http.asgi import (
    ASGIApp,
    Message,
    Receive,
    ResponseTracker,
    Scope,
    Send,
    header_values,
    send_outcome,
)
from provisioning.presentation.operations import (
    OPERATIONS,
    FieldCode,
    FieldError,
    Invoker,
    MalformedRequest,
    RequestTooLarge,
)

_SECURITY_HEADERS: Final = (
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
)


class SecurityHeaders:
    """Every public response, errors included, carries Cache-Control: no-store and
    X-Content-Type-Options: nosniff; no CORS header is ever set."""

    __slots__ = ("_app",)

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in _SECURITY_HEADERS}
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message["headers"] = [*headers, *_SECURITY_HEADERS]
            await send(message)

        await self._app(scope, receive, with_headers)


class RecoverPanics:
    """An exception below it becomes 500 INTERNAL_ERROR with a fixed body; the detail goes to the
    log only (the operation layer's record). After the response has started, it is logged and the
    response ends."""

    __slots__ = ("_app", "_invoker")

    def __init__(self, app: ASGIApp, *, invoker: Invoker) -> None:
        self._app = app
        self._invoker = invoker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        tracker = ResponseTracker(send)
        try:
            await self._app(scope, receive, tracker)
        except Exception as exc:
            outcome = self._invoker.reject(None, exc)
            if not tracker.started:
                await send_outcome(send, outcome, scope)


class InFlightCap:
    """503 SERVICE_OVERLOADED when ``limit`` requests are already being served
    (HTTP_MAX_IN_FLIGHT). It runs before authentication, so verification work is bounded too
    (decision 0006 section 3)."""

    __slots__ = ("_app", "_in_flight", "_invoker", "_limit")

    def __init__(self, app: ASGIApp, *, invoker: Invoker, limit: int) -> None:
        if limit < 1:
            raise ValueError("http: the in-flight limit must be positive")
        self._app = app
        self._invoker = invoker
        self._limit = limit
        self._in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self._in_flight >= self._limit:
            outcome = self._invoker.reject(None, Overloaded("in-flight limit"))
            await send_outcome(send, outcome, scope)
            return
        self._in_flight += 1
        try:
            await self._app(scope, receive, send)
        finally:
            self._in_flight -= 1


class RequestTimeout:
    """Bounds the whole request (HTTP_REQUEST_TIMEOUT applied by the host): 504 REQUEST_TIMEOUT
    when it passes before the response started; a started response is ended. The operation layer
    applies the same bound to the application call, so a slow use case answers before this
    does."""

    __slots__ = ("_app", "_invoker", "_seconds")

    def __init__(self, app: ASGIApp, *, invoker: Invoker, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("http: the request timeout must be positive")
        self._app = app
        self._invoker = invoker
        self._seconds = seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        tracker = ResponseTracker(send)
        try:
            async with asyncio.timeout(self._seconds):
                await self._app(scope, receive, tracker)
        except TimeoutError:
            if not tracker.started:
                outcome = self._invoker.reject(None, Timeout("request timeout"))
                await send_outcome(send, outcome, scope)


type LimitOf = Callable[[Scope], tuple[int, Action | None]]
"""The body limit of a request and the operation it addresses (None when none is known yet: the
MCP endpoint, an unknown route)."""


class BodyLimit:
    """Reads the body up to the request's limit and replays it to the app. A declared length over
    the limit is refused before anything is read; a counted body (chunked included) as soon as it
    passes the limit. Over the limit is REQUEST_TOO_LARGE, except on a read, which declares no
    413 and takes no body: VALIDATION_FAILED on the field ``body``."""

    __slots__ = ("_app", "_invoker", "_limit_of")

    def __init__(self, app: ASGIApp, *, invoker: Invoker, limit_of: LimitOf) -> None:
        self._app = app
        self._invoker = invoker
        self._limit_of = limit_of

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit, action = self._limit_of(scope)
        too_large: Exception = (
            RequestTooLarge("body over the limit")
            if action is None or action.is_mutation()
            else MalformedRequest(
                "a read takes no body", errors=(FieldError("body", FieldCode.INVALID_FORMAT),)
            )
        )
        declared = header_values(scope, b"content-length")
        if declared:
            if len(declared) != 1 or not declared[0].isdigit():
                outcome = self._invoker.reject(
                    action,
                    MalformedRequest(
                        "content length",
                        errors=(FieldError("Content-Length", FieldCode.INVALID_FORMAT),),
                    ),
                )
                await send_outcome(send, outcome, scope)
                return
            if int(declared[0]) > limit:
                await send_outcome(send, self._invoker.reject(action, too_large), scope)
                return
        chunks: list[bytes] = []
        trailing: deque[Message] = deque()
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                trailing.append(message)
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await send_outcome(send, self._invoker.reject(action, too_large), scope)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        await self._app(scope, _Replay(b"".join(chunks), trailing, receive), send)


class _Replay:
    """Hands the app the whole buffered body as one message, then what the client sent after it
    (a disconnect), then whatever the client sends next."""

    __slots__ = ("_body", "_receive", "_trailing")

    def __init__(self, body: bytes, trailing: deque[Message], receive: Receive) -> None:
        self._body: bytes | None = body
        self._trailing = trailing
        self._receive = receive

    async def __call__(self) -> Message:
        if self._body is not None:
            body, self._body = self._body, None
            return {"type": "http.request", "body": body, "more_body": False}
        if self._trailing:
            return self._trailing.popleft()
        return await self._receive()


OTHER_ROUTE: Final = "other"
"""The route label of a path that matches no route (an unknown path, a probe)."""

_MCP_ROUTE: Final = "/mcp"


def route_templates() -> tuple[str, ...]:
    """Every route template of the public listener: the operations' paths and /mcp."""
    return *sorted({op.path for op in OPERATIONS}), _MCP_ROUTE


def route_template_of(templates: Iterable[str]) -> Callable[[str], str]:
    """A function from a request path to the template it matches (``/v1/projects/{projectId}``
    ...), or ``other``. The raw path, which carries identifiers, is never returned."""
    patterns = [
        (re.compile("^" + re.sub(r"\\{[A-Za-z_]+\\}", "[^/]+", re.escape(t)) + "$"), t)
        for t in templates
    ]

    def template(path: str) -> str:
        for pattern, name in patterns:
            if pattern.match(path):
                return name
        return OTHER_ROUTE

    return template


class RequestMetrics:
    """Measures every request of the app it wraps (task H4): its route template, method, status
    and duration, through the ``HTTPMetrics`` port. Outermost, so that it sees every answer,
    the middleware's own included. It changes nothing in the request or the response, and a
    measurement that fails is dropped."""

    __slots__ = ("_app", "_metrics", "_template")

    def __init__(
        self,
        app: ASGIApp,
        *,
        metrics: HTTPMetrics,
        template: Callable[[str], str],
    ) -> None:
        self._app = app
        self._metrics = metrics
        self._template = template

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        start = time.monotonic()
        status: list[int] = []

        async def observe(message: Message) -> None:
            if message["type"] == "http.response.start" and not status:
                status.append(int(message.get("status", 0)))
            await send(message)

        try:
            await self._app(scope, receive, observe)
        finally:
            with contextlib.suppress(Exception):  # a measurement never changes the response
                self._metrics.http_request(
                    self._template(scope.get("path", "")),
                    scope.get("method", ""),
                    status[0] if status else None,
                    time.monotonic() - start,
                )
