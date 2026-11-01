"""Metrics and error reports of one long-lived process, built by its composition root (task H4;
decision 0016 section 5 item 4).

``start_observability`` builds the process's ``Metrics``, when SENTRY_DSN names a DSN its
``ErrorReporter`` (with SENTRY_DSN=none no Sentry client exists at all), and, when
OTEL_EXPORTER_OTLP_ENDPOINT names a collector, its tracer provider (decision 0022 E9; unset or
none: no provider, every tracing helper is a no-op). The host then:

- builds its structured logger with ``log_observers`` (the identity's refusals for the metrics,
  error records for Sentry) and adds ``log_handlers`` to the standard library's root logger;
- wraps its ``AuditLog`` with ``audit`` (commands and build attempts for the metrics);
- passes ``metrics`` to the measuring edges and serves ``metrics.render`` on its operational
  listener;
- the API passes ``tracer_provider`` to its public app (the FastAPI instrumentation);
- calls ``close`` on the way out, which flushes what Sentry has queued within ``CLOSE_TIMEOUT``
  and sends the queued spans (bounded) before turning tracing off.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from opentelemetry.sdk.trace import TracerProvider

from provisioning.core.application import AuditLog
from provisioning.hosts.shared.runtime import InvalidSettings
from provisioning.infrastructure.config.telemetry import SentryConfig, TracingConfig
from provisioning.infrastructure.telemetry.logger import LogObserver
from provisioning.infrastructure.telemetry.metrics import MeteredAuditLog, Metrics
from provisioning.infrastructure.telemetry.sentry import ErrorReporter, sentry_client
from provisioning.infrastructure.telemetry.tracing import configure_tracing, shutdown_tracing

CLOSE_TIMEOUT: Final = 2.0
"""Seconds the process waits on the way out for Sentry to send what it has queued."""


@dataclass(frozen=True, slots=True)
class Observability:
    metrics: Metrics
    reporter: ErrorReporter | None  # None: SENTRY_DSN=none
    tracer_provider: TracerProvider | None = None  # None: tracing off

    def log_observers(self) -> tuple[LogObserver, ...]:
        if self.reporter is None:
            return (self.metrics.observe_log_event,)
        return self.metrics.observe_log_event, self.reporter.log_observer

    def log_handlers(self) -> tuple[logging.Handler, ...]:
        return () if self.reporter is None else (self.reporter.log_handler(),)

    def audit(self, inner: AuditLog) -> AuditLog:
        return MeteredAuditLog(inner, self.metrics)

    def close(self) -> None:
        if self.reporter is not None:
            self.reporter.close(CLOSE_TIMEOUT)
        if self.tracer_provider is not None:
            shutdown_tracing()


def start_observability(
    *,
    process: str,
    profile: str,
    sentry: SentryConfig | None,
    sample_timeout: float,
    routes: Iterable[str] = (),
    tracing: TracingConfig | None = None,
) -> Observability:
    """The process's metrics, with a DSN its error reporter, and with an OTLP endpoint its tracer
    provider (service name OTEL_SERVICE_NAME, or ``process``). Opens nothing: the Sentry
    transport starts its thread with the first report, the span exporter its own with the first
    batch. ``InvalidSettings`` (never with a value) when the Sentry SDK or the tracing setup
    refuses the settings."""
    try:
        metrics = Metrics(routes=routes, sample_timeout=sample_timeout)
        reporter: ErrorReporter | None = None
        if sentry is not None:
            client = sentry_client(
                dsn=sentry.dsn,
                environment=sentry.environment,
                traces_sample_rate=sentry.traces_sample_rate,
                process=process,
            )
            reporter = ErrorReporter(client, process=process, profile=profile)
        provider = None
        if tracing is not None:
            provider = configure_tracing(
                service_name=tracing.service_name or process, endpoint=tracing.endpoint
            )
        return Observability(metrics=metrics, reporter=reporter, tracer_provider=provider)
    except ValueError as err:
        raise InvalidSettings(str(err)) from None
