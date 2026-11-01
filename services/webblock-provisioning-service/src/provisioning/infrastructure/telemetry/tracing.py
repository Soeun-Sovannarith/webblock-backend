"""OpenTelemetry tracing, off unless configured (decision 0022 E9; the default-deny pattern of
``SENTRY_DSN``, decision 0006 section 7).

``configure_tracing`` builds the process's tracer provider when ``OTEL_EXPORTER_OTLP_ENDPOINT``
names a collector; with no endpoint, or ``none``, it builds nothing and every helper here is a
no-op: no span, no ``traceparent`` header and no gRPC metadata. The exporter's URL and the service
name come only from our settings; ``/v1/traces`` is added to the endpoint, as the OTLP/HTTP
convention for ``OTEL_EXPORTER_OTLP_ENDPOINT`` says. The exporter's ``requests`` session takes no
proxy or netrc from the environment.

Every exported span passes ``_scrubbed`` first: the client's address, port and user agent, the
query string (old and new semantic convention names) and every captured header
(``http.request.header.*``, ``http.response.header.*``) are dropped, and a query or fragment is
cut from a URL, whatever ``OTEL_SEMCONV_STABILITY_OPT_IN`` says (no personal data leaves the
process). Exception events keep ``exception.type`` only: their message and stack trace are
dropped. The status description keeps the exception type only (``RuntimeError: x`` becomes
``RuntimeError``); a description that does not start with a type name is dropped. The
configuration refuses the header capture keys at start as well
(``config.telemetry.CAPTURE_HEADER_KEYS``).

``shutdown_tracing`` is bounded for real: the flush and the shutdown run on a daemon thread that
is waited on for ``SHUTDOWN_TIMEOUT`` seconds, because the SDK's flush ignores its own timeout;
the provider registers no exit hook, so a collector that does not answer cannot hold the exit.

The sampler is parent based with a ratio for new traces (0.1), so the choice of an upstream that
already traced the request (Istio) wins.

Propagation is W3C ``traceparent`` (and ``tracestate`` when present): carried on HTTP by the
FastAPI instrumentation, on AMQP by ``inject_headers`` and ``extract_context`` and on gRPC by
``inject_metadata``, our own code (no other instrumentation package). Span attributes carry IDs
and names only, never a body, a token, a credential or a user ID.

The provider lives in this module, set by ``configure_tracing`` and cleared by
``shutdown_tracing``: the OpenTelemetry global provider is never set, so nothing in another
library starts spans on its own.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Final

import requests  # noqa: TID251  # only to give the OTLP exporter a session without env proxies
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from provisioning.infrastructure.config.telemetry import valid_otlp_endpoint

if TYPE_CHECKING:
    from fastapi import FastAPI

NONE: Final = "none"
DEFAULT_RATIO: Final = 0.1
TRACES_PATH: Final = "/v1/traces"
EXPORT_TIMEOUT: Final = 5.0
"""Seconds one export request may take."""
SHUTDOWN_TIMEOUT: Final = 2.0
"""Seconds the process waits on the way out for queued spans to be sent."""
TRACEPARENT: Final = "traceparent"
TRACESTATE: Final = "tracestate"
_INSTRUMENTATION: Final = "provisioning"
_PROPAGATOR: Final = TraceContextTextMapPropagator()
_MAX_HEADER_LEN: Final = 512
_DROPPED: Final = frozenset(
    {
        "http.user_agent",
        "user_agent.original",
        "http.client_ip",
        "net.peer.ip",
        "net.peer.port",
        "net.peer.name",
        "net.sock.peer.addr",
        "net.sock.peer.port",
        "client.address",
        "client.port",
        "network.peer.address",
        "network.peer.port",
        "url.query",
        "url.fragment",
    }
)
"""Attributes never exported: who the client is, and what it put in the query string."""
_URLS: Final = frozenset({"http.url", "url.full", "http.target"})
"""Attributes whose query and fragment are cut before export."""
_HEADER_PREFIXES: Final = ("http.request.header.", "http.response.header.")
"""Captured request and response headers (a header can carry a token): never exported."""
_EVENT_DROPPED: Final = frozenset({"exception.message", "exception.stacktrace"})
# A status description an instrumentation writes as ``<type>: <message>``: the exception type
# (dotted, its last part in CapWords) before the first colon is all that is exported (0023 G8c).
_STATUS_TYPE: Final = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Z][A-Za-z0-9_]*")
_MAX_STATUS_TYPE: Final = 200
"""Event attributes never exported: an exception's text and its stack trace."""


class _State:
    provider: TracerProvider | None = None


_STATE: Final = _State()


