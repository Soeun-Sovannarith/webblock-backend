"""The cluster mode of scripts/prototype/run-provisioning.sh (task PK; decision 0016 section 8
rulings 1-2, section 9 rulings 1-3), checked without a stack: configure() writes for each process
exactly the settings its loader reads, in both modes; the secret keys are exactly those the loaders
read as secrets and never reach a ConfigMap, a values file or a terminal; the mode is chosen once
per run and a host start always records host; endpoints.env names the address the API listens on;
kill-process freezes, scales down and kills; standins-sync waits for every mounting pod; the
cluster stop removes what Helm leaves; proto-e2e and proto-down know the mode. The shell functions
run in a clean bash with stubs."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import pty
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
import yaml  # type: ignore[import-untyped]  # PyYAML has no stubs in the lock; tests only
from tests.unit.scripts._configure import (
    API_ADDRESS,
    HOST_JENKINS_URL,
    MQ_PORT,
    PG_PORT,
    SCRIPT,
    TEXT,
    configure,
    function,
    read_env,
)

from provisioning.infrastructure.config import load_api, load_operator
from provisioning.infrastructure.config.bootstrap import load_bootstrap
from provisioning.infrastructure.config.build_worker import load_build_worker
from provisioning.infrastructure.config.loader import Loader
from provisioning.infrastructure.config.operator import load_operator_broker
from provisioning.infrastructure.config.worker import load_worker

ROOT = SCRIPT.parents[2]
CLUSTER_VALUES = ROOT / "scripts" / "prototype" / "cluster_values.py"
POD_NAME = "provisioning-worker-5d9c7f8b6-x2kqp"
LOADERS: dict[str, Callable[..., object]] = {
    "api": load_api,
    "worker": load_worker,
    "build_worker": load_build_worker,
    "bootstrap": load_bootstrap,
    "operator": load_operator,
    "operator_as_worker": load_operator,
    "operator_dlq": load_operator_broker,
}
PROCESSES = ("api", "worker", "build_worker", "bootstrap")


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cluster_values", CLUSTER_VALUES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cluster_values = _load_module()


def _env(env_dir: Path, name: str, mode: str) -> dict[str, str]:
    env = read_env(env_dir / f"{name}.env")
    if mode == "cluster" and name == "worker":
        env["WORKER_ID"] = POD_NAME  # the chart sets it from the pod's name
    return env


def _bash(program: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
        check=False,
    )


def _plain_env(path: Path) -> dict[str, str]:
    """A KEY=value file as the e2e harness reads it (tests/e2e/_stack.read_env_file)."""
    out = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        out[key] = value
    return out


# --- configure(): the settings of each process, in both modes -------------------------------------


@pytest.mark.parametrize("mode", ["host", "cluster"])
def test_every_env_file_is_complete_for_its_process(tmp_path: Path, mode: str) -> None:
    """Each file loads with its process's loader (every required key, nothing refused), and holds
    no key that the loader does not read."""
    env_dir = configure(tmp_path, mode)
    for name, load in LOADERS.items():
        env = _env(env_dir, name, mode)
        asked: set[str] = set()

        def lookup(key: str, env: dict[str, str] = env, asked: set[str] = asked) -> str | None:
            asked.add(key)
            return env.get(key)

        load(lookup)
        assert set(env) <= asked, (name, sorted(set(env) - asked))


def test_the_cluster_mode_reaches_the_containers_by_name_and_reads_the_chart_mounts(
    tmp_path: Path,
) -> None:
    env_dir = configure(tmp_path, "cluster")
    worker = read_env(env_dir / "worker.env")
    assert (worker["PG_HOST"], worker["PG_PORT"]) == ("webblock-provisioning-db", "5432")
    assert worker["RABBITMQ_URL"].endswith("@webblock-rabbitmq:5672/")
    assert worker["KUBE_CONNECTION"] == "in-cluster"
    assert worker["KUBE_TOKEN_FILE"] == "/var/run/secrets/webblock/kube/token"  # noqa: S105  # a path
    assert worker["KUBE_EXPECTED_USERNAME"] == "system:serviceaccount:webblock:provisioning-worker"
    assert "WORKER_KUBECONFIG" not in worker and "WORKER_ID" not in worker
    assert worker["SITE_GATEWAY_CA_FILE"] == "/etc/webblock/gateway-ca/ca.crt"
    assert worker["ENTITLEMENT_FILE"] == "/etc/webblock/standins/entitlement.json"
    assert worker["OPS_LISTEN_ADDR"] == "0.0.0.0:9090"
    bootstrap = read_env(env_dir / "bootstrap.env")
    assert bootstrap["KUBE_EXPECTED_USERNAME"] == "system:serviceaccount:webblock:tenant-bootstrap"
    assert (bootstrap["CATALOG_HOST"], bootstrap["CATALOG_PORT"]) == ("webblock-catalog-db", "5432")
    build = read_env(env_dir / "build_worker.env")
    assert build["JENKINS_URL"] == "http://webblock-jenkins:8080"
    assert build["PROTOTYPE_REPOSITORIES_FILE"] == "/etc/webblock/standins/repositories.json"
    api = read_env(env_dir / "api.env")
    assert (api["HTTP_LISTEN_ADDR"], api["OPS_LISTEN_ADDR"]) == ("0.0.0.0:8080", "0.0.0.0:8081")
    assert api["MCP_ALLOWED_HOSTS"] == f"{API_ADDRESS}:8080"
    assert api["AUTH_DEV_TOKENS_FILE"] == "/etc/webblock/dev-tokens/tokens.json"
    assert api["PROJECT_ACCESS_FILE"] == "/etc/webblock/standins/access.json"
    password = _plain_env(env_dir.parent / "provisioning.env")["MIGRATOR_PASSWORD"]
    assert read_env(env_dir / "migrate.env") == {
        "GOOSE_DBSTRING": f"postgres://prov_migrator_login:{password}@webblock-provisioning-db:5432/"
        "provisioning?sslmode=disable&options=-c%20role%3Dprovisioning_migrator"
    }
    # The operator CLI runs on the host in both modes: the published ports.
    for name in ("operator", "operator_as_worker"):
        operator = read_env(env_dir / f"{name}.env")
        assert (operator["PG_HOST"], operator["PG_PORT"]) == ("127.0.0.1", PG_PORT), name
    assert read_env(env_dir / "operator_as_worker.env")["PG_USER"] == "prov_worker_login"
    assert read_env(env_dir / "operator_dlq.env")["RABBITMQ_URL"].endswith(f"@127.0.0.1:{MQ_PORT}/")


def test_the_host_mode_is_as_before(tmp_path: Path) -> None:
    env_dir = configure(tmp_path, "host")
    prov = env_dir.parent
    worker = read_env(env_dir / "worker.env")
    assert (worker["PG_HOST"], worker["PG_PORT"]) == ("127.0.0.1", PG_PORT)
    assert worker["KUBE_CONNECTION"] == "kubeconfig"
    assert worker["WORKER_KUBECONFIG"] == str(prov.parent / "worker.kubeconfig")
    assert worker["WORKER_ID"] == "prototype-worker-1"
    assert worker["ENTITLEMENT_FILE"] == str(prov / "entitlement.json")
    assert read_env(env_dir / "bootstrap.env")["KUBE_CONNECTION"] == "kubeconfig"
    api = read_env(env_dir / "api.env")
    assert api["PROJECT_ACCESS_FILE"] == str(prov / "access.json")
    assert api["AUTH_DEV_TOKENS_FILE"] == str(prov / "tokens.json")
    listeners = {name: read_env(env_dir / f"{name}.env")["OPS_LISTEN_ADDR"] for name in PROCESSES}
    assert listeners == {
        "api": "127.0.0.1:18081",
        "worker": "127.0.0.1:18082",
        "build_worker": "127.0.0.1:18083",
        "bootstrap": "127.0.0.1:18084",
    }
    assert not (env_dir / "migrate.env").exists()


# --- endpoints.env and jenkins.env: what the e2e harness reads (task PK-4) ------------------------


@pytest.mark.parametrize("replicas", [False, True])
def test_on_the_host_the_endpoints_are_the_addresses_the_apis_listen_on(
    tmp_path: Path, replicas: bool
) -> None:
    """The harness took the API's address from HTTP_LISTEN_ADDR before task PK; endpoints.env
    names the same address, and on a replica run the second API's as API2_URL."""
    env_dir = configure(tmp_path, "host", replicas=replicas)
    endpoints = _plain_env(env_dir.parent / "endpoints.env")
    expected = {"API_URL": f"http://{read_env(env_dir / 'api.env')['HTTP_LISTEN_ADDR']}"}
    if replicas:
        expected["API2_URL"] = f"http://{read_env(env_dir / 'api-2.env')['HTTP_LISTEN_ADDR']}"
    assert endpoints == expected
    assert endpoints["API_URL"] == "http://127.0.0.1:18080"
    assert endpoints.get("API2_URL", "http://127.0.0.1:18090") == "http://127.0.0.1:18090"
    assert ((env_dir.parent / "endpoints.env").stat().st_mode & 0o077) == 0


