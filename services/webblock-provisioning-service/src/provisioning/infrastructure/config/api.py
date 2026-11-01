"""The API process's configuration (decision 0006 section 7, with the prototype's settings of
decision 0014 section 3).

Notes:
- HTTP_READ_HEADER_TIMEOUT, HTTP_READ_TIMEOUT and HTTP_WRITE_TIMEOUT are not keys: uvicorn has no
  such timeouts and the gateway enforces slow-client limits (decision 0013 section 9, A3). The
  service keeps HTTP_REQUEST_TIMEOUT, HTTP_MAX_REQUEST_BODY_BYTES and HTTP_MAX_IN_FLIGHT in its own
  middleware; HTTP_IDLE_TIMEOUT is uvicorn's keep-alive timeout, HTTP_MAX_HEADER_BYTES its h11
  header limit and HTTP_SHUTDOWN_GRACE its graceful-shutdown timeout.
- PROVISIONING_PROFILE (task H2) is required: prototype or production, no default.
- Identity (``config.identity``, task H3): IDENTITY_MODE is dev-tokens (the prototype's static
  tokens) or jwt (decision 0006 section 5, with the AUTH_* keys of section 7) in the prototype,
  and only jwt in production (``IDENTITY_MODES``). The real issuer, audience and scope names are
  still OD-05's.
- Project access and entitlement are prototype stand-ins, each chosen by an explicit mode key
  whose value names the stand-in: prototype-allow or prototype-file; prototype-file needs
  PROJECT_ACCESS_FILE or ENTITLEMENT_FILE (decision 0014 section 21 ruling 10). In production
  every stand-in key is refused by name, and the host builds the closed adapters.
- PROJECT_ACCESS_MODE=project-service (task PA, decision 0017 section 2) asks the Project service
  instead of a stand-in: PROJECT_SERVICE_URL (http or https, without credentials, query or
  fragment) is required in that mode and refused in the others. The call carries no token (the
  owner's authentication design, A3, 18 October 2026: inside the mesh the caller is its mTLS
  identity), so PROJECT_SERVICE_TOKEN_FILE is refused everywhere (``REMOVED_KEYS``). For the same
  reason production accepts the mode; it still refuses the stand-in modes and PROJECT_ACCESS_FILE,
  and without the mode it builds the closed adapter (decision 0016 section 6). In production the
  URL is plain http only to an in-cluster Service name (``.svc``, ``.svc.cluster.local``), whose
  sidecar adds mTLS, and https otherwise; loopback and RFC 2606 placeholder hosts are refused, as
  the Project service's ProfileGuard does (``production_service_url``, finding M-2 of the PR-3
  review).
- MCP_ALLOWED_HOSTS is the Host allow-list of the MCP endpoint's DNS-rebinding protection
  (decision 0013 section 9, S3).
- The API holds one database login, PG_USER (provisioning_api). Since the build/API split
  (decision 0016 section 5 item 2) build_release only checks the pending build step and sends a
  hint, so the build worker's keys (PG_BUILD_*, BUILD_*, JENKINS_*, the repository file) are
  refused when set (``BUILD_WORKER_KEYS``).
"""

from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config.database import (
    MIN_TIMEOUT,
    DatabaseConfig,
    DatabaseLogin,
    read_database,
    read_login,
)
from provisioning.infrastructure.config.identity import (
    IdentityConfig,
    absolute_path,
    read_identity,
)
from provisioning.infrastructure.config.loader import (
    Loader,
    Lookup,
    has_control,
    load,
    required_keys,
    split_list,
)
from provisioning.infrastructure.config.net import (
    listen_addr,
    listeners_collide,
    valid_allowed_host,
    valid_origin,
)
from provisioning.infrastructure.config.profile import (
    read_profile,
    refuse_everywhere,
    refuse_in_production,
)
from provisioning.infrastructure.config.telemetry import (
    SentryConfig,
    TracingConfig,
    read_sentry,
    read_tracing,
)

