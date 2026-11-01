"""The public HTTP listener (decision 0006; decision 0013
section 9 A3 and S5; review-port-arch.md H3): the fourteen REST routes of
src/contracts/openapi.yaml on FastAPI (the four admin ones of decision 0023 G9 included), the MCP
endpoint at /mcp, and the middleware around both. Handlers only adapt HTTP to the operation layer;
they contain no workflow logic.

Every route is mounted in both profiles, so the served surface is the contract's. Which actions run
is the application's operation gate (``EnabledOperations``, decision 0006 section 6): the production
profile refuses every mutation, the three admin mutations included, with 503
``OPERATION_UNAVAILABLE`` and serves the reads, the admin read included (decision 0023 G13).

FastAPI's defaults that break the contract are switched off: no /docs, /redoc or /openapi.json, no
trailing-slash redirects, no 405 or 422 of its own (a wrong method or an unknown path is 404
RESOURCE_NOT_FOUND, a validation error VALIDATION_FAILED), and no request models: the
routes read their path parameters and headers raw and the operation layer parses them through the
domain.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from typing import Final, Protocol
from urllib.parse import parse_qsl

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from provisioning.core.application import Action
from provisioning.presentation.http.asgi import Receive, Scope, Send, header_values
from provisioning.presentation.http.auth import Authenticate, TokenVerifier
from provisioning.presentation.http.docs import OPEN_READ_PATHS, docs_routes
from provisioning.presentation.http.middleware import (
    BodyLimit,
    InFlightCap,
    RecoverPanics,
    RequestTimeout,
    SecurityHeaders,
)
from provisioning.presentation.http.request_id import RequestId
from provisioning.presentation.operations import (
    INPUT_IDEMPOTENCY_KEY,
    OPERATIONS,
    FieldCode,
    FieldError,
    Invoker,
    MalformedRequest,
    Operation,
    Outcome,
    UnknownRoute,
    check_empty_body,
    field_name,
    principal_from,
    request_accept,
)

MUTATION_BODY_LIMIT: Final = 64
"""The fixed body limit of every /v1/ route, in bytes: a mutation accepts at most the empty JSON
object (decision 0006 sections 2 and 7), so its limit is small; a smaller configured
HTTP_MAX_REQUEST_BODY_BYTES still applies. /mcp has its own limit."""

MCP_PATH: Final = "/mcp"

MAX_QUERY_BYTES: Final = 512
"""The longest query string a listing accepts: limit and a cursor (at most 200 characters) with
room to spare."""

BODY_ERRORS: Final = (FieldError("body", FieldCode.INVALID_FORMAT),)
"""The field errors of a body a route does not take (decision 0023 G5)."""
QUERY_ERRORS: Final = (FieldError("query", FieldCode.INVALID_FORMAT),)
"""The field errors of a query a route does not take: an unknown or repeated parameter, or a
query on an operation that takes none. The parameter's name is never echoed (lead ruling)."""
_KEY_ERRORS: Final = (FieldError(field_name(INPUT_IDEMPOTENCY_KEY), FieldCode.INVALID_FORMAT),)


class MCPMount(Protocol):
    """The MCP endpoint as the public app mounts it: an ASGI app for /mcp, and the lifetime of
    its session manager, entered with the app's lifespan."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None: ...

    def run(self) -> AbstractAsyncContextManager[None]: ...


def build_public_app(
    *,
    invoker: Invoker,
    verifier: TokenVerifier,
    mcp: MCPMount,
    max_in_flight: int,
    max_request_body_bytes: int,
    mcp_max_request_body_bytes: int,
    request_timeout: timedelta,
    openapi_yaml: bytes | None,
) -> FastAPI:
    """The public application. Middleware, outermost first: request ID, security headers, panic
    recovery, the in-flight cap (before authentication), the request timeout, authentication
    (every route, unknown ones included), the body limit; then the routes.

    ``openapi_yaml`` is the contract file's bytes in the prototype profile, which then also serves
    GET/HEAD /docs and /openapi.yaml without a token (task SW); None (the production profile)
    registers neither route."""
    if max_request_body_bytes < 1 or mcp_max_request_body_bytes < 1:
        raise ValueError("http: body limits must be positive")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.run():
            yield

    app = FastAPI(
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
    )
    for op in OPERATIONS:
        app.add_api_route(
            op.path,
            _endpoint(invoker, op),
            methods=[op.method],
            operation_id=op.action.value,
            response_class=Response,
            include_in_schema=True,
        )
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp))
    if openapi_yaml is not None:
        app.router.routes.extend(docs_routes(openapi_yaml))

    async def not_found(request: Request, _exc: Exception) -> Response:
        # A wrong method on a /v1/ route is 404 RESOURCE_NOT_FOUND too (decision 0023 G10).
        return _response(invoker.reject(None, UnknownRoute("no such route")), request.scope)

    async def malformed(request: Request, _exc: Exception) -> Response:
        return _response(
            invoker.reject(None, MalformedRequest("request validation")), request.scope
        )

    app.add_exception_handler(StarletteHTTPException, not_found)
    app.add_exception_handler(RequestValidationError, malformed)

    limit = min(MUTATION_BODY_LIMIT, max_request_body_bytes)
    routes = _route_matcher()

    def limit_of(scope: Scope) -> tuple[int, Action | None]:
        if scope.get("path") == MCP_PATH:
            return mcp_max_request_body_bytes, None
        return limit, routes(scope.get("method", ""), scope.get("path", ""))

    # Starlette wraps the last added middleware outermost.
    app.add_middleware(BodyLimit, invoker=invoker, limit_of=limit_of)
    app.add_middleware(
        Authenticate,
        invoker=invoker,
        verifier=verifier,
        open_reads=OPEN_READ_PATHS if openapi_yaml is not None else frozenset(),
    )
    app.add_middleware(RequestTimeout, invoker=invoker, seconds=request_timeout.total_seconds())
    app.add_middleware(InFlightCap, invoker=invoker, limit=max_in_flight)
    app.add_middleware(RecoverPanics, invoker=invoker)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(RequestId, invoker=invoker)
    return app


def served_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every registered route as (method, path); the MCP route, which takes every method, as
    ("*", "/mcp")."""
    out: list[tuple[str, str]] = []
    for route in app.router.routes:
        if isinstance(route, APIRoute):
            out.extend((method, route.path) for method in sorted(route.methods or ()))
        elif isinstance(route, Route):
            methods = route.methods if route.methods is not None else {"*"}
            out.extend((m, route.path) for m in sorted(methods))
    return out