def test_in_the_cluster_the_endpoint_is_the_api_services_address(tmp_path: Path) -> None:
    env_dir = configure(tmp_path, "cluster")
    endpoints = _plain_env(env_dir.parent / "endpoints.env")
    assert endpoints == {"API_URL": f"http://{API_ADDRESS}:8080"}
    # The MCP host allow-list names the same address, so the harness's MCP calls are accepted.
    assert (
        read_env(env_dir / "api.env")["MCP_ALLOWED_HOSTS"] == endpoints["API_URL"][len("http://") :]
    )


@pytest.mark.parametrize("mode", ["host", "cluster"])
def test_jenkins_env_keeps_the_host_reachable_url_the_harness_reads(
    tmp_path: Path, mode: str
) -> None:
    """The harness reads Jenkins from the stack's jenkins.env (PK-4), which proto-up writes with
    the host's published port; the run script never rewrites it, in either mode. On the host the
    build worker talks to the same URL; in the cluster it uses Jenkins' name on the kind network."""
    env_dir = configure(tmp_path, mode)
    jenkins = _plain_env(tmp_path / "proto" / "jenkins.env")
    assert jenkins["JENKINS_URL"] == HOST_JENKINS_URL
    build = read_env(env_dir / "build_worker.env")
    if mode == "host":
        assert build["JENKINS_URL"] == jenkins["JENKINS_URL"]
    else:
        assert build["JENKINS_URL"] == "http://webblock-jenkins:8080"
    assert not re.search(r'>>?\s*"?\$JENKINS_ENV', TEXT)
    ci = (ROOT / "deploy" / "kind" / "proto-ci.sh").read_text()
    assert re.search(r"printf 'JENKINS_URL=%s\\n[^']*' \"\$JENKINS_URL_HOST\"", ci)
    lib = (ROOT / "deploy" / "kind" / "proto-lib.sh").read_text()
    assert 'JENKINS_URL_HOST="http://127.0.0.1:$PROTO_JENKINS_PORT"' in lib


# --- the secret keys ------------------------------------------------------------------------------


def test_the_secret_keys_are_exactly_what_the_processes_read_as_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = configure(tmp_path, "cluster")
    read_as_secret: set[str] = set()
    original = Loader.secret

    def recording(self: Loader, key: str, check: Callable[[str], str | None]) -> object:
        read_as_secret.add(key)
        return original(self, key, check)

    monkeypatch.setattr(Loader, "secret", recording)
    for name in PROCESSES:
        LOADERS[name](_env(env_dir, name, "cluster").get)
    assert cluster_values.SECRET_KEYS == frozenset(read_as_secret | {"GOOSE_DBSTRING"})


def test_values_carry_no_secret_and_secrets_carry_nothing_else(tmp_path: Path) -> None:
    env_dir = configure(tmp_path, "cluster")
    values = cluster_values.chart_values(env_dir)
    assert set(values) == {"api", "worker", "buildWorker", "bootstrap"}
    for name, key in (
        ("api", "api"),
        ("worker", "worker"),
        ("build_worker", "buildWorker"),
        ("bootstrap", "bootstrap"),
    ):
        env = read_env(env_dir / f"{name}.env")
        secret = sorted(k for k in env if k in cluster_values.SECRET_KEYS)
        assert values[key]["secretEnv"] == secret, name
        assert values[key]["env"] == {k: v for k, v in env.items() if k not in secret}, name
    listed = cluster_values.secrets(env_dir)
    assert listed["kind"] == "List"
    names = [item["metadata"]["name"] for item in listed["items"]]
    assert names == [
        f"provisioning-{c}-secret"
        for c in ("api", "worker", "build-worker", "bootstrap", "migrate")
    ]
    for item, name in zip(
        listed["items"], ("api", "worker", "build_worker", "bootstrap", "migrate"), strict=True
    ):
        env = read_env(env_dir / f"{name}.env")
        decoded = {k: base64.b64decode(v).decode() for k, v in item["data"].items()}
        assert decoded == {k: v for k, v in env.items() if k in cluster_values.SECRET_KEYS}, name


def test_secrets_are_never_printed_to_a_terminal(tmp_path: Path) -> None:
    env_dir = configure(tmp_path, "cluster")
    leader, follower = pty.openpty()
    try:
        done = subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and the script
            [sys.executable, "-I", str(CLUSTER_VALUES), "secrets", str(env_dir)],
            stdout=follower,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        os.close(follower)
        os.close(leader)
    assert done.returncode == 2
    assert "refusing to print Secrets to a terminal" in done.stderr


