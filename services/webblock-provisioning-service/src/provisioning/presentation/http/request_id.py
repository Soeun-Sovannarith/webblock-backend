"""The X-Request-Id middleware (spec E8). A valid inbound value is kept; anything else is replaced
by a UUIDv7, and the replaced value is never logged. The value is the correlation ID of the
command and of every gRPC call made for the request, and it is on every response."""

from __future__ import annotations

import re
import uuid
from typing import Final

from provisioning.presentation.http.asgi import (
    ASGIApp,
    Message,
    Receive,
    Scope,
    Send,
    header_values,
)
from provisioning.presentation.operations import Invoker, request_id_scope, with_request_id

REQUEST_ID_HEADER: Final = "X-Request-Id"
_FORM: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
"""The same form as the event envelope's correlation_id (a test keeps both equal)."""


def accept_request_id(raw: str | None) -> str:
    """``raw`` when it is a safe ID, else a new UUIDv7."""
    if raw is not None and _FORM.fullmatch(raw):
        return raw
    return str(uuid.uuid7())


class RequestId:
    """Outermost middleware: sets the request ID for the call and on the response."""

    __slots__ = ("_app", "_invoker")

    def __init__(self, app: ASGIApp, *, invoker: Invoker) -> None:
        self._app = app
        self._invoker = invoker

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        values = header_values(scope, b"x-request-id")
        raw: str | None = None
        if len(values) == 1:
            try:
                raw = values[0].decode("ascii")
            except UnicodeDecodeError:
                raw = None
        rid = accept_request_id(raw)
        with_request_id(scope, rid)
        header = (REQUEST_ID_HEADER.lower().encode(), rid.encode("ascii"))

        async def with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() != header[0]]
                message["headers"] = [*kept, header]
            await send(message)

        with request_id_scope(rid), self._invoker.bind_correlation(rid):
            await self._app(scope, receive, with_header)
