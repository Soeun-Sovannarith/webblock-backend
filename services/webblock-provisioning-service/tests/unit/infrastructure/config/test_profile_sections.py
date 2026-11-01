"""PROVISIONING_PROFILE in the build worker, bootstrap and operator sections (task H2).

- Every process requires the key, with no default, and accepts exactly prototype or production.
- The build worker refuses its stand-in keys (the repository file) by name in production, and
  reads the build hint's keys (BUILD_HINT_*).
- The bootstrap process refuses production as a whole: its access_setup depends on OD-46.
- The operator accepts production: neither of its command families needs an open contract.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from tests.unit.hosts.envs import (
    bootstrap_env,
    build_worker_env,
    operator_env,
    production,
)

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config import LoadError, load_operator
from provisioning.infrastructure.config.bootstrap import load_bootstrap
from provisioning.infrastructure.config.build_worker import load_build_worker
from provisioning.infrastructure.config.operator import load_operator_broker

KUBECONFIG = Path("/project/bin/kind-state/proto/bootstrap.kubeconfig")
REPOSITORIES = Path("/project/bin/kind-state/proto/provisioning/repositories.json")
BROKER = {
    "PROVISIONING_PROFILE": "prototype",
    "RABBITMQ_URL": "amqp://operator:unused@127.0.0.1:5672/",
    "RABBITMQ_DIAL_TIMEOUT": "5s",
    "DLQ_CALL_TIMEOUT": "10s",
}

LOADERS: dict[str, tuple[Callable[..., object], Callable[[], dict[str, str]]]] = {
    "build_worker": (load_build_worker, lambda: build_worker_env(REPOSITORIES)),
    "bootstrap": (load_bootstrap, lambda: bootstrap_env(KUBECONFIG)),
    "operator": (load_operator, operator_env),
    "operator_broker": (load_operator_broker, lambda: dict(BROKER)),
}


def _refused(load: Callable[..., object], env: dict[str, str]) -> LoadError:
    with pytest.raises(LoadError) as caught:
        load(env.get)
    return caught.value


@pytest.mark.parametrize("name", sorted(LOADERS))
@pytest.mark.parametrize("value", [None, "", "Production", "dev"])
def test_every_process_requires_a_known_profile(name: str, value: str | None) -> None:
    load, env_of = LOADERS[name]
    env = {k: v for k, v in env_of().items() if k != "PROVISIONING_PROFILE"}
    if value is not None:
        env["PROVISIONING_PROFILE"] = value
    err = _refused(load, env)
    assert err.keys() == ("PROVISIONING_PROFILE",)


def test_the_build_worker_reads_its_profile_and_the_hint_settings() -> None:
    config = load_build_worker(build_worker_env(REPOSITORIES).get)
    assert config.profile is Profile.PROTOTYPE
    assert (config.repositories, config.repositories_file) == ("prototype-file", REPOSITORIES)
    assert config.hints.queue_size == 64
    assert config.hints.liveness == timedelta(seconds=30)
    assert (config.hints.reconnect.base, config.hints.reconnect.max) == (
        timedelta(milliseconds=500),
        timedelta(seconds=10),
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("BUILD_HINT_QUEUE_SIZE", "0"),
        ("BUILD_HINT_QUEUE_SIZE", "10001"),
        ("BUILD_HINT_LIVENESS", "500ms"),
        ("BUILD_HINT_LIVENESS", "11m"),
        ("BUILD_HINT_RECONNECT_BASE", "0s"),
    ],
)
def test_the_hint_settings_are_bounded(key: str, value: str) -> None:
    err = _refused(load_build_worker, build_worker_env(REPOSITORIES) | {key: value})
    assert err.keys() == (key,)


def test_the_hint_reconnect_maximum_is_at_least_its_base() -> None:
    env = build_worker_env(REPOSITORIES) | {
        "BUILD_HINT_RECONNECT_BASE": "5s",
        "BUILD_HINT_RECONNECT_MAX": "1s",
    }
    assert _refused(load_build_worker, env).keys() == ("BUILD_HINT_RECONNECT_MAX",)


def test_the_production_build_worker_loads_without_the_repository_file() -> None:
    config = load_build_worker(production(build_worker_env(REPOSITORIES)).get)
    assert config.profile is Profile.PRODUCTION
    assert (config.repositories, config.repositories_file) == ("", None)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("REPOSITORY_MODE", "prototype-file"),
        ("REPOSITORY_MODE", "project-grpc"),
        ("PROJECT_GRPC_TARGET", "project.webblock.svc:9090"),
        ("PROTOTYPE_REPOSITORIES_FILE", str(REPOSITORIES)),
    ],
)
def test_the_production_build_worker_refuses_the_repository_file_by_name(
    key: str, value: str
) -> None:
    err = _refused(load_build_worker, production(build_worker_env(REPOSITORIES)) | {key: value})
    assert err.keys() == (key,)
    assert err.problems[0].reason.startswith("must be unset in production: ")
    assert "OD-01" in err.problems[0].reason
    assert "draft" in err.problems[0].reason


def test_the_bootstrap_process_refuses_production() -> None:
    err = _refused(load_bootstrap, production(bootstrap_env(KUBECONFIG)))
    assert [(p.key, p.reason) for p in err.problems] == [
        (
            "PROVISIONING_PROFILE",
            "production is refused: access_setup (the site's catalog login and Secret) has no "
            "production contract with the catalog owner (OD-46)",
        )
    ]


def test_the_operator_accepts_production() -> None:
    assert load_operator(production(operator_env()).get).profile is Profile.PRODUCTION
    broker = load_operator_broker((BROKER | {"PROVISIONING_PROFILE": "production"}).get)
    assert broker.profile is Profile.PRODUCTION


def _grpc_env() -> dict[str, str]:
    env = build_worker_env(REPOSITORIES) | {
        "REPOSITORY_MODE": "project-grpc",
        "PROJECT_GRPC_TARGET": "project.webblock.svc:9090",
    }
    del env["PROTOTYPE_REPOSITORIES_FILE"]
    return env


def test_the_build_worker_reads_the_grpc_repository_mode() -> None:
    config = load_build_worker(_grpc_env().get)
    assert config.repositories == "project-grpc"
    assert config.repositories_file is None
    assert config.project_grpc_target == "project.webblock.svc:9090"


def test_the_file_mode_has_no_grpc_target() -> None:
    config = load_build_worker(build_worker_env(REPOSITORIES).get)
    assert config.project_grpc_target is None


def test_the_grpc_mode_needs_a_target() -> None:
    env = _grpc_env()
    del env["PROJECT_GRPC_TARGET"]
    assert _refused(load_build_worker, env).keys() == ("PROJECT_GRPC_TARGET",)


@pytest.mark.parametrize("target", ["http://project:9090", "project", "project:0", "a/b:1"])
def test_the_grpc_target_must_be_host_and_port(target: str) -> None:
    err = _refused(load_build_worker, _grpc_env() | {"PROJECT_GRPC_TARGET": target})
    assert err.keys() == ("PROJECT_GRPC_TARGET",)


def test_the_grpc_mode_refuses_the_repository_file() -> None:
    err = _refused(
        load_build_worker, _grpc_env() | {"PROTOTYPE_REPOSITORIES_FILE": str(REPOSITORIES)}
    )
    assert err.keys() == ("PROTOTYPE_REPOSITORIES_FILE",)


def test_the_file_mode_refuses_the_grpc_target() -> None:
    err = _refused(
        load_build_worker,
        build_worker_env(REPOSITORIES) | {"PROJECT_GRPC_TARGET": "project.webblock.svc:9090"},
    )
    assert err.keys() == ("PROJECT_GRPC_TARGET",)
