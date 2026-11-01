"""Every manifest kind the renderer produces, validated by the API server with a server-side dry
run (decision 0014 section 20, task W6-M), with task W6-K's admission policies on.

Each object is applied with ``dryRun=All`` as the identity that writes it in service: the release
worker for the Deployment, Service and the two HTTPRoutes, the tenant-bootstrap identity for the
tenant objects and access_setup's Secret. The API server runs schema validation, strict field
validation and admission (the ``webblock-site-*`` policies match the worker, ``webblock-bootstrap-
scope`` and ``webblock-tenant-writer`` the identities), and stores nothing. To show that the
policies are on during the dry run, three objects the renderer would never produce, each changed
after rendering, are refused (``ClusterRefused``): an image from another registry, a write without
the fence annotation, and a route naming another project's hostname.

``test_every_render_function_is_dry_run`` fails when a new ``render_*`` function is added to the
renderer without being dry-run here."""

import inspect
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from tests.integration.kubernetes.conftest import PREVIEW_TOKEN, SITE_DOMAIN

from provisioning.core.application.ports.site_cluster import (
    ClusterRefused,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    TenantState,
)
from provisioning.core.application.routing.background.routing import preview_hostname
from provisioning.infrastructure.kubernetes import KubeApi, Resource
from provisioning.infrastructure.kubernetes import render as render_module
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_COMMAND_NUMBER,
    CATALOG_SECRET_DELIVERED,
    SiteSettings,
    TenantSettings,
    render_deployment,
    render_limit_range,
    render_namespace,
    render_preview_route,
    render_reference_grant,
    render_resource_quota,
    render_route,
    render_runtime_secret,
    render_service,
    render_worker_role_binding,
    tenant_namespace,
)

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

TENANT = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)
WORKER, BOOTSTRAP = "worker", "bootstrap"


def command_number() -> int:
    return time.time_ns() // 1_000_000


def secret_values(stack: Stack) -> dict[str, str]:
    return {
        "PGHOST": "webblock-catalog-db",
        "PGPORT": "5432",
        "PGDATABASE": "catalog",
        "PGUSER": f"site_{stack.project_id.hex}",
        "PGPASSWORD": "dry-run-placeholder-never-stored",
        "PGSSLMODE": "disable",
    }


type Rendered = tuple[str, Resource, dict[str, Any]]


def rendered(stack: Stack, site: SiteSettings) -> dict[str, list[Rendered]]:
    """Each render function's output for the stack's project, with the identity that writes it."""
    project = stack.project_id
    number = command_number()
    workload = ReleaseWorkload(project, uuid4(), stack.site_image, number)
    hosts = (f"{stack.subdomain}.{SITE_DOMAIN}",)
    to_release = RouteTarget(RouteTargetKind.RELEASE, workload.release_id)
    offline = RouteTarget(RouteTargetKind.OFFLINE)
    return {
        "render_deployment": [
            (WORKER, Resource.DEPLOYMENT, render_deployment(workload, site)),
        ],
        "render_service": [(WORKER, Resource.SERVICE, render_service(workload))],
        "render_route": [
            (
                WORKER,
                Resource.HTTP_ROUTE,
                render_route(
                    project,
                    to_release,
                    hosts,
                    number,
                    site,
                    site_domain=SITE_DOMAIN,
                    subdomain=stack.subdomain,
                ),
            ),
            (
                WORKER,
                Resource.HTTP_ROUTE,
                render_route(
                    project,
                    offline,
                    hosts,
                    number,
                    site,
                    site_domain=SITE_DOMAIN,
                    subdomain=stack.subdomain,
                ),
            ),
        ],
        "render_preview_route": [
            (
                WORKER,
                Resource.HTTP_ROUTE,
                render_preview_route(
                    project,
                    workload.release_id,
                    preview_hostname(stack.subdomain, SITE_DOMAIN),
                    number,
                    preview_secret=PREVIEW_TOKEN,
                    site_domain=SITE_DOMAIN,
                    subdomain=stack.subdomain,
                ),
            )
        ],
        # The namespace as the bootstrap holds it (task F2B): the stack fixture has delivered the
        # placeholder Secret, so the mark is delivered. An apply without the mark, or back to
        # pending, would be refused by the policy, which is the intent.
        "render_namespace": [
            (
                BOOTSTRAP,
                Resource.NAMESPACE,
                render_namespace(
                    project, subdomain=stack.subdomain, catalog_secret=CATALOG_SECRET_DELIVERED
                ),
            )
        ],
        "render_resource_quota": [
            (BOOTSTRAP, Resource.RESOURCE_QUOTA, render_resource_quota(project, TENANT))
        ],
        "render_limit_range": [
            (BOOTSTRAP, Resource.LIMIT_RANGE, render_limit_range(project, TENANT))
        ],
        "render_reference_grant": [
            (BOOTSTRAP, Resource.REFERENCE_GRANT, render_reference_grant(project))
        ],
        "render_worker_role_binding": [
            (BOOTSTRAP, Resource.ROLE_BINDING, render_worker_role_binding(project))
        ],
        "render_runtime_secret": [
            (BOOTSTRAP, Resource.SECRET, render_runtime_secret(project, secret_values(stack)))
        ],
    }


def render_functions() -> set[str]:
    return {
        name
        for name, function in inspect.getmembers(render_module, inspect.isfunction)
        if name.startswith("render_") and function.__module__ == render_module.__name__
    }


