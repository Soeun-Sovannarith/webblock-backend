"""The durable "created from nothing" mark of a tenant namespace (task F2B; H-V finding F-2;
decision 0016 section 8 ruling 4, amending decision 0014 section 24).

``KubernetesTenantBootstrap`` against the local fake API server, with the bootstrap's real
``AccessSetupHook`` and the real ``CatalogAccessSetup`` rule over a fake catalog: the namespace is
created with ``websites.webblock.internal/catalog-secret: pending`` before the worker's
RoleBinding; access_setup sets ``delivered`` before the Secret; and a bootstrap restarted between
the two still delivers a new password to a rebuilt namespace of a live site, where the in-process
marks of tasks H2 and BT forgot it (``runtime_secret_missing``)."""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import pytest_asyncio

from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    TenantState,
)
from provisioning.core.application.shared.audit import AuditAction, AuditEvent
from provisioning.hosts.bootstrap.tenants import AccessSetupHook
from provisioning.infrastructure.catalog import (
    CatalogAccessSetup,
    RuntimeCredential,
    RuntimeEndpoint,
    RuntimeSecretMissing,
)
from provisioning.infrastructure.kubernetes import (
    BOOTSTRAP_FIELD_MANAGER,
    KubeApi,
    KubernetesNotFound,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import (
    KubernetesTenantBootstrap,
    catalog_secret_mark,
)
from provisioning.infrastructure.kubernetes.render import (
    ANNOTATION_CATALOG_SECRET,
    CATALOG_SECRET_DELIVERED,
    CATALOG_SECRET_PENDING,
    LABEL_SUBDOMAIN,
    TenantSettings,
    render_namespace,
)

if TYPE_CHECKING:
    from tests.unit.infrastructure.kubernetes.conftest import FakeApiServer, KubeconfigFactory

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
SUBDOMAIN = "unit-shop"  # project_hosting.subdomain (decision 0023 G18a)
NS = f"wb-{PROJECT}"
NAMESPACE = f"/api/v1/namespaces/{NS}"
BINDING = f"/apis/rbac.authorization.k8s.io/v1/namespaces/{NS}/rolebindings/webblock-tenant-worker"
SECRET = f"/api/v1/namespaces/{NS}/secrets/catalog-runtime"
TENANT = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)
SECRET_VALUES = {
    "PGHOST": "webblock-catalog-db",
    "PGPORT": "5432",
    "PGDATABASE": "catalog",
    "PGUSER": "site_0b7c2f435d5e4f1e9a430e1d5c1c7a11",
    "PGPASSWORD": "CANARY-runtime-password",
    "PGSSLMODE": "disable",
}
ENDPOINT = RuntimeEndpoint(
    host="webblock-catalog-db", port=5432, database="catalog", sslmode="disable"
)


class _Catalog:
    """The catalog side of access_setup; the site role exists unless ``role`` is False."""

    def __init__(self, *, role: bool = True) -> None:
        self.role = role
        self.passwords_set: list[UUID] = []

    async def role_exists(self, project_id: UUID) -> bool:
        return self.role

    async def set_site_role(self, credential: RuntimeCredential) -> bool:
        self.passwords_set.append(credential.project_id)
        return True

    async def verify_site_role(self, project_id: UUID) -> None:
        return None


class _Hosting:
    def __init__(self, live: bool) -> None:
        self.live = live

    async def has_live_release(self, project_id: UUID) -> bool:
        return self.live


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("access_setup never alerts")


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


class _Process:
    """One bootstrap process: its own client, adapter and hook, and nothing kept in memory
    between two of them; only the fake API server (the cluster) is shared."""

    def __init__(self, api: KubeApi, *, live: bool, catalog: _Catalog) -> None:
        self.api = api
        self.tenants = KubernetesTenantBootstrap(api, TENANT)
        self.audit = _Audit()
        self.hook = AccessSetupHook(
            access=CatalogAccessSetup(site_access=catalog, secrets=self.tenants, endpoint=ENDPOINT),
            hosting=_Hosting(live),
            marks=self.tenants,
            audit=self.audit,
            clock=_Clock(),
            logger=logging.getLogger("test.bootstrap"),
        )

    async def round(self) -> TenantState:
        """What the loop does for one project: ensure, then access_setup once it is ready."""
        state = await self.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)
        if state is TenantState.READY:
            await self.hook(PROJECT)
        return state


