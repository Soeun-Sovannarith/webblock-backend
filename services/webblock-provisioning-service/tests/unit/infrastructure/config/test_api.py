"""The API process's configuration (with the prototype's identity and stand-in keys of
decision 0014).

Task H2: the required PROVISIONING_PROFILE (prototype or production, no default); the build/API
split of decision 0016 section 5 item 2, so the API refuses every build, Jenkins and repository
key when it is set; and the production profile, which refuses every stand-in key by name. Task H3:
production accepts the JWT identity (``test_identity.py``).

Every key is required and has no default (OD-15); LOG_LEVEL alone is optional. All problems are
collected; a reason names keys and bounds, never a value (review-port-arch.md M8). The HTTP
server's read and write timeouts are not keys here: the gateway enforces slow-client limits
(decision 0013 section 9, A3).
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta
from pathlib import Path

import pytest

from provisioning.core.application import Action
from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config import (
    APIConfig,
    LoadError,
    Secret,
    api_keys,
    load_api,
)

API_DB_MARK = "api-pass-MARKER-a1"
BUILD_DB_MARK = "build-pass-MARKER-b2"
JENKINS_MARK = "jenkins-token-MARKER-c3"
SECRETS = (API_DB_MARK,)

ACTION_SCOPES = ",".join(f"{a.value}=prov.{a.value}" for a in Action)

ENV: dict[str, str] = {
    "PROVISIONING_PROFILE": "prototype",
    "COMMAND_DEADLINE": "15m",
    "HTTP_LISTEN_ADDR": "127.0.0.1:8080",
    "OPS_LISTEN_ADDR": "127.0.0.1:8081",
    "SENTRY_DSN": "none",
    "HTTP_REQUEST_TIMEOUT": "10s",
    "HTTP_IDLE_TIMEOUT": "60s",
    "HTTP_SHUTDOWN_GRACE": "20s",
    "HTTP_MAX_HEADER_BYTES": "16384",
    "HTTP_MAX_REQUEST_BODY_BYTES": "1024",
    "HTTP_MAX_IN_FLIGHT": "64",
    "MCP_MAX_REQUEST_BODY_BYTES": "65536",
    "MCP_ALLOWED_ORIGINS": "none",
    "MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:8080",
    "IDENTITY_MODE": "dev-tokens",
    "AUTH_DEV_TOKENS_FILE": "/etc/webblock/dev-tokens.json",
    "AUTH_ACTION_SCOPES": ACTION_SCOPES,
    "PROJECT_ACCESS_MODE": "prototype-allow",
    "ENTITLEMENT_MODE": "prototype-allow",
    "PG_HOST": "localhost",
    "PG_PORT": "5432",
    "PG_DATABASE": "provisioning",
    "PG_SSLMODE": "disable",
    "PG_USER": "prov_api_login",
    "PG_PASS": API_DB_MARK,
    "DB_MAX_CONNS": "8",
    "DB_CONNECT_TIMEOUT": "5s",
    "DB_TX_TIMEOUT": "10s",
    "DB_STATEMENT_TIMEOUT": "5s",
    "DB_LOCK_TIMEOUT": "2s",
}


# The build worker's keys, which the API process refuses since the build/API split (decision 0016
# section 5 item 2): only the build worker holds the Jenkins token and the provisioning_build login.
BUILD_ONLY: dict[str, str] = {
    "PG_BUILD_USER": "prov_build_login",
    "PG_BUILD_PASS": BUILD_DB_MARK,
    "REPOSITORY_MODE": "prototype-file",
    "PROTOTYPE_REPOSITORIES_FILE": "/etc/webblock/repositories.json",
    "BUILD_IMAGE_REGISTRY": "localhost:5001",
    "BUILD_DURATION": "20m",
    "BUILD_CALL_TIMEOUT": "15s",
    "BUILD_MAX_ATTEMPTS": "3",
    "JENKINS_URL": "http://localhost:8090/jenkins",
    "JENKINS_USER": "provisioning",
    "JENKINS_API_TOKEN": JENKINS_MARK,
    "JENKINS_JOB": "webblock/site-build",
    "JENKINS_REQUEST_TIMEOUT": "5s",
    "JENKINS_OPERATION_TIMEOUT": "10s",
    "JENKINS_QUEUE_POLLS": "5",
    "JENKINS_QUEUE_POLL_INTERVAL": "500ms",
    "JENKINS_RECENT_BUILDS": "50",
}

# The JWT verifier's keys (decision 0006 section 7, decision 0024 G7), required with
# IDENTITY_MODE=jwt.
JWT: dict[str, str] = {
    "AUTH_ISSUER": "https://issuer.example.test/realms/webblock",
    "AUTH_AUDIENCE": "provisioning",
    "AUTH_MAX_TOKEN_LIFETIME": "15m",
}

# Production maps each admin-only action to ADMIN (FR-50, decision 0023 G10).
PRODUCTION_ACTION_SCOPES = ",".join(
    f"{a.value}={'ADMIN' if a.is_admin_only() else 'prov.' + a.value}" for a in Action
)

# The production profile (task H2) with every stand-in key left out, and the JWT identity of task
# H3, the only identity production accepts.
_STAND_IN_KEYS = {
    "PROVISIONING_PROFILE",
    "AUTH_DEV_TOKENS_FILE",
    "PROJECT_ACCESS_MODE",
    "ENTITLEMENT_MODE",
}
PRODUCTION = (
    {k: v for k, v in ENV.items() if k not in _STAND_IN_KEYS}
    | JWT
    | {
        "PROVISIONING_PROFILE": "production",
        "IDENTITY_MODE": "jwt",
        "AUTH_ACTION_SCOPES": PRODUCTION_ACTION_SCOPES,
    }
)


def _with(**changes: str | None) -> dict[str, str]:
    env = dict(ENV)
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def _load(env: dict[str, str]) -> APIConfig:
    return load_api(env.get)


def _refused(env: dict[str, str]) -> LoadError:
    with pytest.raises(LoadError) as exc_info:
        _load(env)
    return exc_info.value


def test_fixture_names_exactly_the_required_keys() -> None:
    assert api_keys() == tuple(sorted(ENV))


def test_load_reads_every_value() -> None:
    c = _load(ENV)
    assert c.log_level == logging.INFO
    assert c.command_deadline == timedelta(minutes=15)
    h = c.http
    assert (h.listen_addr, h.ops_listen_addr) == ("127.0.0.1:8080", "127.0.0.1:8081")
    assert h.request_timeout == timedelta(seconds=10)
    assert h.idle_timeout == timedelta(seconds=60)
    assert h.shutdown_grace == timedelta(seconds=20)
    assert (h.max_header_bytes, h.max_request_body_bytes, h.max_in_flight) == (16384, 1024, 64)
    assert c.mcp.max_request_body_bytes == 65536
    assert c.mcp.allowed_origins == ()
    assert c.mcp.allowed_hosts == ("127.0.0.1:*", "localhost:8080")
    assert c.identity.mode == "dev-tokens"
    assert c.identity.dev_tokens_file == Path("/etc/webblock/dev-tokens.json")
    assert c.identity.action_scopes == {a: f"prov.{a.value}" for a in Action}
    assert c.standins.project_access == "prototype-allow"
    assert c.standins.entitlement == "prototype-allow"
    assert c.profile is Profile.PROTOTYPE
    d = c.database
    assert (d.host, d.port, d.name, d.ssl_mode, d.max_conns) == (
        "localhost",
        5432,
        "provisioning",
        "disable",
        8,
    )
    assert (d.connect_timeout, d.tx_timeout, d.statement_timeout, d.lock_timeout) == (
        timedelta(seconds=5),
        timedelta(seconds=10),
        timedelta(seconds=5),
        timedelta(seconds=2),
    )
    assert c.api_login.user == "prov_api_login"
    assert c.api_login.password.reveal() == API_DB_MARK


@pytest.mark.parametrize(
    ("value", "level"),
    [("debug", logging.DEBUG), ("INFO", logging.INFO), ("warn", logging.WARNING), ("error", 40)],
)
def test_log_level_is_optional_and_read_without_case(value: str, level: int) -> None:
    assert _load(_with(LOG_LEVEL=value)).log_level == level
    assert _load(_with(LOG_LEVEL="")).log_level == logging.INFO


def test_an_empty_environment_names_every_key_at_once() -> None:
    err = _refused({})
    assert err.keys() == tuple(sorted(ENV))
    assert all(p.reason == "is required" for p in err.problems)


@pytest.mark.parametrize("key", sorted(ENV))
def test_each_missing_key_is_reported_alone_by_name(key: str) -> None:
    for value in (None, ""):
        err = _refused(_with(**{key: value}))
        assert err.keys() == (key,), value


# Each case sets one key to a value that must be refused: the error names that key alone and never
# contains the value.
_INVALID: list[tuple[str, str]] = [
    ("LOG_LEVEL", "verbose"),
    ("COMMAND_DEADLINE", "500ms"),
    ("COMMAND_DEADLINE", "31d"),
    ("COMMAND_DEADLINE", "721h"),
    ("HTTP_LISTEN_ADDR", "localhost"),
    ("HTTP_LISTEN_ADDR", "localhost:0"),
    ("HTTP_LISTEN_ADDR", "localhost:+80"),
    ("HTTP_LISTEN_ADDR", "local host:80"),
    ("HTTP_LISTEN_ADDR", "localhost:65536"),
    ("OPS_LISTEN_ADDR", "http://x:1"),
    ("HTTP_REQUEST_TIMEOUT", "5ms"),
    ("HTTP_REQUEST_TIMEOUT", "2h"),
    ("HTTP_IDLE_TIMEOUT", "fast"),
    ("HTTP_SHUTDOWN_GRACE", "11m"),
    ("HTTP_MAX_HEADER_BYTES", "1023"),
    ("HTTP_MAX_HEADER_BYTES", "1048577"),
    ("HTTP_MAX_REQUEST_BODY_BYTES", "0"),
    ("HTTP_MAX_REQUEST_BODY_BYTES", "67108865"),
    ("HTTP_MAX_IN_FLIGHT", "0"),
    ("MCP_MAX_REQUEST_BODY_BYTES", "0"),
    ("MCP_MAX_REQUEST_BODY_BYTES", "-1"),
    ("MCP_ALLOWED_ORIGINS", "https://a.example,"),
    ("MCP_ALLOWED_ORIGINS", "https://a.example/path"),
    ("MCP_ALLOWED_ORIGINS", "ftp://a.example"),
    ("MCP_ALLOWED_ORIGINS", "https://user@a.example"),
    ("MCP_ALLOWED_ORIGINS", "*"),
    ("MCP_ALLOWED_HOSTS", "a.example:"),
    ("MCP_ALLOWED_HOSTS", "a.example,,b.example"),
    ("MCP_ALLOWED_HOSTS", "http://a.example"),
    ("MCP_ALLOWED_HOSTS", "a.example:99999"),
    ("MCP_ALLOWED_HOSTS", "*"),
    ("IDENTITY_MODE", "oidc"),
    ("IDENTITY_MODE", "Dev-Tokens"),
    ("IDENTITY_MODE", "JWT"),
    ("AUTH_DEV_TOKENS_FILE", "relative/tokens.json"),
    ("AUTH_DEV_TOKENS_FILE", "/etc/x\x00y"),
    ("AUTH_ACTION_SCOPES", "deploy_release=prov.deploy"),
    ("AUTH_ACTION_SCOPES", ACTION_SCOPES + ",create_release=x"),
    ("AUTH_ACTION_SCOPES", ACTION_SCOPES + ",deploy_release=again"),
    ("AUTH_ACTION_SCOPES", ACTION_SCOPES.replace("=prov.get_release", "=")),
    ("AUTH_ACTION_SCOPES", ACTION_SCOPES.replace("=prov.get_release", "=a b")),
    ("AUTH_ACTION_SCOPES", ACTION_SCOPES + ","),
    ("AUTH_ACTION_SCOPES", "deploy_release"),
    ("PROJECT_ACCESS_MODE", "permit-all"),
    ("ENTITLEMENT_MODE", "permit-all"),
    ("PG_HOST", "bad host!"),
    ("PG_PORT", "0"),
    ("PG_PORT", "5432a"),
    ("PG_DATABASE", " prov"),
    ("PG_DATABASE", "x" * 64),
    ("PG_SSLMODE", "prefer"),
    ("PG_SSLMODE", "allow"),
    ("PG_USER", "x" * 64),
    ("PG_PASS", "line\nbreak-MARKER-d4"),
    ("DB_MAX_CONNS", "0"),
    ("DB_MAX_CONNS", "501"),
    ("DB_CONNECT_TIMEOUT", "6m"),
    ("DB_LOCK_TIMEOUT", "2m"),
]


@pytest.mark.parametrize(("key", "value"), _INVALID)
def test_invalid_values_are_refused_by_key_and_never_echoed(key: str, value: str) -> None:
    err = _refused(_with(**{key: value}))
    assert err.keys() == (key,)
    text = str(err) + repr(err)
    if len(value) >= 4:
        assert value not in text
    for token in ("MARKER", "pw-", "verbose", "relative", "create_release=x"):
        if token in value:
            assert token not in text


def test_several_invalid_values_are_reported_together() -> None:
    err = _refused(_with(PG_PORT="0", HTTP_MAX_IN_FLIGHT="0", IDENTITY_MODE="oidc"))
    assert err.keys() == ("HTTP_MAX_IN_FLIGHT", "IDENTITY_MODE", "PG_PORT")


# Accepted neighbours of the refused values above: the rules refuse the value, not the key.
_BOUNDARY: list[tuple[str, str]] = [
    ("COMMAND_DEADLINE", "1s"),
    ("COMMAND_DEADLINE", "720h"),
    ("HTTP_LISTEN_ADDR", ":8080"),
    ("HTTP_LISTEN_ADDR", "[::1]:8080"),
    ("HTTP_LISTEN_ADDR", "0.0.0.0:65535"),
    ("HTTP_REQUEST_TIMEOUT", "10ms"),
    ("HTTP_MAX_HEADER_BYTES", "1024"),
    ("HTTP_MAX_REQUEST_BODY_BYTES", "67108864"),
    ("MCP_ALLOWED_ORIGINS", "https://a.example,http://localhost:3000"),
    ("MCP_ALLOWED_HOSTS", "a.example"),
    ("MCP_ALLOWED_HOSTS", "[::1]:*,10.0.0.1:443"),
    ("PG_SSLMODE", "verify-full"),
    ("PG_HOST", "::1"),
    ("PG_PASS", "p@ss w:rd/?#%"),
    ("DB_MAX_CONNS", "500"),
]


@pytest.mark.parametrize(("key", "value"), _BOUNDARY)
def test_boundary_values_are_accepted(key: str, value: str) -> None:
    _load(_with(**{key: value}))


@pytest.mark.parametrize(
    ("changes", "key", "reason"),
    [
        (
            {"HTTP_LISTEN_ADDR": ":8080", "OPS_LISTEN_ADDR": "127.0.0.1:8080"},
            "OPS_LISTEN_ADDR",
            "must not bind the same address as HTTP_LISTEN_ADDR",
        ),
        (
            {"HTTP_LISTEN_ADDR": "localhost:9000", "OPS_LISTEN_ADDR": "LOCALHOST:9000"},
            "OPS_LISTEN_ADDR",
            "must not bind the same address as HTTP_LISTEN_ADDR",
        ),
        (
            {"HTTP_LISTEN_ADDR": "[::]:9000", "OPS_LISTEN_ADDR": "10.0.0.1:9000"},
            "OPS_LISTEN_ADDR",
            "must not bind the same address as HTTP_LISTEN_ADDR",
        ),
        (
            {"DB_STATEMENT_TIMEOUT": "11s"},
            "DB_STATEMENT_TIMEOUT",
            "must not exceed DB_TX_TIMEOUT",
        ),
        (
            {"DB_LOCK_TIMEOUT": "6s"},
            "DB_LOCK_TIMEOUT",
            "must not exceed DB_STATEMENT_TIMEOUT",
        ),
    ],
)
def test_cross_checks(changes: dict[str, str], key: str, reason: str) -> None:
    err = _refused(_with(**changes))
    assert [(p.key, p.reason) for p in err.problems] == [(key, reason)]


def test_listeners_on_different_ports_or_hosts_do_not_collide() -> None:
    _load(_with(HTTP_LISTEN_ADDR="127.0.0.1:9000", OPS_LISTEN_ADDR="127.0.0.2:9000"))
    _load(_with(HTTP_LISTEN_ADDR=":9000", OPS_LISTEN_ADDR=":9001"))


def test_a_cross_check_runs_only_on_values_that_were_read() -> None:
    err = _refused(_with(DB_TX_TIMEOUT="nonsense", DB_STATEMENT_TIMEOUT="11s"))
    assert err.keys() == ("DB_TX_TIMEOUT",)


def test_no_secret_appears_in_errors_or_in_the_printed_configuration() -> None:
    err = _refused(_with(PG_PORT="0"))
    for secret in SECRETS:
        assert secret not in str(err)
    c = _load(ENV)
    for text in (repr(c), str(c), f"{c}"):
        for secret in SECRETS:
            assert secret not in text
        assert "[REDACTED]" in text


# A test value full of characters a URL must escape.
_URL_SPECIALS = "p@ss w:rd/?#%"


def test_the_database_dsn_escapes_the_credentials_and_is_a_secret() -> None:
    c = _load(_with(PG_USER="api user", PG_PASS=_URL_SPECIALS, PG_HOST="::1"))
    dsn = c.database.dsn(c.api_login)
    assert isinstance(dsn, Secret)
    assert "p@ss" not in str(dsn)
    assert dsn.reveal() == (
        "postgresql://api%20user:p%40ss%20w%3Ard%2F%3F%23%25@[::1]:5432/provisioning"
        "?sslmode=disable&connect_timeout=5"
    )


def test_the_dsn_rounds_a_sub_second_connect_timeout_up() -> None:
    c = _load(_with(DB_CONNECT_TIMEOUT="300ms"))
    assert c.database.dsn(c.api_login).reveal().endswith("connect_timeout=1")
    c = _load(_with(DB_CONNECT_TIMEOUT="1500ms"))
    assert c.database.dsn(c.api_login).reveal().endswith("connect_timeout=2")


def test_the_stand_ins_default_to_allow_without_files() -> None:
    c = _load(ENV)
    assert (c.standins.project_access_file, c.standins.entitlement_file) == (None, None)


def test_the_file_stand_ins_read_their_files() -> None:
    c = _load(
        _with(
            PROJECT_ACCESS_MODE="prototype-file",
            PROJECT_ACCESS_FILE="/etc/webblock/access.json",
            ENTITLEMENT_MODE="prototype-file",
            ENTITLEMENT_FILE="/etc/webblock/entitlement.json",
        )
    )
    assert c.standins.project_access == "prototype-file"
    assert c.standins.project_access_file == Path("/etc/webblock/access.json")
    assert c.standins.entitlement == "prototype-file"
    assert c.standins.entitlement_file == Path("/etc/webblock/entitlement.json")


@pytest.mark.parametrize(
    ("mode_key", "file_key"),
    [
        ("PROJECT_ACCESS_MODE", "PROJECT_ACCESS_FILE"),
        ("ENTITLEMENT_MODE", "ENTITLEMENT_FILE"),
    ],
)
def test_a_stand_in_file_is_required_in_file_mode_and_refused_otherwise(
    mode_key: str, file_key: str
) -> None:
    assert _refused(_with(**{mode_key: "prototype-file"})).keys() == (file_key,)
    relative = _with(**{mode_key: "prototype-file", file_key: "relative/" + JENKINS_MARK})
    err = _refused(relative)
    assert err.keys() == (file_key,)
    assert JENKINS_MARK not in str(err)
    assert _refused(_with(**{file_key: "/etc/webblock/x.json"})).keys() == (file_key,)


def test_load_error_is_deterministic_and_sorted() -> None:
    texts = {str(_refused({"PG_PORT": "x", "COMMAND_DEADLINE": "y"})) for _ in range(5)}
    assert len(texts) == 1
    err = _refused({"PG_PORT": "x", "COMMAND_DEADLINE": "y"})
    assert list(err.keys()) == sorted(err.keys())


# --- task H2 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["Prototype", "PRODUCTION", "dev", "staging", " prototype"])
def test_the_profile_is_exactly_prototype_or_production(value: str) -> None:
    err = _refused(_with(PROVISIONING_PROFILE=value))
    assert err.keys() == ("PROVISIONING_PROFILE",)
    assert err.problems[0].reason == "must be one of prototype, production"


def test_the_profile_has_no_default() -> None:
    for value in (None, ""):
        err = _refused(_with(PROVISIONING_PROFILE=value))
        assert [(p.key, p.reason) for p in err.problems] == [
            ("PROVISIONING_PROFILE", "is required")
        ]


@pytest.mark.parametrize("profile", ["prototype", "production"])
@pytest.mark.parametrize("key", sorted(BUILD_ONLY))
def test_the_api_refuses_every_build_key_by_name(profile: str, key: str) -> None:
    base = ENV if profile == "prototype" else PRODUCTION
    err = _refused({**base, key: BUILD_ONLY[key]})
    problem = next(p for p in err.problems if p.key == key)
    assert problem.reason == (
        "must be unset: only the build worker starts builds and holds this setting "
        "(decision 0016 section 5 item 2)"
    )
    for secret in (BUILD_DB_MARK, JENKINS_MARK):
        assert secret not in str(err)


def test_the_whole_former_build_section_is_refused_together() -> None:
    err = _refused({**ENV, **BUILD_ONLY})
    assert err.keys() == tuple(sorted(BUILD_ONLY))


def test_production_loads_with_the_jwt_identity_and_no_stand_in() -> None:
    """Task H3: production has an identity, so the API starts; mutations stay refused by
    ``OpenContract`` and project access stays closed (``hosts.shared.open_contracts``)."""
    c = _load(PRODUCTION)
    assert c.profile is Profile.PRODUCTION
    assert (c.identity.mode, c.identity.dev_tokens_file) == ("jwt", None)
    assert (c.standins.project_access, c.standins.entitlement) == ("", "")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AUTH_DEV_TOKENS_FILE", "/etc/webblock/dev-tokens.json"),
        ("PROJECT_ACCESS_MODE", "prototype-allow"),
        ("PROJECT_ACCESS_MODE", "prototype-file"),
        ("PROJECT_ACCESS_FILE", "/etc/webblock/access.json"),
        ("ENTITLEMENT_MODE", "prototype-allow"),
        ("ENTITLEMENT_MODE", "prototype-file"),
        ("ENTITLEMENT_FILE", "/etc/webblock/entitlement.json"),
    ],
)
def test_production_refuses_every_stand_in_key_by_name(key: str, value: str) -> None:
    err = _refused({**PRODUCTION, key: value})
    problem = next(p for p in err.problems if p.key == key)
    accepted = "project-service, project-grpc or unset" if key == "PROJECT_ACCESS_MODE" else "unset"
    assert problem.reason.startswith(f"must be {accepted} in production: ")
    assert value not in str(err)


def test_without_a_profile_the_stand_in_keys_are_read_as_in_the_prototype() -> None:
    """A missing profile is one problem; the other keys are still checked, so one run names
    everything."""
    err = _refused(_with(PROVISIONING_PROFILE=None, PROJECT_ACCESS_MODE="permit-all"))
    assert err.keys() == ("PROJECT_ACCESS_MODE", "PROVISIONING_PROFILE")


# --- task PA: PROJECT_ACCESS_MODE=project-service (decision 0017 section 2) --------------------

URL = "PROJECT_SERVICE_URL"
TOKEN_FILE = "PROJECT_SERVICE_TOKEN_FILE"  # noqa: S105  # a key name, not a secret

SERVICE = {
    "PROJECT_ACCESS_MODE": "project-service",
    "PROJECT_SERVICE_URL": "http://127.0.0.1:8085",
}


def test_the_project_service_mode_reads_only_its_url() -> None:
    """No token (authentication design A3, owner, 18 October 2026): the URL is the whole section."""
    c = _load({**ENV, **SERVICE})
    assert c.standins.project_access == "project-service"
    assert c.standins.project_access_file is None
    assert c.project_service is not None
    assert c.project_service.url == "http://127.0.0.1:8085"
    assert [f.name for f in dataclasses.fields(c.project_service)] == ["url"]


def test_the_stand_in_modes_have_no_project_service_settings() -> None:
    assert _load(ENV).project_service is None


def test_the_project_service_url_is_required_in_its_mode() -> None:
    for env in (_with(PROJECT_ACCESS_MODE="project-service"), {**ENV, **SERVICE, URL: ""}):
        err = _refused(env)
        assert [(p.key, p.reason) for p in err.problems] == [(URL, "is required")]


@pytest.mark.parametrize("mode", ["prototype-allow", "prototype-file"])
def test_the_project_service_url_is_refused_in_another_mode(mode: str) -> None:
    env = _with(PROJECT_ACCESS_MODE=mode, **{URL: SERVICE[URL]})
    if mode == "prototype-file":
        env["PROJECT_ACCESS_FILE"] = "/etc/webblock/access.json"
    err = _refused(env)
    assert [(p.key, p.reason) for p in err.problems] == [
        (URL, "must be unset unless PROJECT_ACCESS_MODE is project-service")
    ]


def test_the_project_service_mode_refuses_the_access_file() -> None:
    err = _refused({**ENV, **SERVICE, "PROJECT_ACCESS_FILE": "/etc/webblock/access.json"})
    assert err.keys() == ("PROJECT_ACCESS_FILE",)


@pytest.mark.parametrize(
    "value",
    [
        "ftp://project.test",
        "project.test:8085",
        "/internal",
        "http://",
        "http://user:" + API_DB_MARK + "@project.test",
        "http://project.test/?token=" + API_DB_MARK,
        "http://project.test/#" + API_DB_MARK,
        "http://project.test/ " + API_DB_MARK,
        "http://project.test:0",
        "http://project.test:99999",
        "http://project.test/\x07" + API_DB_MARK,
        "https://" + "a" * 2048 + ".test",
    ],
)
def test_a_bad_project_service_url_is_refused_without_its_value(value: str) -> None:
    err = _refused({**ENV, **SERVICE, "PROJECT_SERVICE_URL": value})
    assert err.keys() == ("PROJECT_SERVICE_URL",)
    assert API_DB_MARK not in str(err)


@pytest.mark.parametrize(
    "value",
    [
        "http://project-service.test",
        "https://project.test:8443/project",
        "http://[::1]:8085/",
        "http://127.0.0.1:8085/project/",
    ],
)
def test_good_project_service_urls_are_accepted(value: str) -> None:
    c = _load({**ENV, **SERVICE, "PROJECT_SERVICE_URL": value})
    assert c.project_service is not None
    assert c.project_service.url == value


@pytest.mark.parametrize(
    "env",
    [
        {**ENV, **SERVICE},
        ENV,
        {**ENV, "PROJECT_ACCESS_MODE": "prototype-file", "PROJECT_ACCESS_FILE": "/etc/a.json"},
        PRODUCTION,
    ],
    ids=["project-service", "prototype-allow", "prototype-file", "production"],
)
def test_the_removed_token_file_key_is_refused_everywhere_without_its_value(
    env: dict[str, str],
) -> None:
    """The service token is gone (authentication design A3); a stale environment that still
    names its file is caught at start-up instead of silently ignored."""
    err = _refused({**env, TOKEN_FILE: "/etc/webblock/token-" + API_DB_MARK})
    assert [(p.key, p.reason) for p in err.problems] == [
        (TOKEN_FILE, "must be unset: calls to the Project service carry no token")
    ]
    assert API_DB_MARK not in str(err)


# Production's project-service section: a plain http URL only to an in-cluster Service name
# (finding M-2 of the PR-3 review; the sidecar adds mTLS).
IN_CLUSTER = {
    "PROJECT_ACCESS_MODE": "project-service",
    "PROJECT_SERVICE_URL": "http://project.project.svc.cluster.local:8080",
}


def test_production_accepts_the_project_service_mode() -> None:
    """Authentication design A3 (owner, 18 October 2026): the call needs no token, so nothing
    is left to decide before production may ask the Project service."""
    c = _load({**PRODUCTION, **IN_CLUSTER})
    assert c.profile is Profile.PRODUCTION
    assert c.standins.project_access == "project-service"
    assert c.standins.project_access_file is None
    assert c.project_service is not None
    assert c.project_service.url == "http://project.project.svc.cluster.local:8080"


@pytest.mark.parametrize(
    "value",
    [
        "http://project.project.svc",
        "http://project.project.svc.cluster.local:8080",
        "HTTP://Project.Project.SVC.Cluster.Local.:8080/",
        "http://project.project.svc./base",
        "https://project.example.com",
        "https://project.example.com:8443/project/",
        "https://10.0.0.5:8443",
        "https://project",
        # Re-review R-1: names that only start like numbers, or hold hex letters, are DNS names.
        "https://1password.example.com",
        "https://0xcafe.example.com",
        "http://123.project.svc",
        "https://deadbeef",
        # Re-review R-1b: an IDN host is accepted in its ASCII (punycode) form only.
        "https://xn--bcher-kva.example",
    ],
)
def test_production_accepts_https_or_an_in_cluster_http_service(value: str) -> None:
    c = _load({**PRODUCTION, **IN_CLUSTER, URL: value})
    assert c.project_service is not None
    assert c.project_service.url == value


PLAIN = (
    "must be https in production unless its host is an in-cluster Service (.svc or "
    ".svc.cluster.local)"
)
PLACEHOLDER = (
    "must name the Project service's host in production, not a loopback or placeholder host"
)
NON_ASCII = "must name its host in production in ASCII (an internationalised name in its xn-- form)"
NUMERIC = (
    "must name its host in production by a DNS name, a canonical IPv4 address or an IPv6 "
    "address without a zone ID"
)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("http://project.example.com", PLAIN),
        ("http://project", PLAIN),
        ("http://project.project", PLAIN),
        ("http://project.svc.cluster.local.example.com", PLAIN),
        ("http://project.svcx", PLAIN),
        ("http://10.0.0.5:8080", PLAIN),
        ("http://[fd00::5]:8080", PLAIN),
        ("https://localhost", PLACEHOLDER),
        ("https://LOCALHOST.", PLACEHOLDER),
        ("https://project.localhost", PLACEHOLDER),
        ("https://127.0.0.1", PLACEHOLDER),
        ("https://127.1.2.3:8443", PLACEHOLDER),
        ("https://[::1]", PLACEHOLDER),
        ("https://[::ffff:127.0.0.1]", PLACEHOLDER),
        # Re-review R-1 (amended spec section 5.4): unspecified, numeric and zoned forms, and
        # the local names, for any scheme.
        ("https://0.0.0.0", PLACEHOLDER),
        ("http://0.0.0.0:8080", PLACEHOLDER),
        ("https://[::]", PLACEHOLDER),
        ("https://[::ffff:0.0.0.0]", PLACEHOLDER),
        ("https://ip6-localhost", PLACEHOLDER),
        ("https://IP6-Localhost.", PLACEHOLDER),
        ("https://ip6-loopback", PLACEHOLDER),
        ("https://localhost.localdomain", PLACEHOLDER),
        ("http://localhost.localdomain:8080", PLACEHOLDER),
        ("https://127.1", NUMERIC),
        ("https://2130706433", NUMERIC),
        ("https://0x7f000001", NUMERIC),
        ("https://0X7F.0.0.1", NUMERIC),
        ("https://0177.0.0.1", NUMERIC),
        ("https://127.000.000.001", NUMERIC),
        ("https://0", NUMERIC),
        ("https://10.0.0.5.", NUMERIC),
        ("http://2130706433:8080", NUMERIC),
        ("https://[::1%25lo]", NUMERIC),
        ("https://[fe80::1%25eth0]:8443", NUMERIC),
        # Re-review R-1b: the HTTP client maps U+3002, U+FF0E and U+FF61 to "." before it
        # connects, so a non-ASCII host is refused whole.
        ("https://127\u30020\u30020\u30021", NON_ASCII),
        ("https://0\uff0e0\uff0e0\uff0e0", NON_ASCII),
        ("https://localhost\uff61", NON_ASCII),
        ("https://p\u3002test", NON_ASCII),
        ("http://project\u3002project\u3002svc", NON_ASCII),
        ("https://b\u00fccher.example", NON_ASCII),
        ("http://127.0.0.1:8085", PLACEHOLDER),
        ("https://invalid", PLACEHOLDER),
        ("https://test", PLACEHOLDER),
        ("https://TEST.", PLACEHOLDER),
        ("https://project.invalid", PLACEHOLDER),
        ("https://project.test", PLACEHOLDER),
        ("https://project.Test.", PLACEHOLDER),
        ("http://project.project.svc.test", PLACEHOLDER),
    ],
)
def test_production_refuses_a_plain_or_placeholder_url_without_its_value(
    value: str, reason: str
) -> None:
    err = _refused({**PRODUCTION, **IN_CLUSTER, URL: value + "/" + API_DB_MARK})
    assert [(p.key, p.reason) for p in err.problems] == [(URL, reason)]
    assert API_DB_MARK not in str(err)  # the exact reason above holds no part of the URL either


def test_the_prototype_keeps_accepting_any_http_host() -> None:
    """The in-cluster rule is production's; the prototype calls the Project service on the host."""
    for value in ("http://127.0.0.1:8085", "http://localhost:8085", "http://project.test"):
        c = _load({**ENV, **SERVICE, URL: value})
        assert c.project_service is not None


