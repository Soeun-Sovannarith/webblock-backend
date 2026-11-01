"""The chart deploy/helm/provisioning, rendered offline (task PK; decision 0016 section 8 rulings 1
and 2, section 9 rulings 2 and 3). The objects are what the Kind run installs: namespaced only, no
Secret, restricted pods by digest, a token only for the worker and the bootstrap and each its own,
one bootstrap recreated, settings by ConfigMap and secret keys by name, stand-ins mounted only
when asked, the migration as a hook Job, and default-deny network policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.chart._render import VALUES, Doc, documents, helm_template

VALUES_DOC: dict[str, Any] = json.loads(VALUES.read_text())
IMAGE = VALUES_DOC["image"]
PROCESSES = {
    "api": "api",
    "worker": "worker",
    "build-worker": "buildWorker",
    "bootstrap": "bootstrap",
}


def tracing_defaults(component: str) -> dict[str, str]:
    """The chart's own env defaults (values.yaml): tracing off, the process's service name
    (decision 0022 E9). A values file's env is merged over them."""
    return {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "none",
        "OTEL_SERVICE_NAME": f"provisioning-{component}",
    }


def by_kind(docs: list[Doc], kind: str) -> list[Doc]:
    return [d for d in docs if d["kind"] == kind]


def named(docs: list[Doc], kind: str, name: str) -> Doc:
    (doc,) = [d for d in by_kind(docs, kind) if d["metadata"]["name"] == name]
    return doc


def pods(docs: list[Doc]) -> dict[str, Doc]:
    """Each pod template's spec by component: the four Deployments and the Job."""
    specs = {}
    for doc in by_kind(docs, "Deployment") + by_kind(docs, "Job"):
        template = doc["spec"]["template"]
        specs[template["metadata"]["labels"]["app.kubernetes.io/component"]] = template["spec"]
    return specs


def projected_tokens(spec: Doc) -> list[Doc]:
    return [
        source["serviceAccountToken"]
        for volume in spec.get("volumes") or []
        for source in (volume.get("projected") or {}).get("sources") or []
        if "serviceAccountToken" in source
    ]


def mounts(spec: Doc) -> list[Doc]:
    return list(spec["containers"][0].get("volumeMounts") or [])


def test_the_default_values_are_refused(helm_home: Path) -> None:
    done = helm_template(helm_home)
    assert done.returncode != 0
    assert "image" in done.stderr


@pytest.mark.parametrize(
    "image", ["registry.test/x:latest", "registry.test/x", "registry.test/x@sha256:abc"]
)
def test_an_image_not_pinned_by_digest_is_refused(helm_home: Path, image: str) -> None:
    done = helm_template(helm_home, VALUES, sets=(f"image={image}",))
    assert done.returncode != 0
    assert "image" in done.stderr


def test_the_chart_renders_only_namespaced_objects_it_may_own(rendered: list[Doc]) -> None:
    assert sorted({d["kind"] for d in rendered}) == [
        "CiliumNetworkPolicy", "ConfigMap", "Deployment", "Job", "Service", "ServiceAccount",
    ]  # fmt: skip
    accounts = {d["metadata"]["name"] for d in by_kind(rendered, "ServiceAccount")}
    assert accounts == {"provisioning-api", "provisioning-build-worker"}
    for account in by_kind(rendered, "ServiceAccount"):
        assert account["automountServiceAccountToken"] is False
    for doc in rendered:
        labels = doc["metadata"]["labels"]
        assert labels["app.kubernetes.io/instance"] == "provisioning"
        assert labels["app.kubernetes.io/part-of"] == "webblock-provisioning"


def test_every_pod_is_restricted_and_runs_the_one_image_by_digest(rendered: list[Doc]) -> None:
    specs = pods(rendered)
    assert set(specs) == {"api", "worker", "build-worker", "bootstrap", "migrate"}
    for component, spec in specs.items():
        assert spec["automountServiceAccountToken"] is False, component
        assert spec["enableServiceLinks"] is False, component
        assert spec["securityContext"] == {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        }, component
        (container,) = spec["containers"]
        assert container["image"] == IMAGE, component
        assert container["securityContext"] == {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        }, component
        assert {"name": "tmp", "mountPath": "/tmp"} in mounts(spec), component  # noqa: S108 - a container path, not a host file
        memory = container["resources"]["requests"]["memory"]
        assert container["resources"]["limits"] == {"memory": memory}, component
        assert spec["nodeSelector"] == {"node-role.kubernetes.io/control-plane": ""}, component