type Start = Callable[..., Awaitable[_Process]]


@pytest_asyncio.fixture
async def start(fake: FakeApiServer, make_kubeconfig: KubeconfigFactory) -> AsyncIterator[Start]:
    apis: list[KubeApi] = []

    async def started(*, live: bool, catalog: _Catalog | None = None) -> _Process:
        api = await connect_kubeconfig(
            make_kubeconfig(fake.url),
            field_manager=BOOTSTRAP_FIELD_MANAGER,
            request_timeout_seconds=5.0,
        )
        apis.append(api)
        return _Process(api, live=live, catalog=catalog or _Catalog())

    try:
        yield started
    finally:
        for api in apis:
            await api.close()


def _stored_mark(fake: FakeApiServer) -> str | None:
    return catalog_secret_mark(fake.objects[NAMESPACE])


def _patches(fake: FakeApiServer) -> list[tuple[str, str | None]]:
    """Each apply's path, with the mark it carried when it is the namespace."""
    return [
        (p.path, catalog_secret_mark(p.body) if p.path == NAMESPACE else None)
        for p in fake.seen("PATCH")
    ]


# --- ensure_tenant: pending on creation, before the RoleBinding; never moved afterwards --------


@pytest.mark.asyncio
async def test_a_namespace_is_created_pending_before_the_role_binding(
    fake: FakeApiServer, start: Start
) -> None:
    process = await start(live=False)

    assert await process.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    patches = _patches(fake)
    assert patches[0] == (NAMESPACE, CATALOG_SECRET_PENDING)  # the creation itself carries it
    assert patches[-1] == (BINDING, None)
    assert _stored_mark(fake) == CATALOG_SECRET_PENDING
    assert fake.seen("PATCH")[0].body == render_namespace(
        PROJECT, subdomain=SUBDOMAIN, catalog_secret=CATALOG_SECRET_PENDING
    )


@pytest.mark.parametrize("mark", [CATALOG_SECRET_PENDING, CATALOG_SECRET_DELIVERED, None])
@pytest.mark.asyncio
async def test_an_existing_namespace_keeps_its_mark(
    fake: FakeApiServer, start: Start, mark: str | None
) -> None:
    """A rerun (every recheck) carries the mark it read: ``delivered`` never goes back to
    ``pending``, and a namespace from before task F2B is never given one."""
    fake.put(NAMESPACE, render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=mark))
    process = await start(live=True)

    assert await process.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY

    assert _patches(fake)[0] == (NAMESPACE, mark)
    assert _stored_mark(fake) == mark


# --- the restart: the mark survives in the namespace ------------------------------------------


@pytest.mark.asyncio
async def test_a_restart_between_the_namespace_and_access_setup_still_delivers(
    fake: FakeApiServer, start: Start
) -> None:
    """QA-04 with a restart: the old namespace (and v1's pods) are gone; one bootstrap creates the
    new one and stops before access_setup; the next one finds the namespace present, the Secret
    absent and the project still recorded live. Before task F2B: ``runtime_secret_missing``."""
    catalog = _Catalog()  # the site role exists: v1 was live
    first = await start(live=True, catalog=catalog)
    assert (
        await first.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN) is TenantState.READY
    )  # then killed

    second = await start(live=True, catalog=catalog)
    assert await second.round() is TenantState.READY

    assert catalog.passwords_set == [PROJECT]
    assert SECRET in fake.objects
    assert _stored_mark(fake) == CATALOG_SECRET_DELIVERED
    assert [e.action for e in second.audit.events] == [AuditAction.CATALOG_CREDENTIAL_ROTATED]
    # The next rounds only verify, and the mark stays delivered.
    third = await start(live=True, catalog=catalog)
    assert await third.round() is TenantState.READY
    assert catalog.passwords_set == [PROJECT]
    assert _stored_mark(fake) == CATALOG_SECRET_DELIVERED


