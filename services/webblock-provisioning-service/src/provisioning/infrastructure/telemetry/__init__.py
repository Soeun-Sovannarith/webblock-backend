"""Structured logging with an allow-list of fields and credential scrubbing, and correlation IDs
in context variables (decision 0006 section 7). Task H4 adds the Prometheus metrics (``metrics``,
``samples``) and the default-deny Sentry reports (``sentry``), imported from their modules;
decision 0022 E9 adds OpenTelemetry tracing (``tracing``), off unless an OTLP endpoint is set."""

from provisioning.infrastructure.telemetry.audit import LoggerAuditLog
from provisioning.infrastructure.telemetry.correlation import (
    MAX_CORRELATION_ID_LEN,
    correlation_id,
    correlation_scope,
    current_correlation_id,
    new_correlation_id,
    valid_correlation_id,
)
from provisioning.infrastructure.telemetry.logger import (
    ALLOWED_FIELDS,
    MAX_FIELD_LEN,
    REDACTED,
    Logger,
    monotonic_ms,
    new_logger,
    scrub,
    stdlib_handler,
)
from provisioning.infrastructure.telemetry.tracing import sampled_trace_id

__all__ = [
    "ALLOWED_FIELDS",
    "MAX_CORRELATION_ID_LEN",
    "MAX_FIELD_LEN",
    "REDACTED",
    "Logger",
    "LoggerAuditLog",
    "correlation_id",
    "correlation_scope",
    "current_correlation_id",
    "monotonic_ms",
    "new_correlation_id",
    "new_logger",
    "sampled_trace_id",
    "scrub",
    "stdlib_handler",
    "valid_correlation_id",
]
