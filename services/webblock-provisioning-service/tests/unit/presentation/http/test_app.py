"""The public HTTP application (decision 0006, decision 0013 section 9 A3; review-port-arch.md H3):
authentication, the routes of the contract and nothing else, FastAPI's defaults switched off, body
limits (chunked included), the in-flight cap, the request timeout, idempotency through the real
application service, and the build endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from uuid import UUID

import httpx2
import pytest
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from tests.unit.presentation.apps import BEARER_ALL, BEARER_READ, build, client
from tests.unit.presentation.fakes import COMMAND, PROJECT, RELEASE, FakeOperations
from tests.unit.presentation.memory import FakeBuilds, MemoryStores, memory_operations

from provisioning.core.application import (
    Action,
    BuildInProgress,
    NotFound,
    Principal,
    ReleaseNotBuildable,
    Unauthenticated,
)
from provisioning.core.domain import ReleaseStatus
from provisioning.presentation.operations import message

OTHER_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000b2")
DEPLOY = f"/v1/projects/{PROJECT}/releases/{RELEASE}/deploy"
BUILD = f"/v1/projects/{PROJECT}/releases/{RELEASE}/build"
DEPLOYMENT = f"/v1/projects/{PROJECT}/deployment"


def _json(response: httpx2.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    return body


def _data(response: httpx2.Response) -> dict[str, Any]:
    """The ``data`` of a success envelope (decision 0023 G2)."""
    body = _json(response)
    assert body["success"] is True, body
    data: dict[str, Any] = body["data"]
    return data


PROBLEM_MEMBERS = {
    "type",
    "title",
    "status",
    "instance",
    "code",
    "requestId",
    "traceId",
    "errors",
    "commandId",
}


def _error(
    response: httpx2.Response,
    status: int,
    code: str,
    media_type: str = "application/problem+json",
) -> None:
    """An RFC 9457 Problem (decision 0018) whose instance is the request path. The content type
    is ``application/problem+json`` unless the request's ``Accept`` allows only
    ``application/json`` (spec 6.3 of the Project service)."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == media_type
    body = _json(response)
    assert body["code"] == code, body
    assert set(body) <= PROBLEM_MEMBERS, body
    assert body["type"] == "urn:webblock:problem:" + code.lower().replace("_", "-")
    assert body["title"] == message(code)
    assert body["status"] == status
    assert body["instance"] == response.request.url.raw_path.split(b"?")[0].decode("ascii")


# --- authentication --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_token_is_401_with_the_bearer_challenge_and_asks_nothing() -> None:
    service = FakeOperations()
    async with client(build(service), token=None) as http:
        response = await http.get(DEPLOYMENT)
    _error(response, 401, "UNAUTHENTICATED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        f"Basic {BEARER_ALL}",
        "Bearer",
        "Bearer ",
        f"Bearer  {BEARER_ALL}",
        f"Bearer {BEARER_ALL} extra",
        f"Bearer {BEARER_ALL}é",
        "Bearer a@b",
    ],
)
async def test_a_malformed_authorization_header_is_401(header: str) -> None:
    service = FakeOperations()
    async with client(build(service), token=None) as http:
        response = await http.get(DEPLOYMENT, headers={b"Authorization": header.encode()})
    _error(response, 401, "UNAUTHENTICATED")
    assert service.calls == []


@pytest.mark.asyncio
async def test_two_authorization_headers_are_401() -> None:
    service = FakeOperations()
    async with client(build(service), token=None) as http:
        response = await http.get(
            DEPLOYMENT,
            headers=[
                ("Authorization", f"Bearer {BEARER_ALL}"),
                ("Authorization", f"Bearer {BEARER_ALL}"),
            ],
        )
    _error(response, 401, "UNAUTHENTICATED")


@pytest.mark.asyncio
async def test_a_bad_token_is_401_and_a_good_token_reaches_the_service() -> None:
    service = FakeOperations()
    async with client(build(service), token=None) as http:
        bad = "Bearer dev-token-unknown-0123456789abcdefghijklmnop"
        _error(await http.get(DEPLOYMENT, headers={"Authorization": bad}), 401, "UNAUTHENTICATED")
        assert service.calls == []
        response = await http.get(DEPLOYMENT, headers={"Authorization": f"bearer {BEARER_ALL}"})
    assert response.status_code == 200
    ((action, principal, _),) = service.calls
    assert action is Action.GET_DEPLOYMENT
    assert principal.subject == "alice"


