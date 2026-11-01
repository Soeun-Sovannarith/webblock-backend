"""The build worker process (task G; decisions 0008 and 0014 section 3): Project's "source
published" events into the inbox, release intake, builds on Jenkins, and their polling. It holds no
cluster credential.

Every setting comes from the environment and is required. With a missing or invalid setting the
process writes the name and the reason of every refused key, never a value, and exits 2. A broken
repository file or any other start-up failure exits 1. SIGINT or SIGTERM stops it; a second signal
ends it at once.

    python -m provisioning.hosts.build_worker
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import Final

from provisioning.core.application import AuditLog
from provisioning.core.application.shared.profile import Profile
from provisioning.hosts.api.signals import install_stop_signals
from provisioning.hosts.build_worker.wiring import build_build_worker, jenkins_build_system
from provisioning.hosts.shared import stand_ins
from provisioning.hosts.shared.observability import Observability
from provisioning.hosts.shared.open_contracts import ClosedRepositories
from provisioning.hosts.shared.ops_listener import OpsListener, exposition_timeout
from provisioning.hosts.shared.process import run_process
from provisioning.hosts.shared.runtime import (
    EXIT_FAILURE,
    EXIT_OK,
    InvalidSettings,
    LoopEnded,
    open_role_engine,
    settings_refused,
    timeouts_of,
)
from provisioning.infrastructure.config.build_worker import (
    PROJECT_GRPC,
    BuildWorkerConfig,
    load_build_worker,
)
from provisioning.infrastructure.config.files import ConfigFileError
from provisioning.infrastructure.grpc.call import open_channel
from provisioning.infrastructure.project.grpc_repository import GrpcRepositories
from provisioning.infrastructure.telemetry import Logger
from provisioning.infrastructure.telemetry.samples import add_inbox_sample

PROCESS_LABEL: Final = "provisioning-build-worker"


def main() -> int:
    return run_process(
        PROCESS_LABEL, load_build_worker, _start, profile_of=lambda config: config.profile.value
    )


def _start(
    config: BuildWorkerConfig, logger: Logger, audit: AuditLog, observability: Observability
) -> int:
    repositories: stand_ins.Repositories = ClosedRepositories()
    if config.profile is Profile.PROTOTYPE and config.repositories != PROJECT_GRPC:
        try:
            repositories = stand_ins.read_repositories(
                config.profile, config.repositories, config.repositories_file
            )
        except ConfigFileError as err:
            logger.error("build worker could not be built", error=str(err))
            return EXIT_FAILURE
    return asyncio.run(_run(config, repositories, logger, audit, observability))


async def _run(
    config: BuildWorkerConfig,
    repositories: stand_ins.Repositories,
    logger: Logger,
    audit: AuditLog,
    observability: Observability,
) -> int:
    stop = asyncio.Event()
    install_stop_signals(asyncio.get_running_loop(), stop)
    with settings_refused():
        build_system = jenkins_build_system(config.jenkins)
    try:
        async with contextlib.AsyncExitStack() as stack:
            intake_engine = open_role_engine(config.database, config.intake_login)
            stack.push_async_callback(intake_engine.dispose)
            build_engine = open_role_engine(config.database, config.build_login)
            stack.push_async_callback(build_engine.dispose)
            if config.repositories == PROJECT_GRPC and config.project_grpc_target is not None:
                # Prototype only (decision 0022 E7): the channel is lazy and closed on the way out.
                channel = open_channel(config.project_grpc_target)
                stack.push_async_callback(channel.close)
                repositories = GrpcRepositories(channel, logger)
            process = build_build_worker(
                config,
                intake_engine=intake_engine,
                build_engine=build_engine,
                build_system=build_system,
                repositories=repositories,
                audit=audit,
                metrics=observability.metrics,
            )
            metrics = observability.metrics
            add_inbox_sample(metrics, intake_engine, timeouts_of(config.database))
            ops = OpsListener(
                config.ops_listen_addr,
                metrics,
                timeout=exposition_timeout(config.database.tx_timeout),
            )
            await process.serve(stop, also=(ops.run,))
    except LoopEnded as err:
        logger.error("build worker stopped with an error", error=f"loop ended: {err}")
        return EXIT_FAILURE
    except InvalidSettings:
        raise
    except Exception as err:
        logger.error("build worker stopped with an error", error=type(err).__name__)
        return EXIT_FAILURE
    logging.getLogger("provisioning.build_worker").info("build worker stopped")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
