"""The worker's wiring of the wave 6 recovery (task W6-B): the reconciler loop is part of the
process, the routing and hosting executors carry the abandon hook with a bound above the route
restore deadline, and the reconcile round has a bound. Built with unconnected engines and an
in-memory cluster; nothing connects."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit.core.application.routing.background.test_recovery import RecordingAudit
from tests.unit.core.application.routing.background.test_routing_support import FakeCluster
from tests.unit.hosts.envs import worker_env

from provisioning.core.application.reconcile.background.reconciler import ReconcilerLoop
from provisioning.infrastructure.config.worker import load_worker
from provisioning.infrastructure.telemetry import new_logger
from provisioning.presentation.messaging.consumers.consumer import Abandoner

if TYPE_CHECKING:
    from provisioning.hosts.worker.wiring import WorkerProcess

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")


def _build(env: dict[str, str]) -> WorkerProcess:
    # Imported here, not at collection (see test_site_settings.py): the wiring imports
    # kubernetes_asyncio, whose default kubeconfig path the kubernetes conftest poisons first.
    import io

    from provisioning.hosts.worker.wiring import build_worker

    config = load_worker(env.get)
    engine = create_async_engine("postgresql+psycopg://unused@127.0.0.1:1/unused")
    return build_worker(
        config,
        worker_engine=engine,
        hosting_engine=engine,
        cluster=FakeCluster([]),
        logger=new_logger(io.StringIO(), config.log_level),
        audit=RecordingAudit(),
    )


def test_the_worker_runs_the_reconciler_and_abandons_route_commands() -> None:
    from provisioning.core.domain import CommandKind

    process = _build(worker_env(KUBECONFIG))

    assert isinstance(process.reconciler, ReconcilerLoop)
    executors = process.consumer.executors()
    for kind in (
        CommandKind.DEPLOY,
        CommandKind.PROMOTE,
        CommandKind.ROLLBACK,
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
    ):
        executor = executors[kind]
        assert isinstance(executor, Abandoner), kind


def test_the_worker_runs_redeploy_through_its_executor_without_an_abandon_hook() -> None:
    """Decision 0023 G12: a redeploy never moves the route and its rolling update keeps the old
    pods serving, so there is nothing to abandon."""
    from provisioning.core.application.deploys.background.redeploy_executor import RedeployExecutor
    from provisioning.core.domain import CommandKind

    process = _build(worker_env(KUBECONFIG))

    executor = process.consumer.executors()[CommandKind.REDEPLOY]
    assert isinstance(executor, RedeployExecutor)
    assert not isinstance(executor, Abandoner)


def test_the_route_abandon_bound_covers_a_restore_and_two_transactions() -> None:
    from provisioning.hosts.worker.wiring import reconcile_round_timeout, route_abandon_timeout

    config = load_worker(worker_env(KUBECONFIG).get)
    assert route_abandon_timeout(config) == config.route.restore_deadline + 2 * (
        config.database.tx_timeout
    )
    assert reconcile_round_timeout(config) > config.reconcile.batch_size * timedelta(seconds=1)


def test_site_probe_gap_reaches_the_adapter_settings() -> None:
    from provisioning.hosts.worker.wiring import site_cluster_settings

    default = site_cluster_settings(load_worker(worker_env(KUBECONFIG).get))
    assert default.probe_success_gap_seconds == 0.5
    env = worker_env(KUBECONFIG) | {"SITE_PROBE_GAP": "2s"}
    assert site_cluster_settings(load_worker(env.get)).probe_success_gap_seconds == 2.0


def test_payment_grpc_modes_use_the_grpc_entitlement_in_routing_and_hosting() -> None:
    import io

    from provisioning.hosts.worker.wiring import build_worker
    from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement

    env = worker_env(KUBECONFIG) | {
        "ENTITLEMENT_MODE": "payment-grpc",
        "HOSTING_ENTITLEMENT_MODE": "payment-grpc",
        "PAYMENT_GRPC_TARGET": "payment.webblock.svc:9090",
    }
    config = load_worker(env.get)
    engine = create_async_engine("postgresql+psycopg://unused@127.0.0.1:1/unused")
    logger = new_logger(io.StringIO(), config.log_level)
    payment = GrpcEntitlement(None, logger)  # the channel is never used here
    process = build_worker(
        config,
        worker_engine=engine,
        hosting_engine=engine,
        cluster=FakeCluster([]),
        logger=logger,
        audit=RecordingAudit(),
        payment=payment,
    )
    from provisioning.core.domain import CommandKind

    executors = process.consumer.executors()
    routing = executors[CommandKind.PROMOTE]
    hosting = executors[CommandKind.SUSPEND_HOSTING]
    assert routing._routing._entitlement is payment  # type: ignore[attr-defined]
    assert hosting._hosting._entitlement is payment  # type: ignore[attr-defined]


def test_payment_grpc_without_the_adapter_is_invalid_settings() -> None:
    from provisioning.hosts.shared.runtime import InvalidSettings

    env = worker_env(KUBECONFIG) | {
        "ENTITLEMENT_MODE": "payment-grpc",
        "PAYMENT_GRPC_TARGET": "payment.webblock.svc:9090",
    }
    with pytest.raises(InvalidSettings):
        _build(env)
