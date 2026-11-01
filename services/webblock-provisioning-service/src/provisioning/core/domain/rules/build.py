"""Build attempts (decision 0008 section 4, decision 0014 section 3 "Build")."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.entities.release import Release
from provisioning.core.domain.entities.release_step import ReleaseStep, validate_attempt
from provisioning.core.domain.enums.states import ReleaseStatus, ReleaseStepName, ReleaseStepStatus
from provisioning.core.domain.errors import (
    IllegalTransition,
    ImmutableField,
    MissingDeadline,
    ResultMismatch,
)
from provisioning.core.domain.values.build import (
    CODE_BUILD_ATTEMPTS_EXHAUSTED,
    BuildAttemptPolicy,
    BuildResult,
)
from provisioning.core.domain.values.release_transition import ReleaseTransition
from provisioning.core.domain.values.step_input import StepInput


@dataclass(frozen=True, slots=True)
class BuildAttemptFailed:
    """Rule result. The rows after a failed build attempt: the release (failed when the attempts are
    spent, unchanged otherwise), the failed step, and the next pending attempt when one is
    queued."""

    release: Release
    step: ReleaseStep
    next_step: ReleaseStep | None


def start_build_attempt(
    release: Release, step: ReleaseStep, deadline: datetime, now: datetime
) -> tuple[Release, ReleaseStep]:
    """Start a pending build attempt: attempt 1 needs the release accepted and moves it to
    building; a later attempt needs it building. The step goes pending -> running with
    started_at = now and deadline_at = deadline, which must be after now (``MissingDeadline``); a
    pending step that already holds a deadline is ``ImmutableField``. Anything else is
    ``IllegalTransition`` (``InvalidAttempt`` for an attempt below 1)."""
    _own_build_step(release, step)
    if step.status != ReleaseStepStatus.PENDING:
        raise IllegalTransition(
            f"build step {step.id} of release {release.id} is {step.status}, not pending"
        )
    validate_attempt(step.attempt)
    want = ReleaseStatus.ACCEPTED if step.attempt == 1 else ReleaseStatus.BUILDING
    if release.status != want:
        raise IllegalTransition(
            f"build attempt {step.attempt} of release {release.id} needs the release {want}, "
            f"not {release.status}"
        )
    if step.deadline_at is not None:
        raise ImmutableField(f"build step {step.id} already holds a deadline")
    if not deadline > now:
        raise MissingDeadline(f"deadline of build step {step.id} is not after its start")
    moved = (
        release.transition_unchecked(ReleaseStatus.BUILDING, ReleaseTransition(), now)
        if step.attempt == 1
        else release
    )
    started = step.transition(ReleaseStepStatus.RUNNING, StepInput(deadline_at=deadline), now)
    return moved, started


def apply_build_result(
    release: Release, step: ReleaseStep, result: BuildResult, now: datetime
) -> tuple[Release, ReleaseStep]:
    """The one entry point for a successful build result (decision 0001 section 7 as amended by
    decision 0008 section 4). A result that does not name the release's running build attempt and
    its source is ``ResultMismatch``; one with a failure code is ``IllegalTransition``. The step
    goes running -> succeeded and the release building -> built with the digest."""
    _match_build_result(release, step, result)
    if result.failure_code != "":
        raise IllegalTransition(
            f"build result for release {release.id} carries a failure code; apply it as a failure"
        )
    built = release.transition_unchecked(
        ReleaseStatus.BUILT,
        ReleaseTransition(container_image_digest=result.container_image_digest),
        now,
    )
    succeeded = step.transition(ReleaseStepStatus.SUCCEEDED, StepInput(log_ref=result.log_ref), now)
    return built, succeeded


def apply_failed_build_result(
    release: Release,
    step: ReleaseStep,
    result: BuildResult,
    policy: BuildAttemptPolicy,
    next_step_id: UUID,
    now: datetime,
) -> BuildAttemptFailed:
    """Apply a result that carries a failure code: the same match as ``apply_build_result``, then
    a digest is ``ImmutableField``, and the attempt fails as ``fail_build_attempt`` says with the
    result's code and log reference."""
    _match_build_result(release, step, result)
    if result.container_image_digest is not None:
        raise ImmutableField(
            f"a failed build result for release {release.id} carries an image digest"
        )
    return _fail_build_attempt(
        release, step, result.failure_code, result.log_ref, policy, next_step_id, now
    )


def fail_build_attempt(
    release: Release,
    step: ReleaseStep,
    reason: str,
    policy: BuildAttemptPolicy,
    next_step_id: UUID,
    now: datetime,
) -> BuildAttemptFailed:
    """Fail a running build attempt (decision 0008 section 4). Below ``policy.max_attempts`` the
    next attempt is returned as a new pending step with ``next_step_id`` and the release is
    unchanged; at the maximum the release goes building -> failed with
    CODE_BUILD_ATTEMPTS_EXHAUSTED and no next step is returned."""
    return _fail_build_attempt(release, step, reason, "", policy, next_step_id, now)


def _fail_build_attempt(
    release: Release,
    step: ReleaseStep,
    reason: str,
    log_ref: str,
    policy: BuildAttemptPolicy,
    next_step_id: UUID,
    now: datetime,
) -> BuildAttemptFailed:
    policy.validate()
    _own_build_step(release, step)
    if release.status != ReleaseStatus.BUILDING or step.status != ReleaseStepStatus.RUNNING:
        raise IllegalTransition(
            f"build attempt of release {release.id} in status {release.status}, step {step.status}"
        )
    failed = step.transition(
        ReleaseStepStatus.FAILED, StepInput(failure_code=reason, log_ref=log_ref), now
    )
    if step.attempt < policy.max_attempts:
        return BuildAttemptFailed(
            release=release, step=failed, next_step=failed.next_attempt(next_step_id)
        )
    exhausted = release.transition_unchecked(
        ReleaseStatus.FAILED, ReleaseTransition(failure_code=CODE_BUILD_ATTEMPTS_EXHAUSTED), now
    )
    return BuildAttemptFailed(release=exhausted, step=failed, next_step=None)


def _own_build_step(release: Release, step: ReleaseStep) -> None:
    if step.release_id != release.id or step.name != ReleaseStepName.BUILD:
        raise IllegalTransition(f"step {step.id} is not a build step of release {release.id}")


def _match_build_result(release: Release, step: ReleaseStep, result: BuildResult) -> None:
    """The result names the release's running build attempt and the release's own source."""
    if (
        step.release_id != release.id
        or step.name != ReleaseStepName.BUILD
        or step.status != ReleaseStepStatus.RUNNING
        or step.id != result.step_id
        or step.attempt != result.attempt
    ):
        raise ResultMismatch(
            f"build result for step {result.step_id} attempt {result.attempt} of release "
            f"{release.id}, which is not its running build attempt"
        )
    if (
        release.status != ReleaseStatus.BUILDING
        or result.project_id != release.project_id
        or result.release_id != release.id
        or result.git_commit_sha != release.git_commit_sha
        or result.repository_id != release.repository_id
    ):
        raise ResultMismatch(f"build result for release {release.id} in status {release.status}")
