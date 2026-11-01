"""Candidate attempts and the plain-object deploy failure codes (decision 0011 sections 3, 6 and
12; decision 0014 section 10.6)."""

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.domain import (
    CODE_ATTEMPTS_EXHAUSTED,
    CODE_CANDIDATE_APPLY_CONFLICT,
    CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
    CODE_CANDIDATE_FENCE_AHEAD,
    CODE_CANDIDATE_NOT_READY,
    CODE_CANDIDATE_REFUSED,
    CODE_CLUSTER_UNAVAILABLE,
    CODE_CRASH_LOOP,
    CODE_DEADLINE_EXCEEDED,
    CODE_HOSTING_OFFLINE,
    CODE_IMAGE_PULL_FAILED,
    CODE_PROGRESS_DEADLINE_EXCEEDED,
    CODE_RELEASE_NOT_DEPLOYABLE,
    CODE_ROUTE_NOT_STABLE,
    CODE_TENANT_NOT_READY,
    MAX_CANDIDATE_ATTEMPTS,
    CandidateFailureEffect,
    CandidatePlanKind,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    IllegalTransition,
    InvalidAttempt,
    InvalidFailureCode,
    InvalidId,
    MissingDeadline,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
    StaleFence,
    classify_deploy_failure,
    deploy_failure_summary,
    deploy_failures,
    end_candidate_attempt,
    exhaust_candidate_attempts,
    fail_candidate_attempt,
    new_command,
    plan_candidate_attempt,
    start_candidate_attempt,
    succeed_candidate_attempt,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
DEADLINE = T0 + timedelta(minutes=5)

PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
OTHER_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a2")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
STEP_LAST = UUID("0190a1b2-0000-7000-8000-0000000000e1")
STEP_NEW = UUID("0190a1b2-0000-7000-8000-0000000000e2")
DIGEST = "sha256:" + "a" * 64
SHA = "0123456789abcdef0123456789abcdef01234567"


def _release(status: ReleaseStatus = ReleaseStatus.BUILT) -> Release:
    return Release(
        id=RELEASE,
        project_id=PROJECT,
        generation_request_id=REQUEST,
        repository_id=4242,
        git_commit_sha=SHA,
        status=status,
        created_at=T0,
        updated_at=T0,
        container_image_digest=DIGEST,
    )


def _step(
    attempt: int,
    status: ReleaseStepStatus,
    *,
    deadline: datetime | None = DEADLINE,
    name: ReleaseStepName = ReleaseStepName.CANDIDATE,
    release_id: UUID = RELEASE,
) -> ReleaseStep:
    started = None if status is ReleaseStepStatus.PENDING else T0
    finished = T1 if status in (ReleaseStepStatus.SUCCEEDED, ReleaseStepStatus.FAILED) else None
    return ReleaseStep(
        id=STEP_LAST,
        release_id=release_id,
        name=name,
        attempt=attempt,
        status=status,
        started_at=started,
        finished_at=finished,
        deadline_at=None if status is ReleaseStepStatus.PENDING else deadline,
        failure_code="candidate_not_ready" if status is ReleaseStepStatus.FAILED else None,
    )


def _running_step(attempt: int = 1) -> ReleaseStep:
    return dataclasses.replace(_step(attempt, ReleaseStepStatus.RUNNING), id=STEP_NEW)


def _input(command_number: int = 4, last_command_number: int = 4) -> OutcomeInput:
    command = new_command(
        command_id=COMMAND,
        request=CommandRequest(
            project_id=PROJECT, kind=CommandKind.DEPLOY, release_id=RELEASE, idempotency_key="k-1"
        ),
        requested_by_subject="user-7f3a",
        command_number=command_number,
        now=T0,
    )
    hosting = ProjectHosting(
        project_id=PROJECT,
        last_command_number=last_command_number,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        subdomain="shop",
    )
    running: Command = dataclasses.replace(command, status=CommandStatus.RUNNING)
    return OutcomeInput(hosting=hosting, command=running, release=_release())


# --- the failure codes ------------------------------------------------------------------------


def test_no_deploy_failure_code_carries_a_crossplane_name() -> None:
    for failure in deploy_failures():
        for word in ("xr", "composition", "generation"):
            assert word not in failure.code.split("_"), failure.code


def test_the_crossplane_codes_are_no_longer_deploy_failure_codes() -> None:
    for old in (
        "candidate_xr_conflict",
        "candidate_generation_ahead",
        "candidate_xr_refused",
        "candidate_refused_by_composition",
    ):
        with pytest.raises(InvalidFailureCode):
            classify_deploy_failure(old)


@pytest.mark.parametrize(
    ("code", "transient", "fails_release"),
    [
        (CODE_TENANT_NOT_READY, True, False),
        (CODE_CLUSTER_UNAVAILABLE, True, False),
        (CODE_CANDIDATE_APPLY_CONFLICT, False, False),
        (CODE_CANDIDATE_FENCE_AHEAD, False, False),
        (CODE_CANDIDATE_NOT_READY, False, False),
        (CODE_IMAGE_PULL_FAILED, False, False),
        (CODE_CRASH_LOOP, False, False),
        (CODE_PROGRESS_DEADLINE_EXCEEDED, False, False),
        (CODE_RELEASE_NOT_DEPLOYABLE, False, False),
        (CODE_ROUTE_NOT_STABLE, False, False),
        (CODE_HOSTING_OFFLINE, False, False),
        (CODE_DEADLINE_EXCEEDED, False, False),
        (CODE_ATTEMPTS_EXHAUSTED, False, False),
        (CODE_CANDIDATE_REFUSED, False, True),
        (CODE_CANDIDATE_ATTEMPTS_EXHAUSTED, False, True),
    ],
)
def test_deploy_failure_classification(code: str, transient: bool, fails_release: bool) -> None:
    failure = classify_deploy_failure(code)
    assert (failure.code, failure.transient, failure.fails_release) == (
        code,
        transient,
        fails_release,
    )


def test_the_workload_failure_reasons_of_the_cluster_port_are_listed_codes() -> None:
    # SiteCluster.release_status reports these reasons for FAILED (task D).
    for reason in ("progress_deadline_exceeded", "image_pull_failed", "crash_loop"):
        assert classify_deploy_failure(reason).code == reason


def test_every_code_that_fails_the_release_has_a_summary_and_no_other_does() -> None:
    for failure in deploy_failures():
        summary = deploy_failure_summary(failure.code)
        assert (summary != "") == failure.fails_release, failure.code


# --- planning an attempt -----------------------------------------------------------------------


def test_the_first_attempt_is_attempt_one_pending() -> None:
    plan = plan_candidate_attempt(_release(), None, STEP_NEW, T1)
    assert plan.kind is CandidatePlanKind.START
    assert plan.ended is None
    assert plan.step is not None
    assert (plan.step.id, plan.step.release_id, plan.step.name) == (
        STEP_NEW,
        RELEASE,
        ReleaseStepName.CANDIDATE,
    )
    assert (plan.step.attempt, plan.step.status) == (1, ReleaseStepStatus.PENDING)


@pytest.mark.parametrize("status", [ReleaseStepStatus.FAILED, ReleaseStepStatus.SUCCEEDED])
def test_a_next_attempt_follows_a_failed_or_succeeded_one(status: ReleaseStepStatus) -> None:
    plan = plan_candidate_attempt(_release(), _step(3, status), STEP_NEW, T2)
    assert plan.kind is CandidatePlanKind.START
    assert plan.ended is None
    assert plan.step is not None
    assert (plan.step.id, plan.step.attempt, plan.step.status) == (
        STEP_NEW,
        4,
        ReleaseStepStatus.PENDING,
    )


def test_a_running_attempt_before_its_deadline_is_resumed() -> None:
    last = _step(2, ReleaseStepStatus.RUNNING)
    plan = plan_candidate_attempt(_release(), last, STEP_NEW, T1)
    assert plan.kind is CandidatePlanKind.RESUME
    assert plan.step == last
    assert plan.ended is None


def test_a_running_attempt_past_its_deadline_is_failed_and_the_next_one_planned() -> None:
    last = _step(2, ReleaseStepStatus.RUNNING, deadline=T1)
    plan = plan_candidate_attempt(_release(), last, STEP_NEW, T1)
    assert plan.kind is CandidatePlanKind.START
    assert plan.ended is not None
    assert (plan.ended.id, plan.ended.status, plan.ended.failure_code, plan.ended.finished_at) == (
        STEP_LAST,
        ReleaseStepStatus.FAILED,
        CODE_DEADLINE_EXCEEDED,
        T1,
    )
    assert plan.step is not None
    assert plan.step.attempt == 3


def test_a_pending_leftover_is_failed_and_the_next_one_planned() -> None:
    plan = plan_candidate_attempt(_release(), _step(1, ReleaseStepStatus.PENDING), STEP_NEW, T1)
    assert plan.kind is CandidatePlanKind.START
    assert plan.ended is not None
    assert (plan.ended.status, plan.ended.started_at, plan.ended.finished_at) == (
        ReleaseStepStatus.FAILED,
        T1,
        T1,
    )
    assert plan.step is not None
    assert plan.step.attempt == 2


@pytest.mark.parametrize("status", [ReleaseStepStatus.FAILED, ReleaseStepStatus.SUCCEEDED])
def test_no_attempt_follows_the_last_allowed_one(status: ReleaseStepStatus) -> None:
    plan = plan_candidate_attempt(_release(), _step(MAX_CANDIDATE_ATTEMPTS, status), STEP_NEW, T2)
    assert plan.kind is CandidatePlanKind.EXHAUSTED
    assert plan.step is None
    assert plan.ended is None


def test_an_expired_last_allowed_attempt_is_failed_and_exhausts_the_release() -> None:
    last = _step(MAX_CANDIDATE_ATTEMPTS, ReleaseStepStatus.RUNNING, deadline=T1)
    plan = plan_candidate_attempt(_release(), last, STEP_NEW, T2)
    assert plan.kind is CandidatePlanKind.EXHAUSTED
    assert plan.step is None
    assert plan.ended is not None
    assert plan.ended.failure_code == CODE_DEADLINE_EXCEEDED


@pytest.mark.parametrize(
    "status",
    [
        ReleaseStatus.ACCEPTED,
        ReleaseStatus.BUILDING,
        ReleaseStatus.CANDIDATE_READY,
        ReleaseStatus.LIVE,
        ReleaseStatus.FAILED,
    ],
)
def test_only_a_built_release_gets_an_attempt(status: ReleaseStatus) -> None:
    with pytest.raises(IllegalTransition):
        plan_candidate_attempt(_release(status), None, STEP_NEW, T1)


def test_the_last_step_must_be_a_candidate_step_of_the_release() -> None:
    build = _step(1, ReleaseStepStatus.SUCCEEDED, name=ReleaseStepName.BUILD)
    other = _step(1, ReleaseStepStatus.FAILED, release_id=OTHER_RELEASE)
    for last in (build, other):
        with pytest.raises(IllegalTransition):
            plan_candidate_attempt(_release(), last, STEP_NEW, T1)


def test_an_attempt_out_of_range_is_refused() -> None:
    last = _step(MAX_CANDIDATE_ATTEMPTS + 1, ReleaseStepStatus.FAILED)
    with pytest.raises(InvalidAttempt):
        plan_candidate_attempt(_release(), last, STEP_NEW, T1)


def test_a_next_attempt_needs_a_fresh_step_id() -> None:
    last = _step(1, ReleaseStepStatus.FAILED)
    for step_id in (UUID(int=0), STEP_LAST):
        with pytest.raises(InvalidId):
            plan_candidate_attempt(_release(), last, step_id, T2)
    with pytest.raises(InvalidId):
        plan_candidate_attempt(_release(), None, UUID(int=0), T2)


# --- moving an attempt -------------------------------------------------------------------------


def test_start_moves_a_pending_attempt_to_running_with_its_deadline() -> None:
    pending = plan_candidate_attempt(_release(), None, STEP_NEW, T0).step
    assert pending is not None
    running = start_candidate_attempt(_release(), pending, DEADLINE, T1)
    assert (running.status, running.started_at, running.deadline_at) == (
        ReleaseStepStatus.RUNNING,
        T1,
        DEADLINE,
    )


def test_start_refuses_a_deadline_not_after_now_a_started_step_and_a_release_not_built() -> None:
    pending = plan_candidate_attempt(_release(), None, STEP_NEW, T0).step
    assert pending is not None
    with pytest.raises(MissingDeadline):
        start_candidate_attempt(_release(), pending, T1, T1)
    with pytest.raises(IllegalTransition):
        start_candidate_attempt(_release(), _running_step(), DEADLINE, T1)
    with pytest.raises(IllegalTransition):
        start_candidate_attempt(_release(ReleaseStatus.CANDIDATE_READY), pending, DEADLINE, T1)


def test_succeed_finishes_a_running_attempt_only() -> None:
    done = succeed_candidate_attempt(_release(), _running_step(), T1)
    assert (done.status, done.finished_at) == (ReleaseStepStatus.SUCCEEDED, T1)
    with pytest.raises(IllegalTransition):
        succeed_candidate_attempt(_release(), _step(1, ReleaseStepStatus.FAILED), T1)


@pytest.mark.parametrize(
    ("attempt", "code", "effect", "result_code"),
    [
        (1, CODE_CLUSTER_UNAVAILABLE, CandidateFailureEffect.RETRY, CODE_CLUSTER_UNAVAILABLE),
        (1, CODE_CANDIDATE_NOT_READY, CandidateFailureEffect.COMMAND_FAILS, "candidate_not_ready"),
        (1, CODE_IMAGE_PULL_FAILED, CandidateFailureEffect.COMMAND_FAILS, "image_pull_failed"),
        (1, CODE_CANDIDATE_REFUSED, CandidateFailureEffect.RELEASE_FAILS, "candidate_refused"),
        (
            MAX_CANDIDATE_ATTEMPTS,
            CODE_CANDIDATE_NOT_READY,
            CandidateFailureEffect.RELEASE_FAILS,
            CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
        ),
        (
            MAX_CANDIDATE_ATTEMPTS,
            CODE_TENANT_NOT_READY,
            CandidateFailureEffect.RELEASE_FAILS,
            CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
        ),
        (
            MAX_CANDIDATE_ATTEMPTS,
            CODE_CANDIDATE_REFUSED,
            CandidateFailureEffect.RELEASE_FAILS,
            CODE_CANDIDATE_REFUSED,
        ),
    ],
)
def test_fail_classifies_the_attempt(
    attempt: int, code: str, effect: CandidateFailureEffect, result_code: str
) -> None:
    step, failure = fail_candidate_attempt(_release(), _running_step(attempt), code, T1)
    assert (step.status, step.failure_code, step.finished_at) == (
        ReleaseStepStatus.FAILED,
        code,
        T1,
    )
    assert (failure.effect, failure.code) == (effect, result_code)


def test_fail_refuses_an_unlisted_code_and_a_finished_step() -> None:
    with pytest.raises(InvalidFailureCode):
        fail_candidate_attempt(_release(), _running_step(), "candidate_xr_refused", T1)
    with pytest.raises(IllegalTransition):
        fail_candidate_attempt(
            _release(), _step(1, ReleaseStepStatus.SUCCEEDED), CODE_CANDIDATE_NOT_READY, T1
        )


# --- ending an attempt with its outcome --------------------------------------------------------


def test_a_ready_candidate_makes_the_release_candidate_ready_and_the_command_succeed() -> None:
    end = end_candidate_attempt(_input(), _running_step(), None, T1)
    assert end.failure is None
    assert end.step.status is ReleaseStepStatus.SUCCEEDED
    assert end.outcome is not None
    assert end.outcome.release is not None
    assert end.outcome.release.status is ReleaseStatus.CANDIDATE_READY
    assert end.outcome.command.status is CommandStatus.SUCCEEDED


def test_a_permanent_failure_ends_the_command_and_keeps_the_release_built() -> None:
    end = end_candidate_attempt(_input(), _running_step(), CODE_CANDIDATE_NOT_READY, T1)
    assert end.step.failure_code == CODE_CANDIDATE_NOT_READY
    assert end.failure is not None
    assert end.failure.effect is CandidateFailureEffect.COMMAND_FAILS
    assert end.outcome is not None
    assert end.outcome.release is None
    assert (end.outcome.command.status, end.outcome.command.failure_code) == (
        CommandStatus.FAILED,
        CODE_CANDIDATE_NOT_READY,
    )


def test_a_refused_candidate_fails_the_release_with_a_fixed_summary() -> None:
    end = end_candidate_attempt(_input(), _running_step(), CODE_CANDIDATE_REFUSED, T1)
    assert end.outcome is not None
    assert end.outcome.release is not None
    assert (
        end.outcome.release.status,
        end.outcome.release.failure_code,
        end.outcome.release.failure_summary,
    ) == (ReleaseStatus.FAILED, CODE_CANDIDATE_REFUSED, deploy_failure_summary("candidate_refused"))
    assert end.outcome.command.failure_code == CODE_CANDIDATE_REFUSED


def test_the_last_allowed_attempt_failing_exhausts_the_release() -> None:
    step = _running_step(MAX_CANDIDATE_ATTEMPTS)
    end = end_candidate_attempt(_input(), step, CODE_CANDIDATE_NOT_READY, T1)
    assert end.step.failure_code == CODE_CANDIDATE_NOT_READY
    assert end.outcome is not None
    assert end.outcome.release is not None
    assert end.outcome.release.failure_code == CODE_CANDIDATE_ATTEMPTS_EXHAUSTED
    assert end.outcome.command.failure_code == CODE_CANDIDATE_ATTEMPTS_EXHAUSTED


def test_a_transient_failure_writes_only_the_step() -> None:
    end = end_candidate_attempt(_input(), _running_step(), CODE_CLUSTER_UNAVAILABLE, T1)
    assert end.outcome is None
    assert end.failure is not None
    assert end.failure.effect is CandidateFailureEffect.RETRY


def test_ending_an_attempt_under_a_stale_fence_is_refused() -> None:
    stale = _input(command_number=4, last_command_number=5)
    with pytest.raises(StaleFence):
        end_candidate_attempt(stale, _running_step(), None, T1)
    with pytest.raises(StaleFence):
        end_candidate_attempt(stale, _running_step(), CODE_CANDIDATE_NOT_READY, T1)


def test_exhausting_fails_the_release_and_the_command() -> None:
    out = exhaust_candidate_attempts(_input(), T1)
    assert out.release is not None
    assert (out.release.status, out.release.failure_code) == (
        ReleaseStatus.FAILED,
        CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
    )
    assert out.release.failure_summary == deploy_failure_summary(CODE_CANDIDATE_ATTEMPTS_EXHAUSTED)
    assert (out.command.status, out.command.failure_code) == (
        CommandStatus.FAILED,
        CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
    )
    with pytest.raises(StaleFence):
        exhaust_candidate_attempts(_input(command_number=4, last_command_number=5), T1)
