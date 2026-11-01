"""Helpers of the live stack tests (tasks T1 and W6-K): the identities, a kubectl runner that
checks each kubeconfig first (``kind_safety``) and always passes ``--kubeconfig``, ``--context``
and a project-local ``--cache-dir`` with a poisoned ``HOME`` and ``KUBECONFIG`` (CLAUDE.md section
0), the stack's addresses from ``stack.env``, an HTTP client (curl), the per-site objects as the
renderer writes them, and a site image of a test project in the stack's registry. See
``conftest.py``."""

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from tests.integration.kind_safety import (
    KUBECTL,
    KUBECTL_TIMEOUT,
    POISON,
    check_local,
    expected_context,
    kube_env,
)

REPO = Path(__file__).resolve().parents[3]
STATE = REPO / "bin" / "kind-state" / "proto"
# Before the renderer's package is imported: it imports kubernetes_asyncio, which fixes its default
# kubeconfig path at import. Nothing here loads a kubeconfig through that library.
os.environ["KUBECONFIG"] = POISON

from provisioning.core.application.ports.site_cluster import (  # noqa: E402 - after the poisoning
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
)
from provisioning.infrastructure.kubernetes.render import (  # noqa: E402 - as above
    SiteSettings,
    TenantSettings,
    render_deployment,
    render_limit_range,
    render_namespace,
    render_reference_grant,
    render_resource_quota,
    render_route,
    render_runtime_secret,
    render_service,
    render_worker_role_binding,
)

CRANE = REPO / "bin" / "crane"
ADMIN_KUBECONFIG = STATE / "kubeconfig"
WORKER_KUBECONFIG = STATE / "worker.kubeconfig"
BOOTSTRAP_KUBECONFIG = STATE / "bootstrap.kubeconfig"
STACK_ENV = STATE / "stack.env"
CACHE_DIR = STATE / "kube-cache-stack-tests"

WORKER_USER = "system:serviceaccount:webblock:provisioning-worker"
BOOTSTRAP_USER = "system:serviceaccount:webblock:tenant-bootstrap"
WORKER_FIELD_MANAGER = "webblock-provisioning-worker"
BOOTSTRAP_FIELD_MANAGER = "webblock-tenant-bootstrap"
TEST_FIELD_MANAGER = "webblock-stack-tests"
GATEWAY_NAMESPACE = "webblock-gateway"
GATEWAY_NAME = "webblock-public"
SITE_SUFFIX = "sites.webblock.test"
RELEASE_HEADER = "X-Webblock-Release"
GATEWAY_GROUP = "gateway.networking.k8s.io"
OFFLINE_PORT = 8080  # PROTO_OFFLINE_SERVICE in stack.env

SITE_SETTINGS = SiteSettings(
    cpu_millicores=100,
    memory_mib=128,
    progress_deadline_seconds=60,
    readiness_deadline_seconds=120,
    offline_service_port=OFFLINE_PORT,
)
TENANT_SETTINGS = TenantSettings(
    quota_cpu="2",
    quota_memory="2Gi",
    quota_pods=10,
    container_max_cpu="1",
    container_max_memory="1Gi",
)


@dataclass(frozen=True, slots=True)
class Identity:
    name: str
    kubeconfig: Path


ADMIN = Identity("admin", ADMIN_KUBECONFIG)
WORKER = Identity("worker", WORKER_KUBECONFIG)
BOOTSTRAP = Identity("bootstrap", BOOTSTRAP_KUBECONFIG)
_MANAGERS = {"worker": WORKER_FIELD_MANAGER, "bootstrap": BOOTSTRAP_FIELD_MANAGER}


