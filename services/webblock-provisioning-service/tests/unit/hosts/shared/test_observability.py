"""The hosts' observability parts (task H4): SENTRY_DSN=none builds no Sentry client, a DSN
builds the reporter, and the operational listener of the worker, build worker and bootstrap
process serves /metrics and /healthz on a real socket, and nothing else."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx2
import pytest

from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.hosts.shared.observability import start_observability
from provisioning.hosts.shared.ops_listener import OpsListener, OpsListenerFailed
from provisioning.hosts.shared.runtime import InvalidSettings
from provisioning.infrastructure.config import Secret
from provisioning.infrastructure.config.telemetry import SentryConfig, TracingConfig
from provisioning.infrastructure.telemetry.metrics import MeteredAuditLog, Metrics
from provisioning.infrastructure.telemetry.tracing import tracing_enabled

DSN = "https://0123456789abcdef@sentry.example.invalid/42"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


async def _until(probe: Callable[[], Awaitable[bool]], what: str) -> None:
    deadline = time.monotonic() + 10
    while not await probe():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting until {what}")
        await asyncio.sleep(0.02)


async def _answers(url: str) -> bool:
    try:
        async with httpx2.AsyncClient(timeout=1) as http:
            await http.get(url)
    except httpx2.TransportError:
        return False
    return True


def test_sentry_dsn_none_builds_no_client_and_no_log_handler() -> None:
    observability = start_observability(
        process="provisioning-worker", profile="prototype", sentry=None, sample_timeout=5
    )
    assert observability.reporter is None
    assert observability.log_handlers() == ()
    assert observability.log_observers() == (observability.metrics.observe_log_event,)
    assert isinstance(observability.audit(NullAuditLog()), MeteredAuditLog)
    assert observability.tracer_provider is None
    assert not tracing_enabled()
    observability.close()


def test_an_otlp_endpoint_starts_tracing_under_the_process_name_and_close_stops_it() -> None:
    observability = start_observability(
        process="provisioning-worker",
        profile="prototype",
        sentry=None,
        sample_timeout=5,
        tracing=TracingConfig(endpoint="http://127.0.0.1:9", service_name=None),
    )
    provider = observability.tracer_provider
    assert provider is not None
    assert tracing_enabled()
    assert provider.resource.attributes["service.name"] == "provisioning-worker"
    observability.close()  # nothing was traced, so nothing is sent to the closed port
    assert not tracing_enabled()


def test_otel_service_name_replaces_the_process_name() -> None:
    observability = start_observability(
        process="provisioning-worker",
        profile="prototype",
        sentry=None,
        sample_timeout=5,
        tracing=TracingConfig(endpoint="http://127.0.0.1:9", service_name="provisioning-jobs"),
    )
    try:
        assert observability.tracer_provider is not None
        name = observability.tracer_provider.resource.attributes["service.name"]
        assert name == "provisioning-jobs"
    finally:
        observability.close()


def test_a_dsn_builds_the_reporter_and_its_log_hooks() -> None:
    observability = start_observability(
        process="provisioning-api",
        profile="production",
        sentry=SentryConfig(dsn=Secret(DSN), environment="staging", traces_sample_rate=0.0),
        sample_timeout=5,
    )
    try:
        assert observability.reporter is not None
        assert len(observability.log_observers()) == 2
        [handler] = observability.log_handlers()
        assert handler.level == logging.ERROR
    finally:
        observability.close()


def test_refused_sentry_settings_are_invalid_settings_without_the_dsn() -> None:
    with pytest.raises(InvalidSettings) as caught:
        start_observability(
            process="provisioning-api",
            profile="prototype",
            sentry=SentryConfig(
                dsn=Secret("https://0123456789abcdef@x/notanumber"),
                environment="staging",
                traces_sample_rate=0.0,
            ),
            sample_timeout=5,
        )
    assert "0123456789abcdef" not in str(caught.value)


@pytest.mark.asyncio
async def test_the_ops_listener_serves_metrics_and_healthz_only() -> None:
    metrics = Metrics()
    metrics.command_executed("deploy", "finished", 1.0)
    port = _free_port()
    listener = OpsListener(f"127.0.0.1:{port}", metrics, timeout=timedelta(seconds=2))
    base = f"http://127.0.0.1:{port}"
    task = asyncio.create_task(listener.run())
    try:
        await _until(lambda: _answers(base + "/healthz"), "the listener answers")
        async with httpx2.AsyncClient(timeout=5) as http:
            assert (await http.get(base + "/healthz")).text == "ok\n"
            scraped = await http.get(base + "/metrics")
            assert scraped.status_code == 200
            assert 'provisioning_command_execution_seconds_count{kind="deploy"' in scraped.text
            assert "server" not in scraped.headers
            for path in ("/readyz", "/v1/projects", "/debug/pprof"):
                assert (await http.get(base + path)).status_code == 404
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not await _answers(base + "/healthz")


@pytest.mark.asyncio
async def test_an_ops_listener_that_cannot_bind_fails() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        listener = OpsListener(f"127.0.0.1:{port}", Metrics(), timeout=timedelta(seconds=1))
        with pytest.raises(OpsListenerFailed):
            await asyncio.wait_for(listener.run(), 10)
