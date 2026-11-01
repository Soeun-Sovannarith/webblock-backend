"""Tracing (decision 0022 E9): off unless an endpoint is set, parent based sampling, and the
``traceparent`` carried from an HTTP request to a published message and on to its consumer."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode

from provisioning.infrastructure.telemetry import new_logger
from provisioning.infrastructure.telemetry.tracing import (
    configure_tracing,
    consume_span,
    current_trace_id,
    extract_context,
    grpc_span,
    inject_headers,
    inject_metadata,
    instrument_fastapi,
    publish_span,
    shutdown_tracing,
    span,
    tracing_enabled,
)

ENDPOINT = "http://collector.test:4318"
SRC = str(Path(__file__).resolve().parents[4] / "src")
SAMPLED_PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
PARENT_TRACE = "0af7651916cd43dd8448eb211c80319c"


@pytest.fixture(autouse=True)
def _off_after() -> Iterator[None]:
    yield
    shutdown_tracing()


def _on(ratio: float = 1.0, service: str = "provisioning-test") -> InMemorySpanExporter:
    return _enable(ratio, service)[0]


def _enable(
    ratio: float = 1.0, service: str = "provisioning-test"
) -> tuple[InMemorySpanExporter, TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = configure_tracing(
        service_name=service, endpoint=ENDPOINT, ratio=ratio, exporter=exporter
    )
    assert provider is not None
    return exporter, provider


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/projects/{project_id}/publish")
    async def publish(project_id: str) -> dict[str, object]:
        headers: dict[str, object] = {}
        with publish_span("provisioning.commands", "command_job", message_id="m-1"):
            inject_headers(headers)
        return {"headers": headers, "project_id": project_id}

    return app


@pytest.mark.parametrize("endpoint", [None, "none"])
def test_disabled_tracing_has_no_provider_and_adds_no_header_or_metadata(
    endpoint: str | None,
) -> None:
    assert configure_tracing(service_name="provisioning-test", endpoint=endpoint) is None
    assert not tracing_enabled()
    headers: dict[str, object] = {}
    with span("anything"), publish_span("x", "y", message_id="m"):
        inject_headers(headers)
        assert inject_metadata() == ()
        assert current_trace_id() is None
    assert headers == {}
    with consume_span("q", {"traceparent": SAMPLED_PARENT}, message_id=None):
        assert current_trace_id() is None


def test_disabled_after_enabled_turns_every_helper_off() -> None:
    _on()
    configure_tracing(service_name="provisioning-test", endpoint="none")
    with span("after"):
        assert inject_metadata() == ()


@pytest.mark.parametrize(
    "endpoint", ["collector:4318", "ftp://c", "http://user:pw@c:4318", "http://c?x=1", "http://"]
)
def test_an_endpoint_that_is_not_a_plain_http_url_is_refused(endpoint: str) -> None:
    with pytest.raises(ValueError, match="endpoint"):
        configure_tracing(service_name="provisioning-test", endpoint=endpoint)


def test_a_ratio_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValueError, match="ratio"):
        configure_tracing(service_name="provisioning-test", endpoint=ENDPOINT, ratio=1.5)


def test_the_request_span_and_the_publish_span_share_a_trace() -> None:
    """FR-43.1: the request span and the publish span share a trace."""
    exporter, provider = _enable(service="provisioning-api")
    app = _app()
    instrument_fastapi(app, provider)
    answer = TestClient(app).post("/v1/projects/p-1/publish?page_token=secret")
    assert answer.status_code == 200
    headers = answer.json()["headers"]
    spans = {s.name: s for s in exporter.get_finished_spans()}
    server = spans["POST /v1/projects/{project_id}/publish"]
    publish = spans["publish provisioning.commands command_job"]
    assert server.kind is SpanKind.SERVER
    assert publish.kind is SpanKind.PRODUCER
    assert publish.context is not None and server.context is not None
    assert publish.context.trace_id == server.context.trace_id
    assert publish.parent is not None and publish.parent.span_id == server.context.span_id
    trace_hex = f"{server.context.trace_id:032x}"
    assert headers["traceparent"].split("-")[1] == trace_hex
    # The query string never reaches the span (a client may put anything there).
    assert server.attributes is not None
    assert "secret" not in str(server.attributes.get("http.url"))
    assert server.resource.attributes["service.name"] == "provisioning-api"


def test_an_inbound_traceparent_is_continued_by_the_request_span() -> None:
    exporter, provider = _enable(ratio=0.0)
    app = FastAPI()
    instrument_fastapi(app, provider)

    @app.get("/v1/ping")
    async def ping() -> dict[str, object]:
        return {"metadata": inject_metadata(), "trace_id": current_trace_id()}

    answer = TestClient(app).get("/v1/ping", headers={"traceparent": SAMPLED_PARENT}).json()
    assert answer["trace_id"] == PARENT_TRACE
    assert answer["metadata"][0][0] == "traceparent"
    assert answer["metadata"][0][1].startswith(f"00-{PARENT_TRACE}-")
    assert [s.name for s in exporter.get_finished_spans()] == ["GET /v1/ping"]


def test_inject_then_extract_continues_the_trace_in_the_consumer() -> None:
    exporter = _on()
    headers: dict[str, object] = {}
    with publish_span("ex", "rk", message_id="m-1"):
        inject_headers(headers)
    # AMQP headers may arrive as bytes.
    received = {"traceparent": str(headers["traceparent"]).encode("ascii")}
    with consume_span("provisioning.command_jobs", received, message_id="m-1"):
        trace_id = current_trace_id()
    spans = {s.name: s for s in exporter.get_finished_spans()}
    publish = spans["publish ex rk"]
    consume = spans["consume provisioning.command_jobs"]
    assert consume.kind is SpanKind.CONSUMER
    assert publish.context is not None and consume.context is not None
    assert consume.context.trace_id == publish.context.trace_id
    assert consume.parent is not None and consume.parent.span_id == publish.context.span_id
    assert trace_id == f"{publish.context.trace_id:032x}"
    assert consume.attributes is not None
    assert consume.attributes["messaging.message.id"] == "m-1"


@pytest.mark.parametrize(
    "headers", [None, {}, {"traceparent": "garbage"}, {"traceparent": b"\xff\xfe"}, {"x": 1}]
)
def test_a_missing_or_malformed_traceparent_starts_a_new_trace(
    headers: dict[str, object] | None,
) -> None:
    exporter = _on()
    with consume_span("q", headers, message_id=None):
        pass
    (only,) = exporter.get_finished_spans()
    assert only.parent is None


def test_a_sampled_parent_is_followed_even_at_ratio_zero() -> None:
    exporter = _on(ratio=0.0)
    with span("root"):
        pass
    assert exporter.get_finished_spans() == ()  # a new trace at ratio 0 is not sampled
    with consume_span("q", {"traceparent": SAMPLED_PARENT}, message_id=None):
        pass
    (child,) = exporter.get_finished_spans()
    assert child.context is not None
    assert f"{child.context.trace_id:032x}" == PARENT_TRACE


def test_the_grpc_span_puts_its_own_traceparent_in_the_metadata() -> None:
    exporter = _on()
    with grpc_span("/webblock.project.v1.ProjectService/CheckAccess"):
        metadata = inject_metadata()
    (call,) = exporter.get_finished_spans()
    assert call.name == "grpc /webblock.project.v1.ProjectService/CheckAccess"
    assert call.kind is SpanKind.CLIENT
    assert call.context is not None
    ((key, value),) = metadata
    assert key == "traceparent"
    assert value.startswith(f"00-{call.context.trace_id:032x}-{call.context.span_id:016x}-")


def test_an_exception_marks_the_span_with_its_class_name_only() -> None:
    exporter = _on()
    with pytest.raises(RuntimeError), span("failing"):
        raise RuntimeError("password=hunter2")
    (failed,) = exporter.get_finished_spans()
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.status.description == "RuntimeError"
    assert failed.events == ()  # no exception event with the message


def test_log_lines_carry_the_trace_id_of_the_current_span() -> None:
    """FR-43.2: log lines carry the trace id of the current span."""
    _on()
    stream = io.StringIO()
    log = new_logger(stream, logging.INFO)
    log.info("outside")
    with span("work"):
        expected = current_trace_id()
        log.info("inside")
    outside, inside = (json.loads(line) for line in stream.getvalue().splitlines())
    assert "trace_id" not in outside
    assert expected is not None and inside["trace_id"] == expected


def test_a_trace_id_field_that_is_not_hex_is_written_invalid() -> None:
    stream = io.StringIO()
    new_logger(stream, logging.INFO).info("x", trace_id="not a trace")
    assert json.loads(stream.getvalue())["trace_id"] == "[INVALID]"


def test_extract_context_reads_the_parent_only_when_tracing_is_on() -> None:
    assert (
        not trace.get_current_span(extract_context({"traceparent": SAMPLED_PARENT}))
        .get_span_context()
        .is_valid
    )
    _on()
    parent = trace.get_current_span(
        extract_context({"traceparent": SAMPLED_PARENT})
    ).get_span_context()
    assert parent.is_valid and f"{parent.trace_id:032x}" == PARENT_TRACE


CLIENT_ATTRIBUTES = {
    "http.user_agent",
    "user_agent.original",
    "net.peer.ip",
    "net.peer.port",
    "client.address",
    "client.port",
    "url.query",
}


def test_the_clients_address_and_user_agent_are_not_exported() -> None:
    exporter, provider = _enable()
    app = _app()
    instrument_fastapi(app, provider)
    TestClient(app).post(
        "/v1/projects/p-1/publish?page_token=secret", headers={"user-agent": "agent-of-u1"}
    )
    (server,) = [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]
    attributes = dict(server.attributes or {})
    assert not CLIENT_ATTRIBUTES & set(attributes)
    assert "agent-of-u1" not in str(attributes) and "secret" not in str(attributes)
    assert attributes["http.route"] == "/v1/projects/{project_id}/publish"


_SEMCONV_SCRIPT = """
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from provisioning.infrastructure.telemetry.tracing import configure_tracing, instrument_fastapi

