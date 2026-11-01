"""The tenant NetworkPolicy on the k3d engine (TR-44; spike design ``k3s-site-cluster-spike`` G8):
``deploy/prototype/policy/tenant-network-k3s.yaml.tmpl``, enforced by k3s's kube-router.

No FR asks for tenant network isolation yet (design section 11, Q1), so nothing in Provisioning
writes this policy: this test applies it, with k3d's admin, to its own test tenants. It runs on the
k3d engine only (``make proto-test-kind`` deselects it on Kind, where the Cilium policy and
``test_tenant_network_live`` hold).

There is no ``cilium-dbg monitor`` to show a policy drop, so every refusal is proven against a
destination that exists and answers: the same address and port are first reached from a control
probe in a namespace without the policy, then refused from tenant A's probe. Refused: tenant B's
site pod, the Kubernetes API server (its Service address and the server node's address), the
image registry, and (with tenant B's own policy) traffic into tenant B from outside the Gateway.
Allowed: the catalog database, the cluster DNS, tenant A's own site through the Gateway (Traefik's
Service address, the in-cluster way to the Gateway, as the worker's probe uses it on k3s: design
G13), and the kubelet's readiness probes of a site under the policy. The metadata address has no
listener on k3d; the test shows only that tenant A cannot reach it."""

import json
import re
import secrets
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from tests.integration.kind_safety import engine
from tests.integration.stack._stack import (
    ADMIN,
    WORKER,
    Kubectl,
    Stack,
    Tenant,
    command_number,
    plain_namespace,
    poll,
    probe_pod,
    site_route,
    wait_route_accepted,
)

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "deploy" / "prototype" / "policy" / "tenant-network-k3s.yaml.tmpl"
POLICY = "webblock-tenant-network"
CONNECT_SECONDS = 3
IPV4 = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")
CATALOG = "webblock-catalog-db"
# The kubelet's readiness probes run every few seconds (render.py); a site that stays Ready for this
# long under the policy is being probed through it.
READY_WATCH_SECONDS = 30


@dataclass(frozen=True, slots=True)
class Probe:
    namespace: str
    name: str


def _start_probe(kubectl: Kubectl, namespace: str, name: str, image: str) -> Probe:
    kubectl.apply(ADMIN, probe_pod(namespace, name, image))
    kubectl.ok(
        ADMIN, "-n", namespace, "wait", "--for=condition=Ready", f"pod/{name}", "--timeout=120s"
    )
    return Probe(namespace, name)


def _exec(kubectl: Kubectl, probe: Probe, *command: str) -> subprocess.CompletedProcess[str]:
    return kubectl.run(ADMIN, "-n", probe.namespace, "exec", probe.name, "--", *command)


def connects(kubectl: Kubectl, probe: Probe, host: str, port: int) -> bool:
    done = _exec(kubectl, probe, "nc", "-z", "-w", str(CONNECT_SECONDS), host, str(port))
    return done.returncode == 0


def resolve(kubectl: Kubectl, probe: Probe, host: str) -> str:
    """``host``'s IPv4 address as the probe's cluster DNS answers it."""
    answer = _exec(kubectl, probe, "nslookup", host).stdout
    found = [
        line.split()[-1]
        for line in answer.splitlines()
        if line.startswith("Address") and "#" not in line and IPV4.fullmatch(line.split()[-1])
    ]
    assert found, f"the probe cannot resolve {host} to an IPv4 address: {answer}"
    return found[0]


def apply_policy(kubectl: Kubectl, namespace: str, catalog_ip: str) -> None:
    """The template for ``namespace``, applied by k3d's admin (test scaffolding, not the
    platform)."""
    text = TEMPLATE.read_text().replace("@NAMESPACE@", namespace)
    kubectl.apply(ADMIN, yaml.safe_load(text.replace("@CATALOG_IP@", catalog_ip)))


