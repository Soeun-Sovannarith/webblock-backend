"""Live Kind tests of the Kubernetes adapter against task A's prototype stack (decision 0014).

Marked ``kind`` by directory; a skip is a failure. They run only against the project's own Kind
cluster ``webblock-proto`` (``make proto-up``), through the kubeconfigs task A writes under
``bin/kind-state/proto/``. ``connect_kubeconfig`` refuses any context that is not
``kind-webblock-*`` or ``k3d-webblock-*`` and any server that is not ``https://127.0.0.1:<port>``,
and this file poisons ``KUBECONFIG`` before the library is imported and ``HOME`` and
``KUBECONFIG`` for every test, so the owner's real cluster can never be reached (CLAUDE.md
section 0).

Inputs, all required (a missing one fails the run):
- ``WEBBLOCK_KIND_SITE_IMAGE``: the sample site built by the stack's Jenkins, by digest, under
  ``<registry>/webblock/sites/<projectId>``; the tests use that project ID.
- ``WEBBLOCK_KIND_NEVER_READY_IMAGE``: the variant whose container exits at once, in the same
  repository.
- ``WEBBLOCK_KIND_GATEWAY_URL``: where the Gateway ``webblock-public`` answers from this machine,
  for example ``http://172.18.255.200``.

Two helpers of task W6-M:
- ``kubectl(...)`` runs the pinned ``bin/kubectl`` with the stack's admin kubeconfig
  ``bin/kind-state/proto/kubeconfig`` only, after checking that its context is
  ``<engine>-webblock-proto`` on ``https://127.0.0.1:<port>`` (CLAUDE.md section 0), with a cache
  folder and ``HOME`` under ``bin/kind-state/proto/``. The tests use it to edit an object under a
  foreign field manager, as a person would.
- ``build_site(stack, variant)`` builds one of the sites under ``sites/`` (the QA variants
  ``qa-huge-readyz`` and ``qa-flap``) through the stack's Jenkins, for the stack's project, the
  way ``deploy/kind/proto-test-kind.sh`` builds the sample site, and returns the image by digest.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx2
import pytest
import pytest_asyncio
from tests.integration.kind_safety import expected_context

# Before the first import of kubernetes_asyncio, which fixes its default path at import.
DEFAULT_KUBECONFIG_POISON = str(
    Path(__file__).resolve().parent / "never-a-kubeconfig" / "poisoned-default"
)
os.environ["KUBECONFIG"] = DEFAULT_KUBECONFIG_POISON

from provisioning.infrastructure.kubernetes import (  # noqa: E402 - after the poisoning above
    BOOTSTRAP_FIELD_MANAGER,
    WORKER_FIELD_MANAGER,
    KubeApi,
    Resource,
    connect_kubeconfig,
)
from provisioning.infrastructure.kubernetes.bootstrap import (  # noqa: E402 - as above
    KubernetesTenantBootstrap,
)
from provisioning.infrastructure.kubernetes.render import (  # noqa: E402 - as above
    OFFLINE_NAMESPACE,
    OFFLINE_SERVICE,
    SiteSettings,
    TenantSettings,
)
from provisioning.infrastructure.kubernetes.site_cluster import (  # noqa: E402 - as above
    KubernetesSiteCluster,
    SiteClusterSettings,
)

REPO = Path(__file__).resolve().parents[3]
STATE = REPO / "bin" / "kind-state" / "proto"
TIMEOUT = 10.0
FOREIGN_FIELD_MANAGER = "webblock-test-foreign"
SITE_DOMAIN = "sites.webblock.test"
PREVIEW_TOKEN = "kind-live-preview-token-0123456789abcdef"  # noqa: S105 - a test value
_IMAGE_PROJECT = re.compile(r".*/webblock/sites/([0-9a-f-]{36})@sha256:[0-9a-f]{64}")


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.fail(f"{name} is required for the kind suite (see tests/integration/kubernetes)")
    return value


@dataclass(frozen=True, slots=True)
class Stack:
    project_id: UUID
    site_image: str
    never_ready_image: str
    gateway_url: str

    @property
    def subdomain(self) -> str:
        """The test project's sub-domain (its hosting row's copy, decision 0023 G18): the site
        answers at ``<subdomain>.sites.webblock.test`` and the namespace carries it as a label."""
        return f"kind-{self.project_id.hex}"


@pytest.fixture
def stack() -> Stack:
    return stack_from_env()


def stack_from_env() -> Stack:
    site = required("WEBBLOCK_KIND_SITE_IMAGE")
    never_ready = required("WEBBLOCK_KIND_NEVER_READY_IMAGE")
    projects = {m.group(1) for m in map(_IMAGE_PROJECT.fullmatch, (site, never_ready)) if m}
    if len(projects) != 1 or not all(_IMAGE_PROJECT.fullmatch(i) for i in (site, never_ready)):
        pytest.fail("both images must be <registry>/webblock/sites/<one projectId>@sha256:...")
    return Stack(
        project_id=UUID(projects.pop()),
        site_image=site,
        never_ready_image=never_ready,
        gateway_url=required("WEBBLOCK_KIND_GATEWAY_URL"),
    )


@pytest.fixture(autouse=True)
def poisoned_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "poisoned-home"
    (home / ".kube").mkdir(parents=True)
    (home / ".kube" / "config").write_text("this file must never be read\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KUBECONFIG", str(home / ".kube" / "config"))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)


async def _connect(file: str, manager: str) -> KubeApi:
    path = STATE / file
    if not path.is_file():
        pytest.fail(f"{path} is missing: run make proto-up (task A) first")
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
async def foreign_api() -> AsyncIterator[KubeApi]:
    """Kind's admin identity under another field manager, to own a field the worker applies."""
    api = await _connect("kubeconfig", FOREIGN_FIELD_MANAGER)
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


