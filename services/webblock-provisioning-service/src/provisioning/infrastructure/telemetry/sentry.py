"""Error reports to Sentry, default-deny (task H4; decision 0016 section 5 item 4; CLAUDE.md
section 3: Sentry data must be redacted).

The client is built here with every automatic collection off: no default or auto-enabled
integration (nothing in another library is patched), no personal data, no local variables, no
source lines, no request bodies, no breadcrumbs, no sessions, client reports, logs or metrics of
Sentry's own, no trace propagation, no proxy taken from the environment, and the release, server
name and project root set here so the SDK neither runs ``git`` nor reads the host name or the
working directory. The SDK's own background thread sends the events (its HTTP transport).

What leaves the process is decided by ``scrub_event``, the client's ``before_send``, which builds a
new event from an allow-list instead of removing what is known to be dangerous:

- only an event with an exception is sent; a message event is dropped;
- each exception keeps its type, its module, its mechanism and its stack frames (file, function,
  module, line, in-app); its message (``value``) is dropped, because a driver's or an HTTP
  client's error text can carry a value; local variables, source lines and absolute paths are
  dropped;
- the event keeps its ID, time, level, platform, release, environment, server name (the process)
  and the SDK's name and version;
- tags are limited to ``process``, ``profile`` and ``correlation_id``, each checked against its
  form; the project ID is not a tag (decision 0006: agent-visible data is project-scoped, which
  needs phase P-C);
- request data (headers, cookies, body, query, environment), user, extra, contexts, breadcrumbs,
  modules and anything else are dropped.

``drop_breadcrumb`` (``before_breadcrumb``) drops every breadcrumb and ``drop_transaction``
(``before_send_transaction``) every transaction: nothing here starts one, and tracing waits for a
trace backend (OD-65). A hook that raises drops the event: the SDK sends nothing then.

``ErrorReporter`` reports exceptions: the ones it is handed, and, through ``log_observer`` and
``log_handler``, an exception being handled while a record of level error or above is written
(the way the processes log unexpected failures). Every report is best effort and never raises.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, cast

import sentry_sdk
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint
from sentry_sdk.utils import event_from_exception

from provisioning.infrastructure.config.secret import Secret
from provisioning.infrastructure.config.telemetry import valid_dsn
from provisioning.infrastructure.telemetry.correlation import (
    current_correlation_id,
    valid_correlation_id,
)

RELEASE: Final = "webblock-provisioning@0.0.0"
"""The release every event names: the package's version (pyproject.toml), fixed here so that the
SDK never runs ``git`` to guess one."""

TAG_PROCESS: Final = "process"
TAG_PROFILE: Final = "profile"
TAG_CORRELATION_ID: Final = "correlation_id"

PROFILES: Final = frozenset({"prototype", "production"})

REPORTED_LEVELS: Final = frozenset({"error", "critical", "exception"})

UNREPORTED_ERROR_CODES: Final = frozenset(
    {
        "dependency_unavailable",
        "identity_unavailable",
        "overloaded",
        "timeout",
    }
)
"""Error codes of a 5xx request that are expected conditions, visible in the metrics and the
readiness check, not defects: such a request is not reported."""

_PROCESS: Final = re.compile(r"provisioning-[a-z-]{1,40}", re.ASCII)
_TOKEN: Final = re.compile(r"[A-Za-z0-9_.:+-]{1,128}", re.ASCII)
_IDENTIFIER: Final = re.compile(r"[A-Za-z_<][A-Za-z0-9_.<>]{0,199}", re.ASCII)
_FILENAME: Final = re.compile(r"[A-Za-z0-9_./<>-]{1,300}", re.ASCII)
_HEX_ID: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)
_RELEASE: Final = re.compile(r"[A-Za-z0-9_.:+@-]{1,128}", re.ASCII)
_SOURCE: Final = re.compile(r"__cause__|__context__|exceptions\[[0-9]{1,3}]", re.ASCII)
_TIMESTAMP: Final = re.compile(r"[0-9T:.+Z-]{10,40}", re.ASCII)
_LEVELS: Final = frozenset({"fatal", "error", "warning", "info", "debug"})
_MAX_EXCEPTIONS: Final = 10
_MAX_FRAMES: Final = 100


def sentry_client(
    *,
    dsn: Secret,
    environment: str,
    traces_sample_rate: float,
    process: str,
    transport: Transport | None = None,
) -> sentry_sdk.Client:
    """A client with every automatic collection off and ``scrub_event`` as its ``before_send``.
    ``transport`` replaces the SDK's HTTP transport (tests capture the envelopes with it).
    ``ValueError`` (never with the DSN) for a process name or a sample rate out of form; the SDK's
    own error for a DSN it cannot parse is replaced by one that names no value."""
    if _PROCESS.fullmatch(process) is None:
        raise ValueError("sentry: the process name is not of the form provisioning-<name>")
    if not 0.0 <= traces_sample_rate <= 1.0:
        raise ValueError("sentry: the traces sample rate must be from 0 to 1")
    if _TOKEN.fullmatch(environment) is None:
        raise ValueError("sentry: the environment is not a short token")
    if not valid_dsn(dsn.reveal()):
        raise ValueError("sentry: SENTRY_DSN is not a Sentry DSN")
    try:
        return sentry_sdk.Client(
            dsn=dsn.reveal(),
            environment=environment,
            release=RELEASE,
            server_name=process,
            project_root=str(Path(__file__).resolve().parents[2]),  # src/provisioning
            traces_sample_rate=traces_sample_rate,
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=[],
            send_default_pii=False,
            include_local_variables=False,
            include_source_context=False,
            max_request_body_size="never",
            attach_stacktrace=False,
            max_breadcrumbs=0,
            before_send=scrub_event,
            before_breadcrumb=drop_breadcrumb,
            before_send_transaction=drop_transaction,
            send_client_reports=False,
            auto_session_tracking=False,
            enable_backpressure_handling=False,
            enable_logs=False,
            enable_metrics=False,
            propagate_traces=False,
            trace_propagation_targets=[],
            http_proxy="",
            https_proxy="",
            keep_alive=False,
            debug=False,
            spotlight=False,
            transport=transport,
        )
    except Exception:
        raise ValueError("sentry: SENTRY_DSN cannot be used by the Sentry SDK") from None


def scrub_event(raw: Event, hint: Hint) -> Event | None:
    """``before_send``: a new event built from the allow-list of the module docstring, or None
    (nothing is sent) for an event without an exception."""
    del hint
    # The SDK has serialized the event: plain JSON values (the timestamp is a string by now).
    event = cast("Mapping[str, Any]", raw)
    exceptions = _exceptions(event.get("exception"))
    if not exceptions:
        return None
    out: dict[str, Any] = {"exception": {"values": exceptions}}
    event_id = event.get("event_id")
    if isinstance(event_id, str) and _HEX_ID.fullmatch(event_id):
        out["event_id"] = event_id
    timestamp = event.get("timestamp")
    if isinstance(timestamp, str) and _TIMESTAMP.fullmatch(timestamp):
        out["timestamp"] = timestamp
    level = event.get("level")
    out["level"] = level if level in _LEVELS else "error"
    out["platform"] = "python"
    release = event.get("release")
    if isinstance(release, str) and _RELEASE.fullmatch(release):
        out["release"] = release
    environment = event.get("environment")
    if isinstance(environment, str) and _TOKEN.fullmatch(environment):
        out["environment"] = environment
    server = event.get("server_name")
    if isinstance(server, str) and _PROCESS.fullmatch(server):
        out["server_name"] = server
    tags = _tags(event.get("tags"))
    if tags:
        out["tags"] = tags
    sdk = event.get("sdk")
    if isinstance(sdk, Mapping):
        name, version = sdk.get("name"), sdk.get("version")
        if isinstance(name, str) and isinstance(version, str):
            if _TOKEN.fullmatch(name) and _TOKEN.fullmatch(version):
                out["sdk"] = {"name": name, "version": version}
    return cast("Event", out)


def drop_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> None:
    """``before_breadcrumb``: no breadcrumb is ever kept."""
    del crumb, hint


def drop_transaction(event: Event, hint: Hint) -> None:
    """``before_send_transaction``: no transaction is sent until tracing is decided (OD-65)."""
    del event, hint


class ErrorReporter:
    """Reports exceptions to one client with the process's tags; never raises."""

    __slots__ = ("_client", "_process", "_profile")

    def __init__(self, client: sentry_sdk.Client, *, process: str, profile: str) -> None:
        if _PROCESS.fullmatch(process) is None or profile not in PROFILES:
            raise ValueError("sentry: unknown process or profile")
        self._client = client
        self._process = process
        self._profile = profile

    def report(self, error: BaseException) -> None:
        """Send ``error`` (its type, module and frames only) with the process's tags."""
        try:
            event, hint = event_from_exception(error, client_options=self._client.options)
            event["tags"] = self._tags()
            self._client.capture_event(event=event, hint=hint)
        except Exception:  # a report is best effort; the caller's own handling goes on
            return

    def report_active(self) -> None:
        """Report the exception being handled, if any."""
        error = sys.exception()
        if error is not None:
            self.report(error)

    def log_observer(self, level: str, event: Mapping[str, Any]) -> None:
        """For the structured logger: an event of level error or above written while an
        exception is handled is reported, unless it ends a request with an expected code
        (``UNREPORTED_ERROR_CODES``)."""
        if level not in REPORTED_LEVELS:
            return
        if event.get("error_code") in UNREPORTED_ERROR_CODES:
            return
        self.report_active()

    def log_handler(self) -> logging.Handler:
        """For the standard library's logging: a record of level ERROR or above with an
        exception (its ``exc_info``, or the one being handled) is reported. Its message and its
        arguments are never read."""
        return _ReportingHandler(self)

    def close(self, timeout: float) -> None:
        """Flush what is queued within ``timeout`` seconds and stop the transport."""
        try:
            self._client.close(timeout=timeout)
        except Exception:  # the process is stopping anyway
            return

    def _tags(self) -> dict[str, str]:
        tags = {TAG_PROCESS: self._process, TAG_PROFILE: self._profile}
        correlation = current_correlation_id()
        if correlation is not None and valid_correlation_id(correlation):
            tags[TAG_CORRELATION_ID] = correlation
        return tags


