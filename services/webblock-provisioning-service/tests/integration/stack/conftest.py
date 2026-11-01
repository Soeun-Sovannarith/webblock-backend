"""Live tests of the prototype stack itself (tasks T1 and W6-K; decision 0014 sections 16, 21 and
23): the Gateway's HTTPS listener with the local CA's wildcard certificate, the per-site admission
policies, the tenant network policy, the worker's RBAC, and Loki with its log shipper. Marked
``kind`` by directory; a skip is a failure. Run with ``make proto-test-kind`` or ``pytest -m kind
tests/integration/stack`` after ``make proto-up``.

No Provisioning process runs here. Tenants are written by the bootstrap identity's own token and
releases and routes by the worker's own token, as the renderer
(``provisioning.infrastructure.kubernetes.render``) builds them; Kind's admin only reads, deletes
the test tenants afterwards, and impersonates identities for the admission checks. Cluster safety
(CLAUDE.md section 0): kubectl gets ``--kubeconfig``, ``--context`` and a project-local
``--cache-dir`` on every call, runs with a poisoned ``HOME`` and ``KUBECONFIG``, and each kubeconfig
is checked first to name the context ``<engine>-webblock-proto`` on ``https://127.0.0.1:<port>``."""

import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from tests.integration.stack._stack import (
    ADMIN,
    BOOTSTRAP,
    WORKER,
    Kubectl,
    Stack,
    Tenant,
    command_number,
    plain_namespace,
    release_objects,
    site_image,
    stack_env,
    tenant_objects,
)


@pytest.fixture(scope="session")
def kubectl(tmp_path_factory: pytest.TempPathFactory) -> Kubectl:
    home = tmp_path_factory.mktemp("poisoned-home")
    (home / ".kube").mkdir()
    (home / ".kube" / "config").write_text("this file must never be read\n")
    return Kubectl(home)


@pytest.fixture(scope="session")
def stack() -> Stack:
    env = stack_env()
    address = env.get("PROTO_GATEWAY_ADDRESS", "")
    url = env.get("PROTO_GATEWAY_HTTPS_URL", "")
    ca = env.get("PROTO_LOCAL_CA", "")
    registry = env.get("PROTO_IMAGE_REGISTRY", "")
    on_host = env.get("PROTO_REGISTRY_ON_HOST", "")
    registry_ca = env.get("PROTO_REGISTRY_CA", "")
    if not address or not url.startswith(f"https://{address}:") or not ca:
        pytest.fail(
            "stack.env lacks PROTO_GATEWAY_ADDRESS, PROTO_GATEWAY_HTTPS_URL "
            "(https://<address>:<port>) or PROTO_LOCAL_CA: run make proto-up on this branch"
        )
    if not registry or not on_host or not registry_ca:
        pytest.fail("stack.env lacks the registry addresses: run make proto-up on this branch")
    local_ca = Path(ca)
    if not local_ca.is_file():
        pytest.fail(f"{local_ca} is missing: run make proto-up")
    return Stack(
        address, int(url.rsplit(":", 1)[1]), local_ca, registry, on_host, Path(registry_ca)
    )


@pytest.fixture(scope="session")
def offline_image(kubectl: Kubectl) -> str:
    """The offline page's unprivileged nginx, by digest, in the stack's registry."""
    image = kubectl.ok(
        ADMIN,
        "-n",
        "webblock-offline",
        "get",
        "deployment",
        "offline-page",
        "-o",
        "jsonpath={.spec.template.spec.containers[0].image}",
    ).strip()
    assert "@sha256:" in image, f"the offline page's image is not pinned by digest: {image}"
    return image


TenantFactory = Callable[[], Tenant]


@pytest.fixture(scope="session")
def make_tenant(
    kubectl: Kubectl, stack: Stack, offline_image: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TenantFactory]:
    """Makes tenants as the platform does: bootstrap's token writes the renderer's tenant objects,
    then the worker's token applies one release (renderer's Deployment and Service, image of the
    tenant's own repository) and waits until it is ready. Every tenant is deleted afterwards."""
    made: list[UUID] = []

    def make() -> Tenant:
        project = uuid4()
        made.append(project)
        for manifest in tenant_objects(project):
            kubectl.apply(BOOTSTRAP, manifest)
        image = site_image(stack, project, offline_image, tmp_path_factory.mktemp("crane"))
        release = uuid4()
        for manifest in release_objects(project, release, image, command_number()):
            kubectl.apply(WORKER, manifest)
        namespace = f"wb-{project}"
        kubectl.ok(
            WORKER,
            "-n",
            namespace,
            "rollout",
            "status",
            f"deployment/rel-{release}",
            "--timeout=180s",
        )
        return Tenant(project, namespace, release, image)

    try:
        yield make
    finally:
        for project in made:
            kubectl.run(ADMIN, "delete", "namespace", f"wb-{project}", "--wait=false")
            kubectl.run(
                ADMIN, "-n", "webblock-offline", "delete", "referencegrant", f"wb-{project}"
            )


@pytest.fixture(scope="session")
def tenant(make_tenant: TenantFactory) -> Tenant:
    return make_tenant()


@pytest.fixture(scope="session")
def other_tenant(make_tenant: TenantFactory) -> Tenant:
    return make_tenant()


@pytest.fixture
def scratch_namespace(kubectl: Kubectl) -> Iterator[str]:
    """A namespace WITHOUT the tenant label (Kind's admin), deleted afterwards."""
    name = f"webblock-stack-test-{secrets.token_hex(4)}"
    kubectl.apply(ADMIN, plain_namespace(name))
    try:
        yield name
    finally:
        kubectl.run(ADMIN, "delete", "namespace", name, "--wait=false")
