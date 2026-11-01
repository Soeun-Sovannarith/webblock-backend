"""KUBE_CONNECTION (decision 0016 section 9 ruling 2): the worker and the bootstrap connect from the
host with their project-local kubeconfig, or from a pod with the projected ServiceAccount token, its
CA and the username a SelfSubjectReview must return. Production refuses the kubeconfig. Every key
is required in its mode and refused in the other, and a reason never quotes a value."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.hosts.envs import MARKER, bootstrap_env, in_cluster, production, worker_env

from provisioning.infrastructure.config import LoadError
from provisioning.infrastructure.config.bootstrap import load_bootstrap, read_bootstrap
from provisioning.infrastructure.config.common import InClusterConnection, KubeconfigConnection
from provisioning.infrastructure.config.loader import required_keys
from provisioning.infrastructure.config.worker import load_worker, read_worker

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")
TOKEN = Path("/var/run/secrets/webblock/kube/token")
CA = Path("/var/run/secrets/webblock/kube/ca.crt")
WORKER = "system:serviceaccount:webblock:provisioning-worker"
BOOTSTRAP = "system:serviceaccount:webblock:tenant-bootstrap"
IN_CLUSTER_KEYS = ("KUBE_TOKEN_FILE", "KUBE_CA_FILE", "KUBE_EXPECTED_USERNAME")


def _worker_in_a_pod() -> dict[str, str]:
    return in_cluster(worker_env(KUBECONFIG), "WORKER_KUBECONFIG", "provisioning-worker")


def _refused(load: Callable[..., object], env: dict[str, str]) -> LoadError:
    with pytest.raises(LoadError) as caught:
        load(env.get)
    return caught.value


def test_on_the_host_the_worker_and_the_bootstrap_use_their_kubeconfigs() -> None:
    worker = load_worker(worker_env(KUBECONFIG).get)
    assert worker.cluster.connection == KubeconfigConnection(KUBECONFIG)
    bootstrap = load_bootstrap(bootstrap_env(KUBECONFIG).get)
    assert bootstrap.cluster.connection == KubeconfigConnection(KUBECONFIG)


def test_in_a_pod_the_worker_and_the_bootstrap_use_the_projected_token() -> None:
    worker = load_worker(_worker_in_a_pod().get)
    assert worker.cluster.connection == InClusterConnection(TOKEN, CA, WORKER)
    env = in_cluster(bootstrap_env(KUBECONFIG), "BOOTSTRAP_KUBECONFIG", "tenant-bootstrap")
    assert load_bootstrap(env.get).cluster.connection == InClusterConnection(TOKEN, CA, BOOTSTRAP)


def test_the_required_keys_name_the_mode_and_the_kubeconfig() -> None:
    assert {"KUBE_CONNECTION", "WORKER_KUBECONFIG", "KUBE_REQUEST_TIMEOUT"} <= set(
        required_keys(read_worker)
    )
    assert {"KUBE_CONNECTION", "BOOTSTRAP_KUBECONFIG"} <= set(required_keys(read_bootstrap))
    assert not set(IN_CLUSTER_KEYS) & set(required_keys(read_worker))


@pytest.mark.parametrize("value", [None, "", "incluster", "Kubeconfig", MARKER])
def test_the_mode_is_exactly_kubeconfig_or_in_cluster(value: str | None) -> None:
    env = {k: v for k, v in worker_env(KUBECONFIG).items() if k != "KUBE_CONNECTION"}
    if value is not None:
        env["KUBE_CONNECTION"] = value
    err = _refused(load_worker, env)
    assert err.keys() == ("KUBE_CONNECTION",)
    assert MARKER not in str(err)


def test_in_a_pod_the_kubeconfig_key_is_refused() -> None:
    env = _worker_in_a_pod() | {"WORKER_KUBECONFIG": str(KUBECONFIG)}
    assert _refused(load_worker, env).keys() == ("WORKER_KUBECONFIG",)


@pytest.mark.parametrize("key", IN_CLUSTER_KEYS)
def test_on_the_host_the_in_cluster_keys_are_refused(key: str) -> None:
    env = worker_env(KUBECONFIG) | {key: _worker_in_a_pod()[key]}
    assert _refused(load_worker, env).keys() == (key,)


@pytest.mark.parametrize("key", IN_CLUSTER_KEYS)
def test_in_a_pod_each_in_cluster_key_is_required(key: str) -> None:
    env = {k: v for k, v in _worker_in_a_pod().items() if k != key}
    assert _refused(load_worker, env).keys() == (key,)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("KUBE_TOKEN_FILE", f"relative/{MARKER}"),
        ("KUBE_CA_FILE", f"relative/{MARKER}"),
        ("KUBE_EXPECTED_USERNAME", f"system:serviceaccount:webblock:{MARKER}"),
        ("KUBE_EXPECTED_USERNAME", MARKER),
        ("KUBE_EXPECTED_USERNAME", "system:serviceaccount:webblock"),
        ("KUBE_EXPECTED_USERNAME", f"{WORKER}:extra"),
        ("KUBE_EXPECTED_USERNAME", "system:node:webblock-proto-worker"),
    ],
)
def test_a_refused_in_cluster_value_is_never_quoted(key: str, value: str) -> None:
    err = _refused(load_worker, _worker_in_a_pod() | {key: value})
    assert err.keys() == (key,)
    assert MARKER not in str(err)


def test_production_refuses_the_kubeconfig() -> None:
    env = {k: v for k, v in production(worker_env(KUBECONFIG)).items() if k not in IN_CLUSTER_KEYS}
    env |= {"KUBE_CONNECTION": "kubeconfig", "WORKER_KUBECONFIG": str(KUBECONFIG)}
    err = _refused(load_worker, env)
    assert err.keys() == ("KUBE_CONNECTION",)
    assert "must be in-cluster in production" in str(err)


def test_production_accepts_the_projected_token() -> None:
    config = load_worker(production(worker_env(KUBECONFIG)).get)
    assert config.cluster.connection == InClusterConnection(TOKEN, CA, WORKER)
