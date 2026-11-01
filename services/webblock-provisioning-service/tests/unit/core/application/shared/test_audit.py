"""The audit declarations (decision 0014 section 21 answer 3): the action, actor and result names
the wave needs, and a null log that accepts and drops every event."""

from datetime import UTC, datetime
from uuid import UUID

from provisioning.core.application import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
    NullAuditLog,
)


def test_the_names_are_the_wire_values_of_the_audit_lines() -> None:
    assert {k.value for k in AuditActorKind} == {"user", "agent", "operator", "system", "event"}
    assert {r.value for r in AuditResult} == {
        "accepted",
        "succeeded",
        "failed",
        "refused",
        "skipped",
    }
    assert {a.value for a in AuditAction} == {
        "deploy",
        "promote",
        "rollback",
        "redeploy",
        "suspend_hosting",
        "resume_hosting",
        "build_release",
        "release_accepted",
        "build_attempt_started",
        "build_attempt_finished",
        "build_cancelled",
        "workload_cleanup",
        "route_reclaim",
        "inbox_redrive",
        "subdomain_backfill",
        "dlq_redrive",
        "usage_event_published",
        "route_uncertain",
        "catalog_credential_rotated",
    }


def test_the_null_log_accepts_records_and_alerts() -> None:
    log: AuditLog = NullAuditLog()
    event = AuditEvent(
        actor_kind=AuditActorKind.SYSTEM,
        actor="worker",
        action=AuditAction.WORKLOAD_CLEANUP,
        project_id=UUID("0190a1b2-0000-7000-8000-000000000001"),
        release_id=None,
        command_id=None,
        result=AuditResult.SUCCEEDED,
        failure_code=None,
        at=datetime(2026, 10, 9, tzinfo=UTC),
        correlation_id=None,
    )
    log.record(event)
    log.alert(event)
    assert NullAuditLog.__slots__ == ()  # no state: nothing is kept
