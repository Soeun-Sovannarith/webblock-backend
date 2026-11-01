"""The API process's entry point (decision 0013 section 10.8): with a missing or invalid setting it
writes every refused key with its reason, never a value, and exits 2; a broken prototype file exits
1 without the file's contents.

Task H2: without PROVISIONING_PROFILE the process refuses to start, and an environment that still
carries the build worker's keys (the Jenkins token, the provisioning_build login) is refused key by
key. Task H3: production starts with the JWT identity; with the issuer unreachable it serves and
answers 503 identity_unavailable, and the development tokens are refused there by name."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.unit.infrastructure.config.test_api import JWT, PRODUCTION_ACTION_SCOPES

from provisioning.core.application import Action
from provisioning.infrastructure.config import api_keys

MARKER = "VALUE-MARKER-3e9d"


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # fixed argument list: the test interpreter and the module
        [sys.executable, "-m", "provisioning.hosts.api"],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _full_env(tmp: Path) -> dict[str, str]:
    tokens = tmp / "tokens.json"
    tokens.write_text(
        json.dumps(
            {
                "tokens": [
                    {"token": "t" * 40, "subject": "alice", "scopes": ["prov.all"]},
                ]
            }
        )
    )
    return {
        "PROVISIONING_PROFILE": "prototype",
        "COMMAND_DEADLINE": "15m",
        "HTTP_LISTEN_ADDR": "127.0.0.1:18080",
        "OPS_LISTEN_ADDR": "127.0.0.1:18081",
        "SENTRY_DSN": "none",
        "HTTP_REQUEST_TIMEOUT": "10s",
        "HTTP_IDLE_TIMEOUT": "60s",
        "HTTP_SHUTDOWN_GRACE": "5s",
        "HTTP_MAX_HEADER_BYTES": "16384",
        "HTTP_MAX_REQUEST_BODY_BYTES": "1024",
        "HTTP_MAX_IN_FLIGHT": "64",
        "MCP_MAX_REQUEST_BODY_BYTES": "65536",
        "MCP_ALLOWED_ORIGINS": "none",
        "MCP_ALLOWED_HOSTS": "127.0.0.1:*",
        "IDENTITY_MODE": "dev-tokens",
        "AUTH_DEV_TOKENS_FILE": str(tokens),
        "AUTH_ACTION_SCOPES": ",".join(f"{a.value}=prov.all" for a in Action),
        "PROJECT_ACCESS_MODE": "prototype-allow",
        "ENTITLEMENT_MODE": "prototype-allow",
        "PG_HOST": "localhost",
        "PG_PORT": "5432",
        "PG_DATABASE": "provisioning",
        "PG_SSLMODE": "disable",
        "PG_USER": "api_login",
        "PG_PASS": MARKER,
        "DB_MAX_CONNS": "4",
        "DB_CONNECT_TIMEOUT": "5s",
        "DB_TX_TIMEOUT": "10s",
        "DB_STATEMENT_TIMEOUT": "5s",
        "DB_LOCK_TIMEOUT": "2s",
    }


def test_an_empty_environment_exits_2_naming_every_key() -> None:
    result = _run({})
    assert result.returncode == 2, result.stderr
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    keys = api_keys()
    assert (
        lines[0]
        == f"provisioning-api: configuration refused; {len(keys)} key(s) missing or invalid:"
    )
    assert lines[1:] == [f"  {key}: is required" for key in keys]
    assert "Traceback" not in result.stderr


def test_an_invalid_value_exits_2_without_the_value(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    env["PG_PORT"] = "port-" + MARKER
    env["IDENTITY_MODE"] = "jwt-" + MARKER
    result = _run(env)
    assert result.returncode == 2
    assert "  IDENTITY_MODE: must be one of dev-tokens, jwt" in result.stderr
    assert "  PG_PORT: must be a decimal integer" in result.stderr
    assert MARKER not in result.stderr


def test_a_broken_token_file_exits_1_without_its_contents(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    Path(env["AUTH_DEV_TOKENS_FILE"]).write_text('{"tokens": [{"token": "' + MARKER + '"}]}')
    result = _run(env)
    assert result.returncode == 1
    record = json.loads(result.stderr.splitlines()[-1])
    assert record["msg"] == "api could not be built"
    assert "dev token file" in record["error"]
    assert MARKER not in result.stderr


def test_an_environment_with_the_build_workers_keys_exits_2_naming_each(tmp_path: Path) -> None:
    env = _full_env(tmp_path) | {
        "JENKINS_API_TOKEN": "token-" + MARKER,
        "PG_BUILD_USER": "build_login",
        "PG_BUILD_PASS": "pass-" + MARKER,
        "PROTOTYPE_REPOSITORIES_FILE": str(tmp_path / "repositories.json"),
    }
    result = _run(env)
    assert result.returncode == 2, result.stderr
    reason = (
        "must be unset: only the build worker starts builds and holds this setting "
        "(decision 0016 section 5 item 2)"
    )
    for key in (
        "JENKINS_API_TOKEN",
        "PG_BUILD_PASS",
        "PG_BUILD_USER",
        "PROTOTYPE_REPOSITORIES_FILE",
    ):
        assert f"  {key}: {reason}" in result.stderr
    assert MARKER not in result.stderr


def test_without_a_profile_the_process_refuses_to_start(tmp_path: Path) -> None:
    env = {k: v for k, v in _full_env(tmp_path).items() if k != "PROVISIONING_PROFILE"}
    result = _run(env)
    assert result.returncode == 2, result.stderr
    assert result.stderr.splitlines()[1:] == ["  PROVISIONING_PROFILE: is required"]


def test_production_refuses_the_development_tokens_and_every_stand_in(tmp_path: Path) -> None:
    env = _full_env(tmp_path) | {"PROVISIONING_PROFILE": "production"}
    result = _run(env)
    assert result.returncode == 2, result.stderr
    lines = result.stderr.splitlines()[1:]
    identity = (
        "  IDENTITY_MODE: must be jwt in production: dev-tokens is a prototype stand-in (OD-05)"
    )
    assert identity in lines
    for key in ("AUTH_DEV_TOKENS_FILE", "ENTITLEMENT_MODE"):
        assert any(line.startswith(f"  {key}: must be unset in production: ") for line in lines)
    # The one production mode is project-service (authentication design A3).
    assert any(
        line.startswith(
            "  PROJECT_ACCESS_MODE: must be project-service, project-grpc or unset in production: "
        )
        for line in lines
    )
    for key in JWT:
        assert f"  {key}: is required" in lines
    assert MARKER not in result.stderr


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def test_with_the_database_down_the_process_serves_and_reports_not_ready(tmp_path: Path) -> None:
    # Task G wired B3's stores: a complete configuration starts the process; connecting is lazy,
    # so a database that refuses connections makes /readyz answer 503, and SIGTERM stops it.
    env = _full_env(tmp_path)
    public, ops, database = _free_port(), _free_port(), _free_port()
    env |= {
        "HTTP_LISTEN_ADDR": f"127.0.0.1:{public}",
        "OPS_LISTEN_ADDR": f"127.0.0.1:{ops}",
        "PG_PORT": str(database),
        "DB_CONNECT_TIMEOUT": "1s",
    }
    process = subprocess.Popen(  # fixed argument list: the test interpreter and the module
        [sys.executable, "-m", "provisioning.hosts.api"],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        status = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and status is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{ops}/readyz", timeout=5) as r:
                    status = r.status
            except urllib.error.HTTPError as err:
                status = err.code
                err.close()
            except OSError:
                time.sleep(0.2)
        assert status == 503
    finally:
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert MARKER not in stderr


def _production_env(tmp_path: Path, public: int, ops: int, jwks: int) -> dict[str, str]:
    env = _full_env(tmp_path)
    for key in ("AUTH_DEV_TOKENS_FILE", "PROJECT_ACCESS_MODE", "ENTITLEMENT_MODE"):
        del env[key]
    return (
        env
        | JWT
        | {
            "PROVISIONING_PROFILE": "production",
            "IDENTITY_MODE": "jwt",
            "AUTH_ACTION_SCOPES": PRODUCTION_ACTION_SCOPES,  # FR-50: admin-only actions to ADMIN
            # Decision 0024: the key set comes from the issuer's discovery document; nothing
            # listens on this port.
            "AUTH_ISSUER": f"https://127.0.0.1:{jwks}/realms/webblock",
            "HTTP_LISTEN_ADDR": f"127.0.0.1:{public}",
            "OPS_LISTEN_ADDR": f"127.0.0.1:{ops}",
        }
    )


def _get(url: str, token: str) -> tuple[int, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310  # fixed http://127.0.0.1 URL
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310  # same URL
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        body = json.loads(err.read())
        err.close()
        return err.code, body


def test_production_starts_with_jwt_and_answers_503_while_the_issuer_is_down(
    tmp_path: Path,
) -> None:
    """FR-1.3: with IDENTITY_MODE=jwt the production API starts (no fetch at start-up). Nothing
    listens on the issuer's port, so a well-formed token is 503 identity_unavailable, never 401; a
    malformed one is 401; and SIGTERM stops it. The library's failed fetch is logged through the
    process's logger with its cause (decision 0024 G8)."""
    public, ops, jwks = _free_port(), _free_port(), _free_port()
    env = _production_env(tmp_path, public, ops, jwks)
    process = subprocess.Popen(  # fixed argument list: the test interpreter and the module
        [sys.executable, "-m", "provisioning.hosts.api"],
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    header = "eyJhbGciOiJSUzI1NiIsImtpZCI6InJzYS0xIn0"  # {"alg":"RS256","kid":"rsa-1"}
    token = f"{header}.e30.c2ln"
    url = f"http://127.0.0.1:{public}/v1/projects/0190a1b2-0000-7000-8000-00000000000a/deployment"
    try:
        deadline = time.monotonic() + 30
        answered: tuple[int, dict[str, str]] | None = None
        while time.monotonic() < deadline and answered is None:
            try:
                answered = _get(url, token)
            except OSError:
                time.sleep(0.2)
        assert answered is not None
        status, body = answered
        assert (status, body["code"]) == (503, "IDENTITY_UNAVAILABLE")
        status, body = _get(url, "not-a-jwt")
        assert (status, body["code"]) == (401, "UNAUTHENTICATED")
        assert process.poll() is None
    finally:
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, stderr
    assert MARKER not in stderr
    assert token not in stderr
    assert "identity keys unavailable" in stderr
    failed = [json.loads(line) for line in stderr.splitlines() if "jwks fetch failed" in line]
    assert failed
    assert {(r["component"], r["auth_cause"]) for r in failed} == {("identity", "jwks_transport")}
