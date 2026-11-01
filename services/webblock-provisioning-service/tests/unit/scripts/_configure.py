"""Runs the run script's configure() in a clean bash with stubs and temporary files only (no stack,
no container, no cluster), so that tests can load the settings each process gets on the host and in
the cluster with that process's own loader (task PK). This module holds no test."""

from __future__ import annotations

import re
import secrets
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prototype" / "run-provisioning.sh"
TEXT = SCRIPT.read_text()
API_ADDRESS = "172.18.255.250"
PG_PORT = "40001"
MQ_PORT = "40002"
PASSWORDS = ("PG_SUPERUSER", "API", "BUILD", "WORKER", "HOSTING_INTAKE", "INTAKE", "BOOTSTRAP",
             "OPERATOR", "MQ", "TOKEN", "MIGRATOR")  # fmt: skip
HOST_JENKINS_URL = "http://127.0.0.1:8090"


def function(name: str) -> str:
    """The text of the shell function ``name``: ``name() {`` to its closing brace, or one line."""
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n", TEXT, re.MULTILINE | re.DOTALL
    ) or re.search(rf"^{re.escape(name)}\(\) \{{.*\}}$", TEXT, re.MULTILINE)
    assert match is not None, name
    return match.group(0)


def read_env(path: Path) -> dict[str, str]:
    """An env file the script wrote: KEY='value' per line."""
    values = {}
    for line in path.read_text().splitlines():
        key, _, value = line.partition("=")
        assert len(value) >= 2 and value[0] == value[-1] == "'", key
        values[key] = value[1:-1]
    return values


def _write(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))


def configure(tmp_path: Path, mode: str, *, replicas: bool = False) -> Path:
    """Runs ``configure <mode>`` against fake stack files, then (with ``replicas``, host mode only)
    ``configure_replicas``, then ``write_endpoints <mode>``, and returns the folder of env files.
    ``endpoints.env`` is written beside that folder, as the run script writes it."""
    proto = tmp_path / "proto"
    prov = proto / "provisioning"
    env_dir = prov / "env"
    env_dir.mkdir(parents=True)
    (proto / "local-ca.crt").write_text("not a certificate\n")
    _write(
        proto / "stack.env",
        {
            "PROTO_GATEWAY_HTTPS_URL": "https://172.18.255.200:443",
            "PROTO_IMAGE_REGISTRY": "webblock-proto-registry.test:5000",
            "PROTO_OFFLINE_SERVICE": "webblock-offline/offline-page:8080",
            "PROTO_CATALOG_IN_CLUSTER": "webblock-catalog-db:5432",
            "PROTO_CATALOG_HOST_ON_HOST": "127.0.0.1",
            "PROTO_CATALOG_PORT_ON_HOST": "55432",
            "PROTO_CATALOG_DATABASE": "catalog",
            "PROTO_SITE_HOST_SUFFIX": "sites.webblock.test",
            "PROTO_JENKINS_JOB": "webblock-site-build",
        },
    )
    _write(
        proto / "jenkins.env",
        {
            "JENKINS_URL": HOST_JENKINS_URL,
            "JENKINS_USER": "provisioning",
            "JENKINS_API_TOKEN": secrets.token_hex(17),
        },
    )
    _write(
        proto / "catalog-admin.env",
        {
            "CATALOG_ACCESS_ADMIN_USER": "catalog_access_admin",
            "CATALOG_ACCESS_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        },
    )
    tokens = ("PREVIEW_TOKEN", "TENANT_TOKEN", "READER_TOKEN")
    _write(
        prov / "provisioning.env",
        {f"{name}_PASSWORD": secrets.token_urlsafe(32) for name in PASSWORDS}
        | {name: secrets.token_urlsafe(32) for name in tokens},
    )
    (prov / "replicas").write_text("2\n" if replicas else "1\n")
    ports = re.findall(r'^PROV_[A-Z0-9_]*PORT="\$\{[A-Z0-9_]+:-[0-9]+\}"$', TEXT, re.MULTILINE)
    program = "\n".join(
        [
            "set -euo pipefail",
            'die() { echo "die: $*" >&2; exit 1; }',
            "log() { :; }",
            *ports,
            f"PROTO='{proto}'",
            f"PROV='{prov}'",
            f"ENV_DIR='{env_dir}'",
            f"STACK_ENV='{proto}/stack.env'",
            f"JENKINS_ENV='{proto}/jenkins.env'",
            f"CATALOG_ENV='{proto}/catalog-admin.env'",
            f"PROV_ENV='{prov}/provisioning.env'",
            f"TOKENS='{prov}/tokens.json'",
            f"REPOSITORIES='{prov}/repositories.json'",
            f"REPLICAS_FILE='{prov}/replicas'",
            f"ENDPOINTS='{prov}/endpoints.env'",
            f"KCFG_WORKER='{proto}/worker.kubeconfig'",
            f"KCFG_BOOTSTRAP='{proto}/bootstrap.kubeconfig'",
            "PROV_PG_NAME=webblock-provisioning-db",
            "PROV_MQ_NAME=webblock-rabbitmq",
            "PROTO_JENKINS_NAME=webblock-jenkins",
            f'container_port() {{ case "$2" in 5432) echo {PG_PORT} ;; 5672) echo {MQ_PORT} ;; '
            "*) exit 1 ;; esac; }",
            f"api_address() {{ printf {API_ADDRESS}; }}",
            function("get_var"),
            function("q"),
            function("write_env"),
            function("derive_env"),
            function("replica_count"),
            function("refuse_stale_token_file"),
            function("access_settings"),
            function("configure"),
            function("configure_replicas"),
            function("write_endpoints"),
            f"configure {mode}",
            "configure_replicas" if replicas else ":",
            f"write_endpoints {mode}",
        ]
    )
    done = subprocess.run(  # noqa: S603  # fixed argument list; the program is the script's own text
        ["/usr/bin/bash", "--noprofile", "--norc", "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return env_dir