exporter = InMemorySpanExporter()
provider = configure_tracing(
    service_name="provisioning-test", endpoint="http://c:4318", ratio=1.0, exporter=exporter
)
app = FastAPI()

@app.get("/v1/ping")
async def ping() -> dict[str, str]:
    return {}

instrument_fastapi(app, provider)
TestClient(app).get("/v1/ping?page_token=secret#frag", headers={"user-agent": "agent-of-u1"})
print(json.dumps([dict(s.attributes or {}) for s in exporter.get_finished_spans()]))
"""


def _run(script: str, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and a script
        [sys.executable, "-c", script],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": SRC, **env},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.mark.parametrize("opt_in", ["http", "http/dup"])
def test_the_new_semantic_conventions_cannot_bring_the_query_or_the_client_back(
    opt_in: str,
) -> None:
    done = _run(_SEMCONV_SCRIPT, {"OTEL_SEMCONV_STABILITY_OPT_IN": opt_in}, 60)
    assert done.returncode == 0, done.stderr
    (attributes,) = json.loads(done.stdout.splitlines()[-1])
    assert "url.path" in attributes or "http.target" in attributes  # the convention applied
    assert not CLIENT_ATTRIBUTES & set(attributes)
    assert "secret" not in str(attributes) and "agent-of-u1" not in str(attributes)


class _Blocking(SpanExporter):
    """An exporter whose export never answers (a black-holed collector)."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.release.wait(60)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.release.set()