def _endpoint(invoker: Invoker, op: Operation) -> Callable[[Request], object]:
    path_inputs = tuple(
        name for name in op.inputs if name != INPUT_IDEMPOTENCY_KEY and name not in op.optional
    )

    async def endpoint(request: Request) -> Response:
        scope = request.scope
        body = await request.body()
        try:
            _check_body(scope, op, body)
            inputs = {name: request.path_params[name] for name in path_inputs}
            inputs.update(_query_inputs(scope, op))
            if INPUT_IDEMPOTENCY_KEY in op.inputs:
                key = _idempotency_key(scope)
                if key is not None:
                    inputs[INPUT_IDEMPOTENCY_KEY] = key
        except Exception as exc:
            return _response(invoker.reject(op.action, exc), scope)
        return _response(await invoker.invoke(principal_from(scope), op.action, inputs), scope)

    endpoint.__name__ = op.action.value
    return endpoint


def _check_body(scope: Scope, op: Operation, body: bytes) -> None:
    """A mutation's body must be absent, empty or the empty JSON object sent as one
    application/json content type; a read takes no body at all. Each refusal names the field
    ``body``."""
    if not body:
        return
    if not op.is_mutation():
        raise MalformedRequest("a read takes no body", errors=BODY_ERRORS)
    types = header_values(scope, b"content-type")
    if len(types) != 1 or types[0].split(b";")[0].strip().lower() != b"application/json":
        raise MalformedRequest("content type", errors=BODY_ERRORS)
    try:
        check_empty_body(body)
    except MalformedRequest:
        raise MalformedRequest("a mutation takes no fields", errors=BODY_ERRORS) from None


def _query_inputs(scope: Scope, op: Operation) -> dict[str, str]:
    """The query parameters of a listing (its optional inputs: limit, cursor), each at
    most once. Any other parameter, a repeated one, or a query on an operation that takes none
    is ``MalformedRequest`` with the field ``query``; the input is never echoed."""
    raw = scope.get("query_string", b"")
    if not raw:
        return {}
    if len(raw) > MAX_QUERY_BYTES or not op.optional:
        raise MalformedRequest("query", errors=QUERY_ERRORS)
    try:
        pairs = parse_qsl(
            raw.decode("ascii"),
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=len(op.optional),
        )
    except UnicodeDecodeError, ValueError:
        raise MalformedRequest("query", errors=QUERY_ERRORS) from None
    found: dict[str, str] = {}
    for name, value in pairs:
        if name not in op.optional or name in found:
            raise MalformedRequest("query", errors=QUERY_ERRORS)
        found[name] = value
    return found


def _idempotency_key(scope: Scope) -> str | None:
    """The one Idempotency-Key header without surrounding optional white space (RFC 9110), None
    when absent; repeated or not UTF-8 is an INVALID_FORMAT of the header."""
    values = header_values(scope, b"idempotency-key")
    if not values:
        return None
    if len(values) > 1:
        raise MalformedRequest("repeated idempotency key", errors=_KEY_ERRORS)
    try:
        return values[0].decode("utf-8").strip(" \t")
    except UnicodeDecodeError:
        raise MalformedRequest("idempotency key not UTF-8", errors=_KEY_ERRORS) from None


def _response(outcome: Outcome, scope: Scope) -> Response:
    """A success as ``application/json`` in the envelope whose ``meta.path`` is the request path
    (decision 0023 G2); an error as ``application/problem+json`` (or ``application/json`` when the
    request's ``Accept`` allows only that) whose ``instance`` is the request path (decision
    0018)."""
    headers = {"WWW-Authenticate": "Bearer"} if outcome.status == 401 else None
    return Response(
        content=outcome.rest_body(scope.get("path", "")),
        status_code=outcome.status,
        media_type=outcome.media_type(request_accept(scope)),
        headers=headers,
    )


def _route_matcher() -> Callable[[str, str], Action | None]:
    """The operation a method and path address, for the body limit's choice of error."""
    patterns = [
        (op.method, re.compile("^" + re.sub(r"\{[A-Za-z_]+}", "[^/]+", op.path) + "$"), op.action)
        for op in OPERATIONS
    ]

    def match(method: str, path: str) -> Action | None:
        for want, pattern, action in patterns:
            if method == want and pattern.match(path):
                return action
        return None

    return match
