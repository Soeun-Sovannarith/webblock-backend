"""The API host builds ``GrpcProjectAccess`` for PROJECT_ACCESS_MODE=project-grpc and closes its
channel on the way out (decision 0022 E7)."""

from __future__ import annotations

import dataclasses
import io
import logging
from uuid import UUID

import pytest
from tests.unit.hosts.api.test_project_access import _env
from tests.unit.infrastructure.grpc.stub import access_server, allowed

from provisioning.core.application import Action, Principal
from provisioning.hosts.api.wiring import project_access
from provisioning.infrastructure.config import load_api
from provisioning.infrastructure.config.api import ProjectGrpcConfig
from provisioning.infrastructure.project.grpc_access import GrpcProjectAccess
from provisioning.infrastructure.telemetry import new_logger

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_grpc_mode_builds_the_grpc_adapter_and_calls_the_server() -> None:
    env = _env(18080, 18081) | {
        "PROJECT_ACCESS_MODE": "project-grpc",
        "PROJECT_GRPC_TARGET": "project.webblock.svc:9090",
    }
    config = load_api(env.get)
    async with access_server() as server:
        server.answers = [allowed()]
        config = dataclasses.replace(config, project_grpc=ProjectGrpcConfig(target=server.target))
        logger = new_logger(io.StringIO(), logging.INFO)
        async with project_access(config, logger) as access:
            assert isinstance(access, GrpcProjectAccess)
            user = UUID("0199e7a0-0000-7000-8000-0000000000aa")
            await access.check_access(
                Principal(subject="a", scopes=(), user_id=user),
                UUID("0199e7a0-0000-7000-8000-0000000000bb"),
                Action.GET_RELEASE,
            )
        assert server.calls == 1


async def test_payment_grpc_mode_builds_the_entitlement_and_calls_the_server() -> None:
    from tests.unit.infrastructure.payment.stub import answer, entitlement_server

    from provisioning.core.domain import CommandKind
    from provisioning.hosts.api.wiring import payment_entitlement
    from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement

    env = _env(18080, 18081) | {
        "ENTITLEMENT_MODE": "payment-grpc",
        "PAYMENT_GRPC_TARGET": "payment.webblock.svc:9090",
    }
    config = load_api(env.get)
    logger = new_logger(io.StringIO(), logging.INFO)
    async with entitlement_server() as server:
        server.answers = [answer(1, 1)]
        standins = dataclasses.replace(config.standins, payment_grpc_target=server.target)
        config = dataclasses.replace(config, standins=standins)
        async with payment_entitlement(config, logger) as entitlement:
            assert isinstance(entitlement, GrpcEntitlement)
            await entitlement.check(
                UUID("0199e7a0-0000-7000-8000-0000000000bb"), CommandKind.DEPLOY
            )
        assert server.calls == 1


async def test_other_entitlement_modes_leave_it_to_build_api() -> None:
    from provisioning.hosts.api.wiring import payment_entitlement

    config = load_api(_env(18080, 18081).get)
    async with payment_entitlement(config, new_logger(io.StringIO(), logging.INFO)) as e:
        assert e is None