def gateway_url(kubectl: Kubectl) -> str:
    """Traefik's Service address on port 80 (the web entry point, 8000 in the pod)."""
    address = kubectl.ok(
        ADMIN, "-n", "kube-system", "get", "service", "traefik", "-o", "jsonpath={.spec.clusterIP}"
    ).strip()
    assert IPV4.fullmatch(address), (
        f"Service kube-system/traefik has no IPv4 cluster address: {address}"
    )
    return f"http://{address}/"


def served_through_gateway(kubectl: Kubectl, client: Probe, host: str) -> str | None:
    done = _exec(
        kubectl,
        client,
        "wget",
        "-q",
        "-T",
        str(CONNECT_SECONDS),
        "-O",
        "-",
        "--header",
        f"Host: {host}",
        gateway_url(kubectl),
    )
    return done.stdout if done.returncode == 0 and "Welcome to nginx" in done.stdout else None


@pytest.fixture(scope="module")
def k3d_only() -> None:
    if engine() != "k3d":
        pytest.fail(
            "the k3s tenant NetworkPolicy runs on the k3d engine only "
            "(make proto-up PROTO_ENGINE=k3d)"
        )


@pytest.fixture(scope="module")
def control(
    k3d_only: None, kubectl: Kubectl, offline_image: str, scratch_module_namespace: str
) -> Probe:
    """A probe in a namespace without the policy: what it reaches exists and answers."""
    return _start_probe(kubectl, scratch_module_namespace, "network-control", offline_image)


@pytest.fixture(scope="module")
def scratch_module_namespace(kubectl: Kubectl) -> Iterator[str]:
    name = f"webblock-stack-test-{secrets.token_hex(4)}"
    kubectl.apply(ADMIN, plain_namespace(name))
    try:
        yield name
    finally:
        kubectl.run(ADMIN, "delete", "namespace", name, "--wait=false")


@pytest.fixture(scope="module")
def catalog_ip(kubectl: Kubectl, control: Probe) -> str:
    return resolve(kubectl, control, CATALOG)


@pytest.fixture(scope="module")
def probe(
    k3d_only: None, kubectl: Kubectl, tenant: Tenant, offline_image: str, catalog_ip: str
) -> Iterator[Probe]:
    """Tenant A's probe, in tenant A's namespace under the policy."""
    apply_policy(kubectl, tenant.namespace, catalog_ip)
    found = _start_probe(kubectl, tenant.namespace, "network-probe", offline_image)
    try:
        yield found
    finally:
        kubectl.run(ADMIN, "-n", tenant.namespace, "delete", "pod", found.name, "--wait=false")


def assert_refused_but_answering(
    kubectl: Kubectl, control: Probe, probe: Probe, host: str, port: int
) -> None:
    assert connects(kubectl, control, host, port), (
        f"{host}:{port} does not answer the control probe"
    )
    assert not connects(kubectl, probe, host, port), f"tenant A reached {host}:{port}"


def test_tr44_the_policy_is_installed_in_the_tenant_namespace(
    kubectl: Kubectl, tenant: Tenant, probe: Probe
) -> None:
    found = kubectl.get(ADMIN, "networkpolicy", POLICY, tenant.namespace)
    assert found["spec"]["policyTypes"] == ["Ingress", "Egress"], found


def test_tr44_a_tenant_reaches_the_catalog_and_the_dns(kubectl: Kubectl, probe: Probe) -> None:
    assert resolve(kubectl, probe, CATALOG)
    assert connects(kubectl, probe, CATALOG, 5432)


def test_tr44_a_tenant_reaches_its_own_site_through_the_gateway(
    kubectl: Kubectl, tenant: Tenant, probe: Probe
) -> None:
    kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))
    wait_route_accepted(kubectl, tenant.namespace, f"route-{tenant.project}")
    poll(
        f"{tenant.host} through Traefik from tenant A's pod",
        60,
        lambda: served_through_gateway(kubectl, probe, tenant.host),
    )