class _ReportingHandler(logging.Handler):
    __slots__ = ("_reporter",)

    def __init__(self, reporter: ErrorReporter) -> None:
        super().__init__(logging.ERROR)
        self._reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        info = record.exc_info
        if info is not None and info[1] is not None:
            self._reporter.report(info[1])
            return
        self._reporter.report_active()

    def handleError(self, record: logging.LogRecord) -> None:
        # The default prints a traceback with the record's arguments to stderr.
        del record


def _exceptions(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    values = raw.get("values")
    if not isinstance(values, list):
        return []
    out: list[dict[str, Any]] = []
    for value in values[-_MAX_EXCEPTIONS:]:
        if not isinstance(value, Mapping):
            continue
        kind = value.get("type")
        if not isinstance(kind, str) or _IDENTIFIER.fullmatch(kind) is None:
            continue
        entry: dict[str, Any] = {"type": kind}
        module = value.get("module")
        if isinstance(module, str) and _IDENTIFIER.fullmatch(module):
            entry["module"] = module
        mechanism = _mechanism(value.get("mechanism"))
        if mechanism:
            entry["mechanism"] = mechanism
        frames = _frames(value.get("stacktrace"))
        if frames:
            entry["stacktrace"] = {"frames": frames}
        out.append(entry)
    return out


def _mechanism(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    kind = raw.get("type")
    if isinstance(kind, str) and _IDENTIFIER.fullmatch(kind):
        out["type"] = kind
    handled = raw.get("handled")
    if isinstance(handled, bool):
        out["handled"] = handled
    for key in ("exception_id", "parent_id"):
        number = raw.get(key)
        if isinstance(number, int) and not isinstance(number, bool):
            out[key] = number
    source = raw.get("source")
    if isinstance(source, str) and _SOURCE.fullmatch(source):
        out["source"] = source
    return out


def _frames(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return []
    frames = raw.get("frames")
    if not isinstance(frames, list):
        return []
    out: list[dict[str, Any]] = []
    for frame in frames[-_MAX_FRAMES:]:
        if not isinstance(frame, Mapping):
            continue
        entry: dict[str, Any] = {}
        filename = frame.get("filename")
        if isinstance(filename, str) and _FILENAME.fullmatch(filename):
            entry["filename"] = filename
        for key in ("function", "module"):
            text = frame.get(key)
            if isinstance(text, str) and _IDENTIFIER.fullmatch(text):
                entry[key] = text
        line = frame.get("lineno")
        if isinstance(line, int) and not isinstance(line, bool):
            entry["lineno"] = line
        in_app = frame.get("in_app")
        if isinstance(in_app, bool):
            entry["in_app"] = in_app
        if entry:
            out.append(entry)
    return out


def _tags(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    process = raw.get(TAG_PROCESS)
    if isinstance(process, str) and _PROCESS.fullmatch(process):
        out[TAG_PROCESS] = process
    profile = raw.get(TAG_PROFILE)
    if isinstance(profile, str) and profile in PROFILES:
        out[TAG_PROFILE] = profile
    correlation = raw.get(TAG_CORRELATION_ID)
    if isinstance(correlation, str) and valid_correlation_id(correlation):
        out[TAG_CORRELATION_ID] = correlation
    return out
