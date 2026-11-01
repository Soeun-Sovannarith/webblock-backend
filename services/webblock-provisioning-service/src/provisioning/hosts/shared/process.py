"""The start of every long-lived process (api, worker, build_worker, bootstrap), in one place.

``run_process`` refuses a mounted default ServiceAccount token, loads the configuration from the
environment, starts the observability, builds the structured logger, routes the standard
library's records and the audit log through them, and runs the host's own part. A refused key or
setting is written to standard error by name and reason (never a value) and exits 2
(``EXIT_CONFIG``), whether it comes from the load, the observability or the host's part. The
observability is closed on every way out once it started.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from typing import Protocol

from provisioning.core.application import AuditLog
from provisioning.hosts.shared.observability import Observability, start_observability
from provisioning.hosts.shared.runtime import (
    EXIT_CONFIG,
    InvalidSettings,
    default_token_refused,
    report_config,
    report_settings,
    route_standard_logging,
)
from provisioning.infrastructure.config import DatabaseConfig, LoadError
from provisioning.infrastructure.config.loader import Lookup
from provisioning.infrastructure.config.telemetry import SentryConfig, TracingConfig
from provisioning.infrastructure.telemetry import Logger, LoggerAuditLog, new_logger


class ProcessConfig(Protocol):
    """What ``run_process`` reads of a process's configuration."""

    @property
    def log_level(self) -> int: ...

    @property
    def database(self) -> DatabaseConfig: ...

    @property
    def sentry(self) -> SentryConfig | None: ...

    @property
    def tracing(self) -> TracingConfig | None: ...


def run_process[C: ProcessConfig](
    label: str,
    load: Callable[[Lookup], C],
    run: Callable[[C, Logger, AuditLog, Observability], int],
    *,
    profile_of: Callable[[C], str],
    routes: Iterable[str] = (),
) -> int:
    """The process's exit code: ``run``'s, or ``EXIT_CONFIG`` for a refused configuration.
    ``label`` names the process in every line it writes; ``profile_of`` gives the profile the
    error reports name; ``routes`` are the API's route templates for the HTTP metrics."""
    out = sys.stderr
    if default_token_refused(out, label):
        return EXIT_CONFIG
    try:
        config = load(os.environ.get)
    except LoadError as err:
        report_config(out, label, err)
        return EXIT_CONFIG
    try:
        observability = start_observability(
            process=label,
            profile=profile_of(config),
            sentry=config.sentry,
            tracing=config.tracing,
            sample_timeout=config.database.tx_timeout.total_seconds(),
            routes=routes,
        )
    except InvalidSettings as err:
        report_settings(out, label, err)
        return EXIT_CONFIG
    try:
        logger = new_logger(out, config.log_level, observers=observability.log_observers())
        route_standard_logging(out, config.log_level, observability.log_handlers())
        audit = observability.audit(LoggerAuditLog(logger))
        return run(config, logger, audit, observability)
    except InvalidSettings as err:
        report_settings(out, label, err)
        return EXIT_CONFIG
    finally:
        observability.close()
