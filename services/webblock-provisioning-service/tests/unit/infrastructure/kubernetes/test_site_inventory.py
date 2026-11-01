"""The wave 6 port methods of KubernetesSiteCluster against the local fake API server (task W6-M):
``list_tenant_projects``, ``inventory`` (the fence and the foreign field managers read from
``managedFields``) and ``reclaim_route`` (the one forced server-side apply, decision 0014
section 23 item 3)."""

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import pytest
import pytest_asyncio

from provisioning.core.application.ports.site_cluster import (
    ClusterRefused,
    ClusterUnavailable,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    SiteInventory,
    SiteObject,
    SiteObjectKind,
    TenantNotReady,
)
from provisioning.infrastructure.kubernetes import WORKER_FIELD_MANAGER, KubeApi
from provisioning.infrastructure.kubernetes.render import (
    SiteSettings,
    render_deployment,
    render_route,
    render_service,
)
from provisioning.infrastructure.kubernetes.site_cluster import (
    KubernetesSiteCluster,
    SiteClusterSettings,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import FakeApiServer, Json

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
RELEASE = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")
OTHER = UUID("9c8b7a69-5847-4362-9150-0a1b2c3d4e5f")
NS = f"wb-{PROJECT}"
IMAGE = f"registry.webblock.test:5000/webblock/sites/{PROJECT}@sha256:" + "ab" * 32
SUBDOMAIN = "unit-shop"  # project_hosting.subdomain (decision 0023 G18)
HOST = f"{SUBDOMAIN}.sites.webblock.test"
NAMESPACES = "/api/v1/namespaces"
DEPLOYMENTS = f"/apis/apps/v1/namespaces/{NS}/deployments"
SERVICES = f"/api/v1/namespaces/{NS}/services"
ROUTES = f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes"
ROUTE = f"{ROUTES}/route-{PROJECT}"
SITE = SiteSettings(
    cpu_millicores=100,
    memory_mib=128,
    progress_deadline_seconds=60,
    readiness_deadline_seconds=120,
    offline_service_port=80,
)
SETTINGS = SiteClusterSettings(
    site=SITE,
    gateway_url="http://gateway.webblock.test:8080",
    probe_path="/readyz",
    probe_timeout_seconds=2.0,
    delete_retry_base_seconds=0.0,
    site_domain="sites.webblock.test",
    preview_token="preview-token-for-unit-tests-0123456789",  # noqa: S106 - a test value
)
TO_RELEASE = RouteTarget(RouteTargetKind.RELEASE, RELEASE)


def refuse_probe(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("no probe expected in this test")


@pytest_asyncio.fixture
async def cluster(worker_api: KubeApi) -> AsyncIterator[KubernetesSiteCluster]:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(refuse_probe)) as client:
        yield KubernetesSiteCluster(worker_api, SETTINGS, client)


def entry(manager: str, fields: Json, operation: str = "Apply", **extra: str) -> Json:
    return {
        "manager": manager,
        "operation": operation,
        "apiVersion": "v1",
        "time": "2026-10-09T10:00:00Z",
        "fieldsType": "FieldsV1",
        "fieldsV1": fields,
        **extra,
    }


SPEC_FIELDS: Json = {"f:spec": {"f:replicas": {}}}
REVISION_ONLY: Json = {
    "f:metadata": {"f:annotations": {".": {}, "f:deployment.kubernetes.io/revision": {}}}
}


def stored(obj: Json, *managed: Json) -> Json:
    out: Json = json.loads(json.dumps(obj))
    out["metadata"]["managedFields"] = list(managed) or [entry(WORKER_FIELD_MANAGER, SPEC_FIELDS)]
    return out


def namespace(name: str, tenant: str = "true") -> Json:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name, "labels": {"websites.webblock.internal/tenant": tenant}},
    }


# --- list_tenant_projects -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_projects_are_the_labelled_namespaces_whose_names_parse(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.put_list(
        NAMESPACES,
        [
            namespace(f"wb-{OTHER}"),
            namespace(f"wb-{PROJECT}"),
            namespace("wb-not-a-uuid"),
            namespace(f"wb-{str(PROJECT).upper()}"),
            namespace(f"wb-{PROJECT.hex}"),
            namespace(f"xx-{PROJECT}"),
            namespace(f"wb-{PROJECT}-extra"),
        ],
    )

    assert await cluster.list_tenant_projects() == (PROJECT, OTHER)
    (get,) = fake.seen("GET")
    assert get.path == NAMESPACES
    assert get.query["labelSelector"] == "websites.webblock.internal/tenant=true"
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_no_tenant_is_an_empty_tuple(cluster: KubernetesSiteCluster) -> None:
    assert await cluster.list_tenant_projects() == ()


