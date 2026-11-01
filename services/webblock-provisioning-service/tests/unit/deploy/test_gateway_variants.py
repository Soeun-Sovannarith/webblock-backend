"""The k3d engine's manifests as written, without a cluster (TR-44, TR-45; spike design
``k3s-site-cluster-spike`` G2, G6, G7 and G8). The live run proves them on k3d; these tests keep the
files equal to what the design says: the Traefik Gateway is the Cilium Gateway apart from its class
and ports, Traefik routes only Gateway API objects, one wildcard certificate covers the site and
preview hosts on both engines, the registry settings trust only the project-local CA, and the
tenant NetworkPolicy admits only what the Cilium policy admits."""

from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only

REPO = Path(__file__).resolve().parents[3]
GATEWAY_DIR = REPO / "deploy" / "prototype" / "gateway"
K3D_DIR = REPO / "deploy" / "kind" / "k3d"
TENANT_POLICY = REPO / "deploy" / "prototype" / "policy" / "tenant-network-k3s.yaml.tmpl"
PROTO_UP = REPO / "deploy" / "kind" / "proto-up.sh"

NAMESPACE = "wb-0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11"
CATALOG_IP = "172.18.0.9"


def _load(path: Path) -> dict[str, Any]:
    found: dict[str, Any] = yaml.safe_load(path.read_text())
    return found


def _gateways() -> tuple[dict[str, Any], dict[str, Any]]:
    return _load(GATEWAY_DIR / "20-gateway.yaml"), _load(GATEWAY_DIR / "20-gateway-traefik.yaml")


# --- G6: the Gateway --------------------------------------------------------------------------


def test_tr44_the_traefik_gateway_differs_only_in_its_class_and_listener_ports() -> None:
    """TR-44 (G6): the name, listeners, hostnames, TLS Secret and allowedRoutes are the Cilium
    Gateway's, so the worker's routes attach to either unchanged."""
    cilium, traefik = _gateways()
    assert cilium["spec"]["gatewayClassName"] == "cilium"
    assert traefik["spec"]["gatewayClassName"] == "traefik"
    assert {listener["name"]: listener["port"] for listener in traefik["spec"]["listeners"]} == {
        "http-sites": 8000,
        "https-sites": 8443,
    }
    for gateway in (cilium, traefik):
        gateway["spec"]["gatewayClassName"] = "<class>"
        for listener in gateway["spec"]["listeners"]:
            listener["port"] = "<port>"
    assert traefik == cilium


def test_tr44_the_gateway_ports_are_traefiks_entry_points() -> None:
    """TR-44 (G6): Traefik refuses a listener whose port is not an entry point's port."""
    _, traefik = _gateways()
    values = yaml.safe_load(_load(K3D_DIR / "traefik-config.yaml")["spec"]["valuesContent"])
    entry_points = {values["ports"][name]["port"] for name in ("web", "websecure")}
    assert {listener["port"] for listener in traefik["spec"]["listeners"]} == entry_points
    assert {values["ports"][name]["exposedPort"] for name in ("web", "websecure")} == {80, 443}


def test_tr44_traefik_routes_only_gateway_api_objects() -> None:
    """TR-44 (G6): the admission policies cover HTTPRoutes only, so Traefik's Ingress and
    IngressRoute providers are off, and the chart's own Gateway is not made."""
    config = _load(K3D_DIR / "traefik-config.yaml")
    assert (config["kind"], config["metadata"]) == (
        "HelmChartConfig",
        {"name": "traefik", "namespace": "kube-system"},
    )
    values = yaml.safe_load(config["spec"]["valuesContent"])
    assert values["providers"] == {
        "kubernetesGateway": {"enabled": True},
        "kubernetesIngress": {"enabled": False},
        "kubernetesCRD": {"enabled": False},
    }
    assert values["gateway"] == {"enabled": False}
    assert values["gatewayClass"] == {"enabled": True, "name": "traefik"}


def test_tr44_proto_up_applies_the_gateway_of_its_engine() -> None:
    """TR-44 (G5, G6): Kind applies the Cilium Gateway, k3d the Traefik one, and the k3d node
    starts with the Traefik values mounted where k3s reads its manifests."""
    text = PROTO_UP.read_text()
    assert 'gateway_file="$proto_dir/gateway/20-gateway.yaml"' in text
    assert 'gateway_file="$proto_dir/gateway/20-gateway-traefik.yaml"' in text
    assert (
        '"$kind_dir/k3d/traefik-config.yaml:/var/lib/rancher/k3s/server/manifests/'
        'traefik-config.yaml@server:0"'
    ) in text


# --- G7: one wildcard certificate -------------------------------------------------------------


def _covers(wildcard: str, host: str) -> bool:
    """RFC 6125 section 6.4.3: ``*`` stands for exactly one whole label."""
    star, _, suffix = wildcard.partition(".")
    label, _, rest = host.partition(".")
    return star == "*" and bool(label) and rest == suffix