class _BrokenVerifier:
    async def verify(self, raw: str) -> Principal:
        raise RuntimeError("keys unavailable")


class _RefusingVerifier:
    async def verify(self, raw: str) -> Principal:
        raise Unauthenticated("expired")


@pytest.mark.asyncio
async def test_a_verifier_that_cannot_verify_is_503_identity_unavailable() -> None:
    async with client(build(FakeOperations(), verifier=_BrokenVerifier())) as http:
        _error(await http.get(DEPLOYMENT), 503, "IDENTITY_UNAVAILABLE")
    async with client(build(FakeOperations(), verifier=_RefusingVerifier())) as http:
        _error(await http.get(DEPLOYMENT), 401, "UNAUTHENTICATED")


@pytest.mark.asyncio
async def test_a_token_without_the_action_is_403_from_the_application() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    async with client(build(memory_operations(stores)), token=BEARER_READ) as http:
        response = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
    _error(response, 403, "ACCESS_DENIED")
    assert stores.commands == []


# --- idempotency and serialization through the real service ------------------------------------


@pytest.mark.asyncio
async def test_an_idempotent_replay_returns_the_original_command() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    async with client(build(memory_operations(stores))) as http:
        first = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
        second = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
    assert first.status_code == second.status_code == 202
    assert _data(first) == _data(second)
    assert _json(first)["meta"]["status"] == 202
    assert (
        _data(first)["statusUrl"] == f"/v1/projects/{PROJECT}/commands/{_data(first)['commandId']}"
    )
    assert len(stores.commands) == 1


@pytest.mark.asyncio
async def test_the_same_key_with_another_request_is_a_conflict() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    stores.add_release(OTHER_RELEASE, ReleaseStatus.BUILT)
    async with client(build(memory_operations(stores))) as http:
        assert (await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})).status_code == 202
        other = f"/v1/projects/{PROJECT}/releases/{OTHER_RELEASE}/deploy"
        _error(
            await http.post(other, headers={"Idempotency-Key": "k1"}), 422, "IDEMPOTENCY_KEY_REUSED"
        )
        promote = f"/v1/projects/{PROJECT}/releases/{RELEASE}/promote"
        _error(
            await http.post(promote, headers={"Idempotency-Key": "k1"}),
            422,
            "IDEMPOTENCY_KEY_REUSED",
        )
    assert len(stores.commands) == 1


@pytest.mark.asyncio
async def test_a_second_command_while_one_is_in_flight_is_busy() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILT)
    stores.add_release(OTHER_RELEASE, ReleaseStatus.BUILT)
    async with client(build(memory_operations(stores))) as http:
        first = await http.post(DEPLOY, headers={"Idempotency-Key": "k1"})
        other = f"/v1/projects/{PROJECT}/releases/{OTHER_RELEASE}/deploy"
        busy = await http.post(other, headers={"Idempotency-Key": "k2"})
    _error(busy, 409, "COMMAND_IN_PROGRESS")
    assert _json(busy)["commandId"] == _data(first)["commandId"]
    assert len(stores.commands) == 1


# --- the Idempotency-Key header --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Idempotency-Key", "k1"), ("Idempotency-Key", "k1")],
        [("Idempotency-Key", "x" * 256)],
    ],
    ids=["missing", "repeated", "too long"],
)
async def test_a_missing_repeated_or_invalid_key_is_400(headers: list[tuple[str, str]]) -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        _error(await http.post(DEPLOY, headers=headers), 400, "VALIDATION_FAILED")
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_key_in_utf8_with_surrounding_spaces_is_accepted() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.post(DEPLOY, headers=[(b"Idempotency-Key", " schlüssel-1 ".encode())])
    assert response.status_code == 202
    ((_, _, request),) = service.calls
    assert getattr(request, "idempotency_key") == "schlüssel-1"  # noqa: B009  # the request type is the fake's object


