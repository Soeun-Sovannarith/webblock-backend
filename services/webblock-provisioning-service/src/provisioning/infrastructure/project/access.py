"""``ProjectAccess`` over HTTP: the Project service's access check (task PA; decision 0017
section 2, OD-01). Selected by ``PROJECT_ACCESS_MODE=project-service``, in either profile.

The call. ``POST {PROJECT_SERVICE_URL}/internal/v1/access-checks`` with ``Content-Type:
application/json``, ``Accept-Encoding: identity`` and the camelCase body ``{"userId", "projectId",
"action"}``, where ``action`` is the action's snake_case value. web-block-commons'
``InternalTransport`` adds ``x-request-id`` (the current correlation ID, or a fresh one),
``x-user-id`` (the body's user) and, when tracing is on, ``traceparent`` (FR-8, decision 0024 G3),
and drops ``authorization``. No ``Authorization`` header (the owner's authentication design of
16-18 October 2026, A3): inside the mesh the caller is its mTLS identity, and the mesh's
authorization policy (caller workload and path) is the only gate. The end user's token is never
relayed.

The answer (the Project service's standard envelope ``{"success", "data", "error", "meta"}``):

- ``200``, ``success: true``, ``data.allowed: true``: allowed;
- ``200``, ``success: true``, ``data.allowed: false`` with reason ``NOT_FOUND``, ``NOT_MANAGER``,
  ``PROJECT_DELETED`` or ``PROJECT_SUSPENDED``: ``ProjectAccessDenied``;
- anything else (a timeout, a connection error, any other status, a redirect, ``success: false``,
  an unknown reason, a malformed, compressed or oversized body): ``ProjectAccessUnavailable``,
  raised ``from None`` with a fixed cause, so no response text or URL reaches an error.

A principal without a platform user ID is denied without a call (owner: no user, no project
access). The Project service answers for owners only; an ``ADMIN``'s rollback of any project is
decided by the operation gate, which then does not call this adapter (decision 0019 section 7).

Bounds. The whole exchange is bounded by ``asyncio.timeout`` (``CALL_TIMEOUT``, 2 s, decision 0017
section 2) and the response body is read as a stream that stops past ``MAX_RESPONSE_BYTES``. The
client follows no redirect, reads no proxy or trust setting from the environment
(``trust_env=False``) and keeps no cookies (a jar whose policy accepts none, finding M-1 of the
PR-3 review), so nothing one answer sets reaches a later check; an https URL is verified against
the platform's roots only.

Logging. A denial is logged with the project, the action and the reason; a failure with the
project, the action and a fixed cause. Never the subject, the user ID or the response body.
"""

from __future__ import annotations

import asyncio
import json
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, Final
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from webblock_commons.outgoing import InternalTransport

from provisioning.core.application import (
    Action,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
)
from provisioning.infrastructure.config.api import http_service_url
from provisioning.infrastructure.inner_call import bound_call
from provisioning.infrastructure.telemetry import (
    Logger,
    current_correlation_id,
    new_correlation_id,
)
from provisioning.infrastructure.tls import platform_roots

ACCESS_CHECK_PATH: Final = "/internal/v1/access-checks"
CALL_TIMEOUT: Final = 2.0
"""Seconds one access check may take in all (decision 0017 section 2, lead)."""
MAX_RESPONSE_BYTES: Final = 64 * 1024
"""The largest response body read; the answer is a few dozen bytes."""
DENIAL_REASONS: Final = frozenset(
    {"NOT_FOUND", "NOT_MANAGER", "PROJECT_DELETED", "PROJECT_SUSPENDED"}
)
"""The reasons a denial may carry; any other is an answer this adapter does not understand."""

_COMPONENT: Final = "project-access"


def user_id_or_deny(principal: Principal, project_id: UUID, action: Action, log: Logger) -> UUID:
    """The principal's platform user ID. Without one the denial is logged and
    ``ProjectAccessDenied`` raised before any call (owner: no user, no project access)."""
    if principal.user_id is None:
        log.info(
            "project access denied without a call: the principal has no user ID",
            component=_COMPONENT,
            project_id=project_id,
            action=action,
            reason="no_user_id",
        )
        raise ProjectAccessDenied("the principal has no platform user ID")
    return principal.user_id


def denied(log: Logger, project_id: UUID, action: Action, reason: str) -> ProjectAccessDenied:
    """Log the Project service's denial (``reason`` is one of ``DENIAL_REASONS``) and return the
    error to raise."""
    log.info(
        "project service denied project access",
        component=_COMPONENT,
        project_id=project_id,
        action=action,
        reason=reason,
    )
    return ProjectAccessDenied(f"the project service denied access: {reason}")


def failed_internally(
    log: Logger, project_id: UUID, action: Action, exc: Exception
) -> ProjectAccessUnavailable:
    """Log a failure no other branch names, by its class only, and return the error to raise:
    the check fails closed."""
    log.warning(
        "project access check failed",
        component=_COMPONENT,
        project_id=project_id,
        action=action,
        reason="internal",
        error=type(exc).__name__,
    )
    return ProjectAccessUnavailable("project service: internal")


