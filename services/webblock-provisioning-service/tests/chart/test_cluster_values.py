"""The values the run script generates in cluster mode, rendered through the chart offline (task
PK; PK-2b review). scripts/prototype/run-provisioning.sh writes configure()'s settings with
in-cluster addresses, scripts/prototype/cluster_values.py turns them into values-env.json, and
write_stack_values adds values-stack.json; helm template with both must pass the chart's
values.schema.json (every env key ``^[A-Z][A-Z0-9_]*$``, none of the nine secret keys in an env
map) and put each secret key into the pod from its Secret, never into a ConfigMap. No cluster is
contacted: helm runs offline (tests/chart/_render.py), the shell functions in a clean bash."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.chart._render import Doc, documents, helm_template
from tests.unit.scripts._configure import API_ADDRESS, SCRIPT, configure, function

ROOT = SCRIPT.parents[2]
CLUSTER_VALUES = ROOT / "scripts" / "prototype" / "cluster_values.py"
IMAGE = "webblock-proto-registry.test:5000/webblock/platform/provisioning@sha256:" + "a" * 64
SECRET_KEYS = {
    "PG_PASS",
    "PG_HOSTING_INTAKE_PASS",
    "PG_INTAKE_PASS",
    "PG_BUILD_PASS",
    "CATALOG_ADMIN_PASS",
    "RABBITMQ_URL",
    "JENKINS_API_TOKEN",
    "PREVIEW_PROBE_TOKEN",
    "GOOSE_DBSTRING",
}
COMPONENTS = {
    "api": "api",
    "worker": "worker",
    "buildWorker": "build-worker",
    "bootstrap": "bootstrap",
}


def _generated(tmp_path: Path, engine: str = "kind") -> tuple[Path, Path]:
    """values-env.json from the cluster_values CLI and values-stack.json from the run script's
    write_stack_values for the local ``engine`` (kind or k3d), both from a cluster-mode
    configure()."""
    env_dir = configure(tmp_path, "cluster")
    prov = env_dir.parent
    done = subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and the script
        [sys.executable, "-I", str(CLUSTER_VALUES), "values", str(env_dir)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    values_env = prov / "values-env.json"
    values_env.write_text(done.stdout)
    ips = {
        "webblock-provisioning-db": "172.18.0.7",
        "webblock-rabbitmq": "172.18.0.8",
        "webblock-jenkins": "172.18.0.4",
        "webblock-catalog-db": "172.18.0.6",
    }
    lookup = " ".join(f"{name}) printf {ip} ;;" for name, ip in ips.items())
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            f"ENGINE={engine}",
            f"PROV='{prov}'",
            f"PROV_IMAGE='{IMAGE}'",
            "PROV_PG_NAME=webblock-provisioning-db",
            "PROV_MQ_NAME=webblock-rabbitmq",
            "PROTO_JENKINS_NAME=webblock-jenkins",
            "PROTO_CATALOG_NAME=webblock-catalog-db",
            f"api_address() {{ printf {API_ADDRESS}; }}",
            f'container_ip() {{ case "$1" in {lookup} *) exit 1 ;; esac; }}',
            function("write_stack_values"),
            "write_stack_values",
        ]
    )
    done = subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    values_stack = prov / "values-stack.json"
    json.loads(values_stack.read_text())  # valid JSON
    return values_env, values_stack


def _named(docs: list[Doc], kind: str, name: str) -> Doc:
    (doc,) = [d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name]
    return doc


def test_the_generated_values_render_through_the_chart_and_its_schema(
    helm_home: Path, tmp_path: Path
) -> None:
    values_env, values_stack = _generated(tmp_path)
    done = helm_template(helm_home, values_env, values_stack)
    assert done.returncode == 0, done.stderr
    docs = documents(done.stdout)
    generated: dict[str, Any] = json.loads(values_env.read_text())
    for key, component in COMPONENTS.items():
        config_map = _named(docs, "ConfigMap", f"provisioning-{component}-env")
        assert config_map["data"] == generated[key]["env"], component
        assert not SECRET_KEYS & set(config_map["data"]), component
        assert generated[key]["secretEnv"], component
        container = _named(docs, "Deployment", f"provisioning-{component}")["spec"]["template"][
            "spec"
        ]["containers"][0]
        from_secret = {
            e["name"]: e["valueFrom"]["secretKeyRef"]
            for e in container.get("env", [])
            if "secretKeyRef" in e.get("valueFrom", {})
        }
        assert sorted(from_secret) == generated[key]["secretEnv"], component
        for name, ref in from_secret.items():
            assert ref == {"name": f"provisioning-{component}-secret", "key": name}, name
    worker = _named(docs, "Deployment", "provisioning-worker")["spec"]["template"]["spec"]
    (worker_id,) = [e for e in worker["containers"][0]["env"] if e["name"] == "WORKER_ID"]
    assert worker_id["valueFrom"] == {"fieldRef": {"fieldPath": "metadata.name"}}
    assert "WORKER_ID" not in generated["worker"]["env"]
    assert (
        _named(docs, "Deployment", "provisioning-api")["spec"]["template"]["spec"]["containers"][0][
            "image"
        ]
        == IMAGE
    )
    service = _named(docs, "Service", "provisioning-api")
    assert service["spec"]["type"] == "LoadBalancer"
    assert service["metadata"]["annotations"] == {"lbipam.cilium.io/ips": API_ADDRESS}


def test_tr44_the_k3d_values_render_through_the_chart_and_its_schema(
    helm_home: Path, tmp_path: Path
) -> None:
    """TR-44 (spike G5): on k3d the pods select k3s's control-plane label value "true" and the API
    Service carries no Cilium annotation; the chart and its schema accept both."""
    values_env, values_stack = _generated(tmp_path, "k3d")
    done = helm_template(helm_home, values_env, values_stack)
    assert done.returncode == 0, done.stderr
    docs = documents(done.stdout)
    for component in COMPONENTS.values():
        spec = _named(docs, "Deployment", f"provisioning-{component}")["spec"]["template"]["spec"]
        assert spec["nodeSelector"] == {"node-role.kubernetes.io/control-plane": "true"}, component
    service = _named(docs, "Service", "provisioning-api")
    assert service["spec"]["type"] == "LoadBalancer"
    assert not service["metadata"].get("annotations"), service["metadata"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda v: v["worker"]["env"].update({"PG_PASS": "x"}), "PG_PASS"),
        (lambda v: v["bootstrap"]["env"].update(lower_case="x"), "lower_case"),
    ],
)
def test_a_secret_or_malformed_key_in_the_generated_env_is_refused_by_the_schema(
    helm_home: Path, tmp_path: Path, change: Any, message: str
) -> None:
    """The render above passes the schema because the values are right, not because the schema is
    loose: the same values with a secret key in an env map, or a key that is not an environment
    name, are refused."""
    values_env, values_stack = _generated(tmp_path)
    values = json.loads(values_env.read_text())
    change(values)
    values_env.write_text(json.dumps(values))
    done = helm_template(helm_home, values_env, values_stack)
    assert done.returncode != 0
    assert "values don't meet the specifications of the schema" in done.stderr
    assert message in done.stderr
