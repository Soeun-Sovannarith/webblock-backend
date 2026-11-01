"""The operational listener (decision 0006 section 7): /healthz and /readyz only, fixed words naming
no dependency, version or error."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx2
import pytest

from provisioning.presentation.http import build_operational_app


async def _ready() -> None:
    return None


async def _not_ready() -> None:
    raise RuntimeError("database at 10.0.0.1 refused password=hunter2")


async def _hangs() -> None:
    await asyncio.Event().wait()


async def _get(ready: object, method: str, path: str) -> httpx2.Response:
    app = build_operational_app(ready=ready, timeout=timedelta(milliseconds=50))  # type: ignore[arg-type]  # each case passes a readiness coroutine function
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ops"
    ) as http:
        return await http.request(method, path)


@pytest.mark.asyncio
async def test_healthz_answers_while_the_process_runs() -> None:
    response = await _get(_not_ready, "GET", "/healthz")
    assert (response.status_code, response.text) == (200, "ok\n")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ready", "status", "text"),
    [(_ready, 200, "ready\n"), (_not_ready, 503, "not ready\n"), (_hangs, 503, "not ready\n")],
)
async def test_readyz_reports_readiness_without_detail(
    ready: object, status: int, text: str
) -> None:
    response = await _get(ready, "GET", "/readyz")
    assert (response.status_code, response.text) == (status, text)
    assert "hunter2" not in response.text


@pytest.mark.asyncio
async def test_head_is_allowed_and_other_methods_are_405() -> None:
    assert (await _get(_ready, "HEAD", "/readyz")).status_code == 200
    response = await _get(_ready, "POST", "/healthz")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/metrics", "/debug/pprof", "/healthz/", "/v1/projects"])
async def test_nothing_else_is_served(path: str) -> None:
    response = await _get(_ready, "GET", path)
    assert (response.status_code, response.text) == (404, "not found\n")


def test_a_non_positive_timeout_is_refused() -> None:
    with pytest.raises(ValueError, match="timeout"):
        build_operational_app(ready=_ready, timeout=timedelta(0))