# The accepted values of the stand-in mode keys in the prototype (decision 0014 sections 3 and
# 21). The identity modes are in ``config.identity``.
PROTOTYPE_ALLOW: Final = "prototype-allow"
PROTOTYPE_FILE: Final = "prototype-file"
PROJECT_SERVICE: Final = "project-service"
"""PROJECT_ACCESS_MODE's value that selects the Project service's access check (decision 0017)."""
PROJECT_GRPC: Final = "project-grpc"
"""PROJECT_ACCESS_MODE's value that selects the access check over gRPC (decision 0022 E7);
PROJECT_GRPC_TARGET (host:port, no scheme) is required with it and refused without it."""
GRPC_TARGET_KEY: Final = "PROJECT_GRPC_TARGET"
PAYMENT_GRPC: Final = "payment-grpc"
"""ENTITLEMENT_MODE's (API and worker) and HOSTING_ENTITLEMENT_MODE's (worker) value that selects
Payment's gRPC GetEntitlement (decision 0022 E7); PAYMENT_GRPC_TARGET (host:port) is required when
either mode has it and refused otherwise. Prototype only: the proto is a draft (OD-04)."""
PAYMENT_TARGET_KEY: Final = "PAYMENT_GRPC_TARGET"

SERVICE_URL_KEY: Final = "PROJECT_SERVICE_URL"

REMOVED_KEYS: Final = ("PROJECT_SERVICE_TOKEN_FILE",)
"""Keys no profile accepts any more, refused so that a stale environment is caught at start-up:
the Project service's token file went with the service token (authentication design A3)."""

MAX_BODY_BYTES: Final = 64 << 20

BUILD_WORKER_KEYS: Final = (
    "PG_BUILD_USER",
    "PG_BUILD_PASS",
    "REPOSITORY_MODE",
    "PROTOTYPE_REPOSITORIES_FILE",
    "BUILD_IMAGE_REGISTRY",
    "BUILD_DURATION",
    "BUILD_CALL_TIMEOUT",
    "BUILD_MAX_ATTEMPTS",
    "JENKINS_URL",
    "JENKINS_USER",
    "JENKINS_API_TOKEN",
    "JENKINS_JOB",
    "JENKINS_REQUEST_TIMEOUT",
    "JENKINS_OPERATION_TIMEOUT",
    "JENKINS_QUEUE_POLLS",
    "JENKINS_QUEUE_POLL_INTERVAL",
    "JENKINS_RECENT_BUILDS",
)
"""The build worker's keys, refused by the API since decision 0016 section 5 item 2."""

STANDIN_PROJECT_ACCESS: Final = (
    "project access is a prototype stand-in; production asks the Project service "
    f"(PROJECT_ACCESS_MODE={PROJECT_SERVICE}) or, without the mode, denies every request"
)
STANDIN_ENTITLEMENT: Final = (
    "entitlement is a prototype stand-in, and Payment's gRPC GetEntitlement is a draft proto; "
    "Payment's entitlement check is open (OD-04), so production denies"
)

_SERVICE_URL_REASON: Final = (
    "must be an absolute http or https URL with a host, without credentials, a query or a fragment"
)

_LOG_LEVELS: Final = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass(frozen=True, slots=True)
class HTTPConfig:
    """The public listener (every route requires a token) and the operational listener
    (/healthz, /readyz), with the limits the service enforces itself."""

    listen_addr: str
    ops_listen_addr: str
    request_timeout: timedelta
    idle_timeout: timedelta
    shutdown_grace: timedelta
    max_header_bytes: int
    max_request_body_bytes: int
    max_in_flight: int


@dataclass(frozen=True, slots=True)
class MCPConfig:
    """The /mcp endpoint's body limit (greater than zero) and its Origin and Host allow-lists. An
    empty origin list accepts no browser origin; a request without Origin is allowed."""

    max_request_body_bytes: int
    allowed_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StandinConfig:
    """The prototype stand-ins and the files they read (decision 0014 sections 10.2 and 21
    ruling 10). ``project_access_file`` and ``entitlement_file`` are set exactly when their mode
    is prototype-file. In production both modes are empty: no stand-in is selected."""

    project_access: str
    entitlement: str
    project_access_file: Path | None = None
    entitlement_file: Path | None = None
    payment_grpc_target: str | None = None  # PAYMENT_GRPC_TARGET, set with payment-grpc


@dataclass(frozen=True, slots=True)
class ProjectServiceConfig:
    """Where the Project service's access check is (``url``, its base URL). Nothing else: the
    call carries no token."""

    url: str


@dataclass(frozen=True, slots=True)
class ProjectGrpcConfig:
    """Where the Project service's gRPC ``AccessService`` is (``target``, ``host:port``)."""

    target: str


