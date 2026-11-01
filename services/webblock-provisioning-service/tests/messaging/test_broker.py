"""The RabbitMQ adapter against a real broker (decision 0005 sections 3, 4, 6 and 8): topology,
publisher confirms, returns, nacks, redelivery after a consumer crash, and bounded reconnect after a
broker restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import aio_pika
import pytest
from tests.messaging._broker import (
    Broker,
    broker_config,
    limits,
    logger,
    queue_depth,
    running,
    unique_names,
    wait_for,
)

from provisioning.infrastructure.rabbitmq import (
    BrokerClient,
    NotConnected,
    PublishNacked,
    PublishReturned,
    QueueNames,
    QueueTopology,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")

AT = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


async def publish(
    client: BrokerClient, names: QueueNames, n: int, *, key: str | None = None
) -> None:
    await client.publish(
        exchange=names.exchange,
        routing_key=names.routing_keys[0] if key is None else key,
        body=b'{"n": %d}' % n,
        content_type="application/json",
        message_type="test.message",
        message_id=f"m-{n}",
        correlation_id=f"c-{n}",
        timestamp=AT,
    )


async def test_topology_is_declared_then_checked_and_a_mismatch_refuses_the_connection(
    broker: Broker,
) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits())
    async with running(BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))):
        pass
    # Checked only: the objects exist, so the connection comes up.
    checker = BrokerClient(
        broker_config(broker.url, (topology,), declare=False), logger=logger("t")
    )
    async with running(checker) as client:
        await publish(client, names, 1)
    assert await queue_depth(broker.url, names.queue) == 1
    # Missing objects, checked only: the client never connects.
    missing = QueueTopology(names=unique_names(), limits=limits())
    never = BrokerClient(broker_config(broker.url, (missing,), declare=False), logger=logger("t"))
    task = asyncio.create_task(never.run())
    await wait_for(lambda: never.connect_failures >= 2)
    with pytest.raises(NotConnected):
        await publish(never, names, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Declared again with other arguments: PRECONDITION_FAILED, the client never connects.
    changed = QueueTopology(names=names, limits=limits(delivery_limit=3))
    refused = BrokerClient(broker_config(broker.url, (changed,)), logger=logger("t"))
    task = asyncio.create_task(refused.run())
    await wait_for(lambda: refused.connect_failures >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_confirmed_publish_is_persistent_and_carries_its_properties(broker: Broker) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits())
    async with running(
        BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))
    ) as c:
        await publish(c, names, 7)
    connection = await aio_pika.connect(broker.url)
    try:
        channel = await connection.channel()
        queue = await channel.get_queue(names.queue, ensure=False)
        message = await queue.get(timeout=5)
        assert message is not None
        assert message.body == b'{"n": 7}'
        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
        assert (message.content_type, message.type) == ("application/json", "test.message")
        assert (message.message_id, message.correlation_id) == ("m-7", "c-7")
        await message.ack()
    finally:
        await connection.close()


async def test_an_unroutable_publish_is_returned_not_counted_as_published(broker: Broker) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits())
    async with running(
        BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))
    ) as c:
        with pytest.raises(PublishReturned) as caught:
            await publish(c, names, 1, key="no-such-binding")
        assert '{"n": 1}' not in str(caught.value)  # the body is never quoted
        results = await asyncio.gather(
            *(publish(c, names, n, key="nowhere") for n in range(20)), return_exceptions=True
        )
        assert all(isinstance(r, PublishReturned) for r in results)
        await publish(c, names, 2)  # the channel is still usable
    assert await queue_depth(broker.url, names.queue) == 1


async def test_a_publish_to_a_full_queue_is_nacked(broker: Broker) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits(queue_max_length=1))
    async with running(
        BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))
    ) as c:
        await publish(c, names, 1)
        # On a quorum queue reject-publish "does not strictly enforce the limit" (RabbitMQ 4.3
        # documentation): a publish or two may still be accepted after the
        # queue is full. What must hold is that the overflow is a negative acknowledgment, never a
        # confirm, and that the queue holds exactly the confirmed messages.
        accepted, nacked = 1, 0
        for n in range(2, 22):
            try:
                await publish(c, names, n)
            except PublishNacked:
                nacked += 1
            else:
                accepted += 1
        assert nacked > 0
    assert await queue_depth(broker.url, names.queue) == accepted


async def test_an_unsettled_delivery_is_redelivered_after_a_consumer_crash(broker: Broker) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits())
    client = BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))
    async with running(client) as c:
        await publish(c, names, 1)
        subscription = c.subscribe(names.queue, prefetch=1)
        consuming = asyncio.create_task(subscription.run())
        try:
            async with asyncio.timeout(10):
                first = await subscription.next_delivery()
            assert first is not None and not first.redelivered
            # The consumer "crashes": its channel closes with the delivery unsettled.
            subscription.restart()
            async with asyncio.timeout(10):
                again = await subscription.next_delivery()
            assert again is not None
            assert again.redelivered and again.body == first.body
            await again.ack()
            assert subscription.subscriptions >= 2
        finally:
            consuming.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consuming
        assert await subscription.next_delivery() is None
    assert await queue_depth(broker.url, names.queue) == 0


async def test_a_broker_restart_is_survived_with_bounded_reconnect(broker: Broker) -> None:
    names = unique_names()
    topology = QueueTopology(names=names, limits=limits())
    client = BrokerClient(broker_config(broker.url, (topology,)), logger=logger("t"))
    async with running(client) as c:
        subscription = c.subscribe(names.queue, prefetch=2)
        consuming = asyncio.create_task(subscription.run())
        try:
            await publish(c, names, 1)
            async with asyncio.timeout(10):
                got = await subscription.next_delivery()
            assert got is not None
            await got.ack()
            connects = c.connections
            broker.stop_app()
            try:
                with pytest.raises((NotConnected, PublishNacked)):
                    async with asyncio.timeout(10):
                        await wait_for(lambda: c.connections == connects and not c.connected)
                        await publish(c, names, 2)
            finally:
                broker.start_app()
            # Reconnected within the bounded backoff (max 500 ms between tries).
            await wait_for(lambda: c.connected and c.connections > connects, within_seconds=30)
            await publish(c, names, 3)
            async with asyncio.timeout(15):
                after = await subscription.next_delivery()
            assert after is not None and after.body == b'{"n": 3}'
            await after.ack()
        finally:
            consuming.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consuming
