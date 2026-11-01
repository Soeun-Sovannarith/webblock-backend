"""KubernetesSiteCluster against the local fake API server: the fence check before every apply,
the acknowledgement of a release (review-k8s-nocp.md M1), the guarded delete with the F2 retry
(decision 0011 section 12, review-k8s-nocp.md M2), the route's status, and the site probe."""

import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx2
import pytest
import pytest_asyncio

from provisioning.core.application.ports.site_cluster import (
    OFFLINE_HEADER_VALUE,
    RELEASE_HEADER,
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    SiteDependencyUnavailable,
    TenantNotReady,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.infrastructure.kubernetes import KubeApi
from provisioning.infrastructure.kubernetes.render import (
    SiteSettings,
    preview_token_for,
    render_deployment,
    render_preview_route,
    render_route,
    render_service,
)
from provisioning.infrastructure.kubernetes.site_cluster import (
    KubernetesSiteCluster,
    SiteClusterSettings,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import FakeApiServer, Json

FIXTURES = Path(__file__).parent / "fixtures" / "status"
PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
RELEASE = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")
OTHER = UUID("9c8b7a69-5847-4362-9150-0a1b2c3d4e5f")
NS = f"wb-{PROJECT}"
IMAGE = f"registry.webblock.test:5000/webblock/sites/{PROJECT}@sha256:" + "ab" * 32
DEPLOYMENT = f"/apis/apps/v1/namespaces/{NS}/deployments/rel-{RELEASE}"
SERVICE = f"/api/v1/namespaces/{NS}/services/rel-{RELEASE}"
ROUTE = f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes/route-{PROJECT}"
PREVIEW = f"/apis/gateway.networking.k8s.io/v1/namespaces/{NS}/httproutes/preview-{PROJECT}"
PODS = f"/api/v1/namespaces/{NS}/pods"
SLICES = f"/apis/discovery.k8s.io/v1/namespaces/{NS}/endpointslices"
# The site's DNS label, from project_hosting.subdomain (decision 0023 G18).
SUBDOMAIN = "unit-shop"
HOST = f"{SUBDOMAIN}.sites.webblock.test"
PREVIEW_HOST = f"{SUBDOMAIN}-preview.sites.webblock.test"
TO_RELEASE = RouteTarget(RouteTargetKind.RELEASE, RELEASE)
TO_OFFLINE = RouteTarget(RouteTargetKind.OFFLINE)
GATEWAY = "http://gateway.webblock.test:8080"
SITE = SiteSettings(
    cpu_millicores=100,
    memory_mib=128,
    progress_deadline_seconds=60,
    readiness_deadline_seconds=120,
    offline_service_port=80,
)
TOKEN = "preview-token-for-unit-tests-0123456789"  # noqa: S105 - a test value
SETTINGS = SiteClusterSettings(
    site=SITE,
    gateway_url=GATEWAY,
    probe_path="/readyz",
    probe_timeout_seconds=2.0,
    delete_retry_base_seconds=0.0,
    site_domain="sites.webblock.test",
    preview_token=TOKEN,
)


def workload(number: int = 7) -> ReleaseWorkload:
    return ReleaseWorkload(PROJECT, RELEASE, IMAGE, number)


def stored(obj: Json, number: int | None, uid: str = "uid-1", version: str = "10") -> Json:
    """The object as the API server returns it, with the given command number."""
    out: Json = json.loads(json.dumps(obj))
    meta = out["metadata"]
    meta.update({"uid": uid, "resourceVersion": version, "generation": 1})
    if number is None:
        meta.pop("annotations", None)
    else:
        meta.setdefault("annotations", {})["websites.webblock.internal/command-number"] = str(
            number
        )
    return out


def route_to(
    target: RouteTarget,
    number: int = 7,
    generation: int = 1,
    *,
    preview: bool = False,
    **conditions: str,
) -> Json:
    rendered = (
        render_preview_route(
            PROJECT,
            target.release_id,
            PREVIEW_HOST,
            number,
            preview_secret=TOKEN,
            site_domain="sites.webblock.test",
            subdomain=SUBDOMAIN,
        )
        if preview and target.release_id is not None
        else render_route(
            PROJECT,
            target,
            (HOST,),
            number,
            SITE,
            site_domain="sites.webblock.test",
            subdomain=SUBDOMAIN,
        )
    )
    route = stored(rendered, number)
    route["metadata"]["generation"] = generation
    accepted = conditions.get("accepted", "True")
    resolved = conditions.get("resolved", "True")
    observed = int(conditions.get("observed", str(generation)))
    gateway = conditions.get("gateway", "webblock-public")
    route["status"] = {
        "parents": [
            {
                "parentRef": {
                    "group": "gateway.networking.k8s.io",
                    "kind": "Gateway",
                    "namespace": "webblock-gateway",
                    "name": gateway,
                },
                "controllerName": "io.cilium/gateway-controller",
                "conditions": [
                    {
                        "type": "Accepted",
                        "status": accepted,
                        "reason": "Accepted",
                        "observedGeneration": observed,
                        "lastTransitionTime": "2026-10-08T10:00:00Z",
                        "message": "Accepted HTTPRoute",
                    },
                    {
                        "type": "ResolvedRefs",
                        "status": resolved,
                        "reason": "ResolvedRefs",
                        "observedGeneration": observed,
                        "lastTransitionTime": "2026-10-08T10:00:00Z",
                        "message": "Service reference is valid",
                    },
                ],
            }
        ]
    }
    return route


type Handler = Callable[[httpx2.Request], httpx2.Response]


def refuse_probe(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError("no probe expected in this test")


@pytest_asyncio.fixture
async def http() -> AsyncIterator[list[Handler]]:
    """The handler the site cluster's HTTP client uses; tests replace element 0."""
    yield [refuse_probe]


@pytest_asyncio.fixture
async def cluster(worker_api: KubeApi, http: list[Handler]) -> AsyncIterator[KubernetesSiteCluster]:
    transport = httpx2.MockTransport(lambda request: http[0](request))
    async with httpx2.AsyncClient(transport=transport) as client:
        yield KubernetesSiteCluster(worker_api, SETTINGS, client)


# --- apply_release and its fence ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_release_applies_the_rendered_deployment_then_service(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    await cluster.apply_release(workload())

    patches = fake.seen("PATCH")
    assert [p.path for p in patches] == [DEPLOYMENT, SERVICE]
    assert patches[0].body == render_deployment(workload(), SITE)
    assert patches[1].body == render_service(workload())


@pytest.mark.parametrize("stored_number", [None, 3, 7])
@pytest.mark.asyncio
async def test_apply_release_proceeds_over_the_same_or_a_lower_command_number(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, stored_number: int | None
) -> None:
    fake.put(DEPLOYMENT, stored(render_deployment(workload(), SITE), stored_number))
    fake.put(SERVICE, stored(render_service(workload()), stored_number))

    await cluster.apply_release(workload(7))

    assert [p.path for p in fake.seen("PATCH")] == [DEPLOYMENT, SERVICE]


@pytest.mark.parametrize("ahead", ["deployment", "service"])
@pytest.mark.asyncio
async def test_apply_release_over_a_higher_command_number_is_fence_rejected_before_any_write(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, ahead: str
) -> None:
    fake.put(
        DEPLOYMENT,
        stored(render_deployment(workload(), SITE), 8 if ahead == "deployment" else 7),
    )
    fake.put(SERVICE, stored(render_service(workload()), 8 if ahead == "service" else 7))

    with pytest.raises(FenceRejected):
        await cluster.apply_release(workload(7))

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_a_malformed_stored_command_number_is_fence_rejected(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    deployment = stored(render_deployment(workload(), SITE), 7)
    deployment["metadata"]["annotations"]["websites.webblock.internal/command-number"] = "x9"
    fake.put(DEPLOYMENT, deployment)

    with pytest.raises(FenceRejected):
        await cluster.apply_release(workload(7))
    assert fake.mutations() == []


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (403, {"kind": "deployments", "group": "apps"}, TenantNotReady),
        (404, {"kind": "namespaces", "name": NS}, TenantNotReady),
        (
            409,
            {"kind": "deployments", "causes": [{"reason": "FieldManagerConflict"}]},
            ApplyConflict,
        ),
        (422, {"kind": "deployments", "causes": [{"reason": "FieldValueInvalid"}]}, ClusterRefused),
        (400, None, ClusterRefused),
        (403, {"kind": "deployments", "causes": [{"reason": "Denied"}]}, ClusterRefused),
    ],
)
@pytest.mark.asyncio
async def test_apply_release_maps_a_missing_tenant_a_conflict_and_a_refusal(
    fake: FakeApiServer,
    cluster: KubernetesSiteCluster,
    code: int,
    details: Json,
    expected: type[Exception],
) -> None:
    fake.script("PATCH", DEPLOYMENT, (code, fake.status(code, "Whatever", details)))

    with pytest.raises(expected):
        await cluster.apply_release(workload())


@pytest.mark.asyncio
async def test_a_forbidden_read_means_the_tenant_is_not_ready(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.script("GET", DEPLOYMENT, (403, fake.status(403, "Forbidden", {"kind": "deployments"})))

    with pytest.raises(TenantNotReady):
        await cluster.apply_release(workload())


# --- release_status: the acknowledgement ---------------------------------------------------------


def status_fixtures() -> list[Any]:
    """Each fixture file, read at collection time."""
    return [
        pytest.param(json.loads(p.read_text()), id=p.stem) for p in sorted(FIXTURES.glob("*.json"))
    ]


@pytest.mark.parametrize("case", status_fixtures())
@pytest.mark.asyncio
async def test_release_status_from_recorded_shapes(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, case: Json
) -> None:
    fake.put(DEPLOYMENT, case["deployment"])
    fake.put_list(SLICES, case["endpointslices"])
    fake.put_list(PODS, case["pods"])
    expected = case["expected"]

    status = await cluster.release_status(workload())

    assert status == WorkloadStatus(WorkloadState(expected["state"]), expected["reason"])


def test_the_status_fixtures_cover_every_state() -> None:
    states = {json.loads(p.read_text())["expected"]["state"] for p in FIXTURES.glob("*.json")}
    assert states == {"ready", "progressing", "failed"}


# The H5a fixtures plant this in every condition message (task H5a): it must never leave the
# adapter, neither in the status nor in a log line.
PLANTED_SECRET = "hunter2-SECRET-TOKEN-0f9e8d"  # noqa: S105 - a planted test value


def _planted_fixtures() -> list[Any]:
    return [p for p in status_fixtures() if PLANTED_SECRET in json.dumps(p.values[0])]


def test_the_precise_cause_fixtures_carry_the_planted_secret() -> None:
    assert len(_planted_fixtures()) == 8


@pytest.mark.parametrize("case", _planted_fixtures())
@pytest.mark.asyncio
async def test_a_condition_message_never_leaves_the_adapter(
    fake: FakeApiServer,
    cluster: KubernetesSiteCluster,
    case: Json,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake.put(DEPLOYMENT, case["deployment"])
    fake.put_list(SLICES, case["endpointslices"])
    fake.put_list(PODS, case["pods"])
    caplog.set_level("DEBUG")

    status = await cluster.release_status(workload())

    assert PLANTED_SECRET not in repr(status)
    assert "exceeded quota" not in repr(status)
    # Our own log lines only: kubernetes_asyncio logs whole response bodies at DEBUG, which the
    # service never enables (reported by task H5a).
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("provisioning")]
    assert not any(PLANTED_SECRET in line for line in ours)


@pytest.mark.asyncio
async def test_release_status_reads_slices_and_pods_by_label(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    case = json.loads((FIXTURES / "starting.json").read_text())
    fake.put(DEPLOYMENT, case["deployment"])

    await cluster.release_status(workload())

    queries = {r.path: r.query.get("labelSelector") for r in fake.seen("GET")}
    assert queries[SLICES] == f"kubernetes.io/service-name=rel-{RELEASE}"
    assert queries[PODS] == (
        f"websites.webblock.internal/project-id={PROJECT},"
        f"websites.webblock.internal/release-id={RELEASE}"
    )


@pytest.mark.asyncio
async def test_release_status_without_a_deployment_is_missing(
    cluster: KubernetesSiteCluster,
) -> None:
    assert await cluster.release_status(workload()) == WorkloadStatus(WorkloadState.MISSING)


@pytest.mark.asyncio
async def test_release_status_compares_the_digest_with_the_configured_resources(
    fake: FakeApiServer, worker_api: KubeApi
) -> None:
    case = json.loads((FIXTURES / "ready.json").read_text())
    fake.put(DEPLOYMENT, case["deployment"])
    fake.put_list(SLICES, case["endpointslices"])
    fake.put_list(PODS, case["pods"])
    more_memory = SiteClusterSettings(
        site=SiteSettings(
            cpu_millicores=100,
            memory_mib=256,
            progress_deadline_seconds=60,
            readiness_deadline_seconds=120,
            offline_service_port=80,
        ),
        gateway_url=GATEWAY,
        probe_path="/readyz",
        probe_timeout_seconds=2.0,
        delete_retry_base_seconds=0.0,
        site_domain="sites.webblock.test",
        preview_token=TOKEN,
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(refuse_probe)) as client:
        cluster = KubernetesSiteCluster(worker_api, more_memory, client)
        status = await cluster.release_status(workload())

    assert status == WorkloadStatus(WorkloadState.PROGRESSING)


def put_ready(fake: FakeApiServer) -> None:
    """The ready.json shapes: everything READY needs, at command number 7 and IMAGE."""
    case = json.loads((FIXTURES / "ready.json").read_text())
    fake.put(DEPLOYMENT, case["deployment"])
    fake.put_list(SLICES, case["endpointslices"])
    fake.put_list(PODS, case["pods"])


@pytest.mark.asyncio
async def test_release_status_is_ready_at_the_same_fence_with_every_fact(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_ready(fake)

    assert await cluster.release_status(workload(7)) == WorkloadStatus(WorkloadState.READY)


@pytest.mark.asyncio
async def test_release_status_of_another_image_is_progressing(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_ready(fake)
    other_image = IMAGE.replace("ab" * 32, "cd" * 32)

    status = await cluster.release_status(ReleaseWorkload(PROJECT, RELEASE, other_image, 7))

    assert status == WorkloadStatus(WorkloadState.PROGRESSING)


@pytest.mark.asyncio
async def test_release_status_of_a_template_running_another_image_is_progressing(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    # The digest annotation still names this workload, but the container runs another image.
    put_ready(fake)
    template = fake.objects[DEPLOYMENT]["spec"]["template"]
    template["spec"]["containers"][0]["image"] = IMAGE.replace("ab" * 32, "cd" * 32)

    assert await cluster.release_status(workload(7)) == WorkloadStatus(WorkloadState.PROGRESSING)


@pytest.mark.asyncio
async def test_release_status_below_the_commands_fence_is_progressing(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_ready(fake)

    assert await cluster.release_status(workload(8)) == WorkloadStatus(WorkloadState.PROGRESSING)


@pytest.mark.asyncio
async def test_release_status_without_a_fence_annotation_is_progressing(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_ready(fake)
    del fake.objects[DEPLOYMENT]["metadata"]["annotations"][
        "websites.webblock.internal/command-number"
    ]

    assert await cluster.release_status(workload(7)) == WorkloadStatus(WorkloadState.PROGRESSING)


@pytest.mark.asyncio
async def test_release_status_with_an_unavailable_replica_is_progressing(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    """A rolling restart (decision 0023 G12) is done only when no replica is unavailable."""
    put_ready(fake)
    fake.objects[DEPLOYMENT]["status"]["unavailableReplicas"] = 1

    assert await cluster.release_status(workload(7)) == WorkloadStatus(WorkloadState.PROGRESSING)


# --- restart_release: the redeploy's rolling restart (decision 0023 G12) -------------------------


@pytest.mark.parametrize("stored_number", [None, 3, 7])
@pytest.mark.asyncio
async def test_restart_release_applies_the_deployment_with_the_restart_annotation_and_the_fence(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, stored_number: int | None
) -> None:
    fake.put(DEPLOYMENT, stored(render_deployment(workload(), SITE), stored_number))
    fake.put(SERVICE, stored(render_service(workload()), stored_number))

    await cluster.restart_release(workload(9))

    patches = fake.seen("PATCH")
    assert [p.path for p in patches] == [DEPLOYMENT, SERVICE]
    assert patches[0].body == render_deployment(workload(9), SITE, restart_command=9)
    template = patches[0].body["spec"]["template"]["metadata"]["annotations"]
    assert template["websites.webblock.internal/restart-command"] == "9"
    assert patches[0].body["metadata"]["annotations"] == {
        "websites.webblock.internal/command-number": "9"
    }
    assert patches[1].body == render_service(workload(9))
    assert all(p.query.get("force") in (None, "false") for p in patches)


@pytest.mark.parametrize("ahead", ["deployment", "service"])
@pytest.mark.asyncio
async def test_restart_release_over_a_higher_command_number_is_fence_rejected_before_any_write(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, ahead: str
) -> None:
    fake.put(
        DEPLOYMENT,
        stored(render_deployment(workload(), SITE), 10 if ahead == "deployment" else 7),
    )
    fake.put(SERVICE, stored(render_service(workload()), 10 if ahead == "service" else 7))

    with pytest.raises(FenceRejected):
        await cluster.restart_release(workload(9))

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_restart_release_without_the_tenant_is_tenant_not_ready(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.script("GET", DEPLOYMENT, (403, fake.status(403, "Forbidden", {"kind": "deployments"})))

    with pytest.raises(TenantNotReady):
        await cluster.restart_release(workload(9))


@pytest.mark.parametrize("stored_number", ["8", "x9"])
@pytest.mark.asyncio
async def test_release_status_above_the_commands_fence_is_fence_rejected(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, stored_number: str
) -> None:
    put_ready(fake)
    annotations = fake.objects[DEPLOYMENT]["metadata"]["annotations"]
    annotations["websites.webblock.internal/command-number"] = stored_number

    with pytest.raises(FenceRejected):
        await cluster.release_status(workload(7))


# --- delete_release: guarded, with the F2 retry ---------------------------------------------------


def put_release(fake: FakeApiServer, number: int = 7) -> None:
    fake.put(DEPLOYMENT, stored(render_deployment(workload(), SITE), number, "uid-d", "10"))
    fake.put(SERVICE, stored(render_service(workload()), number, "uid-s", "20"))


@pytest.mark.asyncio
async def test_delete_release_deletes_the_deployment_then_the_service_with_preconditions(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_release(fake)
    fake.put(ROUTE, route_to(RouteTarget(RouteTargetKind.RELEASE, OTHER)))

    await cluster.delete_release(PROJECT, RELEASE, 7)

    deletes = fake.seen("DELETE")
    assert [d.path for d in deletes] == [DEPLOYMENT, SERVICE]
    assert deletes[0].body["preconditions"] == {"uid": "uid-d", "resourceVersion": "10"}
    assert deletes[1].body["preconditions"] == {"uid": "uid-s", "resourceVersion": "20"}
    assert DEPLOYMENT not in fake.objects
    assert SERVICE not in fake.objects


@pytest.mark.asyncio
async def test_delete_release_refuses_the_release_the_route_points_at(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_release(fake)
    fake.put(ROUTE, route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE)))

    with pytest.raises(FenceRejected):
        await cluster.delete_release(PROJECT, RELEASE, 7)

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_delete_release_refuses_an_object_with_a_higher_command_number(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_release(fake, number=9)

    with pytest.raises(FenceRejected):
        await cluster.delete_release(PROJECT, RELEASE, 7)

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_delete_release_of_missing_objects_is_not_an_error(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    await cluster.delete_release(PROJECT, RELEASE, 7)

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_a_lost_precondition_starts_again_from_the_first_check_with_the_new_version(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    # The Deployment controller writes status during a rollout, so each read sees a new
    # resourceVersion and the first two deletes lose their precondition (F2).
    put_release(fake)
    base = stored(render_deployment(workload(), SITE), 7, "uid-d")
    reads = []
    for version in ("10", "11", "12"):
        reads.append((200, {**base, "metadata": {**base["metadata"], "resourceVersion": version}}))
    fake.script("GET", DEPLOYMENT, *reads)
    conflict = fake.status(409, "Conflict", {"kind": "deployments"})
    fake.script("DELETE", DEPLOYMENT, (409, conflict), (409, conflict))

    await cluster.delete_release(PROJECT, RELEASE, 7)

    deletes = [d for d in fake.seen("DELETE") if d.path == DEPLOYMENT]
    assert [d.body["preconditions"]["resourceVersion"] for d in deletes] == ["10", "11", "12"]
    assert len([r for r in fake.seen("GET") if r.path == ROUTE]) == 3
    assert [d.path for d in fake.seen("DELETE")][-1] == SERVICE


@pytest.mark.asyncio
async def test_five_lost_preconditions_end_the_delete_as_cluster_unavailable(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_release(fake)
    conflict = fake.status(409, "Conflict", {"kind": "deployments"})
    fake.script("DELETE", DEPLOYMENT, *[(409, conflict)] * 6)

    with pytest.raises(ClusterUnavailable):
        await cluster.delete_release(PROJECT, RELEASE, 7)

    assert [d.path for d in fake.seen("DELETE")] == [DEPLOYMENT] * 5


@pytest.mark.asyncio
async def test_every_pass_checks_the_route_again(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    put_release(fake)
    elsewhere = route_to(RouteTarget(RouteTargetKind.RELEASE, OTHER))
    here = route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), number=8)
    fake.script("GET", ROUTE, (200, elsewhere), (200, here))
    fake.script("DELETE", DEPLOYMENT, (409, fake.status(409, "Conflict")))

    with pytest.raises(FenceRejected):
        await cluster.delete_release(PROJECT, RELEASE, 7)

    assert len(fake.seen("DELETE")) == 1
    assert DEPLOYMENT in fake.objects


# --- apply_route and route_status ---------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [RouteTarget(RouteTargetKind.RELEASE, RELEASE), RouteTarget(RouteTargetKind.OFFLINE)],
)
@pytest.mark.asyncio
async def test_apply_route_applies_the_rendered_route(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, target: RouteTarget
) -> None:
    await cluster.apply_route(PROJECT, target, (HOST,), 7, subdomain=SUBDOMAIN)

    (patch,) = fake.seen("PATCH")
    assert patch.path == ROUTE
    assert patch.body == render_route(
        PROJECT, target, (HOST,), 7, SITE, site_domain="sites.webblock.test", subdomain=SUBDOMAIN
    )


@pytest.mark.asyncio
async def test_apply_route_over_a_higher_command_number_is_fence_rejected(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.put(ROUTE, route_to(RouteTarget(RouteTargetKind.OFFLINE), number=9))

    with pytest.raises(FenceRejected):
        await cluster.apply_route(
            PROJECT, RouteTarget(RouteTargetKind.RELEASE, RELEASE), (HOST,), 7, subdomain=SUBDOMAIN
        )

    assert fake.mutations() == []


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (None, RouteStatus(accepted=False, target=None, command_number=None)),
        (
            route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7, generation=2),
            RouteStatus(True, RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7),
        ),
        (
            route_to(RouteTarget(RouteTargetKind.OFFLINE), 8),
            RouteStatus(True, RouteTarget(RouteTargetKind.OFFLINE), 8),
        ),
        (
            route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7, generation=2, observed="1"),
            RouteStatus(False, RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7),
        ),
        (
            route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7, resolved="False"),
            RouteStatus(False, RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7),
        ),
        (
            route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7, accepted="False"),
            RouteStatus(False, RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7),
        ),
        (
            route_to(RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7, gateway="other-gateway"),
            RouteStatus(False, RouteTarget(RouteTargetKind.RELEASE, RELEASE), 7),
        ),
    ],
    ids=["absent", "release", "offline", "old-generation", "unresolved", "refused", "other-gw"],
)
@pytest.mark.asyncio
async def test_route_status(
    fake: FakeApiServer,
    cluster: KubernetesSiteCluster,
    route: Json | None,
    expected: RouteStatus,
) -> None:
    if route is not None:
        fake.put(ROUTE, route)

    assert await cluster.route_status(PROJECT) == expected


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        (route_to(TO_RELEASE, 7), RouteStatus(True, TO_RELEASE, 7)),
        (
            route_to(TO_RELEASE, 7, generation=2, observed="1"),
            RouteStatus(False, TO_RELEASE, 7),
        ),
    ],
    ids=["accepted", "old-generation"],
)
@pytest.mark.asyncio
async def test_a_route_whose_header_does_not_name_its_backend_has_no_target(
    fake: FakeApiServer,
    cluster: KubernetesSiteCluster,
    route: Json,
    expected: RouteStatus,
) -> None:
    assert await _status_of(fake, cluster, route) == expected
    without = json.loads(json.dumps(route))
    del without["spec"]["rules"][0]["filters"]
    assert (await _status_of(fake, cluster, without)).target is None
    other = json.loads(json.dumps(route))
    other["spec"]["rules"][0]["filters"][0]["responseHeaderModifier"]["set"][0]["value"] = str(
        OTHER
    )
    assert (await _status_of(fake, cluster, other)).target is None


async def _status_of(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, route: Json
) -> RouteStatus:
    fake.put(ROUTE, route)
    return await cluster.route_status(PROJECT)


@pytest.mark.asyncio
async def test_delete_release_refuses_the_routed_release_even_without_the_header(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    route = route_to(TO_RELEASE, 7)
    del route["spec"]["rules"][0]["filters"]
    fake.put(ROUTE, route)

    with pytest.raises(FenceRejected):
        await cluster.delete_release(PROJECT, RELEASE, 9)

    assert fake.mutations() == []


# --- the preview route (decision 0014 section 17) ----------------------------------------------


@pytest.mark.asyncio
async def test_apply_preview_applies_the_rendered_preview_route(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    await cluster.apply_preview(PROJECT, RELEASE, PREVIEW_HOST, 7, subdomain=SUBDOMAIN)

    (patch,) = fake.seen("PATCH")
    assert patch.path == PREVIEW
    assert patch.body == render_preview_route(
        PROJECT,
        RELEASE,
        PREVIEW_HOST,
        7,
        preview_secret=TOKEN,
        site_domain="sites.webblock.test",
        subdomain=SUBDOMAIN,
    )


@pytest.mark.parametrize(
    ("hostname", "subdomain"),
    [
        (HOST, SUBDOMAIN),
        (PREVIEW_HOST, "other-shop"),
        ("other-shop-preview.sites.webblock.test", SUBDOMAIN),
        (f"{SUBDOMAIN}.preview.sites.webblock.test", SUBDOMAIN),
    ],
    ids=["site-host", "another-subdomain", "another-projects-preview", "old-dotted-preview-host"],
)
@pytest.mark.asyncio
async def test_s3_apply_preview_refuses_a_host_that_is_not_the_projects_preview_host(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, hostname: str, subdomain: str
) -> None:
    """Security review S3: refused before anything is read or written."""
    with pytest.raises(ValueError, match="preview hostname"):
        await cluster.apply_preview(PROJECT, RELEASE, hostname, 7, subdomain=subdomain)
    assert fake.seen("PATCH") == []
    assert fake.seen("GET") == []


@pytest.mark.asyncio
async def test_apply_preview_proceeds_over_the_same_or_a_lower_command_number(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.put(PREVIEW, route_to(RouteTarget(RouteTargetKind.RELEASE, OTHER), 5, preview=True))

    await cluster.apply_preview(PROJECT, RELEASE, PREVIEW_HOST, 7, subdomain=SUBDOMAIN)
    await cluster.apply_preview(PROJECT, RELEASE, PREVIEW_HOST, 7, subdomain=SUBDOMAIN)

    assert [p.path for p in fake.seen("PATCH")] == [PREVIEW, PREVIEW]


@pytest.mark.asyncio
async def test_apply_preview_over_a_higher_command_number_is_fence_rejected(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.put(PREVIEW, route_to(RouteTarget(RouteTargetKind.RELEASE, OTHER), 9, preview=True))

    with pytest.raises(FenceRejected):
        await cluster.apply_preview(PROJECT, RELEASE, PREVIEW_HOST, 7, subdomain=SUBDOMAIN)

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_apply_preview_without_the_tenant_is_tenant_not_ready(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    fake.script("PATCH", PREVIEW, (404, fake.status(404, "NotFound")))

    with pytest.raises(TenantNotReady):
        await cluster.apply_preview(PROJECT, RELEASE, PREVIEW_HOST, 7, subdomain=SUBDOMAIN)


@pytest.mark.asyncio
async def test_preview_status_reads_the_preview_route_not_the_sites(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    assert await cluster.preview_status(PROJECT) == RouteStatus(False, None, None)
    fake.put(ROUTE, route_to(TO_OFFLINE, 8))
    assert await cluster.preview_status(PROJECT) == RouteStatus(False, None, None)
    fake.put(PREVIEW, route_to(TO_RELEASE, 7, preview=True))
    assert await cluster.preview_status(PROJECT) == RouteStatus(True, TO_RELEASE, 7)
    fake.put(PREVIEW, route_to(TO_RELEASE, 7, generation=2, observed="1", preview=True))
    assert await cluster.preview_status(PROJECT) == RouteStatus(False, TO_RELEASE, 7)


# --- probe_site ---------------------------------------------------------------------------------


# Decision 0014 sections 15 and 17: the promotion probe asks the configured readiness path through
# the Gateway with the site's host. Only 200 with the release header naming the expected backend
# counts: a site whose catalog is broken answers 503 on /readyz, and the old backend, which the
# Gateway may still use for a moment after the route moved, names another release.
@pytest.mark.parametrize(
    ("code", "headers", "expected", "result"),
    [
        (200, [(RELEASE_HEADER, str(RELEASE))], TO_RELEASE, True),
        (200, [(RELEASE_HEADER, OFFLINE_HEADER_VALUE)], TO_OFFLINE, True),
        (200, [(RELEASE_HEADER, str(OTHER))], TO_RELEASE, False),
        (200, [(RELEASE_HEADER, OFFLINE_HEADER_VALUE)], TO_RELEASE, False),
        (200, [(RELEASE_HEADER, str(RELEASE))], TO_OFFLINE, False),
        (200, [], TO_RELEASE, False),
        (200, [(RELEASE_HEADER, str(RELEASE).upper())], TO_RELEASE, False),
        (200, [(RELEASE_HEADER, str(RELEASE)), (RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (204, [(RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (301, [(RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (404, [(RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (500, [(RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (503, [], TO_RELEASE, False),
        (503, [(RELEASE_HEADER, str(OTHER))], TO_RELEASE, False),
        (503, [(RELEASE_HEADER, str(RELEASE)), (RELEASE_HEADER, str(RELEASE))], TO_RELEASE, False),
        (503, [(RELEASE_HEADER, OFFLINE_HEADER_VALUE)], TO_OFFLINE, False),
    ],
    ids=[
        "200-release",
        "200-offline",
        "200-old-backend",
        "200-offline-not-release",
        "200-release-not-offline",
        "200-no-header",
        "200-not-canonical",
        "200-two-headers",
        "204",
        "301",
        "404",
        "500",
        "503-no-header",
        "503-other-release",
        "503-two-headers",
        "503-offline",
    ],
)
@pytest.mark.asyncio
async def test_probe_site_asks_the_gateway_with_the_site_host(
    cluster: KubernetesSiteCluster,
    http: list[Handler],
    code: int,
    headers: list[tuple[str, str]],
    expected: RouteTarget,
    result: bool,
) -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(code, headers=headers)

    http[0] = answer

    assert await cluster.probe_site(PROJECT, HOST, expected) is result
    (request,) = seen
    assert str(request.url) == GATEWAY + "/readyz"
    assert request.headers["Host"] == HOST
    assert request.method == "GET"
    assert request.extensions["timeout"]["read"] == 2.0


@pytest.mark.parametrize("successes", [1, 3])
@pytest.mark.asyncio
async def test_a_503_from_the_expected_release_is_a_dependency_outage(
    fake: FakeApiServer, worker_api: KubeApi, http: list[Handler], successes: int
) -> None:
    """Task H5a: the candidate's readiness answers 503 (a dependency is down) and names itself,
    and the release has a ready endpoint, so the probe raises SiteDependencyUnavailable, with a
    fixed text and nothing of the site's answer."""
    fake.put_list(SLICES, _slices("ready"))
    answers = 0

    def answer(request: httpx2.Request) -> httpx2.Response:
        nonlocal answers
        answers += 1
        if answers < successes:
            return httpx2.Response(200, headers=[(RELEASE_HEADER, str(RELEASE))])
        return httpx2.Response(
            503,
            headers=[(RELEASE_HEADER, str(RELEASE)), ("X-Detail", PLANTED_SECRET)],
            content=b"catalog down: " + PLANTED_SECRET.encode(),
        )

    http[0] = answer
    settings = dataclasses.replace(
        SETTINGS, probe_successes=successes, probe_success_gap_seconds=0.0
    )
    transport = httpx2.MockTransport(lambda request: http[0](request))
    async with httpx2.AsyncClient(transport=transport) as client:
        cluster = KubernetesSiteCluster(worker_api, settings, client)
        with pytest.raises(SiteDependencyUnavailable) as raised:
            await cluster.probe_site(PROJECT, HOST, TO_RELEASE)

    assert answers == successes
    assert str(raised.value) == "the expected backend answered 503"
    assert PLANTED_SECRET not in repr(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None


def _slices(fixture: str) -> list[Json]:
    slices: list[Json] = json.loads((FIXTURES / f"{fixture}.json").read_text())["endpointslices"]
    return slices


def _named_503(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(503, headers=[(RELEASE_HEADER, str(RELEASE))])


@pytest.mark.asyncio
async def test_a_named_503_without_a_ready_endpoint_is_the_gateways_and_false(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    """Lead's ruling B (task H5a): Envoy's own 503 for a backend with no ready endpoint carries
    the route's release header too, so without a ready endpoint the answer is not the site's."""
    fake.put_list(SLICES, _slices("no_ready_endpoint"))
    http[0] = _named_503
    assert await cluster.probe_site(PROJECT, HOST, TO_RELEASE) is False
    assert (
        await cluster.probe_site(PROJECT, PREVIEW_HOST, TO_RELEASE, preview_of=SUBDOMAIN) is False
    )


@pytest.mark.asyncio
async def test_a_named_503_on_the_preview_host_with_a_ready_endpoint_raises(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    fake.put_list(SLICES, _slices("ready"))
    http[0] = _named_503
    with pytest.raises(SiteDependencyUnavailable):
        await cluster.probe_site(PROJECT, PREVIEW_HOST, TO_RELEASE, preview_of=SUBDOMAIN)


@pytest.mark.asyncio
async def test_a_named_503_reads_the_endpoints_of_the_given_project(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    """The host no longer names the project (decision 0023 G18), so the endpoint read is in the
    namespace of the project passed in: another project's namespace has no ready endpoint of
    this release, whatever the host."""
    fake.put_list(SLICES, _slices("ready"))
    http[0] = _named_503
    assert await cluster.probe_site(OTHER, HOST, TO_RELEASE) is False
    with pytest.raises(SiteDependencyUnavailable):
        await cluster.probe_site(PROJECT, f"anything.{SETTINGS.site_domain}", TO_RELEASE)


@pytest.mark.parametrize("code", [403, 404, 500])
@pytest.mark.asyncio
async def test_a_named_503_whose_endpoint_read_fails_is_false(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, http: list[Handler], code: int
) -> None:
    fake.put_list(SLICES, _slices("ready"))
    fake.script("GET", SLICES, (code, fake.status(code, "Failure")))
    http[0] = _named_503
    assert await cluster.probe_site(PROJECT, HOST, TO_RELEASE) is False


@pytest.mark.asyncio
async def test_a_503_naming_another_release_is_false_whatever_the_endpoints(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    fake.put_list(SLICES, _slices("ready"))
    http[0] = lambda request: httpx2.Response(503, headers=[(RELEASE_HEADER, str(OTHER))])
    assert await cluster.probe_site(PROJECT, HOST, TO_RELEASE) is False
    http[0] = _named_503
    assert (
        await cluster.probe_site(PROJECT, HOST, RouteTarget(RouteTargetKind.RELEASE, OTHER))
        is False
    )


@pytest.mark.parametrize("failure", [httpx2.ConnectError("refused"), httpx2.ReadTimeout("slow")])
@pytest.mark.asyncio
async def test_probe_site_is_false_when_the_gateway_does_not_answer(
    cluster: KubernetesSiteCluster, http: list[Handler], failure: Exception
) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise failure

    http[0] = fail

    assert await cluster.probe_site(PROJECT, HOST, TO_RELEASE) is False


@pytest.mark.parametrize("hostname", ["", "*.sites.webblock.test", "a/b", "evil.test:80"])
@pytest.mark.asyncio
async def test_probe_site_refuses_a_hostname_that_is_not_a_dns_name(
    cluster: KubernetesSiteCluster, hostname: str
) -> None:
    with pytest.raises(ValueError, match="hostname"):
        await cluster.probe_site(PROJECT, hostname, TO_RELEASE)


@pytest.mark.parametrize(
    "path", ["", "readyz", "//evil.test/readyz", "/readyz?x=1", "/readyz#f", "/a b", "/../x"]
)
def test_the_probe_path_must_be_a_plain_absolute_path(path: str) -> None:
    with pytest.raises(ValueError, match="probe path"):
        SiteClusterSettings(
            site=SITE,
            gateway_url=GATEWAY,
            probe_path=path,
            probe_timeout_seconds=2.0,
            delete_retry_base_seconds=0.0,
            site_domain="sites.webblock.test",
            preview_token=TOKEN,
        )


# --- the preview token (recheck N1) and the HTTPS probe -----------------------------------------


@pytest.mark.parametrize(
    ("token", "target"),
    [
        (preview_token_for(TOKEN, PROJECT), TO_RELEASE),
        # Security review S1: the raw secret (the global token of before) and another
        # project's token are not this project's.
        (TOKEN, None),
        (preview_token_for(TOKEN, OTHER), None),
        ("another-token-of-an-earlier-worker-0123456", None),
    ],
    ids=["own-project", "raw-secret", "another-project", "earlier-worker"],
)
@pytest.mark.asyncio
async def test_a_preview_route_counts_only_with_this_workers_token(
    fake: FakeApiServer, cluster: KubernetesSiteCluster, token: str, target: RouteTarget | None
) -> None:
    route = route_to(TO_RELEASE, 7, preview=True)
    for match in route["spec"]["rules"][0]["matches"]:
        match["headers"][0]["value"] = token
    fake.put(PREVIEW, route)
    assert (await cluster.preview_status(PROJECT)).target == target


@pytest.mark.asyncio
async def test_the_probe_sends_the_token_the_server_name_and_no_keep_alive(
    cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, headers=[(RELEASE_HEADER, str(RELEASE))])

    http[0] = answer
    assert await cluster.probe_site(PROJECT, PREVIEW_HOST, TO_RELEASE, preview_of=SUBDOMAIN) is True
    (request,) = seen
    assert request.headers["Host"] == PREVIEW_HOST
    assert request.headers["X-Webblock-Preview"] == preview_token_for(TOKEN, PROJECT)
    assert request.headers["X-Webblock-Preview"] != TOKEN
    assert request.headers["Connection"] == "close"
    assert request.extensions["sni_hostname"] == PREVIEW_HOST


@pytest.mark.asyncio
async def test_s1_the_probe_sends_each_project_its_own_token_and_never_to_a_site_host(
    cluster: KubernetesSiteCluster, http: list[Handler]
) -> None:
    """Security review S1 (decision 0023 G18c): the header goes only to a preview host, with the
    project's own token; a site-host probe (the live site, after the switch) never carries it."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, headers=[(RELEASE_HEADER, str(RELEASE))])

    http[0] = answer
    other_preview = "other-shop-preview.sites.webblock.test"
    assert await cluster.probe_site(PROJECT, PREVIEW_HOST, TO_RELEASE, preview_of=SUBDOMAIN) is True
    assert await cluster.probe_site(OTHER, other_preview, TO_RELEASE, preview_of="other-shop")
    assert await cluster.probe_site(PROJECT, HOST, TO_RELEASE) is True
    mine, other, site = seen
    assert mine.headers["X-Webblock-Preview"] == preview_token_for(TOKEN, PROJECT)
    assert other.headers["X-Webblock-Preview"] == preview_token_for(TOKEN, OTHER)
    assert mine.headers["X-Webblock-Preview"] != other.headers["X-Webblock-Preview"]
    assert "X-Webblock-Preview" not in site.headers


_NOT_OUR_PREVIEW = [
    "other-shop-preview.sites.webblock.test",
    f"{SUBDOMAIN}.preview.sites.webblock.test",
    "preview.sites.webblock.test",
    "shop-previews.sites.webblock.test",
    f"{SUBDOMAIN}-preview.other.test",
    f"x.{SUBDOMAIN}-preview.sites.webblock.test",
    HOST,
]
_NOT_OUR_PREVIEW_IDS = [
    "another-projects-preview",
    "old-dotted-preview",
    "site-of-preview",
    "site-of-previews",
    "other-domain",
    "two-labels",
    "own-site-host",
]


@pytest.mark.parametrize("hostname", _NOT_OUR_PREVIEW, ids=_NOT_OUR_PREVIEW_IDS)
@pytest.mark.asyncio
async def test_fr52_1_the_probe_sends_the_token_only_to_the_projects_own_preview_host(
    cluster: KubernetesSiteCluster, http: list[Handler], hostname: str
) -> None:
    """FR-52.1 (security review L1): the token goes only to ``preview_hostname(subdomain, site
    domain)`` of the sub-domain the caller names. A probe that names none never carries it, even
    to another project's ``other-shop-preview`` host, the older dotted host, the site of
    ``preview`` or ``shop-previews``, another domain, a deeper name or the site's own host."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, headers=[(RELEASE_HEADER, str(RELEASE))])

    http[0] = answer
    assert await cluster.probe_site(PROJECT, hostname, TO_RELEASE) is True
    assert await cluster.probe_site(PROJECT, PREVIEW_HOST, TO_RELEASE, preview_of=SUBDOMAIN) is True
    other, preview = seen
    assert "X-Webblock-Preview" not in other.headers
    assert preview.headers["X-Webblock-Preview"] == preview_token_for(TOKEN, PROJECT)


@pytest.mark.parametrize("hostname", _NOT_OUR_PREVIEW, ids=_NOT_OUR_PREVIEW_IDS)
@pytest.mark.asyncio
async def test_fr52_1_a_preview_probe_of_any_other_host_is_refused_before_any_request(
    cluster: KubernetesSiteCluster, http: list[Handler], hostname: str
) -> None:
    """FR-52.1 (security review L1): naming the sub-domain on a host that is not its preview host
    is a caller bug; nothing is sent, so the token reaches no other host."""
    seen: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, headers=[(RELEASE_HEADER, str(RELEASE))])

    http[0] = answer
    with pytest.raises(ValueError, match="preview host") as raised:
        await cluster.probe_site(PROJECT, hostname, TO_RELEASE, preview_of=SUBDOMAIN)
    assert seen == []
    assert hostname not in str(raised.value)


@pytest.mark.asyncio
async def test_fr52_1_a_preview_route_without_the_no_store_header_is_not_confirmed(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    """FR-52.1 (security review M1): the worker confirms only the preview route it renders, whose
    answers carry ``Cache-Control: private, no-store`` so no shared cache keeps them."""
    route = route_to(TO_RELEASE, 7, preview=True)
    route["spec"]["rules"][0]["filters"][0] = {
        "type": "ResponseHeaderModifier",
        "responseHeaderModifier": {"set": [{"name": RELEASE_HEADER, "value": str(RELEASE)}]},
    }
    fake.put(PREVIEW, route)
    assert (await cluster.preview_status(PROJECT)).target is None
    fake.put(PREVIEW, route_to(TO_RELEASE, 7, preview=True))
    assert (await cluster.preview_status(PROJECT)).target == TO_RELEASE


@pytest.mark.asyncio
async def test_s1_a_preview_route_without_the_header_removal_is_not_confirmed(
    fake: FakeApiServer, cluster: KubernetesSiteCluster
) -> None:
    """The preview rule must also remove the probe's header before the site (review S1)."""
    route = route_to(TO_RELEASE, 7, preview=True)
    route["spec"]["rules"][0]["filters"] = route["spec"]["rules"][0]["filters"][:1]
    fake.put(PREVIEW, route)
    assert (await cluster.preview_status(PROJECT)).target is None


def test_the_token_never_appears_in_the_settings_repr() -> None:
    assert TOKEN not in repr(SETTINGS)


@pytest.mark.parametrize("token", ["", "short", "with space and more characters 0123456789"])
def test_settings_refuse_a_token_that_is_not_url_safe_of_32_or_more(token: str) -> None:
    with pytest.raises(ValueError, match="preview token") as raised:
        SiteClusterSettings(
            site=SITE,
            gateway_url=GATEWAY,
            probe_path="/readyz",
            probe_timeout_seconds=2.0,
            delete_retry_base_seconds=0.0,
            site_domain="sites.webblock.test",
            preview_token=token,
        )
    assert token not in str(raised.value) or token == ""