@pytest_asyncio.fixture(autouse=True)
async def catalog_runtime_secret(stack: Stack, bootstrap: KubernetesTenantBootstrap) -> None:
    """Every rendered Deployment takes the Secret catalog-runtime through ``envFrom`` (decision
    0014 section 14, task H), and a pod whose Secret is missing never starts. The sample site of
    this suite does not use the catalog, so the tenant gets a placeholder Secret (written by the
    bootstrap identity, as access_setup would) when it has none; tests/integration/catalog proves
    the real one."""
    await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
    if not await bootstrap.runtime_secret_exists(stack.project_id):
        await bootstrap.write_runtime_secret(
            stack.project_id,
            {
                "PGHOST": "webblock-catalog-db",
                "PGPORT": "5432",
                "PGDATABASE": "catalog",
                "PGUSER": f"site_{stack.project_id.hex}",
                "PGPASSWORD": "placeholder-the-sample-site-never-connects",
                "PGSSLMODE": "disable",
            },
        )


@pytest_asyncio.fixture
async def site_settings(foreign_api: KubeApi) -> SiteSettings:
    offline = await foreign_api.read(Resource.SERVICE, OFFLINE_SERVICE, OFFLINE_NAMESPACE)
    if offline is None:
        pytest.fail("the offline Service webblock-offline/offline-page is missing (task A)")
    port = offline["spec"]["ports"][0]["port"]
    return SiteSettings(
        cpu_millicores=100,
        memory_mib=128,
        progress_deadline_seconds=60,
        readiness_deadline_seconds=120,
        offline_service_port=int(port),
    )


@pytest.fixture
def cluster_settings(site_settings: SiteSettings, stack: Stack) -> SiteClusterSettings:
    """The worker's settings in these tests: the probe defaults (one answer, a 64 KiB bound)."""
    return SiteClusterSettings(
        site=site_settings,
        gateway_url=stack.gateway_url,
        probe_path="/readyz",
        probe_timeout_seconds=5.0,
        delete_retry_base_seconds=0.25,
        site_domain=SITE_DOMAIN,
        preview_token=PREVIEW_TOKEN,
    )


@pytest_asyncio.fixture
async def cluster(
    worker_api: KubeApi, cluster_settings: SiteClusterSettings
) -> AsyncIterator[KubernetesSiteCluster]:
    async with httpx2.AsyncClient(trust_env=False) as http:
        yield KubernetesSiteCluster(worker_api, cluster_settings, http)


# --- kubectl as the stack's administrator, and site images built through Jenkins (task W6-M) -----

ADMIN_KUBECONFIG = STATE / "kubeconfig"
KUBECTL = REPO / "bin" / "kubectl"
SITES = Path(__file__).resolve().parent / "sites"
GIT_NAME = "webblock-git"  # PROTO_GIT_NAME of deploy/prototype/versions.sh
_TOOL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(STATE / "home")}