# Plan decision 18: a memory request equals its limit, CPU has a request and no limit. The numbers
# are PK-design's estimates, except the bootstrap's: 192Mi became 256Mi by the owner's decision of
# 18 October 2026, an override of decision 18 (no OOM kill was seen) based on finding I-1 of PK-V
# (ai/reviews/pk-validation.md: the bootstrap peaked at its 192Mi limit under the full e2e load).
RESOURCES = {
    "api": ("256Mi", "100m"),
    "worker": ("384Mi", "100m"),
    "build-worker": ("256Mi", "100m"),
    "bootstrap": ("256Mi", "100m"),
    "migrate": ("64Mi", "50m"),
}


def test_each_pod_has_its_memory_as_request_and_limit_and_a_cpu_request_only(
    rendered: list[Doc],
) -> None:
    specs = pods(rendered)
    assert set(specs) == set(RESOURCES)
    for component, (memory, cpu) in RESOURCES.items():
        (container,) = specs[component]["containers"]
        assert container["resources"] == {
            "requests": {"memory": memory, "cpu": cpu},
            "limits": {"memory": memory},
        }, component


def test_only_the_worker_and_the_bootstrap_hold_a_token_and_each_its_own(
    rendered: list[Doc],
) -> None:
    specs = pods(rendered)
    for component in ("api", "build-worker", "migrate"):
        assert projected_tokens(specs[component]) == [], component
    assert specs["api"]["serviceAccountName"] == "provisioning-api"
    assert specs["build-worker"]["serviceAccountName"] == "provisioning-build-worker"
    assert "serviceAccountName" not in specs["migrate"]
    for component, account in (
        ("worker", "provisioning-worker"),
        ("bootstrap", "tenant-bootstrap"),
    ):
        spec = specs[component]
        assert spec["serviceAccountName"] == account
        assert projected_tokens(spec) == [{"path": "token", "expirationSeconds": 600}]
        assert {
            "name": "kube",
            "mountPath": "/var/run/secrets/webblock/kube",
            "readOnly": True,
        } in (mounts(spec))


def test_the_bootstrap_is_one_replica_recreated(rendered: list[Doc]) -> None:
    bootstrap = named(rendered, "Deployment", "provisioning-bootstrap")
    assert bootstrap["spec"]["replicas"] == 1
    assert bootstrap["spec"]["strategy"] == {"type": "Recreate"}


def test_each_process_reads_its_configmap_and_each_secret_key_by_name(rendered: list[Doc]) -> None:
    specs = pods(rendered)
    for component, key in PROCESSES.items():
        container = specs[component]["containers"][0]
        assert container["envFrom"] == [{"configMapRef": {"name": f"provisioning-{component}-env"}}]
        secret_env = [
            e for e in container.get("env") or [] if "secretKeyRef" in e.get("valueFrom", {})
        ]
        assert secret_env == [
            {
                "name": k,
                "valueFrom": {
                    "secretKeyRef": {"name": f"provisioning-{component}-secret", "key": k}
                },
            }
            for k in VALUES_DOC[key]["secretEnv"]
        ], component
        data = named(rendered, "ConfigMap", f"provisioning-{component}-env")["data"]
        assert data == tracing_defaults(component) | VALUES_DOC[key]["env"], component
        assert not set(data) & set(VALUES_DOC[key]["secretEnv"]), component
    worker_env = specs["worker"]["containers"][0]["env"]
    assert {
        "name": "WORKER_ID",
        "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}},
    } in worker_env


