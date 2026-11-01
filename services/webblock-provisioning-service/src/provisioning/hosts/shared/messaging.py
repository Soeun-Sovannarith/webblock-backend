"""Broker and backoff settings shared by the worker and the build worker hosts (task G): the
configuration sections' plain values turned into E2's adapter and loop configuration. It imports
no Kubernetes client, so the build worker can use it without holding any cluster code."""

from __future__ import annotations

from provisioning.core.application.shared.backoff import Backoff
from provisioning.infrastructure.config.common import BackoffSettings, BrokerSettings
from provisioning.infrastructure.rabbitmq import (
    BrokerConfig,
    ExchangeNames,
    ExchangeTopology,
    QueueLimits,
    QueueNames,
    QueueTopology,
)


def backoff(settings: BackoffSettings) -> Backoff:
    return Backoff(base=settings.base, max=settings.max)


def broker_config(
    settings: BrokerSettings,
    names: tuple[QueueNames, ...],
    connection_name: str,
    *,
    publish_exchanges: tuple[ExchangeNames, ...] = (),
) -> BrokerConfig:
    """The adapter's configuration for the queues ``names``, all with the configured limits, and
    the exchanges ``publish_exchanges`` it only publishes to (an owned topic exchange's alternate
    queue takes the dead-letter limits); the process declares its own and passively checks the
    others."""
    limits = QueueLimits(
        queue_max_length=settings.queue_max_length,
        delivery_limit=settings.delivery_limit,
        consumer_timeout=settings.consumer_timeout,
        dlq_max_length=settings.dlq_max_length,
        dlq_message_ttl=settings.dlq_message_ttl,
    )
    return BrokerConfig(
        url=settings.url.reveal(),
        heartbeat=settings.heartbeat,
        dial_timeout=settings.dial_timeout,
        reconnect_base=settings.reconnect.base,
        reconnect_max=settings.reconnect.max,
        declare_topology=settings.declare_topology,
        topologies=tuple(QueueTopology(names=n, limits=limits) for n in names),
        publish_exchanges=tuple(
            ExchangeTopology(names=e, limits=limits) for e in publish_exchanges
        ),
        max_in_flight_publishes=settings.max_in_flight_publishes,
        connection_name=connection_name,
    )
