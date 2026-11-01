"""The run profile and the typed refusals of a production build (task H2, phase P-H).

Every process reads the required key PROVISIONING_PROFILE: ``prototype`` runs the prototype's
stand-ins (decision 0014 section 3); ``production`` refuses every stand-in and fails closed where
a contract with another service is still open (OD-01 project access and repository confirmation,
OD-04 entitlement):

- the API refuses every mutation with ``OpenContract``, which names the open contracts; reads
  keep their permission and project-access checks;
- project access denies (``ProjectAccessClosed``), and entitlement, the hosting entitlement, the
  repository registry and the repository locator raise ``ContractNotDecided``: each is an
  explicit typed error, never a placeholder success;
- the worker enables no command kind, so a command that appears anyway fails
  ``operation_unavailable`` (the consumer's existing refusal).

This module holds the values and the errors; which adapter a host builds for each profile is the
hosts' business (``hosts.shared.open_contracts`` and ``hosts.shared.stand_ins``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from provisioning.core.application.shared.errors import (
    DependencyNotConfigured,
    OperationDisabled,
    ProjectAccessDenied,
)

OD_PROJECT: Final = "OD-01"
"""Project's API: the access check and the repository confirmation."""

OD_PAYMENT: Final = "OD-04"
"""Payment: the entitlement check, its events and the usage event."""

OD_CATALOG: Final = "OD-46"
"""The catalog owner: the site's catalog row and the access administrator credential."""


class Profile(StrEnum):
    """The value of PROVISIONING_PROFILE. There is no default: a process without it refuses to
    start."""

    PROTOTYPE = "prototype"
    PRODUCTION = "production"


def _contract_list(contracts: tuple[str, ...]) -> str:
    return ", ".join(contracts)


class OpenContract(OperationDisabled):
    """unavailable, 503, operation_unavailable: the operation is refused in a production build
    because a contract it needs is still open. ``action`` is the operation ID and ``contracts``
    the open decisions; both are fixed values, never request content."""

    def __init__(self, action: str, contracts: tuple[str, ...]) -> None:
        if not contracts:
            raise ValueError("open contract: at least one contract is required")
        self.action = action
        self.contracts = contracts
        super().__init__(
            f"{action} is refused in production: its contracts are open "
            f"({_contract_list(contracts)})"
        )


class ProjectAccessClosed(ProjectAccessDenied):
    """Project access in a production build while Project's access API is open (OD-01): every
    request is denied. The transports conceal it as not found, as any denial."""

    def __init__(self) -> None:
        self.contract = OD_PROJECT
        super().__init__(f"project access denied in production: contract {OD_PROJECT} is open")


class ContractNotDecided(DependencyNotConfigured):
    """A port whose production adapter does not exist because its contract is open: entitlement
    and the hosting entitlement (OD-04), the repository registry and locator (OD-01). Not
    transient: the answer stays the same until the contract is decided. ``port`` names the port,
    ``contract`` the open decision."""

    def __init__(self, port: str, contract: str) -> None:
        self.port = port
        self.contract = contract
        super().__init__(f"{port} has no production implementation: contract {contract} is open")