@pytest.mark.parametrize("gateway_file", ["20-gateway.yaml", "20-gateway-traefik.yaml"])
def test_fr20_tr45_both_gateways_serve_the_preview_host_with_the_one_wildcard(
    gateway_file: str,
) -> None:
    """FR-20, TR-45 (G7): each HTTPS listener names the Secret of the one ``*.sites.webblock.test``
    certificate and the hostname ``*.sites.webblock.test``, which covers
    ``<sub>-preview.sites.webblock.test``; the dotted ``<sub>.preview.sites.webblock.test`` is
    covered by neither."""
    certificate = _load(GATEWAY_DIR / "10-wildcard-certificate.yaml")
    (https,) = [
        listener
        for listener in _load(GATEWAY_DIR / gateway_file)["spec"]["listeners"]
        if listener["protocol"] == "HTTPS"
    ]
    assert https["tls"]["certificateRefs"] == [
        {"group": "", "kind": "Secret", "name": certificate["spec"]["secretName"]}
    ]
    assert certificate["spec"]["dnsNames"] == [https["hostname"]] == ["*.sites.webblock.test"]
    assert _covers(https["hostname"], "shop-preview.sites.webblock.test")
    assert not _covers(https["hostname"], "shop.preview.sites.webblock.test")


# --- G2: the k3d nodes' registry settings ------------------------------------------------------


def test_tr44_the_k3d_nodes_pull_only_from_the_local_registry_over_verified_tls() -> None:
    """TR-44 (G2): both registry names go to the registry container over HTTPS with the
    project-local CA, as the Kind nodes' hosts.toml do."""
    text = (K3D_DIR / "registries.yaml.tmpl").read_text()
    rendered = yaml.safe_load(
        text.replace("@REGISTRY_LOCAL@", "localhost:5001").replace(
            "@REGISTRY_IN_CLUSTER@", "webblock-proto-registry.test:5000"
        )
    )
    endpoint = {"endpoint": ["https://webblock-proto-registry.test:5000"]}
    assert rendered == {
        "mirrors": {"localhost:5001": endpoint, "webblock-proto-registry.test:5000": endpoint},
        "configs": {
            "webblock-proto-registry.test:5000": {
                "tls": {"ca_file": "/etc/webblock/registry-ca.crt"}
            }
        },
    }
    assert "insecure" not in text


# --- G8: the tenant NetworkPolicy --------------------------------------------------------------


def _tenant_policy() -> dict[str, Any]:
    text = TENANT_POLICY.read_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert body.count("@NAMESPACE@") == 1
    assert body.count("@CATALOG_IP@") == 1
    return dict(
        yaml.safe_load(text.replace("@NAMESPACE@", NAMESPACE).replace("@CATALOG_IP@", CATALOG_IP))
    )


TRAEFIK = {
    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "traefik"}},
}


def test_tr44_the_tenant_policy_selects_every_pod_of_its_namespace_both_ways() -> None:
    """TR-44 (G8): a policy of both types makes every site pod default-deny in and out."""
    policy = _tenant_policy()
    assert (policy["apiVersion"], policy["kind"]) == ("networking.k8s.io/v1", "NetworkPolicy")
    assert policy["metadata"]["namespace"] == NAMESPACE
    assert policy["spec"]["podSelector"] == {}
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]


def test_tr44_a_site_takes_traffic_only_from_the_gateway() -> None:
    """TR-44 (G8): only Traefik's pods in kube-system; no other tenant, no namespace-wide peer."""
    assert _tenant_policy()["spec"]["ingress"] == [{"from": [TRAEFIK]}]


def test_tr44_a_site_reaches_only_the_catalog_dns_and_the_gateway() -> None:
    """TR-44 (G8): three egress rules, each with a peer and ports; nothing else, so the API server,
    the registry, other tenants and 169.254.169.254 are refused."""
    egress = _tenant_policy()["spec"]["egress"]
    assert egress == [
        {
            "to": [{"ipBlock": {"cidr": f"{CATALOG_IP}/32"}}],
            "ports": [{"protocol": "TCP", "port": 5432}],
        },
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    },
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
        {
            "to": [TRAEFIK],
            "ports": [{"protocol": "TCP", "port": 8000}, {"protocol": "TCP", "port": 8443}],
        },
    ]
    for rule in egress:
        assert rule["to"] and rule["ports"], "a rule without peers or ports allows everything"


def test_tr44_the_tenant_policy_is_not_applied_with_the_policy_folder() -> None:
    """TR-44 (G8): proto-policy.sh applies every ``*.yaml`` of deploy/prototype/policy; the
    per-tenant template is a ``.tmpl`` with placeholders, applied only by the k3s network test."""
    assert TENANT_POLICY.suffix == ".tmpl"
    assert not TENANT_POLICY.with_suffix("").with_suffix(".yaml").exists()
