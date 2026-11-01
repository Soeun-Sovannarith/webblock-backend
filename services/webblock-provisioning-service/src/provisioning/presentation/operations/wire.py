"""The REST and MCP bodies (decision 0023 G2, G3, G4, G7).

Every name a client sees is camelCase: each snake_case name of the views becomes its camelCase form
(``releaseId``, ``gitCommitSha``, ``failureRetryable``, ``statusUrl``), and the repository ID is
``repositoryId`` (decision 0021 D3). The values of enums (``live``, ``candidate_ready``) and the
failure codes are values, not names, and go out unchanged.

Optional fields are omitted when absent; lists are never null. ``failureRetryable`` (task H5a) goes
with every ``failureCode``, the release's, the command's and every step's, and only with one: the
failure-code catalogue's flag for the code, False for a code it does not list. No body has a field
for a command's idempotency key or request fingerprint (decision 0006 section 2), and only the
admin read's ``AdminCommand`` shows the requester, as ``requestedBySubject`` (decision 0023 G9): the
client ``Command`` encoder lists its fields one by one and never emits it, whatever view it is
handed.

A REST success is wrapped by ``envelope``; MCP sends the ``data`` object as it is, and for a list
``list_result``: ``{"items": <data>, "page": <meta.page>}``, because an MCP result is an object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from provisioning.core.application import (
    AdminCommandPage,
    AdminCommandView,
    CommandAccepted,
    CommandPage,
    CommandView,
    DeploymentView,
    DiagnosticsView,
    ReleasePage,
    ReleaseStepView,
    ReleaseSummaryView,
    ReleaseView,
)
from provisioning.core.domain import failure_retryable
from provisioning.presentation.operations.operations import status_url
from provisioning.presentation.operations.paging import command_cursor, release_cursor

type Body = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Page:
    """A list's page: the limit it was read with and the cursor of the next page, None on the
    last page (decision 0023 G4)."""

    limit: int
    next_cursor: str | None

    def as_json(self) -> Body:
        return {"limit": self.limit, "nextCursor": self.next_cursor}


def timestamp(value: datetime) -> str:
    """RFC 3339 in UTC with the fraction the stored value has (Go's RFC3339Nano)."""
    utc = value.astimezone(UTC)
    text = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        text += "." + f"{utc.microsecond:06d}".rstrip("0")
    return text + "Z"


def meta_timestamp(value: datetime) -> str:
    """RFC 3339 in UTC with exactly three fraction digits (milliseconds) and ``Z``."""
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + f".{utc.microsecond // 1000:03d}Z"


def envelope(
    data: Body | list[Body],
    *,
    request_id: str,
    trace_id: str | None,
    path: str,
    status: int,
    page: Page | None,
    now: datetime,
) -> Body:
    """The REST success body (Notion 2.1, decision 0023 G2): ``success``, ``data`` and ``meta``
    with ``requestId``, ``traceId`` (only when the request is sampled), ``timestamp`` (UTC,
    milliseconds), ``path`` (no query), ``status`` and ``page`` (null except on lists)."""
    meta: Body = {"requestId": request_id}
    if trace_id is not None:
        meta["traceId"] = trace_id
    meta["timestamp"] = meta_timestamp(now)
    meta["path"] = path
    meta["status"] = status
    meta["page"] = None if page is None else page.as_json()
    return {"success": True, "data": data, "meta": meta}


def list_result(items: list[Body], page: Page) -> Body:
    """The MCP result of a list tool (decision 0023 G7): the REST ``data`` and ``meta.page``."""
    return {"items": items, "page": page.as_json()}


def _put(body: Body, key: str, value: object) -> None:
    if value is not None:
        body[key] = value


def _put_failure(body: Body, key: str, code: str | None) -> None:
    """``key`` and ``failureRetryable`` when there is a code; neither otherwise."""
    if code is not None:
        body[key] = code
        body["failureRetryable"] = failure_retryable(code)


def _opt_time(value: datetime | None) -> str | None:
    return None if value is None else timestamp(value)


def accepted_body(accepted: CommandAccepted) -> Body:
    """The 202 body of a command mutation (CommandAccepted)."""
    return {
        "commandId": str(accepted.command_id),
        "status": accepted.status.value,
        "statusUrl": status_url(accepted.project_id, accepted.command_id),
    }


def command_body(view: CommandView) -> Body:
    """The Command schema."""
    body: Body = {
        "commandId": str(view.command_id),
        "projectId": str(view.project_id),
    }
    _put(body, "releaseId", None if view.release_id is None else str(view.release_id))
    body["kind"] = view.kind.value
    body["commandNumber"] = view.command_number
    body["status"] = view.status.value
    _put_failure(body, "failureCode", view.failure_code)
    body["createdAt"] = timestamp(view.created_at)
    body["updatedAt"] = timestamp(view.updated_at)
    return body


def admin_command_body(view: AdminCommandView) -> Body:
    """The AdminCommand schema (decision 0023 G9): the Command schema's fields, then
    ``requestedBySubject`` when the command has a requester (an admin's or a client's subject; a
    command from an event has none)."""
    body = command_body(view)
    _put(body, "requestedBySubject", view.requested_by_subject)
    return body


def _step_body(step: ReleaseStepView) -> Body:
    """The ReleaseStep schema."""
    entry: Body = {
        "stepName": step.step_name.value,
        "attempt": step.attempt,
        "status": step.status.value,
    }
    _put(entry, "startedAt", _opt_time(step.started_at))
    _put(entry, "finishedAt", _opt_time(step.finished_at))
    _put(entry, "deadlineAt", _opt_time(step.deadline_at))
    _put_failure(entry, "failureCode", step.failure_code)
    return entry


def release_body(view: ReleaseView) -> Body:
    """The Release schema, steps in the order of the view."""
    steps = [_step_body(step) for step in view.steps]
    body: Body = {
        "releaseId": str(view.release_id),
        "projectId": str(view.project_id),
        "generationRequestId": str(view.generation_request_id),
        "repositoryId": view.repository_id,
        "gitCommitSha": view.git_commit_sha,
    }
    _put(body, "containerImageDigest", view.container_image_digest)
    body["status"] = view.status.value
    _put_failure(body, "failureCode", view.failure_code)
    _put(body, "failureSummary", view.failure_summary)
    body["steps"] = steps
    body["createdAt"] = timestamp(view.created_at)
    body["updatedAt"] = timestamp(view.updated_at)
    _put(body, "promotedAt", _opt_time(view.promoted_at))
    return body


def deployment_body(view: DeploymentView) -> Body:
    """The Deployment schema."""
    body: Body = {"projectId": str(view.project_id)}
    _put(
        body,
        "liveReleaseId",
        None if view.live_release_id is None else str(view.live_release_id),
    )
    body["lastCommandNumber"] = view.last_command_number
    body["routingStatus"] = view.routing_status.value
    body["hostingStatus"] = view.hosting_status.value
    if view.in_flight_command is not None:
        c = view.in_flight_command
        body["inFlightCommand"] = {
            "commandId": str(c.command_id),
            "kind": c.kind.value,
            "status": c.status.value,
        }
    body["updatedAt"] = timestamp(view.updated_at)
    return body


def diagnostics_body(view: DiagnosticsView) -> Body:
    """The Diagnostics schema: codes, counts and times only."""
    release: Body = {"status": view.release_status.value}
    _put_failure(release, "failureCode", view.release_failure_code)
    release["createdAt"] = timestamp(view.release_created_at)
    release["updatedAt"] = timestamp(view.release_updated_at)
    _put(release, "promotedAt", _opt_time(view.release_promoted_at))
    body: Body = {
        "releaseId": str(view.release_id),
        "projectId": str(view.project_id),
        "release": release,
        "steps": [_step_body(step) for step in view.steps],
        "stepsTruncated": view.steps_truncated,
        "commands": [command_body(command) for command in view.commands],
        "commandsTruncated": view.commands_truncated,
    }
    source = view.source_event
    if source is not None:
        event: Body = {"status": source.status.value}
        _put(event, "outcomeCode", source.outcome_code)
        event["attempts"] = source.attempts
        event["mismatchedRepeatCount"] = source.mismatched_repeat_count
        event["receivedAt"] = timestamp(source.received_at)
        _put(event, "finishedAt", _opt_time(source.finished_at))
        body["sourceEvent"] = event
    return body


def _release_summary_body(view: ReleaseSummaryView) -> Body:
    """The ReleaseSummary schema."""
    body: Body = {
        "releaseId": str(view.release_id),
        "projectId": str(view.project_id),
        "gitCommitSha": view.git_commit_sha,
    }
    _put(body, "containerImageDigest", view.container_image_digest)
    body["status"] = view.status.value
    _put_failure(body, "failureCode", view.failure_code)
    body["createdAt"] = timestamp(view.created_at)
    body["updatedAt"] = timestamp(view.updated_at)
    _put(body, "promotedAt", _opt_time(view.promoted_at))
    return body


def release_list_body(page: ReleasePage, *, limit: int) -> tuple[list[Body], Page]:
    """The ReleaseSummary items of one page and its ``meta.page``; ``nextCursor`` is None on the
    last page."""
    after = page.next_cursor
    return (
        [_release_summary_body(item) for item in page.items],
        Page(limit=limit, next_cursor=None if after is None else release_cursor(after)),
    )


def command_list_body(page: CommandPage, *, limit: int) -> tuple[list[Body], Page]:
    """The Command items of one page and its ``meta.page``; ``nextCursor`` is None on the last
    page."""
    after = page.next_after
    return (
        [command_body(item) for item in page.items],
        Page(limit=limit, next_cursor=None if after is None else command_cursor(after)),
    )


def admin_command_list_body(page: AdminCommandPage, *, limit: int) -> tuple[list[Body], Page]:
    """The AdminCommand items of one page of the admin read and its ``meta.page``, paged like
    ``command_list_body``."""
    after = page.next_after
    return (
        [admin_command_body(item) for item in page.items],
        Page(limit=limit, next_cursor=None if after is None else command_cursor(after)),
    )