class _Unanswered(Exception):
    """The Project service gave no answer this adapter can use; ``cause`` is a fixed word."""

    def __init__(self, cause: str, status: int | None = None) -> None:
        super().__init__(cause)
        self.cause = cause
        self.status = status


class ProjectServiceAccess:
    """Implements ``ProjectAccess`` against the Project service. Construct it once per process
    and close it with ``aclose`` on the way out; ``transport`` is for tests only."""

    __slots__ = ("_client", "_log", "_timeout", "_url")

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        logger: Logger,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """``ValueError`` (naming the field, never the value) for a URL that is not an http or
        https URL without credentials, query or fragment, or a timeout that is not positive.
        Opens no connection."""
        if http_service_url(base_url) is not None:
            raise ValueError(
                "project service: base_url must be an http or https URL without credentials, "
                "query or fragment"
            )
        if not timeout > 0:
            raise ValueError("project service: timeout must be positive")
        parts = urlsplit(base_url)
        self._url = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}{ACCESS_CHECK_PATH}"
        self._timeout = timeout
        self._log = logger
        # The library's internal transport sets the FR-8 headers on every request; the client's
        # own ``verify`` is not used once a transport is given, so the inner transport holds it.
        inner = transport or httpx2.AsyncHTTPTransport(
            verify=platform_roots() if parts.scheme == "https" else True, trust_env=False
        )
        self._client = httpx2.AsyncClient(
            timeout=httpx2.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
            transport=InternalTransport(inner),
        )

    async def aclose(self) -> None:
        """Close the client and its connections."""
        await self._client.aclose()

    def __repr__(self) -> str:
        return "ProjectServiceAccess()"

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        user_id = user_id_or_deny(principal, project_id, action, self._log)
        failure: _Unanswered
        try:
            async with asyncio.timeout(self._timeout):
                reason = await self._ask(user_id, project_id, action)
        except _Unanswered as unanswered:
            failure = unanswered
        except TimeoutError, httpx2.TimeoutException:
            failure = _Unanswered("timeout")
        except httpx2.RequestError:
            failure = _Unanswered("connection")
        except Exception as exc:  # anything else fails closed, logged by class only
            raise failed_internally(self._log, project_id, action, exc) from None
        else:
            if reason is None:
                return
            raise denied(self._log, project_id, action, reason)
        self._log.warning(
            "project access check failed",
            component=_COMPONENT,
            project_id=project_id,
            action=action,
            reason=failure.cause,
            status=failure.status,
        )
        detail = f"HTTP {failure.status}" if failure.status is not None else failure.cause
        raise ProjectAccessUnavailable(f"project service: {detail}") from None

    async def _ask(self, user_id: UUID, project_id: UUID, action: Action) -> str | None:
        """None when allowed, the reason when denied; ``_Unanswered`` otherwise."""
        body = json.dumps(
            {"userId": str(user_id), "projectId": str(project_id), "action": action.value}
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        request_id = current_correlation_id() or new_correlation_id()
        raw = bytearray()
        with bound_call(request_id, user_id):
            await self._read(body, headers, raw)
        return _decision(bytes(raw))

    async def _read(self, body: bytes, headers: dict[str, str], raw: bytearray) -> None:
        """The answer's body into ``raw``; ``_Unanswered`` for a status, size or encoding this
        adapter does not read."""
        async with self._client.stream("POST", self._url, content=body, headers=headers) as resp:
            if resp.status_code != 200:
                raise _Unanswered("status", resp.status_code)
            declared = resp.headers.get("content-length")
            if declared is not None and (
                not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES
            ):
                raise _Unanswered("too_large")
            if resp.headers.get("content-encoding", "identity").lower() != "identity":
                raise _Unanswered("encoded")
            async for chunk in resp.aiter_raw():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise _Unanswered("too_large")


def _decision(raw: bytes) -> str | None:
    """The envelope's decision: None when allowed, a known reason when denied."""
    doc = decode_object(raw)
    if doc is None or doc.get("success") is not True:
        raise _Unanswered("malformed" if doc is None else "not_success")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise _Unanswered("malformed")
    allowed = data.get("allowed")
    if allowed is True:
        return None
    if allowed is not False:
        raise _Unanswered("malformed")
    reason = data.get("reason")
    if not isinstance(reason, str) or reason not in DENIAL_REASONS:
        raise _Unanswered("unknown_reason")
    return reason


def _no_repeats(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("repeated member")
        out[key] = value
    return out


def _no_constant(_name: str) -> Any:
    raise ValueError("not a JSON number")


def decode_object(data: bytes) -> dict[str, Any] | None:
    """The Project service's answer as a JSON object of UTF-8 text; None for anything else, for a
    repeated member name at any depth, for NaN or Infinity, and for trailing data."""
    try:
        doc = json.loads(
            data.decode("utf-8"), object_pairs_hook=_no_repeats, parse_constant=_no_constant
        )
    except UnicodeDecodeError, ValueError, RecursionError:
        return None
    return doc if isinstance(doc, dict) else None
