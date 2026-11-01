"""Row mappers: database rows to validated domain values.

A row is converted field by field and validated by the domain, because a column holds whatever the
table admits. Every defect is ``CorruptRow`` raised ``from None``: the domain's text (IDs,
statuses and kinds only) is kept, the error is not chained, so a transport never classifies a
stored row's defect as the caller's mistake. Times are returned in UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import RowMapping

from provisioning.core.application import (
    CorruptRow,
    InboxEntry,
    SourceEventOutcome,
    parse_source_event_status,
)
from provisioning.core.application.hosting.ports import HostingIntent
from provisioning.core.domain import (
    Command,
    DomainError,
    OfflineReason,
    ProjectHosting,
    Release,
    ReleaseStep,
    SourcePublished,
    parse_command_kind,
    parse_command_status,
    parse_hosting_status,
    parse_offline_reason,
    parse_release_status,
    parse_release_step_name,
    parse_release_step_status,
    parse_routing_status,
)

type Row = RowMapping


def corrupt(what: str, exc: BaseException) -> CorruptRow:
    """The error of a row that failed validation; raise it ``from None``."""
    return CorruptRow(f"postgres: {what}: {exc}")


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def utc_or_none(value: datetime | None) -> datetime | None:
    return None if value is None else value.astimezone(UTC)


def _id(value: object, column: str) -> UUID:
    if not isinstance(value, UUID):
        raise CorruptRow(f"postgres: column {column} is not a UUID")
    return value


def _id_or_none(value: object, column: str) -> UUID | None:
    return None if value is None else _id(value, column)


def _reason_or_none(value: object) -> OfflineReason | None:
    return None if value is None else parse_offline_reason(value)


def offline_reasons_of(value: object, project_id: UUID) -> frozenset[OfflineReason]:
    """``offline_reasons`` as a set. The column has no NULL and no duplicate (its checks); a value
    that is not a list, or that repeats a reason, is a corrupt row."""
    if not isinstance(value, list):
        raise CorruptRow(f"postgres: offline_reasons of project {project_id} is not an array")
    reasons = frozenset(parse_offline_reason(v) for v in value)
    if len(reasons) != len(value):
        raise CorruptRow(f"postgres: offline_reasons of project {project_id} repeats a reason")
    return reasons


def reasons_value(reasons: frozenset[OfflineReason]) -> list[str]:
    """The ``offline_reasons`` value of a set: a list in ``OfflineReason`` declaration order, so
    the stored array of one set is always the same."""
    return [r.value for r in OfflineReason if r in reasons]


def command_of(row: Row) -> Command:
    try:
        command = Command(
            id=_id(row["command_id"], "command_id"),
            project_id=_id(row["project_id"], "project_id"),
            release_id=_id_or_none(row["release_id"], "release_id"),
            kind=parse_command_kind(row["kind"]),
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            command_number=row["command_number"],
            status=parse_command_status(row["status"]),
            created_at=utc(row["created_at"]),
            updated_at=utc(row["updated_at"]),
            failure_code=row["failure_code"],
            requested_by_subject=row["requested_by_subject"],
            hosting_reason=_reason_or_none(row["hosting_reason"]),
        )
        command.validate()
    except DomainError as exc:
        raise corrupt(f"command {row['command_id']}", exc) from None
    return command


def hosting_of(row: Row) -> ProjectHosting:
    try:
        hosting = ProjectHosting(
            project_id=_id(row["project_id"], "project_id"),
            last_command_number=row["last_command_number"],
            routing_status=parse_routing_status(row["routing_status"]),
            hosting_status=parse_hosting_status(row["hosting_status"]),
            updated_at=utc(row["updated_at"]),
            live_release_id=_id_or_none(row["live_release_id"], "live_release_id"),
            offline_reasons=offline_reasons_of(row["offline_reasons"], row["project_id"]),
            owner_status_version=row["owner_status_version"],
            subdomain=row["subdomain"],
        )
        hosting.validate()
    except DomainError as exc:
        raise corrupt(f"hosting row of project {row['project_id']}", exc) from None
    return hosting


def hosting_intent_of(row: Row, project_id: UUID) -> HostingIntent:
    """The latest hosting command of one reason joined to its hosting row. A hosting command
    always has a reason (``commands_hosting_reason_by_kind``)."""
    if row["hosting_reason"] is None:
        raise CorruptRow(f"postgres: hosting command of project {project_id} has no offline reason")
    try:
        return HostingIntent(
            project_id=project_id,
            kind=parse_command_kind(row["kind"]),
            idempotency_key=row["idempotency_key"],
            status=parse_command_status(row["status"]),
            hosting_reason=parse_offline_reason(row["hosting_reason"]),
            offline_reasons=offline_reasons_of(row["offline_reasons"], project_id),
            owner_status_version=row["owner_status_version"],
        )
    except DomainError as exc:
        raise corrupt(f"hosting command of project {project_id}", exc) from None


def release_of(row: Row) -> Release:
    try:
        release = Release(
            id=_id(row["release_id"], "release_id"),
            project_id=_id(row["project_id"], "project_id"),
            generation_request_id=_id(row["generation_request_id"], "generation_request_id"),
            repository_id=row["repository_id"],
            git_commit_sha=row["git_commit_sha"],
            status=parse_release_status(row["status"]),
            created_at=utc(row["created_at"]),
            updated_at=utc(row["updated_at"]),
            container_image_digest=row["container_image_digest"],
            failure_code=row["failure_code"],
            failure_summary=row["failure_summary"],
            promoted_at=utc_or_none(row["promoted_at"]),
        )
        release.validate()
    except DomainError as exc:
        raise corrupt(f"release {row['release_id']}", exc) from None
    return release


def step_of(row: Row) -> ReleaseStep:
    try:
        step = ReleaseStep(
            id=_id(row["release_step_id"], "release_step_id"),
            release_id=_id(row["release_id"], "release_id"),
            name=parse_release_step_name(row["step_name"]),
            attempt=row["attempt"],
            status=parse_release_step_status(row["status"]),
            started_at=utc_or_none(row["started_at"]),
            finished_at=utc_or_none(row["finished_at"]),
            deadline_at=utc_or_none(row["deadline_at"]),
            failure_code=row["failure_code"],
            log_ref=row["log_ref"],
        )
        step.validate()
    except DomainError as exc:
        raise corrupt(f"release step {row['release_step_id']}", exc) from None
    return step


def event_of(row: Row) -> InboxEntry:
    """A source_published_events row; ``InboxEntry.validate`` raises ``CorruptRow`` itself."""
    try:
        status = parse_source_event_status(row["status"])
    except DomainError as exc:
        raise corrupt("source published event", exc) from None
    entry = InboxEntry(
        event=SourcePublished(
            project_id=_id(row["project_id"], "project_id"),
            generation_request_id=_id(row["generation_request_id"], "generation_request_id"),
            repository_id=row["repository_id"],
            git_commit_sha=row["git_commit_sha"],
        ),
        status=status,
        attempts=row["attempts"],
        next_attempt_at=utc(row["next_attempt_at"]),
        received_at=utc(row["received_at"]),
        updated_at=utc(row["updated_at"]),
        max_attempts=row["max_attempts"],
        outcome_code=row["outcome_code"],
        release_id=_id_or_none(row["release_id"], "release_id"),
        mismatched_repeat_count=row["mismatched_repeat_count"],
        last_mismatched_repeat_at=utc_or_none(row["last_mismatched_repeat_at"]),
        finished_at=utc_or_none(row["finished_at"]),
        last_manual_retry_by=row["last_manual_retry_by"],
        last_manual_retry_at=utc_or_none(row["last_manual_retry_at"]),
    )
    try:
        entry.validate()
    except CorruptRow as exc:
        raise CorruptRow(str(exc)) from None
    return entry


def source_event_outcome_of(row: Row) -> SourceEventOutcome:
    """A source_published_events row as the outcome a diagnosis shows."""
    try:
        attempts = int(row["attempts"])
        repeats = int(row["mismatched_repeat_count"])
        if attempts < 0 or repeats < 0:
            raise ValueError("negative count")
        return SourceEventOutcome(
            project_id=_uuid(row["project_id"]),
            generation_request_id=_uuid(row["generation_request_id"]),
            status=parse_source_event_status(row["status"]),
            outcome_code=_text_or_none(row["outcome_code"]),
            release_id=None if row["release_id"] is None else _uuid(row["release_id"]),
            attempts=attempts,
            mismatched_repeat_count=repeats,
            received_at=utc(row["received_at"]),
            finished_at=utc_or_none(row["finished_at"]),
        )
    except (DomainError, ValueError, TypeError) as exc:
        raise corrupt("source published event", exc) from None


def _uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("not a UUID")
    return value


def _text_or_none(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("not a text")