@dataclass(frozen=True, slots=True)
class APIConfig:
    """Everything the API process reads from its environment. No broker, Kubernetes, Jenkins or
    build-login keys: the API holds none of them (decision 0006 section 7, decision 0016 section
    5 item 2). ``project_service`` is set exactly with PROJECT_ACCESS_MODE=project-service."""

    profile: Profile
    log_level: int
    command_deadline: timedelta
    http: HTTPConfig
    mcp: MCPConfig
    identity: IdentityConfig
    standins: StandinConfig
    project_service: ProjectServiceConfig | None
    database: DatabaseConfig
    api_login: DatabaseLogin
    sentry: SentryConfig | None  # None: SENTRY_DSN=none (task H4)
    project_grpc: ProjectGrpcConfig | None = (
        None  # set exactly with PROJECT_ACCESS_MODE=project-grpc
    )
    tracing: TracingConfig | None = None  # None: OTEL_EXPORTER_OTLP_ENDPOINT unset or none


def load_api(lookup: Lookup) -> APIConfig:
    """Read and check the API configuration; ``LoadError`` names every refused key."""
    return load(lookup, read_api)


def api_keys() -> tuple[str, ...]:
    """The required keys of the API process, sorted. LOG_LEVEL, the one optional key, is not
    among them."""
    return required_keys(read_api)


def read_api(loader: Loader) -> APIConfig:
    """The API process's section reader."""
    profile = read_profile(loader)
    database = read_database(loader)
    api_login = read_login(loader, "PG_USER", "PG_PASS")
    refuse_everywhere(
        loader,
        BUILD_WORKER_KEYS,
        "must be unset: only the build worker starts builds and holds this setting "
        "(decision 0016 section 5 item 2)",
    )
    refuse_everywhere(
        loader, REMOVED_KEYS, "must be unset: calls to the Project service carry no token"
    )
    standins, project_service = _read_standins(loader, profile)
    project_grpc = _read_grpc_target(loader, standins.project_access)
    return APIConfig(
        profile=profile,
        log_level=read_log_level(loader),
        command_deadline=loader.duration(
            "COMMAND_DEADLINE", timedelta(seconds=1), timedelta(days=30)
        ),
        http=_read_http(loader),
        mcp=_read_mcp(loader),
        identity=read_identity(loader, profile),
        standins=standins,
        project_service=project_service,
        database=database,
        api_login=api_login,
        sentry=read_sentry(loader),
        tracing=read_tracing(loader),
        project_grpc=project_grpc,
    )


def grpc_target(value: str) -> str | None:
    """A gRPC target ``host:port`` without a scheme, path, credentials or whitespace (the reason
    never holds the value)."""
    reason = "must be host:port without a scheme, path or credentials"
    if len(value) > 255 or has_control(value) or any(c.isspace() for c in value):
        return reason
    if "://" in value or "/" in value or "@" in value or "?" in value or "#" in value:
        return reason
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536:
        return reason
    if host.count(":") and not (host.startswith("[") and host.endswith("]")):
        return reason
    return None


def _read_grpc_target(loader: Loader, mode: str) -> ProjectGrpcConfig | None:
    """PROJECT_GRPC_TARGET belongs to PROJECT_ACCESS_MODE=project-grpc alone."""
    if mode == PROJECT_GRPC:
        return ProjectGrpcConfig(target=loader.text(GRPC_TARGET_KEY, grpc_target))
    if loader.optional(GRPC_TARGET_KEY) is not None:
        loader.fail(GRPC_TARGET_KEY, f"must be unset unless PROJECT_ACCESS_MODE is {PROJECT_GRPC}")
    return None


def read_log_level(loader: Loader) -> int:
    """The optional LOG_LEVEL (debug, info, warn, error, without case); info when unset."""
    value = loader.optional("LOG_LEVEL")
    if value is None:
        return logging.INFO
    level = _LOG_LEVELS.get(value.lower())
    if level is None:
        loader.fail("LOG_LEVEL", "must be one of debug, info, warn, error")
        return logging.INFO
    return level


