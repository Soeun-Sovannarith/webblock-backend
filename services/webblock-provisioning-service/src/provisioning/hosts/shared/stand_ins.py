"""The prototype's stand-ins, built here and nowhere else (decision 0014 sections 3 and 21
ruling 10; task H2).

This module is the only one that may import ``provisioning.infrastructure.standins``: the
import-linter contract ``standins-only-through-hosts-stand-ins`` enforces it. Every builder takes
the run profile and refuses to construct anything when it is ``production``
(``StandinNotAvailable``), so a production process cannot end up with a stand-in even if its
configuration were bypassed. The production hosts build the closed adapters of
``hosts.shared.open_contracts`` instead.

- ``prototype-allow`` builds the allow-all classes.
- ``prototype-file`` builds the file-based, reloadable stand-ins over PROJECT_ACCESS_FILE or
  ENTITLEMENT_FILE, read once here: a file that cannot be read or is invalid at start-up is a
  configuration error (``StandinNotAvailable``, exit code 2). Later, an unusable file fails closed
  at each check.
- Any other mode never passes the configuration; it raises the same error here, so no mode
  silently means "allow".
- The repository file (REPOSITORY_MODE=prototype-file), the in-process Payment event record
  (HOSTING_ENTITLEMENT_MODE=prototype-payment-events) and the development tokens
  (IDENTITY_MODE=dev-tokens) are built here too.

The worker's run-time entitlement check (``Routing``, before a promote moves the route) uses the
same builder, so one ENTITLEMENT_FILE answers at acceptance and at run.

It imports no Kubernetes or broker client, so the API host may use it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from provisioning.core.application import (
    Entitlement,
    ProjectAccess,
    RepositoryLocator,
    RepositoryRegistry,
)
from provisioning.core.application.hosting.ports import HostingEntitlement
from provisioning.core.application.shared.profile import Profile
from provisioning.hosts.shared.runtime import InvalidSettings
from provisioning.infrastructure.config import PROTOTYPE_ALLOW, PROTOTYPE_FILE
from provisioning.infrastructure.config.files import ConfigFileError
from provisioning.infrastructure.config.worker import PROTOTYPE_PAYMENT_EVENTS
from provisioning.infrastructure.standins import (
    PrototypeAllowEntitlement,
    PrototypeAllowProjectAccess,
    PrototypeFileEntitlement,
    PrototypeFileProjectAccess,
    PrototypeReloadingRepositoryFile,
)
from provisioning.infrastructure.standins.dev_tokens import PrototypeDevTokenIdentity
from provisioning.infrastructure.standins.entitlement_record import (
    DeletionLookup,
    PrototypeHostingRecord,
)
from provisioning.infrastructure.telemetry import Logger


class StandinNotAvailable(InvalidSettings):
    """A stand-in this process may not construct (the production profile, or a mode no builder
    knows), or its file cannot be used. The text names the key, never a value or a path."""


class Repositories(RepositoryRegistry, RepositoryLocator, Protocol):
    """The repository file: Project's registry and the clone URL in one stand-in."""


class PaymentEventRecord(HostingEntitlement, Protocol):
    """The in-process record of the latest Payment event per project: the hosting executor reads
    it, the Payment-event consumer writes it."""

    def record(self, project_id: UUID, *, event_key: str, entitled: bool) -> None: ...


def _prototype_only(profile: Profile, key: str) -> None:
    if profile is not Profile.PROTOTYPE:
        raise StandinNotAvailable(f"{key} selects a prototype stand-in, refused in production")


def build_entitlement(
    profile: Profile, mode: str, path: Path | None, logger: Logger
) -> Entitlement:
    """The entitlement stand-in of ENTITLEMENT_MODE (``path`` is ENTITLEMENT_FILE)."""
    _prototype_only(profile, "ENTITLEMENT_MODE")
    if mode == PROTOTYPE_ALLOW:
        return PrototypeAllowEntitlement(logger)
    if mode == PROTOTYPE_FILE and path is not None:
        try:
            return PrototypeFileEntitlement.from_file(path, logger)
        except ConfigFileError:
            raise StandinNotAvailable(
                "ENTITLEMENT_FILE cannot be read or is invalid (ENTITLEMENT_MODE=prototype-file)"
            ) from None
    raise StandinNotAvailable("ENTITLEMENT_MODE names a stand-in this build cannot construct")


def build_project_access(
    profile: Profile, mode: str, path: Path | None, logger: Logger
) -> ProjectAccess:
    """The project-access stand-in of PROJECT_ACCESS_MODE (``path`` is PROJECT_ACCESS_FILE)."""
    _prototype_only(profile, "PROJECT_ACCESS_MODE")
    if mode == PROTOTYPE_ALLOW:
        return PrototypeAllowProjectAccess(logger)
    if mode == PROTOTYPE_FILE and path is not None:
        try:
            return PrototypeFileProjectAccess.from_file(path, logger)
        except ConfigFileError:
            raise StandinNotAvailable(
                "PROJECT_ACCESS_FILE cannot be read or is invalid "
                "(PROJECT_ACCESS_MODE=prototype-file)"
            ) from None
    raise StandinNotAvailable("PROJECT_ACCESS_MODE names a stand-in this build cannot construct")


def read_repositories(profile: Profile, mode: str, path: Path | None) -> Repositories:
    """The reloadable repository file of REPOSITORY_MODE=prototype-file
    (PROTOTYPE_REPOSITORIES_FILE). ``ConfigFileError`` (never with the file's contents) when it
    cannot be read or is invalid."""
    _prototype_only(profile, "REPOSITORY_MODE")
    if mode != PROTOTYPE_FILE or path is None:
        raise StandinNotAvailable("REPOSITORY_MODE names a stand-in this build cannot construct")
    return PrototypeReloadingRepositoryFile.from_file(path)


def build_payment_event_record(
    profile: Profile, mode: str, *, max_projects: int, deletions: DeletionLookup
) -> PaymentEventRecord:
    """The in-process Payment event record of HOSTING_ENTITLEMENT_MODE=prototype-payment-events,
    which also knows deleted projects (``deletions``)."""
    _prototype_only(profile, "HOSTING_ENTITLEMENT_MODE")
    if mode != PROTOTYPE_PAYMENT_EVENTS:
        raise StandinNotAvailable(
            "HOSTING_ENTITLEMENT_MODE names a stand-in this build cannot construct"
        )
    return PrototypeHostingRecord(max_projects=max_projects, deletions=deletions)


def read_dev_tokens(profile: Profile, path: Path | None) -> PrototypeDevTokenIdentity:
    """The development tokens of IDENTITY_MODE=dev-tokens (AUTH_DEV_TOKENS_FILE).
    ``ConfigFileError`` (never with the file's contents) when it cannot be read or is invalid."""
    _prototype_only(profile, "IDENTITY_MODE")
    if path is None:
        raise StandinNotAvailable("AUTH_DEV_TOKENS_FILE is required for IDENTITY_MODE=dev-tokens")
    return PrototypeDevTokenIdentity.from_file(path)