def test_shutdown_returns_within_its_bound_when_the_collector_does_not_answer() -> None:
    _, provider = _enable()
    blocking = _Blocking()
    provider.add_span_processor(BatchSpanProcessor(blocking, schedule_delay_millis=60_000))
    for n in range(20):
        with span(f"s{n}"):
            pass
    started = time.monotonic()
    shutdown_tracing(timeout=1.0)
    elapsed = time.monotonic() - started
    blocking.release.set()
    assert elapsed < 1.5
    assert not tracing_enabled()


_EXIT_SCRIPT = """
import threading, time
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from provisioning.infrastructure.telemetry.tracing import configure_tracing, shutdown_tracing, span

class Blocking(SpanExporter):
    def export(self, spans):
        threading.Event().wait(120)
        return SpanExportResult.SUCCESS
    def shutdown(self):
        pass

provider = configure_tracing(
    service_name="provisioning-test", endpoint="http://c:4318", ratio=1.0,
    exporter=InMemorySpanExporter(),
)
provider.add_span_processor(BatchSpanProcessor(Blocking(), schedule_delay_millis=60_000))
for n in range(50):
    with span(f"s{n}"):
        pass
started = time.monotonic()
shutdown_tracing()
print(round(time.monotonic() - started, 2))
"""


def test_the_process_exits_soon_after_a_shutdown_the_collector_never_answered() -> None:
    started = time.monotonic()
    done = _run(_EXIT_SCRIPT, {}, 30)
    total = time.monotonic() - started
    assert done.returncode == 0, done.stderr
    assert float(done.stdout.split()[-1]) < 2.5  # shutdown_tracing's own bound (2 s)
    assert total < 15  # no exit hook or worker thread holds the interpreter


