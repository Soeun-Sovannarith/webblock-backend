"""The catalog database, access_setup and the Secret catalog-runtime on the prototype stack (task H;
decision 0014 section 14; decision 0011 section 5; Notion 6 · Shared catalog).

- access_setup creates the role ``site_<hex>`` and the Secret; a second run only verifies; a missing
  Secret is refused while a release is live and gets a new credential otherwise.
- Two websites, each seeded through its own role: each sees only its own rows; a site role cannot
  read ``catalog.websites`` nor write, move or delete another website's rows.
- The worker identity gets 403 reading the Secret; the bootstrap identity reads only that name.
- A Deployment rendered with ``envFrom`` starts, and its pod logs in as its role and sees its own
  website through the Gateway.
"""

import asyncio
import base64
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import httpx2
import psycopg
import pytest

from provisioning.core.application.ports.site_cluster import (
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    TenantState,
    WorkloadState,
)
from provisioning.hosts.devtools.catalog_platform import (
    ToolError,
    parse_seed,
    register,
    seed_website,
)
from provisioning.infrastructure.catalog import (
    AccessOutcome,
    CatalogAccessSetup,
    PostgresSiteAccess,
    RuntimeSecretMissing,
    new_runtime_credential,
    site_role_name,
)
from provisioning.infrastructure.kubernetes import KubeApi, KubernetesForbidden, Resource
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.render import (
    OFFLINE_NAMESPACE,
    OFFLINE_SERVICE,
    SiteSettings,
    release_name,
    tenant_namespace,
)
from provisioning.infrastructure.kubernetes.site_cluster import (
    KubernetesSiteCluster,
    SiteClusterSettings,
)

if TYPE_CHECKING:
    from tests.integration.catalog.conftest import Live

PROBE = Path(__file__).resolve().parent / "probe"
READY_WITHIN = 180.0
POLL = 2.0
SITE_DOMAIN = "sites.webblock.test"
PREVIEW_TOKEN = "catalog-live-preview-token-0123456789abcdef"  # noqa: S105 - a test value

PRODUCT_COUNT = "SELECT count(*) FROM catalog.products"
FOREIGN_ROWS = (
    "SELECT (SELECT count(*) FROM catalog.business_profiles WHERE website_id <> %(w)s)"
    " + (SELECT count(*) FROM catalog.categories WHERE website_id <> %(w)s)"
    " + (SELECT count(*) FROM catalog.products WHERE website_id <> %(w)s)"
    " + (SELECT count(*) FROM catalog.product_images WHERE website_id <> %(w)s)"
)
OWN_ROWS = (
    "SELECT (SELECT count(*) FROM catalog.business_profiles),"
    " (SELECT count(*) FROM catalog.categories),"
    " (SELECT count(*) FROM catalog.products),"
    " (SELECT count(*) FROM catalog.product_images)"
)
MEMBERSHIPS = """
SELECT g.rolname, m.inherit_option, m.set_option, m.admin_option
FROM pg_catalog.pg_auth_members m
JOIN pg_catalog.pg_roles r ON r.oid = m.member
JOIN pg_catalog.pg_roles g ON g.oid = m.roleid
WHERE r.rolname = %s
"""
ROLE_ATTRIBUTES = """
SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,
       rolpassword IS NULL
FROM pg_catalog.pg_roles WHERE rolname = %s
"""


def subdomain_of(project: UUID) -> str:
    """The test project's sub-domain (decision 0023 G18): its host and namespace label."""
    return f"catalog-{project.hex}"


def command_number() -> int:
    """Increasing across runs: milliseconds since the epoch (below 2^53)."""
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


def seed_document(products: int, *, prefix: str) -> dict[str, Any]:
    """A seed in task S's format with ``products`` products (one image each) in one category."""
    return {
        "format_version": 1,
        "business_type": "clothing_shop",
        "business_profile": {
            "business_name": f"{prefix} shop",
            "description": f"The {prefix} test shop.",
            "timezone": "UTC",
            "opening_hours": [],
        },
        "categories": [{"slug": "tops", "name": "Tops", "sort_order": 1}],
        "products": [
            {
                "product_type": "top",
                "category_slug": "tops",
                "name": f"{prefix} tee {i}",
                "description": None,
                "price": f"{10 + i}.00",
                "attributes": {"size": ["S", "M"], "color": ["Black"]},
                "status": "published",
                "sort_order": i,
                "images": [
                    {
                        "file": f"products/{prefix}-{i}.svg",
                        "byte_size": 1000 + i,
                        "alt_text": None,
                        "sort_order": 0,
                    }
                ],
            }
            for i in range(products)
        ],
    }