def test_production_requires_the_url_in_the_project_service_mode() -> None:
    err = _refused({**PRODUCTION, "PROJECT_ACCESS_MODE": "project-service"})
    assert [(p.key, p.reason) for p in err.problems] == [(URL, "is required")]


def test_production_still_refuses_the_access_file_in_the_project_service_mode() -> None:
    err = _refused({**PRODUCTION, **IN_CLUSTER, "PROJECT_ACCESS_FILE": "/etc/webblock/access.json"})
    problem = next(p for p in err.problems if p.key == "PROJECT_ACCESS_FILE")
    assert problem.reason.startswith("must be unset in production: ")
    assert err.keys() == ("PROJECT_ACCESS_FILE",)


def test_production_refuses_the_url_without_the_mode() -> None:
    """Without the mode production builds the closed adapter, so a URL would seem to be in force
    when it is not."""
    err = _refused({**PRODUCTION, URL: IN_CLUSTER[URL]})
    assert [(p.key, p.reason) for p in err.problems] == [
        (URL, "must be unset unless PROJECT_ACCESS_MODE is project-service")
    ]
    assert IN_CLUSTER[URL] not in str(err)


def test_production_without_the_mode_keeps_the_closed_adapter() -> None:
    c = _load(PRODUCTION)
    assert (c.standins.project_access, c.project_service) == ("", None)


