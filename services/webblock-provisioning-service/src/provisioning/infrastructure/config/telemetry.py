"""The observability keys of every long-lived process (task H4; decision 0016 section 5 item 4).

- ``SENTRY_DSN`` is required and has no default (OD-15). The value ``none`` turns error reports
  off, as ``MCP_ALLOWED_ORIGINS=none`` means an empty list; the prototype stack runs with it. Any
  other value is a DSN, a secret that prints redacted: an http or https URL with a public key
  before the host and the numeric project ID as its last path segment, without a query or a
  fragment.
- With a DSN, ``SENTRY_ENVIRONMENT`` (a short token) and ``SENTRY_TRACES_SAMPLE_RATE`` (a decimal
  from 0 to 1; 0 is the prototype's value) are required. With ``none`` both must be unset, so a
  setting never seems to be in force when it is not.
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` (decision 0022 E9) is optional: unset or ``none`` keeps
  tracing off. Any other value is the collector's OTLP/HTTP base URL (http or https, a host, no
  user info, query or fragment); ``/v1/traces`` is added to it. ``OTEL_SERVICE_NAME`` is optional
  (``provisioning-<name>``; the process's own name by default) and allowed with ``none``, so a
  chart can set it once. The three header capture keys of the HTTP instrumentation
  (``CAPTURE_HEADER_KEYS``) must be unset or empty: a header can carry a token, so a process
  with any of them set refuses to start. No other ``OTEL_*`` key is read here.
- ``OPS_LISTEN_ADDR`` of the worker, the build worker and the bootstrap process (their own
  operational listener: GET /metrics and GET /healthz). The API reads its own, beside its public
  listener (``config.api``).

No reason ever contains a value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from provisioning.infrastructure.config.loader import Loader, has_control
from provisioning.infrastructure.config.net import listen_addr, split_url
from provisioning.infrastructure.config.secret import Secret

DSN_KEY: Final = "SENTRY_DSN"
ENVIRONMENT_KEY: Final = "SENTRY_ENVIRONMENT"
TRACES_KEY: Final = "SENTRY_TRACES_SAMPLE_RATE"
OPS_KEY: Final = "OPS_LISTEN_ADDR"
OTLP_ENDPOINT_KEY: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_NAME_KEY: Final = "OTEL_SERVICE_NAME"
NONE: Final = "none"
CAPTURE_HEADER_KEYS: Final = (
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE",
    "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS",
)
"""Keys that would make the FastAPI instrumentation copy headers into spans."""

_ENVIRONMENT: Final = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}", re.ASCII)
_RATE: Final = re.compile(r"0|1|0\.[0-9]{1,6}|1\.0{1,6}", re.ASCII)
_KEY: Final = re.compile(r"[A-Za-z0-9]{1,128}", re.ASCII)
_PROJECT: Final = re.compile(r"[0-9]{1,20}", re.ASCII)
_SERVICE_NAME: Final = re.compile(r"provisioning-[a-z-]{1,40}", re.ASCII)


@dataclass(frozen=True, slots=True)
class SentryConfig:
    """Error reports are on: the DSN (redacted when printed), the environment and the traces
    sample rate."""

    dsn: Secret
    environment: str
    traces_sample_rate: float


def read_sentry(loader: Loader) -> SentryConfig | None:
    """SENTRY_DSN and, with a DSN, SENTRY_ENVIRONMENT and SENTRY_TRACES_SAMPLE_RATE; None when
    SENTRY_DSN is none (or failed: the load fails anyway)."""
    value = loader.raw(DSN_KEY)
    if value is None:
        return None
    if value == NONE:
        for key in (ENVIRONMENT_KEY, TRACES_KEY):
            if loader.optional(key) is not None:
                loader.fail(key, f"must be unset when {DSN_KEY} is {NONE}")
        return None
    dsn = Secret(value)
    if not valid_dsn(value):
        loader.fail(
            DSN_KEY,
            "must be none or a Sentry DSN: http(s)://<public key>@<host>[:port][/path]/<project "
            "id>, without a query or a fragment",
        )
    environment = loader.text(ENVIRONMENT_KEY, _environment)
    rate = loader.text(TRACES_KEY, _rate)
    return SentryConfig(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=float(rate) if rate else 0.0,
    )


@dataclass(frozen=True, slots=True)
class TracingConfig:
    """Tracing is on: the collector's OTLP/HTTP base URL and, when set, the service name."""

    endpoint: str
    service_name: str | None


def read_tracing(loader: Loader) -> TracingConfig | None:
    """OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_SERVICE_NAME; None when the endpoint is unset or
    none (or failed: the load fails anyway). A header capture key of ``CAPTURE_HEADER_KEYS``
    with a value fails the load, whether tracing is on or off."""
    for key in CAPTURE_HEADER_KEYS:
        if loader.optional(key) is not None:
            loader.fail(key, "must be unset: span attributes never carry a header")
    service_name = loader.optional(SERVICE_NAME_KEY)
    if service_name is not None and _SERVICE_NAME.fullmatch(service_name) is None:
        loader.fail(SERVICE_NAME_KEY, "must be provisioning-<name>: lowercase letters and '-'")
        service_name = None
    endpoint = loader.optional(OTLP_ENDPOINT_KEY)
    if endpoint is None or endpoint == NONE:
        return None
    if not valid_otlp_endpoint(endpoint):
        loader.fail(
            OTLP_ENDPOINT_KEY,
            "must be none or an http(s) URL with a host, without user info, query or fragment",
        )
        return None
    return TracingConfig(endpoint=endpoint, service_name=service_name)


def valid_otlp_endpoint(value: str) -> bool:
    """An http or https URL with a host, without user info, query, fragment, white space or
    control character; at most 2048 bytes."""
    if len(value) > 2048 or has_control(value) or any(c in value for c in "?# "):
        return False
    parts = split_url(value)
    if parts is None:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and "@" not in parts.netloc


def read_ops_listen_addr(loader: Loader) -> str:
    """OPS_LISTEN_ADDR: host:port or :port."""
    return loader.text(OPS_KEY, listen_addr)


def valid_dsn(value: str) -> bool:
    """A DSN the Sentry SDK accepts: http or https, a public key (letters and digits) as the user
    part, an optional secret key, a host, an optional port, and the numeric project ID as the last
    path segment; no query, fragment or control character; at most 2048 bytes."""
    if len(value) > 2048 or has_control(value) or "?" in value or "#" in value:
        return False
    parts = split_url(value)
    if parts is None:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    user, password = parts.username, parts.password
    if user is None or _KEY.fullmatch(user) is None:
        return False
    if password is not None and _KEY.fullmatch(password) is None:
        return False
    project = parts.path.rstrip("/").rpartition("/")[2]
    return _PROJECT.fullmatch(project) is not None


def _environment(value: str) -> str | None:
    if _ENVIRONMENT.fullmatch(value) is None:
        return (
            "must be 1 to 64 lowercase letters, digits, '.', '_' or '-', starting with a letter "
            "or digit"
        )
    return None


def _rate(value: str) -> str | None:
    if _RATE.fullmatch(value) is None:
        return "must be a decimal from 0 to 1, such as 0 or 0.05"
    return None
