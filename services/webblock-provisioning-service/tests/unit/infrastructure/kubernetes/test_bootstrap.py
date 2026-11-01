"""KubernetesTenantBootstrap against the local fake API server: the order of the objects with the
worker's RoleBinding last, idempotency, and a terminating namespace (review-k8s-nocp.md M5)."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import pytest_asyncio

from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterUnavailable,
    TenantState,
)
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    KubeApi,
    KubernetesForbidden,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    CATALOG_SECRET_PENDING,
    LABEL_SUBDOMAIN,
    TenantSettings,
    render_limit_range,
    render_namespace,
    render_reference_grant,
    render_resource_quota,
    render_runtime_secret,
    render_worker_role_binding,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import FakeApiServer, KubeconfigFactory

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
SUBDOMAIN = "unit-shop"  # project_hosting.subdomain (decision 0023 G18a)
NS = f"wb-{PROJECT}"
NAMESPACE = f"/api/v1/namespaces/{NS}"
QUOTA = f"/api/v1/namespaces/{NS}/resourcequotas/webblock-tenant-quota"
LIMITS = f"/api/v1/namespaces/{NS}/limitranges/webblock-tenant-limits"
GRANT = f"/apis/gateway.networking.k8s.io/v1beta1/namespaces/webblock-offline/referencegrants/{NS}"
BINDING = f"/apis/rbac.authorization.k8s.io/v1/namespaces/{NS}/rolebindings/webblock-tenant-worker"
ORDER = [NAMESPACE, QUOTA, LIMITS, GRANT, BINDING]
TENANT = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)


@pytest_asyncio.fixture
async def bootstrap_api(
    fake: FakeApiServer, make_kubeconfig: KubeconfigFactory
) -> AsyncIterator[KubeApi]:
    api = await connect_kubeconfig(
        make_kubeconfig(fake.url),
        field_manager=BOOTSTRAP_FIELD_MANAGER,
        request_timeout_seconds=5.0,
    )
    try:
        yield api
    finally:
        await api.close()


@pytest.fixture
def bootstrap(bootstrap_api: KubeApi) -> KubernetesTenantBootstrap:
    return KubernetesTenantBootstrap(bootstrap_api, TENANT)


@pytest.mark.asyncio
async def test_a_new_tenant_is_applied_in_order_with_the_role_binding_last(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    patches = fake.seen("PATCH")
    assert [p.path for p in patches] == ORDER
    assert [p.body for p in patches] == [
        render_namespace(
            PROJECT, subdomain=SUBDOMAIN, catalog_secret=CATALOG_SECRET_PENDING
        ),  # task F2B
        render_resource_quota(PROJECT, TENANT),
        render_limit_range(PROJECT, TENANT),
        render_reference_grant(PROJECT),
        render_worker_role_binding(PROJECT),
    ]
    assert {p.query["fieldManager"] for p in patches} == {BOOTSTRAP_FIELD_MANAGER}


@pytest.mark.asyncio
async def test_ensure_tenant_is_idempotent(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY
    first = [(p.path, p.body) for p in fake.seen("PATCH")]

    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    second = [(p.path, p.body) for p in fake.seen("PATCH")][len(first) :]
    assert second == first


@pytest.mark.asyncio
async def test_the_namespace_carries_the_projects_subdomain_label(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    """Decision 0023 G18a: the label the route-shape policy checks route hosts against."""
    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    labels = fake.objects[NAMESPACE]["metadata"]["labels"]
    assert labels[LABEL_SUBDOMAIN] == SUBDOMAIN
    assert labels["websites.webblock.internal/project-id"] == str(PROJECT)


@pytest.mark.asyncio
async def test_a_namespace_from_before_the_label_gets_it(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    old = render_namespace(PROJECT, subdomain=None, catalog_secret=None)
    old["status"] = {"phase": "Active"}
    fake.put(NAMESPACE, old)

    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    first = fake.seen("PATCH")[0]
    assert first.path == NAMESPACE
    assert first.body == render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)
    assert fake.objects[NAMESPACE]["metadata"]["labels"][LABEL_SUBDOMAIN] == SUBDOMAIN


@pytest.mark.asyncio
async def test_a_terminating_namespace_is_reported_and_nothing_is_written(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    namespace = render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)
    namespace["metadata"]["deletionTimestamp"] = "2026-10-08T10:00:00Z"
    namespace["status"] = {"phase": "Terminating"}
    fake.put(NAMESPACE, namespace)

    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.TERMINATING
    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_a_namespace_that_starts_terminating_during_bootstrap_is_reported(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    terminating = fake.status(
        403,
        "Forbidden",
        {"name": NS, "kind": "namespaces", "causes": [{"reason": "NamespaceTerminating"}]},
    )
    fake.script("PATCH", QUOTA, (403, terminating))

    assert await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.TERMINATING
    assert [p.path for p in fake.seen("PATCH")] == [NAMESPACE, QUOTA]


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (409, {"kind": "rolebindings"}, ApplyConflict),
        (403, {"kind": "rolebindings", "group": "rbac.authorization.k8s.io"}, KubernetesForbidden),
        (503, None, ClusterUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_step_stops_the_bootstrap_before_the_role_binding(
    fake: FakeApiServer,
    bootstrap: KubernetesTenantBootstrap,
    code: int,
    details: dict[str, object] | None,
    expected: type[Exception],
) -> None:
    fake.script("PATCH", GRANT, (code, fake.status(code, "Whatever", details)))

    with pytest.raises(expected):
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)

    assert [p.path for p in fake.seen("PATCH")] == ORDER[:4]


# Task BT: ensure reports the namespace it created (decision 0016 section 6 item 4). "Created"
# means its own read found no namespace and its namespace apply then succeeded; the report is made
# at once, before any later step.


class _Created:
    """Records each report with the requests the fake server had seen when it was made."""

    def __init__(self, fake: FakeApiServer) -> None:
        self.fake = fake
        self.reports: list[tuple[UUID, list[tuple[str, str]]]] = []

    def __call__(self, project_id: UUID) -> None:
        self.reports.append((project_id, [(r.method, r.path) for r in self.fake.requests]))


@pytest.mark.asyncio
async def test_a_namespace_created_by_this_call_is_reported_before_any_later_step(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    created = _Created(fake)

    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)
        is TenantState.READY
    )

    assert created.reports == [(PROJECT, [("GET", NAMESPACE), ("PATCH", NAMESPACE)])]
    assert [p.path for p in fake.seen("PATCH")] == ORDER  # the steps are unchanged


@pytest.mark.asyncio
async def test_a_later_step_that_fails_after_the_namespace_was_created_keeps_the_report(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    created = _Created(fake)
    fake.script("PATCH", BINDING, (503, fake.status(503, "ServiceUnavailable")))

    with pytest.raises(ClusterUnavailable):
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)

    assert [project for project, _ in created.reports] == [PROJECT]


@pytest.mark.asyncio
async def test_an_existing_namespace_is_not_reported(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY
    )  # created, unobserved
    created = _Created(fake)

    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)
        is TenantState.READY
    )

    assert created.reports == []


@pytest.mark.asyncio
async def test_a_rival_writer_in_the_gap_still_counts_as_created(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    """The read finds nothing; a rival (the bootstrap identity or an administrator) creates the
    namespace before our apply, which still succeeds. The namespace did not exist when this call
    looked, which is the condition of decision 0014 section 24: reported, so marked fresh."""
    created = _Created(fake)
    fake.script("GET", NAMESPACE, (404, fake.status(404, "NotFound")))
    rival = render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)
    rival["metadata"]["uid"] = "uid-rival"
    fake.put(NAMESPACE, rival)

    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)
        is TenantState.READY
    )

    assert [project for project, _ in created.reports] == [PROJECT]
    assert fake.objects[NAMESPACE]["metadata"]["uid"] == "uid-rival"  # the rival's namespace


@pytest.mark.asyncio
async def test_a_terminating_namespace_is_not_reported(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    namespace = render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)
    namespace["metadata"]["deletionTimestamp"] = "2026-10-08T10:00:00Z"
    namespace["status"] = {"phase": "Terminating"}
    fake.put(NAMESPACE, namespace)
    created = _Created(fake)

    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)
        is TenantState.TERMINATING
    )

    assert created.reports == []


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (409, {"kind": "namespaces"}, ApplyConflict),
        (503, None, ClusterUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_namespace_apply_is_not_reported(
    fake: FakeApiServer,
    bootstrap: KubernetesTenantBootstrap,
    code: int,
    details: dict[str, object] | None,
    expected: type[Exception],
) -> None:
    created = _Created(fake)
    fake.script("PATCH", NAMESPACE, (code, fake.status(code, "Whatever", details)))

    with pytest.raises(expected):
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)

    assert created.reports == []


@pytest.mark.asyncio
async def test_a_namespace_apply_refused_as_terminating_is_not_reported(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    created = _Created(fake)
    terminating = fake.status(
        403,
        "Forbidden",
        {"name": NS, "kind": "namespaces", "causes": [{"reason": "NamespaceTerminating"}]},
    )
    fake.script("PATCH", NAMESPACE, (403, terminating))

    assert (
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=created)
        is TenantState.TERMINATING
    )

    assert created.reports == []


@pytest.mark.asyncio
async def test_what_the_callback_raises_is_not_swallowed(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    def broken(project_id: UUID) -> None:
        raise LookupError("a programming error")

    with pytest.raises(LookupError):
        await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN, on_created=broken)

    assert [p.path for p in fake.seen("PATCH")] == [NAMESPACE]


# access_setup's Secret (decision 0014 section 14): written by the bootstrap identity, never by
# ensure_tenant, and never by the worker.
SECRET = f"/api/v1/namespaces/{NS}/secrets/catalog-runtime"
SECRET_VALUES = {
    "PGHOST": "webblock-catalog-db",
    "PGPORT": "5432",
    "PGDATABASE": "catalog",
    "PGUSER": "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
    "PGPASSWORD": "CANARY-runtime-password",
    "PGSSLMODE": "disable",
}


def _existing_namespace(fake: FakeApiServer) -> None:
    namespace = render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None)
    namespace["metadata"]["resourceVersion"] = "7"
    fake.put(NAMESPACE, namespace)


@pytest.mark.asyncio
async def test_the_runtime_secret_is_applied_by_the_bootstrap_identity(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    _existing_namespace(fake)  # the mark's moves: test_catalog_secret_mark.py

    await bootstrap.write_runtime_secret(PROJECT, SECRET_VALUES)

    patches = fake.seen("PATCH")
    assert [p.path for p in patches] == [NAMESPACE, SECRET]
    assert patches[1].body == render_runtime_secret(PROJECT, SECRET_VALUES)
    for patch in patches:
        assert patch.query["fieldManager"] == BOOTSTRAP_FIELD_MANAGER
        assert patch.query["force"] == "false"
        assert patch.content_type == "application/apply-patch+yaml"


@pytest.mark.asyncio
async def test_the_runtime_secret_is_seen_only_when_it_exists(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    _existing_namespace(fake)
    assert await bootstrap.runtime_secret_exists(PROJECT) is False

    await bootstrap.write_runtime_secret(PROJECT, SECRET_VALUES)

    assert await bootstrap.runtime_secret_exists(PROJECT) is True
    assert [p.path for p in fake.seen("GET") if p.path == SECRET] == [SECRET, SECRET]


@pytest.mark.asyncio
async def test_ensure_tenant_writes_no_secret(
    fake: FakeApiServer, bootstrap: KubernetesTenantBootstrap
) -> None:
    await bootstrap.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)

    assert SECRET not in [p.path for p in fake.seen()]


@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (409, {"kind": "secrets"}, ApplyConflict),
        (403, {"kind": "secrets", "name": "catalog-runtime"}, KubernetesForbidden),
        (503, None, ClusterUnavailable),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_secret_write_raises_without_the_password(
    fake: FakeApiServer,
    bootstrap: KubernetesTenantBootstrap,
    code: int,
    details: dict[str, object] | None,
    expected: type[Exception],
) -> None:
    _existing_namespace(fake)
    fake.script("PATCH", SECRET, (code, fake.status(code, "Whatever", details)))

    with pytest.raises(expected) as caught:
        await bootstrap.write_runtime_secret(PROJECT, SECRET_VALUES)

    assert "CANARY" not in str(caught.value)
