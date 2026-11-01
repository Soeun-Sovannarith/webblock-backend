"""The shared unary call helper against a real local gRPC server (decision 0022 E7)."""

from __future__ import annotations

from datetime import timedelta

import grpc
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tests.unit.infrastructure.grpc.stub import METHOD, Script, access_server, allowed

from provisioning.infrastructure.grpc._gen.webblock.project.v1 import access_pb2
from provisioning.infrastructure.grpc.call import CallFailure, unary
from provisioning.infrastructure.telemetry.tracing import configure_tracing, shutdown_tracing

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def no_sleep(_: float) -> None:
    return None


def req() -> access_pb2.CheckAccessRequest:
    return access_pb2.CheckAccessRequest(user_id="u", project_id="p", action="get_release")


async def call(server: Script, **kw: object) -> access_pb2.CheckAccessResponse:
    return await unary(
        server.channel,
        METHOD,
        req(),
        access_pb2.CheckAccessResponse,
        request_id="r1",
        sleep=no_sleep,
        **kw,  # type: ignore[arg-type]
    )


async def test_success_returns_the_response() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        assert (await call(server)).allowed is True
        assert server.calls == 1


async def test_unavailable_retried_twice_then_fails_closed() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.UNAVAILABLE] * 3
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.code is grpc.StatusCode.UNAVAILABLE
        assert server.calls == 3


async def test_unavailable_then_ok_succeeds() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.UNAVAILABLE, allowed()]
        assert (await call(server)).allowed is True
        assert server.calls == 2


async def test_waits_are_100_and_300_ms_by_default() -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    async with access_server() as server:
        server.answers = [grpc.StatusCode.UNAVAILABLE] * 3
        with pytest.raises(CallFailure):
            await unary(
                server.channel,
                METHOD,
                req(),
                access_pb2.CheckAccessResponse,
                request_id="r1",
                sleep=record,
            )
    assert waits == [0.1, 0.3]


async def test_deadline_exceeded_not_retried() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.DEADLINE_EXCEEDED]
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.code is grpc.StatusCode.DEADLINE_EXCEEDED
        assert server.calls == 1


@pytest.mark.parametrize(
    "code", [grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.INTERNAL, grpc.StatusCode.NOT_FOUND]
)
async def test_other_codes_are_not_retried(code: grpc.StatusCode) -> None:
    async with access_server() as server:
        server.answers = [code]
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.code is code
        assert server.calls == 1


async def test_metadata_carries_request_id() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await call(server)
        assert server.metadata[0]["x-request-id"] == "r1"


async def test_metadata_has_no_traceparent_when_tracing_is_off() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await call(server)
        assert "traceparent" not in server.metadata[0]


async def test_metadata_carries_the_traceparent_of_the_grpc_span_when_tracing_is_on() -> None:
    exporter = InMemorySpanExporter()
    configure_tracing(
        service_name="provisioning-test", endpoint="http://c:4318", ratio=1.0, exporter=exporter
    )
    try:
        async with access_server() as server:
            server.answers = [allowed()]
            await call(server)
    finally:
        shutdown_tracing()
    (span,) = exporter.get_finished_spans()
    assert span.name == f"grpc {METHOD}"
    assert span.context is not None
    assert server.metadata[0]["traceparent"].startswith(
        f"00-{span.context.trace_id:032x}-{span.context.span_id:016x}-"
    )


async def test_error_info_reason_is_read() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.PERMISSION_DENIED]
        server.reason = "NOT_MANAGER"
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.reason == "NOT_MANAGER"


async def test_no_error_info_means_no_reason() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.INTERNAL]
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.reason is None


async def test_deadline_is_two_seconds_by_default() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await call(server)
        left = server.time_remaining[0]
        assert left is not None and 1.0 < left <= 2.5


async def test_deadline_can_be_set() -> None:
    async with access_server() as server:
        server.answers = [allowed()]
        await call(server, deadline=timedelta(seconds=30))
        left = server.time_remaining[0]
        assert left is not None and left > 20


async def test_connection_refused_is_a_call_failure() -> None:
    async with access_server() as server:
        channel = grpc.aio.insecure_channel("127.0.0.1:1")
        try:
            with pytest.raises(CallFailure) as info:
                await unary(
                    channel,
                    METHOD,
                    req(),
                    access_pb2.CheckAccessResponse,
                    request_id="r1",
                    sleep=no_sleep,
                )
            assert info.value.code is grpc.StatusCode.UNAVAILABLE
        finally:
            await channel.close()
        assert server.calls == 0


async def test_malformed_status_details_keep_the_calls_own_code() -> None:
    async with access_server() as server:
        server.answers = [grpc.StatusCode.INTERNAL]
        server.malformed_details = True
        with pytest.raises(CallFailure) as info:
            await call(server)
        assert info.value.code is grpc.StatusCode.INTERNAL
        assert info.value.reason is None
