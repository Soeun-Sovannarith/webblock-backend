"""The release worker process's configuration (task G; decisions 0005 section 7, 0013 section 3,
0014 sections 3 and 10.5).

One process runs the dispatcher, the command consumer with the deploy, routing and hosting
executors, and the Payment-event consumer. It holds two database logins: PG_USER, a member of
``provisioning_worker`` (jobs and fenced writes), and PG_HOSTING_INTAKE_USER, a member of
``provisioning_hosting_intake`` (the commands of Payment events). Its cluster identity is the
worker kubeconfig; it never reads the bootstrap kubeconfig.

Wave 6 (decision 0014 section 21) adds optional keys whose defaults keep the earlier behaviour:
RECONCILE_INTERVAL and RECONCILE_BATCH, SITE_PROBE_SUCCESSES and SITE_PROBE_MAX_BODY_BYTES,
USAGE_PUBLISH_INTERVAL and USAGE_BATCH_SIZE; and ENTITLEMENT_MODE=prototype-file with
ENTITLEMENT_FILE.

Task T: the HTTPS Gateway the promotion probe asks (SITE_GATEWAY_HTTPS_URL, verified against
SITE_GATEWAY_CA_FILE) and the preview route's probe token (PREVIEW_PROBE_TOKEN, a secret that
prints redacted).

Task H2: PROVISIONING_PROFILE is required. In production ENTITLEMENT_MODE, ENTITLEMENT_FILE and
HOSTING_ENTITLEMENT_MODE are refused when set (each selects a prototype stand-in; OD-04), and the
host enables no command kind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

from provisioning.core.application.routing.background.routing import PROBE_CONFIRMATIONS
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import GO_LIVE_TRIES, wait_after_failed_try
from provisioning.infrastructure.config.api import (
    PAYMENT_GRPC,
    read_entitlement_mode,
    read_log_level,
    read_payment_target,
)
from provisioning.infrastructure.config.build import registry_location
from provisioning.infrastructure.config.common import (
    BackoffSettings,
    BrokerSettings,
    ClusterSettings,
    check_store_hold,
    distinct_logins,
    read_backoff,
    read_broker,
    read_cluster,
)
from provisioning.infrastructure.config.database import (
    DatabaseConfig,
    DatabaseLogin,
    read_database,
    read_login,
)
from provisioning.infrastructure.config.identity import absolute_path
from provisioning.infrastructure.config.loader import (
    Loader,
    Lookup,
    load,
    plain_text,
)
from provisioning.infrastructure.config.net import http_url
from provisioning.infrastructure.config.profile import read_profile, refuse_in_production
from provisioning.infrastructure.config.secret import Secret
from provisioning.infrastructure.config.telemetry import (
    SentryConfig,
    TracingConfig,
    read_ops_listen_addr,
    read_sentry,
    read_tracing,
)

# The only accepted value of HOSTING_ENTITLEMENT_MODE in the prototype: the in-process record of
# the latest Payment event per project (F2 report, "Contract of the in-process entitlement
# record"). Payment's real contract (OD-04) replaces it.
PROTOTYPE_PAYMENT_EVENTS: Final = "prototype-payment-events"

STANDIN_PAYMENT_EVENTS: Final = (
    "the in-process Payment event record is a prototype stand-in, and Payment's gRPC "
    "GetEntitlement is a draft proto; Payment's events are open (OD-04), so production consumes "
    "none"
)


@dataclass(frozen=True, slots=True)
class DispatchSettings:
    """DISPATCH_* (decision 0005 section 7)."""

    poll_interval: timedelta
    batch_size: int
    publish_concurrency: int
    confirm_timeout: timedelta
    visibility_timeout: timedelta
    redelivery_interval: timedelta
    backoff: BackoffSettings


@dataclass(frozen=True, slots=True)
class ConsumerSettings:
    """WORKER_* (decision 0005 section 7)."""

    worker_id: str
    concurrency: int
    lease_ttl: timedelta
    heartbeat_interval: timedelta
    attempt_timeout: timedelta
    max_attempts: int
    retry_backoff: BackoffSettings
    store_hold: BackoffSettings
    store_failure_limit: int


@dataclass(frozen=True, slots=True)
class DeploySettings:
    """DEPLOY_* (F1's ``DeployParams`` and the executor's abandon bound)."""

    image_registry: str
    ready_timeout: timedelta
    poll_interval: timedelta
    call_timeout: timedelta
    delete_timeout: timedelta
    finish_margin: timedelta
    abandon_timeout: timedelta


@dataclass(frozen=True, slots=True)
class SiteSettingsConfig:
    """SITE_* (D's ``SiteSettings`` and ``SiteClusterSettings``): the per-site resources, the
    Deployment's progress deadline, the offline Service's port, the hostname suffix, where the
    Gateway answers, the promotion probe's path (decision 0014 section 15), and the probe and
    delete-retry bounds."""

    cpu_millicores: int
    memory_mib: int
    progress_deadline: timedelta
    offline_service_port: int
    domain: str
    gateway_url: str  # SITE_GATEWAY_HTTPS_URL
    gateway_ca_file: Path
    probe_path: str
    probe_timeout: timedelta
    delete_retry_base: timedelta
    preview_token: Secret
    probe_successes: int  # SITE_PROBE_SUCCESSES, optional, 1 (QA-08)
    probe_max_body_bytes: int  # SITE_PROBE_MAX_BODY_BYTES, optional, 65536 (QA-01)
    probe_gap: timedelta  # SITE_PROBE_GAP, optional, 0.5s, 0 to 10s: between two probes of a run


@dataclass(frozen=True, slots=True)
class RouteSettings:
    """ROUTE_* (F2's ``RoutingSettings``)."""

    switch_deadline: timedelta
    restore_deadline: timedelta
    poll_interval: timedelta


@dataclass(frozen=True, slots=True)
class ReconcileSettings:
    """RECONCILE_* (decision 0014 section 21 ruling 9), both optional: the pause between the
    reconciler's rounds (30s) and the projects it reads per round (50)."""

    interval: timedelta
    batch_size: int


@dataclass(frozen=True, slots=True)
class UsageSettings:
    """USAGE_* (decision 0014 section 21 answer 2), both optional: the pause between the usage
    outbox publisher's polls (5s) and the rows it publishes per poll (50)."""

    publish_interval: timedelta
    batch_size: int


@dataclass(frozen=True, slots=True)
class PaymentSettings:
    """PAYMENT_* (E2's Payment-event consumer)."""

    concurrency: int
    retry_backoff: BackoffSettings
    max_retries: int


@dataclass(frozen=True, slots=True)
class WorkerProcessConfig:
    """Everything the worker process reads from its environment. In production the three
    stand-in modes are empty."""

    profile: Profile
    log_level: int
    command_deadline: timedelta
    entitlement: str  # ENTITLEMENT_MODE: prototype-allow or prototype-file
    entitlement_file: Path | None  # ENTITLEMENT_FILE, set exactly in prototype-file mode
    hosting_entitlement: str  # HOSTING_ENTITLEMENT_MODE: prototype-payment-events or payment-grpc
    database: DatabaseConfig
    worker_login: DatabaseLogin
    hosting_login: DatabaseLogin
    broker: BrokerSettings
    dispatch: DispatchSettings
    consumer: ConsumerSettings
    deploy: DeploySettings
    site: SiteSettingsConfig
    route: RouteSettings
    payment: PaymentSettings
    cluster: ClusterSettings
    reconcile: ReconcileSettings
    usage: UsageSettings
    ops_listen_addr: str  # OPS_LISTEN_ADDR: /metrics and /healthz (task H4)
    sentry: SentryConfig | None  # None: SENTRY_DSN=none (task H4)
    payment_grpc_target: str | None = None  # PAYMENT_GRPC_TARGET, set with a payment-grpc mode
    tracing: TracingConfig | None = None  # None: OTEL_EXPORTER_OTLP_ENDPOINT unset or none


def load_worker(lookup: Lookup) -> WorkerProcessConfig:
    """Read and check the worker configuration; ``LoadError`` names every refused key."""
    return load(lookup, read_worker)


def read_worker(loader: Loader) -> WorkerProcessConfig:
    """The worker process's section reader."""
    profile = read_profile(loader)
    database = read_database(loader)
    worker_login = read_login(loader, "PG_USER", "PG_PASS")
    hosting_login = read_login(loader, "PG_HOSTING_INTAKE_USER", "PG_HOSTING_INTAKE_PASS")
    distinct_logins(loader, ("PG_USER", worker_login), ("PG_HOSTING_INTAKE_USER", hosting_login))
    consumer = _read_consumer(loader)
    deploy = _read_deploy(loader)
    site = _read_site(loader)
    if (
        loader.ok("SITE_PROGRESS_DEADLINE", "DEPLOY_READY_TIMEOUT")
        and site.progress_deadline >= deploy.ready_timeout
    ):
        loader.fail("SITE_PROGRESS_DEADLINE", "must be below DEPLOY_READY_TIMEOUT")
    entitlement, entitlement_file = read_entitlement_mode(loader, profile)
    hosting_entitlement = ""
    if not refuse_in_production(
        loader, profile, "HOSTING_ENTITLEMENT_MODE", STANDIN_PAYMENT_EVENTS
    ):
        hosting_entitlement = loader.choice(
            "HOSTING_ENTITLEMENT_MODE", (PROTOTYPE_PAYMENT_EVENTS, PAYMENT_GRPC)
        )
    payment_target = read_payment_target(loader, profile, entitlement, hosting_entitlement)
    command_deadline = loader.duration("COMMAND_DEADLINE", timedelta(seconds=1), timedelta(days=30))
    broker = read_broker(loader)
    check_store_hold(loader, "WORKER_STORE_HOLD_MAX", consumer.store_hold, broker)
    route = _read_route(loader)
    _check_timing(
        loader,
        command_deadline=command_deadline,
        consumer=consumer,
        deploy=deploy,
        route=route,
        broker_consumer_timeout=broker.consumer_timeout,
    )
    _check_probe(loader, site, route)
    return WorkerProcessConfig(
        profile=profile,
        log_level=read_log_level(loader),
        command_deadline=command_deadline,
        entitlement=entitlement,
        entitlement_file=entitlement_file,
        hosting_entitlement=hosting_entitlement,
        database=database,
        worker_login=worker_login,
        hosting_login=hosting_login,
        broker=broker,
        dispatch=_read_dispatch(loader),
        consumer=consumer,
        deploy=deploy,
        site=site,
        route=route,
        payment=PaymentSettings(
            concurrency=loader.integer("PAYMENT_CONSUMER_CONCURRENCY", 1, 64),
            retry_backoff=read_backoff(
                loader, "PAYMENT_RETRY_BACKOFF_BASE", "PAYMENT_RETRY_BACKOFF_MAX"
            ),
            max_retries=loader.integer("PAYMENT_MAX_RETRIES", 1, 1_000),
        ),
        cluster=read_cluster(loader, profile, "WORKER_KUBECONFIG"),
        reconcile=ReconcileSettings(
            interval=loader.optional_duration(
                "RECONCILE_INTERVAL",
                timedelta(seconds=30),
                timedelta(seconds=1),
                timedelta(hours=1),
            ),
            batch_size=loader.optional_integer("RECONCILE_BATCH", 50, 1, 1000),
        ),
        usage=UsageSettings(
            publish_interval=loader.optional_duration(
                "USAGE_PUBLISH_INTERVAL",
                timedelta(seconds=5),
                timedelta(milliseconds=10),
                timedelta(hours=1),
            ),
            batch_size=loader.optional_integer("USAGE_BATCH_SIZE", 50, 1, 1000),
        ),
        ops_listen_addr=read_ops_listen_addr(loader),
        sentry=read_sentry(loader),
        tracing=read_tracing(loader),
        payment_grpc_target=payment_target,
    )


def _check_timing(
    loader: Loader,
    *,
    command_deadline: timedelta,
    consumer: ConsumerSettings,
    deploy: DeploySettings,
    route: RouteSettings,
    broker_consumer_timeout: timedelta,
) -> None:
    """QA-06: refuse timing that no command can survive, each with a reason that names every
    key of the combination (never a value). A combination is checked only when each of its keys
    was read and accepted alone (``Loader.ok``), so a key is reported once.

    - ``COMMAND_DEADLINE`` above ``DEPLOY_FINISH_MARGIN``: else every deploy is refused
      ``deadline_exceeded`` before it starts.
    - ``WORKER_ATTEMPT_TIMEOUT`` above ``DEPLOY_READY_TIMEOUT``: else every slow site's attempt is
      cancelled before its readiness wait ends.
    - ``WORKER_ATTEMPT_TIMEOUT`` above the longest claim of a route command (TR-58): a go-live
      try that first puts the route back, then runs its preview and its switch (each up to
      ``ROUTE_SWITCH_DEADLINE``) and its restore (each restore up to
      ``ROUTE_RESTORE_DEADLINE``); this also covers an operator rollback's apply and reclaim.
    - ``COMMAND_DEADLINE`` above all go-live tries of one promote and their interruptions
      (TR-58; decision 0027): ``GO_LIVE_TRIES`` tries of twice ``ROUTE_SWITCH_DEADLINE`` plus
      ``ROUTE_RESTORE_DEADLINE``, the waits between them, and on each try an interruption of the
      longer of ``WORKER_LEASE_TTL`` and ``WORKER_RETRY_BACKOFF_MAX`` plus
      ``ROUTE_RESTORE_DEADLINE``. The API's ``COMMAND_DEADLINE`` must equal it; this process
      cannot check that (runbook ``production-profile.md``).
    - ``WORKER_MAX_ATTEMPTS`` above ``GO_LIVE_TRIES`` (TR-58): the claim after an interrupted
      last try must still reach the executor, which ends the promote (design G9).
    - ``WORKER_ATTEMPT_TIMEOUT`` below ``RABBITMQ_CONSUMER_TIMEOUT``, and
      ``WORKER_HEARTBEAT_INTERVAL`` at most a third of ``WORKER_LEASE_TTL`` (decision 0005
      section 7; the consumer refuses both at start, these name the keys).
    - ``DEPLOY_FINISH_MARGIN`` and ``DEPLOY_ABANDON_TIMEOUT`` at least ``DEPLOY_DELETE_TIMEOUT``:
      the margin and the abandon exist so that the candidate's delete fits.
    - ``ROUTE_POLL_INTERVAL`` below both route deadlines, ``DEPLOY_POLL_INTERVAL`` at most a third
      of ``DEPLOY_READY_TIMEOUT`` (the use cases refuse both at start)."""
    attempt = consumer.attempt_timeout
    if loader.ok("COMMAND_DEADLINE", "DEPLOY_FINISH_MARGIN") and (
        command_deadline <= deploy.finish_margin
    ):
        loader.fail("COMMAND_DEADLINE", "must be above DEPLOY_FINISH_MARGIN")
    if loader.ok("WORKER_ATTEMPT_TIMEOUT", "DEPLOY_READY_TIMEOUT") and (
        attempt <= deploy.ready_timeout
    ):
        loader.fail("WORKER_ATTEMPT_TIMEOUT", "must be above DEPLOY_READY_TIMEOUT")
    if loader.ok("WORKER_ATTEMPT_TIMEOUT", "ROUTE_SWITCH_DEADLINE", "ROUTE_RESTORE_DEADLINE") and (
        attempt <= 2 * route.switch_deadline + 2 * route.restore_deadline
    ):
        loader.fail(
            "WORKER_ATTEMPT_TIMEOUT",
            "must be above the longest claim of a route command: twice ROUTE_SWITCH_DEADLINE "
            "plus twice ROUTE_RESTORE_DEADLINE",
        )
    command_keys = (
        "COMMAND_DEADLINE",
        "ROUTE_SWITCH_DEADLINE",
        "ROUTE_RESTORE_DEADLINE",
        "WORKER_LEASE_TTL",
        "WORKER_RETRY_BACKOFF_MAX",
    )
    if loader.ok(*command_keys) and command_deadline <= _go_live_bound(consumer, route):
        loader.fail(
            "COMMAND_DEADLINE",
            "must be above the go-live tries of one promote: each try's twice "
            "ROUTE_SWITCH_DEADLINE plus ROUTE_RESTORE_DEADLINE, the waits between tries, and on "
            "each try the longer of WORKER_LEASE_TTL and WORKER_RETRY_BACKOFF_MAX plus "
            "ROUTE_RESTORE_DEADLINE",
        )
    if loader.ok("WORKER_MAX_ATTEMPTS") and consumer.max_attempts <= GO_LIVE_TRIES:
        loader.fail(
            "WORKER_MAX_ATTEMPTS",
            "must be above the go-live tries of one promote, so the claim after an interrupted "
            "last try still ends it",
        )
    if loader.ok("WORKER_ATTEMPT_TIMEOUT", "RABBITMQ_CONSUMER_TIMEOUT") and (
        attempt >= broker_consumer_timeout
    ):
        loader.fail("WORKER_ATTEMPT_TIMEOUT", "must be below RABBITMQ_CONSUMER_TIMEOUT")
    if loader.ok("WORKER_HEARTBEAT_INTERVAL", "WORKER_LEASE_TTL") and (
        consumer.heartbeat_interval * 3 > consumer.lease_ttl
    ):
        loader.fail("WORKER_HEARTBEAT_INTERVAL", "must be at most a third of WORKER_LEASE_TTL")
    for key, value in (
        ("DEPLOY_FINISH_MARGIN", deploy.finish_margin),
        ("DEPLOY_ABANDON_TIMEOUT", deploy.abandon_timeout),
    ):
        if loader.ok(key, "DEPLOY_DELETE_TIMEOUT") and value < deploy.delete_timeout:
            loader.fail(key, "must be at least DEPLOY_DELETE_TIMEOUT")
    if loader.ok("ROUTE_POLL_INTERVAL", "ROUTE_SWITCH_DEADLINE", "ROUTE_RESTORE_DEADLINE") and (
        route.poll_interval >= min(route.switch_deadline, route.restore_deadline)
    ):
        loader.fail(
            "ROUTE_POLL_INTERVAL", "must be below ROUTE_SWITCH_DEADLINE and ROUTE_RESTORE_DEADLINE"
        )
    if loader.ok("DEPLOY_POLL_INTERVAL", "DEPLOY_READY_TIMEOUT") and (
        deploy.poll_interval * 3 > deploy.ready_timeout
    ):
        loader.fail("DEPLOY_POLL_INTERVAL", "must be at most a third of DEPLOY_READY_TIMEOUT")


def _go_live_bound(consumer: ConsumerSettings, route: RouteSettings) -> timedelta:
    """The longest one promote command can take (TR-58; design G12): ``GO_LIVE_TRIES`` tries of
    up to twice the switch deadline plus a restore, the waits between tries, and on each try an
    interruption (a lapsed claim or a released one, then the next claim's restore)."""
    one_try = 2 * route.switch_deadline + route.restore_deadline
    interruption = max(consumer.lease_ttl, consumer.retry_backoff.max) + route.restore_deadline
    waits = timedelta(0)
    for try_number in range(1, GO_LIVE_TRIES):
        wait = wait_after_failed_try(try_number)
        if wait is not None:
            waits += wait
    return GO_LIVE_TRIES * one_try + waits + GO_LIVE_TRIES * interruption


def _check_probe(loader: Loader, site: SiteSettingsConfig, route: RouteSettings) -> None:
    """QA-06 style, for the probe of QA-08: a steady site needs one passing run and
    ``PROBE_CONFIRMATIONS`` more, each of ``SITE_PROBE_SUCCESSES`` probes ``SITE_PROBE_GAP``
    apart. When that alone does not fit in ``ROUTE_SWITCH_DEADLINE`` no promotion can pass."""
    keys = ("SITE_PROBE_GAP", "SITE_PROBE_SUCCESSES", "ROUTE_SWITCH_DEADLINE")
    runs = 1 + PROBE_CONFIRMATIONS
    if loader.ok(*keys) and (
        runs * (site.probe_successes - 1) * site.probe_gap >= route.switch_deadline
    ):
        loader.fail(
            "SITE_PROBE_GAP",
            f"times SITE_PROBE_SUCCESSES minus one, over {runs} probe runs, must be below "
            "ROUTE_SWITCH_DEADLINE",
        )


def _read_dispatch(loader: Loader) -> DispatchSettings:
    hour = timedelta(hours=1)
    return DispatchSettings(
        poll_interval=loader.duration("DISPATCH_POLL_INTERVAL", timedelta(milliseconds=10), hour),
        batch_size=loader.integer("DISPATCH_BATCH_SIZE", 1, 1000),
        publish_concurrency=loader.integer("DISPATCH_PUBLISH_CONCURRENCY", 1, 1000),
        confirm_timeout=loader.duration(
            "DISPATCH_PUBLISH_CONFIRM_TIMEOUT", timedelta(milliseconds=10), hour
        ),
        visibility_timeout=loader.duration(
            "DISPATCH_VISIBILITY_TIMEOUT", timedelta(milliseconds=10), hour
        ),
        redelivery_interval=loader.duration(
            "DISPATCH_REDELIVERY_INTERVAL", timedelta(milliseconds=10), hour
        ),
        backoff=read_backoff(loader, "DISPATCH_BACKOFF_BASE", "DISPATCH_BACKOFF_MAX"),
    )


def _read_consumer(loader: Loader) -> ConsumerSettings:
    hour = timedelta(hours=1)
    return ConsumerSettings(
        worker_id=loader.text("WORKER_ID", plain_text(128)),
        concurrency=loader.integer("WORKER_CONCURRENCY", 1, 256),
        lease_ttl=loader.duration("WORKER_LEASE_TTL", timedelta(seconds=1), hour),
        heartbeat_interval=loader.duration(
            "WORKER_HEARTBEAT_INTERVAL", timedelta(milliseconds=10), hour
        ),
        attempt_timeout=loader.duration("WORKER_ATTEMPT_TIMEOUT", timedelta(seconds=1), hour),
        max_attempts=loader.integer("WORKER_MAX_ATTEMPTS", 1, 1000),
        retry_backoff=read_backoff(loader, "WORKER_RETRY_BACKOFF_BASE", "WORKER_RETRY_BACKOFF_MAX"),
        store_hold=read_backoff(loader, "WORKER_STORE_HOLD_BASE", "WORKER_STORE_HOLD_MAX"),
        store_failure_limit=loader.integer("WORKER_STORE_FAILURE_LIMIT", 1, 1000),
    )


def _read_deploy(loader: Loader) -> DeploySettings:
    hour = timedelta(hours=1)
    ms = timedelta(milliseconds=10)
    return DeploySettings(
        image_registry=loader.text("DEPLOY_IMAGE_REGISTRY", registry_location),
        ready_timeout=loader.duration("DEPLOY_READY_TIMEOUT", timedelta(seconds=2), hour),
        poll_interval=loader.duration("DEPLOY_POLL_INTERVAL", ms, hour),
        call_timeout=loader.duration("DEPLOY_CALL_TIMEOUT", ms, hour),
        delete_timeout=loader.duration("DEPLOY_DELETE_TIMEOUT", ms, hour),
        finish_margin=loader.duration("DEPLOY_FINISH_MARGIN", ms, hour),
        abandon_timeout=loader.duration("DEPLOY_ABANDON_TIMEOUT", ms, hour),
    )


def _read_site(loader: Loader) -> SiteSettingsConfig:
    return SiteSettingsConfig(
        cpu_millicores=loader.integer("SITE_CPU_MILLICORES", 1, 1_000_000),
        memory_mib=loader.integer("SITE_MEMORY_MIB", 1, 1_000_000),
        progress_deadline=loader.duration(
            "SITE_PROGRESS_DEADLINE", timedelta(seconds=1), timedelta(hours=1)
        ),
        offline_service_port=loader.integer("SITE_OFFLINE_SERVICE_PORT", 1, 65535),
        domain=loader.text("SITE_DOMAIN", _domain),
        gateway_url=loader.text("SITE_GATEWAY_HTTPS_URL", _gateway_url),
        gateway_ca_file=Path(loader.text("SITE_GATEWAY_CA_FILE", absolute_path)),
        probe_path=loader.text("SITE_PROBE_PATH", _probe_path),
        probe_timeout=loader.duration(
            "SITE_PROBE_TIMEOUT", timedelta(milliseconds=10), timedelta(minutes=5)
        ),
        delete_retry_base=loader.duration(
            "SITE_DELETE_RETRY_BASE", timedelta(milliseconds=1), timedelta(minutes=1)
        ),
        preview_token=loader.secret("PREVIEW_PROBE_TOKEN", _preview_token),
        probe_successes=loader.optional_integer("SITE_PROBE_SUCCESSES", 1, 1, 10),
        probe_max_body_bytes=loader.optional_integer(
            "SITE_PROBE_MAX_BODY_BYTES", 65536, 1024, 1 << 20
        ),
        probe_gap=loader.optional_duration(
            "SITE_PROBE_GAP", timedelta(milliseconds=500), timedelta(0), timedelta(seconds=10)
        ),
    )


def _read_route(loader: Loader) -> RouteSettings:
    hour = timedelta(hours=1)
    ms = timedelta(milliseconds=10)
    return RouteSettings(
        switch_deadline=loader.duration("ROUTE_SWITCH_DEADLINE", ms, hour),
        restore_deadline=loader.duration("ROUTE_RESTORE_DEADLINE", ms, hour),
        poll_interval=loader.duration("ROUTE_POLL_INTERVAL", ms, hour),
    )


# Security review S5: ``<63 characters>-preview.<site domain>``, for the longest sub-domain the
# database allows, is at most 253 characters (FR-20, FR-55).
MAX_SITE_DOMAIN_LEN = 181


def _domain(value: str) -> str | None:
    labels = value.split(".")
    if (
        len(value) > MAX_SITE_DOMAIN_LEN
        or len(labels) < 2
        or not all(
            0 < len(x) <= 63
            and x.isascii()
            and x.replace("-", "").isalnum()
            and x == x.lower()
            and not x.startswith("-")
            and not x.endswith("-")
            for x in labels
        )
    ):
        return (
            "must be a lowercase DNS name of at least two labels and at most 181 characters, "
            "without a wildcard"
        )
    return None


def _gateway_url(value: str) -> str | None:
    if not http_url(value, schemes=("https",)):
        return "must be an https URL with a host, without credentials, query or fragment"
    return None


_PREVIEW_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}")


def _preview_token(value: str) -> str | None:
    """The preview route's probe token (recheck N1): 32 to 256 URL-safe characters."""
    if not _PREVIEW_TOKEN.fullmatch(value):
        return "must be 32 to 256 URL-safe characters (A-Z, a-z, 0-9, - and _)"
    return None


def _probe_path(value: str) -> str | None:
    """The promotion probe's path (decision 0014 section 15), such as /readyz."""
    segments = value.split("/")
    if (
        len(value) > 256
        or not value.startswith("/")
        or any(
            not x
            or x in (".", "..")
            or not all(c.isascii() and (c.isalnum() or c in "._~-") for c in x)
            for x in segments[1:]
        )
    ):
        return "must be an absolute path of plain segments, such as /readyz"
    return None
