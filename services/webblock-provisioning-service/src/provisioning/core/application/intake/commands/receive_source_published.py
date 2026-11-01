"""Project's "source published" event into the inbox (decision 0008 section 2; cut from the release
intake's ``receive``, decision 0025 CQ6). The inbox processor in background/ accepts
the stored rows later."""

from __future__ import annotations

from dataclasses import dataclass

from provisioning.core.application.intake.ports import (
    IntakeOutcome,
    IntakeParams,
    ReleaseIntakeStore,
)
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.errors import CorruptRow
from provisioning.core.domain import (
    IdempotencyConflict,
    SourcePublishedFields,
    parse_source_published,
)


@dataclass(frozen=True, slots=True)
class ReceiveSourcePublishedCommand(cqrs.Command[IntakeOutcome]):
    """One "source published" event as the consumer decoded it, not yet parsed by the domain."""

    fields: SourcePublishedFields


class ReceiveSourcePublishedHandler:
    """Stores Project's "source published" event in the inbox."""

    __slots__ = ("_max_pending", "_store")

    def __init__(self, *, store: ReleaseIntakeStore, max_pending_per_project: int) -> None:
        """``max_pending_per_project`` is required configuration, at least 1 (``ValueError``)."""
        if max_pending_per_project < 1:
            raise ValueError("release intake: the per-project cap of pending rows must be positive")
        self._store = store
        self._max_pending = max_pending_per_project

    async def handle(self, command: ReceiveSourcePublishedCommand, /) -> IntakeOutcome:
        """Parse the event (a malformed event raises its domain error and nothing is stored) and
        store it. A mismatched repeat raises ``IdempotencyConflict`` naming the key only."""
        event = parse_source_published(command.fields)
        outcome = await self._store.intake(
            IntakeParams(event=event, max_pending_per_project=self._max_pending)
        )
        if outcome in (
            IntakeOutcome.STORED,
            IntakeOutcome.DUPLICATE,
            IntakeOutcome.OVER_CAPACITY,
        ):
            return outcome
        if outcome == IntakeOutcome.MISMATCHED_REPEAT:
            raise IdempotencyConflict(
                f"generation request {event.generation_request_id} of project "
                f"{event.project_id} was published with another source"
            )
        raise CorruptRow(
            f"intake store returned an unknown outcome for generation request "
            f"{event.generation_request_id} of project {event.project_id}"
        )
