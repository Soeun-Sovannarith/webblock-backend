"""Builds the public application the API host serves, around the given operations' buses, for the
transport tests: the shared invoker, the development identity, the MCP endpoint from the contract's
tool file and the FastAPI app with its middleware. Not a test module; imported as
``tests.unit.presentation.apps``."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx2
from tests.unit.presentation.fakes import Operations, buses
from tests.unit.presentation.memory import ALL_SCOPES, READ_SCOPES

from provisioning.core.application import ADMIN_ROLE
from provisioning.infrastructure.standins.dev_tokens import PrototypeDevTokenIdentity
from provisioning.infrastructure.telemetry import correlation_scope, new_logger
from provisioning.presentation.http import TokenVerifier, build_public_app
from provisioning.presentation.mcp import MCPEndpoint
from provisioning.presentation.operations import Invoker, random_correlation_id

TOOLS = (
    Path(__file__).resolve().parents[3] / "src" / "contracts" / "mcp" / "tools.json"
).read_bytes()

OPENAPI = Path(__file__).resolve().parents[3] / "src" / "contracts" / "openapi.yaml"

BEARER_ALL = "dev-token-all-0123456789abcdefghijklmnopqrstu"
BEARER_READ = "dev-token-read-0123456789abcdefghijklmnopqrst"
BEARER_ADMIN = "dev-token-admin-0123456789abcdefghijklmnopqrs"
ADMIN_SUBJECT = "staff"
"""The admin token's subject: every action's scope and the ADMIN role (decision 0023 G10)."""
CORR = "corr-fixed-1"
BASE = "http://testserver"


def dev_identity() -> PrototypeDevTokenIdentity:
    return PrototypeDevTokenIdentity.from_json(
        json.dumps(
            {
                "tokens": [
                    {"token": BEARER_ALL, "subject": "alice", "scopes": list(ALL_SCOPES)},
                    {"token": BEARER_READ, "subject": "reader", "scopes": list(READ_SCOPES)},
                    {
                        "token": BEARER_ADMIN,
                        "subject": ADMIN_SUBJECT,
                        "scopes": [*ALL_SCOPES, ADMIN_ROLE],
                    },
                ]
            }
        ).encode()
    )


@dataclass
class Built:
    app: Any
    invoker: Invoker
    mcp: MCPEndpoint
    log: io.StringIO


def build(
    operations: Operations,
    *,
    verifier: TokenVerifier | None = None,
    max_in_flight: int = 16,
    request_timeout: float = 5.0,
    middleware_timeout: float | None = None,
    body_limit: int = 1024,
    mcp_body_limit: int = 65536,
    allowed_origins: tuple[str, ...] = (),
    docs: bool = False,
    request_ids: bool = False,
    trace_id: str | None = None,
) -> Built:
    log = io.StringIO()
    logger = new_logger(log, logging.DEBUG)
    invoker = Invoker(
        **buses(operations),
        request_timeout=timedelta(seconds=request_timeout),
        new_correlation_id=random_correlation_id if request_ids else (lambda: CORR),
        logger=logger,
        bind_correlation=correlation_scope,
        trace_id=lambda: trace_id,
    )
    mcp = MCPEndpoint(
        invoker=invoker,
        tools=TOOLS,
        max_request_body_bytes=mcp_body_limit,
        allowed_origins=allowed_origins,
        allowed_hosts=("testserver",),
    )
    app = build_public_app(
        invoker=invoker,
        verifier=verifier if verifier is not None else dev_identity(),
        mcp=mcp,
        max_in_flight=max_in_flight,
        max_request_body_bytes=body_limit,
        mcp_max_request_body_bytes=mcp_body_limit,
        openapi_yaml=OPENAPI.read_bytes() if docs else None,
        request_timeout=timedelta(
            seconds=middleware_timeout if middleware_timeout is not None else request_timeout * 2
        ),
    )
    return Built(app=app, invoker=invoker, mcp=mcp, log=log)


@asynccontextmanager
async def client(built: Built, token: str | None = BEARER_ALL) -> AsyncIterator[httpx2.AsyncClient]:
    """An HTTP client on the app in-process, with the app's lifespan (the MCP session manager)
    running and the bearer token set."""
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    async with built.app.router.lifespan_context(built.app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=built.app), base_url=BASE, headers=headers
        ) as http:
            yield http


def mcp_form(rest: dict[str, Any]) -> Any:
    """What MCP returns for a REST success body (decision 0023 G7): the ``data`` object, or for a
    list ``{"items": <data>, "page": <meta.page>}``."""
    assert rest["success"] is True, rest
    if isinstance(rest["data"], list):
        return {"items": rest["data"], "page": rest["meta"]["page"]}
    return rest["data"]