@pytest.mark.parametrize("code", [403, 500, 429])
@pytest.mark.asyncio
async def test_a_refused_or_failed_namespace_list_is_cluster_unavailable(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, code: int
) -> None:
    fake.script("GET", NAMESPACES, (code, fake.status(code, "Failure", {"kind": "namespaces"})))

    with pytest.raises(ClusterUnavailable):
        await cluster.list_tenant_projects()


# --- inventory ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inventory_lists_the_three_kinds_with_fence_and_foreign_managers(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    workload = ReleaseWorkload(PROJECT, RELEASE, IMAGE, 7)
    deployment = stored(
        render_deployment(workload, SITE),
        entry(WORKER_FIELD_MANAGER, SPEC_FIELDS),
        entry("kube-controller-manager", REVISION_ONLY, "Update"),
        entry("kube-controller-manager", {"f:status": {}}, "Update", subresource="status"),
    )
    edited = stored(
        render_service(ReleaseWorkload(PROJECT, OTHER, IMAGE, 9)),
        entry(WORKER_FIELD_MANAGER, SPEC_FIELDS),
        entry("kubectl-patch", {"f:spec": {"f:ports": {}}}, "Update"),
        entry("webblock-test-foreign", SPEC_FIELDS),
        entry("kubectl-patch", {"f:metadata": {"f:labels": {}}}, "Update"),
    )
    edited["metadata"]["annotations"]["websites.webblock.internal/command-number"] = "nine"
    route = stored(
        render_route(
            PROJECT,
            TO_RELEASE,
            (HOST,),
            8,
            SITE,
            site_domain=SETTINGS.site_domain,
            subdomain=SUBDOMAIN,
        )
    )
    stray = {"metadata": {"name": "hand-made", "namespace": NS, "managedFields": []}}
    fake.put_list(DEPLOYMENTS, [deployment])
    fake.put_list(SERVICES, [edited, stray])
    fake.put_list(ROUTES, [route])

    inventory = await cluster.inventory(PROJECT)

    assert inventory == SiteInventory(
        PROJECT,
        (
            SiteObject(SiteObjectKind.DEPLOYMENT, f"rel-{RELEASE}", RELEASE, 7, ()),
            SiteObject(SiteObjectKind.SERVICE, "hand-made", None, None, ()),
            SiteObject(
                SiteObjectKind.SERVICE,
                f"rel-{OTHER}",
                OTHER,
                -1,
                ("kubectl-patch", "webblock-test-foreign"),
            ),
            SiteObject(SiteObjectKind.HTTP_ROUTE, f"route-{PROJECT}", None, 8, ()),
        ),
    )
    assert {r.path for r in fake.seen("GET")} == {DEPLOYMENTS, SERVICES, ROUTES}
    assert all("labelSelector" not in r.query for r in fake.seen("GET"))
    assert fake.mutations() == []


@pytest.mark.parametrize(
    ("name", "release"),
    [
        (f"rel-{RELEASE}", RELEASE),
        (f"rel-{str(RELEASE).upper()}", None),
        ("rel-not-a-uuid", None),
        (f"rel-{RELEASE.hex}", None),
        (f"route-{RELEASE}", None),
    ],
)
@pytest.mark.asyncio
async def test_only_a_canonical_rel_uuid_name_names_a_release(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, name: str, release: UUID | None
) -> None:
    fake.put_list(DEPLOYMENTS, [{"metadata": {"name": name, "namespace": NS}}])

    (only,) = (await cluster.inventory(PROJECT)).objects
    assert only.release_id == release


