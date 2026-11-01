"""The structured logger (decision 0006 section 7).

One JSON object per line with ``time``, ``level`` and ``msg``, the correlation ID of the current
context, and only allow-listed fields. A field that is not listed is written as ``[REDACTED]``,
whatever its name and value: a deny-list misses the field somebody adds next month, an allow-list
does not. Text values and the message are scrubbed of credential-shaped text, a second line of
defence for text from other libraries.

The logger is built per process with ``new_logger`` (structlog, without its global configuration);
``stdlib_handler`` writes records of the standard library's ``logging`` (the core's use cases,
uvicorn, the MCP SDK) through the same filter.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, TextIO
from uuid import UUID

import structlog

from provisioning.infrastructure.telemetry.correlation import (
    current_correlation_id,
    valid_correlation_id,
)
from provisioning.infrastructure.telemetry.tracing import current_trace_id

# Field names that may appear in a record. None of them carries a credential, a request or
# response body, a requester or an idempotency key, and only ``actor`` (audit events) a subject.
# Adding a field means adding it here, in a reviewed change. Revision 19 names: claim_number and
# command_number.
FIELD_CORRELATION_ID: Final = "correlation_id"
FIELD_TRACE_ID: Final = "trace_id"  # the current span's trace ID when tracing is on (0022 E9)
FIELD_COMPONENT: Final = "component"
FIELD_OPERATION: Final = "operation"
FIELD_ACTION: Final = "action"
FIELD_METHOD: Final = "method"
FIELD_ROUTE: Final = "route"
FIELD_STATUS: Final = "status"
FIELD_DURATION: Final = "duration_ms"
FIELD_PROJECT_ID: Final = "project_id"
FIELD_RELEASE_ID: Final = "release_id"
FIELD_COMMAND_ID: Final = "command_id"
FIELD_KIND: Final = "kind"
FIELD_ATTEMPT: Final = "attempt"
FIELD_COUNT: Final = "count"
FIELD_LISTEN_ADDR: Final = "listen_addr"
FIELD_ERROR_CODE: Final = "error_code"
FIELD_ERROR: Final = "error"
FIELD_AUTH_CAUSE: Final = "auth_cause"
FIELD_KID: Final = "kid"
FIELD_CLAIM: Final = "claim"
FIELD_CONNECTION: Final = "connection"
FIELD_FAILURE_CODE: Final = "failure_code"
FIELD_COMMAND_NUMBER: Final = "command_number"
FIELD_HOLD: Final = "hold"
FIELD_CLAIM_NUMBER: Final = "claim_number"
FIELD_PUBLISH_ATTEMPTS: Final = "publish_attempts"
FIELD_QUEUE: Final = "queue"
FIELD_REASON: Final = "reason"
FIELD_REPLY_CODE: Final = "reply_code"
FIELD_RETRY_AFTER: Final = "retry_after"
FIELD_RETRY_IN: Final = "retry_in"
FIELD_REPLAYED: Final = "replayed"
FIELD_GENERATION_REQUEST_ID: Final = "generation_request_id"
FIELD_STEP_ID: Final = "step_id"
# Audit events (decision 0014 section 21 answer 3): ``actor`` is the one field that names who
# acted, a subject or component name, never a credential; ``audit`` and ``alert`` mark the line.
FIELD_AUDIT: Final = "audit"
FIELD_ALERT: Final = "alert"
FIELD_ACTOR_KIND: Final = "actor_kind"
FIELD_ACTOR: Final = "actor"
FIELD_RESULT: Final = "result"
FIELD_AT: Final = "at"

ALLOWED_FIELDS: Final = frozenset(
    {
        FIELD_CORRELATION_ID,
        FIELD_TRACE_ID,
        FIELD_COMPONENT,
        FIELD_OPERATION,
        FIELD_ACTION,
        FIELD_METHOD,
        FIELD_ROUTE,
        FIELD_STATUS,
        FIELD_DURATION,
        FIELD_PROJECT_ID,
        FIELD_RELEASE_ID,
        FIELD_COMMAND_ID,
        FIELD_KIND,
        FIELD_ATTEMPT,
        FIELD_COUNT,
        FIELD_LISTEN_ADDR,
        FIELD_ERROR_CODE,
        FIELD_ERROR,
        FIELD_AUTH_CAUSE,
        FIELD_KID,
        FIELD_CLAIM,
        FIELD_CONNECTION,
        FIELD_FAILURE_CODE,
        FIELD_COMMAND_NUMBER,
        FIELD_HOLD,
        FIELD_CLAIM_NUMBER,
        FIELD_PUBLISH_ATTEMPTS,
        FIELD_QUEUE,
        FIELD_REASON,
        FIELD_REPLY_CODE,
        FIELD_RETRY_AFTER,
        FIELD_RETRY_IN,
        FIELD_REPLAYED,
        FIELD_GENERATION_REQUEST_ID,
        FIELD_STEP_ID,
        FIELD_AUDIT,
        FIELD_ALERT,
        FIELD_ACTOR_KIND,
        FIELD_ACTOR,
        FIELD_RESULT,
        FIELD_AT,
    }
)

# Fields that print booleans only, and fields that print integers only (so that claim_number, for
# one, can never print a credential); any other value under them is redacted.
_BOOLEAN_FIELDS: Final = frozenset({FIELD_REPLAYED, FIELD_AUDIT, FIELD_ALERT})
_COUNTER_FIELDS: Final = frozenset(
    {FIELD_COMMAND_NUMBER, FIELD_CLAIM_NUMBER, FIELD_PUBLISH_ATTEMPTS, FIELD_REPLY_CODE}
)

MAX_FIELD_LEN: Final = 1024
"""A logged string is cut to this many bytes and marked."""

REDACTED: Final = "[REDACTED]"
_INVALID: Final = "[INVALID]"
_TRUNCATED: Final = "…[truncated]"
_BUILTIN: Final = ("time", "level", "msg")
_FIELD_KEY: Final = re.compile(r"[a-z][a-z0-9_]{0,39}", re.ASCII)
_TRACE_ID: Final = re.compile(r"[0-9a-f]{32}", re.ASCII)

_URL_USERINFO: Final = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@]+@")
_JWT: Final = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_AUTH_SCHEME: Final = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}")
_SECRET_PAIR: Final = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|authorization|cookie|api[_-]?key)"
    r"([\"']?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)"
)


def scrub(text: str) -> str:
    """Remove credential-shaped text: user and password in a URL, JWTs, Bearer and Basic
    credentials, and values after names such as password, token, secret, cookie or
    authorization; then cut the result to MAX_FIELD_LEN bytes at a character boundary. A second
    line of defence for text from other libraries: code that logs must not rely on it for values it
    holds itself."""
    text = _URL_USERINFO.sub(r"\g<1>" + REDACTED + "@", text)
    text = _JWT.sub(REDACTED, text)
    text = _AUTH_SCHEME.sub(r"\g<1> " + REDACTED, text)
    text = _SECRET_PAIR.sub(r"\g<1>\g<2>" + REDACTED, text)
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > MAX_FIELD_LEN:
        text = encoded[:MAX_FIELD_LEN].decode("utf-8", "ignore") + _TRUNCATED
    return text


type Logger = Any
"""The structlog bound logger ``new_logger`` returns (``debug`` ... ``critical`` with keyword
fields). Typed loosely: structlog's filtering classes are generated at run time."""