@pytest.mark.parametrize("value", ["prototype-allow", "prototype-file", "allow-all"])
def test_production_refuses_any_other_project_access_mode_without_its_value(value: str) -> None:
    err = _refused({**PRODUCTION, "PROJECT_ACCESS_MODE": value + "-" + API_DB_MARK})
    assert err.keys() == ("PROJECT_ACCESS_MODE",)
    assert err.problems[0].reason.startswith(
        "must be project-service, project-grpc or unset in production: "
    )
    assert API_DB_MARK not in str(err)


# The production example of .claude/docs/runbooks/identity.md (authentication design A5: realm roles
# USER and ADMIN in a flat roles claim; only the operator rollback needs ADMIN). Finding M-3 of the
# PR-3 review: the test reads the runbook's code block itself and loads every line of it.
RUNBOOK = Path(__file__).parents[4] / ".claude" / "docs" / "runbooks" / "identity.md"
RUNBOOK_PLACEHOLDERS = {
    "<domain>": "example.com",  # Keycloak is auth.<domain> (amended spec section 3, D5)
    "<project-service>": "project",
    "<project-namespace>": "project",
    "<port>": "8080",
}
# The keys the runbook says are still to be chosen: taken from the JWT fixture.
RUNBOOK_UNCHOSEN = ("AUTH_MAX_TOKEN_LIFETIME",)


