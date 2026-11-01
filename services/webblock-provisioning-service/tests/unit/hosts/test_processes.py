"""The worker, build worker and bootstrap entry points (task G): every refused key named with its
reason and never its value (exit 2), a kubeconfig that is not the project's Kind cluster refused
(exit 1), and each process importing only the cluster identity it may hold; the API links neither
the Jenkins client nor the build store (finding F-6 of H-V)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tests.unit.hosts.envs import (
    MARKER,
    bootstrap_env,
    build_worker_env,
    in_cluster,
    worker_env,
)

from provisioning.infrastructure.config.bootstrap import read_bootstrap
from provisioning.infrastructure.config.build_worker import read_build_worker
from provisioning.infrastructure.config.loader import required_keys
from provisioning.infrastructure.config.worker import read_worker

SRC = Path(__file__).resolve().parents[3] / "src"
PROCESSES = {
    "worker": ("provisioning.hosts.worker", "provisioning-worker", read_worker),
    "build_worker": (
        "provisioning.hosts.build_worker",
        "provisioning-build-worker",
        read_build_worker,
    ),
    "bootstrap": ("provisioning.hosts.bootstrap", "provisioning-bootstrap", read_bootstrap),
}


def _run(module: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list: the interpreter and a module
        [sys.executable, "-m", module],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONPATH": str(SRC), **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("name", sorted(PROCESSES))
def test_an_empty_environment_exits_2_naming_every_key(name: str) -> None:
    module, label, read = PROCESSES[name]
    keys = required_keys(read)
    result = _run(module, {})
    assert result.returncode == 2, result.stderr
    lines = result.stderr.splitlines()
    assert lines[0] == f"{label}: configuration refused; {len(keys)} key(s) missing or invalid:"
    assert lines[1:] == [f"  {key}: is required" for key in keys]
    assert "Traceback" not in result.stderr


def _foreign_kubeconfig(tmp: Path) -> Path:
    """A kubeconfig naming a remote cluster: the client must refuse it before reading on."""
    path = tmp / "foreign.kubeconfig"
    path.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "remote",
                "contexts": [{"name": "remote", "context": {"cluster": "c", "user": "u"}}],
                "clusters": [{"name": "c", "cluster": {"server": "https://203.0.113.7:6443"}}],
                "users": [{"name": "u", "user": {"token": MARKER}}],
            }
        )
    )
    return path


def test_the_worker_refuses_a_kubeconfig_that_is_not_the_local_kind_cluster(
    tmp_path: Path,
) -> None:
    result = _run("provisioning.hosts.worker", worker_env(_foreign_kubeconfig(tmp_path)))
    assert result.returncode == 1, result.stderr
    record = json.loads(result.stderr.splitlines()[-1])
    assert record["msg"] == "worker could not be built"
    assert MARKER not in result.stderr


def test_the_bootstrap_process_refuses_a_kubeconfig_that_is_not_the_local_kind_cluster(
    tmp_path: Path,
) -> None:
    result = _run("provisioning.hosts.bootstrap", bootstrap_env(_foreign_kubeconfig(tmp_path)))
    assert result.returncode == 1, result.stderr
    record = json.loads(result.stderr.splitlines()[-1])
    assert record["msg"] == "bootstrap could not be built"
    assert MARKER not in result.stderr


def test_the_worker_in_a_pod_outside_a_cluster_exits_1(tmp_path: Path) -> None:
    """Decision 0016 section 9 ruling 2: the in-cluster mode needs KUBERNETES_SERVICE_HOST, which
    only a pod has; the process refuses to build the worker."""
    token, ca = tmp_path / "token", tmp_path / "ca.crt"
    token.write_text("t")
    ca.write_text("c")
    env = in_cluster(worker_env(tmp_path / "unused"), "WORKER_KUBECONFIG", "provisioning-worker")
    result = _run(
        "provisioning.hosts.worker", env | {"KUBE_TOKEN_FILE": str(token), "KUBE_CA_FILE": str(ca)}
    )
    assert result.returncode == 1, result.stderr
    record = json.loads(result.stderr.splitlines()[-1])
    assert record["msg"] == "worker could not be built"
    assert record["error"] == "not running in a cluster"
    assert MARKER not in result.stderr


def test_the_build_worker_refuses_a_missing_repository_file(tmp_path: Path) -> None:
    result = _run("provisioning.hosts.build_worker", build_worker_env(tmp_path / "missing.json"))
    assert result.returncode == 1, result.stderr
    assert "repository file cannot be read" in result.stderr
    assert MARKER not in result.stderr


def test_a_component_refusing_its_settings_exits_2_without_a_value(tmp_path: Path) -> None:
    # SITE_CPU_MILLICORES passes its key's check, but the per-site renderer allows 50 to 2000
    # millicores; the process refuses it before it connects to anything.
    env = worker_env(tmp_path / "unused.kubeconfig") | {"SITE_CPU_MILLICORES": "4000"}
    result = _run("provisioning.hosts.worker", env)
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("provisioning-worker: configuration refused: cpu must be")
    assert MARKER not in result.stderr
    assert "4000" not in result.stderr


def _imported(modules: list[str]) -> list[str]:
    code = (
        "import json, sys\n"
        + "".join(f"import {m}\n" for m in modules)
        + "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(  # noqa: S603  # fixed argument list: the interpreter and code
        [sys.executable, "-c", code],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    loaded: list[str] = json.loads(result.stdout)
    return loaded


def test_the_worker_never_loads_the_bootstrap_adapter() -> None:
    loaded = _imported(["provisioning.hosts.worker.wiring", "provisioning.hosts.worker.__main__"])
    assert "provisioning.infrastructure.kubernetes.site_cluster" in loaded
    assert "provisioning.infrastructure.kubernetes.bootstrap" not in loaded
    assert not any(m.startswith("provisioning.hosts.bootstrap") for m in loaded)


def test_the_build_worker_loads_no_kubernetes_client() -> None:
    loaded = _imported(
        ["provisioning.hosts.build_worker.wiring", "provisioning.hosts.build_worker.__main__"]
    )
    bad = [
        m
        for m in loaded
        if m.split(".")[0] in ("kubernetes", "kubernetes_asyncio")
        or m.startswith("provisioning.infrastructure.kubernetes")
    ]
    assert bad == []


def test_the_bootstrap_process_loads_no_broker_and_not_the_worker_adapter() -> None:
    loaded = _imported(["provisioning.hosts.bootstrap.__main__"])
    assert "provisioning.infrastructure.kubernetes.bootstrap" in loaded
    bad = [
        m
        for m in loaded
        if m.split(".")[0] in ("aio_pika", "aiormq")
        or m.startswith("provisioning.infrastructure.rabbitmq")
        or m == "provisioning.infrastructure.kubernetes.site_cluster"
    ]
    assert bad == []


# Finding F-6 of H-V (task H2, decision 0016 section 6): only the build worker holds the Jenkins
# token and the provisioning_build login, so only it links the Jenkins client and the build store.
BUILD_ONLY = (
    "provisioning.infrastructure.jenkins",
    "provisioning.infrastructure.postgres.build_store",
)


def test_the_api_loads_neither_the_jenkins_client_nor_the_build_store() -> None:
    loaded = _imported(["provisioning.hosts.api.wiring", "provisioning.hosts.api.__main__"])
    assert "provisioning.infrastructure.postgres" in loaded  # the API's own stores
    assert [m for m in loaded if m.startswith(BUILD_ONLY)] == []


def test_the_build_worker_loads_the_jenkins_client_and_the_build_store() -> None:
    """The neighbour of the API test: the module names it looks for are the real ones."""
    loaded = _imported(
        ["provisioning.hosts.build_worker.wiring", "provisioning.hosts.build_worker.__main__"]
    )
    assert "provisioning.infrastructure.jenkins.build_system" in loaded
    assert "provisioning.infrastructure.postgres.build_store" in loaded
