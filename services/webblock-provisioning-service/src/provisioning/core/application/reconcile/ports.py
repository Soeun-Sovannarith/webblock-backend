"""What the reconciler needs from our database: the project record and the reconcile store (cut
from reconcile.py, decision 0025 CQ6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from provisioning.core.domain import HostingStatus, ReleaseStatus, RoutingStatus


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    """What the records say about one project: its ``project_hosting`` row (with the sub-domain
    the site's hostname is built from, decision 0023 G18), the live release's image digest (None
    without a live release), and whether a command is accepted or running."""

    project_id: UUID
    last_command_number: int
    routing_status: RoutingStatus
    hosting_status: HostingStatus
    live_release_id: UUID | None
    live_release_digest: str | None
    unfinished_command: bool
    subdomain: str


class ReconcileStore(Protocol):
    """The reconciler's reads, connected as ``provisioning_worker``. Read only, no locks; each
    method is one short transaction."""

    async def projects_after(self, after: UUID | None, limit: int) -> tuple[ProjectRecord, ...]:
        """Up to ``limit`` projects with an ID above ``after`` (from the first when None), in
        project ID order."""
        ...

    async def known_projects(self, project_ids: Sequence[UUID]) -> frozenset[UUID]:
        """The IDs among ``project_ids`` that have a ``project_hosting`` row."""
        ...

    async def release_statuses(
        self, project_id: UUID, release_ids: Sequence[UUID]
    ) -> Mapping[UUID, ReleaseStatus]:
        """The status of each of the project's releases among ``release_ids``; an ID without a
        row of this project is left out."""
        ...

    async def still_idle(self, project_id: UUID, last_command_number: int) -> bool:
        """Whether the project is still stable, still at ``last_command_number``, and without an
        unfinished command: read right before each write."""
        ...
