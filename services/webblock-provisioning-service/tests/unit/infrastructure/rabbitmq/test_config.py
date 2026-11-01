"""The RabbitMQ adapter's configuration and topology (decision 0005 sections 6 and 7). No
broker is needed here."""

import asyncio
import dataclasses
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.unit.hosts.envs import worker_env

from provisioning.infrastructure.config.worker import load_worker
from provisioning.infrastructure.rabbitmq import (
    COMMAND_JOB_NAMES,
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
    OWNER_STATUS_NAMES,
    PAYMENT_EVENTS,
    PROJECT_DELETED_NAMES,
    PROJECT_EVENTS,
    PROVISIONING_EVENTS,
    PROVISIONING_EVENTS_EXCHANGE,
    PROVISIONING_EVENTS_UNROUTED,
    PROVISIONING_QUEUES,
    PUBLISH_COMPLETED_ROUTING_KEY,
    SOURCE_PUBLISHED_NAMES,
    BrokerClient,
    BrokerConfig,
    ExchangeNames,
    ExchangeTopology,
    InvalidBrokerConfig,
    NotConnected,
    QueueLimits,
    QueueTopology,
)

URL_WITH_CREDENTIALS = "amqp://user:s3cr3t-pass@broker.invalid:5672/vhost"


def limits(**changes: Any) -> QueueLimits:
    base = QueueLimits(
        queue_max_length=1000,
        delivery_limit=5,
        consumer_timeout=timedelta(minutes=30),
        dlq_max_length=10_000,
        dlq_message_ttl=timedelta(days=7),
    )
    return dataclasses.replace(base, **changes)


def config(**changes: Any) -> BrokerConfig:
    base = BrokerConfig(
        url=URL_WITH_CREDENTIALS,
        heartbeat=timedelta(seconds=10),
        dial_timeout=timedelta(seconds=5),
        reconnect_base=timedelta(milliseconds=100),
        reconnect_max=timedelta(seconds=5),
        declare_topology=True,
        topologies=(QueueTopology(names=COMMAND_JOB_NAMES, limits=limits()),),
        publish_exchanges=(),
        max_in_flight_publishes=10,
        connection_name="provisioning-worker",
    )
    return dataclasses.replace(base, **changes)


def test_the_names_are_those_of_decision_0022_e6() -> None:
    assert COMMAND_JOB_NAMES.exchange == "provisioning.jobs"
    assert COMMAND_JOB_NAMES.exchange_type == "direct"
    assert COMMAND_JOB_NAMES.exchange_owned
    assert COMMAND_JOB_NAMES.queue == "provisioning.command_job.v1"
    assert COMMAND_JOB_NAMES.routing_keys == ("command_job.v1",)
    assert COMMAND_JOB_NAMES.dead_letter_exchange == "provisioning.dlx"
    assert COMMAND_JOB_NAMES.dead_letter_queue == "provisioning.command_job.v1.dlq"
    for names, exchange, event in (
        (SOURCE_PUBLISHED_NAMES, PROJECT_EVENTS, "source_published"),
        (PROJECT_DELETED_NAMES, PROJECT_EVENTS, "project_deleted"),
        (OWNER_STATUS_NAMES, PROJECT_EVENTS, "owner_status_changed"),
        (ENTITLEMENT_ENDED_NAMES, PAYMENT_EVENTS, "hosting_entitlement_ended"),
        (ENTITLEMENT_RESTORED_NAMES, PAYMENT_EVENTS, "hosting_entitlement_restored"),
    ):
        assert names.exchange == exchange
        assert names.exchange_type == "topic"
        assert names.queue == f"provisioning.{event}.v1"
        assert names.routing_keys == (f"{event}.v1",)
    assert PROVISIONING_EVENTS == "provisioning.events"
    assert PROVISIONING_EVENTS_UNROUTED == "provisioning.events.unrouted"
    assert PROVISIONING_EVENTS_EXCHANGE.alternate_exchange == PROVISIONING_EVENTS_UNROUTED
    assert PUBLISH_COMPLETED_ROUTING_KEY == "publish_completed.v1"
    assert len({n.queue for n in PROVISIONING_QUEUES}) == 6


@pytest.mark.parametrize("names", PROVISIONING_QUEUES, ids=lambda n: n.queue)
def test_every_provisioning_queue_follows_the_one_rule(names: Any) -> None:
    assert re.fullmatch(r"provisioning\.[a-z_]+\.v1", names.queue)
    assert names.dead_letter_queue == f"{names.queue}.dlq"
    assert names.dead_letter_exchange == "provisioning.dlx"
    if names.exchange_owned:
        assert names.exchange.startswith("provisioning.")
    QueueTopology(names=names, limits=limits()).validate()


def test_project_and_payment_exchanges_are_not_owned() -> None:
    foreign = {n.exchange for n in PROVISIONING_QUEUES if not n.exchange_owned}
    assert foreign == {PROJECT_EVENTS, PAYMENT_EVENTS}
    assert all(n.exchange_owned for n in PROVISIONING_QUEUES if n.exchange not in foreign)
    assert PROVISIONING_EVENTS_EXCHANGE.owned
    assert PROVISIONING_EVENTS_EXCHANGE.name.startswith("provisioning.")