def _runbook_example() -> dict[str, str]:
    """The ``sh`` block under "## Production example", as keys and values with the placeholders
    replaced."""
    text = RUNBOOK.read_text()
    section = text[text.index("## Production example") :]
    opened = section.index("```sh\n") + len("```sh\n")
    block = section[opened : section.index("\n```", opened)]
    for placeholder, value in RUNBOOK_PLACEHOLDERS.items():
        block = block.replace(placeholder, value)
    assert "<" not in block, "a placeholder of the runbook's example has no test value"
    pairs = [line.split("=", 1) for line in block.splitlines() if line.strip()]
    assert all(len(pair) == 2 for pair in pairs), "every line of the example is KEY=value"
    example = dict(pairs)
    assert len(example) == len(pairs), "the example sets each key once"
    return example


def test_the_runbooks_production_example_loads() -> None:
    """Every identity and project-access key comes from the runbook; only the keys it names as
    still to be chosen, and the sections it does not cover (listeners, database, limits), come
    from the fixtures. A key dropped from the example is then "is required", and a wrong value
    fails the load or the comparisons below."""
    example = _runbook_example()
    assert not set(example) & set(RUNBOOK_UNCHOSEN)
    identity_keys = set(JWT) | {"AUTH_ACTION_SCOPES", "IDENTITY_MODE", "PROVISIONING_PROFILE"}
    base = {k: v for k, v in PRODUCTION.items() if k not in identity_keys}
    c = _load(base | {k: JWT[k] for k in RUNBOOK_UNCHOSEN} | example)
    assert c.profile is Profile.PRODUCTION
    assert c.identity.mode == "jwt"
    jwt = c.identity.jwt
    assert jwt is not None
    assert jwt.issuer == "https://auth.example.com/realms/webblock"
    assert jwt.audience == "provisioning"
    assert dict(c.identity.action_scopes) == {
        a: "ADMIN" if a.is_admin_only() else "USER" for a in Action
    }
    assert c.standins.project_access == "project-service"
    assert c.project_service is not None
    assert c.project_service.url == "http://project.project.svc.cluster.local:8080"


