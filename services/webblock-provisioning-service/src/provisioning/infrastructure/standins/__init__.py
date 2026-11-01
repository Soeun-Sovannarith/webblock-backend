"""Prototype stand-ins for the external ports of B1 (decision 0014 sections 3, 10.2 and 21 ruling
10). Every class name starts with ``Prototype``; each is selected only by an explicit
configuration value (PROJECT_ACCESS_MODE, ENTITLEMENT_MODE, REPOSITORY_MODE). Real adapters
replace them once OD-01 and OD-04 are settled."""

from provisioning.infrastructure.standins.access import (
    AccessFileError,
    PrototypeAllowEntitlement,
    PrototypeAllowProjectAccess,
    PrototypeFileProjectAccess,
    ReloadingFile,
)
from provisioning.infrastructure.standins.entitlement_file import (
    EntitlementFileError,
    PrototypeFileEntitlement,
)
from provisioning.infrastructure.standins.entitlement_record import PrototypePaymentEventRecord
from provisioning.infrastructure.standins.repositories import (
    PrototypeRepositoryFile,
    RepositoryFileError,
)
from provisioning.infrastructure.standins.repository_reload import (
    PrototypeReloadingRepositoryFile,
)

__all__ = [
    "AccessFileError",
    "EntitlementFileError",
    "PrototypeAllowEntitlement",
    "PrototypeAllowProjectAccess",
    "PrototypeFileEntitlement",
    "PrototypeFileProjectAccess",
    "PrototypePaymentEventRecord",
    "PrototypeReloadingRepositoryFile",
    "PrototypeRepositoryFile",
    "ReloadingFile",
    "RepositoryFileError",
]
