"""QA probe, adopted (task W6-F; QA branch ``d487207``, ``tests/qa/passing``): RabbitMQ is paused
(connections stall) while a promotion is accepted, and unpaused 25 s later. The command must still
run and end once, and the recorded state must equal the cluster.

PostgreSQL is the authority and the broker only notifies (CLAUDE.md section 3): the accepted
command is a committed row, so the dispatcher publishes it once the broker is back.

Touches only the stack's own container ``webblock-rabbitmq``, never the owner's ``rabbitmq``.
Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import asyncio

import pytest
from tests.e2e._stack import Stack, docker
from tests.e2e.faults._recovery_support import Site, kube_json, namespace

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]


async def test_a_promote_accepted_while_the_broker_is_paused_runs_once_the_broker_is_back() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"

    docker("pause", "webblock-rabbitmq")
    try:
        command_id = await site.accept(v2, "promote")  # 202: the row is committed
        await asyncio.sleep(25)
    finally:
        docker("unpause", "webblock-rabbitmq")

    done = await stack.wait_command(site.id, command_id, 400)
    assert done["status"] == "succeeded", done
    deployment = await stack.deployment(site.id)
    assert (deployment["routingStatus"], deployment["liveReleaseId"]) == ("stable", str(v2))
    route = await asyncio.to_thread(
        kube_json, "-n", namespace(site.id), "get", "httproute", f"route-{site.id}"
    )
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == f"rel-{v2}"
    assert (await stack.release(site.id, v1))["status"] == "superseded"
    # One command only: a redelivery after the pause did not run it twice.
    rows = await stack.query(
        "SELECT count(*) FROM provisioning.commands WHERE release_id = %s AND kind = 'promote'",
        v2,
    )
    assert rows == [(1,)]
