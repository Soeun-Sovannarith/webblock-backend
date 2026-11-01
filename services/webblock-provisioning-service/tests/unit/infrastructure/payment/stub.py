"""A real ``grpc.aio`` server on 127.0.0.1:0 serving ``GetEntitlement`` from a script: ``answers``
holds one item per attempt, either a status code (the call ends with it) or a
``GetEntitlementResponse`` (the call succeeds). ``reason``, when set, is the ErrorInfo reason
sent with a status code."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import grpc
import grpc.aio
from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2
from grpc_status import rpc_status

from provisioning.infrastructure.grpc._gen.webblock.payment.v1 import entitlement_pb2
from provisioning.infrastructure.grpc.call import open_channel

SERVICE = "webblock.payment.v1.EntitlementService"


class Script:
    def __init__(self) -> None:
        self.answers: list[grpc.StatusCode | entitlement_pb2.GetEntitlementResponse] = []
        self.calls = 0
        self.metadata: list[dict[str, str]] = []
        self.requests: list[entitlement_pb2.GetEntitlementRequest] = []
        self.channel: grpc.aio.Channel
        self.target = ""
        self.reason: str | None = None


@asynccontextmanager
async def entitlement_server() -> AsyncIterator[Script]:
    script = Script()

    async def handler(
        request: entitlement_pb2.GetEntitlementRequest, context: grpc.aio.ServicerContext
    ) -> entitlement_pb2.GetEntitlementResponse:
        script.calls += 1
        script.requests.append(request)
        script.metadata.append({k: str(v) for k, v in context.invocation_metadata()})
        answer = script.answers[min(script.calls, len(script.answers)) - 1]
        if isinstance(answer, entitlement_pb2.GetEntitlementResponse):
            return answer
        if script.reason is not None:
            detail = any_pb2.Any()
            detail.Pack(error_details_pb2.ErrorInfo(reason=script.reason, domain="webblock"))
            status = status_pb2.Status(code=answer.value[0], message="x", details=[detail])
            await context.abort_with_status(rpc_status.to_status(status))
        await context.abort(answer, "scripted")
        raise AssertionError("unreachable")

    generic = grpc.method_handlers_generic_handler(
        SERVICE,
        {
            "GetEntitlement": grpc.unary_unary_rpc_method_handler(
                handler,
                request_deserializer=entitlement_pb2.GetEntitlementRequest.FromString,
                response_serializer=entitlement_pb2.GetEntitlementResponse.SerializeToString,
            )
        },
    )
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((generic,))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with open_channel(f"127.0.0.1:{port}") as channel:
        script.channel = channel
        script.target = f"127.0.0.1:{port}"
        try:
            yield script
        finally:
            await server.stop(None)


def answer(hosting: Any, publish: Any) -> entitlement_pb2.GetEntitlementResponse:
    return entitlement_pb2.GetEntitlementResponse(hosting=hosting, publish=publish)