def test_tr44_the_kubelet_still_probes_a_site_under_the_policy(
    kubectl: Kubectl, tenant: Tenant, probe: Probe
) -> None:
    """kube-router admits traffic from the pod's own node, so readiness probes pass."""
    selector = f"websites.webblock.internal/release-id={tenant.release}"
    deadline = time.monotonic() + READY_WATCH_SECONDS
    while time.monotonic() < deadline:
        pods = json.loads(
            kubectl.ok(ADMIN, "-n", tenant.namespace, "get", "pods", "-l", selector, "-o", "json")
        )["items"]
        assert pods, "the site pod is gone"
        for pod in pods:
            ready = [c for c in pod["status"].get("conditions", []) if c["type"] == "Ready"]
            assert ready and ready[0]["status"] == "True", pod["status"]
        time.sleep(5)


def site_pod_address(kubectl: Kubectl, tenant: Tenant) -> str:
    address = kubectl.ok(
        ADMIN,
        "-n",
        tenant.namespace,
        "get",
        "pods",
        "-l",
        f"websites.webblock.internal/release-id={tenant.release}",
        "-o",
        "jsonpath={.items[0].status.podIP}",
    ).strip()
    assert address, f"the site pod of {tenant.namespace} has no address"
    return address


def test_tr44_a_tenant_cannot_reach_another_tenants_pod(
    kubectl: Kubectl, other_tenant: Tenant, control: Probe, probe: Probe
) -> None:
    """Tenant B has no policy yet, so the control probe reaches its pod; tenant A's egress does
    not."""
    address = site_pod_address(kubectl, other_tenant)
    assert_refused_but_answering(kubectl, control, probe, address, 8080)


def test_tr44_a_tenant_cannot_reach_the_api_server(
    kubectl: Kubectl, control: Probe, probe: Probe
) -> None:
    service = kubectl.ok(
        ADMIN, "-n", "default", "get", "service", "kubernetes", "-o", "jsonpath={.spec.clusterIP}"
    ).strip()
    endpoint = json.loads(
        kubectl.ok(
            ADMIN,
            "-n",
            "default",
            "get",
            "endpointslices",
            "-l",
            "kubernetes.io/service-name=kubernetes",
            "-o",
            "json",
        )
    )["items"][0]
    node_address = endpoint["endpoints"][0]["addresses"][0]
    port = int(endpoint["ports"][0]["port"])
    assert_refused_but_answering(kubectl, control, probe, service, 443)
    assert_refused_but_answering(kubectl, control, probe, node_address, port)


def test_tr44_a_tenant_cannot_reach_the_registry(
    kubectl: Kubectl, stack: Stack, control: Probe, probe: Probe
) -> None:
    host, _, port = stack.image_registry.partition(":")
    assert_refused_but_answering(
        kubectl, control, probe, resolve(kubectl, control, host), int(port)
    )


def test_tr44_a_tenant_cannot_reach_the_metadata_address(kubectl: Kubectl, probe: Probe) -> None:
    """Nothing listens at 169.254.169.254 on k3d: this shows only that tenant A gets nothing."""
    assert not connects(kubectl, probe, "169.254.169.254", 80)


def test_tr44_a_site_takes_traffic_only_from_the_gateway(
    kubectl: Kubectl, other_tenant: Tenant, control: Probe, catalog_ip: str
) -> None:
    """With tenant B's own policy, the control probe no longer reaches B's pod directly, while B's
    site still answers through Traefik."""
    address = site_pod_address(kubectl, other_tenant)
    assert connects(kubectl, control, address, 8080), "tenant B's pod does not answer"
    apply_policy(kubectl, other_tenant.namespace, catalog_ip)
    poll(
        "tenant B's pod refusing the control probe",
        30,
        lambda: True if not connects(kubectl, control, address, 8080) else None,
    )
    kubectl.apply(WORKER, site_route(other_tenant.project, other_tenant.release, command_number()))
    wait_route_accepted(kubectl, other_tenant.namespace, f"route-{other_tenant.project}")
    poll(
        f"{other_tenant.host} through Traefik under its own policy",
        60,
        lambda: served_through_gateway(kubectl, control, other_tenant.host),
    )
