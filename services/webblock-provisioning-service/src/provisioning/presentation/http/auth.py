"""Bearer-token authentication for /v1/ and /mcp (decision
0006 sections 3 and 4)."""

from __future__ import annotations

import re
from typing import Final, Protocol

from provisioning.core.application import ActionDenied, Principal, Unauthenticated
from provisioning.core.domain import is_error
from provisioning.presentation.http.asgi import (
    ASGIApp,
    Receive,
    Scope,
    Send,
    header_values,
    send_outcome,
)
from provisioning.presentation.operations import IdentityUnavailable, Invoker, with_principal


class TokenVerifier(Protocol):
    """Verifies a bearer token and returns the principal it names, declared by its consumer.

    ``Unauthenticated`` (anywhere in the cause chain) means the token is refused: 401, one message
    for every cause. ``ActionDenied`` means a verified token names no user (FR-2): 403
    ACCESS_DENIED, before any store or Project call. Any other error means the token could not be
    verified: 503 identity_unavailable, failing closed without calling it a bad token. The raw token
    is passed only to ``verify`` and is never logged."""

    async def verify(self, raw: str) -> Principal: ...


_TOKEN68: Final = re.compile(rb"[A-Za-z0-9._~+/-]+=*")
_WS_POLICY_VIOLATION: Final = 1008


def bearer_token(values: list[bytes]) -> str | None:
    """The token of the only Authorization header when it has the Bearer scheme (compared without
    case) followed by one space and a token68 value (RFC 6750 section 2.1); None otherwise."""
    if len(values) != 1:
        return None
    scheme, sep, token = values[0].partition(b" ")
    if not sep or scheme.lower() != b"bearer" or _TOKEN68.fullmatch(token) is None:
        return None
    return token.decode("ascii")


class Authenticate:
    """Puts the verified principal in the request's scope for the operation layer. Applied to
    every route of the public listener, unknown ones included: every route requires a token.
    The one exception is ``open_reads`` (the prototype's /docs and /openapi.yaml, task SW): a GET
    or HEAD of exactly one of those paths, with no query string, passes without a token."""

    __slots__ = ("_app", "_invoker", "_open_reads", "_verifier")

    def __init__(
        self,
        app: ASGIApp,
        *,
        invoker: Invoker,
        verifier: TokenVerifier,
        open_reads: frozenset[str] = frozenset(),
    ) -> None:
        self._app = app
        self._invoker = invoker
        self._verifier = verifier
        self._open_reads = open_reads

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            # No route accepts a WebSocket; one added later must not be reachable without a token
            # (decision 0014 section 17, L3). Closing before accepting is refused with 1008
            # (policy violation); the server answers the handshake with 403.
            await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})
            return
        if scope["type"] != "http":  # lifespan
            await self._app(scope, receive, send)
            return
        if (
            scope.get("method") in ("GET", "HEAD")
            and scope.get("path") in self._open_reads
            and not scope.get("query_string")
        ):
            await self._app(scope, receive, send)
            return
        token = bearer_token(header_values(scope, b"authorization"))
        if token is None:
            outcome = self._invoker.reject(None, Unauthenticated("no bearer token"))
            await send_outcome(send, outcome, scope)
            return
        try:
            principal = await self._verifier.verify(token)
        except Exception as exc:
            refused: Exception
            if is_error(exc, Unauthenticated):
                refused = Unauthenticated("token refused")
            elif is_error(exc, ActionDenied):
                refused = ActionDenied("no user id")
            else:
                refused = IdentityUnavailable("the token could not be verified")
                refused.__cause__ = exc
            await send_outcome(send, self._invoker.reject(None, refused), scope)
            return
        if not principal.subject:
            outcome = self._invoker.reject(None, Unauthenticated("no subject"))
            await send_outcome(send, outcome, scope)
            return
        with_principal(scope, principal)
        await self._app(scope, receive, send)
