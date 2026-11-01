"""admin_list_commands: the project's commands newest first with their requester, paged like the
client listing, which never shows the requester (decision 0023 G13; cut from admin.py, design
2026-10-28 CQ8). Only an ``ADMIN`` takes it, on any project."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.admin import (
    AdminCommandPage,
    AdminGate,
    _InternalFault,
    new_admin_command_view,
)
from provisioning.core.application.operations.ports import MAX_PAGE_SIZE, HistoryStore
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.errors import CorruptRow, OperationNotAvailable
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class AdminListCommandsQuery(cqrs.Query[AdminCommandPage]):
    """``limit`` 1 to MAX_PAGE_SIZE; ``cursor`` the command number to continue below."""

    principal: Principal
    project_id: UUID
    limit: int
    cursor: int | None


class AdminListCommandsHandler:
    """``history`` None makes the operation ``OperationNotAvailable``."""

    __slots__ = ("_gate", "_history")

    def __init__(self, *, gate: AdminGate, history: HistoryStore | None) -> None:
        self._gate = gate
        self._history = history

    async def handle(self, query: AdminListCommandsQuery, /) -> AdminCommandPage:
        """One page of the project's commands, newest first, with their requester. ``limit`` is
        1 to MAX_PAGE_SIZE (the transports validate it; anything else is ``ValueError``);
        ``cursor`` is the command number to continue below. The store is asked for one row more
        than the page, so the last page has no cursor; a row of another project, out of order or
        beyond the limit is never returned."""
        principal, project_id = query.principal, query.project_id
        limit, cursor = query.limit, query.cursor
        await self._gate.check(principal, Action.ADMIN_LIST_COMMANDS, project_id)
        if not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"page size {limit} outside 1 to {MAX_PAGE_SIZE}")
        if cursor is not None and cursor < 1:
            raise InvalidId("the cursor names no command")
        history = self._history
        if history is None:
            raise OperationNotAvailable(str(Action.ADMIN_LIST_COMMANDS))
        asked = limit + 1
        rows = await history.list_commands(project_id, limit=asked, after=cursor)
        if len(rows) > asked:
            raise CorruptRow(f"list commands returned {len(rows)} rows, asked for {asked}")
        previous = cursor
        for row in rows:
            if row.project_id != project_id:
                raise _InternalFault(f"command {row.id} of another project")
            if previous is not None and row.command_number >= previous:
                raise CorruptRow(f"list commands returned command {row.id} out of order")
            previous = row.command_number
        page = rows[:limit]
        return AdminCommandPage(
            items=tuple(new_admin_command_view(c) for c in page),
            next_after=page[-1].command_number if len(rows) > limit else None,
        )
