"""The API process built from its configuration with constructors only (port of
src/hosts/api/apiwiring; decisions 0004, 0006, 0014 section 3): the action policy, the identity
(the bearer-JWT verifier, or the prototype's development tokens), the prototype's stand-ins (or,
in production, the closed adapters of ``hosts.shared.open_contracts``), the build request use
case, the fourteen operations on the command and query buses, the shared operation layer, the
REST and MCP transports and the operational listener, served by uvicorn.

There is no global state and no service locator. The process holds no cluster credential and no
broker credential (decision 0006 section 7); import-linter's ``hosts-no-kubernetes`` contract and a
test keep Kubernetes out of it.

The PostgreSQL stores come from task B3 (``provisioning.infrastructure.postgres``): one engine as
a login of ``provisioning_api`` for acceptance, reads and build_release. Since the build/API split
(decision 0016 section 5 item 2, task H2) build_release (``RequestBuild``) only reads the pending
build step and sends the build hint; the API holds neither the Jenkins token nor the
``provisioning_build`` login. ``postgres_stores`` opens the engine; tests use doubles.

The profile (task H2): ``api_operations`` enables all ten operations in the prototype; in
production it refuses every mutation with ``OpenContract``, and project access is the Project
service's (PROJECT_ACCESS_MODE=project-service) or, without the mode, closed.

Project access (task PA, decision 0017 section 2): ``project_access`` gives the adapter
PROJECT_ACCESS_MODE names. With ``project-service``, in either profile, it builds
``ProjectServiceAccess``, which sends no token (the owner's authentication design, A3, approved 18
October 2026), and closes its HTTP client on the way out; otherwise the stand-in, or in production
the closed adapter of ``hosts.shared.open_contracts``. A token whose roles hold ``ADMIN`` rolls back
any project without the Project service being asked (the gate skips it, decision 0019 section 7,
OD-45); any other principal without a platform user ID is denied by the adapter before any call.

The identity (decision 0024): ``read_identity`` builds the ``JwtVerifier`` of IDENTITY_MODE=jwt,
web-block-commons' token check, which trusts the system's roots and fetches nothing until the first
request, so the API starts with the issuer down and answers 503 identity_unavailable until keys
arrive; a JWT without a valid ``user_id`` answers 403 before any call. Or, in the prototype only,
the development tokens through ``hosts.shared.stand_ins``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import uvicorn
from opentelemetry.sdk.trace import TracerProvider

from provisioning.core.application import (
    OPERATION_COMMANDS,
    OPERATION_QUERIES,
    AcceptanceStore,
    Action,
    AuditLog,
    CommandBus,
    Entitlement,
    HistoryStore,
    OperationDeps,
    ProjectAccess,
    QueryBus,
    ReadStore,
    RedeployStore,
    ServiceAudit,
    register_operations,
)
from provisioning.core.application.builds.ports import BuildRequestStore
from provisioning.core.application.builds.request_build import RequestBuild
from provisioning.core.application.shared.profile import Profile
from provisioning.hosts.shared import open_contracts, stand_ins
from provisioning.hosts.shared.buses import require_operations
from provisioning.hosts.shared.runtime import (
    InvalidSettings,
    SystemClock,
    UUIDv7,
    open_role_engine,
    timeouts_of,
)
from provisioning.infrastructure.config import APIConfig
from provisioning.infrastructure.config.api import PAYMENT_GRPC, PROJECT_GRPC, PROJECT_SERVICE
from provisioning.infrastructure.config.files import read_file
from provisioning.infrastructure.config.identity import IDENTITY_JWT
from provisioning.infrastructure.grpc.call import open_channel
from provisioning.infrastructure.identity import ActionScopePolicy, JwtVerifier
from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement
from provisioning.infrastructure.postgres import (
    PgAcceptanceStore,
    PgReadStore,
    insert_dispatch,
)
from provisioning.infrastructure.postgres.build_requests import PgBuildRequests
from provisioning.infrastructure.project import CALL_TIMEOUT, ProjectServiceAccess
from provisioning.infrastructure.project.grpc_access import GrpcProjectAccess
from provisioning.infrastructure.telemetry import Logger, correlation_scope, sampled_trace_id
from provisioning.infrastructure.telemetry.metrics import Metrics
from provisioning.infrastructure.telemetry.tracing import instrument_fastapi
from provisioning.presentation.http import (
    ReadinessCheck,
    TokenVerifier,
    build_operational_app,
    build_public_app,
)
from provisioning.presentation.http.middleware import (
    RequestMetrics,
    route_template_of,
    route_templates,
)
from provisioning.presentation.mcp import MCPEndpoint
from provisioning.presentation.operations import Invoker, random_correlation_id
from provisioning.presentation.operations.operations import OPERATIONS

PROTOTYPE_OPERATIONS: Final[tuple[Action, ...]] = open_contracts.PROTOTYPE_OPERATIONS
"""The operations the prototype exposes (``hosts.shared.open_contracts``)."""

BUILD_LOGGER: Final = "provisioning.build"


@dataclass(frozen=True, slots=True)
class APIStores:
    """The stores the API process uses, and the readiness check of their pools. ``history`` is the
    listing and diagnostics reads (the same PostgreSQL store as ``reads``); ``build_requests``
    reads the build target and sends the build hint (``PgBuildRequests``). ``redeploys`` accepts an
    admin's redeploy (the same PostgreSQL store as ``acceptance``); None leaves redeploy_release
    not available."""

    acceptance: AcceptanceStore
    reads: ReadStore
    history: HistoryStore
    build_requests: BuildRequestStore
    ready: ReadinessCheck
    redeploys: RedeployStore | None = None


@dataclass(frozen=True, slots=True)
class APIIdentity:
    """What verifies every bearer token on both transports, built once at start-up."""

    verifier: TokenVerifier


def read_identity(config: APIConfig, logger: Logger) -> APIIdentity:
    """The verifier IDENTITY_MODE names. jwt: the ``JwtVerifier`` (``InvalidSettings`` if its
    settings are refused; the loader has already checked them). dev-tokens: the token file read
    through ``hosts.shared.stand_ins`` (``ConfigFileError``, never with the file's contents, when it
    cannot be read or is invalid; ``StandinNotAvailable`` in production)."""
    jwt = config.identity.jwt
    if config.identity.mode == IDENTITY_JWT and jwt is not None:
        try:
            return APIIdentity(verifier=JwtVerifier(jwt, logger=logger))
        except ValueError as err:
            raise InvalidSettings(str(err)) from None
    return APIIdentity(
        verifier=stand_ins.read_dev_tokens(config.profile, config.identity.dev_tokens_file)
    )


def _uses_project_service(config: APIConfig) -> bool:
    """The mode decides, not the profile: production may ask the Project service too."""
    return config.standins.project_access == PROJECT_SERVICE and config.project_service is not None


@asynccontextmanager
async def project_access(config: APIConfig, logger: Logger) -> AsyncIterator[ProjectAccess]:
    """The ProjectAccess of PROJECT_ACCESS_MODE for the life of the block. project-service: the
    adapter (``InvalidSettings`` when it refuses its settings), whose client is closed on the
    way out. Any other mode: the stand-in, or the closed adapter in production
    (``hosts.shared.open_contracts``), which holds nothing to close."""
    grpc_config = config.project_grpc
    if config.standins.project_access == PROJECT_GRPC and grpc_config is not None:
        # The channel is lazy: it connects on the first call, so start-up needs no Project service.
        channel = open_channel(grpc_config.target)
        try:
            yield GrpcProjectAccess(channel, logger)
        finally:
            await channel.close()
        return
    service = config.project_service
    if not _uses_project_service(config) or service is None:
        yield open_contracts.project_access(
            config.profile,
            config.standins.project_access,
            config.standins.project_access_file,
            logger,
        )
        return
    try:
        adapter = ProjectServiceAccess(base_url=service.url, timeout=CALL_TIMEOUT, logger=logger)
    except ValueError as err:
        raise InvalidSettings(str(err)) from None
    try:
        yield adapter
    finally:
        await adapter.aclose()


@asynccontextmanager
async def payment_entitlement(
    config: APIConfig, logger: Logger
) -> AsyncIterator[Entitlement | None]:
    """The gRPC ``Entitlement`` of ENTITLEMENT_MODE=payment-grpc (prototype only; the config
    refuses it in production) for the life of the block, or None for any other mode, which
    ``build_api`` builds itself. The channel is lazy and closed on the way out."""
    target = config.standins.payment_grpc_target
    if config.standins.entitlement != PAYMENT_GRPC or target is None:
        yield None
        return
    channel = open_channel(target)
    try:
        yield GrpcEntitlement(channel, logger)
    finally:
        await channel.close()


# src/: this file is src/provisioning/hosts/api/wiring.py.
_SOURCE_ROOT: Final = Path(__file__).resolve().parents[3]


def contract_tools() -> bytes:
    """src/contracts/mcp/tools.json, read from the source tree the package runs from. The wheel
    does not carry the contracts yet (review-port-arch.md M5)."""
    path = _SOURCE_ROOT / "contracts" / "mcp" / "tools.json"
    return read_file(path, "the MCP tool contract")


def contract_openapi() -> bytes:
    """src/contracts/openapi.yaml, read from the same source tree as the tool contract (the
    prototype's /openapi.yaml, task SW)."""
    path = _SOURCE_ROOT / "contracts" / "openapi.yaml"
    return read_file(path, "the REST contract")


@asynccontextmanager
async def postgres_stores(config: APIConfig) -> AsyncIterator[APIStores]:
    """The PostgreSQL stores of task B3 on the one login of ``config``, a member of
    ``provisioning_api``. Opening them connects nothing; the engine is disposed of on the way out.
    ``ready`` checks out a connection (with ``pool_pre_ping``), so /readyz answers 503 while the
    database cannot be reached."""
    timeouts = timeouts_of(config.database)
    api_engine = open_role_engine(config.database, config.api_login)
    try:

        async def ready() -> None:
            async with api_engine.connect():
                pass

        reads = PgReadStore(api_engine, timeouts)
        acceptance = PgAcceptanceStore(api_engine, timeouts, insert_dispatch)
        yield APIStores(
            acceptance=acceptance,
            reads=reads,
            history=reads,
            build_requests=PgBuildRequests(api_engine, timeouts),
            ready=ready,
            redeploys=acceptance,
        )
    finally:
        await api_engine.dispose()


class APIProcess:
    """A built API process; ``serve`` it once."""

    __slots__ = ("_config", "_log", "_operations", "_ops", "_public")

    def __init__(
        self,
        *,
        config: APIConfig,
        public: uvicorn.Config,
        ops: uvicorn.Config,
        logger: Logger,
        operations: tuple[Action, ...],
    ) -> None:
        self._config = config
        self._public = public
        self._ops = ops
        self._log = logger
        self._operations = operations

    async def serve(self, stop: asyncio.Event) -> None:
        """Serve the public listener (HTTP_LISTEN_ADDR) and the operational listener
        (OPS_LISTEN_ADDR) until ``stop`` is set or a listener ends. Then both stop accepting
        connections and let the requests in progress finish within HTTP_SHUTDOWN_GRACE.
        ``ListenerFailed`` when a listener could not start or ended on its own."""
        servers = (_Server(self._public), _Server(self._ops))
        self._log.info(
            "api serving",
            listen_addr=f"{self._config.http.listen_addr} {self._config.http.ops_listen_addr}",
            operation=",".join(a.value for a in self._operations),
        )
        failed = False
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_serve(s)) for s in servers]
            stopper = tg.create_task(stop.wait())
            await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
            failed = not stop.is_set()
            self._log.info("api shutting down")
            for server in servers:
                server.should_exit = True
            stopper.cancel()
        if failed or any(not t.result() for t in tasks):
            raise ListenerFailed("a listener could not start or ended on its own")


