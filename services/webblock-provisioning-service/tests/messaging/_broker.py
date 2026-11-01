"""Private helpers of the RabbitMQ suite: the disposable broker, unique topologies, clients."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import aio_pika
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import DockerContainer

from provisioning.hosts.devtools.simulate import producer_config
from provisioning.infrastructure.rabbitmq import (
    BrokerClient,
    BrokerConfig,
    ExchangeTopology,
    QueueLimits,
    QueueNames,
    QueueTopology,
)

RABBITMQ_IMAGE = "rabbitmq:4.3.6-management-alpine"
RYUK_IMAGE = "testcontainers/ryuk:0.14.0"
START_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class Broker:
    container: DockerContainer
    url: str

    def ctl(self, *args: str) -> str:
        """Run ``rabbitmqctl`` as the rabbitmq user inside this test's own container; its
        standard output."""
        wrapped = self.container.get_wrapped_container()
        code, output = wrapped.exec_run(["rabbitmqctl", *args], user="rabbitmq")
        if code != 0:
            raise RuntimeError(f"rabbitmqctl {' '.join(args)} failed: {output!r}")
        return bytes(output).decode()

    def stop_app(self) -> None:
        self.ctl("stop_app")

    def start_app(self) -> None:
        self.ctl("start_app")
        self.ctl("await_startup")


def start_broker() -> tuple[DockerContainer, Broker]:
    """Start the disposable broker; raises (never skips) when Docker or the broker fails."""
    testcontainers_config.ryuk_image = RYUK_IMAGE
    user, password = "webblock", secrets.token_hex(8)
    container = (
        DockerContainer(RABBITMQ_IMAGE)
        .with_env("RABBITMQ_DEFAULT_USER", user)
        .with_env("RABBITMQ_DEFAULT_PASS", password)
        .with_exposed_ports(5672)
    )
    try:
        container.start()
    except Exception as exc:  # any Docker failure must fail the run with one message
        raise RuntimeError(
            f"cannot start {RABBITMQ_IMAGE} with Testcontainers (is Docker running?): {exc}"
        ) from None
    try:
        url = (
            f"amqp://{user}:{password}@{container.get_container_host_ip()}:"
            f"{container.get_exposed_port(5672)}/"
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while not asyncio.run(_accepts(url)):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{RABBITMQ_IMAGE} did not accept AMQP connections")
            time.sleep(0.5)
    except BaseException:
        container.stop()
        raise
    return container, Broker(container=container, url=url)


async def _accepts(url: str) -> bool:
    try:
        async with asyncio.timeout(5):
            connection = await aio_pika.connect(url)
    except Exception:  # not up yet
        return False
    await connection.close()
    return True


def unique_names(prefix: str = "t", routing_keys: tuple[str, ...] = ("job",)) -> QueueNames:
    tag = f"{prefix}.{secrets.token_hex(4)}"
    return QueueNames(
        exchange=f"{tag}.x",
        exchange_type="direct",
        exchange_owned=True,
        queue=f"{tag}.q",
        routing_keys=routing_keys,
        dead_letter_exchange=f"{tag}.dlx",
        dead_letter_queue=f"{tag}.dlq",
    )


def limits(*, queue_max_length: int = 1000, delivery_limit: int = 20) -> QueueLimits:
    return QueueLimits(
        queue_max_length=queue_max_length,
        delivery_limit=delivery_limit,
        consumer_timeout=timedelta(minutes=5),
        dlq_max_length=1000,
        dlq_message_ttl=timedelta(hours=1),
    )


def broker_config(
    url: str,
    topologies: tuple[QueueTopology, ...],
    *,
    declare: bool = True,
    publish_exchanges: tuple[ExchangeTopology, ...] = (),
) -> BrokerConfig:
    return BrokerConfig(
        url=url,
        heartbeat=timedelta(seconds=5),
        dial_timeout=timedelta(seconds=5),
        reconnect_base=timedelta(milliseconds=50),
        reconnect_max=timedelta(milliseconds=500),
        declare_topology=declare,
        topologies=topologies,
        publish_exchanges=publish_exchanges,
        max_in_flight_publishes=50,
        connection_name=f"test-{secrets.token_hex(3)}",
    )


@asynccontextmanager
async def running(client: BrokerClient) -> AsyncIterator[BrokerClient]:
    """Run the client's connection loop for the block, and wait until it is connected."""
    task = asyncio.create_task(client.run())
    try:
        async with asyncio.timeout(15):
            await client.wait_connected()
        yield client
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def declare_producers(url: str) -> None:
    """``project.events`` and ``payment.events`` as the simulator declares them, standing in for
    Project and Payment (decision 0022 E6); Provisioning only checks them."""
    client = BrokerClient(producer_config(url), logger=logger("producers"))
    async with running(client):
        pass


def logger(name: str) -> logging.Logger:
    return logging.getLogger(f"tests.messaging.{name}")


async def queue_depth(url: str, queue: str) -> int:
    """Messages ready in ``queue``, read on a separate connection."""
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        declared = await channel.declare_queue(queue, passive=True)
        count = declared.declaration_result.message_count
        return 0 if count is None else count
    finally:
        await connection.close()


async def wait_for(
    condition: Callable[[], bool | Awaitable[bool]], within_seconds: float = 15.0
) -> None:
    """Poll a sync or async zero-argument condition until it holds or the time passes."""
    async with asyncio.timeout(within_seconds):
        while True:
            value = condition()
            if not isinstance(value, bool):
                value = await value
            if value:
                return
            await asyncio.sleep(0.02)
