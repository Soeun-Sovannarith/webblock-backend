"""Live precise deploy failure codes (task H5a; decision 0015 section 4 step 1).

A project with a live release deploys a second release while the cluster cannot run it:

- **``insufficient_resources``:** the Kind worker node is cordoned, so the candidate's pod stays
  ``Unschedulable``. The control-plane node keeps its ``NoSchedule`` taint, which the test checks
  first; otherwise the pod would land there. Only scheduling of new pods stops: the stack's
  running pods, and the live release's pod, keep running, and Cilium's Gateway runs as a
  DaemonSet. The node is uncordoned in a ``finally``.
- **``quota_exceeded``:** a ResourceQuota is added to the tenant namespace with the admin
  kubeconfig, allowing no more pods than run now, so the candidate's ReplicaSet reports
  ``FailedCreate`` with "exceeded quota". It is deleted in a ``finally``.

In both cases the command fails with the precise code, the API's ``failure_retryable`` is the
catalogue's flag, the release stays ``built``, the candidate's Deployment is deleted, and the live
site is unchanged: the route stays on the active release, which still answers.

Every ``kubectl`` call goes through ``_recovery_support.kubectl``, which checks first that the
kubeconfig is ``bin/kind-state/proto/kubeconfig`` on context ``<engine>-webblock-proto`` at
``https://127.0.0.1:<port>`` (CLAUDE.md section 0). Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import (
    Site,
    check_context,
    eventually,
    kube_json,
    kubectl,
    namespace,
    workloads,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

QUOTA = "h5a-test-quota"
CONTROL_PLANE_LABEL = "node-role.kubernetes.io/control-plane"


async def _kubectl(*args: str, ok: bool = True) -> str:
    return await asyncio.to_thread(kubectl, *args, ok=ok)


async def _kube_json(*args: str) -> Any:
    return await asyncio.to_thread(kube_json, *args)


async def _workloads(project: uuid.UUID) -> set[str]:
    return await asyncio.to_thread(workloads, project)


async def _live_and_candidate(stack: Stack) -> tuple[Site, uuid.UUID, uuid.UUID]:
    """A project whose v1 is live, and its v2 built and not yet deployed."""
    site = Site(stack)
    live = await site.live("v1")
    candidate = await site.build("v2")
    return site, live, candidate


async def _assert_failed_with(
    stack: Stack, site: Site, live: uuid.UUID, candidate: uuid.UUID, done: dict[str, Any], code: str
) -> None:
    assert (done["status"], done.get("failureCode")) == ("failed", code), done
    # The catalogue's flag, as the API shows it (insufficient_resources retryable, quota not).
    assert done["failureRetryable"] is (code == "insufficient_resources"), done
    release = await stack.release(site.id, candidate)
    assert release["status"] == "built", release
    steps = [s for s in release["steps"] if s["stepName"] == "candidate"]
    assert steps and steps[-1]["status"] == "failed", steps
    assert steps[-1]["failureCode"] == code, steps
    assert steps[-1]["failureRetryable"] is done["failureRetryable"], steps
    await eventually(
        "the candidate's Deployment deleted",
        lambda: _workloads(site.id),
        lambda names: f"rel-{candidate}" not in names,
        within=120,
    )
    deployment = await stack.deployment(site.id)
    assert deployment["liveReleaseId"] == str(live), deployment
    assert deployment["routingStatus"] == "stable", deployment
    assert await site.answering() == str(live)


def _schedulable_workers(nodes: dict[str, Any]) -> list[str]:
    """The worker nodes; fails when a control-plane node would take the candidate's pod."""
    workers: list[str] = []
    for node in nodes["items"]:
        name = node["metadata"]["name"]
        if CONTROL_PLANE_LABEL in node["metadata"].get("labels", {}):
            taints = node["spec"].get("taints", [])
            assert any(
                t.get("key") == CONTROL_PLANE_LABEL and t.get("effect") == "NoSchedule"
                for t in taints
            ), f"control-plane node {name} is schedulable: cordoning the workers proves nothing"
        else:
            assert not node["spec"].get("unschedulable"), f"node {name} is already cordoned"
            workers.append(name)
    assert workers, "no worker node"
    return workers


async def test_an_unschedulable_candidate_fails_with_insufficient_resources() -> None:
    check_context()
    stack = Stack()
    site, live, candidate = await _live_and_candidate(stack)
    workers = _schedulable_workers(await _kube_json("get", "nodes"))
    try:
        for node in workers:
            await _kubectl("cordon", node)
        done = await site.command(candidate, "deploy", within=420)
    finally:
        for node in workers:
            await _kubectl("uncordon", node)
    await _assert_failed_with(stack, site, live, candidate, done, "insufficient_resources")


async def test_a_candidate_refused_by_a_quota_fails_with_quota_exceeded() -> None:
    check_context()
    stack = Stack()
    site, live, candidate = await _live_and_candidate(stack)
    ns = namespace(site.id)
    pods = await _kube_json("-n", ns, "get", "pods", "--field-selector=status.phase!=Succeeded")
    running = len([p for p in pods["items"] if not p["metadata"].get("deletionTimestamp")])
    assert running >= 1, "the live release has no pod"
    try:
        await _kubectl("-n", ns, "create", "quota", QUOTA, f"--hard=pods={running}")
        done = await site.command(candidate, "deploy", within=420)
    finally:
        await _kubectl("-n", ns, "delete", "resourcequota", QUOTA, "--ignore-not-found")
    await _assert_failed_with(stack, site, live, candidate, done, "quota_exceeded")
