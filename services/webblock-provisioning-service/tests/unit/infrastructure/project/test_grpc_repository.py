"""The repository registry and locator over gRPC ``GetRepository`` against a real local server
(decision 0022 E7)."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import grpc
import grpc.aio
import pytest
from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status
from webblock_commons.core import context as commons_context
from webblock_commons.core.context import RequestContext
from webblock_commons.core.user import WebBlockUser

from provisioning.core.application import DependencyUnavailable
from provisioning.infrastructure.grpc._gen.webblock.project.v1 import repository_pb2
from provisioning.infrastructure.grpc.call import open_channel
from provisioning.infrastructure.project.grpc_repository import GrpcRepositories
from provisioning.infrastructure.telemetry import correlation_scope, new_logger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

PROJECT = UUID("0199e7a0-0000-7000-8000-0000000000bb")
type Answer = grpc.StatusCode | repository_pb2.GetRepositoryResponse


class Script:
    def __init__(self) -> None:
        self.answers: list[Answer] = []
        self.reason: str | None = None
        self.calls = 0
        self.metadata: list[dict[str, str]] = []
        self.requests: list[repository_pb2.GetRepositoryRequest] = []
        self.channel: grpc.aio.Channel


@asynccontextmanager
async def repository_server() -> AsyncIterator[Script]:
    script = Script()

    async def handler(
        request: repository_pb2.GetRepositoryRequest, context: grpc.aio.ServicerContext
    ) -> repository_pb2.GetRepositoryResponse:
        script.calls += 1
        script.requests.append(request)
        script.metadata.append({k: str(v) for k, v in context.invocation_metadata()})
        answer = script.answers[min(script.calls, len(script.answers)) - 1]
        if isinstance(answer, repository_pb2.GetRepositoryResponse):
            return answer
        if script.reason is not None:
            detail = any_pb2.Any()
            detail.Pack(error_details_pb2.ErrorInfo(reason=script.reason, domain="webblock"))
            status = status_pb2.Status(
                code=code_pb2.Code.NOT_FOUND
                if answer is grpc.StatusCode.NOT_FOUND
                else answer.value[0],
                message="x",
                details=[detail],
            )
            await context.abort_with_status(rpc_status.to_status(status))
        await context.abort(answer, "scripted")
        return repository_pb2.GetRepositoryResponse()

    generic = grpc.method_handlers_generic_handler(
        "webblock.project.v1.RepositoryService",
        {
            "GetRepository": grpc.unary_unary_rpc_method_handler(
                handler,
                request_deserializer=repository_pb2.GetRepositoryRequest.FromString,
                response_serializer=repository_pb2.GetRepositoryResponse.SerializeToString,
            )
        },
    )
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((generic,))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with open_channel(f"127.0.0.1:{port}") as channel:
        script.channel = channel
        try:
            yield script
        finally:
            await server.stop(None)


def found(
    repository_id: int = 4242, subdomain: str = "shop", url: str = "https://git.example/x.git"
) -> repository_pb2.GetRepositoryResponse:
    return repository_pb2.GetRepositoryResponse(
        repository_id=repository_id, clone_url=url, full_name="o/x", subdomain=subdomain
    )


def adapter(channel: grpc.aio.Channel) -> GrpcRepositories:
    return GrpcRepositories(channel, new_logger(sys.stderr, logging.INFO))


async def test_found_returns_the_repository_and_sub_domain() -> None:
    async with repository_server() as server:
        server.answers = [found()]
        registered = await adapter(server.channel).registered_repository(PROJECT)
        assert registered is not None
        assert (registered.repository_id, registered.subdomain) == (4242, "shop")
        assert server.requests[0].project_id == str(PROJECT)


async def test_not_found_with_its_reason_is_none() -> None:
    async with repository_server() as server:
        server.answers = [grpc.StatusCode.NOT_FOUND]
        server.reason = "REPOSITORY_NOT_FOUND"
        assert await adapter(server.channel).registered_repository(PROJECT) is None


@pytest.mark.parametrize("reason", [None, "PROJECT_NOT_FOUND"])
async def test_not_found_with_another_reason_is_an_error(reason: str | None) -> None:
    async with repository_server() as server:
        server.answers = [grpc.StatusCode.NOT_FOUND]
        server.reason = reason
        with pytest.raises(DependencyUnavailable):
            await adapter(server.channel).registered_repository(PROJECT)


@pytest.mark.parametrize(
    "answer",
    [
        found(repository_id=0),
        found(repository_id=2**53),
        found(subdomain="Bad_Name"),
        found(subdomain=""),
        found(url=""),
    ],
)
async def test_an_answer_that_breaks_the_contract_is_an_error(
    answer: repository_pb2.GetRepositoryResponse,
) -> None:
    async with repository_server() as server:
        server.answers = [answer]
        with pytest.raises(ValueError, match="project service"):
            await adapter(server.channel).registered_repository(PROJECT)


async def test_the_largest_repository_id_is_accepted() -> None:
    async with repository_server() as server:
        server.answers = [found(repository_id=2**53 - 1)]
        registered = await adapter(server.channel).registered_repository(PROJECT)
        assert registered is not None
        assert registered.repository_id == 2**53 - 1


async def test_unavailable_three_times_is_a_transient_error() -> None:
    async with repository_server() as server:
        server.answers = [grpc.StatusCode.UNAVAILABLE]
        with pytest.raises(DependencyUnavailable):
            await adapter(server.channel).registered_repository(PROJECT)
        assert server.calls == 3


async def test_a_closed_channel_is_an_ordinary_error() -> None:
    async with repository_server() as server:
        channel = grpc.aio.insecure_channel("127.0.0.1:1")
        await channel.close()
        with pytest.raises(DependencyUnavailable):
            await adapter(channel).registered_repository(PROJECT)
        del server


async def test_the_url_is_returned_when_the_repository_matches() -> None:
    async with repository_server() as server:
        server.answers = [found(url="https://git.example/shop.git")]
        url = await adapter(server.channel).repository_url(PROJECT, 4242)
        assert url == "https://git.example/shop.git"


async def test_a_different_repository_id_is_an_error() -> None:
    async with repository_server() as server:
        server.answers = [found(repository_id=9)]
        with pytest.raises(DependencyUnavailable):
            await adapter(server.channel).repository_url(PROJECT, 4242)


async def test_a_missing_repository_or_empty_url_is_an_error_for_the_locator() -> None:
    async with repository_server() as server:
        server.answers = [grpc.StatusCode.NOT_FOUND, found(url="")]
        server.reason = "REPOSITORY_NOT_FOUND"
        with pytest.raises(DependencyUnavailable):
            await adapter(server.channel).repository_url(PROJECT, 4242)
        server.reason = None
        with pytest.raises(DependencyUnavailable):
            await adapter(server.channel).repository_url(PROJECT, 4242)


async def test_the_correlation_id_is_the_request_id() -> None:
    async with repository_server() as server:
        server.answers = [found()]
        with correlation_scope("req-42"):
            await adapter(server.channel).registered_repository(PROJECT)
        assert server.metadata[0]["x-request-id"] == "req-42"


async def test_fr8_1_get_repository_sends_the_request_id_and_no_user_id() -> None:
    """FR-8.1: a call with no user carries ``x-request-id`` and no ``x-user-id``, even inside a
    user's context, and never ``authorization``."""
    user = WebBlockUser(user_id=UUID("0199e7a0-0000-7000-8000-0000000000aa"))
    async with repository_server() as server:
        server.answers = [found()]
        with (
            commons_context.bound(RequestContext(request_id="outer", user=user)),
            correlation_scope("req-81"),
        ):
            await adapter(server.channel).registered_repository(PROJECT)
        sent = server.metadata[0]
        assert sent["x-request-id"] == "req-81"
        assert "x-user-id" not in sent
        assert "authorization" not in sent


@pytest.mark.parametrize(
    ("code", "reason", "deadline"),
    [
        (grpc.StatusCode.FAILED_PRECONDITION, "PROJECT_ARCHIVED", False),
        (grpc.StatusCode.DEADLINE_EXCEEDED, None, True),
        (grpc.StatusCode.NOT_FOUND, "PROJECT_NOT_FOUND", False),
    ],
)
async def test_a_failed_call_keeps_the_upstream_reason_and_deadline(
    code: grpc.StatusCode, reason: str | None, deadline: bool
) -> None:
    """Decision 0023 G14; the error table, not the adapter, hides a NOT_FOUND reason."""
    async with repository_server() as server:
        server.answers = [code]
        server.reason = reason
        with pytest.raises(DependencyUnavailable) as info:
            await adapter(server.channel).registered_repository(PROJECT)
    assert (info.value.upstream_reason, info.value.deadline) == (reason, deadline)
