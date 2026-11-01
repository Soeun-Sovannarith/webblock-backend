"""The tenant network policy (task W6-K; decision 0014 section 21 ruling 1):
``CiliumClusterwideNetworkPolicy webblock-tenant-network`` (``deploy/prototype/policy``).

From a probe pod in tenant A (the offline page's unprivileged nginx image, whose BusyBox has ``nc``
and ``wget``): the catalog database and the Gateway (to A's own site) answer; tenant B's site pod,
the Kubernetes API server (its Service address and the control-plane node), the metadata address
and the image registry do not. A refusal must be Cilium's: while each refused connection is tried,
``cilium-dbg monitor --type drop`` on the probe pod's node must report a ``Policy denied`` drop
towards that destination, so a destination that is merely absent (nothing listens at
169.254.169.254 on Kind) cannot pass the test. The probe pod is Kind's admin's scaffolding; the
policies of ``test_site_policies_live`` do not cover Pods."""

import json
import re
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from tests.integration.stack._stack import (
    ADMIN,
    WORKER,
    Kubectl,
    Stack,
    Tenant,
    command_number,
    poll,
    probe_pod,
    site_route,
    wait_route_accepted,
)

CONNECT_SECONDS = 3
IPV4 = re.compile(r"[0-9]{1,3}(\.[0-9]{1,3}){3}")
POLICY = "webblock-tenant-network"


@dataclass(frozen=True, slots=True)
class Probe:
    namespace: str
    name: str
    node: str
    cilium_pod: str


@pytest.fixture(scope="module")
def probe(kubectl: Kubectl, tenant: Tenant, offline_image: str) -> Iterator[Probe]:
    name = "network-probe"
    kubectl.apply(ADMIN, probe_pod(tenant.namespace, name, offline_image))
    try:
        kubectl.ok(
            ADMIN,
            "-n",
            tenant.namespace,
            "wait",
            "--for=condition=Ready",
            f"pod/{name}",
            "--timeout=120s",
        )
        node = kubectl.ok(
            ADMIN, "-n", tenant.namespace, "get", "pod", name, "-o", "jsonpath={.spec.nodeName}"
        ).strip()
        pods = json.loads(
            kubectl.ok(
                ADMIN, "-n", "kube-system", "get", "pods", "-l", "k8s-app=cilium", "-o", "json"
            )
        )["items"]
        agent = [p["metadata"]["name"] for p in pods if p["spec"].get("nodeName") == node]
        assert len(agent) == 1, f"no single Cilium agent on node {node}"
        yield Probe(tenant.namespace, name, node, agent[0])
    finally:
        kubectl.run(ADMIN, "-n", tenant.namespace, "delete", "pod", name, "--wait=false")


def _exec(kubectl: Kubectl, probe: Probe, *command: str) -> subprocess.CompletedProcess[str]:
    return kubectl.run(ADMIN, "-n", probe.namespace, "exec", probe.name, "--", *command)


def connects(kubectl: Kubectl, probe: Probe, host: str, port: int) -> bool:
    done = _exec(kubectl, probe, "nc", "-z", "-w", str(CONNECT_SECONDS), host, str(port))
    return done.returncode == 0


def policy_drops_while(
    kubectl: Kubectl, probe: Probe, host: str, port: int, destination: str
) -> tuple[bool, str]:
    """Try ``host:port`` from the probe while Cilium's drop monitor runs on the probe's node.
    Returns (connected, the monitor's lines that are policy drops towards ``destination``)."""
    monitor = kubectl.popen(
        ADMIN,
        "-n",
        "kube-system",
        "exec",
        probe.cilium_pod,
        "-c",
        "cilium-agent",
        "--",
        "timeout",
        str(CONNECT_SECONDS + 6),
        "cilium-dbg",
        "monitor",
        "--type",
        "drop",
    )
    try:
        time.sleep(2)  # the monitor attaches before the connection is tried
        connected = connects(kubectl, probe, host, port)
        output, _ = monitor.communicate(timeout=CONNECT_SECONDS + 20)
    finally:
        if monitor.poll() is None:
            monitor.kill()
            monitor.communicate()
    drops = [
        line for line in output.splitlines() if "Policy denied" in line and destination in line
    ]
    return connected, "\n".join(drops)


