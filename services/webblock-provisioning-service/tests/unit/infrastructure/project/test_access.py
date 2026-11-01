"""``ProjectAccess`` over HTTP to the Project service (task PA; decision 0017 section 2), against a
local stub of ``POST /internal/v1/access-checks`` (``stub.py``), never a mock of the client.

The mapping: ``200`` with ``success: true`` and ``data.allowed: true`` allows; ``data.allowed:
false`` with ``NOT_FOUND``, ``NOT_MANAGER``, ``PROJECT_DELETED`` or ``PROJECT_SUSPENDED`` is
``ProjectAccessDenied``; anything else (timeout, connection error, non-200, ``success: false``, an
unknown reason, a malformed, encoded or oversized body) is ``ProjectAccessUnavailable`` with no
cause, no response text or URL. A principal without a user ID is denied without a call.

No token (owner, 18 October 2026, authentication design A3): the call carries no
``Authorization`` header; inside the mesh the caller is its mTLS identity.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.unit.infrastructure.project.stub import (
    AccessCheckServer,
    access_check_server,
    allowed,
    closed_port,
    denied,
    envelope,
)
from webblock_commons.core import context as commons_context
from webblock_commons.core.context import RequestContext
from webblock_commons.core.user import WebBlockUser

from provisioning.core.application import (
    Action,
    EnabledOperations,
    OperationGate,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
)
from provisioning.infrastructure.project import (
    ACCESS_CHECK_PATH,
    CALL_TIMEOUT,
    DENIAL_REASONS,
    MAX_RESPONSE_BYTES,
    ProjectServiceAccess,
)
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.infrastructure.telemetry.tracing import (
    configure_tracing,
    shutdown_tracing,
    span,
)

USER = UUID("0199e7a0-0000-7000-8000-0000000000aa")
PROJECT = UUID("0199e7a0-0000-7000-8000-0000000000bb")
OWNER = Principal(subject="alice", scopes=("prov.all",), user_id=USER)
BODY_MARK = "BODY-MARKER-77d1"


class Harness:
    def __init__(self, server: AccessCheckServer, base_url: str, timeout: float) -> None:
        self.server = server
        self.log = io.StringIO()
        self.access = ProjectServiceAccess(
            base_url=base_url,
            timeout=timeout,
            logger=new_logger(self.log, logging.DEBUG),
        )

    async def check(
        self, principal: Principal = OWNER, action: Action = Action.GET_RELEASE
    ) -> None:
        await self.access.check_access(principal, PROJECT, action)

    async def unavailable(self, action: Action = Action.GET_RELEASE) -> ProjectAccessUnavailable:
        with pytest.raises(ProjectAccessUnavailable) as err:
            await self.check(action=action)
        assert err.value.__cause__ is None
        assert err.value.__suppress_context__
        text = str(err.value)
        assert BODY_MARK not in text
        assert "127.0.0.1" not in text
        return err.value

    async def denied(self, action: Action = Action.GET_RELEASE) -> ProjectAccessDenied:
        with pytest.raises(ProjectAccessDenied) as err:
            await self.check(action=action)
        assert not isinstance(err.value, ProjectAccessUnavailable)
        return err.value


@asynccontextmanager
async def harness(prefix: str = "", call_timeout: float = CALL_TIMEOUT) -> AsyncIterator[Harness]:
    async with access_check_server() as server:
        h = Harness(server, server.url(prefix), call_timeout)
        try:
            yield h
        finally:
            await h.access.aclose()


# --- the request ----------------------------------------------------------------------------------


def test_the_contract_constants() -> None:
    assert ACCESS_CHECK_PATH == "/internal/v1/access-checks"
    assert CALL_TIMEOUT == 2.0
    assert MAX_RESPONSE_BYTES == 64 * 1024
    assert frozenset({"NOT_FOUND", "NOT_MANAGER", "PROJECT_DELETED", "PROJECT_SUSPENDED"}) == (
        DENIAL_REASONS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_the_request_carries_the_ids_and_snake_case_action_and_no_token(
    action: Action,
) -> None:
    async with harness() as h:
        with correlation_scope("corr-0123456789"):
            await h.check(action=action)
    [request] = h.server.requests
    assert request.method == "POST"
    assert request.target == "/internal/v1/access-checks"
    assert "authorization" not in request.headers
    assert request.headers["x-request-id"] == "corr-0123456789"
    assert request.headers["content-type"] == "application/json"
    assert request.json() == {
        "userId": str(USER),
        "projectId": str(PROJECT),
        "action": action.value,
    }
    assert list(request.json()) == ["userId", "projectId", "action"]


@pytest.mark.asyncio
async def test_fr8_1_the_http_access_check_sends_the_user_request_and_trace_ids() -> None:
    """FR-8.1 on the HTTP path (``project-service`` mode): the check carries ``x-user-id`` (the
    body's user), ``x-request-id`` and the ``traceparent`` of the current span; no
    ``authorization``."""
    exporter = InMemorySpanExporter()
    configure_tracing(
        service_name="provisioning-test", endpoint="http://c:4318", ratio=1.0, exporter=exporter
    )
    try:
        async with harness() as h:
            with correlation_scope("corr-81"), span("api request"):
                await h.check()
    finally:
        shutdown_tracing()
    (finished,) = exporter.get_finished_spans()
    assert finished.context is not None
    [request] = h.server.requests
    assert request.headers["x-user-id"] == str(USER) == request.json()["userId"]
    assert request.headers["x-request-id"] == "corr-81"
    assert request.headers["traceparent"].startswith(f"00-{finished.context.trace_id:032x}-")
    assert "authorization" not in request.headers


@pytest.mark.asyncio
async def test_fr8_1_the_http_access_check_sends_the_principals_user_never_an_outer_one() -> None:
    """FR-8.1: ``x-user-id`` is the principal the check is for, never a user the library's
    context held before; with tracing off there is no ``traceparent``."""
    other = WebBlockUser(user_id=UUID("0199e7a0-0000-7000-8000-0000000000cc"))
    outer = RequestContext(request_id="outer", user=other)
    async with harness() as h:
        with commons_context.bound(outer), correlation_scope("corr-82"):
            await h.check()
            assert commons_context.current() is outer
    [request] = h.server.requests
    assert request.headers["x-user-id"] == str(USER)
    assert request.headers["x-request-id"] == "corr-82"
    assert "traceparent" not in request.headers


@pytest.mark.asyncio
async def test_a_cookie_the_project_service_sets_is_never_sent_back() -> None:
    """Finding M-1 of the PR-3 review: the client keeps no cookies, so a session the callee (or
    anything on the path) starts never carries one user's check into another's."""
    other = Principal(subject="bob", scopes=("prov.all",), user_id=UUID(int=USER.int + 1))
    async with harness() as h:
        h.server.headers = {
            "Content-Type": "application/json",
            "Set-Cookie": "JSESSIONID=abc; Path=/",
        }
        await h.check()
        h.server.headers = {
            "Content-Type": "application/json",
            "Set-Cookie": "SESSION=def; Domain=127.0.0.1; Path=/internal",
        }
        await h.access.check_access(other, PROJECT, Action.GET_RELEASE)
        await h.access.check_access(other, PROJECT, Action.GET_RELEASE)
    assert [r.headers.get("cookie") for r in h.server.requests] == [None, None, None]


@pytest.mark.asyncio
async def test_without_a_current_request_id_a_fresh_one_is_sent() -> None:
    async with harness() as h:
        await h.check()
        await h.check()
    ids = [r.headers["x-request-id"] for r in h.server.requests]
    assert all(UUID(i).version == 7 for i in ids)
    assert ids[0] != ids[1]


@pytest.mark.asyncio
async def test_a_base_url_with_a_path_prefix_keeps_it() -> None:
    async with harness(prefix="/project/") as h:
        await h.check()
    assert h.server.requests[0].target == "/project/internal/v1/access-checks"


@pytest.mark.asyncio
async def test_a_principal_without_a_user_id_is_denied_without_a_call() -> None:
    """No user, no project access (owner, decision 0017 section 2): an operator's or an agent's
    token without a user ID never reaches the Project service."""
    async with harness() as h:
        for principal in (
            Principal(subject="prototype-operator", scopes=("provisioning:all",)),
            Principal(subject="agent:bot", scopes=("prov.read",)),
        ):
            for action in Action:
                with pytest.raises(ProjectAccessDenied):
                    await h.access.check_access(principal, PROJECT, action)
    assert h.server.requests == []


# --- the mapping --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowed_returns() -> None:
    async with harness() as h:
        await h.check()
        h.server.body = envelope({"allowed": True}, error=None)
        await h.check(action=Action.DEPLOY_RELEASE)
    assert len(h.server.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(DENIAL_REASONS))
async def test_each_denial_reason_is_project_access_denied(reason: str) -> None:
    async with harness() as h:
        h.server.body = denied(reason)
        err = await h.denied(action=Action.DEPLOY_RELEASE)
    assert BODY_MARK not in str(err)
    assert reason in h.log.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"allowed": False, "reason": "BANNED"},
        {"allowed": False, "reason": "not_found"},
        {"allowed": False, "reason": None},
        {"allowed": False, "reason": 1},
        {"allowed": False},
    ],
    ids=["unknown", "lower-case", "null", "number", "missing"],
)
async def test_a_denial_without_a_known_reason_is_unavailable(data: dict[str, object]) -> None:
    async with harness() as h:
        h.server.body = envelope(data)
        await h.unavailable()


@pytest.mark.asyncio
async def test_success_false_is_unavailable_even_with_allowed_data() -> None:
    async with harness() as h:
        h.server.body = envelope(
            {"allowed": True}, success=False, error={"code": "X", "message": BODY_MARK}
        )
        await h.unavailable()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 204, 302, 400, 401, 403, 404, 409, 429, 500, 503])
async def test_any_status_but_200_is_unavailable(status: int) -> None:
    async with harness() as h:
        h.server.status = status
        h.server.body = allowed()  # even an "allowed" body does not count
        err = await h.unavailable()
    assert f"HTTP {status}" in str(err)


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed() -> None:
    async with harness() as h:
        h.server.status = 307
        h.server.location = h.server.url("/elsewhere")
        await h.unavailable()
    assert [r.target for r in h.server.requests] == ["/internal/v1/access-checks"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json " + BODY_MARK.encode(),
        b"<html>" + BODY_MARK.encode() + b"</html>",
        b"[]",
        b"null",
        b'{"success": true}',
        b'{"success": true, "data": null}',
        b'{"success": true, "data": []}',
        b'{"success": "true", "data": {"allowed": true}}',
        b'{"success": 1, "data": {"allowed": true}}',
        b'{"success": true, "data": {"allowed": "true"}}',
        b'{"success": true, "data": {"allowed": 1}}',
        b'{"success": true, "data": {}}',
        b'{"success": true, "success": true, "data": {"allowed": true}}',
        b'{"success": true, "data": {"allowed": true, "allowed": false}}',
        b'{"success": true, "data": {"allowed": true}, "meta": NaN}',
        b'{"success": true, "data": {"allowed": true}} trailing',
        b"\xff\xfe",
    ],
)
async def test_a_malformed_body_is_unavailable(body: bytes) -> None:
    async with harness() as h:
        h.server.body = body
        await h.unavailable()


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True], ids=["declared", "chunked"])
async def test_an_oversized_body_is_unavailable(chunked: bool) -> None:
    padding = "x" * MAX_RESPONSE_BYTES
    body = json.dumps({"success": True, "data": {"allowed": True}, "meta": padding}).encode()
    async with harness() as h:
        h.server.body = body
        h.server.chunked = chunked
        await h.unavailable()


@pytest.mark.asyncio
async def test_a_body_at_the_limit_is_read() -> None:
    base = json.dumps({"success": True, "data": {"allowed": True}, "meta": ""}).encode()
    body = json.dumps(
        {"success": True, "data": {"allowed": True}, "meta": "x" * (MAX_RESPONSE_BYTES - len(base))}
    ).encode()
    assert len(body) == MAX_RESPONSE_BYTES
    async with harness() as h:
        h.server.body = body
        h.server.chunked = True
        await h.check()


@pytest.mark.asyncio
async def test_a_compressed_body_is_unavailable() -> None:
    async with harness() as h:
        h.server.body = gzip.compress(allowed())
        h.server.headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
        await h.unavailable()
    assert h.server.requests[0].headers["accept-encoding"] == "identity"


@pytest.mark.asyncio
async def test_a_slow_answer_is_cut_at_the_timeout() -> None:
    async with harness(call_timeout=0.2) as h:
        h.server.delay = 2.0
        started = time.monotonic()
        await h.unavailable()
        elapsed = time.monotonic() - started
    assert elapsed < 1.5


@pytest.mark.asyncio
async def test_a_dripping_answer_is_cut_at_the_total_bound() -> None:
    """Finding L-1 of P-J1: each 8-byte piece arrives well within the client's per-read timeout,
    so only the total bound (``asyncio.timeout``) can stop an answer that takes a second in all."""
    async with harness(call_timeout=0.3) as h:
        h.server.drip = 0.1
        assert len(h.server.body) // 8 * h.server.drip > 3 * 0.3  # the whole answer is slow
        started = time.monotonic()
        err = await h.unavailable()
        elapsed = time.monotonic() - started
    assert str(err) == "project service: timeout"
    assert 0.25 <= elapsed < 0.6


@pytest.mark.asyncio
async def test_a_connection_closed_without_an_answer_is_unavailable() -> None:
    async with harness() as h:
        h.server.hang_up = True
        await h.unavailable()


@pytest.mark.asyncio
async def test_connection_refused_is_unavailable() -> None:
    port = await closed_port()
    log = io.StringIO()
    access = ProjectServiceAccess(
        base_url=f"http://127.0.0.1:{port}",
        timeout=CALL_TIMEOUT,
        logger=new_logger(log, logging.DEBUG),
    )
    try:
        with pytest.raises(ProjectAccessUnavailable) as err:
            await access.check_access(OWNER, PROJECT, Action.GET_RELEASE)
    finally:
        await access.aclose()
    assert err.value.__cause__ is None
    assert str(port) not in str(err.value)
    assert str(port) not in log.getvalue()


@pytest.mark.asyncio
async def test_proxy_settings_of_the_environment_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = await closed_port()
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{port}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    async with harness() as h:
        await h.check()
    assert len(h.server.requests) == 1


# --- through the operation gate -------------------------------------------------------------------


class _Permit:
    def permit(self, principal: Principal, action: Action) -> None:
        del principal, action


@pytest.mark.asyncio
async def test_the_gate_reports_denial_as_denied_and_failure_as_unavailable() -> None:
    async with harness() as h:
        gate = OperationGate(policy=_Permit(), access=h.access, enabled=EnabledOperations(Action))
        await gate.check(OWNER, Action.LIST_RELEASES, PROJECT)
        h.server.body = denied("PROJECT_SUSPENDED")
        with pytest.raises(ProjectAccessDenied) as denial:
            await gate.check(OWNER, Action.DEPLOY_RELEASE, PROJECT)
        assert not isinstance(denial.value, ProjectAccessUnavailable)
        h.server.status = 503
        with pytest.raises(ProjectAccessUnavailable) as failure:
            await gate.check(OWNER, Action.GET_RELEASE, PROJECT)
    for exc in (denial.value, failure.value):
        chain: BaseException | None = exc
        while chain is not None:
            assert "127.0.0.1" not in str(chain)
            chain = chain.__cause__


# --- secrets -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_body_or_user_reaches_any_log_record(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    async with harness(call_timeout=0.2) as h:
        await h.check()
        h.server.body = denied("NOT_MANAGER")
        await h.denied()
        h.server.body = b"oops " + BODY_MARK.encode()
        await h.unavailable()
        h.server.status = 500
        await h.unavailable()
        h.server.status, h.server.delay = 200, 2.0
        await h.unavailable()
    records = h.log.getvalue() + "\n".join(
        f"{r.getMessage()} {r.args!r} {r.__dict__!r}" for r in caplog.records
    )
    assert BODY_MARK not in records
    assert str(USER) not in h.log.getvalue()
    assert "alice" not in h.log.getvalue()
    assert repr(h.access) == "ProjectServiceAccess()"


# --- log lines -----------------------------------------------------------------------------------
# The fields the shared helpers write (user_id_or_deny, denied, failed_internally): the gRPC
# adapter's tests expect the same records.


def _logged(log: io.StringIO) -> list[dict[str, object]]:
    """Every JSON record written, without its time."""
    records = [json.loads(line) for line in log.getvalue().splitlines()]
    for record in records:
        assert isinstance(record.pop("time"), str)
    return records


@pytest.mark.asyncio
async def test_fr3_a_principal_without_a_user_id_logs_a_denial_with_fixed_fields() -> None:
    """FR-3, TR-33: the denial without a call names the project, the action and the reason, never
    the subject."""
    async with harness() as h:
        with pytest.raises(ProjectAccessDenied):
            await h.access.check_access(
                Principal(subject="agent:bot", scopes=("prov.read",)),
                PROJECT,
                Action.DEPLOY_RELEASE,
            )
    assert _logged(h.log) == [
        {
            "level": "info",
            "msg": "project access denied without a call: the principal has no user ID",
            "component": "project-access",
            "project_id": str(PROJECT),
            "action": "deploy_release",
            "reason": "no_user_id",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", sorted(DENIAL_REASONS))
async def test_fr3_a_denial_logs_the_project_action_and_reason_only(reason: str) -> None:
    """FR-3, TR-33: the Project service's denial is logged with its reason; never the user ID,
    the subject or the body."""
    async with harness() as h:
        h.server.body = denied(reason)
        await h.denied(action=Action.DEPLOY_RELEASE)
    assert _logged(h.log) == [
        {
            "level": "info",
            "msg": "project service denied project access",
            "component": "project-access",
            "project_id": str(PROJECT),
            "action": "deploy_release",
            "reason": reason,
        }
    ]


@pytest.mark.asyncio
async def test_fr5_an_internal_failure_logs_the_error_class_only() -> None:
    """FR-5, TR-33: a failure no other branch names (here the closed client) fails closed and is
    logged as ``internal`` with the error's class name, never its text or the URL."""
    async with harness() as h:
        await h.access.aclose()
        err = await h.unavailable()
    assert str(err) == "project service: internal"
    (record,) = _logged(h.log)
    error = record.pop("error")
    assert isinstance(error, str) and error.isidentifier()
    assert record == {
        "level": "warning",
        "msg": "project access check failed",
        "component": "project-access",
        "project_id": str(PROJECT),
        "action": "get_release",
        "reason": "internal",
    }
    assert "127.0.0.1" not in h.log.getvalue()


def test_construction_refuses_an_unusable_url_or_timeout() -> None:
    logger = new_logger(io.StringIO(), logging.INFO)
    for url in (
        "ftp://project.test",
        "http://user:" + BODY_MARK + "@project.test",
        "http://project.test/?a=1",
        "http://project.test/#f",
        "project.test",
        "http://",
    ):
        with pytest.raises(ValueError) as err:
            ProjectServiceAccess(base_url=url, timeout=1.0, logger=logger)
        assert BODY_MARK not in str(err.value)
    for timeout in (0.0, -1.0):
        with pytest.raises(ValueError):
            ProjectServiceAccess(base_url="http://project.test", timeout=timeout, logger=logger)


@pytest.mark.asyncio
async def test_a_closed_adapter_answers_unavailable() -> None:
    async with harness() as h:
        await h.access.aclose()
        await h.unavailable()
    assert h.server.requests == []


@pytest.mark.asyncio
async def test_concurrent_checks_each_get_their_own_answer() -> None:
    async with harness() as h:
        await asyncio.gather(*(h.check(action=a) for a in Action))
    assert sorted(r.json()["action"] for r in h.server.requests) == sorted(a.value for a in Action)
