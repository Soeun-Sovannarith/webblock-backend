"""External and infrastructure ports, each defined by the use case that consumes it (decision
0004 sections 2 and 10). The lead's ports for the build system and the cluster are
in ``provisioning.core.application.ports``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import CommandKind


class ActionPolicy(Protocol):
    """Whether a principal's token carries an action: the first check of every operation. Local
    (configured scope mapping), so it is synchronous."""

    def permit(self, principal: Principal, action: Action) -> None:
        """Return when permitted; raise ``ActionDenied`` when not."""
        ...


class ProjectAccess(Protocol):
    """Whether a principal may act on a project (OD-01); consulted by every operation."""

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        """Return when allowed; ``ProjectAccessDenied`` when not; any other error when access
        could not be determined (reported as ``ProjectAccessUnavailable``)."""
        ...


class Entitlement(Protocol):
    """Whether a project may deploy or promote now (OD-04); takes no principal."""

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        """Return when entitled; ``LimitReached`` when a Payment limit refuses it; any other error
        when entitlement could not be determined."""
        ...


@dataclass(frozen=True, slots=True)
class RegisteredRepository:
    """The repository lookup's answer for a project (decision 0021 D8): the repository ID Project
    registered and the project's sub-domain, which the first release copies once into
    ``project_hosting.subdomain``. The release intake checks both values."""

    repository_id: int
    subdomain: str


class RepositoryRegistry(Protocol):
    """Which repository Project has registered for a project, with its sub-domain (OD-01,
    decision 0008 section 3, decision 0021 D8); consulted by the inbox processor outside any
    transaction."""

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        """The registered repository and sub-domain, or None when the project has none. Any error
        means the answer is unknown: ``DependencyNotConfigured`` is not transient; anything else
        is."""
        ...


class RepositoryLocator(Protocol):
    """Where the build system clones a release's source from (decision 0014 section 3, "What
    Provisioning sends"). Revision 19 stores only the GitHub repository ID, so the URL comes from
    this port: Project's registry in production (OD-01), a configured location in the prototype."""

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        """The clone URL. Any error means it is unknown for now (``DependencyUnavailable``)."""
        ...


class Clock(Protocol):
    """Supplies every timestamp the application stores: UTC, truncated to microseconds."""

    def now(self) -> datetime: ...


class IdSource(Protocol):
    """Supplies new identifiers: fresh non-nil UUIDv7 values."""

    def new_id(self) -> UUID: ...