class ListenerFailed(Exception):
    """A listener could not be bound or stopped without a stop request."""


class _Server(uvicorn.Server):
    """uvicorn's server without its own signal handling: the process installs one handler for
    both listeners (``hosts.api.signals``)."""

    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def _serve(server: _Server) -> bool:
    """Serve until told to exit; False when the server failed to start (uvicorn exits through
    SystemExit when it cannot bind)."""
    try:
        await server.serve()
    except SystemExit:
        return False
    return server.started


def build_api(
    config: APIConfig,
    *,
    stores: APIStores,
    identity: APIIdentity,
    logger: Logger,
    audit: AuditLog,
    tools: bytes | None = None,
    metrics: Metrics | None = None,
    access: ProjectAccess | None = None,
    entitlement: Entitlement | None = None,
    tracer_provider: TracerProvider | None = None,
) -> APIProcess:
    """Build the process from its configuration and its dependencies. ``audit`` receives the
    audit event of every API mutation (decision 0014 section 21 answer 3). ``tools`` defaults to
    the contract's tool file. ``metrics`` (task H4) measures every public request and is served
    at /metrics on the operational listener only; without it there is no /metrics. With
    PROJECT_ACCESS_MODE or ENTITLEMENT_MODE prototype-file, the stand-in reads its file here
    (``StandinNotAvailable`` when it cannot); in production the closed adapters are built
    instead. ``access`` is the ProjectAccess of ``project_access``; without it the stand-in or
    closed adapter is built here, and PROJECT_ACCESS_MODE=project-service or project-grpc is
    ``InvalidSettings`` (its client must be closed by ``project_access``). ``entitlement`` is the
    Entitlement of ``payment_entitlement``; ENTITLEMENT_MODE=payment-grpc without it is
    ``InvalidSettings``. ``tracer_provider`` (tracing on, decision 0022 E9) traces every public
    request through the FastAPI instrumentation. Opens no socket or connection."""
    if entitlement is None:
        if config.standins.entitlement == PAYMENT_GRPC:
            raise InvalidSettings(
                "ENTITLEMENT_MODE=payment-grpc needs the adapter built by payment_entitlement"
            )
        entitlement = open_contracts.entitlement(
            config.profile,
            config.standins.entitlement,
            config.standins.entitlement_file,
            logger,
        )
    if access is None:
        if _uses_project_service(config) or config.standins.project_access == PROJECT_GRPC:
            raise InvalidSettings(
                "PROJECT_ACCESS_MODE=project-service or project-grpc needs the adapter built by "
                "project_access"
            )
        access = open_contracts.project_access(
            config.profile,
            config.standins.project_access,
            config.standins.project_access_file,
            logger,
        )
    ids = UUIDv7()
    clock = SystemClock()
    enabled = open_contracts.api_operations(config.profile)
    # The API's buses: the fourteen REST and MCP operations, registered by hand and checked
    # against the operation table before anything is served (decision 0025 CQ11).
    commands, queries = CommandBus(), QueryBus()
    register_operations(
        commands,
        queries,
        OperationDeps(
            policy=ActionScopePolicy(config.identity.action_scopes),
            access=access,
            entitlement=entitlement,
            acceptance=stores.acceptance,
            reads=stores.reads,
            clock=clock,
            ids=ids,
            builds=RequestBuild(
                store=stores.build_requests, logger=logging.getLogger(BUILD_LOGGER)
            ),
            enabled=enabled,
            command_deadline=config.command_deadline,
            history=stores.history,
            audit=ServiceAudit(log=audit, clock=clock),
            redeploys=stores.redeploys,
        ),
    )
    require_operations(
        commands,
        queries,
        (op.action for op in OPERATIONS),
        command_of=OPERATION_COMMANDS,
        query_of=OPERATION_QUERIES,
        process="api",
    )
    invoker = Invoker(
        commands=commands,
        queries=queries,
        request_timeout=config.http.request_timeout,
        new_correlation_id=random_correlation_id,
        logger=logger,
        bind_correlation=correlation_scope,
        trace_id=sampled_trace_id,
    )
    mcp = MCPEndpoint(
        invoker=invoker,
        tools=tools if tools is not None else contract_tools(),
        max_request_body_bytes=config.mcp.max_request_body_bytes,
        allowed_origins=config.mcp.allowed_origins,
        allowed_hosts=config.mcp.allowed_hosts,
    )
    public = build_public_app(
        invoker=invoker,
        verifier=identity.verifier,
        mcp=mcp,
        max_in_flight=config.http.max_in_flight,
        max_request_body_bytes=config.http.max_request_body_bytes,
        mcp_max_request_body_bytes=config.mcp.max_request_body_bytes,
        request_timeout=config.http.request_timeout,
        # Swagger UI for team testing exists in the prototype profile only (decision 0006 s. 7).
        openapi_yaml=contract_openapi() if config.profile is Profile.PROTOTYPE else None,
    )
    if tracer_provider is not None:
        instrument_fastapi(public, tracer_provider)
    served: object = public
    if metrics is not None:
        served = RequestMetrics(
            public, metrics=metrics, template=route_template_of(route_templates())
        )
    operational = build_operational_app(
        ready=stores.ready,
        timeout=config.http.request_timeout,
        metrics=None if metrics is None else metrics.render,
    )
    return APIProcess(
        config=config,
        public=_uvicorn(config, served, config.http.listen_addr, lifespan="on"),
        ops=_uvicorn(config, operational, config.http.ops_listen_addr, lifespan="off"),
        logger=logger,
        operations=enabled.actions(),
    )


def _uvicorn(config: APIConfig, app: object, address: str, *, lifespan: str) -> uvicorn.Config:
    """One listener: h11 with the configured header limit, keep-alive and shutdown grace; no
    access log, no Server header, no proxy headers, no websockets, and no logging configuration
    of uvicorn's own (its records go through the root handler the host installs)."""
    host, port = _host_port(address)
    return uvicorn.Config(
        app,  # type: ignore[arg-type]  # an ASGI 3 application; uvicorn's union type is narrower
        host=host,
        port=port,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan=lifespan,  # type: ignore[arg-type]  # "on" or "off", a LifespanType literal
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_keep_alive=max(1, math.ceil(config.http.idle_timeout.total_seconds())),
        timeout_graceful_shutdown=max(1, math.ceil(config.http.shutdown_grace.total_seconds())),
        h11_max_incomplete_event_size=config.http.max_header_bytes,
    )


def _host_port(address: str) -> tuple[str, int]:
    """A checked listen address as uvicorn's host and port; an empty host binds every
    interface."""
    host, _, port = address.rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    return host or "0.0.0.0", int(port)  # noqa: S104  # ":port" means every interface (0006 section 7)