def test_a_line_that_is_not_key_quoted_value_is_refused_without_its_value(tmp_path: Path) -> None:
    for name in ("api", "worker", "build_worker", "bootstrap", "migrate"):
        (tmp_path / f"{name}.env").write_text("PROVISIONING_PROFILE='prototype'\n")
    (tmp_path / "worker.env").write_text("PG_PASS=s3cret-VALUE-MARKER\n")
    done = subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and the script
        [sys.executable, "-I", str(CLUSTER_VALUES), "values", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 1
    assert "worker.env line 1" in done.stderr
    assert "MARKER" not in done.stderr + done.stdout


# --- the mode -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "recorded", "mode", "message"),
    [
        ({}, None, "host", ""),
        ({"PROV_MODE": "cluster"}, None, "cluster", ""),
        ({"PROV_MODE": "cluster"}, "cluster", "cluster", ""),
        ({"PROV_MODE": "pods"}, None, "", "PROV_MODE must be host or cluster"),
        ({"PROV_MODE": "cluster"}, "host", "", "a host-mode run is recorded"),
        ({}, "cluster", "", "a cluster-mode run is recorded"),
        (
            {"PROV_MODE": "cluster", "PROV_REPLICAS": "2"},
            None,
            "",
            "PROV_REPLICAS=2 runs on the host only",
        ),
        (
            {"PROV_MODE": "cluster", "PROJECT_ACCESS_MODE": "project-service"},
            None,
            "",
            "runs on the host only",
        ),
        ({"PROV_REPLICAS": "2", "PROJECT_ACCESS_MODE": "project-service"}, None, "host", ""),
    ],
)
def test_the_mode_is_chosen_once_per_run(
    tmp_path: Path, env: dict[str, str], recorded: str | None, mode: str, message: str
) -> None:
    mode_file = tmp_path / "mode"
    if recorded is not None:
        mode_file.write_text(recorded + "\n")
    done = _bash(
        "\n".join(
            [
                "set -euo pipefail",
                'die() { echo "die: $*" >&2; exit 1; }',
                f"MODE_FILE='{mode_file}'",
                function("run_mode"),
                function("start_mode"),
                "start_mode",
            ]
        ),
        env,
    )
    if message:
        assert done.returncode != 0
        assert message in done.stderr
    else:
        assert done.returncode == 0, done.stderr
        assert done.stdout == mode


def _start(
    tmp_path: Path, recorded: str | None, env: dict[str, str] | None = None
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Runs the script's cmd_start with every step stubbed: each step records its name and the
    recorded mode at that moment."""
    prov = tmp_path / "prov"
    (prov / "env").mkdir(parents=True)
    (tmp_path / "bin").mkdir()
    goose = tmp_path / "bin" / "goose"
    goose.write_text("#!/bin/sh\n")
    goose.chmod(0o755)
    mode_file = prov / "mode"
    if recorded is not None:
        mode_file.write_text(recorded + "\n")
    endpoints = prov / "endpoints.env"
    endpoints.write_text(f"API_URL=http://{API_ADDRESS}:8080\n")  # left by an earlier run
    calls = tmp_path / "calls"
    steps = (
        "ensure_secrets start_postgres start_rabbitmq declare_producers setup_database "
        "write_files configure configure_replicas cluster_install wait_ready"
    ).split()
    stubs = [
        f'{step}() {{ echo "{step} $* mode=$(cat "$MODE_FILE") endpoints=$([ -f "$ENDPOINTS" ] '
        f'&& echo old || echo none)" >> "{calls}"; }}'
        for step in steps
    ]
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            f"kind_root='{tmp_path}'",
            "PY=/usr/bin/true",
            "HELM=/usr/bin/true",
            f"PROV='{prov}'",
            f"ENV_DIR='{prov}/env'",
            f"RUNS='{prov}/logs'",
            f"LOGS='{prov}/logs/latest'",
            f"PIDS='{prov}/pids'",
            f"MODE_FILE='{mode_file}'",
            f"REPLICAS_FILE='{prov}/replicas'",
            f"ENDPOINTS='{endpoints}'",
            f"KCFG_WORKER='{tmp_path}/worker.kubeconfig'",
            f"KCFG_BOOTSTRAP='{tmp_path}/bootstrap.kubeconfig'",
            "PROV_API_PORT=18080",
            "PROV_API2_PORT=18090",
            "REPLICATED=(api worker build_worker)",
            "cluster_exists() { :; }",
            "require_local_cluster() { :; }",
            "require_local_kubeconfig() { :; }",
            "running() { return 1; }",
            "process_names() { echo api; }",
            f'start_process() {{ echo "start_process $1 mode=$(cat "$MODE_FILE")" >> "{calls}"; }}',
            f'write_endpoints() {{ echo "write_endpoints $1" >> "{calls}"; '
            'echo "API_URL=http://127.0.0.1:18080" > "$ENDPOINTS"; }',
            "api_address() { printf 172.18.255.250; }",
            *stubs,
            function("run_mode"),
            function("start_mode"),
            function("refuse_stale_token_file"),
            function("cmd_start"),
            "cmd_start",
        ]
    )
    done = _bash(program, env)
    return done, calls.read_text().splitlines() if calls.exists() else [], mode_file


@pytest.mark.parametrize("recorded", [None, "host"])
def test_a_host_start_records_host_before_anything_starts(
    tmp_path: Path, recorded: str | None
) -> None:
    """PK-1 review: a host-mode start always writes ``host`` to provisioning/mode before its first
    step, and removes an earlier endpoints.env, so the harness never reads a mode or an address
    that this run did not set."""
    done, calls, mode_file = _start(tmp_path, recorded)
    assert done.returncode == 0, done.stderr
    assert mode_file.read_text() == "host\n"
    assert calls[0] == "ensure_secrets  mode=host endpoints=none"
    assert "start_process api mode=host" in calls
    assert "configure host mode=host endpoints=none" in calls
    assert "setup_database host mode=host endpoints=none" in calls
    assert calls[-1] == "write_endpoints host"
    assert not any(c.startswith("cluster_install") for c in calls)
    # Decision 0022 E6: the producers' exchanges before the processes, Payment's queue after.
    steps = [c.split()[0] for c in calls]
    assert steps.count("declare_producers") == 2
    assert steps.index("declare_producers") < steps.index("start_process")
    assert steps[-2] == "declare_producers"


def test_a_host_start_over_a_recorded_cluster_run_is_refused_before_anything_starts(
    tmp_path: Path,
) -> None:
    """A stale ``cluster`` never survives into a host run: the start stops before its first step
    and asks for make proto-stop, which removes the record (cmd_stop)."""
    done, calls, _ = _start(tmp_path, "cluster")
    assert done.returncode != 0
    assert "a cluster-mode run is recorded" in done.stderr
    assert "make proto-stop" in done.stderr
    assert calls == []
    assert '"$MODE_FILE"' in function("cmd_stop").split("rm -f", 1)[1]


def test_a_cluster_start_records_cluster_and_never_starts_a_host_process(tmp_path: Path) -> None:
    done, calls, mode_file = _start(tmp_path, None, {"PROV_MODE": "cluster"})
    assert done.returncode == 0, done.stderr
    assert mode_file.read_text() == "cluster\n"
    assert "cluster_install  mode=cluster endpoints=none" in calls
    assert calls[-1] == "write_endpoints cluster"
    assert not any(c.startswith(("start_process", "wait_ready")) for c in calls)


@pytest.mark.parametrize(
    "access",
    [
        {},
        {"PROJECT_ACCESS_MODE": "prototype-allow"},
        {"PROJECT_ACCESS_MODE": "project-service", "PROJECT_SERVICE_URL": "http://p.test:8085"},
    ],
    ids=["file", "allow", "project-service"],
)
def test_a_cluster_start_with_a_stale_service_token_file_stops_before_anything(
    tmp_path: Path, access: dict[str, str]
) -> None:
    """Merge of auth PR-3 (decision 0019): the call to the Project service carries no token, so the
    cluster mode neither passes nor mounts one. A PROJECT_SERVICE_TOKEN_FILE still set stops a
    cluster start first, whatever the access mode: no mode record, no step, no image, no Secret,
    and the message names the variable, never its value."""
    env = {"PROV_MODE": "cluster", "PROJECT_SERVICE_TOKEN_FILE": "/prov/stale-MARKER", **access}
    done, calls, mode_file = _start(tmp_path, None, env)
    assert done.returncode != 0
    assert "PROJECT_SERVICE_TOKEN_FILE is gone" in done.stderr
    assert "MARKER" not in done.stderr
    assert calls == []
    assert not mode_file.exists()


def test_the_cluster_mode_passes_no_service_token_to_any_process(tmp_path: Path) -> None:
    """No env file of the cluster mode, so neither the chart's values nor its Secrets, names a
    Project service token (decision 0019)."""
    env_dir = configure(tmp_path, "cluster")
    for path in sorted(env_dir.glob("*.env")):
        assert "PROJECT_SERVICE_TOKEN" not in path.read_text(), path.name
    assert "PROJECT_SERVICE_TOKEN" not in json.dumps(cluster_values.chart_values(env_dir))


# --- kill-process and standins-sync in the cluster ------------------------------------------------


POD = "provisioning-worker-5d9c7f8b6-x2kqp"
NODE = "webblock-proto-control-plane"


def _kill(
    tmp_path: Path,
    *,
    listing: str = f"{POD} {NODE} Running containerd://abc123",
    listing_fails: bool = False,
    pid: str = "4242",
    terminating_from: int = 2,
    node_prefix: str = "webblock-proto",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Runs cluster_kill_process worker with a stub ``k`` (the pod listing, then the pod's
    deletionTimestamp, present from poll ``terminating_from`` on) and a stub ``docker`` (crictl
    answers ``pid``); ``sleep`` returns at once."""
    calls = tmp_path / "calls"
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            "sleep() { :; }",
            "NS=webblock",
            "RELEASE=provisioning",
            "CLUSTER=webblock-proto",
            f"NODE_PREFIX={node_prefix}",
            f"CALLS='{calls}'",
            'k() { echo "k $*" >> "$CALLS"; case "$*" in '
            f'*"range .items"*) [ {int(listing_fails)} = 0 ] || return 1; '
            f'[ -f "$CALLS.listed" ] || {{ touch "$CALLS.listed"; '
            f"printf '%s\\n' '{listing}'; }} ;; "
            '*deletionTimestamp*) n=$(cat "$CALLS.polls" 2>/dev/null || echo 0); n=$((n + 1)); '
            'echo "$n" > "$CALLS.polls"; '
            f"if [ \"$n\" -ge {terminating_from} ]; then printf '{POD} 2026-10-15T09:00:00Z'; "
            f"else printf '{POD} '; fi ;; "
            "esac; }",
            'docker() { echo "docker $*" >> "$CALLS"; case "$*" in *crictl*) echo '
            f"{pid} ;; esac; }}",
            function("deployment_of"),
            function("selector_of"),
            function("wait_no_pods"),
            function("cluster_kill_process"),
            "cluster_kill_process worker",
        ]
    )
    done = _bash(program)
    return done, calls.read_text().splitlines() if calls.exists() else []


