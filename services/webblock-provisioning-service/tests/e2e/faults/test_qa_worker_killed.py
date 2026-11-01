"""QA probe, adopted (task W6-F; QA branch ``d487207``): the worker is killed (SIGKILL) right after
the site route was switched and before the database recorded it. The claim after the restart is
try 2 of the promote (FR-21.5): it first points the route back at the live release, then runs the
whole go-live again, which passes. The command ends ``succeeded`` after 2 claims, and the recorded
state equals the cluster: the live release is the one the route points at, and the route is
``stable``.

Needs ``make proto-up proto-run``. The worker is started again whatever happens.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import Site, kube_json, namespace

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]


def _backend(site: Site) -> str:
    route = kube_json("-n", namespace(site.id), "get", "httproute", f"route-{site.id}")
    return str(route["spec"]["rules"][0]["backendRefs"][0]["name"])


async def _execution_attempts(stack: Stack, command_id: str) -> int:
    rows = await stack.query_ops(
        "SELECT execution_attempts FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        uuid.UUID(command_id),
    )
    assert len(rows) == 1, rows
    return int(rows[0][0])


async def test_fr21_5_a_worker_killed_after_the_switch_leaves_a_consistent_project() -> None:
    """FR-21.5: the claim after the kill is try 2; the command succeeds with 2 claims."""
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"

    command_id = await site.accept(v2, "promote")
    try:
        deadline = time.monotonic() + 120
        while await asyncio.to_thread(_backend, site) != f"rel-{v2}":
            assert time.monotonic() < deadline, "the route never moved"
            await asyncio.sleep(0.05)
        await stack.process("kill-process", "worker")
    finally:
        await stack.process("start-process", "worker")

    done = await stack.wait_command(site.id, command_id, 300)
    assert done["status"] == "succeeded", done
    assert await _execution_attempts(stack, command_id) == 2
    deployment = await stack.deployment(site.id)
    backend = await asyncio.to_thread(_backend, site)
    assert deployment["routingStatus"] == "stable", (done, deployment)
    # The API answers in camelCase (liveReleaseId), never live_release_id.
    assert backend == f"rel-{deployment['liveReleaseId']}", (backend, deployment)
    assert deployment["liveReleaseId"] == str(v2), (deployment, v1)
    assert (await stack.release(site.id, v1))["status"] == "superseded"
    assert await site.answering() == deployment["liveReleaseId"]
