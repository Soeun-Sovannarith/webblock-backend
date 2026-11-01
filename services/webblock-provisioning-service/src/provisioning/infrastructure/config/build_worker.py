"""The build worker process's configuration (task G; decisions 0008 and 0014 sections 3, 10.2,
10.3 and 10.5).

The process consumes Project's "source published" events into the inbox, accepts releases from
it, starts their builds on Jenkins and polls them. It holds two database logins: PG_INTAKE_USER, a
member of ``provisioning_intake`` (the inbox), and PG_BUILD_USER, a member of
``provisioning_build`` (build steps). It holds no cluster credential.

Task H2: PROVISIONING_PROFILE is required; in production REPOSITORY_MODE and
PROTOTYPE_REPOSITORIES_FILE are refused when set (the repository file is a prototype stand-in,
OD-01). The build hint (decision 0016 section 5 item 2) adds BUILD_HINT_QUEUE_SIZE (hints waiting
to start, beyond which they are dropped), BUILD_HINT_LIVENESS (how long the listening connection
may stay silent before it is checked) and the listener's reconnect backoff
BUILD_HINT_RECONNECT_BASE and BUILD_HINT_RECONNECT_MAX.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config.api import (
    GRPC_TARGET_KEY,
    PROTOTYPE_FILE,
    grpc_target,
    read_log_level,
)
from provisioning.infrastructure.config.build import (
    BuildConfig,
    JenkinsConfig,
    read_build,
    read_jenkins,
)
from provisioning.infrastructure.config.common import (
    BackoffSettings,
    BrokerSettings,
    check_store_hold,
    distinct_logins,
    read_backoff,
    read_broker,
)
from provisioning.infrastructure.config.database import (
    DatabaseConfig,
    DatabaseLogin,
    read_database,
    read_login,
)
from provisioning.infrastructure.config.identity import absolute_path
from provisioning.infrastructure.config.loader import Loader, Lookup, load
from provisioning.infrastructure.config.profile import read_profile, refuse_in_production
from provisioning.infrastructure.config.telemetry import (
    SentryConfig,
    TracingConfig,
    read_ops_listen_addr,
    read_sentry,
    read_tracing,
)

PROJECT_GRPC: Final = "project-grpc"
"""REPOSITORY_MODE's value that selects Project's gRPC GetRepository (decision 0022 E7);
PROJECT_GRPC_TARGET (host:port) is required with it and refused without it."""

STANDIN_REPOSITORIES: Final = (
    "the repository file and Project's gRPC GetRepository (a draft proto) are prototype only; "
    "Project's repository confirmation is open (OD-01), so production starts no build"
)


@dataclass(frozen=True, slots=True)
class InboxSettings:
    """INBOX_* and INTAKE_MAX_PENDING_PER_PROJECT (E2's consumer and processor, B1's intake)."""

    consumer_concurrency: int
    store_hold: BackoffSettings
    store_failure_limit: int
    poll_interval: timedelta
    batch_size: int
    max_per_project: int
    visibility: timedelta
    concurrency: int
    registry_timeout: timedelta
    transaction_timeout: timedelta
    max_attempts: int
    deadline: timedelta
    backoff: BackoffSettings
    max_pending_per_project: int


@dataclass(frozen=True, slots=True)
class PollerSettings:
    """BUILD_POLL_*, BUILD_SWEEP_* and BUILD_DISPATCH_GRACE (E2's build poller, B1's
    ``PollBuilds`` and ``BuildAttempts``), and BUILD_PENDING_GRACE (optional, 30s): how long an
    attempt may stay ``pending`` before the poller starts it (QA-10, OD-51). It is not
    BUILD_DISPATCH_GRACE, which is about a running step without a handle."""

    poll_interval: timedelta
    poll_limit: int
    poll_concurrency: int
    sweep_interval: timedelta
    sweep_limit: int
    sweep_concurrency: int
    sweep_transaction_timeout: timedelta
    dispatch_grace: timedelta
    pending_grace: timedelta


@dataclass(frozen=True, slots=True)
class HintSettings:
    """BUILD_HINT_*: the build hint listener (task H2)."""

    queue_size: int
    liveness: timedelta
    reconnect: BackoffSettings


@dataclass(frozen=True, slots=True)
class BuildWorkerConfig:
    """Everything the build worker reads from its environment. ``repositories`` is empty and
    ``repositories_file`` None in production."""

    profile: Profile
    log_level: int
    database: DatabaseConfig
    intake_login: DatabaseLogin
    build_login: DatabaseLogin
    broker: BrokerSettings
    build: BuildConfig
    jenkins: JenkinsConfig
    repositories: str
    repositories_file: Path | None
    inbox: InboxSettings
    poller: PollerSettings
    hints: HintSettings
    ops_listen_addr: str  # OPS_LISTEN_ADDR: /metrics and /healthz (task H4)
    sentry: SentryConfig | None  # None: SENTRY_DSN=none (task H4)
    project_grpc_target: str | None = None  # set exactly with REPOSITORY_MODE=project-grpc
    tracing: TracingConfig | None = None  # None: OTEL_EXPORTER_OTLP_ENDPOINT unset or none


def load_build_worker(lookup: Lookup) -> BuildWorkerConfig:
    """Read and check the build worker configuration; ``LoadError`` names every refused key."""
    return load(lookup, read_build_worker)


def read_build_worker(loader: Loader) -> BuildWorkerConfig:
    """The build worker's section reader."""
    profile = read_profile(loader)
    database = read_database(loader)
    intake_login = read_login(loader, "PG_INTAKE_USER", "PG_INTAKE_PASS")
    build_login = read_login(loader, "PG_BUILD_USER", "PG_BUILD_PASS")
    distinct_logins(loader, ("PG_INTAKE_USER", intake_login), ("PG_BUILD_USER", build_login))
    repositories, repositories_file, project_grpc_target = _read_repositories(loader, profile)
    broker = read_broker(loader)
    inbox = _read_inbox(loader)
    check_store_hold(loader, "INBOX_STORE_HOLD_MAX", inbox.store_hold, broker)
    return BuildWorkerConfig(
        profile=profile,
        log_level=read_log_level(loader),
        database=database,
        intake_login=intake_login,
        build_login=build_login,
        broker=broker,
        build=read_build(loader),
        jenkins=read_jenkins(loader),
        repositories=repositories,
        repositories_file=repositories_file,
        inbox=inbox,
        poller=_read_poller(loader),
        hints=_read_hints(loader),
        ops_listen_addr=read_ops_listen_addr(loader),
        sentry=read_sentry(loader),
        tracing=read_tracing(loader),
        project_grpc_target=project_grpc_target,
    )


