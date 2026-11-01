"""QA probe, adopted (task W6-F; QA branch ``d487207``): the Provisioning PostgreSQL container is
paused (every connection stalls) after the promotion switched the route and before the database
recorded it, and unpaused 20 s later. Afterwards the command ends and the recorded state equals
the cluster: the active release is the one the route points at, and the route is ``stable``.

Touches only the stack's own container ``webblock-provisioning-db``, never the owner's
``postgres``. Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from tests.e2e._stack import Stack, docker, until
from tests.e2e.faults._recovery_support import Site, kube_json, namespace

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]


def _backend(site: Site) -> str:
    route = kube_json("-n", namespace(site.id), "get", "httproute", f"route-{site.id}")
    return str(route["spec"]["rules"][0]["backendRefs"][0]["name"])


async def _status(stack: Stack, site: Site, command_id: str) -> dict[str, Any] | None:
    try:
        return await stack.command(site.id, command_id)
    except Exception:  # the API answers 5xx while the database is paused
        return None


async def test_the_database_paused_after_the_switch_leaves_the_record_equal_to_the_cluster() -> (
    None
):
    stack = Stack()
    site = Site(stack)
    await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    command_id = await site.accept(v2, "promote")

    deadline = time.monotonic() + 120
    while await asyncio.to_thread(_backend, site) != f"rel-{v2}":
        assert time.monotonic() < deadline, "the route never moved"
        await asyncio.sleep(0.05)
    docker("pause", "webblock-provisioning-db")
    try:
        await asyncio.sleep(20)
    finally:
        docker("unpause", "webblock-provisioning-db")

    done = await until(
        "the promote to finish",
        lambda: _status(stack, site, command_id),
        lambda b: b is not None and b["status"] in {"succeeded", "failed"},
        400,
        3,
    )
    assert done is not None
    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "stable", deployment
    assert await asyncio.to_thread(_backend, site) == f"rel-{deployment['liveReleaseId']}"