def assert_refused_by_policy(
    kubectl: Kubectl, probe: Probe, host: str, port: int, destination: str
) -> None:
    connected, drops = policy_drops_while(kubectl, probe, host, port, destination)
    assert not connected, f"tenant A reached {host}:{port}"
    assert drops, f"no Cilium policy drop towards {destination} seen; refused for another reason"


def test_the_network_policy_is_installed_and_valid(kubectl: Kubectl) -> None:
    found = kubectl.get(ADMIN, "ciliumclusterwidenetworkpolicy", POLICY, "default")
    assert found, f"CiliumClusterwideNetworkPolicy {POLICY} is not installed"
    conditions = found.get("status", {}).get("conditions", [])
    assert any(c["type"] == "Valid" and c["status"] == "True" for c in conditions), conditions


def test_a_tenant_reaches_the_catalog(kubectl: Kubectl, probe: Probe) -> None:
    assert connects(kubectl, probe, "webblock-catalog-db", 5432)


def test_a_tenant_reaches_its_own_site_through_the_gateway(
    kubectl: Kubectl, stack: Stack, tenant: Tenant, probe: Probe
) -> None:
    kubectl.apply(WORKER, site_route(tenant.project, tenant.release, command_number()))
    wait_route_accepted(kubectl, tenant.namespace, f"route-{tenant.project}")

    def served() -> str | None:
        done = _exec(
            kubectl,
            probe,
            "wget",
            "-q",
            "-T",
            str(CONNECT_SECONDS),
            "-O",
            "-",
            "--header",
            f"Host: {tenant.host}",
            f"http://{stack.gateway_address}/",
        )
        return done.stdout if done.returncode == 0 and "Welcome to nginx" in done.stdout else None

    poll(f"{tenant.host} through the Gateway from tenant A's pod", 60, served)


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


def test_a_tenant_cannot_reach_another_tenants_pod(
    kubectl: Kubectl, other_tenant: Tenant, probe: Probe
) -> None:
    address = site_pod_address(kubectl, other_tenant)
    assert_refused_by_policy(kubectl, probe, address, 8080, f"{address}:8080")


def test_direct_pod_traffic_is_refused_even_inside_the_tenant(
    kubectl: Kubectl, tenant: Tenant, probe: Probe
) -> None:
    """Egress to site pods on 8080 is open only for Envoy's check of a request through the Gateway;
    the site pod's ingress admits only the Gateway and the kubelet, so a direct connection is
    dropped there, even from the site's own namespace."""
    address = site_pod_address(kubectl, tenant)
    assert_refused_by_policy(kubectl, probe, address, 8080, f"{address}:8080")


def test_a_tenant_cannot_reach_the_api_server(kubectl: Kubectl, probe: Probe) -> None:
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
    port = endpoint["ports"][0]["port"]
    # The Service address is translated to the control-plane node by Cilium's socket load balancer
    # before policy, so the drop names the node's address.
    assert_refused_by_policy(kubectl, probe, service, 443, f"{node_address}:{port}")
    assert_refused_by_policy(kubectl, probe, node_address, port, f"{node_address}:{port}")


def test_a_tenant_cannot_reach_the_metadata_address(kubectl: Kubectl, probe: Probe) -> None:
    assert_refused_by_policy(kubectl, probe, "169.254.169.254", 80, "169.254.169.254:80")


def test_a_tenant_cannot_reach_the_registry(kubectl: Kubectl, stack: Stack, probe: Probe) -> None:
    host, _, port = stack.image_registry.partition(":")
    address = _exec(kubectl, probe, "nslookup", host).stdout
    # The kind network is dual-stack; the IPv4 address is the one the nodes pull from.
    found = [
        line.split()[-1]
        for line in address.splitlines()
        if line.startswith("Address") and "#" not in line and IPV4.fullmatch(line.split()[-1])
    ]
    assert found, f"the probe cannot resolve {host} to an IPv4 address: {address}"
    assert_refused_by_policy(kubectl, probe, found[0], int(port), f"{found[0]}:{port}")
