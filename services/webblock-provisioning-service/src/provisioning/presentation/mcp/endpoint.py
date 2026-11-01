"""The MCP endpoint (decision 0006 section 4; decision 0013
section 9 S3): the official SDK's low-level server behind a thin adapter, over Streamable HTTP in
stateless mode with JSON responses, mounted at /mcp behind the public listener's authentication.

Each tool calls the operation layer, the same path as its REST operation, and reports a failure as
a tool result marked as an error whose structured content is the operation layer's RFC 9457
Problem without ``instance`` (decision 0018). The principal is the one the
authentication middleware put in the request's scope; the SDK hands the HTTP request to the tool
handler (``ctx.request``), and the handler never reads the raw Authorization header.

The SDK is given explicit transport-security settings (DNS-rebinding protection with the configured
Host and Origin allow-lists) and the body limit. Its own refusals answer in plain text, so the
adapter checks first and answers with a Problem with ``instance`` ``/mcp``, its content type
following ``Accept`` (decision 0018 section 3): a method other than POST is 405 without a body
(stateless: no stream, no session to end); a Host or Origin outside the allow-lists,
a content type other than one application/json, an Accept without JSON and event streams, invalid
JSON (duplicate keys included) and a JSON-RPC batch are VALIDATION_FAILED, naming the header or the
body in ``errors``; a body over the limit is REQUEST_TOO_LARGE. A batch is refused so that the
in-flight and body limits keep meaning one call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, MutableMapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any, Final

import mcp_types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

from provisioning.core.application import AuditActorKind
from provisioning.presentation.http.asgi import outcome_headers
from provisioning.presentation.mcp.tools import ToolSpec, parse_tools
from provisioning.presentation.operations import (
    FieldCode,
    FieldError,
    Invoker,
    MalformedRequest,
    Outcome,
    RequestTooLarge,
    decode_arguments,
    parse_json_strict,
    principal_from,
    request_accept,
    request_id_from,
    request_id_scope,
)

type _Scope = MutableMapping[str, Any]
type _Message = MutableMapping[str, Any]

_SERVER_NAME: Final = "provisioning"


class MCPEndpoint:
    """The /mcp ASGI app and the lifetime of its session manager (``run``, entered once, with the
    public app's lifespan). Construction fails with ``ToolFileError`` when the tool file and the
    operations differ, and with ``ValueError`` for a limit that is not positive."""

    __slots__ = ("_hosts", "_invoker", "_limit", "_manager", "_origins", "_tools")

    def __init__(
        self,
        *,
        invoker: Invoker,
        tools: bytes,
        max_request_body_bytes: int,
        allowed_origins: Sequence[str],
        allowed_hosts: Sequence[str],
    ) -> None:
        if max_request_body_bytes < 1:
            raise ValueError("mcp: the body limit must be positive")
        if not allowed_hosts:
            raise ValueError("mcp: at least one allowed host is required")
        version, specs = parse_tools(tools)
        self._invoker = invoker
        self._tools = {spec.name: spec for spec in specs}
        self._limit = max_request_body_bytes
        self._origins = tuple(allowed_origins)
        self._hosts = tuple(allowed_hosts)
        listed = [
            types.Tool(name=s.name, description=s.description, input_schema=s.input_schema)
            for s in specs
        ]

        async def list_tools(
            _ctx: ServerRequestContext[Any, Any], _params: types.PaginatedRequestParams | None
        ) -> types.ListToolsResult:
            return types.ListToolsResult(tools=listed)

        server: Server[Any] = Server(
            _SERVER_NAME, version=version, on_list_tools=list_tools, on_call_tool=self._call_tool
        )
        self._manager = StreamableHTTPSessionManager(
            app=server,
            json_response=True,
            stateless=True,
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=list(self._hosts),
                allowed_origins=list(self._origins),
            ),
            max_request_body_size=max_request_body_bytes,
        )

    def run(self) -> AbstractAsyncContextManager[None]:
        """The session manager's lifetime; it can be entered only once."""
        return self._manager.run()

    async def __call__(self, scope: _Scope, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        if scope.get("method") != "POST":
            await send(
                {"type": "http.response.start", "status": 405, "headers": [(b"allow", b"POST")]}
            )
            await send({"type": "http.response.body", "body": b""})
            return
        try:
            self._check_headers(scope)
            body = await self._read_body(receive)
            _check_payload(body)
        except Exception as exc:
            await _send_outcome(send, self._invoker.reject(None, exc), scope)
            return
        replayed = False

        async def replay() -> _Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            message: _Message = await receive()
            return message

        await self._manager.handle_request(scope, replay, send)

    async def _call_tool(
        self, ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        spec = self._tools.get(params.name)
        if spec is None:
            raise MCPError(types.INVALID_PARAMS, "unknown tool")
        try:
            outcome = await self._run(spec, ctx, params)
        except Exception as exc:  # a defect below; the detail goes to the log only
            outcome = self._invoker.reject(spec.operation.action, exc, field_of=_argument)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=outcome.body.decode())],
            structured_content=json.loads(outcome.body),
            is_error=outcome.is_error(),
        )

    async def _run(
        self,
        spec: ToolSpec,
        ctx: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> Outcome:
        action = spec.operation.action
        request = ctx.request
        scope = getattr(request, "scope", None)
        principal = principal_from(scope) if isinstance(scope, Mapping) else None
        rid = request_id_from(scope) if isinstance(scope, Mapping) else None
        with request_id_scope(rid) if rid else nullcontext():
            try:
                inputs = decode_arguments(spec.operation, params.arguments)
            except MalformedRequest as exc:
                return self._invoker.reject(action, exc, field_of=_argument)
            return await self._invoker.invoke(
                principal, action, inputs, actor_kind=AuditActorKind.AGENT, field_of=_argument
            )

    def _check_headers(self, scope: _Scope) -> None:
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", ()))
        hosts = [v for k, v in headers if k.lower() == b"host"]
        if len(hosts) != 1 or not _host_allowed(hosts[0].decode("latin-1"), self._hosts):
            raise MalformedRequest("host not allowed", errors=_header("Host"))
        origins = [v for k, v in headers if k.lower() == b"origin"]
        if origins and (len(origins) != 1 or origins[0].decode("latin-1") not in self._origins):
            raise MalformedRequest("origin not allowed", errors=_header("Origin"))
        content_types = [v for k, v in headers if k.lower() == b"content-type"]
        if (
            len(content_types) != 1
            or content_types[0].split(b";")[0].strip().lower() != b"application/json"
        ):
            raise MalformedRequest("content type", errors=_header("Content-Type"))
        if not _accepts([v for k, v in headers if k.lower() == b"accept"]):
            raise MalformedRequest("accept", errors=_header("Accept"))

    async def _read_body(self, receive: Any) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                raise MalformedRequest("body cannot be read", errors=_BODY)
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self._limit:
                raise RequestTooLarge("body over the MCP limit")
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)