def _steps(calls: list[str]) -> list[str]:
    return [
        c
        for c in calls
        if "kill -" in c or "scale" in c or "crictl" in c or "deletionTimestamp" in c
    ]


def test_kill_freezes_scales_down_waits_for_the_deletion_then_kills(tmp_path: Path) -> None:
    """Crash semantics in a pod (PID 1 ignores a SIGKILL from inside its container): SIGSTOP from
    the Kind node, scale to 0, wait until the pod is terminating (else the kubelet would restart
    the killed container, and the new instance would stop gracefully), SIGKILL from the node,
    wait until no pod is left."""
    done, calls = _kill(tmp_path)
    assert done.returncode == 0, done.stderr
    poll = (
        f"k -n webblock get pod {POD} --ignore-not-found -o jsonpath="
        '{.metadata.name}{" "}{.metadata.deletionTimestamp}'
    )
    assert _steps(calls) == [
        f"docker exec {NODE} crictl inspect --output go-template "
        "--template {{.info.pid}} abc123",
        f"docker exec {NODE} kill -STOP 4242",
        "k -n webblock scale deployment/provisioning-worker --replicas=0",
        poll,
        poll,
        f"docker exec {NODE} kill -KILL 4242",
    ]
    assert calls[-1].startswith("k -n webblock get pods -l ")


def test_kill_refuses_a_node_outside_the_cluster(tmp_path: Path) -> None:
    done, calls = _kill(tmp_path, listing=f"{POD} someone-elses-node Running containerd://abc123")
    assert done.returncode != 0
    assert "not a node of webblock-proto" in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


def test_tr44_kill_on_k3d_freezes_from_the_k3d_node(tmp_path: Path) -> None:
    """TR-44 (spike G5): k3d names its nodes, and their containers, k3d-webblock-proto-*; the
    process is frozen and killed from that node as on Kind."""
    node = "k3d-webblock-proto-agent-0"
    done, calls = _kill(
        tmp_path,
        listing=f"{POD} {node} Running containerd://abc123",
        node_prefix="k3d-webblock-proto",
    )
    assert done.returncode == 0, done.stderr
    assert f"docker exec {node} kill -STOP 4242" in calls
    assert f"docker exec {node} kill -KILL 4242" in calls


def test_tr44_kill_on_k3d_refuses_a_kind_node(tmp_path: Path) -> None:
    """TR-44 (spike G5): on k3d a Kind node's name is not a node of the cluster."""
    done, calls = _kill(tmp_path, node_prefix="k3d-webblock-proto")
    assert done.returncode != 0
    assert "not a node of webblock-proto" in done.stderr
    assert not [c for c in calls if c.startswith("docker")]


def test_kill_refuses_when_the_pods_cannot_be_listed(tmp_path: Path) -> None:
    """A failed listing would freeze nothing, and the scale-down would then stop the worker
    gracefully: not a crash. The kill stops before it scales."""
    done, calls = _kill(tmp_path, listing_fails=True)
    assert done.returncode != 0
    assert "cannot list the pods of worker" in done.stderr
    assert _steps(calls) == []


def test_kill_refuses_a_running_pod_without_a_container_id(tmp_path: Path) -> None:
    done, calls = _kill(tmp_path, listing=f"{POD} {NODE} Running ")
    assert done.returncode != 0
    assert f"pod {POD} of worker runs but shows no container ID" in done.stderr
    assert _steps(calls) == []


