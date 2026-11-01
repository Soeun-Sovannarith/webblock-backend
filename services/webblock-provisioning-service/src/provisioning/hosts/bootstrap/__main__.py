"""The bootstrap process (task G; decisions 0013 sections 3 and 9 K1, 0014 sections 3 and 10.1):
it reads, as a login of ``provisioning_bootstrap`` alone (read-only), the projects that need a
tenant (a release on its way to a deploy, or a live site), and creates each one's tenant with its
own cluster identity, the bootstrap kubeconfig. Once the tenant is ready it runs H's
``access_setup`` (decision 0014 section 14): the site's catalog login role and the Secret
``catalog-runtime`` its pods read, written before the password. Every tenant needs it before its
first deploy. The release worker never holds this identity.

The bootstrap is a singleton (task H5c): its rounds run only while it holds a PostgreSQL
session-level advisory lock (``hosts.bootstrap.singleton``). A second bootstrap stands by, logs
that it does, and takes over when the first one's session ends; a bootstrap that loses the lock
stops its rounds at once and exits 1. A check of the lock that stalls (the database paused or
failing over) only pauses the rounds until a check on the same session confirms the lock (task
HF-A); a stall longer than ``LOCK_STALL_BOUND`` (60 s) is a loss.

Every setting comes from the environment and is required. With a missing or invalid setting the
process writes the name and the reason of every refused key, never a value, and exits 2. A
kubeconfig that is not the project's local Kind cluster, a pod whose token is not the expected
ServiceAccount's, or any other start-up failure, exits 1.
SIGINT or SIGTERM stops it; a second signal ends it at once.

    python -m provisioning.hosts.bootstrap
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from collections.abc import Mapping
from datetime import timedelta
from typing import Final
from uuid import UUID

from provisioning.core.application import AuditLog
from provisioning.core.application.tenants.background.bootstrap_loop import LoopSettings, TenantLoop
from provisioning.hosts.api.signals import install_stop_signals
from provisioning.hosts.bootstrap.singleton import (
    LOCK_CHECK,
    LOCK_POLL,
    LOCK_STALL_BOUND,
    STANDBY_LOG_INTERVAL,
    Singleton,
)
from provisioning.hosts.bootstrap.tenants import AccessSetupHook
from provisioning.hosts.shared.cluster import connect_cluster
from provisioning.hosts.shared.observability import Observability
from provisioning.hosts.shared.ops_listener import OpsListener, exposition_timeout
from provisioning.hosts.shared.process import run_process
from provisioning.hosts.shared.runtime import (
    EXIT_FAILURE,
    EXIT_OK,
    LoopEnded,
    SystemClock,
    open_role_engine,
    run_until_stopped,
    settings_refused,
    timeouts_of,
)
from provisioning.infrastructure.catalog import (
    AdminLogin,
    CatalogAccessSetup,
    CatalogConnection,
    PostgresSiteAccess,
    RuntimeEndpoint,
    RuntimeSecretSink,
)
from provisioning.infrastructure.config.bootstrap import BootstrapConfig, load_bootstrap
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    KubeconfigRefused,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import TenantSettings
from provisioning.infrastructure.postgres.session_lock import BOOTSTRAP_LOCK_KEY, PgSessionLock
from provisioning.infrastructure.postgres.tenant_projects import PgTenantProjects
from provisioning.infrastructure.telemetry import Logger

PROCESS_LABEL: Final = "provisioning-bootstrap"
PROFILE: Final = "prototype"  # the bootstrap process runs only in the prototype (task H2)


def main() -> int:
    return run_process(PROCESS_LABEL, load_bootstrap, _start, profile_of=lambda _config: PROFILE)


def _start(
    config: BootstrapConfig, logger: Logger, audit: AuditLog, observability: Observability
) -> int:
    return asyncio.run(_run(config, logger, audit, observability))


def loop_settings(config: BootstrapConfig) -> LoopSettings:
    with settings_refused():
        settings = LoopSettings(
            interval=config.loop.interval,
            batch_size=config.loop.batch_size,
            concurrency=config.loop.concurrency,
            call_timeout=config.loop.call_timeout,
            recheck_rounds=config.loop.recheck_rounds,
        )
        settings.validate()
    return settings


class _NoSecrets:
    """Stands in for the Secret sink while the settings alone are checked; never called."""

    async def runtime_secret_exists(self, project_id: UUID) -> bool:
        raise RuntimeError("not connected")

    async def write_runtime_secret(self, project_id: UUID, values: Mapping[str, str]) -> None:
        raise RuntimeError("not connected")


def catalog_access(config: BootstrapConfig, secrets: RuntimeSecretSink) -> CatalogAccessSetup:
    """H's access_setup over the catalog as the access administrator; ``InvalidSettings`` when
    its settings are refused (never with a value)."""
    c = config.catalog
    with settings_refused():
        return CatalogAccessSetup(
            site_access=PostgresSiteAccess(
                CatalogConnection(
                    host=c.host,
                    port=c.port,
                    database=c.database,
                    sslmode=c.ssl_mode,
                    connect_timeout_seconds=int(c.connect_timeout.total_seconds()),
                    statement_timeout_ms=c.statement_timeout // timedelta(milliseconds=1),
                    lock_timeout_ms=c.lock_timeout // timedelta(milliseconds=1),
                ),
                AdminLogin(user=c.admin.user, password=c.admin.password.reveal()),
            ),
            secrets=secrets,
            endpoint=RuntimeEndpoint(
                host=c.runtime_host, port=c.runtime_port, database=c.database, sslmode=c.ssl_mode
            ),
        )


async def _run(
    config: BootstrapConfig, logger: Logger, audit: AuditLog, observability: Observability
) -> int:
    stop = asyncio.Event()
    install_stop_signals(asyncio.get_running_loop(), stop)
    settings = loop_settings(config)
    timeouts = timeouts_of(config.database)
    catalog_access(config, _NoSecrets())  # refuse invalid catalog settings before connecting
    log = logging.getLogger("provisioning.bootstrap")
    metrics = observability.metrics
    t = config.tenant
    try:
        async with contextlib.AsyncExitStack() as stack:
            engine = open_role_engine(config.database, config.reader_login)
            stack.push_async_callback(engine.dispose)
            kube = await connect_cluster(config.cluster, field_manager=BOOTSTRAP_FIELD_MANAGER)
            stack.push_async_callback(kube.close)
            projects = PgTenantProjects(engine, timeouts)
            tenants = KubernetesTenantBootstrap(
                kube,
                TenantSettings(
                    quota_cpu=t.quota_cpu,
                    quota_memory=t.quota_memory,
                    quota_pods=t.quota_pods,
                    container_max_cpu=t.container_max_cpu,
                    container_max_memory=t.container_max_memory,
                ),
            )
            access = catalog_access(config, tenants)
            # The namespace's catalog-secret mark tells a tenant created from nothing (task F2B).
            access_setup = AccessSetupHook(
                access=access,
                hosting=projects,
                marks=tenants,
                audit=audit,
                clock=SystemClock(),
                logger=log,
            )
            loop = TenantLoop(
                settings,
                projects=projects,
                bootstrap=tenants,
                logger=log,
                after_ready=access_setup,
                on_round=lambda not_ready: metrics.bootstrap_round(not_ready=not_ready),
            )
            # Task H5c: the rounds run only while this process holds the singleton lock, on a
            # connection of an engine of its own (the rounds' pool never loses one to it).
            lock_engine = open_role_engine(config.database, config.reader_login)
            stack.push_async_callback(lock_engine.dispose)
            singleton = Singleton(
                PgSessionLock(
                    lock_engine,
                    timeouts,
                    key=BOOTSTRAP_LOCK_KEY,
                    poll=LOCK_POLL,
                    check=LOCK_CHECK,
                    stall=max(LOCK_STALL_BOUND, timeouts.statement),
                ),
                loop,
                logger=log,
                standby_log_interval=STANDBY_LOG_INTERVAL,
                on_standby=lambda standby: metrics.bootstrap_standby(standby=standby),
            )
            ops = OpsListener(
                config.ops_listen_addr,
                metrics,
                timeout=exposition_timeout(config.database.tx_timeout),
            )
            log.info("bootstrap running")
            await run_until_stopped(stop, first=(ops.run,), then=(singleton.run,), logger=log)
    except KubeconfigRefused as err:
        logger.error("bootstrap could not be built", error=str(err))
        return EXIT_FAILURE
    except LoopEnded as err:
        logger.error("bootstrap stopped with an error", error=f"loop ended: {err}")
        return EXIT_FAILURE
    except Exception as err:
        logger.error("bootstrap stopped with an error", error=type(err).__name__)
        return EXIT_FAILURE
    log.info("bootstrap stopped")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