@dataclass(frozen=True, slots=True)
class Kubectl:
    home: Path

    def run(
        self, who: Identity, *args: str, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        check_local(who.kubeconfig, self.home)
        return subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [
                str(KUBECTL),
                "--kubeconfig",
                str(who.kubeconfig),
                "--context",
                expected_context(),
                "--cache-dir",
                f"{CACHE_DIR}-{who.name}",
                "--request-timeout=30s",
                *args,
            ],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT,
            env=kube_env(self.home),
            check=False,
        )

    def popen(self, who: Identity, *args: str) -> subprocess.Popen[str]:
        """A kubectl that runs in the background (its standard output piped), after the same
        kubeconfig check and with the same arguments and environment as ``run``."""
        check_local(who.kubeconfig, self.home)
        return subprocess.Popen(  # noqa: S603 - fixed argument list, no shell
            [
                str(KUBECTL),
                "--kubeconfig",
                str(who.kubeconfig),
                "--context",
                expected_context(),
                "--cache-dir",
                f"{CACHE_DIR}-{who.name}",
                *args,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=kube_env(self.home),
        )

    def ok(self, who: Identity, *args: str, stdin: str | None = None) -> str:
        done = self.run(who, *args, stdin=stdin)
        assert done.returncode == 0, f"{who.name} kubectl {args[0]}: {done.stderr.strip()}"
        return done.stdout

    def apply_args(self, manager: str, *extra: str) -> list[str]:
        return [
            "apply",
            "--server-side",
            f"--field-manager={manager}",
            "--validate=strict",
            *extra,
            "-f",
            "-",
        ]

    def apply(self, who: Identity, manifest: dict[str, Any], *extra: str) -> None:
        """Server-side apply as the worker and bootstrap write (strict validation, never forced)."""
        manager = _MANAGERS.get(who.name, TEST_FIELD_MANAGER)
        self.ok(who, *self.apply_args(manager, *extra), stdin=json.dumps(manifest))

    def try_apply_as(
        self, user: str, manifest: dict[str, Any], *groups: str, force: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """A server-side dry run of ``manifest`` by Kind's admin impersonating ``user`` (and
        ``groups``): RBAC and admission judge it as that identity; nothing is stored. ``force``
        takes fields another manager owns, so that admission, not a field conflict, decides."""
        manager = WORKER_FIELD_MANAGER if user == WORKER_USER else TEST_FIELD_MANAGER
        identity = ["--as", user, *[f"--as-group={g}" for g in groups]]
        extra = ["--dry-run=server", *(["--force-conflicts"] if force else [])]
        return self.run(
            ADMIN,
            *identity,
            *self.apply_args(manager, *extra),
            stdin=json.dumps(manifest),
        )

    def get(self, who: Identity, kind: str, name: str, namespace: str) -> dict[str, Any]:
        done = self.run(who, "-n", namespace, "get", kind, name, "-o", "json")
        if done.returncode != 0:
            return {}
        loaded: dict[str, Any] = json.loads(done.stdout)
        return loaded

    def can_i(self, verb: str, resource: str, namespace: str | None) -> bool:
        """``kubectl auth can-i`` by Kind's admin, impersonating the worker's ServiceAccount;
        ``namespace`` None asks cluster-wide."""
        where = ["-n", namespace] if namespace is not None else []
        done = self.run(ADMIN, "auth", "can-i", verb, resource, *where, "--as", WORKER_USER)
        answer = done.stdout.strip()
        assert answer in ("yes", "no"), f"auth can-i {verb} {resource}: {done.stderr.strip()}"
        return answer == "yes"


@dataclass(frozen=True, slots=True)
class Stack:
    gateway_address: str
    https_port: int
    local_ca: Path
    image_registry: str
    registry_on_host: str
    registry_ca: Path


@dataclass(frozen=True, slots=True)
class Tenant:
    """A tenant ``wb-<project>`` that bootstrap created, with one release the worker deployed
    (ready) from ``image``, the test project's copy of the offline page's nginx (default page on
    8080)."""

    project: UUID
    namespace: str
    release: UUID
    image: str

    @property
    def service(self) -> str:
        return f"rel-{self.release}"

    @property
    def subdomain(self) -> str:
        return tenant_subdomain(self.project)

    @property
    def host(self) -> str:
        """``<subdomain>.<site domain>`` (decision 0023 G18)."""
        return f"{self.subdomain}.{SITE_SUFFIX}"


def stack_env() -> dict[str, str]:
    """The non-secret addresses ``make proto-up`` wrote; a missing file fails the run."""
    if not STACK_ENV.is_file():
        pytest.fail(f"{STACK_ENV} is missing: run make proto-up first")
    values: dict[str, str] = {}
    for line in STACK_ENV.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def poll[T](what: str, seconds: float, probe: Callable[[], T | None]) -> T:
    """Call ``probe`` every 2 s until it returns a value other than None; fail after ``seconds``."""
    deadline = time.monotonic() + seconds
    while True:
        value = probe()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            pytest.fail(f"timed out after {seconds:.0f}s waiting for {what}")
        time.sleep(2)


def condition(conditions: list[dict[str, Any]] | None, kind: str) -> dict[str, Any] | None:
    for item in conditions or []:
        if item.get("type") == kind:
            return item
    return None


def is_true(conditions: list[dict[str, Any]] | None, kind: str, generation: int) -> bool:
    found = condition(conditions, kind)
    return (
        found is not None
        and found.get("status") == "True"
        and found.get("observedGeneration", generation) == generation
    )


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: list[tuple[str, str]]
    body: str

    def header_values(self, name: str) -> list[str]:
        return [value for key, value in self.headers if key.lower() == name.lower()]


def curl(
    url: str, *, resolve: str | None, cacert: Path | None, work: Path, timeout: float = 5.0
) -> Response | None:
    """One request with curl; None on a transport or TLS failure. ``resolve`` is curl's
    ``host:port:address``. With ``cacert`` the server certificate must verify against that CA alone
    (``--cacert`` replaces the system roots for the request)."""
    head, body = work / "curl-headers", work / "curl-body"
    args = ["curl", "-sS", "--max-time", str(timeout), "-D", str(head), "-o", str(body)]
    args += ["-w", "%{http_code}"]
    if resolve is not None:
        args += ["--resolve", resolve]
    if cacert is not None:
        args += ["--cacert", str(cacert)]
    done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [*args, url],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(work)},
        check=False,
    )
    if done.returncode != 0:
        return None
    headers: list[tuple[str, str]] = []
    for line in head.read_text(errors="replace").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            headers.append((key.strip(), value.strip()))
    return Response(int(done.stdout.strip()), headers, body.read_text(errors="replace"))