async def new_website(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    *,
    storage_limit_bytes: int = 10 * 1024 * 1024,
    max_image_bytes: int = 1024 * 1024,
) -> UUID:
    """A tenant, a registered website and its role and Secret, as bootstrap would leave them."""
    project = uuid4()
    assert (
        await bootstrap.ensure_tenant(project, subdomain=subdomain_of(project)) is TenantState.READY
    )
    assert await register(
        live.tool,
        project,
        "clothing_shop",
        name=f"Website {project}",
        timezone="UTC",
        storage_limit_bytes=storage_limit_bytes,
        max_image_bytes=max_image_bytes,
    )
    assert await access_setup.run(project, has_live_release=False) is AccessOutcome.CREATED
    return project


async def secret_values(admin_api: KubeApi, project: UUID) -> dict[str, str]:
    secret = await admin_api.read(Resource.SECRET, "catalog-runtime", tenant_namespace(project))
    assert secret is not None
    assert secret["type"] == "Opaque"
    return {k: base64.b64decode(v).decode() for k, v in secret["data"].items()}


async def as_site(
    live: Live, admin_api: KubeApi, project: UUID
) -> psycopg.AsyncConnection[tuple[Any, ...]]:
    values = await secret_values(admin_api, project)
    return await live.connect(values["PGUSER"], values["PGPASSWORD"])


async def one(
    conn: psycopg.AsyncConnection[tuple[Any, ...]], query: str, params: Any = None
) -> Any:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_access_setup_creates_the_role_and_the_secret_then_only_verifies(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    admin_api: KubeApi,
) -> None:
    project = await new_website(live, bootstrap, access_setup)
    role = site_role_name(project)

    assert await bootstrap.runtime_secret_exists(project)
    values = await secret_values(admin_api, project)
    assert set(values) == {"PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE"}
    assert values["PGHOST"] == "webblock-catalog-db"
    assert values["PGPORT"] == "5432"
    assert values["PGUSER"] == role
    assert values["PGSSLMODE"] == "disable"

    admin = await live.connect(*live.tool.login("ACCESS_ADMIN"))
    async with admin:
        cursor = await admin.execute(MEMBERSHIPS, (role,))
        assert await cursor.fetchall() == [("catalog_site", True, False, False)]
        attributes = await one(admin, ROLE_ATTRIBUTES, (role,))
        assert attributes == (True, False, False, False, False, False, False)

    site = await live.connect(role, values["PGPASSWORD"])
    async with site:
        assert await one(site, "SELECT session_user, catalog.current_website_id()") == (
            role,
            project,
        )

    # A second run only verifies: the Secret, and so the password, is unchanged.
    assert await access_setup.run(project, has_live_release=False) is AccessOutcome.VERIFIED
    assert await access_setup.run(project, has_live_release=True) is AccessOutcome.VERIFIED
    assert await secret_values(admin_api, project) == values


@pytest.mark.asyncio
async def test_a_role_without_its_secret_is_refused_while_live_and_redelivered_otherwise(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    site_access: PostgresSiteAccess,
    admin_api: KubeApi,
) -> None:
    project = uuid4()
    assert (
        await bootstrap.ensure_tenant(project, subdomain=subdomain_of(project)) is TenantState.READY
    )
    await register(
        live.tool,
        project,
        "clothing_shop",
        name="no secret",
        timezone="UTC",
        storage_limit_bytes=1024,
        max_image_bytes=1024,
    )
    # The role exists, its Secret does not: a crash after the password, or a deleted Secret.
    assert await site_access.set_site_role(new_runtime_credential(project)) is True
    assert not await bootstrap.runtime_secret_exists(project)

    with pytest.raises(RuntimeSecretMissing):
        await access_setup.run(project, has_live_release=True)
    assert not await bootstrap.runtime_secret_exists(project)

    assert await access_setup.run(project, has_live_release=False) is AccessOutcome.DELIVERED
    site = await as_site(live, admin_api, project)
    async with site:
        assert (await one(site, "SELECT catalog.current_website_id()"))[0] == project