def test_only_an_owned_topic_exchange_has_an_alternate_exchange() -> None:
    assert ExchangeNames("x.events", "topic", True).alternate_exchange == "x.events.unrouted"
    assert ExchangeNames("x.events", "topic", False).alternate_exchange is None
    assert ExchangeNames("x.jobs", "direct", True).alternate_exchange is None


def test_the_unrouted_queue_is_bounded_like_a_dead_letter_queue() -> None:
    topology = ExchangeTopology(names=PROVISIONING_EVENTS_EXCHANGE, limits=limits())
    assert topology.unrouted_arguments() == {
        "x-queue-type": "quorum",
        "x-max-length": 10_000,
        "x-message-ttl": 604_800_000,
    }


def test_the_delivery_limit_is_5_when_unset() -> None:
    env = worker_env(Path("/nonexistent/kubeconfig"))
    assert "RABBITMQ_DELIVERY_LIMIT" not in env
    assert load_worker(env.get).broker.delivery_limit == 5
    env["RABBITMQ_DELIVERY_LIMIT"] = "7"
    assert load_worker(env.get).broker.delivery_limit == 7


def test_queue_arguments_carry_every_limit() -> None:
    t = QueueTopology(names=COMMAND_JOB_NAMES, limits=limits())
    assert t.queue_arguments() == {
        "x-queue-type": "quorum",
        "x-dead-letter-exchange": "provisioning.dlx",
        "x-delivery-limit": 5,
        "x-max-length": 1000,
        "x-overflow": "reject-publish",
        "x-consumer-timeout": 1_800_000,
    }
    assert t.dead_letter_arguments() == {
        "x-queue-type": "quorum",
        "x-max-length": 10_000,
        "x-message-ttl": 604_800_000,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"queue_max_length": 0},
        {"delivery_limit": 0},
        {"dlq_max_length": 0},
        {"consumer_timeout": timedelta(microseconds=999)},
        {"dlq_message_ttl": timedelta(0)},
        {"consumer_timeout": timedelta(milliseconds=2**31)},
    ],
)
def test_limits_are_refused_when_out_of_range(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidBrokerConfig):
        QueueTopology(names=COMMAND_JOB_NAMES, limits=limits(**changes)).validate()


def test_names_are_refused_when_empty_spaced_or_shared_with_the_dead_letter_side() -> None:
    for names in (
        dataclasses.replace(COMMAND_JOB_NAMES, queue=""),
        dataclasses.replace(COMMAND_JOB_NAMES, exchange="has space"),
        dataclasses.replace(COMMAND_JOB_NAMES, routing_keys=()),
        dataclasses.replace(COMMAND_JOB_NAMES, dead_letter_exchange="provisioning.jobs"),
        dataclasses.replace(COMMAND_JOB_NAMES, dead_letter_queue="provisioning.command_job.v1"),
        dataclasses.replace(COMMAND_JOB_NAMES, queue="q" * 256),
    ):
        with pytest.raises(InvalidBrokerConfig):
            QueueTopology(names=names, limits=limits()).validate()


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "http://broker.invalid"},
        {"url": "amqp://"},
        {"url": "not a url at all"},
        {"heartbeat": timedelta(milliseconds=999)},
        {"dial_timeout": timedelta(0)},
        {"reconnect_base": timedelta(0)},
        {"reconnect_max": timedelta(milliseconds=50)},
        {"max_in_flight_publishes": 0},
        {"connection_name": ""},
        {"connection_name": "with space"},
        {"topologies": (), "publish_exchanges": ()},
        {
            "publish_exchanges": (
                ExchangeTopology(ExchangeNames("bad name", "direct", True), limits()),
            )
        },
    ],
)
def test_the_config_refuses_missing_values_without_quoting_the_url(changes: dict[str, Any]) -> None:
    with pytest.raises(InvalidBrokerConfig) as caught:
        config(**changes).validate()
    assert "s3cr3t" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_the_url_is_never_printed() -> None:
    c = config()
    assert "s3cr3t" not in repr(c) and "s3cr3t" not in str(c)


@pytest.mark.asyncio
async def test_publishing_without_a_connection_fails_at_once() -> None:
    client = BrokerClient(config(), logger=logging.getLogger("tests.rabbitmq"))
    with pytest.raises(NotConnected):
        async with asyncio.timeout(1):
            await client.publish(
                exchange="provisioning.jobs",
                routing_key="command_job.v1",
                body=b"{}",
                content_type="application/json",
                message_type="provisioning.command_job",
                message_id="m-1",
                correlation_id="c-1",
                timestamp=datetime(2026, 10, 8, tzinfo=UTC),
            )


@pytest.mark.asyncio
async def test_run_retries_an_unreachable_broker_until_cancelled() -> None:
    unreachable = config(
        url="amqp://guest:guest@127.0.0.1:1/",
        reconnect_base=timedelta(milliseconds=5),
        reconnect_max=timedelta(milliseconds=10),
    )
    client = BrokerClient(unreachable, logger=logging.getLogger("tests.rabbitmq"))
    task = asyncio.create_task(client.run())
    await asyncio.sleep(0.1)
    assert not task.done()
    assert client.connect_failures >= 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