def test_kill_skips_a_pending_pod_that_has_no_container_yet(tmp_path: Path) -> None:
    done, calls = _kill(tmp_path, listing=f"{POD} {NODE} Pending ")
    assert done.returncode == 0, done.stderr
    assert _steps(calls) == ["k -n webblock scale deployment/provisioning-worker --replicas=0"]


@pytest.mark.parametrize("pid", ["0", "1", "", "007", "pid"])
def test_kill_refuses_a_process_id_that_is_not_a_running_process(tmp_path: Path, pid: str) -> None:
    """crictl reports PID 0 for a container that is not running; ``kill -STOP 0`` would signal the
    exec's own process group, and ``kill -STOP 1`` would freeze the Kind node's init (the same
    guard as stop_follower's)."""
    done, calls = _kill(tmp_path, pid=pid)
    assert done.returncode != 0
    assert "no process ID for the container of worker" in done.stderr
    assert not [c for c in _steps(calls) if "kill -" in c or "scale" in c]


def test_kill_never_sends_sigkill_to_a_pod_that_is_not_terminating(tmp_path: Path) -> None:
    done, calls = _kill(tmp_path, terminating_from=10_000)
    assert done.returncode != 0
    assert f"pod {POD} of worker is not terminating" in done.stderr
    assert not [c for c in calls if "kill -KILL" in c]
    assert sum("deletionTimestamp" in c for c in calls) == 30


def _standins(
    tmp_path: Path, mode: str, matching_from: int, listing_fails: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path]:
    prov = tmp_path / "prov"
    prov.mkdir()
    names = ("access.json", "entitlement.json", "repositories.json")
    for name in names:
        (prov / name).write_text(f'{{"{name}": 1}}\n')
    want = "".join(hashlib.sha256((prov / n).read_bytes()).hexdigest() + " " for n in names)
    calls, count = tmp_path / "calls", tmp_path / "exec-count"
    stub = (
        f'k() {{ echo "k $*" >> "{calls}"; case "$*" in '
        '*"create configmap"*) echo \'{"kind": "ConfigMap"}\' ;; '
        "*apply*) cat > /dev/null ;; "
        f'*"get pods"*) [ {int(listing_fails)} = 0 ] || return 1 ;;& '
        '*"get pods"*component=api*) echo pod/provisioning-api-1 ;; '
        '*"get pods"*component=worker*) echo pod/provisioning-worker-1 ;; '
        '*"get pods"*) ;; '
        f'*exec*) n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "{count}"; '
        f"if [ \"$n\" -lt {matching_from} ]; then printf 'old old old '; "
        f"else printf '%s' '{want}'; fi ;; "
        "esac; }"
    )
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            "NS=webblock",
            "RELEASE=provisioning",
            f"PROV='{prov}'",
            f"REPOSITORIES='{prov}/repositories.json'",
            f"run_mode() {{ printf {mode}; }}",
            function("cluster_mode"),
            stub,
            function("k_apply"),
            function("standins_apply"),
            function("standins_seen"),
            function("cmd_standins_sync"),
            "cmd_standins_sync",
        ]
    )
    return _bash(program), calls


def test_standins_sync_waits_for_every_mounting_pod(tmp_path: Path) -> None:
    """Decision 0016 section 9 ruling 3: the ConfigMap is applied from the host files, then each
    running pod that mounts it is asked until it reads the same bytes (here the API's third
    answer; then the worker's first)."""
    done, calls = _standins(tmp_path, "cluster", matching_from=3)
    assert done.returncode == 0, done.stderr
    lines = calls.read_text().splitlines()
    first_exec = next(i for i, c in enumerate(lines) if " exec " in c)
    assert any("create configmap provisioning-standins" in c for c in lines[:first_exec])
    assert any("apply --server-side" in c for c in lines[:first_exec])
    execs = [c for c in lines if " exec " in c]
    assert [c.split(" exec ")[1].split()[0] for c in execs] == [
        "pod/provisioning-api-1", "pod/provisioning-api-1", "pod/provisioning-api-1",
        "pod/provisioning-worker-1",
    ]  # fmt: skip


def test_standins_sync_does_nothing_on_the_host(tmp_path: Path) -> None:
    done, calls = _standins(tmp_path, "host", matching_from=1)
    assert done.returncode == 0, done.stderr
    assert not calls.exists()


def test_standins_sync_fails_when_the_pods_cannot_be_listed(tmp_path: Path) -> None:
    """A failed listing is not "no pod to wait for": the sync would pass while no pod reads the
    new files."""
    done, _ = _standins(tmp_path, "cluster", matching_from=1, listing_fails=True)
    assert done.returncode != 0
    assert "cannot list the api pods" in done.stderr


# --- the mode guard of every other subcommand -----------------------------------------------------


