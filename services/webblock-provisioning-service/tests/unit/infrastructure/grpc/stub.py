"""A real ``grpc.aio`` server on 127.0.0.1:0 for the unit tests, serving ``CheckAccess`` from a
script: ``answers`` holds one item per attempt, either a status code (the call ends with it) or a
``CheckAccessResponse`` (the call succeeds). ``details`` is an optional ErrorInfo reason sent with
an error. The server records metadata, calls and the time left of each call."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import grpc
import grpc.aio
from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status

from provisioning.infrastructure.grpc._gen.webblock.project.v1 import access_pb2
from provisioning.infrastructure.grpc.call import open_channel

METHOD = "/webblock.project.v1.AccessService/CheckAccess"


class Script:
    def __init__(self) -> None:
        self.answers: list[grpc.StatusCode | access_pb2.CheckAccessResponse] = []
        self.reason: str | None = None
        self.malformed_details = False
        self.calls = 0
        self.metadata: list[dict[str, str]] = []
        self.requests: list[access_pb2.CheckAccessRequest] = []
        self.time_remaining: list[float | None] = []
        self.channel: grpc.aio.Channel
        self.target = ""


async def _handle(
    script: Script, request: access_pb2.CheckAccessRequest, context: grpc.aio.ServicerContext
) -> access_pb2.CheckAccessResponse:
    script.calls += 1
    script.requests.append(request)
    script.metadata.append({k: str(v) for k, v in context.invocation_metadata()})
    script.time_remaining.append(context.time_remaining())
    answer = script.answers[min(script.calls, len(script.answers)) - 1]
    if isinstance(answer, access_pb2.CheckAccessResponse):
        return answer
    if script.malformed_details:
        context.set_trailing_metadata((("grpc-status-details-bin", b"\xff\x00garbage"),))
    if script.reason is not None:
        detail = any_pb2.Any()
        detail.Pack(error_details_pb2.ErrorInfo(reason=script.reason, domain="webblock"))
        status = status_pb2.Status(
            code=code_pb2.Code.PERMISSION_DENIED
            if answer is grpc.StatusCode.PERMISSION_DENIED
            else answer.value[0],
            message="x",
            details=[detail],
        )
        await context.abort_with_status(rpc_status.to_status(status))
    await context.abort(answer, "scripted")
    raise AssertionError("unreachable")


@asynccontextmanager
async def access_server() -> AsyncIterator[Script]:
    script = Script()

    async def handler(
        request: access_pb2.CheckAccessRequest, context: grpc.aio.ServicerContext
    ) -> access_pb2.CheckAccessResponse:
        return await _handle(script, request, context)

    generic = grpc.method_handlers_generic_handler(
        "webblock.project.v1.AccessService",
        {
            "CheckAccess": grpc.unary_unary_rpc_method_handler(
                handler,
                request_deserializer=access_pb2.CheckAccessRequest.FromString,
                response_serializer=access_pb2.CheckAccessResponse.SerializeToString,
            )
        },
    )
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((generic,))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    # The channel every host builds (``open_channel``), so the headers are the ones sent for real.
    async with open_channel(f"127.0.0.1:{port}") as channel:
        script.channel = channel
        script.target = f"127.0.0.1:{port}"
        try:
            yield script
        finally:
            await server.stop(None)


def allowed() -> access_pb2.CheckAccessResponse:
    return access_pb2.CheckAccessResponse(allowed=True)


def denied(reason: str) -> access_pb2.CheckAccessResponse:
    return access_pb2.CheckAccessResponse(allowed=False, reason=reason)
