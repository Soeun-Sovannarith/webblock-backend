"""The prototype's stand-in for Payment's hosting entitlement (OD-04; F2 report, "Contract of the
in-process entitlement record"; task G; decision 0014 section 17 item 2).

One object, built by the worker host and given to both the Payment-event consumer (which records
each decoded event, with its event key, before it turns it into a command) and the hosting use case
(which asks it when a suspend or resume runs). Per project it keeps the Payment events in the order
this process first saw them: "entitlement ended" as False, "entitlement restored" as True.

``entitlement_after(project, key)`` answers what the latest event recorded after the event ``key``
said, or None when that is not known: the event ``key`` was never recorded (a restarted worker,
another replica, an event dropped by the bound) or nothing came after it. The hosting use case
skips a suspend only on True and a resume only on False, so a record that does not know never
skips a shutdown (finding M2 of the prototype review).

The record lives in memory and is bounded: at most ``max_projects`` projects (the one recorded
longest ago is forgotten first) and ``max_events_per_project`` events each (the oldest is dropped
first). A redelivered event keeps its first position. Selected only by
HOSTING_ENTITLEMENT_MODE=prototype-payment-events.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Final, Protocol
from uuid import UUID

MAX_EVENTS_PER_PROJECT: Final = 32


class PrototypePaymentEventRecord:
    """Implements ``HostingEntitlement`` from the Payment events this process consumed."""

    __slots__ = ("_max_events", "_max_projects", "_projects")

    def __init__(
        self, *, max_projects: int, max_events_per_project: int = MAX_EVENTS_PER_PROJECT
    ) -> None:
        if max_projects < 1 or max_events_per_project < 1:
            raise ValueError("the entitlement record needs positive bounds")
        self._max_projects = max_projects
        self._max_events = max_events_per_project
        # project -> event key -> entitled, each in the order first seen
        self._projects: OrderedDict[UUID, OrderedDict[str, bool]] = OrderedDict()

    def __len__(self) -> int:
        """The number of projects remembered."""
        return len(self._projects)

    def event_count(self) -> int:
        """The number of events remembered, across projects."""
        return sum(len(events) for events in self._projects.values())

    def record(self, project_id: UUID, *, event_key: str, entitled: bool) -> None:
        """Remember a Payment event of ``project_id``. A key already recorded for the project (a
        redelivered event) keeps its position and first value."""
        events = self._projects.get(project_id)
        if events is None:
            events = OrderedDict()
            self._projects[project_id] = events
        self._projects.move_to_end(project_id)
        if event_key not in events:
            events[event_key] = entitled
            while len(events) > self._max_events:
                events.popitem(last=False)
        while len(self._projects) > self._max_projects:
            self._projects.popitem(last=False)

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        """What the latest event recorded after ``event_key`` said; None when not known. Never
        raises."""
        events = self._projects.get(project_id)
        if events is None or event_key not in events:
            return None
        last_key, last_value = next(reversed(events.items()))
        return None if last_key == event_key else last_value


class DeletionLookup(Protocol):
    """Whether a project has a deletion command (``project-deleted``); implemented by
    ``infrastructure.postgres.hosting_intake.PgHostingHistory``. An error means unknown."""

    async def project_deleted(self, project_id: UUID) -> bool: ...


class PrototypeHostingRecord(PrototypePaymentEventRecord):
    """The record the worker builds (decision 0014 section 21 answer 4): the Payment events as
    above, and project deletion. A project with a deletion command answers False (ended) to every
    question, so a later resume ends ``skipped`` and the site stays offline. The deletion fact is
    read from the database at each question, so it survives a restart and holds on every replica.
    A failed lookup is raised (the hosting use case treats it as transient), never taken as "not
    deleted"."""

    __slots__ = ("_deletions",)

    def __init__(
        self,
        *,
        max_projects: int,
        deletions: DeletionLookup,
        max_events_per_project: int = MAX_EVENTS_PER_PROJECT,
    ) -> None:
        super().__init__(max_projects=max_projects, max_events_per_project=max_events_per_project)
        self._deletions = deletions

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        """False for a deleted project; otherwise what the Payment events say. Raises what the
        deletion lookup raises."""
        if await self._deletions.project_deleted(project_id):
            return False
        return await super().entitlement_after(project_id, event_key)
