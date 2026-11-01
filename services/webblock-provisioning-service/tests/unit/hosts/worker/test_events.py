"""The worker's event parts (wave 6 task W6-S, moved out of the wiring; task W6-E added the
deletion consumer, the hosting retry sweep and the usage publisher): the queues the broker client
must declare, the loops in their order, and the entitlement record the hosting executor reads.
Builds without opening any connection."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from tests.unit.hosts.envs import worker_env, worker_file_mode_env

from provisioning.hosts.shared.messaging import broker_config
from provisioning.hosts.shared.runtime import SystemClock, UUIDv7, open_role_engine, timeouts_of
from provisioning.hosts.worker.events import EventParts, build_events
from provisioning.infrastructure.config.worker import WorkerProcessConfig, load_worker
from provisioning.infrastructure.rabbitmq import (
    COMMAND_JOB_NAMES,
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
    OWNER_STATUS_NAMES,
    PROJECT_DELETED_NAMES,
    PROVISIONING_EVENTS_EXCHANGE,
    BrokerClient,
    ExchangeTopology,
    Subscription,
)
from provisioning.infrastructure.standins.entitlement_record import PrototypeHostingRecord

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")


def _config() -> WorkerProcessConfig:
    return load_worker(worker_env(KUBECONFIG).get)


@pytest.mark.asyncio
async def test_the_event_side_is_three_queues_and_eight_loops_over_the_client() -> None:
    config = _config()
    engine = open_role_engine(config.database, config.hosting_login)
    worker = open_role_engine(config.database, config.worker_login)
    try:
        events = build_events(
            config,
            hosting_engine=engine,
            worker_engine=worker,
            timeouts=timeouts_of(config.database),
            clock=SystemClock(),
            ids=UUIDv7(),
        )
        assert isinstance(events, EventParts)
        assert events.queue_names == (
            PROJECT_DELETED_NAMES,
            OWNER_STATUS_NAMES,
            ENTITLEMENT_ENDED_NAMES,
            ENTITLEMENT_RESTORED_NAMES,
        )
        # The usage event goes to Provisioning's own exchange; no Payment queue is declared.
        assert events.publish_exchanges == (PROVISIONING_EVENTS_EXCHANGE,)
        # The record that answers "ended" for a deleted project (no resume after deletion).
        assert isinstance(events.record, PrototypeHostingRecord)
        client = BrokerClient(
            broker_config(
                config.broker,
                (COMMAND_JOB_NAMES, *events.queue_names),
                "test-events",
                publish_exchanges=events.publish_exchanges,
            ),
            logger=logging.getLogger("test.events"),
        )
        loops = events.loops(client)
        # Each subscription's loop, then its consumer's loop; then the sweep and the publisher.
        assert len(loops) == 10
        assert all(callable(loop) for loop in loops)
        assert isinstance(getattr(loops[0], "__self__", None), Subscription)
        assert loops[1].__name__ == "consume_ended"
        assert isinstance(getattr(loops[2], "__self__", None), Subscription)
        assert loops[3].__name__ == "consume_restored"
        assert isinstance(getattr(loops[4], "__self__", None), Subscription)
        assert loops[5].__name__ == "consume_deletions"
        assert isinstance(getattr(loops[6], "__self__", None), Subscription)
        assert loops[7].__name__ == "consume_owner_statuses"
        assert type(getattr(loops[8], "__self__", None)).__name__ == "HostingRetrySweeper"
        assert type(getattr(loops[9], "__self__", None)).__name__ == "UsagePublisher"
    finally:
        await engine.dispose()
        await worker.dispose()


def test_the_broker_configuration_declares_the_event_queues_and_exchanges() -> None:
    config = _config()
    names = (COMMAND_JOB_NAMES, ENTITLEMENT_ENDED_NAMES)
    plain = broker_config(config.broker, names, "worker")
    assert [t.names for t in plain.topologies] == list(names)
    assert all(t.limits.delivery_limit == 5 for t in plain.topologies)
    assert plain.publish_exchanges == ()
    with_exchange = broker_config(
        config.broker, names, "worker", publish_exchanges=(PROVISIONING_EVENTS_EXCHANGE,)
    )
    assert with_exchange.publish_exchanges == (
        ExchangeTopology(names=PROVISIONING_EVENTS_EXCHANGE, limits=plain.topologies[0].limits),
    )


def test_the_run_scripts_file_mode_environment_loads() -> None:
    """The worker environment of run-provisioning.sh since wave 6: the entitlement stand-in in
    file mode. Only the configuration is loaded; the stand-in itself is W6-D's."""
    entitlement = KUBECONFIG.parent / "provisioning" / "entitlement.json"
    config = load_worker(worker_file_mode_env(KUBECONFIG, entitlement).get)
    assert (config.entitlement, config.entitlement_file) == ("prototype-file", entitlement)
