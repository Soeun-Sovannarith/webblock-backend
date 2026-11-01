"""rollback_release (decision 0004 section 4, decision 0014 section 13): accept operator recovery of
an uncertain route; never consults Entitlement. The acceptance is ``CommandAcceptor.accept`` (design
2026-10-28 CQ8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from provisioning.core.application.operations.acceptance import CommandAcceptor
from provisioning.core.application.operations.views import CommandAccepted
from provisioning.core.application.shared import cqrs
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.audit import AuditActorKind
from provisioning.core.application.shared.principal import Principal


@dataclass(frozen=True, slots=True)
class RollbackReleaseCommand(cqrs.Command[CommandAccepted]):
    """The verified principal, the release, the client's idempotency key and the call's
    correlation ID; ``actor_kind`` names the transport's caller in the audit event."""

    principal: Principal
    project_id: UUID
    release_id: UUID
    idempotency_key: str
    correlation_id: str
    actor_kind: AuditActorKind = AuditActorKind.USER


class RollbackReleaseHandler:
    __slots__ = ("_acceptor",)

    def __init__(self, acceptor: CommandAcceptor) -> None:
        self._acceptor = acceptor

    async def handle(self, command: RollbackReleaseCommand, /) -> CommandAccepted:
        """Accept operator recovery of an uncertain route; never consults Entitlement."""
        return await self._acceptor.accept(Action.ROLLBACK_RELEASE, command)