def _read_http(loader: Loader) -> HTTPConfig:
    config = HTTPConfig(
        listen_addr=loader.text("HTTP_LISTEN_ADDR", listen_addr),
        ops_listen_addr=loader.text("OPS_LISTEN_ADDR", listen_addr),
        request_timeout=loader.duration("HTTP_REQUEST_TIMEOUT", MIN_TIMEOUT, timedelta(hours=1)),
        idle_timeout=loader.duration("HTTP_IDLE_TIMEOUT", MIN_TIMEOUT, timedelta(hours=1)),
        shutdown_grace=loader.duration("HTTP_SHUTDOWN_GRACE", MIN_TIMEOUT, timedelta(minutes=10)),
        max_header_bytes=loader.integer("HTTP_MAX_HEADER_BYTES", 1 << 10, 1 << 20),
        max_request_body_bytes=loader.integer("HTTP_MAX_REQUEST_BODY_BYTES", 1, MAX_BODY_BYTES),
        max_in_flight=loader.integer("HTTP_MAX_IN_FLIGHT", 1, 100_000),
    )
    if loader.ok("HTTP_LISTEN_ADDR", "OPS_LISTEN_ADDR") and listeners_collide(
        config.listen_addr, config.ops_listen_addr
    ):
        loader.fail("OPS_LISTEN_ADDR", "must not bind the same address as HTTP_LISTEN_ADDR")
    return config


def _read_mcp(loader: Loader) -> MCPConfig:
    body = loader.integer("MCP_MAX_REQUEST_BODY_BYTES", 1, MAX_BODY_BYTES)
    origins: tuple[str, ...] = ()
    value = loader.raw("MCP_ALLOWED_ORIGINS")
    if value is not None and value != "none":
        items = split_list(value)
        if items is None:
            loader.fail(
                "MCP_ALLOWED_ORIGINS",
                "must be none or a comma-separated list of origins without empty elements",
            )
        elif not all(valid_origin(item) for item in items):
            loader.fail(
                "MCP_ALLOWED_ORIGINS",
                "must hold only origins of the form http(s)://host[:port], without a path",
            )
        else:
            origins = items
    hosts: tuple[str, ...] = ()
    value = loader.raw("MCP_ALLOWED_HOSTS")
    if value is not None:
        items = split_list(value)
        if items is None or not all(valid_allowed_host(item) for item in items):
            loader.fail(
                "MCP_ALLOWED_HOSTS",
                "must be a comma-separated list of host, host:port or host:* without empty "
                "elements",
            )
        else:
            hosts = items
    return MCPConfig(max_request_body_bytes=body, allowed_origins=origins, allowed_hosts=hosts)


def _read_standins(
    loader: Loader, profile: Profile
) -> tuple[StandinConfig, ProjectServiceConfig | None]:
    access, access_file, project_service = _read_project_access(loader, profile)
    entitlement, entitlement_file = read_entitlement_mode(loader, profile)
    payment_target = read_payment_target(loader, profile, entitlement)
    standins = StandinConfig(
        project_access=access,
        entitlement=entitlement,
        project_access_file=access_file,
        entitlement_file=entitlement_file,
        payment_grpc_target=payment_target,
    )
    return standins, project_service


def _read_project_access(
    loader: Loader, profile: Profile
) -> tuple[str, Path | None, ProjectServiceConfig | None]:
    """PROJECT_ACCESS_MODE: project-service with its URL, in either profile, or, in the
    prototype only, a stand-in (``read_standin_mode``). PROJECT_SERVICE_URL belongs to
    project-service alone and PROJECT_ACCESS_FILE to prototype-file alone. In production any other
    mode and the access file are refused when set; without the mode the mode is empty and the
    host builds the closed adapter."""
    mode_key, file_key = "PROJECT_ACCESS_MODE", "PROJECT_ACCESS_FILE"
    value = loader.optional(mode_key)
    if value == PROJECT_SERVICE:
        if not refuse_in_production(loader, profile, file_key, STANDIN_PROJECT_ACCESS) and (
            loader.optional(file_key) is not None
        ):
            loader.fail(file_key, f"must be unset unless {mode_key} is {PROTOTYPE_FILE}")
        check = production_service_url if profile is Profile.PRODUCTION else http_service_url
        service = ProjectServiceConfig(url=loader.text(SERVICE_URL_KEY, check))
        return PROJECT_SERVICE, None, service
    if value == PROJECT_GRPC:
        if not refuse_in_production(loader, profile, file_key, STANDIN_PROJECT_ACCESS) and (
            loader.optional(file_key) is not None
        ):
            loader.fail(file_key, f"must be unset unless {mode_key} is {PROTOTYPE_FILE}")
        if loader.optional(SERVICE_URL_KEY) is not None:
            loader.fail(SERVICE_URL_KEY, f"must be unset unless {mode_key} is {PROJECT_SERVICE}")
        return PROJECT_GRPC, None, None
    if profile is Profile.PRODUCTION:
        if value is not None:
            loader.fail(
                mode_key,
                f"must be {PROJECT_SERVICE}, {PROJECT_GRPC} or unset in production: "
                f"{STANDIN_PROJECT_ACCESS}",
            )
        refuse_in_production(loader, profile, file_key, STANDIN_PROJECT_ACCESS)
        mode, access_file = "", None
    else:
        if value is not None and value not in (PROTOTYPE_ALLOW, PROTOTYPE_FILE):
            # Reported first, so the reason lists every accepted value.
            loader.fail(
                mode_key,
                f"must be one of {PROTOTYPE_ALLOW}, {PROTOTYPE_FILE}, {PROJECT_SERVICE}, "
                f"{PROJECT_GRPC}",
            )
        mode, access_file = read_standin_mode(
            loader, profile, mode_key, file_key, STANDIN_PROJECT_ACCESS
        )
    # In production an empty mode is a valid choice (the closed adapter), so the URL is checked.
    if (mode or profile is Profile.PRODUCTION) and loader.optional(SERVICE_URL_KEY) is not None:
        loader.fail(SERVICE_URL_KEY, f"must be unset unless {mode_key} is {PROJECT_SERVICE}")
    return mode, access_file, None


