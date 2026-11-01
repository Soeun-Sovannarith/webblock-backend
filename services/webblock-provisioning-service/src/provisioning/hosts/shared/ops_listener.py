"""The operational listener of the worker, the build worker and the bootstrap process (task H4):
GET /metrics and GET /healthz on OPS_LISTEN_ADDR, served by uvicorn beside the process's loops.
The API serves its own operational listener beside its public one (``hosts.api.wiring``).

The listener is internal: it has no public route, no access log, no Server header, no proxy
headers and no websockets, and its limits are fixed here (a handful of scrapes at a time).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Final

import uvicorn

from provisioning.infrastructure.telemetry.metrics import Metrics
from provisioning.presentation.http.operational import build_operational_app

MAX_CONNECTIONS: Final = 16
"""Connections the listener serves at once; uvicorn answers 503 beyond them."""

KEEP_ALIVE_SECONDS: Final = 5
SHUTDOWN_SECONDS: Final = 2


def exposition_timeout(tx_timeout: timedelta) -> timedelta:
    """The bound of one exposition: its SQL samples, each bounded by DB_TX_TIMEOUT (a process has
    at most one), and a second to render."""
    return tx_timeout + timedelta(seconds=1)


class OpsListenerFailed(Exception):
    """The operational listener could not be bound, or stopped on its own."""


class OpsListener:
    """Serves the process's metrics and liveness on ``address`` until cancelled."""

    __slots__ = ("_server",)

    def __init__(self, address: str, metrics: Metrics, *, timeout: timedelta) -> None:
        """``timeout`` bounds one exposition (its SQL samples included)."""
        host, port = host_port(address)
        app = build_operational_app(ready=None, timeout=timeout, metrics=metrics.render)
        self._server = _Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                loop="asyncio",
                http="h11",
                ws="none",
                lifespan="off",
                log_config=None,
                access_log=False,
                server_header=False,
                proxy_headers=False,
                limit_concurrency=MAX_CONNECTIONS,
                timeout_keep_alive=KEEP_ALIVE_SECONDS,
                timeout_graceful_shutdown=SHUTDOWN_SECONDS,
            )
        )

    @property
    def started(self) -> bool:
        return self._server.started

    async def run(self) -> None:
        """Serve until cancelled; then close the socket and let a scrape in progress finish
        within SHUTDOWN_SECONDS. ``OpsListenerFailed`` when it cannot bind or ends on its own."""
        serving = asyncio.ensure_future(self._serve())
        try:
            await asyncio.shield(serving)
        except asyncio.CancelledError:
            self._server.should_exit = True
            await serving
            raise
        raise OpsListenerFailed("the operational listener stopped on its own")

    async def _serve(self) -> None:
        try:
            await self._server.serve()
        except SystemExit:  # uvicorn exits this way when it cannot bind
            raise OpsListenerFailed("the operational listener could not be bound") from None


class _Server(uvicorn.Server):
    """uvicorn's server without its own signal handling: the process installs its own."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def host_port(address: str) -> tuple[str, int]:
    """A checked listen address as uvicorn's host and port; an empty host binds every
    interface."""
    host, _, port = address.rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    return host or "0.0.0.0", int(port)  # noqa: S104  # ":port" means every interface (0006 section 7)