def _guarded(
    tmp_path: Path, recorded: str, command: str
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Runs one subcommand's handler with the real run_mode and cluster_mode against a recorded
    mode; every step either mode would take only records its name."""
    prov = tmp_path / "prov"
    prov.mkdir()
    mode_file = prov / "mode"
    mode_file.write_text(recorded)
    for name in ("access.json", "entitlement.json", "repositories.json"):
        (prov / name).write_text("{}\n")
    calls = tmp_path / "calls"
    steps = (
        "cluster_stop stop_processes cluster_kill_process kill_process standins_apply docker k h"
    ).split()
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            f"PROV='{prov}'",
            f"MODE_FILE='{mode_file}'",
            f"ENV_DIR='{prov}/env'",
            f"PIDS='{prov}/pids'",
            f"PROV_ENV='{prov}/provisioning.env'",
            f"TOKENS='{prov}/tokens.json'",
            f"REPOSITORIES='{prov}/repositories.json'",
            f"REPLICAS_FILE='{prov}/replicas'",
            f"ENDPOINTS='{prov}/endpoints.env'",
            f"LOGS='{prov}/logs/latest'",
            "PROV_PG_NAME=webblock-provisioning-db",
            "PROV_MQ_NAME=webblock-rabbitmq",
            "NS=webblock",
            "RELEASE=provisioning",
            "usage() { exit 2; }",
            *(f'{s}() {{ echo "{s} $*" >> "{calls}"; }}' for s in steps),
            function("run_mode"),
            function("cluster_mode"),
            function("valid_process"),
            function("cmd_stop"),
            function("cmd_kill_process"),
            function("cmd_standins_sync"),
            command,
        ]
    )
    done = _bash(program)
    return done, calls.read_text().splitlines() if calls.exists() else [], mode_file


COMMANDS = {
    "stop": "cmd_stop",
    "kill-process": "cmd_kill_process worker",
    "standins-sync": "cmd_standins_sync",
}


@pytest.mark.parametrize("recorded", ["clusterx\n", "", "\n"])
@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_garbled_mode_record_stops_every_subcommand_before_it_acts(
    tmp_path: Path, command: str, recorded: str
) -> None:
    """PK-3 review, Important 1: a damaged record (an empty file after a full disk) must not make a
    cluster run act as a host run, which would leave the release and its Secrets behind, turn a
    kill into a no-op, or skip a stand-in sync. Each subcommand refuses, takes no step, and keeps
    the record."""
    done, calls, mode_file = _guarded(tmp_path, recorded, COMMANDS[command])
    assert done.returncode != 0
    assert "holds neither host nor cluster" in done.stderr
    assert calls == []
    assert mode_file.read_text() == recorded


@pytest.mark.parametrize(
    ("recorded", "command", "first"),
    [
        ("cluster\n", "stop", "cluster_stop "),
        ("host\n", "stop", "stop_processes "),
        ("cluster\n", "kill-process", "cluster_kill_process worker"),
        ("host\n", "kill-process", "kill_process worker"),
        ("cluster\n", "standins-sync", "standins_apply "),
    ],
)
def test_a_valid_mode_record_picks_the_matching_step(
    tmp_path: Path, recorded: str, command: str, first: str
) -> None:
    done, calls, _ = _guarded(tmp_path, recorded, COMMANDS[command])
    assert done.returncode == 0, done.stderr
    assert calls[0] == first


def test_standins_sync_on_a_host_record_takes_no_step(tmp_path: Path) -> None:
    done, calls, _ = _guarded(tmp_path, "host\n", COMMANDS["standins-sync"])
    assert done.returncode == 0, done.stderr
    assert calls == []


# --- the install's server-side dry run ------------------------------------------------------------


def test_the_dry_run_checks_the_chart_in_its_namespace_before_the_install(tmp_path: Path) -> None:
    """PK-3 review, Important 2: without -n the objects were dry-run in namespace default, where
    PodSecurity restricted does not apply. --force-conflicts lets a re-run after a code change
    pass although Helm and kubectl scale own the live fields; a dry run takes no ownership."""
    calls = tmp_path / "calls"
    prov = tmp_path / "prov"
    prov.mkdir()
    steps = "push_image write_stack_values standins_apply start_follower cluster_diagnose".split()
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            f"kind_root='{tmp_path}'",
            f"PROV='{prov}'",
            f"ENV_DIR='{prov}/env'",
            f"TOKENS='{prov}/tokens.json'",
            f"PROTO='{tmp_path}'",
            "NS=webblock",
            "RELEASE=provisioning",
            "CHART=/chart",
            "python3() { echo '{}'; }",
            *(f'{s}() {{ echo "{s}" >> "{calls}"; }}' for s in steps),
            f'k() {{ echo "k $*" >> "{calls}"; case "$*" in *"-f -"*) cat > /dev/null ;; esac; }}',
            f'h() {{ echo "h $*" >> "{calls}"; '
            'case "$1" in template) echo "kind: ConfigMap" ;; esac; }',
            function("k_apply"),
            function("cluster_install"),
            "cluster_install",
        ]
    )
    done = _bash(program)
    assert done.returncode == 0, done.stderr
    lines = calls.read_text().splitlines()
    (dry,) = [c for c in lines if "--dry-run=server" in c]
    assert dry.startswith("k -n webblock apply --server-side --dry-run=server ")
    for flag in ("--force-conflicts", "--warnings-as-errors", "--field-manager=webblock-proto-run"):
        assert flag in dry.split(), flag
    (upgrade,) = [c for c in lines if c.startswith("h upgrade --install ")]
    assert lines.index(dry) < lines.index(upgrade)
    assert lines.index(dry) < lines.index("start_follower")
    comment = TEXT[: TEXT.index("cluster_install() {")].rsplit("\n\n", 1)[1]
    flat = " ".join(comment.replace("#", " ").split())
    assert "in webblock" in flat and "PodSecurity restricted" in flat
    assert "--force-conflicts" in flat


# --- the log follower's stop ----------------------------------------------------------------------


def _stop_follower(
    tmp_path: Path, content: str | None, *, spoofed: bool = False
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    """Runs stop_follower with a stub ``kill`` that only records, so a wrong process ID can never
    signal anything real."""
    pid_file = tmp_path / "log-follower.pid"
    if content is not None:
        pid_file.write_text(content)
    calls = tmp_path / "calls"
    program = "\n".join(
        [
            "set -euo pipefail",
            "log() { :; }",
            f"FOLLOWER_PID='{pid_file}'",
            f'kill() {{ echo "kill $*" >> "{calls}"; }}',
            # Every process looks like the follower: only the process-ID check is left.
            "tr() { cat > /dev/null; echo follow_logs.py; }" if spoofed else ":",
            function("stop_follower"),
            "stop_follower",
        ]
    )
    done = _bash(program)
    return done, calls.read_text().splitlines() if calls.exists() else [], pid_file


@pytest.mark.parametrize("content", ["1\n", "0\n", "-1\n", "abc\n", "12 34\n", "\n"])
def test_stop_follower_never_signals_a_pid_file_that_is_not_a_process_id(
    tmp_path: Path, content: str
) -> None:
    """``kill -TERM -- -1`` would signal every process of the user."""
    done, calls, pid_file = _stop_follower(tmp_path, content)
    assert done.returncode == 0, done.stderr
    assert calls == []
    assert not pid_file.exists()


def test_stop_follower_refuses_pid_1_even_when_it_looks_like_the_follower(tmp_path: Path) -> None:
    """The process-ID check holds on its own: with the identity check spoofed, a pid file holding
    1 still signals nothing."""
    done, calls, _ = _stop_follower(tmp_path, "1\n", spoofed=True)
    assert done.returncode == 0, done.stderr
    assert calls == []


def test_stop_follower_without_a_pid_file_does_nothing(tmp_path: Path) -> None:
    done, calls, _ = _stop_follower(tmp_path, None)
    assert done.returncode == 0, done.stderr
    assert calls == []


def test_stop_follower_is_quiet_when_the_follower_has_already_ended(tmp_path: Path) -> None:
    """A pid file whose process is gone: nothing is signalled and nothing reaches standard error
    (the redirection of standard error comes before the read of /proc/<pid>/cmdline)."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])  # this interpreter
    process.wait(timeout=10)
    done, calls, pid_file = _stop_follower(tmp_path, f"{process.pid}\n")
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    assert calls == []
    assert not pid_file.exists()


@pytest.mark.parametrize("follower", [True, False])
def test_stop_follower_signals_only_the_follower_itself(tmp_path: Path, follower: bool) -> None:
    """A process ID can be reused after the follower ended: the group is signalled only while
    /proc/<pid>/cmdline still names follow_logs.py."""
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    if follower:
        argv.append("scripts/prototype/follow_logs.py")
    process = subprocess.Popen(argv)  # noqa: S603  # fixed argument list: this interpreter
    try:
        done, calls, _ = _stop_follower(tmp_path, f"{process.pid}\n")
    finally:
        process.kill()
        process.wait(timeout=10)
    assert done.returncode == 0, done.stderr
    assert calls == ([f"kill -TERM -- -{process.pid}"] if follower else [])


# --- the cluster stop -----------------------------------------------------------------------------


def _array(name: str) -> str:
    """The script's line ``NAME=(...)``, which may continue over several lines."""
    match = re.search(rf"^{name}=\(.*?\)$", TEXT, re.MULTILINE | re.DOTALL)
    assert match is not None, name
    return match.group(0)


def _cluster_stop(
    tmp_path: Path, exists: bool, engine: str = "kind"
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls"
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            f"ENGINE={engine}",
            "NS=webblock",
            "RELEASE=provisioning",
            _array("CLUSTER_SECRETS"),
            _array("CLUSTER_CONFIGMAPS"),
            f'stop_follower() {{ echo "stop_follower" >> "{calls}"; }}',
            f"cluster_exists() {{ {'true' if exists else 'false'}; }}",
            "require_local_cluster() { :; }",
            f'k() {{ echo "k $*" >> "{calls}"; }}',
            f'h() {{ echo "h $*" >> "{calls}"; }}',
            function("cluster_stop"),
            "cluster_stop",
        ]
    )
    done = _bash(program)
    return done, calls.read_text().splitlines() if calls.exists() else []


