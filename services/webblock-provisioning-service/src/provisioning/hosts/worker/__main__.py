"""The release worker process (task G; decisions 0005, 0013 section 3, 0014 section 3): the
dispatcher, the command consumer with every executor, the reconciler (0014 section 21), and the
Payment-event consumer, with the worker's own cluster identity. Audit events and the
uncertain-route alert go to the structured log (``LoggerAuditLog``).

Every setting comes from the environment and is required. With a missing or invalid setting the
process writes the name and the reason of every refused key, never a value, and exits 2. A
kubeconfig that is not the project's local Kind cluster, a pod whose token is not the expected
ServiceAccount's, or any other start-up failure, exits 1.
SIGINT or SIGTERM stops it: the consumers release their claims first; a second signal ends it at
once.

    python -m provisioning.hosts.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import sys
from typing import Final

import httpx2

from provisioning.core.application import AuditLog
from provisioning.hosts.api.signals import install_stop_signals
from provisioning.hosts.shared.cluster import connect_cluster
from provisioning.hosts.shared.observability import Observability
from provisioning.hosts.shared.ops_listener import OpsListener, exposition_timeout
from provisioning.hosts.shared.process import run_process
from provisioning.hosts.shared.runtime import (
    EXIT_FAILURE,
    EXIT_OK,
    InvalidSettings,
    LoopEnded,
    open_role_engine,
    timeouts_of,
)
from provisioning.hosts.worker.wiring import build_worker, site_cluster_settings
from provisioning.infrastructure.config.worker import WorkerProcessConfig, load_worker
from provisioning.infrastructure.grpc.call import open_channel
from provisioning.infrastructure.kubernetes import (
    WORKER_FIELD_MANAGER,
    KubeconfigRefused,
)
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster
from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement
from provisioning.infrastructure.telemetry import Logger
from provisioning.infrastructure.telemetry.samples import add_dispatch_sample

PROCESS_LABEL: Final = "provisioning-worker"


def main() -> int:
    return run_process(
        PROCESS_LABEL, load_worker, _start, profile_of=lambda config: config.profile.value
    )


def _start(
    config: WorkerProcessConfig, logger: Logger, audit: AuditLog, observability: Observability
) -> int:
    return asyncio.run(_run(config, logger, audit, observability))


async def _run(
    config: WorkerProcessConfig, logger: Logger, audit: AuditLog, observability: Observability
) -> int:
    stop = asyncio.Event()
    install_stop_signals(asyncio.get_running_loop(), stop)
    settings = site_cluster_settings(config)
    try:
        async with contextlib.AsyncExitStack() as stack:
            worker_engine = open_role_engine(config.database, config.worker_login)
            stack.push_async_callback(worker_engine.dispose)
            hosting_engine = open_role_engine(config.database, config.hosting_login)
            stack.push_async_callback(hosting_engine.dispose)
            kube = await connect_cluster(config.cluster, field_manager=WORKER_FIELD_MANAGER)
            stack.push_async_callback(kube.close)
            # The probe asks the Gateway's HTTPS listeners with the site's hostname as the TLS
            # server name, verified against the configured CA, one connection per probe.
            tls = ssl.create_default_context(cafile=str(config.site.gateway_ca_file))
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    verify=tls,
                    limits=httpx2.Limits(max_keepalive_connections=0),
                )
            )
            payment: GrpcEntitlement | None = None
            if config.payment_grpc_target is not None:
                # Lazy channel: it connects on the first call; closed with the stack.
                channel = open_channel(config.payment_grpc_target)
                stack.push_async_callback(channel.close)
                payment = GrpcEntitlement(channel, logger)
            worker = build_worker(
                config,
                worker_engine=worker_engine,
                hosting_engine=hosting_engine,
                cluster=KubernetesSiteCluster(kube, settings, http),
                logger=logger,
                audit=audit,
                metrics=observability.metrics,
                payment=payment,
            )
            metrics = observability.metrics
            add_dispatch_sample(metrics, worker_engine, timeouts_of(config.database))
            ops = OpsListener(
                config.ops_listen_addr,
                metrics,
                timeout=exposition_timeout(config.database.tx_timeout),
            )
            await worker.serve(stop, also=(ops.run,))
    except KubeconfigRefused as err:
        logger.error("worker could not be built", error=str(err))
        return EXIT_FAILURE
    except OSError, ssl.SSLError:
        # The CA file is missing or not PEM; the operating system's text may quote the path.
        logger.error("worker could not be built", error="the gateway CA file cannot be read")
        return EXIT_FAILURE
    except LoopEnded as err:
        logger.error("worker stopped with an error", error=f"loop ended: {err}")
        return EXIT_FAILURE
    except InvalidSettings:
        raise
    except Exception as err:
        logger.error("worker stopped with an error", error=type(err).__name__)
        return EXIT_FAILURE
    logging.getLogger("provisioning.worker").info("worker stopped")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