def _check_payload(body: bytes) -> None:
    """One JSON-RPC message: strict JSON (no duplicate key) whose top level is an object. Each
    refusal names the field ``body``."""
    try:
        value = parse_json_strict(body)
    except MalformedRequest:
        raise MalformedRequest("invalid JSON", errors=_BODY) from None
    if isinstance(value, list):
        raise MalformedRequest("more than one call in a request", errors=_BODY)
    if not isinstance(value, dict):
        raise MalformedRequest("not a JSON-RPC message", errors=_BODY)


_BODY: Final = (FieldError("body", FieldCode.INVALID_FORMAT),)


def _header(name: str) -> tuple[FieldError, ...]:
    """The field error of a refused request header, named as HTTP spells it."""
    return (FieldError(name, FieldCode.INVALID_FORMAT),)


def _argument(name: str) -> str:
    """A tool's input is named in a field error as its argument (decision 0023 G5)."""
    return name


def _host_allowed(host: str, allowed: Sequence[str]) -> bool:
    """The SDK's rule: an exact match, or ``base:*`` for ``base`` with any port."""
    for pattern in allowed:
        if host == pattern:
            return True
        if (
            pattern.endswith(":*")
            and host.startswith(pattern[:-1])
            and host[len(pattern) - 1 :].isdigit()
        ):
            return True
    return False


def _accepts(values: list[bytes]) -> bool:
    """The Accept values cover application/json and text/event-stream (wildcards count), so
    nothing the SDK would serve is refused here."""
    json_ok = stream_ok = False
    for value in values:
        for token in value.decode("latin-1").split(","):
            base = token.split(";")[0].strip().lower()
            if base in ("application/json", "application/*"):
                json_ok = True
            elif base in ("text/event-stream", "text/*"):
                stream_ok = True
            elif base == "*/*":
                json_ok = stream_ok = True
    return json_ok and stream_ok


async def _send_outcome(send: Any, outcome: Outcome, scope: _Scope) -> None:
    """An HTTP-level refusal of the endpoint: the Problem with the request path as ``instance``
    and the content type the request's ``Accept`` header asks for, as the public listener's
    middleware sends it (decision 0018)."""
    body = outcome.rest_body(scope.get("path", ""))
    headers = outcome_headers(outcome, body, request_accept(scope))
    await send({"type": "http.response.start", "status": outcome.status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