# --- bodies --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"", b"{}", b" { } "])
async def test_a_mutation_accepts_an_absent_or_empty_body(body: bytes) -> None:
    async with client(build(FakeOperations())) as http:
        headers = {"Idempotency-Key": "k1"}
        if body:
            headers["Content-Type"] = "application/json"
        response = await http.post(DEPLOY, headers=headers, content=body)
    assert response.status_code == 202


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b'{"expected_generation":1}', "application/json"),
        (b"{}", "text/plain"),
        (b"{}", None),
        (b"[]", "application/json"),
    ],
)
async def test_a_mutation_refuses_any_other_body(body: bytes, content_type: str | None) -> None:
    service = FakeOperations()
    headers = {"Idempotency-Key": "k1"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    async with client(build(service)) as http:
        _error(await http.post(DEPLOY, headers=headers, content=body), 400, "VALIDATION_FAILED")
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_read_with_a_body_is_400() -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.request("GET", DEPLOYMENT, content=b"{}")
    _error(response, 400, "VALIDATION_FAILED")


@pytest.mark.asyncio
async def test_an_over_limit_body_is_413_with_the_contract_body() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.post(
            DEPLOY,
            headers={"Idempotency-Key": "k1", "Content-Type": "application/json"},
            content=b"{" + b" " * 100 + b"}",
        )
    _error(response, 413, "REQUEST_TOO_LARGE")
    assert service.calls == []


def _scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


async def _call_chunked(
    app: Callable[[Any, Any, Any], Awaitable[None]],
    method: str,
    path: str,
    headers: list[tuple[bytes, bytes]],
    chunks: list[bytes],
) -> tuple[int, dict[str, str], bytes]:
    """Call the ASGI app with a body sent in chunks and no Content-Length (chunked transfer)."""
    messages: list[MutableMapping[str, Any]] = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        if messages:
            return messages.pop(0)
        await asyncio.Event().wait()  # a client that sends nothing more
        raise AssertionError("unreachable")

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(_scope(method, path, headers), receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, body


@pytest.mark.asyncio
async def test_an_over_limit_chunked_body_is_413_with_the_contract_body() -> None:
    service = FakeOperations()
    built = build(service)
    async with built.app.router.lifespan_context(built.app):
        status, headers, body = await _call_chunked(
            built.app,
            "POST",
            DEPLOY,
            [
                (b"authorization", f"Bearer {BEARER_ALL}".encode()),
                (b"idempotency-key", b"k1"),
                (b"content-type", b"application/json"),
                (b"transfer-encoding", b"chunked"),
            ],
            [b"{", b" " * 40, b" " * 40, b"}"],
        )
    assert status == 413
    assert headers["content-type"] == "application/problem+json"
    problem = json.loads(body)
    assert (problem["code"], problem["status"], problem["instance"]) == (
        "REQUEST_TOO_LARGE",
        413,
        DEPLOY,
    )
    assert service.calls == []


# --- routes: the contract and nothing else ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect", "/"])
async def test_framework_routes_are_not_served(path: str) -> None:
    async with client(build(FakeOperations())) as http:
        _error(await http.get(path), 404, "RESOURCE_NOT_FOUND")
    async with client(build(FakeOperations()), token=None) as http:
        _error(await http.get(path), 401, "UNAUTHENTICATED")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", DEPLOYMENT + "/"),
        ("POST", DEPLOY + "/"),
        ("GET", f"/v1/projects/{PROJECT}//deployment"),
        ("GET", "/mcp/"),
    ],
)
async def test_a_trailing_slash_is_404_not_a_redirect(method: str, path: str) -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.request(method, path, headers={"Idempotency-Key": "k1"})
    _error(response, 404, "RESOURCE_NOT_FOUND")
    assert "location" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", DEPLOYMENT),
        ("PUT", DEPLOY),
        ("GET", DEPLOY),
        ("DELETE", f"/v1/projects/{PROJECT}/releases/{RELEASE}"),
        ("HEAD", DEPLOYMENT),
        ("OPTIONS", DEPLOY),
    ],
)
async def test_a_wrong_method_on_a_v1_route_is_404(method: str, path: str) -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.request(method, path)
    assert response.status_code == 404
    if method != "HEAD":
        _error(response, 404, "RESOURCE_NOT_FOUND")
    assert service.calls == []