def test_the_cluster_stop_removes_the_release_and_what_helm_leaves(tmp_path: Path) -> None:
    """PK-2b review: the migration Job's CiliumNetworkPolicy provisioning-migrate is a pre-install
    hook, which helm uninstall never deletes; the stop deletes it after the uninstall, with the
    Job, the Secrets and the ConfigMaps the run script made."""
    done, calls = _cluster_stop(tmp_path, exists=True)
    assert done.returncode == 0, done.stderr
    assert calls[0] == "stop_follower"
    uninstall = calls.index("h uninstall provisioning --namespace webblock --wait --timeout 5m")
    after = calls[uninstall + 1 :]
    assert (
        "k -n webblock delete ciliumnetworkpolicy provisioning-migrate --ignore-not-found" in after
    )
    assert "k -n webblock delete job provisioning-migrate --ignore-not-found" in after
    assert (
        "k -n webblock delete secret provisioning-api-secret provisioning-worker-secret "
        "provisioning-build-worker-secret provisioning-bootstrap-secret "
        "provisioning-migrate-secret provisioning-dev-tokens --ignore-not-found"
    ) in after
    assert (
        "k -n webblock delete configmap provisioning-standins provisioning-gateway-ca "
        "--ignore-not-found"
    ) in after


def test_tr44_the_cluster_stop_on_k3d_deletes_no_cilium_object(tmp_path: Path) -> None:
    """TR-44 (spike G5): k3s has no CiliumNetworkPolicy type, so on k3d the stop deletes a
    NetworkPolicy provisioning-migrate instead, with everything else as on Kind."""
    done, calls = _cluster_stop(tmp_path, exists=True, engine="k3d")
    assert done.returncode == 0, done.stderr
    assert not [c for c in calls if "cilium" in c]
    assert "k -n webblock delete networkpolicy provisioning-migrate --ignore-not-found" in calls
    assert "k -n webblock delete job provisioning-migrate --ignore-not-found" in calls


def test_the_cluster_stop_without_a_cluster_only_stops_the_follower(tmp_path: Path) -> None:
    done, calls = _cluster_stop(tmp_path, exists=False)
    assert done.returncode == 0, done.stderr
    assert calls == ["stop_follower"]


def test_the_stop_refuses_a_cluster_that_fails_the_local_kubeconfig_check(tmp_path: Path) -> None:
    """CLAUDE.md section 0: a cluster whose kubeconfig is not the local Kind one is not touched,
    and the stop says so instead of reporting success; the mode record stays, so the release and
    its Secrets are not forgotten."""
    prov = tmp_path / "prov"
    prov.mkdir()
    mode_file = prov / "mode"
    mode_file.write_text("cluster\n")
    calls = tmp_path / "calls"
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            "NS=webblock",
            "RELEASE=provisioning",
            _array("CLUSTER_SECRETS"),
            _array("CLUSTER_CONFIGMAPS"),
            f"PROV='{prov}'",
            f"MODE_FILE='{mode_file}'",
            f"ENV_DIR='{prov}/env'",
            f"PIDS='{prov}/pids'",
            f"PROV_ENV='{prov}/provisioning.env'",
            f"TOKENS='{prov}/tokens.json'",
            f"REPOSITORIES='{prov}/repositories.json'",
            f"REPLICAS_FILE='{prov}/replicas'",
            f"ENDPOINTS='{prov}/endpoints.env'",
            f"LOGS='{prov}/logs/latest'",
            "PROV_PG_NAME=webblock-provisioning-db",
            "PROV_MQ_NAME=webblock-rabbitmq",
            "ENGINE=kind",
            f'stop_follower() {{ echo "stop_follower" >> "{calls}"; }}',
            "cluster_exists() { true; }",
            'require_local_cluster() { die "kubeconfig: context points elsewhere; refusing"; }',
            *(f'{s}() {{ echo "{s} $*" >> "{calls}"; }}' for s in ("k", "h", "docker")),
            function("run_mode"),
            function("cluster_mode"),
            function("cluster_stop"),
            function("cmd_stop"),
            "cmd_stop",
        ]
    )
    done = _bash(program)
    assert done.returncode != 0
    assert "refusing" in done.stderr
    assert calls.read_text().splitlines() == ["stop_follower"]
    assert mode_file.read_text() == "cluster\n"


def test_every_hook_of_the_chart_is_deleted_by_the_cluster_stop() -> None:
    """Each object the chart renders as a Helm hook (which helm uninstall leaves behind) is named
    in cluster_stop's deletes."""
    templates = ROOT / "deploy" / "helm" / "provisioning" / "templates"
    hooks: list[tuple[str, str]] = []
    for path in sorted(templates.glob("*.yaml")):
        for part in re.split(r"^---$", path.read_text(), flags=re.MULTILINE):
            if "helm.sh/hook:" in part:
                kind = re.search(r"^kind: (\S+)$", part, re.MULTILINE)
                name = re.search(r"^  name: (\S+)$", part, re.MULTILINE)
                assert kind and name, path
                hooks.append((kind.group(1).lower(), name.group(1)))
    assert sorted(hooks) == [
        ("ciliumnetworkpolicy", "provisioning-migrate"),
        ("job", "provisioning-migrate"),
    ]
    stop = function("cluster_stop")
    for hook_kind, hook_name in hooks:
        assert f"delete {hook_kind} {hook_name} --ignore-not-found" in stop, hook_kind


def _stack_values(tmp_path: Path, engine: str) -> dict[str, object]:
    """write_stack_values with stub addresses; returns values-stack.json."""
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            f"ENGINE={engine}",
            f"PROV='{tmp_path}'",
            "PROV_IMAGE=webblock-proto-registry.test:5000/webblock/platform/provisioning@sha256:"
            + "0" * 64,
            "PROV_PG_NAME=pg PROV_MQ_NAME=mq PROTO_JENKINS_NAME=jenkins PROTO_CATALOG_NAME=catalog",
            "api_address() { printf 172.18.0.3; }",
            'container_ip() { case "$1" in pg) printf 172.18.0.11;; mq) printf 172.18.0.12;; '
            "jenkins) printf 172.18.0.13;; catalog) printf 172.18.0.14;; esac; }",
            function("write_stack_values"),
            "write_stack_values",
        ]
    )
    done = _bash(program)
    assert done.returncode == 0, done.stderr
    values: dict[str, object] = json.loads((tmp_path / "values-stack.json").read_text())
    return values