_HEADERS_SCRIPT = """
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from provisioning.infrastructure.telemetry.tracing import configure_tracing, instrument_fastapi

exporter = InMemorySpanExporter()
raw = InMemorySpanExporter()
provider = configure_tracing(
    service_name="provisioning-test", endpoint="http://c:4318", ratio=1.0, exporter=exporter
)
provider.add_span_processor(SimpleSpanProcessor(raw))
app = FastAPI()

@app.get("/v1/ping")
async def ping() -> dict[str, str]:
    return {}

instrument_fastapi(app, provider)
TestClient(app).get("/v1/ping", headers={"Authorization": "Bearer secret-token-value"})
print(json.dumps({
    "raw": [dict(s.attributes or {}) for s in raw.get_finished_spans()],
    "exported": [dict(s.attributes or {}) for s in exporter.get_finished_spans()],
}))
"""


def test_a_captured_header_is_never_exported() -> None:
    # configure_tracing directly: read_tracing, which refuses the key, is bypassed on purpose.
    done = _run(
        _HEADERS_SCRIPT, {"OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST": ".*"}, 60
    )
    assert done.returncode == 0, done.stderr
    spans = json.loads(done.stdout.splitlines()[-1])
    assert "secret-token-value" in json.dumps(spans["raw"])  # the instrumentation captured it
    assert spans["exported"]
    for attributes in spans["exported"]:
        assert "secret-token-value" not in json.dumps(attributes)
        assert not any(k.startswith("http.request.header.") for k in attributes)


def test_an_exception_event_is_exported_without_its_message_or_stack_trace() -> None:
    exporter, provider = _enable()
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("failing") as current:
        try:
            raise RuntimeError("password=hunter2")
        except RuntimeError as error:
            current.record_exception(error)
    (failed,) = exporter.get_finished_spans()
    (event,) = failed.events
    attributes = dict(event.attributes or {})
    assert event.name == "exception"
    assert attributes["exception.type"] == "RuntimeError"
    assert "exception.message" not in attributes
    assert "exception.stacktrace" not in attributes
    assert "hunter2" not in str(attributes)


@pytest.mark.parametrize(
    ("description", "exported"),
    [
        ("RuntimeError: password=hunter2", "RuntimeError"),
        (
            "asyncpg.exceptions.QueryCanceledError: SELECT secret",
            "asyncpg.exceptions.QueryCanceledError",
        ),
        ("Timeout", "Timeout"),
        ("password=hunter2", None),
        ("token: abc", None),
    ],
    ids=["type-and-message", "dotted-type", "type-only", "no-type", "lower-case-word"],
)
def test_span_status_description_is_the_type_only(description: str, exported: str | None) -> None:
    """Decision 0023 G8c: an instrumentation sets ``<type>: <message>``; only the type leaves the
    process. A description that does not start with a type name is dropped."""
    exporter, provider = _enable()
    with provider.get_tracer("test").start_as_current_span("failing") as current:
        current.set_status(Status(StatusCode.ERROR, description))
    (failed,) = exporter.get_finished_spans()
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.status.description == exported


def test_an_exception_status_set_by_the_sdk_keeps_the_type_only() -> None:
    exporter, provider = _enable()
    with (
        pytest.raises(RuntimeError),
        provider.get_tracer("test").start_as_current_span("failing"),
    ):
        raise RuntimeError("password=hunter2")
    (failed,) = exporter.get_finished_spans()
    assert failed.status.description == "RuntimeError"
    assert "hunter2" not in str(failed.to_json())