def http_service_url(value: str) -> str | None:
    """An absolute http or https URL with a host, without credentials, a query, a fragment,
    whitespace or control characters, of at most 2048 bytes (PROJECT_SERVICE_URL, task PA). The
    reason never contains the URL."""
    if (
        len(value.encode("utf-8", "surrogatepass")) > 2048
        or has_control(value)
        or any(c.isspace() for c in value)
        or "#" in value
        or "?" in value
    ):
        return _SERVICE_URL_REASON
    try:
        parts = urlsplit(value)
        port_ok = parts.port is None or parts.port > 0
    except ValueError:
        return _SERVICE_URL_REASON
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or not port_ok
    ):
        return _SERVICE_URL_REASON
    return None


_PLAIN_IN_PRODUCTION: Final = (
    "must be https in production unless its host is an in-cluster Service (.svc or "
    ".svc.cluster.local)"
)
_PLACEHOLDER_IN_PRODUCTION: Final = (
    "must name the Project service's host in production, not a loopback or placeholder host"
)
_NON_ASCII_IN_PRODUCTION: Final = (
    "must name its host in production in ASCII (an internationalised name in its xn-- form)"
)
_NUMERIC_IN_PRODUCTION: Final = (
    "must name its host in production by a DNS name, a canonical IPv4 address or an IPv6 "
    "address without a zone ID"
)
_IN_CLUSTER_SUFFIXES: Final = (".svc", ".svc.cluster.local")
_PLACEHOLDER_NAMES: Final = ("invalid", "test", "localhost")
_LOCAL_NAMES: Final = frozenset({"ip6-localhost", "ip6-loopback", "localhost.localdomain"})
"""The usual /etc/hosts names of the loopback address that are not under ``localhost``."""
_NUMERIC_PART: Final = r"(?:0x[0-9a-f]*|[0-9]+)"
_NUMERIC_HOST: Final = re.compile(rf"{_NUMERIC_PART}(?:\.{_NUMERIC_PART})*\.?", re.ASCII)
"""What the C resolver may read as an IPv4 address (inet_aton: one to four decimal, octal or hex
parts, so ``127.1``, ``2130706433`` and ``0x7f000001`` all reach 127.0.0.1)."""


