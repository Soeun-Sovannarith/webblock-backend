"""get_release_diagnostics: diagnostics of a release from rows that exist (decision 0014 section
21 ruling 6, amending 0004 section 3; decision 0025 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.gate import OperationGate
from provisioning.core.application.operations.ports import (
    DIAGNOSTIC_COMMAND_LIMIT,
    DIAGNOSTIC_STEP_LIMIT,
    DiagnosticFacts,
    HistoryStore,
    ReleaseKey,
)
from provisioning.core.application.operations.rows import _ForeignRow
from provisioning.core.application.operations.views import (
    DiagnosticsView,
    SourceEventView,
    new_command_view,
    new_step_view,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.errors import CorruptRow, OperationNotAvailable
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import InvalidId


@dataclass(frozen=True, slots=True)
class GetReleaseDiagnosticsQuery(cqrs.Query[DiagnosticsView]):
    principal: Principal
    project_id: UUID
    release_id: UUID


class GetReleaseDiagnosticsHandler:
    """``history`` None makes the operation ``OperationNotAvailable`` after the gate and the
    validation."""

    __slots__ = ("_gate", "_history")

    def __init__(self, *, gate: OperationGate, history: HistoryStore | None) -> None:
        self._gate = gate
        self._history = history

    def _store(self) -> HistoryStore:
        """The listing and diagnostics store; without one the operation is not available."""
        if self._history is None:
            raise OperationNotAvailable(str(Action.GET_RELEASE_DIAGNOSTICS))
        return self._history

    async def handle(self, query: GetReleaseDiagnosticsQuery, /) -> DiagnosticsView:
        """Diagnostics from rows that exist (decision 0014 section 21 ruling 6, amending 0004
        section 3): bounded to the newest DIAGNOSTIC_STEP_LIMIT steps and
        DIAGNOSTIC_COMMAND_LIMIT commands, redacted to codes, counts and times, and scoped to
        the project: a row of another project or release is never returned."""
        await self._gate.check(query.principal, Action.GET_RELEASE_DIAGNOSTICS, query.project_id)
        if query.release_id.int == 0:
            raise InvalidId("release ID is required")
        facts = await self._store().read_diagnostics(
            ReleaseKey(project_id=query.project_id, release_id=query.release_id),
            step_limit=DIAGNOSTIC_STEP_LIMIT + 1,
            command_limit=DIAGNOSTIC_COMMAND_LIMIT + 1,
        )
        return _diagnostics_view(query, facts)


def _diagnostics_view(
    request: GetReleaseDiagnosticsQuery, facts: DiagnosticFacts
) -> DiagnosticsView:
    """Check every row against the request and the bounds, then keep the newest rows."""
    release = facts.release
    if release.project_id != request.project_id or release.id != request.release_id:
        raise _ForeignRow(f"release {release.id}")
    if len(facts.steps) > DIAGNOSTIC_STEP_LIMIT + 1:
        raise CorruptRow(f"diagnostics returned {len(facts.steps)} steps")
    if len(facts.commands) > DIAGNOSTIC_COMMAND_LIMIT + 1:
        raise CorruptRow(f"diagnostics returned {len(facts.commands)} commands")
    for step in facts.steps:
        if step.release_id != release.id:
            raise _ForeignRow(f"step {step.id} of release {step.release_id}")
    for command in facts.commands:
        if command.project_id != release.project_id:
            raise _ForeignRow(f"command {command.id}")
    source = facts.source_event
    if source is not None and (
        source.project_id != release.project_id
        or source.generation_request_id != release.generation_request_id
        or (source.release_id is not None and source.release_id != release.id)
    ):
        raise _ForeignRow("source event")
    return DiagnosticsView(
        release_id=release.id,
        project_id=release.project_id,
        release_status=release.status,
        release_failure_code=release.failure_code,
        release_created_at=release.created_at,
        release_updated_at=release.updated_at,
        release_promoted_at=release.promoted_at,
        steps=tuple(new_step_view(s) for s in facts.steps[:DIAGNOSTIC_STEP_LIMIT]),
        steps_truncated=len(facts.steps) > DIAGNOSTIC_STEP_LIMIT,
        commands=tuple(new_command_view(c) for c in facts.commands[:DIAGNOSTIC_COMMAND_LIMIT]),
        commands_truncated=len(facts.commands) > DIAGNOSTIC_COMMAND_LIMIT,
        source_event=(
            None
            if source is None
            else SourceEventView(
                status=source.status,
                outcome_code=source.outcome_code,
                attempts=source.attempts,
                mismatched_repeat_count=source.mismatched_repeat_count,
                received_at=source.received_at,
                finished_at=source.finished_at,
            )
        ),
    )
