"""The worker, build worker, bootstrap and operator sections (task G, wave 6): every key required
except the wave 6 keys whose defaults keep the earlier behaviour, every problem collected,
cross-checks between keys, and no value in any reason."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest
from tests.unit.hosts.envs import (
    MARKER,
    bootstrap_env,
    build_worker_env,
    operator_env,
    worker_env,
)

from provisioning.infrastructure.config import LoadError
from provisioning.infrastructure.config.bootstrap import load_bootstrap, read_bootstrap
from provisioning.infrastructure.config.build_worker import load_build_worker, read_build_worker
from provisioning.infrastructure.config.common import KubeconfigConnection
from provisioning.infrastructure.config.loader import Loader, required_keys
from provisioning.infrastructure.config.operator import load_operator, operator_keys
from provisioning.infrastructure.config.worker import load_worker, read_worker

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")
REPOSITORIES = Path("/project/bin/kind-state/proto/repositories.json")


def _refused(load: object, env: dict[str, str]) -> LoadError:
    with pytest.raises(LoadError) as exc_info:
        load(env.get)  # type: ignore[operator]  # one of the three load functions
    return exc_info.value


def test_the_worker_loads_a_complete_environment() -> None:
    c = load_worker(worker_env(KUBECONFIG).get)
    assert c.worker_login.user == "worker_login"
    assert c.hosting_login.user == "hosting_login"
    assert c.cluster.connection == KubeconfigConnection(KUBECONFIG)
    assert c.deploy.ready_timeout == timedelta(seconds=90)
    assert c.site.progress_deadline == timedelta(seconds=60)
    assert c.dispatch.backoff.max == timedelta(seconds=30)
    assert c.broker.url.reveal().startswith("amqp://")
    assert MARKER not in repr(c)


def test_the_build_worker_loads_a_complete_environment() -> None:
    c = load_build_worker(build_worker_env(REPOSITORIES).get)
    assert (c.intake_login.user, c.build_login.user) == ("intake_login", "build_login")
    assert c.repositories_file == REPOSITORIES
    assert c.inbox.visibility == timedelta(minutes=10)
    assert c.poller.dispatch_grace == timedelta(minutes=1)
    assert MARKER not in repr(c)


def test_the_bootstrap_process_loads_a_complete_environment() -> None:
    c = load_bootstrap(bootstrap_env(KUBECONFIG).get)
    assert c.reader_login.user == "bootstrap_reader"
    assert c.cluster.connection == KubeconfigConnection(KUBECONFIG)
    assert (c.tenant.quota_cpu, c.tenant.quota_pods) == ("2", 10)
    assert c.loop.concurrency == 4
    assert MARKER not in repr(c)


@pytest.mark.parametrize(
    ("load", "read", "env"),
    [
        (load_worker, read_worker, worker_env(KUBECONFIG)),
        (load_build_worker, read_build_worker, build_worker_env(REPOSITORIES)),
        (load_bootstrap, read_bootstrap, bootstrap_env(KUBECONFIG)),
    ],
    ids=["worker", "build_worker", "bootstrap"],
)
def test_an_empty_environment_names_every_key(
    load: object, read: Callable[[Loader], object], env: dict[str, str]
) -> None:
    err = _refused(load, {})
    assert set(err.keys()) == set(env)
    assert err.keys() == required_keys(read)


def test_two_roles_of_one_process_need_two_logins() -> None:
    env = worker_env(KUBECONFIG) | {"PG_HOSTING_INTAKE_USER": "worker_login"}
    assert _refused(load_worker, env).keys() == ("PG_HOSTING_INTAKE_USER",)
    env = build_worker_env(REPOSITORIES) | {"PG_BUILD_USER": "intake_login"}
    assert _refused(load_build_worker, env).keys() == ("PG_BUILD_USER",)


def test_the_progress_deadline_is_below_the_ready_timeout() -> None:
    env = worker_env(KUBECONFIG) | {"SITE_PROGRESS_DEADLINE": "90s"}
    assert _refused(load_worker, env).keys() == ("SITE_PROGRESS_DEADLINE",)


def test_a_backoff_maximum_below_its_base_is_refused() -> None:
    env = worker_env(KUBECONFIG) | {"DISPATCH_BACKOFF_MAX": "100ms"}
    assert _refused(load_worker, env).keys() == ("DISPATCH_BACKOFF_MAX",)


def test_bootstrap_concurrency_is_at_most_the_batch() -> None:
    env = bootstrap_env(KUBECONFIG) | {"BOOTSTRAP_CONCURRENCY": "51"}
    assert _refused(load_bootstrap, env).keys() == ("BOOTSTRAP_CONCURRENCY",)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RABBITMQ_URL", f"http://user:{MARKER}@host/"),
        ("RABBITMQ_URL", f"amqp://user:{MARKER}@/"),
        ("WORKER_KUBECONFIG", f"relative/{MARKER}"),
        ("SITE_DOMAIN", f"*.{MARKER.lower()}.test"),
        ("SITE_GATEWAY_HTTPS_URL", f"https://user:{MARKER}@172.18.255.200"),
        ("SITE_GATEWAY_HTTPS_URL", f"http://{MARKER}.test"),
        ("SITE_GATEWAY_CA_FILE", f"relative/{MARKER}"),
        ("PREVIEW_PROBE_TOKEN", f"short-{MARKER}"),
        ("PREVIEW_PROBE_TOKEN", f"has a space {MARKER} 0123456789abcdefghij"),
        ("DEPLOY_IMAGE_REGISTRY", f"https://{MARKER}"),
        ("HOSTING_ENTITLEMENT_MODE", MARKER),
        ("SITE_PROBE_PATH", f"/readyz?{MARKER}"),
        ("SITE_PROBE_PATH", f"//{MARKER}/readyz"),
        ("SITE_PROBE_PATH", f"{MARKER}"),
    ],
)
def test_a_refused_worker_value_is_never_quoted(key: str, value: str) -> None:
    err = _refused(load_worker, worker_env(KUBECONFIG) | {key: value})
    assert err.keys() == (key,)
    assert MARKER not in str(err)
    assert MARKER.lower() not in str(err)


def test_s5_the_worker_site_domain_is_at_most_181_characters() -> None:
    """Security review S5: ``<63>-preview.<site domain>``, for the longest sub-domain the database
    allows, must fit in 253 characters, so the worker refuses a SITE_DOMAIN over 181 characters
    and accepts one of exactly 181."""
    longest = "a" * 63 + "." + "b" * 63 + "." + "c" * 53
    assert len(longest) == 181
    c = load_worker((worker_env(KUBECONFIG) | {"SITE_DOMAIN": longest}).get)
    assert c.site.domain == longest
    err = _refused(load_worker, worker_env(KUBECONFIG) | {"SITE_DOMAIN": longest + "c"})
    assert err.keys() == ("SITE_DOMAIN",)


def test_the_wave_6_worker_keys_default_to_the_earlier_behaviour() -> None:
    c = load_worker(worker_env(KUBECONFIG).get)
    assert (c.reconcile.interval, c.reconcile.batch_size) == (timedelta(seconds=30), 50)
    assert (c.site.probe_successes, c.site.probe_max_body_bytes) == (1, 65536)
    assert (c.usage.publish_interval, c.usage.batch_size) == (timedelta(seconds=5), 50)
    assert (c.entitlement, c.entitlement_file) == ("prototype-allow", None)


def test_the_wave_6_worker_keys_are_read_when_set() -> None:
    env = worker_env(KUBECONFIG) | {
        "RECONCILE_INTERVAL": "1m",
        "RECONCILE_BATCH": "20",
        "SITE_PROBE_SUCCESSES": "3",
        "SITE_PROBE_MAX_BODY_BYTES": "4096",
        "USAGE_PUBLISH_INTERVAL": "2s",
        "USAGE_BATCH_SIZE": "10",
        "ENTITLEMENT_MODE": "prototype-file",
        "ENTITLEMENT_FILE": "/project/bin/kind-state/proto/entitlement.json",
    }
    c = load_worker(env.get)
    assert (c.reconcile.interval, c.reconcile.batch_size) == (timedelta(minutes=1), 20)
    assert (c.site.probe_successes, c.site.probe_max_body_bytes) == (3, 4096)
    assert (c.usage.publish_interval, c.usage.batch_size) == (timedelta(seconds=2), 10)
    assert c.entitlement == "prototype-file"
    assert c.entitlement_file == Path("/project/bin/kind-state/proto/entitlement.json")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RECONCILE_INTERVAL", "500ms"),
        ("RECONCILE_INTERVAL", "2h"),
        ("RECONCILE_BATCH", "0"),
        ("RECONCILE_BATCH", "1001"),
        ("SITE_PROBE_SUCCESSES", "0"),
        ("SITE_PROBE_SUCCESSES", "11"),
        ("SITE_PROBE_MAX_BODY_BYTES", "1023"),
        ("SITE_PROBE_MAX_BODY_BYTES", "1048577"),
        ("USAGE_PUBLISH_INTERVAL", "5ms"),
        ("USAGE_BATCH_SIZE", "0"),
        ("ENTITLEMENT_MODE", "prototype-files"),
    ],
)
def test_the_wave_6_worker_keys_are_bounded(key: str, value: str) -> None:
    assert _refused(load_worker, worker_env(KUBECONFIG) | {key: value}).keys() == (key,)


def test_the_entitlement_file_is_required_in_file_mode_and_refused_otherwise() -> None:
    file_mode = worker_env(KUBECONFIG) | {"ENTITLEMENT_MODE": "prototype-file"}
    assert _refused(load_worker, file_mode).keys() == ("ENTITLEMENT_FILE",)
    relative = file_mode | {"ENTITLEMENT_FILE": f"relative/{MARKER}"}
    err = _refused(load_worker, relative)
    assert err.keys() == ("ENTITLEMENT_FILE",)
    assert MARKER not in str(err)
    stray = worker_env(KUBECONFIG) | {"ENTITLEMENT_FILE": "/project/entitlement.json"}
    assert _refused(load_worker, stray).keys() == ("ENTITLEMENT_FILE",)


def test_the_bootstrap_recheck_rounds_default_to_ten_and_are_bounded() -> None:
    assert load_bootstrap(bootstrap_env(KUBECONFIG).get).loop.recheck_rounds == 10
    env = bootstrap_env(KUBECONFIG) | {"BOOTSTRAP_RECHECK_ROUNDS": "3"}
    assert load_bootstrap(env.get).loop.recheck_rounds == 3
    env = bootstrap_env(KUBECONFIG) | {"BOOTSTRAP_RECHECK_ROUNDS": "0"}
    assert _refused(load_bootstrap, env).keys() == ("BOOTSTRAP_RECHECK_ROUNDS",)


def test_the_build_pending_grace_defaults_to_thirty_seconds_and_is_bounded() -> None:
    c = load_build_worker(build_worker_env(REPOSITORIES).get)
    assert c.poller.pending_grace == timedelta(seconds=30)
    env = build_worker_env(REPOSITORIES) | {"BUILD_PENDING_GRACE": "2m"}
    assert load_build_worker(env.get).poller.pending_grace == timedelta(minutes=2)
    env = build_worker_env(REPOSITORIES) | {"BUILD_PENDING_GRACE": "500ms"}
    assert _refused(load_build_worker, env).keys() == ("BUILD_PENDING_GRACE",)


def test_the_operator_cli_needs_the_database_and_one_login() -> None:
    env = operator_env()
    c = load_operator(env.get)
    assert c.operator_login.user == "prov_operator_login"
    assert c.database.name == "provisioning"
    assert MARKER not in repr(c)
    assert set(_refused(load_operator, {}).keys()) == set(env)
    assert _refused(load_operator, {}).keys() == operator_keys()


@pytest.mark.parametrize("value", ["2", "500m", "1.5", "1Gi", "512Mi"])
def test_tenant_quantities_are_accepted(value: str) -> None:
    c = load_bootstrap((bootstrap_env(KUBECONFIG) | {"TENANT_QUOTA_MEMORY": value}).get)
    assert c.tenant.quota_memory == value


@pytest.mark.parametrize("value", ["two", "-1", "1Gb", "1 Gi", "1.", ".5", "1e3x"])
def test_tenant_quantities_are_refused(value: str) -> None:
    env = bootstrap_env(KUBECONFIG) | {"TENANT_QUOTA_MEMORY": value}
    assert _refused(load_bootstrap, env).keys() == ("TENANT_QUOTA_MEMORY",)


# --- decision 0022 E7: payment-grpc on the worker -----------------------------------------------


def _payment(**changes: str) -> dict[str, str]:
    return worker_env(KUBECONFIG) | {"PAYMENT_GRPC_TARGET": "payment.webblock.svc:9090"} | changes


@pytest.mark.parametrize(
    "mode", [{"ENTITLEMENT_MODE": "payment-grpc"}, {"HOSTING_ENTITLEMENT_MODE": "payment-grpc"}]
)
def test_either_worker_mode_reads_the_payment_target(mode: dict[str, str]) -> None:
    c = load_worker((_payment() | mode).get)
    assert c.payment_grpc_target == "payment.webblock.svc:9090"
    assert "payment-grpc" in (c.entitlement, c.hosting_entitlement)


def test_both_worker_modes_share_one_target() -> None:
    env = _payment(ENTITLEMENT_MODE="payment-grpc", HOSTING_ENTITLEMENT_MODE="payment-grpc")
    c = load_worker(env.get)
    assert (c.entitlement, c.hosting_entitlement) == ("payment-grpc", "payment-grpc")


def test_the_worker_payment_mode_needs_the_target_and_the_target_needs_a_mode() -> None:
    missing = worker_env(KUBECONFIG) | {"ENTITLEMENT_MODE": "payment-grpc"}
    assert _refused(load_worker, missing).keys() == ("PAYMENT_GRPC_TARGET",)
    stray = _payment()
    assert _refused(load_worker, stray).keys() == ("PAYMENT_GRPC_TARGET",)


def test_the_worker_refuses_the_payment_modes_in_production() -> None:
    from tests.unit.hosts.envs import production

    for key in ("ENTITLEMENT_MODE", "HOSTING_ENTITLEMENT_MODE"):
        env = production(worker_env(KUBECONFIG)) | {key: "payment-grpc"}
        err = _refused(load_worker, env)
        assert key in err.keys()
        reason = next(p.reason for p in err.problems if p.key == key)
        assert "OD-04" in reason and "draft" in reason
