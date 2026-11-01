"""What each process builds per run profile where a contract with another service is still open
(task H2; OD-01 Project, OD-04 Payment).

``prototype`` builds the stand-ins through ``hosts.shared.stand_ins`` (the only importer of
``infrastructure.standins``). ``production`` builds the closed adapters here, each failing with an
explicit typed error from ``core.application.shared.profile``, never a placeholder success:

- project access denies every request (``ProjectAccessClosed``, concealed as not found), unless
  PROJECT_ACCESS_MODE=project-service or project-grpc, whose adapters the API host builds
  (``hosts.api.wiring``);
- entitlement, the hosting entitlement, the repository registry and the repository locator raise
  ``ContractNotDecided``;
- the API refuses every mutation with ``OpenContract`` (``api_operations``), the admin ones
  included, and keeps its reads, the admin read included (decision 0023 G13);
- the worker enables no command kind (``worker_kinds``), so a command that appears anyway fails
  ``operation_unavailable`` without any executor running.

The per-process decisions for the build worker and the bootstrap process are listed where they
are wired (``hosts.build_worker.wiring``, ``infrastructure.config.bootstrap``).

This module imports no Kubernetes or broker client, so every host may use it.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Final
from uuid import UUID

from provisioning.core.application import (
    Action,
    EnabledKinds,
    EnabledOperations,
    Entitlement,
    Principal,
    ProjectAccess,
    RegisteredRepository,
)
from provisioning.core.application.shared.profile import (
    OD_PAYMENT,
    OD_PROJECT,
    ContractNotDecided,
    Profile,
    ProjectAccessClosed,
)
from provisioning.core.domain import CommandKind
from provisioning.hosts.shared import stand_ins
from provisioning.infrastructure.telemetry import Logger

PROTOTYPE_OPERATIONS: Final = tuple(Action)
"""The operations the prototype exposes: all fourteen, for its flows (decision 0014 section 2:
publish, build, deploy, promote, rollback and the MCP tools; section 21 rulings 6 and 7:
diagnostics and the two listings; decision 0023 G13: the four admin operations). Decision 0006
section 6 makes this a value in code, not a configuration key, so that enabling an operation is a
reviewed change."""

PRODUCTION_REFUSED: Final = MappingProxyType(
    {
        Action.DEPLOY_RELEASE: (OD_PROJECT, OD_PAYMENT),
        Action.PROMOTE_RELEASE: (OD_PROJECT, OD_PAYMENT),
        Action.ROLLBACK_RELEASE: (OD_PROJECT,),
        Action.BUILD_RELEASE: (OD_PROJECT,),
        Action.SUSPEND_WEBSITE: (OD_PROJECT, OD_PAYMENT),
        Action.RESUME_WEBSITE: (OD_PROJECT, OD_PAYMENT),
        Action.REDEPLOY_RELEASE: (OD_PROJECT, OD_PAYMENT),
    }
)
"""Every mutation and the open contracts it needs in production: project access (OD-01) for the
four client mutations, entitlement (OD-04) for deploy and promote. Rollback never consults
entitlement; a build needs Project's repository confirmation (OD-01). The three admin mutations
(decision 0023 G13) ask neither, but the worker runs no command kind while OD-01 and OD-04 are
open (``PRODUCTION_KINDS``), so they name both."""

PRODUCTION_OPERATIONS: Final = tuple(a for a in Action if a not in PRODUCTION_REFUSED)
"""The reads, which keep their permission and project-access checks (and so are denied while
project access is closed), and the admin read, which only an ``ADMIN`` takes (decision 0023
G13)."""

PROTOTYPE_KINDS: Final = tuple(k for k in CommandKind if k is not CommandKind.SYNC_DOMAINS)
"""Every command kind but ``sync_domains`` is executed in the prototype. Decision 0014 section 23
removed custom domains, so ``sync_domains`` stays only to mirror the schema. ``redeploy`` joined in
Phase C with its executor (decision 0023 G12, G13; it waited under decision 0021 D10). A value in
code, not a configuration key, so that enabling a kind is a reviewed change (decision 0006
section 6)."""

PRODUCTION_KINDS: Final[tuple[CommandKind, ...]] = ()
"""No command kind runs in production while OD-01 and OD-04 are open."""


def api_operations(profile: Profile) -> EnabledOperations:
    """The operations of the API process: all in the prototype; the reads in production, with
    each mutation refused by ``OpenContract`` naming its contracts."""
    if profile is Profile.PRODUCTION:
        return EnabledOperations(PRODUCTION_OPERATIONS, refused=PRODUCTION_REFUSED)
    return EnabledOperations(PROTOTYPE_OPERATIONS)


def worker_kinds(profile: Profile) -> EnabledKinds:
    """The command kinds the worker executes: none in production."""
    if profile is Profile.PRODUCTION:
        return EnabledKinds(PRODUCTION_KINDS)
    return EnabledKinds(PROTOTYPE_KINDS)


class ClosedProjectAccess:
    """Project access in production: denies every request (OD-01)."""

    async def check_access(self, principal: Principal, project_id: UUID, action: Action) -> None:
        raise ProjectAccessClosed()


class ClosedEntitlement:
    """Entitlement in production: never entitled (OD-04)."""

    async def check(self, project_id: UUID, kind: CommandKind) -> None:
        raise ContractNotDecided("entitlement", OD_PAYMENT)


class ClosedHostingEntitlement:
    """The hosting entitlement in production: Payment's events are open (OD-04)."""

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        raise ContractNotDecided("hosting entitlement", OD_PAYMENT)


class ClosedRepositories:
    """Project's repository registry and the clone URL in production (OD-01)."""

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        raise ContractNotDecided("repository registry", OD_PROJECT)

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        raise ContractNotDecided("repository locator", OD_PROJECT)


def project_access(profile: Profile, mode: str, path: Path | None, logger: Logger) -> ProjectAccess:
    """PROJECT_ACCESS_MODE's stand-in in the prototype; ``ClosedProjectAccess`` in production.
    The project-service mode never comes here: the API host builds its adapter."""
    if profile is Profile.PRODUCTION:
        return ClosedProjectAccess()
    return stand_ins.build_project_access(profile, mode, path, logger)


def entitlement(profile: Profile, mode: str, path: Path | None, logger: Logger) -> Entitlement:
    """ENTITLEMENT_MODE's stand-in in the prototype; ``ClosedEntitlement`` in production."""
    if profile is Profile.PRODUCTION:
        return ClosedEntitlement()
    return stand_ins.build_entitlement(profile, mode, path, logger)
