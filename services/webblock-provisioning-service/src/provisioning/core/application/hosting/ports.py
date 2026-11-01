"""What the hosting intake, jobs and retry need from our database and from Payment: the
entitlement, the hosting command and owner-status stores, the hosting history and the types that
cross them (cut from the former hosting and owner_status modules, decision 0025 CQ6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.core.application.jobs.ports import AcceptedCommand
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    OfflineReason,
    owner_status_version_of_key,
)


class HostingEntitlement(Protocol):
    """What Payment said about a project's hosting after a given event (OD-04). Consulted when a
    hosting command runs, outside any transaction."""

    async def entitlement_after(self, project_id: UUID, event_key: str) -> bool | None:
        """The entitlement named by the latest Payment event of ``project_id`` known to have come
        after the event ``event_key``: True (restored), False (ended), or None when that is not
        known (no later event, or the event ``event_key`` itself is unknown). Any error means
        unknown and transient."""
        ...


class HostingCommandStore(Protocol):
    """Accepts a hosting command keyed by a Payment event (consumer-defined; implemented by
    ``infrastructure.postgres.HostingIntakeStore`` on a login of ``provisioning_hosting_intake``
    alone). The store makes the unlocked lookup by the event key (same kind: the stored command,
    ``replayed=True``; other kind: ``IdempotencyConflict``), then the acceptance transaction with
    ``new_acceptance_decider`` (``OperationInProgress``, ``RouteNotStable``, ``NotFound`` without
    a hosting row)."""

    async def accept_hosting_command(
        self,
        *,
        project_id: UUID,
        kind: CommandKind,
        event_key: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> AcceptedCommand: ...


@dataclass(frozen=True, slots=True)
class HostingIntent:
    """The latest hosting command of one reason, and the hosting row's reasons and owner-status
    version, as the retry reads them."""

    project_id: UUID
    kind: CommandKind
    idempotency_key: str
    status: CommandStatus
    hosting_reason: OfflineReason
    offline_reasons: frozenset[OfflineReason]
    owner_status_version: int

    def met(self) -> bool:
        """Whether the command's reason is already as it asked: held after a suspend, not held
        after a resume."""
        held = self.hosting_reason in self.offline_reasons
        return held if self.kind is CommandKind.SUSPEND_HOSTING else not held

    def still_current(self) -> bool:
        """Whether a newer intent of the same source has not replaced this one: a
        ``user_suspended`` command is current only while its key's version is the row's
        ``owner_status_version``; every other reason is current."""
        if self.hosting_reason is not OfflineReason.USER_SUSPENDED:
            return True
        return owner_status_version_of_key(self.idempotency_key) == self.owner_status_version


class HostingHistory(Protocol):
    """What the retry reads (consumer-defined; implemented by
    ``infrastructure.postgres.hosting_intake.PgHostingHistory`` on the hosting intake's login)."""

    async def latest_hosting_intents(self, project_id: UUID) -> tuple[HostingIntent, ...]:
        """The project's hosting command with the highest command number of each reason, the
        oldest command number first; empty without hosting commands."""
        ...

    async def projects_to_retry(self, *, limit: int, budget: int) -> tuple[UUID, ...]:
        """At most ``limit`` projects, each once, with a reason whose latest hosting command
        failed, is not met, is still current, whose key's retry attempt is below ``budget`` and
        whose next retry key fits ``MAX_IDEMPOTENCY_KEY_LEN`` bytes, whose route is stable and
        which have no command in flight; the oldest failure first."""
        ...


class OwnerStatus(StrEnum):
    """The owner's status an owner-status event carries."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class OwnerStatusOutcome(StrEnum):
    ACCEPTED = "accepted"  # a suspend or resume was accepted and the version recorded
    REPLAYED = "replayed"  # the key's command was already stored; nothing written
    ROW_INSERTED = "row_inserted"  # no hosting row: one was inserted with the version, no command
    STALE = "stale"  # the row holds this version or a newer one; nothing written


@dataclass(frozen=True, slots=True)
class OwnerStatusResult:
    """The outcome, and the command for ``ACCEPTED`` and ``REPLAYED`` (else None)."""

    outcome: OwnerStatusOutcome
    accepted: AcceptedCommand | None


class OwnerStatusStore(Protocol):
    """Applies one owner-status event (consumer-defined; implemented by
    ``infrastructure.postgres.HostingIntakeStore`` on a login of ``provisioning_hosting_intake``
    alone), in the steps of the module docstring, in one transaction."""

    async def accept_owner_status(
        self,
        *,
        project_id: UUID,
        status: OwnerStatus,
        version: int,
        subdomain: str,
        command_id: UUID,
        now: datetime,
        correlation_id: str,
        deadline_at: datetime,
    ) -> OwnerStatusResult: ...
