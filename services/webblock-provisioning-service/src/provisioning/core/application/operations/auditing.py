"""The audit of the operations' mutations (decision 0014 section 21 answer 3; cut from the former
Service class, decision 0025 CQ8).

Every mutation (the command mutations, the three admin mutations and build_release) records one
audit event when a ``ServiceAudit`` is given: accepted, refused (a refusal before any change:
permission, project access, validation, entitlement, state, conflict) or failed (a dependency or
store fault). The actor is the principal's subject, never a token; the failure code is the error
class in snake case (``project_access_denied``, ``idempotency_conflict``), never its text. Reads
are not audited.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from provisioning.core.application.operations.views import CommandAccepted
from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.shared.action import ADMIN_ROLE
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.application.shared.errors import (
    ActionDenied,
    ApplicationError,
    BuildInProgress,
    IdempotencyKeyTooLong,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    ProjectAccessDenied,
    RedeployNotAllowed,
    ReleaseNotBuildable,
    Unauthenticated,
)
from provisioning.core.application.shared.principal import Principal
from provisioning.core.domain import DomainError


@dataclass(frozen=True, slots=True)
class ServiceAudit:
    """Where the operations record the audit event of every mutation, and the clock of its
    time."""

    log: AuditLog
    clock: Clock


@dataclass(slots=True)
class OwnerCheck:
    """Whether the gate skipped the owner check of a command because an ``ADMIN`` acts on any
    project (decision 0019 section 7); set by the acceptance, read by its audit."""

    skipped: bool = False


@dataclass(frozen=True, slots=True)
class AuditContext:
    """What the audit event of a mutation names before its outcome is known."""

    actor_kind: AuditActorKind
    actor: str
    action: AuditAction
    project_id: UUID
    release_id: UUID | None
    correlation_id: str | None

    def as_operator(self) -> AuditContext:
        """The same context with the actor kind ``OPERATOR``: an ADMIN's operator rollback."""
        return replace(self, actor_kind=AuditActorKind.OPERATOR)

    def event(
        self,
        *,
        result: AuditResult,
        failure_code: str | None,
        command_id: UUID | None,
        clock: Clock,
    ) -> AuditEvent:
        return AuditEvent(
            actor_kind=self.actor_kind,
            actor=self.actor,
            action=self.action,
            project_id=self.project_id,
            release_id=self.release_id,
            command_id=command_id,
            result=result,
            failure_code=failure_code,
            at=clock.now(),
            correlation_id=self.correlation_id,
        )


class Auditor:
    """Runs a mutation and records its audit event. ``audit`` None records nothing (tests that do
    not observe audit)."""

    __slots__ = ("_audit",)

    def __init__(self, audit: ServiceAudit | None) -> None:
        self._audit = audit

    async def run[T](
        self,
        run: Callable[[], Awaitable[T]],
        context: AuditContext,
        command_of: Callable[[T], UUID | None],
        owner_check: OwnerCheck | None = None,
    ) -> T:
        """Run one mutation and record its audit event, whatever the outcome. The event never
        changes the outcome: the audit log does not raise. When ``owner_check`` says an ``ADMIN``
        skipped the owner check, the event's actor kind is ``OPERATOR`` (finding M-2); the actor
        stays the verified subject."""
        audit = self._audit
        try:
            result = await run()
        except Exception as exc:
            if owner_check is not None and owner_check.skipped:
                context = context.as_operator()
            if audit is not None:
                audit.log.record(
                    context.event(
                        result=AuditResult.REFUSED if _is_refusal(exc) else AuditResult.FAILED,
                        failure_code=_audit_code(exc),
                        command_id=None,
                        clock=audit.clock,
                    )
                )
            raise
        if owner_check is not None and owner_check.skipped:
            context = context.as_operator()
        if audit is not None:
            audit.log.record(
                context.event(
                    result=AuditResult.ACCEPTED,
                    failure_code=None,
                    command_id=command_of(result),
                    clock=audit.clock,
                )
            )
        return result

    async def admin(
        self,
        principal: Principal,
        action: AuditAction,
        project_id: UUID,
        correlation_id: str,
        actor_kind: AuditActorKind,
        run: Callable[[], Awaitable[CommandAccepted]],
    ) -> CommandAccepted:
        """Run an admin mutation with its audit event. The release is not known before the
        acceptance, so the event names none. An ``ADMIN`` acts on any project without the owner
        check, so the event names an operator (finding M-2); anyone else is refused by the gate
        and keeps the transport's actor kind."""
        return await self.run(
            run,
            AuditContext(
                actor_kind=actor_kind,
                actor=principal.subject,
                action=action,
                project_id=project_id,
                release_id=None,
                correlation_id=correlation_id or None,
            ),
            lambda accepted: accepted.command_id,
            OwnerCheck(skipped=principal.has_scope(ADMIN_ROLE)),
        )


# Errors that refuse a mutation before any change; any other error is a failure.
_REFUSALS: Final = (
    ActionDenied,
    ProjectAccessDenied,
    NotFound,
    LimitReached,
    OperationInProgress,
    OperationDisabled,
    OperationNotAvailable,
    Unauthenticated,
    ReleaseNotBuildable,
    BuildInProgress,
    RedeployNotAllowed,
    IdempotencyKeyTooLong,
    DomainError,
)
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _is_refusal(exc: Exception) -> bool:
    return isinstance(exc, _REFUSALS)


def _audit_code(exc: Exception) -> str:
    """The error class in snake case for an application or domain error, else ``internal``:
    never the error's text."""
    if not isinstance(exc, (ApplicationError, DomainError)):
        return "internal"
    return _CAMEL_BOUNDARY.sub("_", type(exc).__name__).lower()[:100]
