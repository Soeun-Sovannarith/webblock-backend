"""/metrics on the operational listener and the public listener's request metrics (task H4)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx2
import pytest

from provisioning.presentation.http.asgi import Receive, Scope, Send
from provisioning.presentation.http.middleware import (
    OTHER_ROUTE,
    RequestMetrics,
    route_template_of,
    route_templates,
)
from provisioning.presentation.http.operational import build_operational_app

PROJECT = "0190f5a0-1111-7000-8000-00000000abcd"


async def _exposition() -> tuple[bytes, str]:
    return b"provisioning_up 1\n", "text/plain; version=0.0.4; charset=utf-8"


async def _broken() -> tuple[bytes, str]:
    raise RuntimeError("database at 10.0.0.1 refused password=hunter2")


async def _hangs() -> tuple[bytes, str]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def _ready() -> None:
    return None


async def _get(app: Any, path: str, method: str = "GET") -> httpx2.Response:
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ops"
    ) as http:
        return await http.request(method, path)


@pytest.mark.asyncio
async def test_the_operational_listener_serves_metrics_when_given_them() -> None:
    app = build_operational_app(ready=_ready, timeout=timedelta(seconds=1), metrics=_exposition)
    response = await _get(app, "/metrics")
    assert (response.status_code, response.text) == (200, "provisioning_up 1\n")
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert response.headers["cache-control"] == "no-store"
    assert (await _get(app, "/metrics", "POST")).status_code == 405
    assert (await _get(app, "/readyz")).status_code == 200


@pytest.mark.asyncio
async def test_without_metrics_there_is_no_metrics_route_and_without_readiness_no_readyz() -> None:
    api_like = build_operational_app(ready=_ready, timeout=timedelta(seconds=1))
    assert (await _get(api_like, "/metrics")).status_code == 404
    worker_like = build_operational_app(
        ready=None, timeout=timedelta(seconds=1), metrics=_exposition
    )
    assert (await _get(worker_like, "/readyz")).status_code == 404
    assert (await _get(worker_like, "/healthz")).text == "ok\n"
    assert (await _get(worker_like, "/metrics")).status_code == 200
    for path in ("/debug/pprof", "/debug/vars", "/profile"):
        assert (await _get(worker_like, path)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("exposition", [_broken, _hangs])
async def test_a_failed_exposition_is_503_without_detail(exposition: Any) -> None:
    app = build_operational_app(ready=None, timeout=timedelta(milliseconds=50), metrics=exposition)
    response = await _get(app, "/metrics")
    assert (response.status_code, response.text) == (503, "metrics unavailable\n")


def test_route_templates_never_return_the_raw_path() -> None:
    template = route_template_of(route_templates())
    assert template(f"/v1/projects/{PROJECT}/releases/{PROJECT}/deploy") == (
        "/v1/projects/{projectId}/releases/{releaseId}/deploy"
    )
    assert template(f"/v1/projects/{PROJECT}/deployment") == "/v1/projects/{projectId}/deployment"
    assert template("/mcp") == "/mcp"
    assert template(f"/v1/projects/{PROJECT}/unknown") == OTHER_ROUTE
    assert template("/metrics") == OTHER_ROUTE


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None]] = []

    def http_request(self, route: str, method: str, status: int | None, seconds: float) -> None:
        assert seconds >= 0
        self.calls.append((route, method, status))


@pytest.mark.asyncio
async def test_request_metrics_measure_every_answer_by_template() -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        status = 404 if scope["path"] == "/metrics" else 202
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    recorder = _Recorder()
    app = RequestMetrics(inner, metrics=recorder, template=route_template_of(route_templates()))
    response = await _get(app, f"/v1/projects/{PROJECT}/releases/{PROJECT}/deploy", "POST")
    assert (response.status_code, response.text) == (202, "{}")
    await _get(app, "/metrics")
    assert recorder.calls == [
        ("/v1/projects/{projectId}/releases/{releaseId}/deploy", "POST", 202),
        (OTHER_ROUTE, "GET", 404),
    ]


@pytest.mark.asyncio
async def test_a_failing_measurement_never_changes_the_response() -> None:
    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    class Broken:
        def http_request(self, route: str, method: str, status: int | None, seconds: float) -> None:
            raise RuntimeError("metrics down")

    app = RequestMetrics(inner, metrics=Broken(), template=lambda _path: "/mcp")
    response = await _get(app, "/mcp")
    assert (response.status_code, response.text) == (200, "ok")
