"""The broker dead-letter re-drive live (task W6-E2; .claude/docs/runbooks/operator.md section 6).

A live site; Payment's "hosting entitlement ended" v1 event for it is parked in its dead-letter
queue by the broker itself; the operator lists it, fixes the cause and re-drives it; the suspend
happens and the site goes offline; the re-drive is one audit line, in the run's operator.log and in
Loki.

Parking. A well-formed event is blocked: every worker replica (the queue's only consumers) is
stopped (task H5c: on a replica run the second worker would consume it), and the event is
published with a per-message lifetime of one second, so the broker dead-letters it from the work
queue with reason ``expired`` and a real ``x-death`` record. The cause is then fixed by starting
the workers again. Everything after the publish is the real system: the broker's dead-lettering,
the operator CLI on the broker settings only, the worker's consumer and the hosting intake. Needs
``make proto-up proto-run`` (or ``make proto-run-replicas``). Secrets come from the run script's
files and are never printed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pytest
from tests.e2e._stack import PROV, ROOT, Stack, docker_port, read_env_file, run, until
from tests.e2e.faults.test_hosting_deletion import hosting_commands, live_site

from provisioning.core.domain import CommandKind
from provisioning.infrastructure.rabbitmq import ENTITLEMENT_ENDED_NAMES
from provisioning.presentation.messaging import HOSTING_ENTITLEMENT_ENDED_V1, EventCodecV1

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]

RUN = ROOT / "scripts" / "prototype" / "run-provisioning.sh"
DLQ = ENTITLEMENT_ENDED_NAMES.dead_letter_queue


async def operator_dlq(*args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(RUN),
        "operator-dlq",
        *args,
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(120):
        out, err = await proc.communicate()
    assert proc.returncode is not None
    return proc.returncode, out.decode(), err.decode()


async def park_blocked(project: uuid.UUID, key: str) -> str:
    """Publish a well-formed "entitlement ended" event that expires after one second unconsumed;
    returns its message ID."""
    prov = read_env_file(PROV / "provisioning.env")
    port = docker_port("webblock-rabbitmq", 5672)
    url = f"amqp://provisioning:{prov['MQ_PASSWORD']}@127.0.0.1:{port}/"
    message = EventCodecV1(max_body_bytes=4096).encode_hosting_event(
        message_id=uuid.uuid7(),
        event_key=key,
        project_id=project,
        kind=CommandKind.SUSPEND_HOSTING,
        reason="trial_ended",
        occurred_at=datetime.now(UTC),
    )
    connection = await aio_pika.connect(url, timeout=10)
    try:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange(ENTITLEMENT_ENDED_NAMES.exchange, ensure=False)
        await exchange.publish(
            aio_pika.Message(
                message.body,
                content_type=message.content_type,
                type=message.message_type,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                timestamp=message.timestamp,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                expiration=timedelta(seconds=1),
            ),
            ENTITLEMENT_ENDED_NAMES.routing_keys[0],
            mandatory=True,
        )
    finally:
        await connection.close()
    return message.message_id


def listed(out: str, message_id: str) -> list[list[str]]:
    rows = [line.split("\t") for line in out.splitlines()]
    assert rows and rows[0][:2] == ["queue", "message_id"], out
    return [row for row in rows[1:] if row[1] == message_id]


async def loki(project: uuid.UUID) -> list[dict[str, Any]]:
    query = (
        '{job="webblock-provisioning", process="operator"} | json | audit="true"'
        f' | action="dlq_redrive" | project_id="{project}"'
    )
    out = await run(
        ["bash", str(ROOT / "deploy" / "kind" / "proto-loki-query.sh"), "--since", "1h", query]
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def test_a_parked_payment_event_is_redriven_and_the_site_goes_offline() -> None:
    stack = Stack()
    project, _ = await live_site(stack, "e2e-dlq")
    key = f"e2e-dlq-{uuid.uuid7()}"

    await stack.process_all("stop-process", "worker")
    try:
        message_id = await park_blocked(project, key)

        async def parked() -> list[list[str]]:
            code, out, err = await operator_dlq("list", "--queue", DLQ, "--limit", "500")
            assert code == 0, err
            return listed(out, message_id)

        (row,) = await until("the event parked", parked, bool, 60)
        assert row[2] == "expired", row  # death reason
        assert row[4:9] == [
            ENTITLEMENT_ENDED_NAMES.exchange,
            ENTITLEMENT_ENDED_NAMES.routing_keys[0],
            HOSTING_ENTITLEMENT_ENDED_V1,
            key,
            str(project),
        ], row
    finally:
        await stack.process_all(
            "start-process", "worker"
        )  # the cause fixed: the consumers are back

    # The parked event did nothing: no hosting command yet, the site still serves.
    assert await hosting_commands(stack, project) == []

    code, out, err = await operator_dlq(
        "redrive", "--queue", DLQ, "--message-id", message_id, "--by", "operator-e2e"
    )
    assert code == 0, err
    assert out.startswith(f"redriven queue={DLQ} message_id={message_id} "), out
    code, out, err = await operator_dlq("list", "--queue", DLQ, "--limit", "500")
    assert code == 0 and listed(out, message_id) == [], (out, err)

    await until(
        "the suspend succeeded",
        lambda: hosting_commands(stack, project),
        lambda rows: rows == [(key, "suspend_hosting", "succeeded", None)],
        180,
    )
    await until(
        "the offline page",
        lambda: stack.visit(project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )

    # One audit line for the re-drive, with the operator as the actor and the event's project.
    audit = [
        json.loads(line)
        for line in (PROV / "logs" / "latest" / "operator.log").read_text().splitlines()
        if line.startswith("{") and '"audit"' in line
    ]
    mine = [
        a for a in audit if a.get("action") == "dlq_redrive" and a.get("project_id") == str(project)
    ]
    assert [(a["actor"], a["actor_kind"], a["result"]) for a in mine] == [
        ("operator-e2e", "operator", "succeeded")
    ]
    assert mine[0]["correlation_id"] == f"dlq-redrive-{message_id}"
    lines = await until("the re-drive in Loki", lambda: loki(project), bool, 120, 5)
    assert [(r["actor"], r["result"]) for r in lines] == [("operator-e2e", "succeeded")]