@pytest.mark.asyncio
async def test_security_headers_are_on_every_response() -> None:
    async with client(build(FakeOperations())) as http:
        responses = [await http.get(DEPLOYMENT), await http.get("/nothing")]
    async with client(build(FakeOperations()), token=None) as http:
        responses.append(await http.get(DEPLOYMENT))
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in response.headers
        assert "server" not in response.headers


# --- the in-flight cap and the request timeout --------------------------------------------------


@pytest.mark.asyncio
async def test_the_in_flight_cap_answers_overloaded_before_authentication() -> None:
    service = FakeOperations(gate=asyncio.Event())
    built = build(service, max_in_flight=1)
    async with client(built) as http:
        first = asyncio.create_task(http.get(DEPLOYMENT))
        await asyncio.wait_for(service.entered.wait(), timeout=5)
        second = await http.get(DEPLOYMENT, headers={"Authorization": "Bearer nonsense-token"})
        assert service.gate is not None
        service.gate.set()
        done = await first
    _error(second, 503, "SERVICE_OVERLOADED")
    assert done.status_code == 200
    assert len(service.calls) == 1


class _SlowVerifier:
    async def verify(self, raw: str) -> Principal:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_the_request_timeout_middleware_bounds_the_whole_request() -> None:
    built = build(FakeOperations(), verifier=_SlowVerifier(), middleware_timeout=0.05)
    async with client(built) as http:
        _error(await http.get(DEPLOYMENT), 504, "REQUEST_TIMEOUT")


@pytest.mark.asyncio
async def test_the_application_call_is_bounded_by_the_request_timeout() -> None:
    built = build(FakeOperations(delay=5), request_timeout=0.05, middleware_timeout=5)
    async with client(built) as http:
        _error(await http.get(DEPLOYMENT), 504, "REQUEST_TIMEOUT")


# --- the build endpoint ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_release_starts_the_build_and_returns_the_release() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.ACCEPTED)
    builds = FakeBuilds(stores)
    async with client(build(memory_operations(stores, builds))) as http:
        response = await http.post(BUILD)
    assert response.status_code == 202
    body = _data(response)
    assert body["releaseId"] == str(RELEASE)
    assert body["status"] == "building"
    assert builds.starts == [(PROJECT, RELEASE)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NotFound("release"), 404, "RELEASE_NOT_FOUND"),
        (ReleaseNotBuildable("failed"), 422, "RELEASE_NOT_BUILDABLE"),
        (BuildInProgress("running"), 409, "BUILD_IN_PROGRESS"),
    ],
)
async def test_build_release_refusals(error: Exception, status: int, code: str) -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.BUILDING)
    builds = FakeBuilds(stores, error=error)
    async with client(build(memory_operations(stores, builds))) as http:
        _error(await http.post(BUILD), status, code)


@pytest.mark.asyncio
async def test_build_release_needs_a_token_and_the_action() -> None:
    stores = MemoryStores()
    stores.add_release(RELEASE, ReleaseStatus.ACCEPTED)
    builds = FakeBuilds(stores)
    service = memory_operations(stores, builds)
    async with client(build(service), token=None) as http:
        _error(await http.post(BUILD), 401, "UNAUTHENTICATED")
    async with client(build(service), token=BEARER_READ) as http:
        _error(await http.post(BUILD), 403, "ACCESS_DENIED")
    assert builds.starts == []


@pytest.mark.asyncio
async def test_build_release_takes_no_idempotency_key_and_no_body() -> None:
    service = FakeOperations()
    async with client(build(service)) as http:
        response = await http.post(
            BUILD,
            headers={"Content-Type": "application/json"},
            content=b'{"idempotencyKey":"k"}',
        )
    _error(response, 400, "VALIDATION_FAILED")
    assert service.calls == []


