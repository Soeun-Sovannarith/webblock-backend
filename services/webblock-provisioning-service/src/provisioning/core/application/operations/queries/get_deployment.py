"""get_deployment: the project's hosting row and its command in flight (decision 0004 section 3;
decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import ReadStore
from provisioning.core.application.operations.rows import _ForeignRow
from provisioning.core.application.operations.views import (
    DeploymentView,
    new_deployment_view,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class GetDeploymentQuery(cqrs.Query[DeploymentView]):
    principal: Principal
    project_id: UUID


class GetDeploymentHandler:
    __slots__ = ("_gate", "_reads")

    def __init__(self, *, gate: OperationGate, reads: ReadStore) -> None:
        self._gate = gate
        self._reads = reads

    async def handle(self, query: GetDeploymentQuery, /) -> DeploymentView:
        await self._gate.check(query.principal, Action.GET_DEPLOYMENT, query.project_id)
        deployment = await self._reads.read_deployment(query.project_id)
        if deployment.hosting.project_id != query.project_id or (
            deployment.in_flight is not None and deployment.in_flight.project_id != query.project_id
        ):
            raise _ForeignRow(f"hosting row of project {deployment.hosting.project_id}")
        return new_deployment_view(deployment)
