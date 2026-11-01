"""Audit events (decision 0014 section 21 answer 3, section 23 item 5).

Audit means structured audit log events: who acted, on what, when, and with what result, for every
mutation and every operator action. There is no audit table (OD-14 stays open for one); the
infrastructure writes each event as one JSON log line with ``audit=true``, which the Kind stack
ships to Loki. An alert (the uncertain route, section 23) is an audit event that also carries
``alert=true``; it is not a broker message.

The use cases depend on the ``AuditLog`` protocol only; the hosts inject the implementation
(``infrastructure.telemetry.LoggerAuditLog``), and tests use ``NullAuditLog`` or their own record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class AuditActorKind(StrEnum):
    """Who caused the event."""

    USER = "user"  # a person through the REST API
    AGENT = "agent"  # an agent through the MCP tools
    OPERATOR = "operator"  # an operator tool, such as the inbox re-drive or an operator rollback
    SYSTEM = "system"  # the platform's own loops: the worker, the reconciler, the build poller
    EVENT = "event"  # another service's event: Project's source published, Payment's hosting


class AuditAction(StrEnum):
    """What happened. One name per mutation or operator action of the prototype."""

    DEPLOY = "deploy"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    REDEPLOY = "redeploy"  # an admin's redeploy of the live release (decision 0023 G9)
    SUSPEND_HOSTING = "suspend_hosting"
    RESUME_HOSTING = "resume_hosting"
    BUILD_RELEASE = "build_release"
    RELEASE_ACCEPTED = "release_accepted"  # a release accepted from Project's "source published"
    BUILD_ATTEMPT_STARTED = "build_attempt_started"
    BUILD_ATTEMPT_FINISHED = "build_attempt_finished"
    BUILD_CANCELLED = "build_cancelled"  # an overdue attempt's build aborted in the build system
    WORKLOAD_CLEANUP = "workload_cleanup"  # a superseded, rolled-back or failed workload deleted
    ROUTE_RECLAIM = "route_reclaim"  # the operator rollback's forced apply of the site's route
    INBOX_REDRIVE = "inbox_redrive"  # an operator re-drove an inbox event whose budget ran out
    SUBDOMAIN_BACKFILL = "subdomain_backfill"  # an operator set an older hosting row's sub-domain
    DLQ_REDRIVE = "dlq_redrive"  # an operator moved a parked message back to its queue
    USAGE_EVENT_PUBLISHED = "usage_event_published"  # "publish completed" sent to Payment
    ROUTE_UNCERTAIN = "route_uncertain"  # the alert: a project's route could not be confirmed
    # access_setup gave an existing site role a new password (decision 0014 section 24)
    CATALOG_CREDENTIAL_ROTATED = "catalog_credential_rotated"


class AuditResult(StrEnum):
    """How it ended."""

    ACCEPTED = "accepted"  # a command or release was accepted; its outcome is a later event
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"  # refused before any change: authorization, entitlement, state
    SKIPPED = "skipped"  # a command that was no longer needed


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One audit event.

    ``actor`` names who acted without any credential: a token's subject, an agent's client ID, an
    operator's login, or a component name such as ``worker``; never a token, password or key.
    ``failure_code`` is a short machine code (``release_steps.failure_code`` style), never error
    text. ``at`` is timezone-aware. ``correlation_id`` is the request's or message's correlation
    ID when there is one."""

    actor_kind: AuditActorKind
    actor: str
    action: AuditAction
    project_id: UUID
    release_id: UUID | None
    command_id: UUID | None
    result: AuditResult
    failure_code: str | None
    at: datetime
    correlation_id: str | None


class AuditLog(Protocol):
    """Where audit events go. Neither method raises: an audit write never fails the mutation it
    describes."""

    def record(self, event: AuditEvent) -> None:
        """Write one audit event."""
        ...

    def alert(self, event: AuditEvent) -> None:
        """Write one audit event that also asks for attention (``alert=true``)."""
        ...


class NullAuditLog:
    """An ``AuditLog`` that drops every event, for wiring and tests that do not observe audit."""

    __slots__ = ()

    def record(self, event: AuditEvent) -> None:
        del event

    def alert(self, event: AuditEvent) -> None:
        del event