@pytest.mark.asyncio
async def test_get_command_reads_any_command_of_the_project() -> None:
    async with client(build(FakeOperations())) as http:
        response = await http.get(f"/v1/projects/{PROJECT}/commands/{COMMAND}")
    assert response.status_code == 200
    assert _data(response)["commandId"] == str(COMMAND)


# --- the Problem body on every path (decision 0018) ----------------------------------------------


@pytest.mark.asyncio
async def test_the_instance_is_the_percent_encoded_path_without_the_query() -> None:
    path = "/v1/x%22%3Cy%3E/%C3%A9"
    async with client(build(FakeOperations())) as http:
        found = await http.get(path + "?limit=1")
    async with client(build(FakeOperations()), token=None) as http:
        anonymous = await http.get(path + "?limit=1")
    for response, status, code in (
        (found, 404, "RESOURCE_NOT_FOUND"),
        (anonymous, 401, "UNAUTHENTICATED"),
    ):
        _error(response, status, code)
        assert _json(response)["instance"] == path
        assert response.content.isascii()
        for raw in (b"limit", b'"<', b"<y>"):
            assert raw not in response.content


ACCEPT_CASES = [
    (None, "application/problem+json"),
    ("*/*", "application/problem+json"),
    ("application/*", "application/problem+json"),
    ("application/problem+json", "application/problem+json"),
    ("application/json, application/problem+json", "application/problem+json"),
    ("application/json", "application/json"),
    ("application/json;q=0", "application/problem+json"),
    ("application/problem+json;q=0, application/json", "application/json"),
    ("text/html", "application/problem+json"),
    ("text/html, application/json", "application/json"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("accept", "media_type"), ACCEPT_CASES)
async def test_an_error_content_type_follows_accept(accept: str | None, media_type: str) -> None:
    """Spec 6.3 of the Project service (Review Focus 3): the content type follows Accept, the
    status, the bytes of the body and Cache-Control do not, and an error is never a 406."""
    service = FakeOperations(errors={Action.GET_DEPLOYMENT: NotFound("x")})
    async with client(build(service)) as http:
        baseline = await http.get(DEPLOYMENT)
        headers = {} if accept is None else {"Accept": accept}
        response = await http.get(DEPLOYMENT, headers=headers)
    _error(response, 404, "DEPLOYMENT_NOT_FOUND", media_type)
    assert response.headers["cache-control"] == "no-store"
    assert response.content == baseline.content


@pytest.mark.asyncio
@pytest.mark.parametrize(("accept", "media_type"), ACCEPT_CASES[:2] + ACCEPT_CASES[5:6])
async def test_a_401_follows_accept(accept: str | None, media_type: str) -> None:
    """The middleware paths follow the same rule, and the challenge stays."""
    headers = {} if accept is None else {"Accept": accept}
    async with client(build(FakeOperations()), token=None) as http:
        response = await http.get(DEPLOYMENT, headers=headers)
    _error(response, 401, "UNAUTHENTICATED", media_type)
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_a_success_is_the_envelope_as_json() -> None:
    """Decision 0023 G2: a success is application/json whatever Accept says, with success, data
    and meta; meta.path is the path without the query."""
    async with client(build(FakeOperations())) as http:
        response = await http.get(DEPLOYMENT, headers={"Accept": "application/problem+json"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    body = _json(response)
    assert list(body) == ["success", "data", "meta"]
    assert not {"type", "title", "instance"} & set(body)
    assert body["meta"]["path"] == DEPLOYMENT
    assert body["meta"]["status"] == 200
    assert body["meta"]["page"] is None


@pytest.mark.asyncio
async def test_the_validation_handler_answers_a_problem() -> None:
    built = build(FakeOperations())
    handler = built.app.exception_handlers[RequestValidationError]
    scope = {"type": "http", "method": "GET", "path": DEPLOYMENT, "headers": []}
    response = await handler(Request(scope), RequestValidationError([]))
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    assert json.loads(response.body) == {
        "type": "urn:webblock:problem:validation-failed",
        "title": "The request is invalid.",
        "status": 400,
        "instance": DEPLOYMENT,
        "code": "VALIDATION_FAILED",
        "requestId": "corr-fixed-1",
        "errors": [],
    }