def test_every_render_function_is_dry_run(stack: Stack) -> None:
    site = SiteSettings(
        cpu_millicores=100,
        memory_mib=128,
        progress_deadline_seconds=60,
        readiness_deadline_seconds=120,
        offline_service_port=8080,
    )
    assert set(rendered(stack, site)) == render_functions()


@pytest.mark.asyncio
async def test_every_rendered_kind_passes_a_server_side_dry_run_with_the_policies_on(
    stack: Stack,
    worker_api: KubeApi,
    bootstrap_api: KubeApi,
    bootstrap: KubernetesTenantBootstrap,
    site_settings: SiteSettings,
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    apis = {WORKER: worker_api, BOOTSTRAP: bootstrap_api}
    admitted: list[str] = []
    objects = rendered(stack, site_settings)
    for function, manifests in objects.items():
        for identity, resource, manifest in manifests:
            stored = await apis[identity].apply(resource, manifest, dry_run=True)
            # A custom resource the dry run would create comes back empty (KubeApi.apply); every
            # other answer is the object as the API server would store it.
            if stored or resource not in (Resource.HTTP_ROUTE, Resource.REFERENCE_GRANT):
                assert stored["metadata"]["name"] == manifest["metadata"]["name"]
            admitted.append(f"{function}: {manifest['kind']} {manifest['metadata']['name']}")
    print("admitted by dry run:", *admitted, sep="\n  ")

    # Nothing was stored: the fresh release's Deployment and Service do not exist.
    name = objects["render_deployment"][0][2]["metadata"]["name"]
    namespace = tenant_namespace(stack.project_id)
    assert await worker_api.read(Resource.DEPLOYMENT, name, namespace) is None
    assert await worker_api.read(Resource.SERVICE, name, namespace) is None


def _other_registry(manifest: dict[str, Any]) -> None:
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    container["image"] = "docker.io/" + container["image"].split("/", 1)[1]


def _no_fence(manifest: dict[str, Any]) -> None:
    del manifest["metadata"]["annotations"][ANNOTATION_COMMAND_NUMBER]


def _foreign_hostname(manifest: dict[str, Any]) -> None:
    manifest["spec"]["hostnames"] = [f"{uuid4()}.{SITE_DOMAIN}"]


def _other_subdomain(manifest: dict[str, Any]) -> None:
    """Another project's sub-domain host (decision 0023 G18): hostname takeover."""
    manifest["spec"]["hostnames"] = [f"other-shop.{SITE_DOMAIN}"]


def _project_id_hostname(manifest: dict[str, Any]) -> None:
    """The host of before the cut-over, ``<projectId>.<site domain>``: no longer the site's."""
    project = manifest["metadata"]["namespace"].removeprefix("wb-")
    manifest["spec"]["hostnames"] = [f"{project}.{SITE_DOMAIN}"]


def _subdomain_of_preview(manifest: dict[str, Any]) -> str:
    """The sub-domain of the rendered preview host ``<subdomain>-preview.<site domain>``."""
    label: str = manifest["spec"]["hostnames"][0].split(".", 1)[0]
    return label.removesuffix("-preview")


def _preview_on_the_site_host(manifest: dict[str, Any]) -> None:
    """The preview route on the site's own host instead of ``<subdomain>-preview.<site domain>``."""
    manifest["spec"]["hostnames"] = [f"{_subdomain_of_preview(manifest)}.{SITE_DOMAIN}"]


def _preview_on_the_old_preview_dash_host(manifest: dict[str, Any]) -> None:
    """The preview host of before decision 0023 G18b, ``preview-<subdomain>.<site domain>``: it
    could be another project's site host (Task 12 review I2), so it is refused."""
    manifest["spec"]["hostnames"] = [f"preview-{_subdomain_of_preview(manifest)}.{SITE_DOMAIN}"]


def _preview_on_the_old_dotted_host(manifest: dict[str, Any]) -> None:
    """FR-20 (PREVIEW1): the preview host of decision 0023 G18b,
    ``<subdomain>.preview.<site domain>``, is refused, never served."""
    manifest["spec"]["hostnames"] = [f"{_subdomain_of_preview(manifest)}.preview.{SITE_DOMAIN}"]


@pytest.mark.parametrize(
    ("function", "change"),
    [
        ("render_deployment", _other_registry),
        ("render_service", _no_fence),
        ("render_route", _foreign_hostname),
        ("render_route", _other_subdomain),
        ("render_route", _project_id_hostname),
        ("render_preview_route", _preview_on_the_site_host),
        ("render_preview_route", _preview_on_the_old_preview_dash_host),
        ("render_preview_route", _preview_on_the_old_dotted_host),
    ],
    ids=[
        "image-from-another-registry",
        "service-without-fence",
        "route-of-another-project",
        "route-on-another-subdomain",
        "route-on-the-project-id-host",
        "preview-on-the-site-host",
        "preview-on-the-old-preview-dash-host",
        "fr20-preview-on-the-old-dotted-preview-host",
    ],
)
@pytest.mark.asyncio
async def test_the_policies_refuse_a_changed_object_in_the_same_dry_run(
    stack: Stack,
    worker_api: KubeApi,
    bootstrap: KubernetesTenantBootstrap,
    site_settings: SiteSettings,
    function: str,
    change: Callable[[dict[str, Any]], None],
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    _, resource, manifest = rendered(stack, site_settings)[function][0]
    change(manifest)

    with pytest.raises(ClusterRefused):
        await worker_api.apply(resource, manifest, dry_run=True)
