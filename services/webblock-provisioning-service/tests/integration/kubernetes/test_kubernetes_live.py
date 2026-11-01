"""The Kubernetes adapter against task A's Kind stack (decision 0014; brief of task D, "Live").

Run against task A's stack (``make proto-up``); 7 of 7 passed on 8 October 2026. Each test
bootstraps the tenant of the images' project first (idempotent), uses fresh release IDs, and
command numbers from the clock so that a rerun is never fenced by an earlier run. Releases are
deleted by the tests; the tenant namespace ``wb-<projectId>``, its route (left pointing at the
offline page) and the ReferenceGrant ``wb-<projectId>`` in ``webblock-offline`` stay. Neither
identity may delete them, so remove both afterwards with the admin kubeconfig
(``bin/kind-state/proto/kubeconfig``, after checking its context is
``<engine>-webblock-proto``); the ReferenceGrant is not removed with the namespace.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx2
import pytest
from tests.integration.kubernetes.conftest import PREVIEW_TOKEN

from provisioning.core.application.ports.site_cluster import (
    OFFLINE_HEADER_VALUE,
    PREVIEW_HEADER,
    RELEASE_HEADER,
    ApplyConflict,
    ClusterRefused,
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    TenantState,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.application.routing.background.routing import preview_hostname
from provisioning.infrastructure.kubernetes import (
    KubeApi,
    KubernetesForbidden,
    Resource,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    SiteSettings,
    preview_token_for,
    render_deployment,
    render_namespace,
    render_service,
    tenant_namespace,
)
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

READY_WITHIN = 180.0
POLL = 2.0


def command_number() -> int:
    """Increasing across runs: milliseconds since the epoch (below 2^53)."""
    return time.time_ns() // 1_000_000


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool], within: float) -> T:
    """Poll ``read`` until ``done`` holds or ``within`` seconds pass; return the last value."""
    value = await read()
    try:
        async with asyncio.timeout(within):
            while not done(value):
                await asyncio.sleep(POLL)
                value = await read()
    except TimeoutError:
        pass
    return value


def hostname(stack: Stack) -> str:
    return f"{stack.subdomain}.sites.webblock.test"


async def ready_release(
    cluster: KubernetesSiteCluster, stack: Stack, number: int
) -> tuple[ReleaseWorkload, WorkloadStatus]:
    workload = ReleaseWorkload(stack.project_id, uuid4(), stack.site_image, number)
    await cluster.apply_release(workload)
    status = await until(
        lambda: cluster.release_status(workload),
        lambda s: s.state is not WorkloadState.PROGRESSING,
        READY_WITHIN,
    )
    return workload, status


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(bootstrap: KubernetesTenantBootstrap, stack: Stack) -> None:
    created: list[UUID] = []  # task BT: the namespace exists (autouse fixture), so no report
    assert (
        await bootstrap.ensure_tenant(
            stack.project_id, subdomain=stack.subdomain, on_created=created.append
        )
        is TenantState.READY
    )
    assert (
        await bootstrap.ensure_tenant(
            stack.project_id, subdomain=stack.subdomain, on_created=created.append
        )
        is TenantState.READY
    )
    assert created == []


@pytest.mark.asyncio
async def test_the_worker_gets_403_creating_a_namespace(worker_api: KubeApi) -> None:
    with pytest.raises(KubernetesForbidden):
        await worker_api.apply(
            Resource.NAMESPACE,
            render_namespace(uuid4(), subdomain="forbidden", catalog_secret=None),
        )


@pytest.mark.asyncio
async def test_an_unknown_field_is_refused(
    bootstrap: KubernetesTenantBootstrap,
    worker_api: KubeApi,
    site_settings: SiteSettings,
    stack: Stack,
) -> None:
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    workload = ReleaseWorkload(stack.project_id, uuid4(), stack.site_image, command_number())
    manifest = render_deployment(workload, site_settings)
    manifest["spec"]["notAField"] = True
    # Kubernetes 1.36 answers this server-side apply with 500 "failed to create typed patch
    # object ... field not declared in schema", not 400 (measured here, task D); the client maps
    # that answer to ClusterRefused.

    with pytest.raises(ClusterRefused):
        await worker_api.apply(Resource.DEPLOYMENT, manifest)
    assert (
        await worker_api.read(
            Resource.DEPLOYMENT, manifest["metadata"]["name"], tenant_namespace(stack.project_id)
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_field_owned_by_a_foreign_manager_is_a_conflict(
    bootstrap: KubernetesTenantBootstrap,
    worker_api: KubeApi,
    foreign_api: KubeApi,
    stack: Stack,
) -> None:
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    workload = ReleaseWorkload(stack.project_id, uuid4(), stack.site_image, command_number())
    service = render_service(workload)
    foreign = render_service(workload)
    foreign["metadata"]["labels"]["websites.webblock.internal/release-id"] = "foreign"
    del foreign["metadata"]["annotations"]
    await foreign_api.apply(Resource.SERVICE, foreign)
    try:
        with pytest.raises(ApplyConflict):
            await worker_api.apply(Resource.SERVICE, service)
    finally:
        name, namespace = service["metadata"]["name"], service["metadata"]["namespace"]
        current = await worker_api.read(Resource.SERVICE, name, namespace)
        if current is not None:
            meta = current["metadata"]
            await worker_api.delete(
                Resource.SERVICE,
                name,
                namespace,
                uid=meta["uid"],
                resource_version=meta["resourceVersion"],
            )


@pytest.mark.asyncio
async def test_a_release_deploys_to_ready(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    number = command_number()
    workload, status = await ready_release(cluster, stack, number)
    release = workload.release_id
    try:
        assert status == WorkloadStatus(WorkloadState.READY)
    finally:
        await cluster.delete_release(stack.project_id, release, number)


@pytest.mark.asyncio
async def test_a_release_that_never_becomes_ready_fails(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    number = command_number()
    workload = ReleaseWorkload(stack.project_id, uuid4(), stack.never_ready_image, number)
    release = workload.release_id
    await cluster.apply_release(workload)
    try:
        status = await until(
            lambda: cluster.release_status(workload),
            lambda s: s.state is WorkloadState.FAILED,
            READY_WITHIN,
        )
        assert status.state is WorkloadState.FAILED, status
        assert status.reason in ("crash_loop", "progress_deadline_exceeded"), status
    finally:
        await cluster.delete_release(stack.project_id, release, number)


@pytest.mark.asyncio
async def test_the_route_goes_to_a_release_then_offline_and_a_routed_release_is_not_deleted(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    host = hostname(stack)
    number = command_number()
    workload, status = await ready_release(cluster, stack, number)
    release = workload.release_id
    assert status == WorkloadStatus(WorkloadState.READY)
    to_release = RouteTarget(RouteTargetKind.RELEASE, release)
    offline = RouteTarget(RouteTargetKind.OFFLINE)
    try:
        await cluster.apply_route(
            stack.project_id, to_release, (host,), number, subdomain=stack.subdomain
        )
        routed = await until(
            lambda: cluster.route_status(stack.project_id),
            lambda s: s.accepted and s.target == to_release,
            60.0,
        )
        assert routed == RouteStatus(True, to_release, number)
        assert (
            await until(lambda: cluster.probe_site(stack.project_id, host, to_release), bool, 60.0)
            is True
        )
        # The sample site serves as UID 65532 on a read-only root file system (the renderer's pod).
        async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
            page = await http.get(stack.gateway_url + "/", headers={"Host": host})
        assert page.status_code == 200
        assert "sample-site version" in page.text

        with pytest.raises(FenceRejected):
            await cluster.delete_release(stack.project_id, release, number)
        assert (await cluster.release_status(workload)).state is WorkloadState.READY
    finally:
        await cluster.apply_route(
            stack.project_id, offline, (host,), number + 1, subdomain=stack.subdomain
        )

    moved = await until(
        lambda: cluster.route_status(stack.project_id),
        lambda s: s.accepted and s.target == offline,
        60.0,
    )
    assert moved == RouteStatus(True, offline, number + 1)
    # Once the Gateway serves the offline page, the release is no longer what answers.
    assert (
        await until(
            lambda: cluster.probe_site(stack.project_id, host, to_release), lambda ok: not ok, 60.0
        )
        is False
    )

    await cluster.delete_release(stack.project_id, release, number + 1)
    gone = await until(
        lambda: cluster.release_status(workload),
        lambda s: s.state is WorkloadState.MISSING,
        60.0,
    )
    assert gone == WorkloadStatus(WorkloadState.MISSING)


async def release_header(
    stack: Stack, host: str, path: str = "/readyz", *, token: str | None = None
) -> tuple[int, list[str]]:
    """The status and the ``X-Webblock-Release`` values of one request through the Gateway; the
    preview route answers only with the probe token (task T2, recheck N1)."""
    headers = {"Host": host} if token is None else {"Host": host, PREVIEW_HEADER: token}
    async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
        response = await http.get(stack.gateway_url + path, headers=headers, follow_redirects=False)
    return response.status_code, response.headers.get_list(RELEASE_HEADER)


async def cache_control(stack: Stack, host: str, path: str = "/readyz", *, token: str) -> list[str]:
    """The ``Cache-Control`` values of one request with the preview token through the Gateway."""
    headers = {"Host": host, PREVIEW_HEADER: token}
    async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
        response = await http.get(stack.gateway_url + path, headers=headers, follow_redirects=False)
    return response.headers.get_list("Cache-Control")


@pytest.mark.asyncio
async def test_both_routes_name_their_backend_and_the_preview_is_fenced(
    bootstrap: KubernetesTenantBootstrap, cluster: KubernetesSiteCluster, stack: Stack
) -> None:
    """FR-20.1, FR-52.1: the candidate answers on ``<subdomain>-preview.<site domain>`` with its
    project's token, and not with no token, the worker's raw secret or another project's token
    (404); its answers say ``Cache-Control: private, no-store`` (security review M1). Decision 0014
    section 17: every rule of the site's route and of the preview route sets
    ``X-Webblock-Release`` (checked on the Gateway's data plane), the probe requires it, and the
    preview route is fenced like the site's route."""
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    host = hostname(stack)
    preview = preview_hostname(stack.subdomain, "sites.webblock.test")
    number = command_number()
    workload, status = await ready_release(cluster, stack, number)
    release = workload.release_id
    assert status == WorkloadStatus(WorkloadState.READY)
    to_release = RouteTarget(RouteTargetKind.RELEASE, release)
    offline = RouteTarget(RouteTargetKind.OFFLINE)
    someone_else = RouteTarget(RouteTargetKind.RELEASE, uuid4())
    try:
        # The preview route: confirmed at this number, its header names the release.
        await cluster.apply_preview(
            stack.project_id, release, preview, number, subdomain=stack.subdomain
        )
        confirmed = await until(
            lambda: cluster.preview_status(stack.project_id),
            lambda s: s.accepted and s.target == to_release and s.command_number == number,
            60.0,
        )
        assert confirmed == RouteStatus(True, to_release, number)
        assert (
            await until(
                lambda: cluster.probe_site(
                    stack.project_id, preview, to_release, preview_of=stack.subdomain
                ),
                bool,
                60.0,
            )
            is True
        )
        # Decision 0023 G18c (Task 12 security review S1): only the project's own token opens
        # its preview; the worker's raw secret and another project's token get 404.
        own = preview_token_for(PREVIEW_TOKEN, stack.project_id)
        assert await release_header(stack, preview, token=own) == (200, [str(release)])
        assert await release_header(stack, preview) == (404, [])
        assert await release_header(stack, preview, token=PREVIEW_TOKEN) == (404, [])
        other = preview_token_for(PREVIEW_TOKEN, uuid4())
        assert await release_header(stack, preview, token=other) == (404, [])
        assert await release_header(stack, preview, "/", token=own) == (200, [str(release)])
        # FR-52.1 (preview-host security review M1): no shared cache may keep a preview answer.
        assert await cache_control(stack, preview, token=own) == ["private, no-store"]
        assert await cache_control(stack, preview, "/", token=own) == ["private, no-store"]
        # 200 from the right site, but another release is expected: not this backend.
        assert (
            await cluster.probe_site(
                stack.project_id, preview, someone_else, preview_of=stack.subdomain
            )
            is False
        )
        assert (
            await cluster.probe_site(stack.project_id, preview, offline, preview_of=stack.subdomain)
            is False
        )

        # A lower command number may not move the preview route.
        with pytest.raises(FenceRejected):
            await cluster.apply_preview(
                stack.project_id, uuid4(), preview, number - 1, subdomain=stack.subdomain
            )
        assert await cluster.preview_status(stack.project_id) == confirmed

        # The site's route to the release: the same header on every path.
        await cluster.apply_route(
            stack.project_id, to_release, (host,), number, subdomain=stack.subdomain
        )
        assert (
            await until(lambda: cluster.probe_site(stack.project_id, host, to_release), bool, 60.0)
            is True
        )
        assert await release_header(stack, host, "/") == (200, [str(release)])
    finally:
        await cluster.apply_route(
            stack.project_id, offline, (host,), number + 1, subdomain=stack.subdomain
        )

    # The offline page: the header says "offline", and the release's probe fails.
    moved = await until(
        lambda: cluster.route_status(stack.project_id),
        lambda s: s.accepted and s.target == offline,
        60.0,
    )
    assert moved == RouteStatus(True, offline, number + 1)
    assert (
        await until(
            lambda: cluster.probe_site(stack.project_id, host, to_release), lambda ok: not ok, 60.0
        )
        is False
    )
    _, values = await release_header(stack, host, "/")
    assert values == [OFFLINE_HEADER_VALUE]
    await cluster.delete_release(stack.project_id, release, number + 1)