def _read_repositories(loader: Loader, profile: Profile) -> tuple[str, Path | None, str | None]:
    refused_mode = refuse_in_production(loader, profile, "REPOSITORY_MODE", STANDIN_REPOSITORIES)
    refused_file = refuse_in_production(
        loader, profile, "PROTOTYPE_REPOSITORIES_FILE", STANDIN_REPOSITORIES
    )
    refused_target = refuse_in_production(loader, profile, GRPC_TARGET_KEY, STANDIN_REPOSITORIES)
    if refused_mode or refused_file or refused_target:
        return "", None, None
    mode = loader.choice("REPOSITORY_MODE", (PROTOTYPE_FILE, PROJECT_GRPC))
    if mode == PROJECT_GRPC:
        if loader.optional("PROTOTYPE_REPOSITORIES_FILE") is not None:
            loader.fail(
                "PROTOTYPE_REPOSITORIES_FILE",
                f"must be unset unless REPOSITORY_MODE is {PROTOTYPE_FILE}",
            )
        return mode, None, loader.text(GRPC_TARGET_KEY, grpc_target)
    if loader.optional(GRPC_TARGET_KEY) is not None:
        loader.fail(GRPC_TARGET_KEY, f"must be unset unless REPOSITORY_MODE is {PROJECT_GRPC}")
    return mode, Path(loader.text("PROTOTYPE_REPOSITORIES_FILE", absolute_path)), None


def _read_hints(loader: Loader) -> HintSettings:
    return HintSettings(
        queue_size=loader.integer("BUILD_HINT_QUEUE_SIZE", 1, 10_000),
        liveness=loader.duration(
            "BUILD_HINT_LIVENESS", timedelta(seconds=1), timedelta(minutes=10)
        ),
        reconnect=read_backoff(loader, "BUILD_HINT_RECONNECT_BASE", "BUILD_HINT_RECONNECT_MAX"),
    )


def _read_inbox(loader: Loader) -> InboxSettings:
    hour = timedelta(hours=1)
    ms = timedelta(milliseconds=10)
    return InboxSettings(
        consumer_concurrency=loader.integer("INBOX_CONSUMER_CONCURRENCY", 1, 64),
        store_hold=read_backoff(loader, "INBOX_STORE_HOLD_BASE", "INBOX_STORE_HOLD_MAX"),
        store_failure_limit=loader.integer("INBOX_STORE_FAILURE_LIMIT", 1, 1000),
        poll_interval=loader.duration("INBOX_POLL_INTERVAL", ms, hour),
        batch_size=loader.integer("INBOX_BATCH_SIZE", 1, 1000),
        max_per_project=loader.integer("INBOX_MAX_PER_PROJECT", 1, 1000),
        visibility=loader.duration("INBOX_VISIBILITY_TIMEOUT", ms, hour),
        concurrency=loader.integer("INBOX_CONCURRENCY", 1, 256),
        registry_timeout=loader.duration("INBOX_REGISTRY_TIMEOUT", ms, hour),
        transaction_timeout=loader.duration("INBOX_TX_TIMEOUT", ms, hour),
        max_attempts=loader.integer("INBOX_MAX_ATTEMPTS", 1, 1000),
        deadline=loader.duration("INBOX_DEADLINE", timedelta(seconds=1), timedelta(days=30)),
        backoff=read_backoff(loader, "INBOX_BACKOFF_BASE", "INBOX_BACKOFF_MAX"),
        max_pending_per_project=loader.integer("INTAKE_MAX_PENDING_PER_PROJECT", 1, 10_000),
    )


def _read_poller(loader: Loader) -> PollerSettings:
    hour = timedelta(hours=1)
    ms = timedelta(milliseconds=10)
    return PollerSettings(
        poll_interval=loader.duration("BUILD_POLL_INTERVAL", ms, hour),
        poll_limit=loader.integer("BUILD_POLL_LIMIT", 1, 1000),
        poll_concurrency=loader.integer("BUILD_POLL_CONCURRENCY", 1, 256),
        sweep_interval=loader.duration("BUILD_SWEEP_INTERVAL", ms, hour),
        sweep_limit=loader.integer("BUILD_SWEEP_LIMIT", 1, 1000),
        sweep_concurrency=loader.integer("BUILD_SWEEP_CONCURRENCY", 1, 256),
        sweep_transaction_timeout=loader.duration("BUILD_SWEEP_TX_TIMEOUT", ms, hour),
        dispatch_grace=loader.duration("BUILD_DISPATCH_GRACE", ms, hour),
        pending_grace=loader.optional_duration(
            "BUILD_PENDING_GRACE", timedelta(seconds=30), timedelta(seconds=1), hour
        ),
    )
