"""Live QA-07 (task W6-B; decision 0014 section 23 item 3): a route taken by hand no longer strands
the project. Adapted from the QA branch ``d487207`` (``tests/qa/test_qa_live_route_fence_ahead_
sticks_project.py`` and ``test_qa_live_foreign_edit_of_route_sticks_project.py``).

Both forms: the promotion of v2 is refused (the fence, or a server-side apply conflict), the
restore to v1 is refused by the same rule, so the route is recorded ``uncertain`` and the alert is
written. The operator rollback reads the observed route, takes it back with the forced
``reclaim_route`` (audited ``route_reclaim``), and the project is ``stable`` with v1 answering.

The audit events are read from every worker replica's log (task H5c): on a replica run either worker
may run the promotion and the rollback.

Needs ``make proto-up proto-run`` (or ``make proto-run-replicas``).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import (
    LogMarks,
    Site,
    audit_events,
    eventually,
    kube_json,
    kubectl,
    log_marks,
    logs_since,
    namespace,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]


async def _strand_and_recover(
    site: Site, stack: Stack, v1: uuid.UUID, v2: uuid.UUID, mark: LogMarks
) -> None:
    promote = await site.command(v2, "promote")
    assert promote["status"] == "failed", promote
    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "uncertain", deployment

    rollback = await site.command(v2, "rollback")
    assert rollback["status"] == "succeeded", rollback
    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "stable", deployment
    await eventually(f"release {v1} answering", site.answering, lambda name: name == str(v1))

    route = await asyncio.to_thread(
        kube_json, "-n", namespace(site.id), "get", "httproute", f"route-{site.id}"
    )
    number = route["metadata"]["annotations"]["websites.webblock.internal/command-number"]
    assert number != "999999"
    events = audit_events(await asyncio.to_thread(logs_since, mark))
    alerts = [e for e in events if e.get("alert") is True and e.get("project_id") == str(site.id)]
    assert [e["action"] for e in alerts] == ["route_uncertain"], alerts
    reclaims = [
        e
        for e in events
        if e.get("action") == "route_reclaim" and e.get("project_id") == str(site.id)
    ]
    assert len(reclaims) == 1 and reclaims[0]["result"] == "succeeded", reclaims
    assert reclaims[0]["actor_kind"] == "operator"


async def test_qa07_a_route_with_a_higher_number_is_recovered_by_the_operator_rollback() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    mark = log_marks()

    await asyncio.to_thread(
        kubectl,
        "-n",
        namespace(site.id),
        "annotate",
        "httproute",
        f"route-{site.id}",
        "websites.webblock.internal/command-number=999999",
        "--overwrite",
    )
    await _strand_and_recover(site, stack, v1, v2, mark)


async def test_qa07_a_route_edited_by_hand_is_recovered_by_the_operator_rollback() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    mark = log_marks()

    patch = [{"op": "replace", "path": "/spec/rules/0/matches/0/path/value", "value": "/qa"}]
    await asyncio.to_thread(
        kubectl,
        "-n",
        namespace(site.id),
        "patch",
        "httproute",
        f"route-{site.id}",
        "--type=json",
        "-p",
        json.dumps(patch),
    )
    await _strand_and_recover(site, stack, v1, v2, mark)
    route = await asyncio.to_thread(
        kube_json, "-n", namespace(site.id), "get", "httproute", f"route-{site.id}"
    )
    assert route["spec"]["rules"][0]["matches"][0]["path"]["value"] != "/qa"
