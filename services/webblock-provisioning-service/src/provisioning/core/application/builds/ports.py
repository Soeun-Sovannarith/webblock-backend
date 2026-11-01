"""Build attempt types and the build store port (decision 0008 section 4, decision 0014 section 3
"Build").

A build is not a command: no fence, no dispatch row. Build writes lock the release, then the step,
and never the hosting row. Clock rule (decision 0008 section 6): every BuildStore transaction reads
the database clock once and uses it for every timestamp it writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.domain import (
    BuildAttemptPolicy,
    BuildResult,
    InvalidId,
    MissingDeadline,
    Release,
    ReleaseStep,
)


@dataclass(frozen=True, slots=True)
class BuildStepKey:
    project_id: UUID
    release_id: UUID
    step_id: UUID

    def validate(self) -> None:
        """The three IDs are required (``InvalidId``)."""
        if self.project_id.int == 0 or self.release_id.int == 0 or self.step_id.int == 0:
            raise InvalidId("build step key needs project, release and step IDs")


@dataclass(frozen=True, slots=True)
class StartBuildParams:
    """``duration`` is the configured build duration: deadline_at = now + duration."""

    step: BuildStepKey
    duration: timedelta

    def validate(self) -> None:
        """The key, and duration >= 1 microsecond (``MissingDeadline``)."""
        self.step.validate()
        if self.duration < timedelta(microseconds=1):
            raise MissingDeadline("build duration must be at least one microsecond")


@dataclass(frozen=True, slots=True)
class BuildAttemptParams:
    """The policy and a fresh step ID for the next attempt, used only when one is queued."""

    policy: BuildAttemptPolicy
    next_step_id: UUID

    def validate(self) -> None:
        """The policy (``InvalidAttempt``) and a non-nil next step ID (``InvalidId``)."""
        self.policy.validate()
        if self.next_step_id.int == 0:
            raise InvalidId("next build attempt needs a step ID")


@dataclass(frozen=True, slots=True)
class BuildWrite:
    """What a build path wrote: the release and step as committed, and the next pending attempt
    when one was inserted."""

    release: Release
    step: ReleaseStep
    next_step: ReleaseStep | None = None


@dataclass(frozen=True, slots=True)
class OverdueBuild:
    """A running build step past its deadline when the sweeper's unlocked scan read it, with the
    build system's handle when one was stored (the build to cancel once the attempt failed)."""

    step: BuildStepKey
    attempt: int
    deadline_at: datetime
    log_ref: str | None = None


class SweepOutcome(StrEnum):
    FAILED = "failed"
    BUSY = "busy"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class BuildTarget:
    """A release and its build step with the highest attempt (None when it has none)."""

    release: Release
    step: ReleaseStep | None


@dataclass(frozen=True, slots=True)
class RunningBuild:
    """A running build step and the source it builds, as the poller's unlocked scan read it.
    ``log_ref`` is the build system's handle, None until it was stored."""

    step: BuildStepKey
    attempt: int
    git_commit_sha: str
    repository_id: int
    started_at: datetime
    deadline_at: datetime
    log_ref: str | None


@dataclass(frozen=True, slots=True)
class PendingBuild:
    """A pending build step that nothing started (QA-10, OD-51), as the unlocked scan read it.
    ``pending_since`` is when it became pending: the end of the attempt before it, or for a first
    attempt the release's last write (its acceptance)."""

    step: BuildStepKey
    attempt: int
    pending_since: datetime


@dataclass(frozen=True, slots=True)
class PendingBuilds:
    """The scan's result; ``now`` is the database clock of the scan."""

    now: datetime
    builds: tuple[PendingBuild, ...]


@dataclass(frozen=True, slots=True)
class RunningBuilds:
    """The scan's result; ``now`` is the database clock of the scan."""

    now: datetime
    builds: tuple[RunningBuild, ...]


class BuildStore(Protocol):
    """The build paths (decision 0008 section 4). Lock order: releases, then release_steps. Every
    query filters on the project ID. Every release write carries its expected status in the WHERE
    clause, every step write the step's expected status, each checked for exactly one row (zero is
    ``IllegalTransition``). Rows are validated on load (``CorruptRow``). Lock timeouts, deadlocks
    and serialization failures are ``Contention``."""

    async def start_attempt(self, params: StartBuildParams) -> BuildWrite:
        """Start a pending build attempt: lock the release, then the step (either missing is
        ``NotFound``); apply ``start_build_attempt`` with deadline now + duration; write the
        release only when it moved accepted -> building, and the step pending -> running."""
        ...

    async def apply_result(self, result: BuildResult, attempt: BuildAttemptParams) -> BuildWrite:
        """Apply a build result: lock the release and the step named by the result (a missing row
        is ``ResultMismatch``); ``apply_build_result`` without a failure code,
        ``apply_failed_build_result`` with one; write what changed and insert the next attempt."""
        ...

    async def scan_overdue(self, limit: int) -> list[OverdueBuild]:
        """Up to ``limit`` running build steps past their deadline, oldest first; no lock."""
        ...

    async def sweep_overdue(
        self, candidate: OverdueBuild, attempt: BuildAttemptParams
    ) -> SweepOutcome:
        """Fail one candidate with deadline_exceeded after re-checking it under the locks (the
        release locked SKIP LOCKED: ``BUSY``; the re-check failed: ``STALE``)."""
        ...

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        """The release of the project and its build step with the highest attempt, read without a
        lock. A missing release, or one of another project, is ``NotFound``. (Decision 0014.)"""
        ...

    async def record_log_ref(self, step: BuildStepKey, log_ref: str) -> bool:
        """Store the build system's handle on a running build step whose log_ref is null, in one
        statement conditional on status 'running' and log_ref IS NULL. True when written; False
        when the step is no longer running or already holds a log_ref (write-once).
        (Decision 0014.)"""
        ...

    async def scan_running(self, limit: int) -> RunningBuilds:
        """Up to ``limit`` running build steps with their release's commit SHA and repository ID,
        oldest start first, and the database clock; no lock. (Decision 0014.)"""
        ...

    async def scan_pending(self, limit: int, older_than: timedelta) -> PendingBuilds:
        """Up to ``limit`` pending build steps of accepted or building releases that have been
        pending for longer than ``older_than`` by the database clock, longest pending first, and
        that clock; no lock. (QA-10, OD-51.)"""
        ...


def record_build_event(
    audit: AuditLog,
    *,
    actor: str,
    action: AuditAction,
    step: BuildStepKey,
    result: AuditResult,
    failure_code: str | None,
    at: datetime,
) -> None:
    """One audit event of a build mutation: the platform's own build path (``SYSTEM``) acting on
    a release; a build is not a command, so there is no command ID."""
    audit.record(
        AuditEvent(
            actor_kind=AuditActorKind.SYSTEM,
            actor=actor,
            action=action,
            project_id=step.project_id,
            release_id=step.release_id,
            command_id=None,
            result=result,
            failure_code=failure_code,
            at=at,
            correlation_id=None,
        )
    )


def finished_at(write: BuildWrite) -> datetime:
    """When the attempt of ``write`` ended: the step's finish time (the database clock of the
    write), else the release's last write."""
    return write.step.finished_at or write.release.updated_at


class BuildRequestStore(Protocol):
    """What the API's build_release reads and sends (implemented on the API's own login,
    ``provisioning_api``, which may only read releases and steps)."""

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        """The release and its build step with the highest attempt; ``NotFound`` when the
        release is missing from the project."""
        ...

    async def build_wanted(self, project_id: UUID, release_id: UUID) -> None:
        """Send the build hint; any error means it was not sent."""
        ...