@pytest.mark.asyncio
async def test_a_controller_entry_that_owns_more_than_the_revision_is_foreign(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fields: Json = {"f:metadata": {"f:annotations": {"f:deployment.kubernetes.io/revision": {}}}}
    fields["f:spec"] = {"f:replicas": {}}
    deployment = stored(
        render_deployment(ReleaseWorkload(PROJECT, RELEASE, IMAGE, 7), SITE),
        entry(WORKER_FIELD_MANAGER, SPEC_FIELDS),
        entry("kube-controller-manager", fields, "Update"),
    )
    fake.put_list(DEPLOYMENTS, [deployment])

    (only,) = (await cluster.inventory(PROJECT)).objects
    assert only.foreign_managers == ("kube-controller-manager",)


@pytest.mark.parametrize("code", [403, 404])
@pytest.mark.asyncio
async def test_inventory_of_a_missing_tenant_is_tenant_not_ready(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, code: int
) -> None:
    details = {"kind": "deployments"}
    fake.script("GET", DEPLOYMENTS, (code, fake.status(code, "Failure", details)))

    with pytest.raises(TenantNotReady):
        await cluster.inventory(PROJECT)


@pytest.mark.asyncio
async def test_inventory_when_the_api_server_fails_is_cluster_unavailable(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.script("GET", ROUTES, (503, fake.status(503, "ServiceUnavailable")))

    with pytest.raises(ClusterUnavailable):
        await cluster.inventory(PROJECT)


# --- reclaim_route ------------------------------------------------------------------------------


def foreign_route(number: str) -> Json:
    route = render_route(
        PROJECT,
        RouteTarget(RouteTargetKind.OFFLINE),
        (HOST,),
        3,
        SITE,
        site_domain="sites.webblock.test",
        subdomain=SUBDOMAIN,
    )
    route["metadata"]["annotations"]["websites.webblock.internal/command-number"] = number
    return stored(route, entry("kubectl-patch", SPEC_FIELDS, "Update"))


@pytest.mark.parametrize("number", ["999999", "not-a-number", "8"])
@pytest.mark.asyncio
async def test_reclaim_route_force_applies_the_rendered_route_over_any_fence(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, number: str
) -> None:
    fake.put(ROUTE, foreign_route(number))

    await cluster.reclaim_route(PROJECT, TO_RELEASE, (HOST,), 8, subdomain=SUBDOMAIN)

    (patch,) = fake.seen("PATCH")
    assert patch.path == ROUTE
    assert patch.query["force"] == "true"
    assert patch.query["fieldManager"] == WORKER_FIELD_MANAGER
    assert patch.body == render_route(
        PROJECT, TO_RELEASE, (HOST,), 8, SITE, site_domain=SETTINGS.site_domain, subdomain=SUBDOMAIN
    )
    assert fake.objects[ROUTE]["metadata"]["annotations"] == {
        "websites.webblock.internal/command-number": "8"
    }


@pytest.mark.asyncio
async def test_reclaim_route_does_not_read_the_fence_first(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    await cluster.reclaim_route(PROJECT, TO_RELEASE, (HOST,), 8, subdomain=SUBDOMAIN)

    assert [r.method for r in fake.requests] == ["PATCH"]


@pytest.mark.parametrize(
    ("code", "details", "error"),
    [
        (404, None, TenantNotReady),
        (403, {"kind": "httproutes"}, TenantNotReady),
        (
            403,
            {"kind": "httproutes", "causes": [{"reason": "FieldValueForbidden"}]},
            ClusterRefused,
        ),
        (422, None, ClusterRefused),
        (409, None, ClusterUnavailable),
        (503, None, ClusterUnavailable),
    ],
    ids=["no-namespace", "no-rolebinding", "admission", "invalid", "conflict", "unavailable"],
)
@pytest.mark.asyncio
async def test_reclaim_route_errors_never_name_a_conflict_or_a_fence(
    fake: FakeApiServer,
    cluster: KubernetesSiteCluster,
    code: int,
    details: dict[str, Any] | None,
    error: type[Exception],
) -> None:
    fake.script("PATCH", ROUTE, (code, fake.status(code, "Failure", details)))

    with pytest.raises(error):
        await cluster.reclaim_route(PROJECT, TO_RELEASE, (HOST,), 8, subdomain=SUBDOMAIN)


@pytest.mark.asyncio
async def test_reclaim_route_refuses_a_hostname_of_another_project_before_any_call(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    with pytest.raises(ValueError, match="hostname"):
        await cluster.reclaim_route(
            PROJECT, TO_RELEASE, ("other-shop.sites.webblock.test",), 8, subdomain=SUBDOMAIN
        )
    assert fake.requests == []
