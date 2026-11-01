"""The project_hosting row (revision 19)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.enums.states import (
    HostingStatus,
    OfflineReason,
    RoutingStatus,
    parse_hosting_status,
    parse_offline_reason,
    parse_routing_status,
)
from provisioning.core.domain.errors import IllegalTransition, InvalidCommandNumber, InvalidId
from provisioning.core.domain.values.hosting_reason import hosting_status_for
from provisioning.core.domain.values.identifiers import (
    MAX_COMMAND_NUMBER,
    validate_last_command_number,
)
from provisioning.core.domain.values.text import validate_subdomain


@dataclass(frozen=True, slots=True)
class ProjectHosting:
    """The columns of provisioning.project_hosting: the live release, the only fencing counter,
    the routing status and the hosting status (decision 0001 section 3)."""

    project_id: UUID
    last_command_number: int
    routing_status: RoutingStatus
    hosting_status: HostingStatus
    updated_at: datetime
    live_release_id: UUID | None = None
    offline_reasons: frozenset[OfflineReason] = frozenset()
    owner_status_version: int = 0
    subdomain: str = field(kw_only=True)
    """The site's DNS label, copied once from Project when the row is inserted (migration 00020;
    NOT NULL from 00021, after the operator filled the older rows). Required, by keyword."""

    def validate(self) -> None:
        """Check a row loaded from storage: a non-nil project ID, a non-nil live release ID when one
        is set, the command number range, the enum labels, the hosting status against the offline
        reasons (offline exactly when one is held), the owner status version range and the
        sub-domain."""
        if self.project_id.int == 0:
            raise InvalidId("project ID is required")
        if self.live_release_id is not None and self.live_release_id.int == 0:
            raise InvalidId(f"live release of project {self.project_id}")
        validate_last_command_number(self.last_command_number)
        parse_routing_status(self.routing_status)
        parse_hosting_status(self.hosting_status)
        for reason in self.offline_reasons:
            parse_offline_reason(reason)
        if self.hosting_status != hosting_status_for(self.offline_reasons):
            raise IllegalTransition(
                f"hosting row of project {self.project_id} is {self.hosting_status} "
                f"with {len(self.offline_reasons)} offline reasons"
            )
        if not 0 <= self.owner_status_version <= MAX_COMMAND_NUMBER:
            raise InvalidCommandNumber(
                f"owner status version {self.owner_status_version} outside 0..{MAX_COMMAND_NUMBER}"
            )
        validate_subdomain(self.subdomain)


def new_project_hosting(project_id: UUID, now: datetime, *, subdomain: str) -> ProjectHosting:
    """The hosting row of a project's first release: command number 0, routing stable, hosting
    online, no live release (decision 0004 section 5), and the sub-domain the repository lookup
    answered (decision 0021 D8). A nil project ID is ``InvalidId``; a sub-domain that is not a
    DNS label is ``InvalidSubdomain``."""
    if project_id.int == 0:
        raise InvalidId("project ID is required")
    validate_subdomain(subdomain)
    return ProjectHosting(
        project_id=project_id,
        last_command_number=0,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=now,
        subdomain=subdomain,
    )


def new_owner_status_hosting(
    project_id: UUID, now: datetime, *, suspended: bool, version: int, subdomain: str
) -> ProjectHosting:
    """The hosting row an owner-status event inserts for a project that has none yet (decision
    0021): command number 0, routing stable, no live release, the event's version and sub-domain
    (Notion: the event carries it), and offline with ``user_suspended`` when the owner is
    suspended, else online with no reason. A nil project ID is ``InvalidId``; a version outside 1
    to 2^53-1 is ``InvalidCommandNumber``; a sub-domain that is not a DNS label is
    ``InvalidSubdomain``."""
    if project_id.int == 0:
        raise InvalidId("project ID is required")
    validate_subdomain(subdomain)
    if not 1 <= version <= MAX_COMMAND_NUMBER:
        raise InvalidCommandNumber(
            f"owner status version {version} outside 1..{MAX_COMMAND_NUMBER}"
        )
    reasons = frozenset({OfflineReason.USER_SUSPENDED}) if suspended else frozenset()
    return ProjectHosting(
        project_id=project_id,
        last_command_number=0,
        routing_status=RoutingStatus.STABLE,
        hosting_status=hosting_status_for(reasons),
        updated_at=now,
        offline_reasons=reasons,
        owner_status_version=version,
        subdomain=subdomain,
    )


_ROUTING_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (RoutingStatus.STABLE, RoutingStatus.CHANGING),
        (RoutingStatus.CHANGING, RoutingStatus.STABLE),
        (RoutingStatus.CHANGING, RoutingStatus.UNCERTAIN),
        (RoutingStatus.UNCERTAIN, RoutingStatus.CHANGING),
    }
)
_HOSTING_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (HostingStatus.ONLINE, HostingStatus.OFFLINE),
        (HostingStatus.OFFLINE, HostingStatus.ONLINE),
    }
)


def legal_routing_transition(from_: RoutingStatus, to: RoutingStatus) -> bool:
    """stable -> changing, changing -> stable, changing -> uncertain, uncertain -> changing."""
    return (str(from_), str(to)) in _ROUTING_TRANSITIONS


def legal_hosting_transition(from_: HostingStatus, to: HostingStatus) -> bool:
    """online -> offline (a verified suspend) and offline -> online (a verified resume)."""
    return (str(from_), str(to)) in _HOSTING_TRANSITIONS
