"""promote_release (decision 0004 section 4): accept the promotion of a candidate_ready release;
consults Entitlement. The acceptance is ``CommandAcceptor.accept`` (decision 0025 CQ8)."""

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
class PromoteReleaseCommand(cqrs.Command[CommandAccepted]):
    """The verified principal, the release, the client's idempotency key and the call's
    correlation ID; ``actor_kind`` names the transport's caller in the audit event."""

    principal: Principal
    project_id: UUID
    release_id: UUID
    idempotency_key: str
    correlation_id: str
    actor_kind: AuditActorKind = AuditActorKind.USER


class PromoteReleaseHandler:
    __slots__ = ("_acceptor",)

    def __init__(self, acceptor: CommandAcceptor) -> None:
        self._acceptor = acceptor

    async def handle(self, command: PromoteReleaseCommand, /) -> CommandAccepted:
        """Accept the promotion of a candidate_ready release; consults Entitlement."""
        return await self._acceptor.accept(Action.PROMOTE_RELEASE, command)