def curl_exit_code(url: str, *, resolve: str, cacert: Path | None, work: Path) -> int:
    """curl's exit status for one request whose body is discarded (60 = certificate not trusted)."""
    args = ["curl", "-sS", "--max-time", "5", "-o", os.devnull, "--resolve", resolve]
    if cacert is not None:
        args += ["--cacert", str(cacert)]
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [*args, url],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": str(work)},
        check=False,
    ).returncode


def command_number() -> int:
    """A fence value from the clock (milliseconds), so a rerun is never below an earlier run's."""
    return int(time.time() * 1000)


def site_image(stack: Stack, project: UUID, source: str, work: Path) -> str:
    """The test project's site image ``<registry>/webblock/sites/<project>@sha256:<digest>``: the
    image ``source`` (in the stack's registry, by digest) copied under the project's repository with
    crane. The digest does not change, so no layer is copied again."""
    if not CRANE.is_file():
        pytest.fail(f"{CRANE} is missing: run make kind-tools first")
    _, _, digest = source.rpartition("@")
    path = source.partition("/")[2].rpartition("@")[0].rpartition(":")[0]
    target = f"{stack.registry_on_host}/webblock/sites/{project}:stack-test"
    trust = stack.registry_ca.parent / "trust"
    done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [str(CRANE), "copy", f"{stack.registry_on_host}/{path}@{digest}", target],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "HOME": str(work),
            "DOCKER_CONFIG": str(work),
            "SSL_CERT_DIR": str(trust),
        },
        check=False,
    )
    assert done.returncode == 0, f"crane copy into the test project's repository: {done.stderr}"
    return f"{stack.image_registry}/webblock/sites/{project}@{digest}"