type LogObserver = Callable[[str, Mapping[str, Any]], None]
"""Sees each record of ``new_logger`` before its fields are filtered: the method's level name
("info", "error", ...) and the record's fields as the caller passed them. Used for metrics and
error reports (task H4); it must never raise, and anything it raises is dropped. It writes
nothing to the log."""


def new_logger(stream: TextIO, level: int, *, observers: Sequence[LogObserver] = ()) -> Logger:
    """A JSON logger writing one object per line to ``stream``, records at ``level`` and above.
    It uses no global structlog configuration. ``observers`` see every record, whatever its
    level, before the level filter (task H4: a metric must not depend on LOG_LEVEL)."""
    if not observers:
        return structlog.wrap_logger(
            structlog.WriteLogger(stream),
            processors=[_rename_builtins, _add_correlation_id, *_TAIL],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            context_class=dict,
            cache_logger_on_first_use=False,
        )
    return structlog.wrap_logger(
        structlog.WriteLogger(stream),
        processors=[
            _observing(tuple(observers)),
            _level_at_least(level),
            _rename_builtins,
            _add_correlation_id,
            *_TAIL,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        cache_logger_on_first_use=False,
    )


def stdlib_handler(stream: TextIO, level: int) -> logging.Handler:
    """A ``logging`` handler that writes each record as the same filtered JSON object: the message
    (scrubbed) and the logger's name as ``component``. A record's arguments other than those of its
    message, its ``extra`` fields and its exception are never written."""
    handler = _StdlibHandler(stream)
    handler.setLevel(level)
    return handler


_LEVEL_NUMBERS: Final = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "exception": logging.ERROR,
    "critical": logging.CRITICAL,
    "fatal": logging.CRITICAL,
}


