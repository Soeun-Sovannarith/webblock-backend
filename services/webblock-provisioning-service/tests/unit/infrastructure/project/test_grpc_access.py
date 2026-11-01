"""``ProjectAccess`` over gRPC ``CheckAccess`` against a real local server (decision 0022 E7)."""

from __future__ import annotations

import io
import json
import logging
import sys
from uuid import UUID

import grpc
import pytest
from tests.unit.infrastructure.grpc.stub import access_server, allowed, denied
from webblock_commons.core import context as commons_context
from webblock_commons.core.context import RequestContext
from webblock_commons.core.user import WebBlockUser

from provisioning.core.application import (
    Action,
    Principal,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
)
from provisioning.infrastructure.project.grpc_access import GrpcProjectAccess
from provisioning.infrastructure.telemetry import correlation_scope, new_logger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

USER = UUID("0199e7a0-0000-7000-8000-0000000000aa")
PROJECT = UUID("0199e7a0-0000-7000-8000-0000000000bb")
OWNER = Principal(subject="alice", scopes=("prov.all",), user_id=USER)


def adapter(channel: grpc.aio.Channel) -> GrpcProjectAccess:
    return GrpcProjectAccess(channel, new_logger(sys.stderr, logging.INFO))


async def test_allowed_sends_user_project_and_action() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
        request = server.requests[0]
        assert (request.user_id, request.project_id) == (str(USER), str(PROJECT))
        assert request.action == Action.GET_RELEASE.value


@pytest.mark.parametrize(
    "reason", ["NOT_FOUND", "NOT_MANAGER", "PROJECT_DELETED", "PROJECT_SUSPENDED"]
)
async def test_each_known_reason_is_a_denial(reason: str) -> None:
    async with access_server() as server:
        server.answers = [denied(reason)]
        with pytest.raises(ProjectAccessDenied) as info:
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.DEPLOY_RELEASE)
        assert reason in str(info.value)


async def test_a_read_of_a_deleted_project_is_allowed_when_the_project_says_so() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)


@pytest.mark.parametrize("reason", ["SOMETHING_NEW", ""])
async def test_unknown_or_empty_reason_is_unavailable(reason: str) -> None:
    async with access_server() as server:
        server.answers = [denied(reason)]
        with pytest.raises(ProjectAccessUnavailable):
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)


@pytest.mark.parametrize(
    "code",
    [
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.INTERNAL,
    ],
)
async def test_a_failed_call_is_unavailable(code: grpc.StatusCode) -> None:
    async with access_server() as server:
        server.answers = [code]
        with pytest.raises(ProjectAccessUnavailable):
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)


async def test_no_user_id_is_denied_without_a_call() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        with pytest.raises(ProjectAccessDenied):
            await adapter(server.channel).check_access(
                Principal(subject="x", scopes=()), PROJECT, Action.GET_RELEASE
            )
        assert server.calls == 0


async def test_the_current_correlation_id_is_the_request_id() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        with correlation_scope("req-42"):
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
        assert server.metadata[0]["x-request-id"] == "req-42"


async def test_fr8_1_check_access_sends_the_user_id_and_request_id_never_a_token() -> None:
    """FR-8.1: a signed-in request's ``CheckAccess`` carries ``x-user-id`` (the body's user) and
    ``x-request-id``; no ``authorization``."""
    async with access_server() as server:
        server.answers = [allowed()]
        with correlation_scope("req-81"):
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
        sent = server.metadata[0]
        assert sent["x-user-id"] == str(USER) == server.requests[0].user_id
        assert sent["x-request-id"] == "req-81"
        assert "authorization" not in sent


async def test_fr8_1_check_access_never_sends_the_user_of_an_outer_context() -> None:
    """FR-8.1: the ``x-user-id`` is the principal the check is for, never a user the library's
    context held before the call; nothing of a call is left in that context afterwards."""
    other = WebBlockUser(user_id=UUID("0199e7a0-0000-7000-8000-0000000000cc"))
    outer = RequestContext(request_id="outer", user=other)
    async with access_server() as server:
        server.answers = [allowed()]
        with commons_context.bound(outer):
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
            assert commons_context.current() is outer
        assert server.metadata[0]["x-user-id"] == str(USER)


