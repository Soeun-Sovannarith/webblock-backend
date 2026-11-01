"""``AuditLog`` as JSON log lines (decision 0014 section 21 answer 3, section 23 item 5).

Each event is one line of the process's structured logger with ``audit=true`` and the event's
fields; an alert adds ``alert=true`` and is written at warning level. The Kind stack's log shipper
sends the lines to Loki, where the live tests query ``audit=true``. The logger's allow-list and
scrubbing apply as to every other line: UUIDs and times print as strings, and no field can carry a
token because the event has none.
"""

from __future__ import annotations

from typing import Final

from provisioning.core.application.shared.audit import AuditEvent
from provisioning.infrastructure.telemetry.logger import Logger

AUDIT_MESSAGE: Final = "audit"
ALERT_MESSAGE: Final = "audit alert"


class LoggerAuditLog:
    """Implements ``core.application.AuditLog`` over the structured logger ``new_logger`` builds.
    It never raises: the logger's processors drop or redact what they cannot write."""

    __slots__ = ("_log",)

    def __init__(self, logger: Logger) -> None:
        self._log = logger

    def record(self, event: AuditEvent) -> None:
        self._log.info(AUDIT_MESSAGE, audit=True, **_fields(event))

    def alert(self, event: AuditEvent) -> None:
        self._log.warning(ALERT_MESSAGE, audit=True, alert=True, **_fields(event))


def _fields(event: AuditEvent) -> dict[str, object]:
    """The event's fields under the logger's allow-listed names. An absent optional field (None)
    is not written; an absent correlation ID lets the logger add the current context's."""
    fields: dict[str, object] = {
        "actor_kind": event.actor_kind,
        "actor": event.actor,
        "action": event.action,
        "project_id": event.project_id,
        "release_id": event.release_id,
        "command_id": event.command_id,
        "result": event.result,
        "failure_code": event.failure_code,
        "at": event.at,
    }
    if event.correlation_id is not None:
        fields["correlation_id"] = event.correlation_id
    return fields
