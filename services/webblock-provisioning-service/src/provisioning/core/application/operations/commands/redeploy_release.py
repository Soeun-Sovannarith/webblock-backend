"""redeploy_release (decision 0023 G9 to G11): an admin's redeploy of the live release
(``AdminAcceptor.redeploy``), with its audit event (decision 0025 CQ8). Only an ``ADMIN`` takes
it, on any project."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.admin import AdminAcceptor
from provisioning.core.application.operations.auditing import Auditor
from provisioning.core.application.operations.views import CommandAccepted
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.audit import AuditAction, AuditActorKind
from provisioning.core.application.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class RedeployReleaseCommand(cqrs.Command[CommandAccepted]):
    """The verified principal, the project and the admin's idempotency key; ``correlation_id``
    is the call's, or empty (the command ID is used); ``actor_kind`` names the transport's
    caller in the audit event."""

    principal: Principal
    project_id: UUID
    idempotency_key: str
    correlation_id: str = ""
    actor_kind: AuditActorKind = AuditActorKind.USER


class RedeployReleaseHandler:
    __slots__ = ("_admin", "_auditor")

    def __init__(self, *, admin: AdminAcceptor, auditor: Auditor) -> None:
        self._admin = admin
        self._auditor = auditor

    async def handle(self, command: RedeployReleaseCommand, /) -> CommandAccepted:
        """An admin's redeploy of the live release (``AdminAcceptor.redeploy``)."""
        return await self._auditor.admin(
            command.principal,
            AuditAction.REDEPLOY,
            command.project_id,
            command.correlation_id,
            command.actor_kind,
            lambda: self._admin.redeploy(
                command.principal,
                command.project_id,
                command.idempotency_key,
                correlation_id=command.correlation_id,
            ),
        )