def test_an_unknown_project_access_mode_lists_every_accepted_value() -> None:
    err = _refused(_with(PROJECT_ACCESS_MODE="project-service-" + API_DB_MARK))
    assert [(p.key, p.reason) for p in err.problems] == [
        (
            "PROJECT_ACCESS_MODE",
            "must be one of prototype-allow, prototype-file, project-service, project-grpc",
        )
    ]


# --- decision 0022 E7: PROJECT_ACCESS_MODE=project-grpc -------------------------------------------

GRPC = {"PROJECT_ACCESS_MODE": "project-grpc", "PROJECT_GRPC_TARGET": "project.webblock.svc:9090"}


def test_project_grpc_mode_reads_the_target() -> None:
    c = _load({**ENV, **GRPC})
    assert c.standins.project_access == "project-grpc"
    assert c.project_grpc is not None and c.project_grpc.target == "project.webblock.svc:9090"
    assert c.project_service is None


def test_project_grpc_mode_is_allowed_in_production() -> None:
    c = _load({**PRODUCTION, **GRPC})
    assert c.standins.project_access == "project-grpc"
    assert c.project_grpc is not None


def test_project_grpc_mode_needs_the_target() -> None:
    for env in (
        {**ENV, "PROJECT_ACCESS_MODE": "project-grpc"},
        {**ENV, **GRPC, "PROJECT_GRPC_TARGET": ""},
    ):
        err = _refused(env)
        assert [(p.key, p.reason) for p in err.problems] == [("PROJECT_GRPC_TARGET", "is required")]