@pytest.mark.asyncio
async def test_each_website_reads_only_its_own_rows(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    admin_api: KubeApi,
) -> None:
    first = await new_website(live, bootstrap, access_setup)
    second = await new_website(live, bootstrap, access_setup)
    loaded_first = await seed_website(live.tool, first, parse_seed(seed_document(2, prefix="a")))
    loaded_second = await seed_website(live.tool, second, parse_seed(seed_document(3, prefix="b")))
    assert (loaded_first.products, loaded_first.images) == (2, 2)
    assert (loaded_second.products, loaded_second.images) == (3, 3)

    for project, products in ((first, 2), (second, 3)):
        site = await as_site(live, admin_api, project)
        async with site:
            assert await one(site, OWN_ROWS) == (1, 1, products, products)
            assert (await one(site, FOREIGN_ROWS, {"w": project}))[0] == 0
            name = (await one(site, "SELECT business_name FROM catalog.business_profiles"))[0]
            assert name == ("a shop" if project == first else "b shop")

    # Seeding twice is refused: the website already has categories.
    with pytest.raises(ToolError, match="already has categories"):
        await seed_website(live.tool, first, parse_seed(seed_document(1, prefix="a")))


@pytest.mark.asyncio
async def test_a_site_role_cannot_read_websites_nor_touch_another_websites_rows(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    admin_api: KubeApi,
) -> None:
    first = await new_website(live, bootstrap, access_setup)
    second = await new_website(live, bootstrap, access_setup)
    await seed_website(live.tool, second, parse_seed(seed_document(2, prefix="victim")))
    site = await as_site(live, admin_api, first)
    async with site:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await site.execute("SELECT * FROM catalog.websites")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await site.execute(
                "UPDATE catalog.websites SET storage_limit_bytes = storage_limit_bytes * 2"
            )
        # Writing a row for the other website is refused by WITH CHECK.
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            await site.execute(
                "INSERT INTO catalog.categories (category_id, website_id, name, slug, sort_order,"
                " created_at, updated_at) VALUES (%s, %s, 'x', 'x', 1, now(), now())",
                (uuid4(), second),
            )
        # The other website's rows are invisible, so updating or deleting them changes nothing.
        cursor = await site.execute(
            "UPDATE catalog.products SET name = 'stolen' WHERE website_id = %s", (second,)
        )
        assert cursor.rowcount == 0
        cursor = await site.execute(
            "DELETE FROM catalog.product_images WHERE website_id = %s", (second,)
        )
        assert cursor.rowcount == 0
        # Moving its own row to the other website is refused by WITH CHECK.
        await seed_website(live.tool, first, parse_seed(seed_document(1, prefix="mover")))
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            await site.execute("UPDATE catalog.categories SET website_id = %s", (second,))

    victim = await as_site(live, admin_api, second)
    async with victim:
        assert await one(victim, OWN_ROWS) == (1, 1, 2, 2)
        names = await (await victim.execute("SELECT name FROM catalog.products")).fetchall()
        assert all(name != "stolen" for (name,) in names)


@pytest.mark.asyncio
async def test_the_storage_limits_hold_and_a_deleted_product_releases_its_bytes(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    admin_api: KubeApi,
) -> None:
    project = await new_website(
        live, bootstrap, access_setup, storage_limit_bytes=3000, max_image_bytes=1500
    )
    await seed_website(live.tool, project, parse_seed(seed_document(2, prefix="s")))  # 2001 bytes
    owner = await live.connect(*live.tool.login("MIGRATOR"))
    used = "SELECT storage_used_bytes FROM catalog.websites WHERE website_id = %s"
    async with owner:
        assert (await one(owner, used, (project,)))[0] == 1000 + 1001
        site = await as_site(live, admin_api, project)
        async with site:
            product = (await one(site, "SELECT product_id FROM catalog.products LIMIT 1"))[0]
            image = (
                "INSERT INTO catalog.product_images (product_image_id, website_id, product_id,"
                " storage_key, byte_size, sort_order, created_at)"
                " VALUES (%s, %s, %s, %s, %s, 9, now())"
            )
            key = f"{project}/products/extra.svg"
            # Over the single-image limit, then over the total limit: both refused.
            with pytest.raises(psycopg.errors.ConfigurationLimitExceeded, match="catalog_storage"):
                await site.execute(image, (uuid4(), project, product, key, 1501))
            with pytest.raises(psycopg.errors.ConfigurationLimitExceeded, match="catalog_storage"):
                await site.execute(image, (uuid4(), project, product, key, 1000))
            await site.execute("DELETE FROM catalog.products WHERE product_id = %s", (product,))
        remaining = (await one(owner, used, (project,)))[0]
        assert remaining in (1000, 1001)


