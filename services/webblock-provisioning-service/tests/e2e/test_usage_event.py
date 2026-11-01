"""The "publish completed" usage event live (decision 0014 section 21 answer 2; task W6-E).

A test queue is bound to the worker's ``provisioning.events`` exchange with ``publish_completed.v1``
before a promotion; after the release goes live, the queue receives the release's publish-completed
event (persistent, under the v1 schema, with the release's key, project and release). Delivery is
at least once, so a duplicate may follow; every message for the release must carry the same key.
The outbox row ends ``sent``. Needs ``make proto-up proto-run``. Secrets come from the run script's
files and are never printed.
"""

from __future__ import annotations

import asyncio
import secrets
import time

import aio_pika
import pytest
from tests.e2e._stack import PROV, Stack, docker_port, read_env_file, until
from tests.e2e.faults.test_hosting_deletion import live_site

from provisioning.infrastructure.rabbitmq import PROVISIONING_EVENTS, PUBLISH_COMPLETED_ROUTING_KEY
from provisioning.presentation.messaging import (
    PUBLISH_COMPLETED_V1,
    EventCodecV1,
    PublishCompleted,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]


def amqp_url() -> str:
    prov = read_env_file(PROV / "provisioning.env")
    port = docker_port("webblock-rabbitmq", 5672)
    return f"amqp://provisioning:{prov['MQ_PASSWORD']}@127.0.0.1:{port}/"


async def test_a_release_that_goes_live_sends_its_publish_completed_event() -> None:
    stack = Stack()
    codec = EventCodecV1(max_body_bytes=65536)
    connection = await aio_pika.connect(amqp_url())
    try:
        channel = await connection.channel()
        exchange = await channel.get_exchange(PROVISIONING_EVENTS, ensure=True)
        queue = await channel.declare_queue(
            f"e2e.usage.{secrets.token_hex(4)}", exclusive=True, auto_delete=True
        )
        await queue.bind(exchange, PUBLISH_COMPLETED_ROUTING_KEY)

        project, release = await live_site(stack, "e2e-usage")

        got: list[PublishCompleted] = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            message = await queue.get(fail=False)
            if message is None:
                if got:
                    break  # the event arrived; a duplicate would follow the same poll
                await asyncio.sleep(1)
                continue
            assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
            assert message.type == PUBLISH_COMPLETED_V1
            event = codec.decode_publish_completed(
                body=message.body,
                content_type=message.content_type,
                message_type=message.type,
                message_id=message.message_id,
            )
            await message.ack()
            if event.release_id == release:
                got.append(event)
        assert got, "no publish-completed event for the release reached the test queue"
        assert {(e.event_key, e.project_id, e.release_id) for e in got} == {
            (f"publish-completed-{release}", project, release)
        }
    finally:
        await connection.close()

    rows = await until(
        "the outbox row sent",
        lambda: stack.query_ops(
            "SELECT status::text, attempts >= 1, sent_at IS NOT NULL"
            " FROM provisioning_ops.usage_outbox WHERE release_id = %s",
            release,
        ),
        lambda r: bool(r) and r[0][0] == "sent",
        60,
    )
    assert rows == [("sent", True, True)]
