"""Live: the reconciler (task W6-B; decision 0014 section 21 ruling 9).

- A deleted live Deployment is re-applied, through the normal never-forced apply at the project's
  last command number, and the site answers again.
- An object written by another field manager is reported in the worker's log, not overwritten.
- Two promotions leave only the live release's workload (ruling 8, the inline cleanup).

The reconciler runs every ``RECONCILE_INTERVAL`` (30 s by default). Needs ``make proto-up
proto-run``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import (
    Site,
    audit_events,
    eventually,
    kube_json,
    kubectl,
    log_mark,
    namespace,
    worker_log_since,
    workloads,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

LABEL = "qa.webblock.test/manual"
MANAGER = "qa-manual"


async def _workloads(project: uuid.UUID) -> set[str]:
    return await asyncio.to_thread(workloads, project)


async def _log(mark: int) -> list[str]:
    return await asyncio.to_thread(worker_log_since, mark)


async def test_a_deleted_live_deployment_is_reapplied_and_the_site_answers_again() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    mark = log_mark()

    await asyncio.to_thread(
        kubectl, "-n", namespace(site.id), "delete", "deployment", f"rel-{v1}", "--wait=true"
    )
    assert f"rel-{v1}" not in await _workloads(site.id)

    await eventually(
        "the live Deployment re-applied",
        lambda: _workloads(site.id),
        lambda names: f"rel-{v1}" in names,
        within=180,
    )
    await eventually(
        f"release {v1} answering", site.answering, lambda name: name == str(v1), within=240
    )
    lines = await _log(mark)
    assert any(f"rel-{v1} of project {site.id} re-applied" in line for line in lines), lines[-20:]
    deployment = await asyncio.to_thread(
        kube_json,
        "-n",
        namespace(site.id),
        "get",
        "deployment",
        f"rel-{v1}",
        "--show-managed-fields",
    )
    managers = {e["manager"] for e in deployment["metadata"]["managedFields"]}
    assert "webblock-provisioning-worker" in managers


async def test_a_foreign_field_manager_is_reported_and_not_overwritten() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    mark = log_mark()
    ns = namespace(site.id)

    await asyncio.to_thread(
        kubectl,
        "-n",
        ns,
        "label",
        "deployment",
        f"rel-{v1}",
        f"{LABEL}=yes",
        f"--field-manager={MANAGER}",
    )
    try:
        lines: list[str] = await eventually(
            "the foreign manager reported",
            lambda: _log(mark),
            lambda found: any(
                "foreign field managers" in line and f"rel-{v1}" in line and MANAGER in line
                for line in found
            ),
            within=180,
        )
        deployment = await asyncio.to_thread(kube_json, "-n", ns, "get", "deployment", f"rel-{v1}")
        assert deployment["metadata"]["labels"].get(LABEL) == "yes", "the label was overwritten"
        assert not any(f"rel-{v1} of project {site.id} re-applied" in line for line in lines)
        assert await site.answering() == str(v1)
    finally:
        await asyncio.to_thread(
            kubectl,
            "-n",
            ns,
            "label",
            "deployment",
            f"rel-{v1}",
            f"{LABEL}-",
            f"--field-manager={MANAGER}",
        )


async def test_after_two_promotions_only_the_live_release_workload_remains() -> None:
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    mark = log_mark()
    v2 = await site.live("v2")

    await eventually(
        "only the live workload",
        lambda: _workloads(site.id),
        lambda names: names == {f"rel-{v2}"},
        within=120,
    )
    assert (await stack.release(site.id, v1))["status"] == "superseded"
    services = await asyncio.to_thread(kube_json, "-n", namespace(site.id), "get", "services")
    assert f"rel-{v1}" not in {s["metadata"]["name"] for s in services["items"]}
    cleanups = [
        e
        for e in audit_events(await _log(mark))
        if e.get("action") == "workload_cleanup" and e.get("release_id") == str(v1)
    ]
    assert cleanups and cleanups[0]["result"] == "succeeded", cleanups
