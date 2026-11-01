"""The wave 6 adapter methods on the Kind stack (task W6-M): ``list_tenant_projects``,
``inventory`` with the foreign field managers read from ``managedFields``, and ``reclaim_route``,
the operator rollback's one forced apply (decision 0014 section 23 item 3, QA-07).

The foreign edits are made as a person would make them: ``kubectl patch`` and ``kubectl annotate``
with the stack's admin kubeconfig under the field manager ``webblock-test-foreign`` (the
``kubectl`` helper of ``conftest.py`` checks the context first). Command numbers come from the
clock, so a rerun is never fenced by an earlier one; every test leaves the route on the offline
page and deletes its releases."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.integration.kubernetes.conftest import FOREIGN_FIELD_MANAGER, SITE_DOMAIN, kubectl

from provisioning.core.application.ports.site_cluster import (
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    SiteObject,
    SiteObjectKind,
    TenantState,
    WorkloadState,
)
from provisioning.infrastructure.kubernetes import WORKER_FIELD_MANAGER
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_COMMAND_NUMBER,
    release_name,
    route_name,
    tenant_namespace,
)
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

POLL = 2.0
pytestmark = pytest.mark.timeout(600)  # readiness, the route and the probe, each bounded
OFFLINE = RouteTarget(RouteTargetKind.OFFLINE)


def command_number() -> int:
    return time.time_ns() // 1_000_000


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool], within: float) -> T:
    value = await read()
    try:
        async with asyncio.timeout(within):
            while not done(value):
                await asyncio.sleep(POLL)
                value = await read()
    except TimeoutError:
        pass
    return value


async def ready(cluster: KubernetesSiteCluster, stack: Stack, number: int) -> ReleaseWorkload:
    workload = ReleaseWorkload(stack.project_id, uuid4(), stack.site_image, number)
    await cluster.apply_release(workload)
    status = await until(
        lambda: cluster.release_status(workload),
        lambda s: s.state is not WorkloadState.PROGRESSING,
        180.0,
    )
    assert status.state is WorkloadState.READY, status
    return workload


def by_name(objects: tuple[SiteObject, ...], kind: SiteObjectKind, name: str) -> SiteObject:
    (found,) = [o for o in objects if o.kind is kind and o.name == name]
    return found


@pytest.mark.asyncio
async def test_the_stacks_project_is_a_tenant_project(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )

    projects = await cluster.list_tenant_projects()

    assert stack.project_id in projects
    labelled = json.loads(
        kubectl("get", "namespaces", "-l", "websites.webblock.internal/tenant=true", "-o", "json")
    )
    names = {item["metadata"]["name"] for item in labelled["items"]}
    assert {f"wb-{p}" for p in projects} <= names
    print(f"{len(projects)} tenant projects of {len(names)} labelled namespaces")


@pytest.mark.asyncio
async def test_inventory_reads_the_fence_and_a_foreign_manager_made_by_kubectl(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    number = command_number()
    workload = await ready(cluster, stack, number)
    name = release_name(workload.release_id)
    namespace = tenant_namespace(stack.project_id)
    try:
        before = await cluster.inventory(stack.project_id)
        # The worker's own objects, after the Deployment controller has written the revision
        # annotation and every controller its status: no foreign manager.
        for kind in (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE):
            assert by_name(before.objects, kind, name) == SiteObject(
                kind, name, workload.release_id, number, ()
            )
        deployment = json.loads(
            kubectl(
                "-n", namespace, "get", "deployment", name, "--show-managed-fields", "-o", "json"
            )
        )
        managers = sorted({e["manager"] for e in deployment["metadata"]["managedFields"]})
        print("managedFields of the worker's Deployment:", managers)
        probe = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]
        assert probe["httpGet"]["path"] == "/healthz"
        assert "tcpSocket" not in probe

        kubectl(
            "-n",
            namespace,
            "patch",
            "service",
            name,
            "--type=merge",
            "--field-manager",
            FOREIGN_FIELD_MANAGER,
            "-p",
            json.dumps({"metadata": {"labels": {"edited-by": "hand"}}}),
        )
        after = await cluster.inventory(stack.project_id)
        assert by_name(after.objects, SiteObjectKind.SERVICE, name).foreign_managers == (
            FOREIGN_FIELD_MANAGER,
        )
        assert by_name(after.objects, SiteObjectKind.DEPLOYMENT, name).foreign_managers == ()
    finally:
        await cluster.delete_release(stack.project_id, workload.release_id, number)


@pytest.mark.asyncio
async def test_reclaim_route_takes_back_a_route_edited_by_hand(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    """QA-07, both forms: a fence stamped far ahead by ``kubectl annotate`` and a rule field the
    worker owns changed by ``kubectl patch``. The never-forced apply is refused; the reclaim
    puts the route back on the release, re-stamps the fence and leaves no foreign manager."""
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    project = stack.project_id
    host = f"{stack.subdomain}.{SITE_DOMAIN}"
    namespace = tenant_namespace(project)
    route = route_name(project)
    number = command_number()
    workload = await ready(cluster, stack, number)
    to_release = RouteTarget(RouteTargetKind.RELEASE, workload.release_id)
    try:
        await cluster.apply_route(project, to_release, (host,), number, subdomain=stack.subdomain)
        assert (
            await until(lambda: cluster.route_status(project), lambda s: s.accepted, 60.0)
        ).target == to_release

        kubectl(
            "-n",
            namespace,
            "annotate",
            "--overwrite",
            "--field-manager",
            FOREIGN_FIELD_MANAGER,
            "httproute",
            route,
            f"{ANNOTATION_COMMAND_NUMBER}=999999999999999",
        )
        patch = [
            {
                "op": "replace",
                "path": "/spec/rules/0/backendRefs/0/name",
                "value": f"rel-{uuid4()}",
            }
        ]
        kubectl(
            "-n",
            namespace,
            "patch",
            "httproute",
            route,
            "--type=json",
            "--field-manager",
            FOREIGN_FIELD_MANAGER,
            "-p",
            json.dumps(patch),
        )
        edited = by_name(
            (await cluster.inventory(project)).objects, SiteObjectKind.HTTP_ROUTE, route
        )
        assert edited.command_number == 999999999999999
        assert edited.foreign_managers == (FOREIGN_FIELD_MANAGER,)
        with pytest.raises(FenceRejected):
            await cluster.apply_route(
                project, to_release, (host,), number + 1, subdomain=stack.subdomain
            )

        await cluster.reclaim_route(
            project, to_release, (host,), number + 1, subdomain=stack.subdomain
        )

        status = await until(
            lambda: cluster.route_status(project),
            lambda s: s.accepted and s.target == to_release,
            60.0,
        )
        assert status == RouteStatus(True, to_release, number + 1)
        reclaimed = by_name(
            (await cluster.inventory(project)).objects, SiteObjectKind.HTTP_ROUTE, route
        )
        assert reclaimed.command_number == number + 1
        assert reclaimed.foreign_managers == ()
        stored = json.loads(
            kubectl(
                "-n", namespace, "get", "httproute", route, "--show-managed-fields", "-o", "json"
            )
        )
        writers = {
            e["manager"] for e in stored["metadata"]["managedFields"] if not e.get("subresource")
        }
        assert writers == {WORKER_FIELD_MANAGER}
        assert (
            await until(lambda: cluster.probe_site(project, host, to_release), bool, 60.0) is True
        )
    finally:
        try:
            await cluster.apply_route(
                project, OFFLINE, (host,), number + 2, subdomain=stack.subdomain
            )
        except FenceRejected:
            # The test failed before its reclaim: take the route back so later tests can run.
            await cluster.reclaim_route(
                project, OFFLINE, (host,), number + 2, subdomain=stack.subdomain
            )
    moved = await until(
        lambda: cluster.route_status(project),
        lambda s: s.accepted and s.target == OFFLINE,
        60.0,
    )
    assert moved == RouteStatus(True, OFFLINE, number + 2)
    await cluster.delete_release(project, workload.release_id, number + 2)