def configure_tracing(
    *,
    service_name: str,
    endpoint: str | None,
    ratio: float = DEFAULT_RATIO,
    exporter: SpanExporter | None = None,
) -> TracerProvider | None:
    """The process's tracer provider, or None (tracing off) when ``endpoint`` is None or
    ``none``. ``exporter`` replaces the OTLP/HTTP exporter (tests pass an in-memory one, which is
    flushed at once). ``ValueError`` for a ratio outside 0 to 1 or an endpoint that is not an
    http or https URL. A provider built before is shut down first."""
    if endpoint is None or endpoint == NONE:
        shutdown_tracing()
        return None
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("tracing: the sampling ratio must be from 0 to 1")
    if not valid_otlp_endpoint(endpoint):
        raise ValueError("tracing: the OTLP endpoint is not an http or https URL")
    shutdown_tracing()
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: service_name}),
        sampler=ParentBased(TraceIdRatioBased(ratio)),
        shutdown_on_exit=False,  # shutdown_tracing is the bounded way out
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(_Scrubbing(exporter)))
    else:
        session = requests.Session()
        session.trust_env = False  # no proxy or netrc from the environment
        provider.add_span_processor(
            BatchSpanProcessor(
                _Scrubbing(
                    OTLPSpanExporter(
                        endpoint=endpoint.rstrip("/") + TRACES_PATH,
                        timeout=EXPORT_TIMEOUT,
                        session=session,
                    )
                ),
                export_timeout_millis=EXPORT_TIMEOUT * 1000,
            )
        )
    _STATE.provider = provider
    return provider


def shutdown_tracing(timeout: float = SHUTDOWN_TIMEOUT) -> None:
    """Turn tracing off and send what is queued, waiting at most ``timeout`` seconds. The flush
    and the shutdown run on a daemon thread, so an unanswered export cannot hold the caller or
    the exit. Never raises."""
    provider, _STATE.provider = _STATE.provider, None
    if provider is None:
        return

    def stop() -> None:
        with contextlib.suppress(Exception):
            provider.force_flush(int(timeout * 1000))
        with contextlib.suppress(Exception):
            provider.shutdown()

    stopping = threading.Thread(target=stop, name="provisioning-tracing-shutdown", daemon=True)
    stopping.start()
    stopping.join(timeout)


class _Scrubbing(SpanExporter):
    """Exports through ``inner`` copies of the spans without the attributes of ``_DROPPED`` or a
    captured header, with the query and fragment cut from the URLs of ``_URLS``, without an
    exception event's message or stack trace, and with the status description cut to the
    exception type."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([_scrubbed(s) for s in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _scrubbed(span_: ReadableSpan) -> ReadableSpan:
    attributes = dict(span_.attributes or {})
    events = tuple(_scrubbed_event(e) for e in span_.events)
    status = _scrubbed_status(span_.status)
    changed = status is not span_.status or any(
        new is not old for new, old in zip(events, span_.events, strict=True)
    )
    if not changed and not any(_dropped(k) or k in _URLS for k in attributes):
        return span_
    kept: dict[str, object] = {}
    for key, value in attributes.items():
        if _dropped(key):
            continue
        if key in _URLS and isinstance(value, str):
            value = value.split("#", 1)[0].split("?", 1)[0]
        kept[key] = value
    return ReadableSpan(
        name=span_.name,
        context=span_.context,
        parent=span_.parent,
        resource=span_.resource,
        attributes=kept,  # type: ignore[arg-type]  # the span's own attribute values, some dropped
        events=events,
        links=span_.links,
        kind=span_.kind,
        status=status,
        start_time=span_.start_time,
        end_time=span_.end_time,
        instrumentation_scope=span_.instrumentation_scope,
    )


def _dropped(key: str) -> bool:
    return key in _DROPPED or key.startswith(_HEADER_PREFIXES)


def _scrubbed_status(status: Status) -> Status:
    """The status with its description cut to the exception type; the same object when the
    description is already a type name or absent."""
    description = status.description
    if description is None:
        return status
    kind = description.split(":", 1)[0].strip()
    if len(kind) > _MAX_STATUS_TYPE or not _STATUS_TYPE.fullmatch(kind):
        return Status(status.status_code)
    if kind == description:
        return status
    return Status(status.status_code, kind)


def _scrubbed_event(event: Event) -> Event:
    """The event without ``exception.message`` and ``exception.stacktrace`` (their text can
    carry a value); ``exception.type`` and the rest stay."""
    attributes = dict(event.attributes or {})
    if not _EVENT_DROPPED & set(attributes):
        return event
    kept = {k: v for k, v in attributes.items() if k not in _EVENT_DROPPED}
    return Event(event.name, attributes=kept, timestamp=event.timestamp)


def tracing_enabled() -> bool:
    return _STATE.provider is not None


def inject_headers(headers: dict[str, object]) -> None:
    """Add ``traceparent`` (and ``tracestate``) of the current span to AMQP ``headers``; nothing
    when tracing is off or no span is active."""
    if _STATE.provider is None:
        return
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    headers.update(carrier)


def extract_context(headers: Mapping[str, object] | None) -> Context:
    """The context a message's ``traceparent`` names; an empty context when tracing is off or the
    header is absent or malformed (the span then starts a new trace)."""
    if _STATE.provider is None or not headers:
        return Context()
    carrier: dict[str, str] = {}
    for key in (TRACEPARENT, TRACESTATE):
        value = headers.get(key)
        if isinstance(value, bytes):
            try:
                value = value.decode("ascii")
            except UnicodeDecodeError:
                continue
        if isinstance(value, str) and len(value) <= _MAX_HEADER_LEN:
            carrier[key] = value
    return _PROPAGATOR.extract(carrier)


def inject_metadata() -> tuple[tuple[str, str], ...]:
    """gRPC metadata with the ``traceparent`` (and ``tracestate``) of the current span; empty
    when tracing is off or no span is active."""
    if _STATE.provider is None:
        return ()
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return tuple(carrier.items())


def current_trace_id() -> str | None:
    """The current span's trace ID, 32 lowercase hex digits, for log lines; None without a valid
    span."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return trace.format_trace_id(context.trace_id)


