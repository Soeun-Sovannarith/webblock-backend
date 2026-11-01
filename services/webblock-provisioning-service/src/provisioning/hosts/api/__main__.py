"""The Provisioning API process: the REST operations under /v1/ and
the MCP endpoint at /mcp on the public listener, /healthz and /readyz on the operational listener.
It holds no cluster credential and no broker credential (decision 0006 section 7).

Every setting comes from the environment and is required (``infrastructure.config``), including
PROVISIONING_PROFILE (task H2). Production accepts only IDENTITY_MODE=jwt, web-block-commons' token
check (decision 0024); the key set is fetched on the first request, so the process starts with the
issuer down. The library's log records go through the process's logger (G8). With a missing or
invalid setting the process writes the name and the reason of every refused key, never a value, and
exits 2. Any other start-up failure is logged and exits 1. SIGINT or SIGTERM starts a graceful
shutdown within HTTP_SHUTDOWN_GRACE; a second signal ends the process at once.

    python -m provisioning.hosts.api
"""

from __future__ import annotations

import asyncio
import sys
from typing import Final

from provisioning.core.application import AuditLog
from provisioning.hosts.api.signals import install_stop_signals
from provisioning.hosts.api.wiring import (
    APIIdentity,
    ListenerFailed,
    build_api,
    payment_entitlement,
    postgres_stores,
    project_access,
    read_identity,
)
from provisioning.hosts.shared.observability import Observability
from provisioning.hosts.shared.process import run_process
from provisioning.hosts.shared.runtime import EXIT_CONFIG, EXIT_FAILURE, EXIT_OK, InvalidSettings
from provisioning.infrastructure.config import APIConfig, load_api
from provisioning.infrastructure.config.files import ConfigFileError
from provisioning.infrastructure.identity import attach_library_logs
from provisioning.infrastructure.telemetry import Logger
from provisioning.presentation.http.middleware import route_templates

PROCESS_LABEL: Final = "provisioning-api"


def main() -> int:
    return run_process(
        PROCESS_LABEL,
        load_api,
        _start,
        profile_of=lambda config: config.profile.value,
        routes=route_templates(),
    )


def _start(config: APIConfig, logger: Logger, audit: AuditLog, observability: Observability) -> int:
    try:
        identity = read_identity(config, logger)
    except ConfigFileError as err:
        logger.error("api could not be built", error=str(err))
        return EXIT_FAILURE
    except InvalidSettings as err:
        logger.error("api could not be built", error=str(err))
        return EXIT_CONFIG
    # web-block-commons' token refusals and key set failures as the identity's log events and
    # metrics (decision 0024 G8), for the life of the process.
    attach_library_logs(logger)
    return asyncio.run(_run(config, identity, logger, audit, observability))


async def _run(
    config: APIConfig,
    identity: APIIdentity,
    logger: Logger,
    audit: AuditLog,
    observability: Observability,
) -> int:
    stop = asyncio.Event()
    install_stop_signals(asyncio.get_running_loop(), stop)
    try:
        async with (
            postgres_stores(config) as stores,
            project_access(config, logger) as access,
            payment_entitlement(config, logger) as entitlement,
        ):
            api = build_api(
                config,
                stores=stores,
                identity=identity,
                logger=logger,
                audit=audit,
                metrics=observability.metrics,
                access=access,
                entitlement=entitlement,
                tracer_provider=observability.tracer_provider,
            )
            await api.serve(stop)
    except InvalidSettings as err:
        logger.error("api could not be built", error=str(err))
        return EXIT_CONFIG
    except ListenerFailed as err:
        logger.error("api stopped with an error", error=str(err))
        return EXIT_FAILURE
    except Exception as err:
        logger.error("api stopped with an error", error=err)
        return EXIT_FAILURE
    logger.info("api stopped")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
