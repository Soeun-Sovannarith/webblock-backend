"""Swagger UI and the contract in the prototype profile only (task SW; decision 0006 section 7,
"Amended by task SW"): GET/HEAD /docs and /openapi.yaml are the one exception to "every route
requires a token", exact path only; in the production profile neither route exists."""

from __future__ import annotations

import re

import httpx2
import pytest
from tests.unit.presentation.apps import BEARER_ALL, OPENAPI, build, client
from tests.unit.presentation.fakes import PROJECT, FakeOperations

from provisioning.presentation.http import served_routes
from provisioning.presentation.http.docs import (
    SWAGGER_UI_BUNDLE_SRI,
    SWAGGER_UI_CSS_SRI,
    SWAGGER_UI_VERSION,
)

DEPLOYMENT = f"/v1/projects/{PROJECT}/deployment"


def _error(response: httpx2.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def _headers_are_safe(response: httpx2.Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert not [name for name in response.headers if name.lower().startswith("access-control-")]


# --- prototype profile -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docs_page_is_served_without_a_token_and_pins_swagger_ui() -> None:
    async with client(build(FakeOperations(), docs=True), token=None) as http:
        response = await http.get("/docs")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css" in page
    assert f"swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js" in page
    assert page.count("https://cdn.jsdelivr.net/npm/swagger-ui-dist@") == 2
    assert f'integrity="{SWAGGER_UI_CSS_SRI}"' in page
    assert f'integrity="{SWAGGER_UI_BUNDLE_SRI}"' in page
    assert page.count('crossorigin="anonymous"') == 2
    assert SWAGGER_UI_CSS_SRI.startswith("sha384-")
    assert SWAGGER_UI_BUNDLE_SRI.startswith("sha384-")
    assert 'url: "/openapi.yaml"' in page
    assert "persistAuthorization: false" in page
    assert "tryItOutEnabled: true" in page
    assert page.count("<script") == 2  # the pinned bundle and the one-line initialiser
    assert BEARER_ALL not in page
    _headers_are_safe(response)


@pytest.mark.asyncio
async def test_the_contract_is_served_byte_for_byte_without_a_token() -> None:
    async with client(build(FakeOperations(), docs=True), token=None) as http:
        response = await http.get("/openapi.yaml")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/yaml; charset=utf-8"
    assert response.content == OPENAPI.read_bytes()
    _headers_are_safe(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/openapi.yaml"])
async def test_head_is_200_without_a_body_or_a_token(path: str) -> None:
    async with client(build(FakeOperations(), docs=True), token=None) as http:
        response = await http.head(path)
    assert response.status_code == 200
    assert response.content == b""
    _headers_are_safe(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/docs"),
        ("PUT", "/openapi.yaml"),
        ("GET", "/docs/"),
        ("GET", "/openapi.yaml/"),
        ("GET", "/docs?x=1"),
        ("GET", "/openapi.yaml?x=1"),
        ("GET", "/docs/oauth2-redirect"),
        ("GET", "/openapi.json"),
        ("GET", "/redoc"),
        ("GET", "/DOCS"),
        ("GET", "/docs/index.html"),
        ("GET", "/docs%2f"),
        ("GET", "//docs"),
        ("GET", "//openapi.yaml"),
        ("GET", "/docs;a=b"),
    ],
)
async def test_every_other_route_still_needs_a_token_first_then_is_404(
    method: str, path: str
) -> None:
    async with client(build(FakeOperations(), docs=True), token=None) as http:
        response = await http.request(method, path)
    _error(response, 401, "UNAUTHENTICATED")
    async with client(build(FakeOperations(), docs=True)) as http:
        response = await http.request(method, path)
    _error(response, 404, "RESOURCE_NOT_FOUND")


@pytest.mark.asyncio
async def test_v1_still_requires_a_token_in_the_prototype_profile() -> None:
    service = FakeOperations()
    async with client(build(service, docs=True), token=None) as http:
        _error(await http.get(DEPLOYMENT), 401, "UNAUTHENTICATED")
    assert service.calls == []


def test_the_prototype_route_list_adds_exactly_the_two_read_routes() -> None:
    plain = set(served_routes(build(FakeOperations()).app))
    with_docs = set(served_routes(build(FakeOperations(), docs=True).app))
    assert with_docs - plain == {("GET", "/docs"), ("GET", "/openapi.yaml")} | {
        ("HEAD", "/docs"),
        ("HEAD", "/openapi.yaml"),
    }
    assert plain <= with_docs


# --- production profile ------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/openapi.yaml"])
async def test_production_serves_neither_route(path: str) -> None:
    app = build(FakeOperations(), docs=False)
    async with client(build(FakeOperations(), docs=False), token=None) as http:
        _error(await http.get(path), 401, "UNAUTHENTICATED")
    async with client(app) as http:
        _error(await http.get(path), 404, "RESOURCE_NOT_FOUND")
    assert not [r for r in served_routes(app.app) if r[1] in {"/docs", "/openapi.yaml"}]
    assert app.app.docs_url is None
    assert app.app.openapi_url is None
    assert app.app.redoc_url is None


def test_fastapis_own_documentation_urls_are_off_in_the_prototype_profile_too() -> None:
    app = build(FakeOperations(), docs=True).app
    assert (app.docs_url, app.openapi_url, app.redoc_url) == (None, None, None)


# --- the invariant: every route needs a token, the two reads excepted ---------------------------

EXEMPT = {(m, p) for m in ("GET", "HEAD") for p in ("/docs", "/openapi.yaml")}
SOME_ID = "0190a1b2-0000-7000-8000-0000000000a1"
UNKNOWN = ["/", "/nothing", "/v1", "/healthz", "/docs/x", "/openapi.json", "/redoc"]


def _walk(app_routes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    walked = []
    for method, path in app_routes:
        filled = re.sub(r"\{[A-Za-z]+\}", SOME_ID, path)
        for m in ("GET", "POST") if method == "*" else (method,):
            walked.append((m, filled))
    for path in UNKNOWN:
        walked.append(("GET", path))
    return walked


async def _unauthenticated_pass(docs: bool) -> set[tuple[str, str]]:
    built = build(FakeOperations(), docs=docs)
    walked = _walk(served_routes(built.app))
    if docs:
        walked += sorted(EXEMPT - set(walked))
    passed: set[tuple[str, str]] = set()
    async with client(built, token=None) as http:
        for method, path in walked:
            response = await http.request(method, path, headers={"Idempotency-Key": "k1"})
            if response.status_code != 401:
                passed.add((method, path))
    return passed


@pytest.mark.asyncio
async def test_in_the_prototype_only_get_and_head_of_the_two_docs_paths_pass_without_a_token() -> (
    None
):
    assert await _unauthenticated_pass(docs=True) == EXEMPT


@pytest.mark.asyncio
async def test_in_production_no_route_passes_without_a_token() -> None:
    assert await _unauthenticated_pass(docs=False) == set()


@pytest.mark.asyncio
async def test_an_encoded_path_is_the_same_resource_not_a_bypass() -> None:
    # The server decodes /%64ocs to /docs before routing and before authentication sees the
    # path, so it is exactly the exempt resource (200 serves the same public page); no other
    # path can reach the page by encoding, as the %2f case above shows.
    async with client(build(FakeOperations(), docs=True), token=None) as http:
        response = await http.get("/%64ocs")
    assert response.status_code == 200
    assert "swagger-ui-dist@" in response.text