def sampled_trace_id() -> str | None:
    """The current span's trace ID, 32 lowercase hex digits, only when the span is sampled: the
    ``traceId`` of a response (decision 0023 G2, G5). None without a valid span, and for a span
    that is not exported, so a client never gets an ID that Tempo cannot find."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return None
    return trace.format_trace_id(context.trace_id)


type Attributes = Mapping[str, str | int | bool]


def span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Attributes | None = None,
    parent: Context | None = None,
) -> contextlib.AbstractContextManager[None]:
    """A current span named ``name`` for the block; a no-op when tracing is off. An exception
    leaving the block marks the span as an error with the exception's class name only (its text
    can carry a value)."""
    provider = _STATE.provider
    if provider is None:
        return contextlib.nullcontext()
    return _Span(provider, name, kind, attributes, parent)


class _Span:
    """A class, not a generator: a generator-based context manager writes ``__traceback__`` on
    the exception passing through, which a frozen dataclass exception refuses."""

    __slots__ = ("_attributes", "_kind", "_name", "_parent", "_provider", "_span", "_token")

    def __init__(
        self,
        provider: TracerProvider,
        name: str,
        kind: SpanKind,
        attributes: Attributes | None,
        parent: Context | None,
    ) -> None:
        self._provider = provider
        self._name = name
        self._kind = kind
        self._attributes = attributes
        self._parent = parent
        self._span: trace.Span | None = None
        self._token: object = None

    def __enter__(self) -> None:
        tracer = self._provider.get_tracer(_INSTRUMENTATION)
        current = tracer.start_span(
            self._name,
            context=self._parent,
            kind=self._kind,
            attributes=dict(self._attributes) if self._attributes else None,
            record_exception=False,
            set_status_on_exception=False,
        )
        self._span = current
        self._token = otel_context.attach(trace.set_span_in_context(current))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        current, self._span = self._span, None
        if current is None:
            return
        if exc_type is not None:
            current.set_status(Status(StatusCode.ERROR, exc_type.__name__))
        otel_context.detach(self._token)  # type: ignore[arg-type]  # the Token attach gave __enter__
        current.end()


def publish_span(
    exchange: str, routing_key: str, *, message_id: str
) -> contextlib.AbstractContextManager[None]:
    """The producer span ``publish <exchange> <routing_key>``."""
    return span(
        f"publish {exchange} {routing_key}",
        kind=SpanKind.PRODUCER,
        attributes={
            "messaging.system": "rabbitmq",
            "messaging.destination.name": exchange,
            "messaging.rabbitmq.destination.routing_key": routing_key,
            "messaging.message.id": message_id,
        },
    )


def consume_span(
    queue: str, headers: Mapping[str, object] | None, *, message_id: str | None
) -> contextlib.AbstractContextManager[None]:
    """The consumer span ``consume <queue>``, continuing the trace of the message's
    ``traceparent``."""
    attributes: dict[str, str | int | bool] = {
        "messaging.system": "rabbitmq",
        "messaging.destination.name": queue,
    }
    if message_id is not None and len(message_id) <= 128:
        attributes["messaging.message.id"] = message_id
    return span(
        f"consume {queue}",
        kind=SpanKind.CONSUMER,
        attributes=attributes,
        parent=extract_context(headers),
    )


def grpc_span(method: str) -> contextlib.AbstractContextManager[None]:
    """The client span ``grpc <method>`` (the full method path)."""
    return span(
        f"grpc {method}",
        kind=SpanKind.CLIENT,
        attributes={"rpc.system": "grpc", "rpc.method": method},
    )


def instrument_fastapi(app: FastAPI, provider: TracerProvider) -> None:
    """Trace every request of ``app`` with ``provider``: the server span ``<method> <route>``
    continues an inbound ``traceparent``; the ASGI send and receive spans are left out. The
    instrumentation captures headers only when an ``OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*``
    key says so; the configuration refuses those keys at start, and whatever a process configured
    outside it captures is dropped at export, as are the client's attributes, the query string and
    an exception event's message and stack trace. What is guaranteed is the exported span, not
    the in-process one."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, exclude_spans=["receive", "send"]
    )