def production_service_url(value: str) -> str | None:
    """``http_service_url`` with production's rules (finding M-2 of the PR-3 review, re-review
    R-1; the amended authentication spec section 5.4, like the Project service's ProfileGuard).
    For any scheme:

    - a host with any non-ASCII character is refused (re-review R-1b): the HTTP client maps
      U+3002, U+FF0E and U+FF61 to ``.`` before it connects, so 127, U+3002, 0 and so on would
      reach the loopback address past the checks below. In-cluster and platform names are
      ASCII, and an internationalised name is written in its ``xn--`` form;
    - a host made only of decimal, octal or hex numbers that is not a canonical IPv4 literal,
      and an IPv6 literal with a zone ID (``%``), are refused: the resolver may read them as an
      address this check cannot judge;
    - loopback and unspecified addresses (``127.0.0.0/8``, ``::1``, ``0.0.0.0``, ``::``, and
      their IPv4-mapped forms), ``localhost`` or a name under it, ``ip6-localhost``,
      ``ip6-loopback``, ``localhost.localdomain``, and RFC 2606's ``invalid`` and ``test`` (as
      names or suffixes) are refused, compared in lower case without a trailing dot.

    Plain http is accepted only for a host ending in ``.svc`` or ``.svc.cluster.local``, where
    the sidecar adds mTLS. The reason never contains the URL."""
    reason = http_service_url(value)
    if reason is not None:
        return reason
    parts = urlsplit(value)
    raw = (parts.hostname or "").lower()
    if not raw.isascii():
        return _NON_ASCII_IN_PRODUCTION
    if "%" in raw or (_NUMERIC_HOST.fullmatch(raw) is not None and not _is_ipv4_literal(raw)):
        return _NUMERIC_IN_PRODUCTION
    host = raw.removesuffix(".")
    if (
        _is_local_address(host)
        or host in _LOCAL_NAMES
        or any(host == name or host.endswith("." + name) for name in _PLACEHOLDER_NAMES)
    ):
        return _PLACEHOLDER_IN_PRODUCTION
    if parts.scheme.lower() == "http" and not host.endswith(_IN_CLUSTER_SUFFIXES):
        return _PLAIN_IN_PRODUCTION
    return None


def _is_ipv4_literal(host: str) -> bool:
    """A canonical dotted quad: four decimal parts of 0 to 255 without leading zeros."""
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return True


def _is_local_address(host: str) -> bool:
    """An IP literal that reaches the local host: loopback or unspecified, IPv4-mapped IPv6
    included."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) else None
    return any(a.is_loopback or a.is_unspecified for a in (address, mapped) if a is not None)


def read_payment_target(loader: Loader, profile: Profile, *modes: str) -> str | None:
    """PAYMENT_GRPC_TARGET (host:port) belongs to the payment-grpc mode alone: required when any
    of ``modes`` is payment-grpc, refused when none is, and refused in production (the proto is a
    draft, OD-04)."""
    if refuse_in_production(loader, profile, PAYMENT_TARGET_KEY, STANDIN_ENTITLEMENT):
        return None
    if PAYMENT_GRPC in modes:
        return loader.text(PAYMENT_TARGET_KEY, grpc_target)
    if loader.optional(PAYMENT_TARGET_KEY) is not None:
        loader.fail(
            PAYMENT_TARGET_KEY,
            f"must be unset unless ENTITLEMENT_MODE or HOSTING_ENTITLEMENT_MODE is {PAYMENT_GRPC}",
        )
    return None


def read_entitlement_mode(loader: Loader, profile: Profile) -> tuple[str, Path | None]:
    """ENTITLEMENT_MODE and ENTITLEMENT_FILE, read the same way by the API and the worker: a
    stand-in mode or payment-grpc, and the file of prototype-file."""
    return read_standin_mode(
        loader,
        profile,
        "ENTITLEMENT_MODE",
        "ENTITLEMENT_FILE",
        STANDIN_ENTITLEMENT,
        also=(PAYMENT_GRPC,),
    )


def read_standin_mode(
    loader: Loader,
    profile: Profile,
    mode_key: str,
    file_key: str,
    what: str,
    *,
    also: tuple[str, ...] = (),
) -> tuple[str, Path | None]:
    """A stand-in's mode, prototype-allow or prototype-file, and the file of prototype-file
    (decision 0014 section 21 ruling 10). The file key is required in prototype-file mode and
    refused in prototype-allow mode, so a file never seems to be in force when it is not. In
    production both keys are refused when set (``what`` says why) and the mode is empty."""
    refused_mode = refuse_in_production(loader, profile, mode_key, what)
    refused_file = refuse_in_production(loader, profile, file_key, what)
    if refused_mode or refused_file:
        return "", None
    mode = loader.choice(mode_key, (PROTOTYPE_ALLOW, PROTOTYPE_FILE, *also))
    if mode == PROTOTYPE_FILE:
        return mode, Path(loader.text(file_key, absolute_path))
    if mode and loader.optional(file_key) is not None:
        loader.fail(file_key, f"must be unset unless {mode_key} is {PROTOTYPE_FILE}")
    return mode, None
