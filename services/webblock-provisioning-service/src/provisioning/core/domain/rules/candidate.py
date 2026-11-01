"""Candidate attempts of a deploy on plain Kubernetes objects (decision 0011 sections 3, 4, 6 and
12; decision 0014).

Each attempt to run a release as a candidate is a ``candidate`` step row. A release has at most
``MAX_CANDIDATE_ATTEMPTS`` of them over all its deploys; a next attempt follows a failed or a
succeeded one (a succeeded attempt belongs to a candidate that a newer deploy returned to built).

The store applies these functions under the deploy's fence and writes exactly the rows they
return; the deploy use case never moves a step or a release itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.entities.release import Release
from provisioning.core.domain.entities.release_step import ReleaseStep, new_release_step
from provisioning.core.domain.enums.candidate import CandidateFailureEffect, CandidatePlanKind
from provisioning.core.domain.enums.states import ReleaseStatus, ReleaseStepName, ReleaseStepStatus
from provisioning.core.domain.errors import (
    IllegalTransition,
    ImmutableField,
    InvalidAttempt,
    InvalidId,
    MissingDeadline,
)
from provisioning.core.domain.rules.outcome import (
    Outcome,
    OutcomeInput,
    deploy_succeeded,
    fail_without_route_change,
)
from provisioning.core.domain.values.build import CODE_DEADLINE_EXCEEDED
from provisioning.core.domain.values.candidate import MAX_CANDIDATE_ATTEMPTS, CandidateFailure
from provisioning.core.domain.values.deploy_failures import (
    CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
    classify_deploy_failure,
    deploy_failure_summary,
)
from provisioning.core.domain.values.failure import Failure
from provisioning.core.domain.values.step_input import StepInput


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    """Rule result. The plan for a deploy's attempt. ``ended`` is a leftover attempt of an earlier
    claim, failed with ``deadline_exceeded`` at ``now``, to be written before anything else."""

    kind: CandidatePlanKind
    step: ReleaseStep | None = None
    ended: ReleaseStep | None = None


def plan_candidate_attempt(
    release: Release, last: ReleaseStep | None, step_id: UUID, now: datetime
) -> CandidatePlan:
    """The next attempt of a built release (``IllegalTransition`` otherwise). ``last`` is the
    release's candidate step with the highest attempt, None when it has none.

    - No ``last``: START attempt 1.
    - ``last`` running with a deadline after ``now``: RESUME it. Only the command holding the
      current fence reaches this, so the attempt is its own from an earlier claim (a crash, or a
      transient failure that released the claim); resuming spends no further attempt.
    - ``last`` pending, or running past its deadline: it belongs to a claim that is gone, so it is
      ``ended`` with ``deadline_exceeded`` (decision 0011 section 12) and counts as finished.
    - A finished attempt (failed or succeeded) below ``MAX_CANDIDATE_ATTEMPTS``: START the next
      one with ``step_id``; at the maximum: EXHAUSTED.

    ``last`` must be a candidate step of the release (``IllegalTransition``) with an attempt in
    range (``InvalidAttempt``); ``step_id`` must be non-nil and fresh (``InvalidId``)."""
    _require_built(release)
    if last is None:
        _require_fresh(step_id, None)
        step = new_release_step(
            step_id=step_id, release_id=release.id, name=ReleaseStepName.CANDIDATE
        )
        return CandidatePlan(CandidatePlanKind.START, step=step)
    _own_candidate_step(release, last)
    _attempt_in_range(last)
    ended: ReleaseStep | None = None
    expired = last.deadline_at is None or last.deadline_at <= now
    if last.status is ReleaseStepStatus.RUNNING and not expired:
        return CandidatePlan(CandidatePlanKind.RESUME, step=last)
    if last.status in (ReleaseStepStatus.PENDING, ReleaseStepStatus.RUNNING):
        ended = last.transition(
            ReleaseStepStatus.FAILED, StepInput(failure_code=CODE_DEADLINE_EXCEEDED), now
        )
    if last.attempt >= MAX_CANDIDATE_ATTEMPTS:
        return CandidatePlan(CandidatePlanKind.EXHAUSTED, ended=ended)
    _require_fresh(step_id, last)
    step = ReleaseStep(
        id=step_id,
        release_id=release.id,
        name=ReleaseStepName.CANDIDATE,
        attempt=last.attempt + 1,
        status=ReleaseStepStatus.PENDING,
    )
    return CandidatePlan(CandidatePlanKind.START, step=step, ended=ended)


def start_candidate_attempt(
    release: Release, step: ReleaseStep, deadline: datetime, now: datetime
) -> ReleaseStep:
    """A pending candidate attempt of a built release goes running at ``now`` with ``deadline``,
    which must be after ``now`` (``MissingDeadline``). Any other row is ``IllegalTransition``; a
    step that already holds a deadline is ``ImmutableField``."""
    _own_candidate_step(release, step)
    _attempt_in_range(step)
    _require_built(release)
    if step.status is not ReleaseStepStatus.PENDING:
        raise IllegalTransition(
            f"candidate attempt {step.attempt} of release {release.id} is {step.status}"
        )
    if step.deadline_at is not None:
        raise ImmutableField(f"candidate step {step.id} already holds a deadline")
    if deadline <= now:
        raise MissingDeadline(f"deadline of candidate step {step.id} is not after its start")
    return step.transition(ReleaseStepStatus.RUNNING, StepInput(deadline_at=deadline), now)


def succeed_candidate_attempt(release: Release, step: ReleaseStep, now: datetime) -> ReleaseStep:
    """A running candidate attempt of a built release succeeds at ``now``; the release moves
    built -> candidate_ready through ``deploy_succeeded`` in the same write."""
    _own_candidate_step(release, step)
    _require_built(release)
    if step.status is not ReleaseStepStatus.RUNNING:
        raise IllegalTransition(
            f"candidate attempt {step.attempt} of release {release.id} is {step.status}"
        )
    return step.transition(ReleaseStepStatus.SUCCEEDED, StepInput(), now)


def fail_candidate_attempt(
    release: Release, step: ReleaseStep, code: str, now: datetime
) -> tuple[ReleaseStep, CandidateFailure]:
    """Fail a pending or running candidate attempt of a built release with a listed deploy
    failure code (``classify_deploy_failure``), and say what follows (decision 0011 sections 3
    and 6): a code that fails the release, RELEASE_FAILS with it; otherwise, at the last allowed
    attempt, RELEASE_FAILS with ``candidate_attempts_exhausted``; otherwise RETRY for a transient
    code and COMMAND_FAILS for a permanent one. The release is not changed here."""
    failure = classify_deploy_failure(code)
    _own_candidate_step(release, step)
    _attempt_in_range(step)
    _require_built(release)
    if step.status not in (ReleaseStepStatus.PENDING, ReleaseStepStatus.RUNNING):
        raise IllegalTransition(
            f"candidate attempt {step.attempt} of release {release.id} is {step.status}"
        )
    failed = step.transition(ReleaseStepStatus.FAILED, StepInput(failure_code=code), now)
    if failure.fails_release:
        return failed, CandidateFailure(CandidateFailureEffect.RELEASE_FAILS, code)
    if step.attempt >= MAX_CANDIDATE_ATTEMPTS:
        return failed, CandidateFailure(
            CandidateFailureEffect.RELEASE_FAILS, CODE_CANDIDATE_ATTEMPTS_EXHAUSTED
        )
    if failure.transient:
        return failed, CandidateFailure(CandidateFailureEffect.RETRY, code)
    return failed, CandidateFailure(CandidateFailureEffect.COMMAND_FAILS, code)


@dataclass(frozen=True, slots=True)
class CandidateEnd:
    """Rule result. The rows ending an attempt writes: the finished step and, unless the failure is
    retried, the outcome (command finished, release moved or not). ``failure`` is None on
    success."""

    step: ReleaseStep
    outcome: Outcome | None
    failure: CandidateFailure | None


def end_candidate_attempt(
    inp: OutcomeInput, step: ReleaseStep, failure_code: str | None, now: datetime
) -> CandidateEnd:
    """End the deploy's running attempt. Without a failure code: the step succeeded, the release
    candidate_ready and the command succeeded (``deploy_succeeded``). With one: the step failed
    and, as ``fail_candidate_attempt`` classifies it, nothing more (RETRY), the command failed
    (COMMAND_FAILS), or the command and the release failed (RELEASE_FAILS, with the code's fixed
    summary). The fence, the running command and the rows are checked in every case."""
    release = _release_of(inp)
    if failure_code is None:
        outcome = deploy_succeeded(inp, now)
        return CandidateEnd(succeed_candidate_attempt(release, step, now), outcome, None)
    failed, failure = fail_candidate_attempt(release, step, failure_code, now)
    release_failed = failure.effect is CandidateFailureEffect.RELEASE_FAILS
    summary = deploy_failure_summary(failure.code) if release_failed else ""
    # Computed for RETRY too, so that a stale fence or a finished command refuses the step write.
    outcome = fail_without_route_change(
        inp, Failure(code=failure.code, summary=summary), release_failed, now
    )
    if failure.effect is CandidateFailureEffect.RETRY:
        return CandidateEnd(failed, None, failure)
    return CandidateEnd(failed, outcome, failure)


def exhaust_candidate_attempts(inp: OutcomeInput, now: datetime) -> Outcome:
    """A built release with no candidate attempt left (decision 0011 section 12, last ruling):
    the command and the release fail with ``candidate_attempts_exhausted`` in one write."""
    code = CODE_CANDIDATE_ATTEMPTS_EXHAUSTED
    return fail_without_route_change(
        inp, Failure(code=code, summary=deploy_failure_summary(code)), True, now
    )


def _release_of(inp: OutcomeInput) -> Release:
    if inp.release is None:
        raise IllegalTransition(f"command {inp.command.id} names no release")
    return inp.release


def _require_built(release: Release) -> None:
    if release.status is not ReleaseStatus.BUILT:
        raise IllegalTransition(
            f"a candidate attempt of release {release.id} in status {release.status}"
        )


def _own_candidate_step(release: Release, step: ReleaseStep) -> None:
    if step.release_id != release.id or step.name is not ReleaseStepName.CANDIDATE:
        raise IllegalTransition(f"step {step.id} is not a candidate step of release {release.id}")


def _attempt_in_range(step: ReleaseStep) -> None:
    if not 1 <= step.attempt <= MAX_CANDIDATE_ATTEMPTS:
        raise InvalidAttempt(
            f"candidate attempt {step.attempt}, allowed 1 to {MAX_CANDIDATE_ATTEMPTS}"
        )


def _require_fresh(step_id: UUID, last: ReleaseStep | None) -> None:
    if step_id.int == 0 or (last is not None and step_id == last.id):
        raise InvalidId("a candidate attempt needs a fresh step ID")