# --- the objects bootstrap and the worker write, from the renderer --------------------------------


def tenant_subdomain(project: UUID) -> str:
    """A test tenant's sub-domain: the namespace label bootstrap writes (decision 0023 G18a)."""
    return f"stack-{project.hex}"


def tenant_objects(project: UUID) -> list[dict[str, Any]]:
    """What bootstrap writes for a tenant, from the renderer: namespace, quota, limits, the worker's
    RoleBinding, the offline ReferenceGrant and a placeholder ``catalog-runtime`` (the test sites
    never connect to the catalog)."""
    secret = render_runtime_secret(
        project,
        {
            "PGHOST": "webblock-catalog-db",
            "PGPORT": "5432",
            "PGDATABASE": "catalog",
            "PGUSER": f"site_{project.hex}",
            "PGPASSWORD": "placeholder-the-stack-test-site-never-connects",
            "PGSSLMODE": "disable",
        },
    )
    return [
        render_namespace(project, subdomain=tenant_subdomain(project), catalog_secret=None),
        render_resource_quota(project, TENANT_SETTINGS),
        render_limit_range(project, TENANT_SETTINGS),
        render_worker_role_binding(project),
        render_reference_grant(project),
        secret,
    ]


def release_objects(project: UUID, release: UUID, image: str, number: int) -> list[dict[str, Any]]:
    workload = ReleaseWorkload(project, release, image, number)
    deployment = render_deployment(workload, SITE_SETTINGS)
    # This fixture's image is stock nginx (the offline page's), which has no /healthz: its
    # readiness probe asks "/" instead. The override applies to this test fixture only; the
    # renderer's real output keeps GET /healthz (decision 0014 section 21 ruling 12), and no
    # admission policy checks the probe.
    site = deployment["spec"]["template"]["spec"]["containers"][0]
    site["readinessProbe"]["httpGet"]["path"] = "/"
    return [deployment, render_service(workload)]


def site_route(project: UUID, release: UUID | None, number: int) -> dict[str, Any]:
    """``route-<project>`` on the platform subdomain, to the release or (None) the offline page."""
    target = (
        RouteTarget(RouteTargetKind.RELEASE, release)
        if release is not None
        else RouteTarget(RouteTargetKind.OFFLINE)
    )
    return render_route(
        project,
        target,
        (f"{tenant_subdomain(project)}.{SITE_SUFFIX}",),
        number,
        SITE_SETTINGS,
        site_domain=SITE_SUFFIX,
        subdomain=tenant_subdomain(project),
    )


def plain_namespace(name: str) -> dict[str, Any]:
    """A namespace WITHOUT the tenant labels."""
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": name, "labels": {"pod-security.kubernetes.io/enforce": "restricted"}},
    }


def wait_route_accepted(kubectl: Kubectl, namespace: str, name: str, seconds: float = 120) -> None:
    """Until the route's Gateway parent reports Accepted and ResolvedRefs at its generation."""

    def accepted() -> bool | None:
        route = kubectl.get(WORKER, "httproute", name, namespace)
        generation = route.get("metadata", {}).get("generation", 0)
        for parent in route.get("status", {}).get("parents", []):
            conditions = parent.get("conditions")
            if is_true(conditions, "Accepted", generation) and is_true(
                conditions, "ResolvedRefs", generation
            ):
                return True
        return None

    poll(f"HTTPRoute {namespace}/{name} accepted by the Gateway", seconds, accepted)


def probe_pod(namespace: str, name: str, image: str) -> dict[str, Any]:
    """A probe Pod of the network tests: the offline page's unprivileged nginx image (its BusyBox
    has ``nc``, ``wget`` and ``nslookup``) sleeping, restricted, with no ServiceAccount token."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 65532,
                "runAsGroup": 65532,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "command": ["sleep", "3600"],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "32Mi"},
                        "limits": {"cpu": "50m", "memory": "32Mi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
