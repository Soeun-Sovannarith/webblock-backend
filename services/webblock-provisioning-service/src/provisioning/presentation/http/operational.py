"""The operational listener (decision 0006 section 7,
with task H4's /metrics): GET or HEAD /healthz answers 200 while the process runs; GET or HEAD
/readyz, where the process has a readiness check (the API), answers 200 when it returns within the
timeout and 503 otherwise; GET /metrics, where the process exposes metrics, answers the Prometheus
text exposition, or 503 when it cannot be produced within the timeout. Every other body is a fixed
word that names no dependency, version or error. There is no other route: no profiling.

The public listener never serves these routes: this app is bound only to OPS_LISTEN_ADDR, which
must differ from the public address (``config.api``)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Final

from provisioning.presentation.http.asgi import ASGIApp, Receive, Scope, Send

type ReadinessCheck = Callable[[], Awaitable[None]]
"""Returns when the process is ready; raises (with any error) when it is not."""

type MetricsExposition = Callable[[], Awaitable[tuple[bytes, str]]]
"""Returns the metrics' text exposition and its content type (``infrastructure.telemetry``'s
``Metrics.render``); raises when it cannot."""

_HEADERS: Final = [
    (b"cache-control", b"no-store"),
    (b"x-content-type-options", b"nosniff"),
    (b"content-type", b"text/plain; charset=utf-8"),
]


def build_operational_app(
    *,
    ready: ReadinessCheck | None,
    timeout: timedelta,
    metrics: MetricsExposition | None = None,
) -> ASGIApp:
    """The operational app; ``timeout`` bounds each readiness check and each exposition and must
    be positive. Without ``ready`` there is no /readyz, without ``metrics`` no /metrics (404)."""
    if timeout <= timedelta(0):
        raise ValueError("http: the readiness timeout must be positive")
    seconds = timeout.total_seconds()
    paths = {"/healthz"}
    if ready is not None:
        paths.add("/readyz")
    if metrics is not None:
        paths.add("/metrics")

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await _lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        if path not in paths:
            await _text(send, 404, b"not found\n")
            return
        if method not in ("GET", "HEAD"):
            await _text(send, 405, b"method not allowed\n", [(b"allow", b"GET, HEAD")])
            return
        if path == "/healthz":
            await _text(send, 200, b"ok\n")
            return
        if path == "/metrics" and metrics is not None:
            await _metrics(send, metrics, seconds, head=method == "HEAD")
            return
        try:
            async with asyncio.timeout(seconds):
                if ready is not None:
                    await ready()
        except Exception:  # not ready, whatever the reason; the reason is not shown
            await _text(send, 503, b"not ready\n")
            return
        await _text(send, 200, b"ready\n")

    return app


async def _metrics(send: Send, metrics: MetricsExposition, seconds: float, *, head: bool) -> None:
    try:
        async with asyncio.timeout(seconds):
            body, content_type = await metrics()
    except Exception:  # whatever the reason; the reason is not shown
        await _text(send, 503, b"metrics unavailable\n")
        return
    headers = [
        (b"cache-control", b"no-store"),
        (b"x-content-type-options", b"nosniff"),
        (b"content-type", content_type.encode("ascii", "replace")),
        (b"content-length", str(len(body)).encode()),
    ]
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": b"" if head else body})


async def _text(
    send: Send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    headers = [*_HEADERS, *(extra or []), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _lifespan(receive: Receive, send: Send) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return