@pytest.mark.parametrize(
    "value",
    [
        "http://project:9090",
        "project",
        "project:0",
        "project:99999",
        "project:90/x",
        "a b:1",
        ":9090",
    ],
)
def test_project_grpc_target_must_be_host_and_port(value: str) -> None:
    err = _refused({**ENV, **GRPC, "PROJECT_GRPC_TARGET": value})
    assert err.keys() == ("PROJECT_GRPC_TARGET",)
    assert value not in str(err)


def test_the_target_is_refused_without_the_mode() -> None:
    for env in (_with(PROJECT_GRPC_TARGET="p:1"), {**PRODUCTION, "PROJECT_GRPC_TARGET": "p:1"}):
        err = _refused(env)
        assert [(p.key, p.reason) for p in err.problems] == [
            ("PROJECT_GRPC_TARGET", "must be unset unless PROJECT_ACCESS_MODE is project-grpc")
        ]


def test_the_http_url_is_refused_in_the_grpc_mode() -> None:
    err = _refused({**ENV, **GRPC, URL: "http://127.0.0.1:8085"})
    assert err.keys() == (URL,)


# --- decision 0022 E7: ENTITLEMENT_MODE=payment-grpc ----------------------------------------------

PAYMENT = {"ENTITLEMENT_MODE": "payment-grpc", "PAYMENT_GRPC_TARGET": "payment.webblock.svc:9090"}