def _level_at_least(level: int) -> Callable[..., Any]:
    def keep(_logger: object, method: str, event: MutableMapping[str, Any]) -> Any:
        if _LEVEL_NUMBERS.get(method, logging.CRITICAL) < level:
            raise structlog.DropEvent
        return event

    return keep


def _observing(observers: tuple[LogObserver, ...]) -> Callable[..., Any]:
    def observe(_logger: object, method: str, event: MutableMapping[str, Any]) -> Any:
        for observer in observers:
            with contextlib.suppress(Exception):  # an observer never changes the record
                observer(method, MappingProxyType(event))
        return event

    return observe


def _rename_builtins(_logger: object, _method: str, event: MutableMapping[str, Any]) -> Any:
    # A caller cannot slip a value past the filter by naming it like a built-in field.
    for key in _BUILTIN:
        if key in event:
            event["field_" + key] = event.pop(key)
    return event


def _add_correlation_id(_logger: object, _method: str, event: MutableMapping[str, Any]) -> Any:
    current = current_correlation_id()
    if current is not None and FIELD_CORRELATION_ID not in event:
        event[FIELD_CORRELATION_ID] = current
    trace_id = current_trace_id()
    if trace_id is not None and FIELD_TRACE_ID not in event:
        event[FIELD_TRACE_ID] = trace_id
    return event


def _filter(_logger: object, _method: str, event: MutableMapping[str, Any]) -> Any:
    message = event.pop("event", "")
    level = event.pop("level", "info")
    out: dict[str, Any] = {
        "time": datetime.now(UTC).isoformat(timespec="microseconds"),
        "level": level,
        "msg": scrub(message) if isinstance(message, str) else REDACTED,
    }
    out.update(_fields(event))
    return out


def _fields(event: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in event.items():
        if value is None:  # an absent optional field is not written
            continue
        name = key if _FIELD_KEY.fullmatch(key) else "invalid_field"
        out[name] = _value(name, value)
    return out


def _value(key: str, value: object) -> object:
    if key not in ALLOWED_FIELDS:
        return REDACTED
    if key in _COUNTER_FIELDS:
        return value if isinstance(value, int) and not isinstance(value, bool) else REDACTED
    if key in _BOOLEAN_FIELDS:
        return value if isinstance(value, bool) else REDACTED
    if key == FIELD_CORRELATION_ID:
        if isinstance(value, str) and valid_correlation_id(value):
            return value
        return _INVALID
    if key == FIELD_TRACE_ID:
        return value if isinstance(value, str) and _TRACE_ID.fullmatch(value) else _INVALID
    match value:
        case bool() | int() | float():
            return value
        case Enum():
            return scrub(str(value.value)) if isinstance(value.value, str) else REDACTED
        case str():
            return scrub(value)
        case UUID():
            return str(value)
        case timedelta():
            return int(value / timedelta(milliseconds=1))
        case datetime():
            return value.isoformat()
        case BaseException():
            return scrub(str(value))
        case _:
            return REDACTED


def _render(_logger: object, _method: str, event: Mapping[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


# The processors after the observers, the level filter and the field preparation.
_TAIL: Final = (structlog.processors.add_log_level, _filter, _render)


class _StdlibHandler(logging.StreamHandler[TextIO]):
    """Formats a standard-library record through the filter; never prints a traceback."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # an unformattable record; its arguments are not written either
            message = "unformattable log record"
        event: dict[str, Any] = {"event": message, FIELD_COMPONENT: record.name}
        _add_correlation_id(None, "", event)
        out = _filter(None, "", event)
        out["time"] = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="microseconds")
        out["level"] = record.levelname.lower()
        return _render(None, "", out)

    def handleError(self, record: logging.LogRecord) -> None:
        # The default prints a traceback with the record's arguments to stderr.
        del record


def monotonic_ms(start: float) -> int:
    """Milliseconds since ``start``, a ``time.monotonic()`` reading, for duration_ms."""
    return int((time.monotonic() - start) * 1000)
