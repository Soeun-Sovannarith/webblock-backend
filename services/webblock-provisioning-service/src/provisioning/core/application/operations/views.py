"""Result views of the operations; their inputs are the commands and queries of
``commands/`` and ``queries/`` (decision 0025 CQ6). Views carry revision 19 names, which the
REST and MCP bodies use unchanged (task RN). No view carries a command's idempotency key, request
fingerprint or requester."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from provisioning.core.application.intake.ports import SourceEventStatus
from provisioning.core.application.jobs.ports import AcceptedCommand
from provisioning.core.application.operations.ports import (
    Deployment,
    ReleaseCursor,
    ReleaseWithSteps,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandStatus,
    HostingStatus,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
)

# The length limit of a correlation ID: the dispatch row's column and the envelope (1 to 128).
MAX_CORRELATION_ID_LEN = 128


@dataclass(frozen=True, slots=True)
class CommandAccepted:
    """The result of a mutation: the 202 body's command_id and status, the project for the
    status_url, and whether the command was already stored (for the logs only)."""

    command_id: UUID
    project_id: UUID
    status: CommandStatus
    replayed: bool


@dataclass(frozen=True, slots=True)
class CommandView:
    command_id: UUID
    project_id: UUID
    release_id: UUID | None
    kind: CommandKind
    command_number: int
    status: CommandStatus
    failure_code: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class CommandSummaryView:
    command_id: UUID
    kind: CommandKind
    status: CommandStatus


@dataclass(frozen=True, slots=True)
class ReleaseStepView:
    step_name: ReleaseStepName
    attempt: int
    status: ReleaseStepStatus
    started_at: datetime | None
    finished_at: datetime | None
    deadline_at: datetime | None
    failure_code: str | None


@dataclass(frozen=True, slots=True)
class ReleaseView:
    release_id: UUID
    project_id: UUID
    generation_request_id: UUID
    repository_id: int
    git_commit_sha: str
    container_image_digest: str | None
    status: ReleaseStatus
    failure_code: str | None
    failure_summary: str | None
    steps: tuple[ReleaseStepView, ...]
    created_at: datetime
    updated_at: datetime
    promoted_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeploymentView:
    project_id: UUID
    live_release_id: UUID | None
    last_command_number: int
    routing_status: RoutingStatus
    hosting_status: HostingStatus
    in_flight_command: CommandSummaryView | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReleaseSummaryView:
    """A release without its steps or failure text, as a listing returns it."""

    release_id: UUID
    project_id: UUID
    git_commit_sha: str
    container_image_digest: str | None
    status: ReleaseStatus
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    promoted_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReleasePage:
    """One page of releases, newest first; ``next_cursor`` is None on the last page."""

    items: tuple[ReleaseSummaryView, ...]
    next_cursor: ReleaseCursor | None


@dataclass(frozen=True, slots=True)
class CommandPage:
    """One page of commands, newest first; ``next_after`` (the last item's command number) is
    None on the last page."""

    items: tuple[CommandView, ...]
    next_after: int | None


@dataclass(frozen=True, slots=True)
class SourceEventView:
    """The outcome of the release's "source published" event: codes, counts and times only."""

    status: SourceEventStatus
    outcome_code: str | None
    attempts: int
    mismatched_repeat_count: int
    received_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class DiagnosticsView:
    """Diagnostics from rows that exist (decision 0014 section 21 ruling 6): bounded (the newest
    DIAGNOSTIC_STEP_LIMIT steps and DIAGNOSTIC_COMMAND_LIMIT commands, newest first, with
    ``*_truncated`` when older rows were left out) and redacted (no failure summary, log
    reference, idempotency key, fingerprint, requester, repository or commit)."""

    release_id: UUID
    project_id: UUID
    release_status: ReleaseStatus
    release_failure_code: str | None
    release_created_at: datetime
    release_updated_at: datetime
    release_promoted_at: datetime | None
    steps: tuple[ReleaseStepView, ...]
    steps_truncated: bool
    commands: tuple[CommandView, ...]
    commands_truncated: bool
    source_event: SourceEventView | None


class BuildDispatch(StrEnum):
    """What happened to a started build attempt at the build system."""

    DISPATCHED = "dispatched"  # the build system holds the build; its handle is in log_ref
    PENDING = "pending"  # the build system was unavailable; the build poller retries
    REJECTED = "rejected"  # the build system refused it; the attempt failed build_rejected


@dataclass(frozen=True, slots=True)
class BuildStarted:
    """The result of build_release and of the intake's automatic start."""

    project_id: UUID
    release_id: UUID
    step_id: UUID
    attempt: int
    dispatch: BuildDispatch


def new_command_view(command: Command) -> CommandView:
    return CommandView(
        command_id=command.id,
        project_id=command.project_id,
        release_id=command.release_id,
        kind=command.kind,
        command_number=command.command_number,
        status=command.status,
        failure_code=command.failure_code,
        created_at=command.created_at,
        updated_at=command.updated_at,
    )


def new_command_accepted(accepted: AcceptedCommand) -> CommandAccepted:
    return CommandAccepted(
        command_id=accepted.command.id,
        project_id=accepted.command.project_id,
        status=accepted.command.status,
        replayed=accepted.replayed,
    )


def new_step_view(s: ReleaseStep) -> ReleaseStepView:
    """A step without its log reference."""
    return ReleaseStepView(
        step_name=s.name,
        attempt=s.attempt,
        status=s.status,
        started_at=s.started_at,
        finished_at=s.finished_at,
        deadline_at=s.deadline_at,
        failure_code=s.failure_code,
    )


def new_release_summary_view(r: Release) -> ReleaseSummaryView:
    return ReleaseSummaryView(
        release_id=r.id,
        project_id=r.project_id,
        git_commit_sha=r.git_commit_sha,
        container_image_digest=r.container_image_digest,
        status=r.status,
        failure_code=r.failure_code,
        created_at=r.created_at,
        updated_at=r.updated_at,
        promoted_at=r.promoted_at,
    )


def new_release_view(release: ReleaseWithSteps) -> ReleaseView:
    """The view of a release and its steps, in the order the store returned them."""
    r = release.release
    return ReleaseView(
        release_id=r.id,
        project_id=r.project_id,
        generation_request_id=r.generation_request_id,
        repository_id=r.repository_id,
        git_commit_sha=r.git_commit_sha,
        container_image_digest=r.container_image_digest,
        status=r.status,
        failure_code=r.failure_code,
        failure_summary=r.failure_summary,
        steps=tuple(new_step_view(s) for s in release.steps),
        created_at=r.created_at,
        updated_at=r.updated_at,
        promoted_at=r.promoted_at,
    )


def new_deployment_view(deployment: Deployment) -> DeploymentView:
    h = deployment.hosting
    in_flight = deployment.in_flight
    return DeploymentView(
        project_id=h.project_id,
        live_release_id=h.live_release_id,
        last_command_number=h.last_command_number,
        routing_status=h.routing_status,
        hosting_status=h.hosting_status,
        in_flight_command=(
            None
            if in_flight is None
            else CommandSummaryView(
                command_id=in_flight.id, kind=in_flight.kind, status=in_flight.status
            )
        ),
        updated_at=h.updated_at,
    )
