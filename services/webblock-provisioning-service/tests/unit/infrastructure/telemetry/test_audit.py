"""LoggerAuditLog (decision 0014 section 21 answer 3, section 23 item 5): one JSON line per audit
event with ``audit=true`` (and ``alert=true`` for an alert), every field of the event under its own
name, UUIDs and times as strings, absent optional fields left out, and no credential."""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from provisioning.core.application import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.infrastructure.telemetry import (
    REDACTED,
    LoggerAuditLog,
    correlation_scope,
    new_logger,
)

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
AT = datetime(2026, 10, 9, 12, 30, 15, 250000, tzinfo=UTC)
FAKE_JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLW5vdC1yZWFs"

EVENT = AuditEvent(
    actor_kind=AuditActorKind.OPERATOR,
    actor="operator-ana",
    action=AuditAction.ROLLBACK,
    project_id=PROJECT,
    release_id=RELEASE,
    command_id=COMMAND,
    result=AuditResult.FAILED,
    failure_code="route_uncertain",
    at=AT,
    correlation_id="corr-42",
)


def _event(**changes: Any) -> AuditEvent:
    fields: dict[str, Any] = {
        "actor_kind": EVENT.actor_kind,
        "actor": EVENT.actor,
        "action": EVENT.action,
        "project_id": EVENT.project_id,
        "release_id": EVENT.release_id,
        "command_id": EVENT.command_id,
        "result": EVENT.result,
        "failure_code": EVENT.failure_code,
        "at": EVENT.at,
        "correlation_id": EVENT.correlation_id,
    }
    return AuditEvent(**(fields | changes))


def _write(method: str, event: AuditEvent) -> dict[str, Any]:
    stream = io.StringIO()
    audit: AuditLog = LoggerAuditLog(new_logger(stream, logging.INFO))
    getattr(audit, method)(event)
    (line,) = stream.getvalue().splitlines()
    record = json.loads(line)
    assert isinstance(record, dict)
    return record


def test_a_recorded_event_is_one_json_line_with_every_field() -> None:
    record = _write("record", EVENT)
    assert record.pop("time")
    assert record == {
        "level": "info",
        "msg": "audit",
        "audit": True,
        "actor_kind": "operator",
        "actor": "operator-ana",
        "action": "rollback",
        "project_id": str(PROJECT),
        "release_id": str(RELEASE),
        "command_id": str(COMMAND),
        "result": "failed",
        "failure_code": "route_uncertain",
        "at": "2026-10-09T12:30:15.250000+00:00",
        "correlation_id": "corr-42",
    }


def test_an_alert_also_carries_alert_true_at_warning_level() -> None:
    event = _event(
        actor_kind=AuditActorKind.SYSTEM,
        actor="worker",
        action=AuditAction.ROUTE_UNCERTAIN,
        release_id=None,
        failure_code="restore_failed",
        correlation_id=None,
    )
    record = _write("alert", event)
    assert (record["level"], record["msg"]) == ("warning", "audit alert")
    assert record["audit"] is True
    assert record["alert"] is True
    assert record["action"] == "route_uncertain"
    assert record["actor"] == "worker"


def test_absent_optional_fields_are_left_out_and_a_record_is_not_an_alert() -> None:
    event = _event(
        actor_kind=AuditActorKind.EVENT,
        actor="project",
        action=AuditAction.RELEASE_ACCEPTED,
        release_id=None,
        command_id=None,
        result=AuditResult.ACCEPTED,
        failure_code=None,
        correlation_id=None,
    )
    record = _write("record", event)
    for absent in ("release_id", "command_id", "failure_code", "correlation_id", "alert"):
        assert absent not in record, absent


def test_uuids_and_times_are_strings_and_no_value_is_redacted() -> None:
    record = _write("record", EVENT)
    for key in ("project_id", "release_id", "command_id", "at", "actor_kind", "actor", "result"):
        assert isinstance(record[key], str), key
    assert REDACTED not in record.values()
    assert datetime.fromisoformat(record["at"]) == AT


def test_no_token_field_is_written_and_token_shaped_text_is_scrubbed() -> None:
    record = _write("record", EVENT)
    assert not any(word in key for key in record for word in ("token", "secret", "password"))
    # A caller's mistake (a credential as the actor) still never reaches the line.
    leaky = _event(actor_kind=AuditActorKind.USER, actor=f"Bearer {FAKE_JWT}")
    assert FAKE_JWT not in json.dumps(_write("record", leaky))


def test_the_context_correlation_id_is_used_when_the_event_has_none() -> None:
    event = _event(actor_kind=AuditActorKind.AGENT, correlation_id=None)
    with correlation_scope("ctx-7"):
        record = _write("record", event)
    assert record["correlation_id"] == "ctx-7"