def test_the_api_is_probed_for_readiness_and_served_on_its_port(rendered: list[Doc]) -> None:
    container = pods(rendered)["api"]["containers"][0]
    assert container["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "ops"}
    assert {"name": "ops", "containerPort": 8081} in container["ports"]
    service = named(rendered, "Service", "provisioning-api")
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["metadata"]["annotations"] == {"lbipam.cilium.io/ips": "172.18.255.250"}
    assert service["spec"]["ports"] == [{"name": "http", "port": 8080, "targetPort": "http"}]


def test_the_migration_is_a_hook_job_running_goose_as_the_migrator(rendered: list[Doc]) -> None:
    job = named(rendered, "Job", "provisioning-migrate")
    annotations = job["metadata"]["annotations"]
    assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert annotations["helm.sh/hook-delete-policy"] == "before-hook-creation,hook-succeeded"
    assert job["spec"]["backoffLimit"] == 0
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["/usr/local/bin/goose", "up"]
    assert {
        "name": "GOOSE_DBSTRING",
        "valueFrom": {
            "secretKeyRef": {"name": "provisioning-migrate-secret", "key": "GOOSE_DBSTRING"}
        },
    } in container["env"]


def test_the_stand_ins_are_mounted_only_when_asked(helm_home: Path, rendered: list[Doc]) -> None:
    def names(spec: Doc) -> set[str]:
        return {m["name"] for m in mounts(spec)}

    specs = pods(rendered)
    assert {"standins", "dev-tokens"} <= names(specs["api"])
    assert "standins" in names(specs["worker"]) and "standins" in names(specs["build-worker"])
    assert "standins" not in names(specs["bootstrap"])
    assert {"name": "standins", "configMap": {"name": "provisioning-standins"}} in specs["api"][
        "volumes"
    ]
    done = helm_template(helm_home, VALUES, sets=("standins=false",))
    assert done.returncode == 0, done.stderr
    for spec in pods(documents(done.stdout)).values():
        assert not {"standins", "dev-tokens"} & names(spec)


def _ports(*ports: tuple[str, str]) -> list[Doc]:
    return [{"ports": [{"port": port, "protocol": proto} for port, proto in ports]}]


def _from_entity(entity: str, port: str) -> Doc:
    return {"fromEntities": [entity], "toPorts": _ports((port, "TCP"))}


def _cidr(cidr: str, port: str) -> Doc:
    return {"toCIDR": [cidr], "toPorts": _ports((port, "TCP"))}


DNS: Doc = {
    "toEndpoints": [
        {
            "matchLabels": {
                "k8s:io.kubernetes.pod.namespace": "kube-system",
                "k8s:k8s-app": "kube-dns",
            }
        }
    ],
    "toPorts": _ports(("53", "UDP"), ("53", "TCP")),
}
TENANT_LABEL = "k8s:io.cilium.k8s.namespace.labels.websites.webblock.internal/tenant"
API_SERVER: Doc = {"toEntities": ["kube-apiserver"]}


def test_every_pod_is_default_deny_with_exactly_its_own_rules(rendered: list[Doc]) -> None:
    net = VALUES_DOC["networkPolicy"]
    db, mq = net["database"], net["broker"]
    expected: dict[str, tuple[list[Doc], list[Doc]]] = {
        "api": (
            [_from_entity("all", "8080"), _from_entity("host", "8081")],
            [DNS, _cidr(db, "5432")],
        ),
        "worker": (
            [_from_entity("host", "9090")],
            [
                DNS,
                _cidr(db, "5432"),
                _cidr(mq, "5672"),
                API_SERVER,
                {"toEntities": ["ingress"]},
                {
                    "toEndpoints": [{"matchLabels": {TENANT_LABEL: "true"}}],
                    "toPorts": _ports(("8080", "TCP")),
                },
            ],
        ),
        "build-worker": (
            [_from_entity("host", "9090")],
            [DNS, _cidr(db, "5432"), _cidr(mq, "5672"), _cidr(net["jenkins"], "8080")],
        ),
        "bootstrap": (
            [_from_entity("host", "9090")],
            [DNS, _cidr(db, "5432"), _cidr(net["catalog"], "5432"), API_SERVER],
        ),
        "migrate": ([], [DNS, _cidr(db, "5432")]),
    }
    policies = {p["metadata"]["name"]: p["spec"] for p in by_kind(rendered, "CiliumNetworkPolicy")}
    assert set(policies) == {f"provisioning-{c}" for c in expected}
    for component, (ingress, egress) in expected.items():
        spec = policies[f"provisioning-{component}"]
        assert spec["endpointSelector"] == {
            "matchLabels": {
                "app.kubernetes.io/instance": "provisioning",
                "app.kubernetes.io/component": component,
            }
        }, component
        assert spec["enableDefaultDeny"] == {"ingress": True, "egress": True}, component
        assert spec.get("ingress", []) == ingress, component
        assert spec["egress"] == egress, component
        assert set(spec) <= {"endpointSelector", "enableDefaultDeny", "ingress", "egress"}, (
            component
        )


def test_the_migrate_policy_is_a_hook_created_before_the_job(rendered: list[Doc]) -> None:
    policy = named(rendered, "CiliumNetworkPolicy", "provisioning-migrate")
    job = named(rendered, "Job", "provisioning-migrate")
    notes = policy["metadata"]["annotations"]
    assert notes["helm.sh/hook"] == "pre-install,pre-upgrade"
    assert notes["helm.sh/hook-delete-policy"] == "before-hook-creation"
    assert int(notes["helm.sh/hook-weight"]) < int(
        job["metadata"]["annotations"]["helm.sh/hook-weight"]
    )
    for other in by_kind(rendered, "CiliumNetworkPolicy"):
        if other is not policy:
            assert "annotations" not in other["metadata"], other["metadata"]["name"]


def test_an_env_key_that_is_a_secret_or_could_inject_yaml_is_refused(helm_home: Path) -> None:
    for sets in (
        "api.env.PG_PASS=hunter2",
        "worker.env.RABBITMQ_URL=amqp://x",
        "bootstrap.env.GOOSE_DBSTRING=x",
    ):
        done = helm_template(helm_home, VALUES, sets=(sets,))
        assert done.returncode != 0, sets
        assert "invalid propertyName" in done.stderr, sets
    bad = json.dumps({"api": {"env": {"X: y\n  PG_PASS": "v", "lower key": "v"}}})
    values = helm_home / "injection.json"
    values.write_text(bad)
    done = helm_template(helm_home, VALUES, values)
    assert done.returncode != 0
    assert "invalid propertyName" in done.stderr


def test_no_pod_shares_the_host_and_the_env_lists_are_exactly_the_expected(
    rendered: list[Doc],
) -> None:
    for component, spec in pods(rendered).items():
        assert not {"hostNetwork", "hostPID", "hostIPC", "hostUsers"} & set(spec), component
        assert not [v for v in spec["volumes"] if "hostPath" in v], component
        assert "hostPort" not in json.dumps(spec["containers"]), component
    for component, key in PROCESSES.items():
        env = pods(rendered)[component]["containers"][0].get("env") or []
        refs = [
            {
                "name": k,
                "valueFrom": {
                    "secretKeyRef": {"name": f"provisioning-{component}-secret", "key": k}
                },
            }
            for k in VALUES_DOC[key]["secretEnv"]
        ]
        worker_id = [
            {"name": "WORKER_ID", "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}
        ]
        assert env == (worker_id if component == "worker" else []) + refs, component


def test_the_migration_job_is_bounded_and_not_restarted(rendered: list[Doc]) -> None:
    job = named(rendered, "Job", "provisioning-migrate")
    assert job["spec"]["activeDeadlineSeconds"] == 300
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"
    env = job["spec"]["template"]["spec"]["containers"][0]["env"]
    assert [e["name"] for e in env if "value" in e] == ["GOOSE_DRIVER", "GOOSE_MIGRATION_DIR"]


def test_the_rendered_output_has_no_trailing_spaces(helm_home: Path) -> None:
    done = helm_template(helm_home, VALUES)
    assert done.returncode == 0, done.stderr
    assert [n for n, line in enumerate(done.stdout.splitlines(), 1) if line != line.rstrip()] == []


def test_an_empty_destination_leaves_its_traffic_refused(helm_home: Path) -> None:
    done = helm_template(helm_home, VALUES, sets=("networkPolicy.jenkins=",))
    assert done.returncode == 0, done.stderr
    (policy,) = [
        p
        for p in by_kind(documents(done.stdout), "CiliumNetworkPolicy")
        if p["metadata"]["name"] == "provisioning-build-worker"
    ]
    assert all(
        "toCIDR" not in rule or "8080" not in json.dumps(rule) for rule in policy["spec"]["egress"]
    )
