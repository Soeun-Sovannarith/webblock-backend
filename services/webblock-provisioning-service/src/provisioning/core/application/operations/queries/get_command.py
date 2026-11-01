"""get_command: one command of the project (decision 0004 section 3; decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import CommandKey, ReadStore
from provisioning.core.application.operations.rows import _ForeignRow
from provisioning.core.application.operations.views import (
    CommandView,
    new_command_view,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class GetCommandQuery(cqrs.Query[CommandView]):
    principal: Principal
    project_id: UUID
    command_id: UUID


class GetCommandHandler:
    __slots__ = ("_gate", "_reads")

    def __init__(self, *, gate: OperationGate, reads: ReadStore) -> None:
        self._gate = gate
        self._reads = reads

    async def handle(self, query: GetCommandQuery, /) -> CommandView:
        await self._gate.check(query.principal, Action.GET_COMMAND, query.project_id)
        if query.command_id.int == 0:
            raise InvalidId("command ID is required")
        command = await self._reads.read_command(
            CommandKey(project_id=query.project_id, command_id=query.command_id)
        )
        if command.project_id != query.project_id or command.id != query.command_id:
            raise _ForeignRow(f"command {command.id}")
        return new_command_view(command)