@pytest.mark.asyncio
async def test_a_secret_missing_under_delivered_is_for_an_operator(
    fake: FakeApiServer, start: Start
) -> None:
    """The Secret was delivered once, then lost while the namespace (and the live pods) stayed:
    decision 0011 section 5 unchanged. Also the state a crash between the mark and the Secret
    leaves: the safe direction."""
    fake.put(
        NAMESPACE,
        render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=CATALOG_SECRET_DELIVERED),
    )
    catalog = _Catalog()
    process = await start(live=True, catalog=catalog)

    with pytest.raises(RuntimeSecretMissing):
        await process.round()

    assert catalog.passwords_set == []
    assert SECRET not in fake.objects
    assert process.audit.events == []


@pytest.mark.asyncio
async def test_a_namespace_without_the_mark_keeps_the_rule_of_0011(
    fake: FakeApiServer, start: Start
) -> None:
    """A namespace from before task F2B: no annotation counts as "not fresh", as before."""
    fake.put(NAMESPACE, render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None))
    catalog = _Catalog()
    process = await start(live=True, catalog=catalog)

    with pytest.raises(RuntimeSecretMissing):
        await process.round()

    assert catalog.passwords_set == []
    assert _stored_mark(fake) is None


@pytest.mark.parametrize("label", [SUBDOMAIN, None])
@pytest.mark.asyncio
async def test_the_delivered_mark_keeps_the_subdomain_label_it_read(
    fake: FakeApiServer, start: Start, label: str | None
) -> None:
    """write_runtime_secret re-applies the namespace under the bootstrap's field manager; it
    carries the sub-domain label it read, so the apply never drops it (decision 0023 G18a)."""
    namespace = render_namespace(PROJECT, subdomain=label, catalog_secret=None)
    namespace["metadata"]["resourceVersion"] = "5"
    fake.put(NAMESPACE, namespace)
    process = await start(live=False)

    await process.tenants.write_runtime_secret(PROJECT, SECRET_VALUES)

    mark = next(p for p in fake.seen("PATCH") if p.path == NAMESPACE)
    assert mark.body["metadata"]["labels"].get(LABEL_SUBDOMAIN) == label
    assert mark.body["metadata"]["annotations"] == {
        "websites.webblock.internal/catalog-secret": CATALOG_SECRET_DELIVERED
    }


@pytest.mark.asyncio
async def test_a_namespace_without_the_mark_and_nothing_live_is_delivered_and_marked(
    fake: FakeApiServer, start: Start
) -> None:
    fake.put(NAMESPACE, render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=None))
    catalog = _Catalog()
    process = await start(live=False, catalog=catalog)

    assert await process.round() is TenantState.READY

    assert catalog.passwords_set == [PROJECT]
    assert _stored_mark(fake) == CATALOG_SECRET_DELIVERED


@pytest.mark.asyncio
async def test_a_first_tenant_is_created_and_marked_delivered(
    fake: FakeApiServer, start: Start
) -> None:
    catalog = _Catalog(role=False)
    process = await start(live=False, catalog=catalog)

    assert await process.round() is TenantState.READY

    assert catalog.passwords_set == [PROJECT]
    assert _stored_mark(fake) == CATALOG_SECRET_DELIVERED
    assert process.audit.events == []  # a first password is not a rotation


# --- write_runtime_secret: delivered before the Secret ----------------------------------------


@pytest.mark.asyncio
async def test_the_mark_is_delivered_before_the_secret_is_written(
    fake: FakeApiServer, start: Start
) -> None:
    process = await start(live=False)
    await process.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)
    created = fake.objects[NAMESPACE]["metadata"]["resourceVersion"]
    before = len(fake.seen("PATCH"))

    await process.tenants.write_runtime_secret(PROJECT, SECRET_VALUES)

    writes = fake.seen("PATCH")[before:]
    assert [(p.path, p.body["metadata"].get("annotations")) for p in writes] == [
        (NAMESPACE, {ANNOTATION_CATALOG_SECRET: CATALOG_SECRET_DELIVERED}),
        (SECRET, None),
    ]
    # The mark names the version it read: it never creates a namespace or overwrites a change.
    assert writes[0].body["metadata"]["resourceVersion"] == created