@pytest.mark.asyncio
async def test_the_worker_cannot_read_the_secret_and_bootstrap_reads_only_that_name(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    worker_api: KubeApi,
    bootstrap_api: KubeApi,
) -> None:
    project = await new_website(live, bootstrap, access_setup)
    namespace = tenant_namespace(project)

    with pytest.raises(KubernetesForbidden):
        await worker_api.read(Resource.SECRET, "catalog-runtime", namespace)
    with pytest.raises(KubernetesForbidden):
        await bootstrap_api.read(Resource.SECRET, "some-other-secret", namespace)
    assert await bootstrap_api.read(Resource.SECRET, "catalog-runtime", namespace) is not None


async def _run(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    async with asyncio.timeout(600):
        out, err = await process.communicate()
    assert process.returncode == 0, (args[:2], err.decode(errors="replace")[-2000:])
    return out.decode()


async def push_probe_image(live: Live, project: UUID) -> str:
    """Build the probe image, push it under this project's repository and return its reference as
    the nodes pull it, by digest."""
    repository = f"{live.registry_on_host}/webblock/sites/{project}"
    tag = f"{repository}:catalog-probe"
    await _run("docker", "build", "-q", "-t", tag, str(PROBE))
    try:
        await _run("docker", "push", "-q", tag)
        digests = await _run(
            "docker",
            "image",
            "inspect",
            "--format",
            "{{range .RepoDigests}}{{println .}}{{end}}",
            tag,
        )
    finally:
        await _run("docker", "image", "rm", tag)
    digest = next(d for d in digests.split() if d.startswith(repository + "@")).split("@")[1]
    return f"{live.image_registry}/webblock/sites/{project}@{digest}"


@pytest.mark.asyncio
async def test_a_deployment_with_envfrom_starts_and_its_pod_logs_in_as_its_role(
    live: Live,
    bootstrap: KubernetesTenantBootstrap,
    access_setup: CatalogAccessSetup,
    worker_api: KubeApi,
    admin_api: KubeApi,
) -> None:
    project = await new_website(live, bootstrap, access_setup)
    await seed_website(live.tool, project, parse_seed(seed_document(3, prefix="pod")))
    image = await push_probe_image(live, project)
    offline = await admin_api.read(Resource.SERVICE, OFFLINE_SERVICE, OFFLINE_NAMESPACE)
    assert offline is not None
    settings = SiteClusterSettings(
        site=SiteSettings(
            cpu_millicores=100,
            memory_mib=128,
            progress_deadline_seconds=60,
            readiness_deadline_seconds=120,
            offline_service_port=int(offline["spec"]["ports"][0]["port"]),
        ),
        gateway_url=live.gateway_url,
        probe_path="/readyz",
        probe_timeout_seconds=5.0,
        delete_retry_base_seconds=0.25,
        site_domain=SITE_DOMAIN,
        preview_token=PREVIEW_TOKEN,
    )
    host = f"{subdomain_of(project)}.{SITE_DOMAIN}"
    async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
        cluster = KubernetesSiteCluster(worker_api, settings, http)
        workload = ReleaseWorkload(project, uuid4(), image, command_number())
        await cluster.apply_release(workload)
        status = await until(
            lambda: cluster.release_status(workload),
            lambda s: s.state is not WorkloadState.PROGRESSING,
            READY_WITHIN,
        )
        assert status.state is WorkloadState.READY, status

        deployment = await admin_api.read(
            Resource.DEPLOYMENT, release_name(workload.release_id), tenant_namespace(project)
        )
        assert deployment is not None
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        assert container["envFrom"] == [{"secretRef": {"name": "catalog-runtime"}}]

        await cluster.apply_route(
            project,
            RouteTarget(RouteTargetKind.RELEASE, workload.release_id),
            (host,),
            command_number(),
            subdomain=subdomain_of(project),
        )

        async def page() -> str:
            try:
                response = await http.get(live.gateway_url + "/", headers={"Host": host})
            except httpx2.HTTPError:
                return ""
            return response.text if response.status_code == 200 else ""

        expected = f"catalog-probe {site_role_name(project)} {project} 3\n"
        assert await until(page, lambda text: text == expected, 90.0) == expected

        await cluster.apply_route(
            project,
            RouteTarget(RouteTargetKind.OFFLINE),
            (host,),
            command_number(),
            subdomain=subdomain_of(project),
        )
        await cluster.delete_release(project, workload.release_id, command_number())
