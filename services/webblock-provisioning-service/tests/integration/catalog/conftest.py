"""Live tests of the catalog database and access_setup on the prototype stack (task H; decision 0014
section 14). Marked ``catalog_live`` by directory; a skip is a failure.

They need ``make proto-up`` (the Kind cluster ``webblock-proto`` and the catalog container
``webblock-catalog-db``) and read only what it writes under ``bin/kind-state/proto/``:
``stack.env``, ``catalog-admin.env`` and the admin, worker and bootstrap kubeconfigs. Every
Kubernetes client is built by ``connect_kubeconfig``, which refuses any context that is not
``kind-webblock-*`` or ``k3d-webblock-*`` on ``https://127.0.0.1:<port>``; ``KUBECONFIG`` is
poisoned before the library is imported and ``HOME`` and ``KUBECONFIG`` for every test, so the
owner's real cluster can never be reached (CLAUDE.md section 0). The catalog is reached on
127.0.0.1 only.

Each test uses fresh project IDs. Tenants, catalog rows and site roles stay behind;
``make proto-down`` removes them with the stack.
"""

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
import pytest_asyncio

# Before the first import of kubernetes_asyncio, which fixes its default path at import.
DEFAULT_KUBECONFIG_POISON = str(
    Path(__file__).resolve().parent / "never-a-kubeconfig" / "poisoned-default"
)
os.environ["KUBECONFIG"] = DEFAULT_KUBECONFIG_POISON

from provisioning.hosts.devtools.catalog_platform import (  # noqa: E402 - after the poisoning above
    Stack,
    read_env_file,
)
from provisioning.infrastructure.catalog import (  # noqa: E402 - as above
    AdminLogin,
    CatalogAccessSetup,
    CatalogConnection,
    PostgresSiteAccess,
    RuntimeEndpoint,
)
from provisioning.infrastructure.kubernetes import (  # noqa: E402 - as above
    BOOTSTRAP_FIELD_MANAGER,
    WORKER_FIELD_MANAGER,
    KubeApi,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import (  # noqa: E402 - as above
    KubernetesTenantBootstrap,
)
from provisioning.infrastructure.kubernetes.render import TenantSettings  # noqa: E402 - as above

REPO = Path(__file__).resolve().parents[3]
STATE = REPO / "bin" / "kind-state" / "proto"
STACK_ENV = STATE / "stack.env"
TIMEOUT = 10.0
ADMIN_FIELD_MANAGER = "webblock-test-catalog-admin"


@dataclass(frozen=True, slots=True)
class Live:
    """The running stack as the tests see it."""

    env: dict[str, str]
    tool: Stack  # the catalog_platform stand-in's view: host address and logins

    @property
    def catalog_host(self) -> str:
        return self.env["PROTO_CATALOG_HOST_ON_HOST"]

    @property
    def catalog_port(self) -> int:
        return int(self.env["PROTO_CATALOG_PORT_ON_HOST"])

    @property
    def database(self) -> str:
        return self.env["PROTO_CATALOG_DATABASE"]

    @property
    def gateway_url(self) -> str:
        return "http://" + self.env["PROTO_GATEWAY_ADDRESS"]

    @property
    def image_registry(self) -> str:
        return self.env["PROTO_IMAGE_REGISTRY"]

    @property
    def registry_on_host(self) -> str:
        return self.env["PROTO_REGISTRY_ON_HOST"]

    def endpoint(self) -> RuntimeEndpoint:
        host, _, port = self.env["PROTO_CATALOG_IN_CLUSTER"].rpartition(":")
        return RuntimeEndpoint(host=host, port=int(port), database=self.database, sslmode="disable")

    async def connect(self, user: str, password: str) -> psycopg.AsyncConnection[tuple[Any, ...]]:
        return await psycopg.AsyncConnection.connect(
            host=self.catalog_host,
            port=self.catalog_port,
            dbname=self.database,
            user=user,
            password=password,
            sslmode="disable",
            connect_timeout=10,
            require_auth="scram-sha-256",
            autocommit=True,
        )


@pytest.fixture
def live() -> Live:
    if not STACK_ENV.is_file():
        pytest.fail(f"{STACK_ENV} is missing: run make proto-up first")
    env = read_env_file(STACK_ENV)
    if "PROTO_CATALOG_PORT_ON_HOST" not in env:
        pytest.fail("stack.env has no catalog: run make proto-up with task H's stack")
    return Live(env=env, tool=Stack.load(STACK_ENV))


@pytest.fixture(autouse=True)
def poisoned_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "poisoned-home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube" / "config").write_text("this file must never be read\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(home / ".kube" / "config"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE", "PGSERVICE"):
        monkeypatch.delenv(name, raising=False)


async def _connect(file: str, manager: str) -> KubeApi:
    path = STATE / file
    if not path.is_file():
        pytest.fail(f"{path} is missing: run make proto-up first")
    return await connect_kubeconfig(path, field_manager=manager, request_timeout_seconds=TIMEOUT)


@pytest_asyncio.fixture
async def worker_api() -> AsyncIterator[KubeApi]:
    api = await _connect("worker.kubeconfig", WORKER_FIELD_MANAGER)
    try:
        yield api
    finally:
        await api.close()


@pytest_asyncio.fixture
async def bootstrap_api() -> AsyncIterator[KubeApi]:
    api = await _connect("bootstrap.kubeconfig", BOOTSTRAP_FIELD_MANAGER)
    try:
        yield api
    finally:
        await api.close()


@pytest_asyncio.fixture
async def admin_api() -> AsyncIterator[KubeApi]:
    """Kind's admin identity: reads the Secret's content for the checks, writes nothing."""
    api = await _connect("kubeconfig", ADMIN_FIELD_MANAGER)
    try:
        yield api
    finally:
        await api.close()


@pytest.fixture
def bootstrap(bootstrap_api: KubeApi) -> KubernetesTenantBootstrap:
    return KubernetesTenantBootstrap(
        bootstrap_api,
        TenantSettings(
            quota_cpu="2",
            quota_memory="2Gi",
            quota_pods=10,
            container_max_cpu="1",
            container_max_memory="1Gi",
        ),
    )


@pytest.fixture
def site_access(live: Live) -> PostgresSiteAccess:
    user, password = live.tool.login("ACCESS_ADMIN")
    return PostgresSiteAccess(
        CatalogConnection(
            host=live.catalog_host,
            port=live.catalog_port,
            database=live.database,
            sslmode="disable",
            connect_timeout_seconds=10,
            statement_timeout_ms=10_000,
            lock_timeout_ms=2_000,
        ),
        AdminLogin(user=user, password=password),
    )


@pytest.fixture
def access_setup(
    live: Live, site_access: PostgresSiteAccess, bootstrap: KubernetesTenantBootstrap
) -> CatalogAccessSetup:
    return CatalogAccessSetup(site_access=site_access, secrets=bootstrap, endpoint=live.endpoint())