@pytest.mark.parametrize(
    ("code", "expected"),
    [(409, ApplyConflict), (422, ClusterRefused), (403, ClusterRefused)],
    ids=["conflict", "refused", "admission-403"],
)
@pytest.mark.asyncio
async def test_a_refused_mark_writes_no_secret(
    fake: FakeApiServer, start: Start, code: int, expected: type[Exception]
) -> None:
    """409: the namespace changed since the read; 422 and 403: admission refused the move (a 403
    from admission, the one a live policy refusal takes, is ``ClusterRefused``)."""
    process = await start(live=False)
    await process.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)
    fake.script("PATCH", NAMESPACE, (code, fake.status(code, "Whatever")))

    with pytest.raises(expected):
        await process.tenants.write_runtime_secret(PROJECT, SECRET_VALUES)

    assert SECRET not in [p.path for p in fake.seen("PATCH")]
    assert _stored_mark(fake) == CATALOG_SECRET_PENDING


@pytest.mark.asyncio
async def test_no_secret_and_no_namespace_are_written_into_a_missing_namespace(
    fake: FakeApiServer, start: Start
) -> None:
    process = await start(live=False)

    with pytest.raises(KubernetesNotFound):
        await process.tenants.write_runtime_secret(PROJECT, SECRET_VALUES)

    assert fake.mutations() == []


@pytest.mark.asyncio
async def test_a_refused_secret_value_moves_no_mark(fake: FakeApiServer, start: Start) -> None:
    process = await start(live=False)
    await process.tenants.ensure_tenant(PROJECT, subdomain=SUBDOMAIN)
    before = len(fake.mutations())

    with pytest.raises(ValueError, match="six PG"):
        await process.tenants.write_runtime_secret(PROJECT, {"PGPASSWORD": "x"})

    assert fake.mutations()[before:] == []
    assert _stored_mark(fake) == CATALOG_SECRET_PENDING


@pytest.mark.asyncio
async def test_an_already_delivered_namespace_is_not_marked_again(
    fake: FakeApiServer, start: Start
) -> None:
    fake.put(
        NAMESPACE,
        render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=CATALOG_SECRET_DELIVERED),
    )
    process = await start(live=False)

    await process.tenants.write_runtime_secret(PROJECT, SECRET_VALUES)

    assert [p.path for p in fake.seen("PATCH")] == [SECRET]


# --- the probe --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (CATALOG_SECRET_PENDING, True),
        (CATALOG_SECRET_DELIVERED, False),
        (None, False),
        ("something-else", False),
    ],
)
@pytest.mark.asyncio
async def test_only_a_pending_mark_counts_as_created_from_nothing(
    fake: FakeApiServer, start: Start, stored: str | None, expected: bool
) -> None:
    fake.put(NAMESPACE, render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=stored))
    process = await start(live=True)

    assert await process.tenants.catalog_secret_pending(PROJECT) is expected


@pytest.mark.asyncio
async def test_a_missing_namespace_is_not_pending(fake: FakeApiServer, start: Start) -> None:
    process = await start(live=True)

    assert await process.tenants.catalog_secret_pending(PROJECT) is False


@pytest.mark.asyncio
async def test_a_verification_leaves_the_mark_alone(fake: FakeApiServer, start: Start) -> None:
    """The role and the Secret exist: access_setup only verifies and writes nothing."""
    fake.put(
        NAMESPACE,
        render_namespace(PROJECT, subdomain=SUBDOMAIN, catalog_secret=CATALOG_SECRET_DELIVERED),
    )
    fake.put(SECRET, {"metadata": {"name": "catalog-runtime"}})
    process = await start(live=True)
    before = len(fake.mutations())

    await process.hook(PROJECT)

    assert fake.mutations()[before:] == []
    assert process.audit.events == []