async def test_a_closed_channel_is_unavailable_not_an_escape() -> None:
    async with access_server() as server:
        channel = grpc.aio.insecure_channel(server.target)
        await channel.close()
        with pytest.raises(ProjectAccessUnavailable):
            await adapter(channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)


async def test_a_failed_call_keeps_the_upstream_reason() -> None:
    """Decision 0023 G14: the reason Project sent rides on the error; the error table decides
    whether a caller may see it."""
    async with access_server() as server:
        server.answers = [grpc.StatusCode.FAILED_PRECONDITION]
        server.reason = "PROJECT_ARCHIVED"
        with pytest.raises(ProjectAccessUnavailable) as info:
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
    assert info.value.upstream_reason == "PROJECT_ARCHIVED"
    assert info.value.deadline is False


async def test_a_passed_deadline_is_marked() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.DEADLINE_EXCEEDED]
        with pytest.raises(ProjectAccessUnavailable) as info:
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
    assert info.value.deadline is True
    assert info.value.upstream_reason is None


async def test_a_denial_without_a_known_reason_carries_no_upstream_reason() -> None:
    async with access_server() as server:
        server.answers = [denied("SOMETHING_NEW")]
        with pytest.raises(ProjectAccessUnavailable) as info:
            await adapter(server.channel).check_access(OWNER, PROJECT, Action.GET_RELEASE)
    assert (info.value.upstream_reason, info.value.deadline) == (None, False)


# --- log lines: the same records as the HTTP adapter's (test_access.py), through the shared
# helpers user_id_or_deny, denied and failed_internally.


def _logged(log: io.StringIO) -> list[dict[str, object]]:
    """Every JSON record written, without its time."""
    records = [json.loads(line) for line in log.getvalue().splitlines()]
    for record in records:
        assert isinstance(record.pop("time"), str)
    return records


async def test_fr3_a_principal_without_a_user_id_logs_a_denial_with_fixed_fields() -> None:
    """FR-3, TR-33: the same denial record as the HTTP adapter's, never the subject."""
    log = io.StringIO()
    async with access_server() as server:
        with pytest.raises(ProjectAccessDenied):
            await GrpcProjectAccess(server.channel, new_logger(log, logging.DEBUG)).check_access(
                Principal(subject="agent:bot", scopes=("prov.read",)),
                PROJECT,
                Action.DEPLOY_RELEASE,
            )
    assert _logged(log) == [
        {
            "level": "info",
            "msg": "project access denied without a call: the principal has no user ID",
            "component": "project-access",
            "project_id": str(PROJECT),
            "action": "deploy_release",
            "reason": "no_user_id",
        }
    ]


async def test_fr3_a_denial_logs_the_project_action_and_reason_only() -> None:
    """FR-3, TR-33: the same denial record as the HTTP adapter's, never the user ID."""
    log = io.StringIO()
    async with access_server() as server:
        server.answers = [denied("NOT_MANAGER")]
        with pytest.raises(ProjectAccessDenied):
            await GrpcProjectAccess(server.channel, new_logger(log, logging.DEBUG)).check_access(
                OWNER, PROJECT, Action.DEPLOY_RELEASE
            )
    assert _logged(log) == [
        {
            "level": "info",
            "msg": "project service denied project access",
            "component": "project-access",
            "project_id": str(PROJECT),
            "action": "deploy_release",
            "reason": "NOT_MANAGER",
        }
    ]
    assert str(USER) not in log.getvalue()


async def test_fr5_an_internal_failure_logs_the_error_class_only() -> None:
    """FR-5, TR-33: a closed channel fails closed with the same ``internal`` record as the HTTP
    adapter's: the error's class name, never its text or the target."""
    log = io.StringIO()
    async with access_server() as server:
        channel = grpc.aio.insecure_channel(server.target)
        await channel.close()
        with pytest.raises(ProjectAccessUnavailable) as info:
            await GrpcProjectAccess(channel, new_logger(log, logging.DEBUG)).check_access(
                OWNER, PROJECT, Action.GET_RELEASE
            )
        target = server.target
    assert str(info.value) == "project service: internal"
    (record,) = _logged(log)
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
    assert target not in log.getvalue()
