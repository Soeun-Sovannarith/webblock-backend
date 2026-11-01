"""list_commands: one page of the project's commands, newest first, hosting commands included
(decision 0014 section 21 ruling 7; decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import (
    HistoryStore,
)
from provisioning.core.application.operations.rows import _ForeignRow, check_page_size
from provisioning.core.application.operations.views import (
    CommandPage,
    new_command_view,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.errors import CorruptRow, OperationNotAvailable
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class ListCommandsQuery(cqrs.Query[CommandPage]):
    """``page_size`` 1 to MAX_PAGE_SIZE, with command numbers below ``after`` when given."""

    principal: Principal
    project_id: UUID
    page_size: int
    after: int | None


class ListCommandsHandler:
    """``history`` None makes the operation ``OperationNotAvailable`` after the gate and the
    validation."""

    __slots__ = ("_gate", "_history")

    def __init__(self, *, gate: OperationGate, history: HistoryStore | None) -> None:
        self._gate = gate
        self._history = history

    def _store(self) -> HistoryStore:
        """The listing and diagnostics store; without one the operation is not available."""
        if self._history is None:
            raise OperationNotAvailable(str(Action.LIST_COMMANDS))
        return self._history

    async def handle(self, query: ListCommandsQuery, /) -> CommandPage:
        """One page of the project's commands, newest first, hosting commands included; checked
        as ``list_releases``."""
        await self._gate.check(query.principal, Action.LIST_COMMANDS, query.project_id)
        check_page_size(query.page_size)
        after = query.after
        if after is not None and after < 1:
            raise InvalidId("the page token names no command")
        limit = query.page_size + 1
        rows = await self._store().list_commands(query.project_id, limit=limit, after=after)
        if len(rows) > limit:
            raise CorruptRow(f"list commands returned {len(rows)} rows, asked for {limit}")
        previous = after
        for row in rows:
            if row.project_id != query.project_id:
                raise _ForeignRow(f"command {row.id}")
            if previous is not None and row.command_number >= previous:
                raise CorruptRow(f"list commands returned command {row.id} out of order")
            previous = row.command_number
        page = rows[: query.page_size]
        more = len(rows) > query.page_size
        return CommandPage(
            items=tuple(new_command_view(c) for c in page),
            next_after=page[-1].command_number if more else None,
        )
