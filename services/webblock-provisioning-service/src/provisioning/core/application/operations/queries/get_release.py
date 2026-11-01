"""get_release: one release with its steps (decision 0004 section 3; decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import ReadStore
from provisioning.core.application.operations.rows import read_release_view
from provisioning.core.application.operations.views import (
    ReleaseView,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class GetReleaseQuery(cqrs.Query[ReleaseView]):
    principal: Principal
    project_id: UUID
    release_id: UUID


class GetReleaseHandler:
    __slots__ = ("_gate", "_reads")

    def __init__(self, *, gate: OperationGate, reads: ReadStore) -> None:
        self._gate = gate
        self._reads = reads

    async def handle(self, query: GetReleaseQuery, /) -> ReleaseView:
        await self._gate.check(query.principal, Action.GET_RELEASE, query.project_id)
        if query.release_id.int == 0:
            raise InvalidId("release ID is required")
        return await read_release_view(self._reads, query.project_id, query.release_id)
