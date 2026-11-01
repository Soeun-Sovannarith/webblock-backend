"""The observability keys of every long-lived process (task H4): SENTRY_DSN is required with no
default and ``none`` turns reports off; a DSN needs SENTRY_ENVIRONMENT and
SENTRY_TRACES_SAMPLE_RATE; the worker, build worker and bootstrap process each need their own
OPS_LISTEN_ADDR; and no reason ever repeats a value."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.hosts.envs import bootstrap_env, build_worker_env, worker_env
from tests.unit.infrastructure.config.test_api import ENV as API_ENV

from provisioning.infrastructure.config import LoadError, load, load_api, required_keys
from provisioning.infrastructure.config.api import read_api
from provisioning.infrastructure.config.bootstrap import read_bootstrap
from provisioning.infrastructure.config.build_worker import read_build_worker
from provisioning.infrastructure.config.telemetry import valid_dsn
from provisioning.infrastructure.config.worker import read_worker

DSN = "https://0123456789abcdef@sentry.example.invalid/42"
KEY = "0123456789abcdef"

type Reader = Callable[..., object]


def _envs(tmp: Path) -> dict[str, tuple[dict[str, str], Reader]]:
    kubeconfig = tmp / "kubeconfig"
    return {
        "api": (dict(API_ENV), read_api),
        "worker": (worker_env(kubeconfig), read_worker),
        "build_worker": (build_worker_env(tmp / "repositories.json"), read_build_worker),
        "bootstrap": (bootstrap_env(kubeconfig), read_bootstrap),
    }


PROCESSES = ("api", "worker", "build_worker", "bootstrap")


@pytest.mark.parametrize("process", PROCESSES)
def test_sentry_dsn_is_required_in_every_long_lived_process(tmp_path: Path, process: str) -> None:
    env, read = _envs(tmp_path)[process]
    assert "SENTRY_DSN" in required_keys(read)
    del env["SENTRY_DSN"]
    with pytest.raises(LoadError) as caught:
        load(env.get, read)
    assert caught.value.keys() == ("SENTRY_DSN",)


@pytest.mark.parametrize("process", ("worker", "build_worker", "bootstrap"))
def test_the_workers_and_bootstrap_require_their_own_ops_listener(
    tmp_path: Path, process: str
) -> None:
    env, read = _envs(tmp_path)[process]
    assert "OPS_LISTEN_ADDR" in required_keys(read)
    del env["OPS_LISTEN_ADDR"]
    with pytest.raises(LoadError) as caught:
        load(env.get, read)
    assert caught.value.keys() == ("OPS_LISTEN_ADDR",)
    env["OPS_LISTEN_ADDR"] = "http://127.0.0.1:1"
    with pytest.raises(LoadError) as caught:
        load(env.get, read)
    assert caught.value.keys() == ("OPS_LISTEN_ADDR",)


@pytest.mark.parametrize("process", PROCESSES)
def test_sentry_dsn_none_builds_no_sentry_configuration(tmp_path: Path, process: str) -> None:
    env, read = _envs(tmp_path)[process]
    config = load(env.get, read)
    assert getattr(config, "sentry") is None  # noqa: B009  # one check for four config types


@pytest.mark.parametrize("process", PROCESSES)
def test_a_dsn_needs_its_environment_and_sample_rate(tmp_path: Path, process: str) -> None:
    env, read = _envs(tmp_path)[process]
    env["SENTRY_DSN"] = DSN
    with pytest.raises(LoadError) as caught:
        load(env.get, read)
    assert caught.value.keys() == ("SENTRY_ENVIRONMENT", "SENTRY_TRACES_SAMPLE_RATE")
    env |= {"SENTRY_ENVIRONMENT": "staging", "SENTRY_TRACES_SAMPLE_RATE": "0"}
    config = load(env.get, read)
    sentry = getattr(config, "sentry")  # noqa: B009  # one check for four config types
    assert sentry is not None
    assert (sentry.environment, sentry.traces_sample_rate) == ("staging", 0.0)
    assert sentry.dsn.reveal() == DSN
    assert KEY not in repr(config) and KEY not in str(sentry)


def test_with_none_the_dsn_settings_must_be_unset() -> None:
    env = dict(API_ENV) | {"SENTRY_ENVIRONMENT": "staging", "SENTRY_TRACES_SAMPLE_RATE": "0.1"}
    with pytest.raises(LoadError) as caught:
        load_api(env.get)
    assert caught.value.keys() == ("SENTRY_ENVIRONMENT", "SENTRY_TRACES_SAMPLE_RATE")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SENTRY_DSN", "https://sentry.example.invalid/42"),  # no public key
        ("SENTRY_DSN", f"ftp://{KEY}@sentry.example.invalid/42"),
        ("SENTRY_DSN", f"https://{KEY}@sentry.example.invalid/project"),
        ("SENTRY_DSN", f"https://{KEY}@sentry.example.invalid/42?x={KEY}"),
        ("SENTRY_DSN", "None"),
        ("SENTRY_ENVIRONMENT", "Has Spaces"),
        ("SENTRY_TRACES_SAMPLE_RATE", "1.5"),
        ("SENTRY_TRACES_SAMPLE_RATE", "-0.1"),
        ("SENTRY_TRACES_SAMPLE_RATE", "nan"),
    ],
)
def test_invalid_values_are_refused_without_being_repeated(key: str, value: str) -> None:
    env = dict(API_ENV) | {
        "SENTRY_DSN": DSN,
        "SENTRY_ENVIRONMENT": "staging",
        "SENTRY_TRACES_SAMPLE_RATE": "0.25",
    }
    env[key] = value
    with pytest.raises(LoadError) as caught:
        load_api(env.get)
    assert caught.value.keys() == (key,)
    assert value not in str(caught.value)
    assert KEY not in str(caught.value)


def test_the_api_ops_listener_refuses_the_public_listeners_address() -> None:
    env = dict(API_ENV) | {
        "HTTP_LISTEN_ADDR": "127.0.0.1:18080",
        "OPS_LISTEN_ADDR": "127.0.0.1:18080",
    }
    with pytest.raises(LoadError) as caught:
        load_api(env.get)
    assert caught.value.keys() == ("OPS_LISTEN_ADDR",)


@pytest.mark.parametrize(
    ("dsn", "valid"),
    [
        (DSN, True),
        (f"http://{KEY}@sentry.svc:9000/7", True),
        (f"https://{KEY}:secretpart@sentry.example.invalid/path/42", True),
        (f"https://{KEY}@/42", False),
        (f"https://{KEY}@host:99999/42", False),
        (f"https://{KEY}@host/42#frag", False),
        (f"https://{KEY}@host/42\n", False),
    ],
)
def test_valid_dsn(dsn: str, valid: bool) -> None:
    assert valid_dsn(dsn) is valid


@pytest.mark.parametrize("process", PROCESSES)
def test_tracing_is_off_when_the_endpoint_is_unset_or_none(tmp_path: Path, process: str) -> None:
    env, read = _envs(tmp_path)[process]
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in required_keys(read)
    env.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    assert getattr(load(env.get, read), "tracing") is None  # noqa: B009  # four config types
    env |= {"OTEL_EXPORTER_OTLP_ENDPOINT": "none", "OTEL_SERVICE_NAME": "provisioning-api"}
    assert getattr(load(env.get, read), "tracing") is None  # noqa: B009  # four config types


@pytest.mark.parametrize("process", PROCESSES)
def test_an_endpoint_turns_tracing_on(tmp_path: Path, process: str) -> None:
    env, read = _envs(tmp_path)[process]
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://otel-collector.observability:4318"
    tracing = getattr(load(env.get, read), "tracing")  # noqa: B009  # four config types
    assert tracing is not None
    assert tracing.endpoint == "http://otel-collector.observability:4318"
    assert tracing.service_name is None
    env["OTEL_SERVICE_NAME"] = "provisioning-worker"
    tracing = getattr(load(env.get, read), "tracing")  # noqa: B009  # four config types
    assert tracing.service_name == "provisioning-worker"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "collector:4318"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://user:secretpw@collector:4318"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/?key=secretpw"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT", "None"),
        ("OTEL_SERVICE_NAME", "Other Service"),
        ("OTEL_SERVICE_NAME", "api"),
    ],
)
def test_invalid_tracing_values_are_refused_without_being_repeated(key: str, value: str) -> None:
    env = dict(API_ENV) | {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}
    env[key] = value
    with pytest.raises(LoadError) as caught:
        load_api(env.get)
    assert caught.value.keys() == (key,)
    assert value not in str(caught.value)


@pytest.mark.parametrize(
    "key",
    [
        "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST",
        "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE",
        "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS",
    ],
)
@pytest.mark.parametrize("endpoint", ["http://collector:4318", "none"])
def test_a_header_capture_key_is_refused_without_its_value(key: str, endpoint: str) -> None:
    env = dict(API_ENV) | {"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint, key: "authorization-xyz"}
    with pytest.raises(LoadError) as caught:
        load_api(env.get)
    assert caught.value.keys() == (key,)
    assert "authorization-xyz" not in str(caught.value)
    env[key] = ""
    load_api(env.get)  # empty is unset
