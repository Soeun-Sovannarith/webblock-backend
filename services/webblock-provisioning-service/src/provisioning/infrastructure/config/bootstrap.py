"""The bootstrap process's configuration (task G; decisions 0013 sections 3 and 9 K1, 0014
sections 3 and 10.1).

The process finds projects that need a tenant through a login that is a member of
``provisioning_bootstrap`` alone, a read-only role (PG_USER), and creates each tenant with its own
cluster identity (BOOTSTRAP_KUBECONFIG on the host, the projected token of ServiceAccount
tenant-bootstrap in a pod; decision 0016 section 9 ruling 2). It never reads the worker's.

Task H2: PROVISIONING_PROFILE is required, and ``production`` is refused: every tenant the process
creates ends with access_setup, the site's catalog login and Secret, whose production contract
with the catalog owner is open (OD-46: the catalog row and the access administrator credential).
The process therefore does not start in production until that contract is decided.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from provisioning.core.application.shared.profile import OD_CATALOG, Profile
from provisioning.infrastructure.config.api import read_log_level
from provisioning.infrastructure.config.common import ClusterSettings, read_cluster
from provisioning.infrastructure.config.database import (
    DatabaseConfig,
    DatabaseLogin,
    read_database,
    read_login,
)
from provisioning.infrastructure.config.loader import (
    Loader,
    Lookup,
    load,
    plain_text,
)
from provisioning.infrastructure.config.profile import PROFILE_KEY, read_profile
from provisioning.infrastructure.config.telemetry import (
    SentryConfig,
    TracingConfig,
    read_ops_listen_addr,
    read_sentry,
    read_tracing,
)

NO_PRODUCTION_BOOTSTRAP: Final = (
    "production is refused: access_setup (the site's catalog login and Secret) has no "
    f"production contract with the catalog owner ({OD_CATALOG})"
)


@dataclass(frozen=True, slots=True)
class TenantConfig:
    """TENANT_* (D's ``TenantSettings``): the namespace quota and the container limits, as
    Kubernetes quantities (OD-33)."""

    quota_cpu: str
    quota_memory: str
    quota_pods: int
    container_max_cpu: str
    container_max_memory: str


@dataclass(frozen=True, slots=True)
class BootstrapLoop:
    """BOOTSTRAP_*: the pause between rounds, the projects read per round, the tenants ensured
    at once, the bound of one tenant's ensure, and after how many rounds a ready tenant is
    ensured again (BOOTSTRAP_RECHECK_ROUNDS, optional, 10; QA-04)."""

    interval: timedelta
    batch_size: int
    concurrency: int
    call_timeout: timedelta
    recheck_rounds: int


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    """CATALOG_*: how access_setup reaches the shared catalog database as the access
    administrator login (decision 0014 section 14; H's ``CatalogConnection`` and ``AdminLogin``),
    and where the site's pods reach it (``RuntimeEndpoint``, written into the Secret)."""

    host: str
    port: int
    database: str
    ssl_mode: str
    admin: DatabaseLogin
    connect_timeout: timedelta
    statement_timeout: timedelta
    lock_timeout: timedelta
    runtime_host: str
    runtime_port: int


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    """Everything the bootstrap process reads from its environment."""

    log_level: int
    database: DatabaseConfig
    reader_login: DatabaseLogin
    cluster: ClusterSettings
    tenant: TenantConfig
    loop: BootstrapLoop
    catalog: CatalogConfig
    ops_listen_addr: str  # OPS_LISTEN_ADDR: /metrics and /healthz (task H4)
    sentry: SentryConfig | None  # None: SENTRY_DSN=none (task H4)
    tracing: TracingConfig | None = None  # None: OTEL_EXPORTER_OTLP_ENDPOINT unset or none


def load_bootstrap(lookup: Lookup) -> BootstrapConfig:
    """Read and check the bootstrap configuration; ``LoadError`` names every refused key."""
    return load(lookup, read_bootstrap)


def read_bootstrap(loader: Loader) -> BootstrapConfig:
    """The bootstrap process's section reader."""
    profile = read_profile(loader)
    if profile is Profile.PRODUCTION:
        loader.fail(PROFILE_KEY, NO_PRODUCTION_BOOTSTRAP)
    database = read_database(loader)
    loop = BootstrapLoop(
        interval=loader.duration(
            "BOOTSTRAP_INTERVAL", timedelta(milliseconds=100), timedelta(hours=1)
        ),
        batch_size=loader.integer("BOOTSTRAP_BATCH_SIZE", 1, 1000),
        concurrency=loader.integer("BOOTSTRAP_CONCURRENCY", 1, 64),
        call_timeout=loader.duration(
            "BOOTSTRAP_CALL_TIMEOUT", timedelta(milliseconds=100), timedelta(minutes=10)
        ),
        recheck_rounds=loader.optional_integer("BOOTSTRAP_RECHECK_ROUNDS", 10, 1, 10_000),
    )
    if (
        loader.ok("BOOTSTRAP_CONCURRENCY", "BOOTSTRAP_BATCH_SIZE")
        and loop.concurrency > loop.batch_size
    ):
        loader.fail("BOOTSTRAP_CONCURRENCY", "must not exceed BOOTSTRAP_BATCH_SIZE")
    return BootstrapConfig(
        log_level=read_log_level(loader),
        database=database,
        reader_login=read_login(loader, "PG_USER", "PG_PASS"),
        cluster=read_cluster(loader, profile, "BOOTSTRAP_KUBECONFIG"),
        tenant=TenantConfig(
            quota_cpu=loader.text("TENANT_QUOTA_CPU", _quantity),
            quota_memory=loader.text("TENANT_QUOTA_MEMORY", _quantity),
            quota_pods=loader.integer("TENANT_QUOTA_PODS", 1, 1000),
            container_max_cpu=loader.text("TENANT_CONTAINER_MAX_CPU", _quantity),
            container_max_memory=loader.text("TENANT_CONTAINER_MAX_MEMORY", _quantity),
        ),
        loop=loop,
        catalog=_read_catalog(loader),
        ops_listen_addr=read_ops_listen_addr(loader),
        sentry=read_sentry(loader),
        tracing=read_tracing(loader),
    )


def _read_catalog(loader: Loader) -> CatalogConfig:
    host_check = plain_text(253)
    return CatalogConfig(
        host=loader.text("CATALOG_HOST", host_check),
        port=loader.integer("CATALOG_PORT", 1, 65535),
        database=loader.text("CATALOG_DATABASE", plain_text(63)),
        ssl_mode=loader.choice(
            "CATALOG_SSLMODE", ("disable", "require", "verify-ca", "verify-full")
        ),
        admin=read_login(loader, "CATALOG_ADMIN_USER", "CATALOG_ADMIN_PASS"),
        connect_timeout=loader.duration(
            "CATALOG_CONNECT_TIMEOUT", timedelta(seconds=1), timedelta(seconds=60)
        ),
        statement_timeout=loader.duration(
            "CATALOG_STATEMENT_TIMEOUT", timedelta(milliseconds=1), timedelta(seconds=60)
        ),
        lock_timeout=loader.duration(
            "CATALOG_LOCK_TIMEOUT", timedelta(milliseconds=100), timedelta(seconds=60)
        ),
        runtime_host=loader.text("CATALOG_RUNTIME_HOST", host_check),
        runtime_port=loader.integer("CATALOG_RUNTIME_PORT", 1, 65535),
    )


def _quantity(value: str) -> str | None:
    """A Kubernetes quantity such as 2, 500m or 1Gi: digits, an optional fraction and an optional
    suffix."""
    reason = plain_text(32)(value)
    if reason is not None:
        return reason
    number = value.rstrip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    suffix = value[len(number) :]
    whole, dot, fraction = number.partition(".")
    if (
        not whole.isdigit()
        or (dot and not fraction.isdigit())
        or suffix not in _SUFFIXES
        or not whole.isascii()
    ):
        return "must be a Kubernetes quantity such as 2, 500m or 1Gi"
    return None


_SUFFIXES = frozenset({"", "m", "k", "M", "G", "T", "Ki", "Mi", "Gi", "Ti"})