def test_payment_grpc_mode_reads_the_target() -> None:
    c = _load({**ENV, **PAYMENT})
    assert c.standins.entitlement == "payment-grpc"
    assert c.standins.entitlement_file is None
    assert c.standins.payment_grpc_target == "payment.webblock.svc:9090"


def test_payment_grpc_mode_needs_the_target() -> None:
    err = _refused({**ENV, "ENTITLEMENT_MODE": "payment-grpc"})
    assert [(p.key, p.reason) for p in err.problems] == [("PAYMENT_GRPC_TARGET", "is required")]


@pytest.mark.parametrize("value", ["http://payment:9090", "payment", "payment:0", "a b:1"])
def test_payment_grpc_target_must_be_host_and_port(value: str) -> None:
    err = _refused({**ENV, **PAYMENT, "PAYMENT_GRPC_TARGET": value})
    assert err.keys() == ("PAYMENT_GRPC_TARGET",)
    assert value not in str(err)


def test_the_payment_target_is_refused_without_the_mode() -> None:
    err = _refused({**ENV, "PAYMENT_GRPC_TARGET": "p:1"})
    assert err.keys() == ("PAYMENT_GRPC_TARGET",)


def test_the_entitlement_file_is_refused_in_payment_grpc_mode() -> None:
    err = _refused({**ENV, **PAYMENT, "ENTITLEMENT_FILE": "/etc/e.json"})
    assert err.keys() == ("ENTITLEMENT_FILE",)


def test_payment_grpc_mode_is_refused_in_production_naming_od_04_and_the_draft() -> None:
    err = _refused({**PRODUCTION, **PAYMENT})
    assert err.keys() == ("ENTITLEMENT_MODE", "PAYMENT_GRPC_TARGET")
    for problem in err.problems:
        assert "OD-04" in problem.reason
        assert "draft" in problem.reason