def test_tr44_the_stack_values_pin_the_pods_to_the_kind_control_plane_with_a_fixed_address(
    tmp_path: Path,
) -> None:
    """TR-44 (spike G5): Kind labels its control plane with an empty value and gives the API
    Service its address from Cilium's LB-IPAM pool."""
    values = _stack_values(tmp_path, "kind")
    assert values["nodeSelector"] == {"node-role.kubernetes.io/control-plane": ""}
    assert values["api"] == {
        "service": {"type": "LoadBalancer", "annotations": {"lbipam.cilium.io/ips": "172.18.0.3"}}
    }


def test_tr44_on_k3d_the_stack_values_use_k3s_labels_and_no_cilium_annotation(
    tmp_path: Path,
) -> None:
    """TR-44 (spike G5): k3s labels its server node control-plane=true, and ServiceLB serves the
    API Service on the node addresses, so no LB-IPAM annotation is written."""
    values = _stack_values(tmp_path, "k3d")
    assert values["nodeSelector"] == {"node-role.kubernetes.io/control-plane": "true"}
    assert values["tolerations"] == [
        {
            "key": "node-role.kubernetes.io/control-plane",
            "operator": "Exists",
            "effect": "NoSchedule",
        }
    ]
    assert values["api"] == {"service": {"type": "LoadBalancer", "annotations": {}}}
    assert "cilium" not in (tmp_path / "values-stack.json").read_text()


# --- make, the stack and the runbook --------------------------------------------------------------


def _proto_e2e(tmp_path: Path, recorded: str | None) -> list[str]:
    """Runs the proto-e2e recipe of mk/prototype.mk with a fake interpreter that prints its
    arguments, from a folder that holds (or not) the recorded mode."""
    make = (ROOT / "mk" / "prototype.mk").read_text()
    block = make[make.index("proto-e2e: ") :].split("\n\n", 1)[0]
    recipe = block.split("\n", 1)[1]
    fake = tmp_path / "py" / "python"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    mode = tmp_path / "bin" / "kind-state" / "proto" / "provisioning" / "mode"
    mode.parent.mkdir(parents=True)
    if recorded is not None:
        mode.write_text(recorded + "\n")
    (tmp_path / "Makefile").write_text(
        f"py_prelude :=\nPY_BIN := {fake.parent}\nproto-e2e:\n{recipe}\n"
    )
    done = subprocess.run(  # noqa: S603  # fixed argument list: make and a folder of this test
        ["/usr/bin/make", "-s", "-C", str(tmp_path), "proto-e2e"],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


@pytest.mark.parametrize("recorded", [None, "host"])
def test_proto_e2e_deselects_the_incluster_folder_in_host_mode(
    tmp_path: Path, recorded: str | None
) -> None:
    out = _proto_e2e(tmp_path, recorded)
    assert out[0].startswith("proto-e2e: host mode: tests/e2e/incluster deselected")
    assert out[1:] == [
        "-X", "dev", "-m", "pytest", "-m", "e2e", "tests/e2e", "--ignore=tests/e2e/demo",
        "--ignore=tests/e2e/replicas", "--ignore=tests/e2e/incluster",
    ]  # fmt: skip


def test_proto_e2e_runs_the_incluster_folder_in_cluster_mode(tmp_path: Path) -> None:
    out = _proto_e2e(tmp_path, "cluster")
    assert out == [
        "-X", "dev", "-m", "pytest", "-m", "e2e", "tests/e2e", "--ignore=tests/e2e/demo",
        "--ignore=tests/e2e/replicas",
    ]  # fmt: skip


def test_proto_e2e_reads_the_run_scripts_record_and_proto_run_passes_the_mode_through() -> None:
    make = (ROOT / "mk" / "prototype.mk").read_text()
    recipe = make[make.index("proto-e2e: ") :].split("\n\n", 1)[0]
    assert "bin/kind-state/proto/provisioning/mode" in recipe
    assert "PROV_MODE" not in make[make.index("proto-run:\n") :].split("\n\n", 1)[0]


def test_proto_down_stops_provisioning_before_it_deletes_the_cluster() -> None:
    down = (ROOT / "deploy" / "kind" / "proto-down.sh").read_text()
    assert down.index('run-provisioning.sh" stop') < down.index('delete cluster --name "$CLUSTER"')


def test_the_kubelets_resync_configmaps_every_5_seconds() -> None:
    cluster = yaml.safe_load((ROOT / "deploy" / "kind" / "cluster.yaml").read_text())
    (patch,) = cluster["kubeadmConfigPatches"]
    assert yaml.safe_load(patch) == {
        "kind": "KubeletConfiguration",
        "syncFrequency": "5s",
        "containerLogMaxSize": "100Mi",
    }


def test_the_runbook_says_the_base_images_date_is_read_again_for_a_new_digest() -> None:
    """PK-2a review: make py-age trusts BASE_IMAGE_PUBLISHED, and nothing ties that date to the
    digest, so moving the base image must re-read the publication date of the new digest."""
    runbook = (ROOT / ".claude" / "docs" / "runbooks" / "prototype-in-cluster.md").read_text()
    part = runbook[runbook.index("**Moving the base image.**") :].split("\n- ", 1)[0]
    flat = " ".join(part.split())
    assert "BASE_IMAGE_PUBLISHED" in flat
    assert "make py-age" in flat
    assert "read again for the new digest" in flat
    assert "never kept from the old pin" in flat
    versions = (ROOT / "deploy" / "image" / "versions.sh").read_text()
    assert '.claude/docs/runbooks/prototype-in-cluster.md ("Moving the base image")' in " ".join(
        line.lstrip("# ") for line in versions.splitlines()
    )


# --- the import order of the unit suite -----------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "tests.unit.scripts._configure",
        "tests.unit.scripts.test_cluster_mode",
        "tests.unit.scripts.test_follow_logs",
        "tests.unit.scripts.test_run_provisioning",
    ],
)
def test_the_script_tests_load_no_kubernetes_client_at_import(module: str) -> None:
    """PK-1 review: tests/unit/infrastructure/kubernetes/test_client.py checks the library's
    default kubeconfig path, which is fixed when kubernetes_asyncio is first imported in the test
    process. A unit test module that imports a host which loads the library (hosts.shared.cluster,
    hosts.worker, hosts.bootstrap) would fix that path first, depending on the collection order.
    Importing each of these modules in a fresh interpreter loads none of them."""
    probe = (
        "import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        "banned = ('kubernetes_asyncio', 'provisioning.hosts.shared.cluster',"
        " 'provisioning.hosts.worker', 'provisioning.hosts.bootstrap',"
        " 'provisioning.infrastructure.kubernetes')\n"
        "loaded = (m for m in sys.modules if m.split('.')[0] == banned[0] or m in banned)\n"
        "print(','.join(sorted(loaded)))\n"
    )
    done = subprocess.run(  # noqa: S603  # fixed argument list: this interpreter and a fixed probe
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(ROOT / "never-a-home"),
            "KUBECONFIG": "/nonexistent/never-a-kubeconfig",
        },
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