def _run(
    argv: list[str], *, stdin: str | None = None, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - a fixed argument list of pinned tools, no shell
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_TOOL_ENV,
        check=False,
    )


def _kubectl_raw(args: tuple[str, ...], stdin: str | None) -> tuple[int, str]:
    if not ADMIN_KUBECONFIG.is_file() or not KUBECTL.is_file():
        pytest.fail(f"{ADMIN_KUBECONFIG} or {KUBECTL} is missing: run make proto-up first")
    argv = [str(KUBECTL), "--kubeconfig", str(ADMIN_KUBECONFIG)]
    argv += ["--cache-dir", str(STATE / "kube-cache"), *args]
    done = _run(argv, stdin=stdin)
    return done.returncode, done.stdout + done.stderr


def check_admin_context() -> None:
    """The owner's rule: the project's Kind cluster on the loopback address, nothing else."""
    rc, out = _kubectl_raw(("config", "view", "--minify", "-o", "json"), None)
    assert rc == 0, out[-500:]
    view = json.loads(out)
    assert view["current-context"] == expected_context(), view["current-context"]
    server = view["clusters"][0]["cluster"]["server"]
    assert re.fullmatch(r"https://127\.0\.0\.1:[0-9]{1,5}", server), server


def kubectl(*args: str, stdin: str | None = None) -> str:
    """``bin/kubectl`` with the admin kubeconfig, after the context check; fails on an error."""
    check_admin_context()
    rc, out = _kubectl_raw(args, stdin)
    assert rc == 0, f"kubectl {' '.join(args)} exited {rc}: {out[-1500:]}"
    return out


_GIT = [
    "git",
    "-c",
    "user.name=WebBlock live test",
    "-c",
    "user.email=live@webblock.test",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "init.defaultBranch=main",
]
_GIT_ENV = {**_TOOL_ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
_built: dict[tuple[UUID, str], str] = {}


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - a fixed git argument list, no shell
        [*_GIT, *args],
        cwd=cwd,
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, f"git {args[0]} failed: {done.stderr[-500:]}"
    return done.stdout.strip()


def _commit(repository: str, variant: str) -> str:
    """A new commit of ``sites/<variant>`` in the stack's bare repository, as
    scripts/prototype/sample-repo.sh makes one; returns its SHA."""
    source = SITES / variant
    assert (source / "Dockerfile").is_file(), source
    bare = STATE / "git" / f"{repository}.git"
    if not bare.is_dir():
        _git("init", "--quiet", "--bare", str(bare))
    work = Path(tempfile.mkdtemp(prefix=".live-site.", dir=STATE))
    try:
        repo = work / "repo"
        _git("clone", "--quiet", str(bare), str(repo))
        _git("rm", "-rq", "--ignore-unmatch", "--", ".", cwd=repo)
        shutil.copytree(source, repo, dirs_exist_ok=True)
        _git("add", "-A", cwd=repo)
        _git("commit", "--quiet", "--allow-empty", "-m", f"live {variant}", cwd=repo)
        _git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=repo)
        _git("--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main")
        _git("--git-dir", str(bare), "update-server-info")
        return _git("rev-parse", "HEAD", cwd=repo)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_site(stack: Stack, variant: str) -> str:
    """The image of ``sites/<variant>`` for the stack's project, built once per run through the
    stack's Jenkins (scripts/prototype/jenkins-build.sh), as ``<repository>@sha256:<digest>``."""
    key = (stack.project_id, variant)
    if key in _built:
        return _built[key]
    repository = stack.site_image.split("@", 1)[0]
    sha = _commit("kind-suite-live", variant)
    script = REPO / "scripts" / "prototype" / "jenkins-build.sh"
    git_url = f"http://{GIT_NAME}:8080/kind-suite-live.git"
    done = _run(
        ["bash", str(script), str(stack.project_id), str(uuid4()), "1", git_url, sha, repository],
        timeout=1200,
    )
    assert done.returncode == 0, f"the Jenkins build of {variant} failed: {done.stderr[-1500:]}"
    result = json.loads(done.stdout)
    assert not result.get("failure_code"), result
    assert result["commit_sha"] == sha, result
    digest = result["image_digest"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), result
    _built[key] = f"{repository}@{digest}"
    return _built[key]
