"""list_releases: one page of the project's releases, newest first (decision 0014 section 21
ruling 7; decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import (
    HistoryStore,
    ReleaseCursor,
)
from provisioning.core.application.operations.rows import _ForeignRow, check_page_size
from provisioning.core.application.operations.views import (
    ReleasePage,
    new_release_summary_view,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.errors import CorruptRow, OperationNotAvailable
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class ListReleasesQuery(cqrs.Query[ReleasePage]):
    """``page_size`` 1 to MAX_PAGE_SIZE, after ``after``."""

    principal: Principal
    project_id: UUID
    page_size: int
    after: ReleaseCursor | None


class ListReleasesHandler:
    """``history`` None makes the operation ``OperationNotAvailable`` after the gate and the
    validation."""

    __slots__ = ("_gate", "_history")

    def __init__(self, *, gate: OperationGate, history: HistoryStore | None) -> None:
        self._gate = gate
        self._history = history

    def _store(self) -> HistoryStore:
        """The listing and diagnostics store; without one the operation is not available."""
        if self._history is None:
            raise OperationNotAvailable(str(Action.LIST_RELEASES))
        return self._history

    async def handle(self, query: ListReleasesQuery, /) -> ReleasePage:
        """One page of the project's releases, newest first (decision 0014 section 21 ruling 7).
        The store is asked for one row more than the page, so the last page has no cursor; a
        row of another project, out of order or beyond the limit is never returned."""
        await self._gate.check(query.principal, Action.LIST_RELEASES, query.project_id)
        check_page_size(query.page_size)
        after = query.after
        if after is not None and after.release_id.int == 0:
            raise InvalidId("the page token names no release")
        limit = query.page_size + 1
        rows = await self._store().list_releases(query.project_id, limit=limit, after=after)
        if len(rows) > limit:
            raise CorruptRow(f"list releases returned {len(rows)} rows, asked for {limit}")
        previous = None if after is None else (after.created_at, after.release_id)
        for row in rows:
            if row.project_id != query.project_id:
                raise _ForeignRow(f"release {row.id}")
            position = (row.created_at, row.id)
            if previous is not None and position >= previous:
                raise CorruptRow(f"list releases returned release {row.id} out of order")
            previous = position
        page = rows[: query.page_size]
        more = len(rows) > query.page_size
        return ReleasePage(
            items=tuple(new_release_summary_view(r) for r in page),
            next_cursor=(
                ReleaseCursor(created_at=page[-1].created_at, release_id=page[-1].id)
                if more
                else None
            ),
        )
