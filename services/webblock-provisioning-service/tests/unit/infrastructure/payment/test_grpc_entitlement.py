"""``GrpcEntitlement`` against a real local gRPC server (decision 0022 E7)."""

from __future__ import annotations

import io
import logging
from uuid import UUID

import grpc
import pytest
from tests.unit.infrastructure.payment.stub import answer, entitlement_server

from provisioning.core.application import DependencyUnavailable, LimitReached
from provisioning.core.domain import CommandKind
from provisioning.infrastructure.grpc._gen.webblock.payment.v1 import entitlement_pb2 as pb
from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement
from provisioning.infrastructure.telemetry import new_logger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PID = UUID("0199e7a0-0000-7000-8000-0000000000bb")
ENTITLED = pb.HOSTING_ENTITLED
ENDED = pb.HOSTING_ENDED
ALLOWED = pb.PUBLISH_ALLOWED
LIMIT = pb.PUBLISH_LIMIT_REACHED


def client(channel: grpc.aio.Channel, sink: io.StringIO | None = None) -> GrpcEntitlement:
    return GrpcEntitlement(channel, new_logger(sink or io.StringIO(), logging.INFO))


async def test_entitled_deploy_is_allowed_and_sends_the_project() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, ALLOWED)]
        await client(server.channel).check(PID, CommandKind.DEPLOY)
        assert server.requests[0].project_id == str(PID)
        assert "x-request-id" in server.metadata[0]


async def test_publish_limit_reached_refuses_a_deploy() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, LIMIT)]
        with pytest.raises(LimitReached):
            await client(server.channel).check(PID, CommandKind.DEPLOY)


async def test_publish_limit_reached_does_not_refuse_a_promote() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, LIMIT)]
        await client(server.channel).check(PID, CommandKind.PROMOTE)


@pytest.mark.parametrize("kind", [CommandKind.DEPLOY, CommandKind.PROMOTE])
async def test_ended_hosting_is_limit_reached(kind: CommandKind) -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENDED, ALLOWED)]
        with pytest.raises(LimitReached):
            await client(server.channel).check(PID, kind)


@pytest.mark.parametrize(
    ("hosting", "publish"),
    [
        (pb.HOSTING_UNSPECIFIED, ALLOWED),
        (ENTITLED, pb.PUBLISH_UNSPECIFIED),
    ],
)
async def test_unspecified_is_unavailable_for_deploy(hosting: int, publish: int) -> None:
    async with entitlement_server() as server:
        server.answers = [answer(hosting, publish)]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.DEPLOY)


async def test_unspecified_publish_does_not_matter_for_promote() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, pb.PUBLISH_UNSPECIFIED)]
        await client(server.channel).check(PID, CommandKind.PROMOTE)


async def test_unknown_hosting_value_fails_closed() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(7, 1)]  # an enum number a newer Payment may send
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.DEPLOY)
        assert await client(server.channel).entitlement_after(PID, "evt-1") is None


async def test_unknown_publish_value_fails_closed_for_deploy() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, 9)]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.DEPLOY)


async def test_a_kind_other_than_deploy_or_promote_is_unavailable() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(ENTITLED, ALLOWED)]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.ROLLBACK)
        assert server.calls == 0


async def test_unavailable_three_times_is_unavailable_after_two_retries() -> None:
    async with entitlement_server() as server:
        server.answers = [grpc.StatusCode.UNAVAILABLE]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.DEPLOY)
        assert server.calls == 3
        assert await client(server.channel).entitlement_after(PID, "evt-1") is None


async def test_other_failures_are_not_retried() -> None:
    async with entitlement_server() as server:
        server.answers = [grpc.StatusCode.INTERNAL]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel).check(PID, CommandKind.DEPLOY)
        assert server.calls == 1


async def test_entitlement_after_is_true_when_entitled_and_false_when_ended() -> None:
    async with entitlement_server() as server:
        c = client(server.channel)
        server.answers = [answer(ENTITLED, LIMIT)]
        assert await c.entitlement_after(PID, "evt-1") is True
        server.calls = 0
        server.answers = [answer(ENDED, ALLOWED)]
        assert await c.entitlement_after(PID, "evt-1") is False


async def test_entitlement_after_is_none_when_unspecified() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(pb.HOSTING_UNSPECIFIED, ALLOWED)]
        assert await client(server.channel).entitlement_after(PID, "evt-1") is None


async def test_a_non_rpc_error_is_logged_by_class_only_and_fails_closed() -> None:
    sink = io.StringIO()
    async with entitlement_server() as server:
        pass  # the server and the channel are closed now: a closed channel raises a usage error
    c = client(server.channel, sink)
    with pytest.raises(DependencyUnavailable):
        await c.check(PID, CommandKind.DEPLOY)
    assert await c.entitlement_after(PID, "evt-1") is None
    assert "Error" in sink.getvalue()
    assert str(PID) in sink.getvalue()


async def test_an_answer_for_the_same_project_is_used() -> None:
    async with entitlement_server() as server:
        same = answer(ENTITLED, ALLOWED)
        same.project_id = str(PID).upper()
        server.answers = [same]
        await client(server.channel).check(PID, CommandKind.DEPLOY)


@pytest.mark.parametrize("other", ["0199e7a0-0000-7000-8000-0000000000cc", "not-a-uuid"])
async def test_an_answer_for_another_project_fails_closed(other: str) -> None:
    sink = io.StringIO()
    async with entitlement_server() as server:
        wrong = answer(ENTITLED, ALLOWED)
        wrong.project_id = other
        server.answers = [wrong]
        with pytest.raises(DependencyUnavailable):
            await client(server.channel, sink).check(PID, CommandKind.DEPLOY)
        assert await client(server.channel, sink).entitlement_after(PID, "evt-1") is None
    assert "project_mismatch" in sink.getvalue()


async def test_a_failed_call_keeps_the_upstream_reason_and_no_deadline() -> None:
    """Decision 0023 G14: Payment's ErrorInfo reason rides on the error, for the error table."""
    async with entitlement_server() as server:
        server.answers = [grpc.StatusCode.FAILED_PRECONDITION]
        server.reason = "PAYMENT_ACCOUNT_LOCKED"
        with pytest.raises(DependencyUnavailable) as info:
            await client(server.channel).check(PID, CommandKind.DEPLOY)
    assert info.value.upstream_reason == "PAYMENT_ACCOUNT_LOCKED"
    assert info.value.deadline is False


async def test_a_passed_deadline_is_marked() -> None:
    async with entitlement_server() as server:
        server.answers = [grpc.StatusCode.DEADLINE_EXCEEDED]
        with pytest.raises(DependencyUnavailable) as info:
            await client(server.channel).check(PID, CommandKind.DEPLOY)
    assert info.value.deadline is True
    assert info.value.upstream_reason is None


async def test_an_unusable_answer_carries_no_upstream_reason() -> None:
    async with entitlement_server() as server:
        server.answers = [answer(0, 0)]
        with pytest.raises(DependencyUnavailable) as info:
            await client(server.channel).check(PID, CommandKind.DEPLOY)
    assert (info.value.upstream_reason, info.value.deadline) == (None, False)
