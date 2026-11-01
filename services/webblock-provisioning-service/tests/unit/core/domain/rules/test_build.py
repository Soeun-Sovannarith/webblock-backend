"""Build attempts, build results and the step's log reference (decision 0008 section 4, decision
0014 section 3)."""

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.unit.core.domain.builders import (
    ID_COMMAND,
    ID_PREV,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    ID_RELEASE2,
    REPO_ID,
    SHA40,
    T0,
    T1,
    base_release,
    image_digest,
    valid_row,
)

from provisioning.core.domain import (
    CODE_BUILD_ATTEMPTS_EXHAUSTED,
    CODE_DEADLINE_EXCEEDED,
    CODE_RELEASE_CONFLICT,
    CODE_REPOSITORY_MISMATCH,
    CODE_REPOSITORY_NOT_REGISTERED,
    CODE_REPOSITORY_UNCONFIRMED,
    MAX_BUILD_ATTEMPTS,
    MAX_LOG_REF_LEN,
    NIL_ID,
    BuildAttemptPolicy,
    BuildResult,
    CommandNumberOverflow,
    DomainError,
    HostingOffline,
    IdempotencyConflict,
    IllegalTransition,
    ImmutableField,
    InvalidAttempt,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidCommitSha,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidImageDigest,
    InvalidLogRef,
    InvalidRepositoryId,
    InvalidRequester,
    InvalidRequestFingerprint,
    MissingDeadline,
    MissingFailureCode,
    MissingImageDigest,
    ProjectMismatch,
    Release,
    ReleaseOutdated,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ResultMismatch,
    RouteNotStable,
    StaleFence,
    StepInput,
    UnknownEnumValue,
    apply_build_result,
    apply_failed_build_result,
    fail_build_attempt,
    is_error,
    start_build_attempt,
    validate_code,
    validate_log_ref,
)

# --- fixtures -----------------------------------------------------------------------------------

STEP_DEADLINE = T0 + timedelta(hours=1)

ID_STEP1 = UUID("0190a1b2-0000-7000-8000-0000000000e1")
ID_STEP2 = UUID("0190a1b2-0000-7000-8000-0000000000e2")
ID_STEP3 = UUID("0190a1b2-0000-7000-8000-0000000000e3")

SHA64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

S = ReleaseStatus
St = ReleaseStepStatus


def _build_step(step_id: UUID, attempt: int, status: ReleaseStepStatus) -> ReleaseStep:
    """The build step of ``base_release``: a step that has left pending started at T0 with
    STEP_DEADLINE; a finished one ended at T0 (failed with a code)."""
    s = ReleaseStep(
        id=step_id,
        release_id=ID_RELEASE,
        name=ReleaseStepName.BUILD,
        attempt=attempt,
        status=status,
    )
    if status != St.PENDING:
        s = dataclasses.replace(s, started_at=T0, deadline_at=STEP_DEADLINE)
    if status == St.SUCCEEDED:
        s = dataclasses.replace(s, finished_at=T0)
    elif status == St.FAILED:
        s = dataclasses.replace(s, finished_at=T0, failure_code="deadline_exceeded")
    return s


def _valid_step_row(status: ReleaseStepStatus) -> ReleaseStep:
    """A release step in the status with the fields the database constraints require."""
    s = ReleaseStep(
        id=ID_COMMAND,
        release_id=ID_RELEASE,
        name=ReleaseStepName.BUILD,
        attempt=1,
        status=status,
    )
    if status != St.PENDING:
        s = dataclasses.replace(s, started_at=T0, deadline_at=STEP_DEADLINE)
    if status == St.SUCCEEDED:
        s = dataclasses.replace(s, finished_at=T1)
    elif status == St.FAILED:
        s = dataclasses.replace(s, finished_at=T1, failure_code="deadline_exceeded")
    return s


def _result_for(s: ReleaseStep) -> BuildResult:
    """A successful build result that matches ``base_release`` and the step."""
    return BuildResult(
        project_id=ID_PROJECT,
        release_id=ID_RELEASE,
        step_id=s.id,
        attempt=s.attempt,
        git_commit_sha=SHA40,
        repository_id=REPO_ID,
        container_image_digest=image_digest("a"),
    )


def _policy(n: int) -> BuildAttemptPolicy:
    return BuildAttemptPolicy(max_attempts=n)


def _copies(r: Release, s: ReleaseStep) -> tuple[Release, ReleaseStep]:
    return dataclasses.replace(r), dataclasses.replace(s)


# --- tests ------------------------------------------------------------------------------------


def test_build_outcome_codes() -> None:
    codes = {
        CODE_REPOSITORY_MISMATCH: "repository_mismatch",
        CODE_REPOSITORY_NOT_REGISTERED: "repository_not_registered",
        CODE_REPOSITORY_UNCONFIRMED: "repository_unconfirmed",
        CODE_RELEASE_CONFLICT: "release_conflict",
        CODE_DEADLINE_EXCEEDED: "deadline_exceeded",
        CODE_BUILD_ATTEMPTS_EXHAUSTED: "build_attempts_exhausted",
    }
    assert len(codes) == 6
    for got, want in codes.items():
        assert got == want
        validate_code(got)


def test_build_attempt_policy_validate() -> None:
    assert MAX_BUILD_ATTEMPTS == 10
    for n in range(1, 11):
        _policy(n).validate()
    for n in (-1, 0, 11, 1 << 30):
        with pytest.raises(InvalidAttempt):
            _policy(n).validate()


def test_validate_details_ref() -> None:
    for ok in (
        "a",
        "builds/42",
        "build-log/1",
        "A.b_c-D/9",
        "a.b/..c/d..",
        "...",
        "x." + "y" * 498,
        # The build system's handles (decision 0014), opaque references.
        "jenkins/build/42",
        "jenkins/queue/7",
    ):
        validate_log_ref(ok)
    assert MAX_LOG_REF_LEN == 500


_MARKER = "MARKER"
_BAD_LOG_REFS = {
    "empty": "",
    "URL": "https://" + _MARKER + ".example/builds/1",
    "scheme only": "builds:" + _MARKER,
    "double slash": "builds//" + _MARKER,
    "leading slash": "/" + _MARKER,
    "501 bytes": "a" * 501,
    "newline": _MARKER + "\n1",
    "NUL": _MARKER + "\x00",
    "tab": _MARKER + "\t",
    "DEL": _MARKER + "\x7f",
    "space": _MARKER + " 1",
    "non-ASCII": _MARKER + "\u00e9",
    "query": _MARKER + "?a=1",
    "fragment": _MARKER + "#x",
    "backslash": _MARKER + "\\x",
    "percent": _MARKER + "%2F",
    "bidi control": _MARKER + "\u202e",
    # The byte 0xff is not UTF-8; a Python str holds it as the lone surrogate U+DCFF.
    "invalid UTF-8": _MARKER + "\udcff",
    "at sign": "user@" + _MARKER,
    "two-byte runes": "\u00e9" * 250,
    "dot-dot segment": "a/../" + _MARKER,
    "dot segment": "a/./" + _MARKER,
    "only dot-dot": "..",
    "only dot": ".",
    "leading dot-dot": "../" + _MARKER,
    "trailing dot-dot": _MARKER + "/..",
    "trailing slash": _MARKER + "/",
    "trailing slash 2": "a/" + _MARKER + "/",
    "a/../b": "a/../b",
    "a/./b": "a/./b",
    "a/": "a/",
    "a/b/": "a/b/",
}


@pytest.mark.parametrize("ref", list(_BAD_LOG_REFS.values()), ids=list(_BAD_LOG_REFS))
def test_validate_details_ref_refused(ref: str) -> None:
    with pytest.raises(InvalidLogRef) as exc_info:
        validate_log_ref(ref)
    assert _MARKER not in str(exc_info.value)


_OTHER_ERRORS: tuple[type[DomainError], ...] = (
    IllegalTransition,
    IdempotencyConflict,
    MissingImageDigest,
    InvalidImageDigest,
    MissingFailureCode,
    ImmutableField,
    ProjectMismatch,
    InvalidIdempotencyKey,
    CommandNumberOverflow,
    InvalidCommandNumber,
    InvalidAttempt,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    UnknownEnumValue,
    StaleFence,
    RouteNotStable,
    HostingOffline,
    ReleaseOutdated,
    InvalidCommandTarget,
    ResultMismatch,
    InvalidCommitSha,
    InvalidRepositoryId,
    InvalidRequestFingerprint,
    InvalidRequester,
    MissingDeadline,
)


def _wrapped(cause: BaseException) -> Exception:
    """An error that carries ``cause`` as its ``__cause__``."""
    outer = Exception("wrapped")
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize("other", _OTHER_ERRORS, ids=[c.__name__ for c in _OTHER_ERRORS])
def test_err_invalid_details_ref_is_its_own_sentinel(other: type[DomainError]) -> None:
    assert InvalidLogRef.__doc__
    assert not is_error(_wrapped(InvalidLogRef()), other)
    assert not is_error(_wrapped(other()), InvalidLogRef)
    assert other.__doc__ != InvalidLogRef.__doc__


def test_step_details_ref() -> None:
    """The step row and the step transitions validate a log reference (decision 0008 sec. 4)."""
    for ref in ("", "https://x.example/1", "a//b"):
        with pytest.raises(InvalidLogRef):
            dataclasses.replace(_valid_step_row(St.FAILED), log_ref=ref).validate()
    for status in ReleaseStepStatus:
        dataclasses.replace(_valid_step_row(status), log_ref="builds/42").validate()

    s = _build_step(ID_STEP1, 1, St.RUNNING).transition(
        St.SUCCEEDED, StepInput(log_ref="builds/42"), T1
    )
    assert s.log_ref == "builds/42"

    p = _build_step(ID_STEP1, 1, St.PENDING).transition(
        St.FAILED, StepInput(failure_code="build_failed", log_ref="builds/7"), T1
    )
    assert p.log_ref == "builds/7"

    # Write-once: the same value again is accepted.
    same = dataclasses.replace(_build_step(ID_STEP1, 1, St.RUNNING), log_ref="builds/42")
    out = same.transition(St.SUCCEEDED, StepInput(log_ref="builds/42"), T1)
    assert out.log_ref == "builds/42"


_STEP_REF_REFUSALS: dict[str, tuple[str | None, str, type[DomainError]]] = {
    "invalid": (None, "https://x.example/1", InvalidLogRef),
    "leading slash": (None, "/builds/1", InvalidLogRef),
    "another recorded": ("builds/1", "builds/2", ImmutableField),
}


@pytest.mark.parametrize(
    ("recorded", "ref", "want"), list(_STEP_REF_REFUSALS.values()), ids=list(_STEP_REF_REFUSALS)
)
def test_step_details_ref_refused(recorded: str | None, ref: str, want: type[DomainError]) -> None:
    s = dataclasses.replace(_build_step(ID_STEP1, 1, St.RUNNING), log_ref=recorded)
    before = dataclasses.replace(s)
    with pytest.raises(want):
        s.transition(St.SUCCEEDED, StepInput(log_ref=ref), T1)
    assert s == before


_DEADLINE = T1 + timedelta(hours=1)


def test_start_build_attempt() -> None:
    # The first attempt moves the release to building.
    r, s = start_build_attempt(
        base_release(S.ACCEPTED), _build_step(ID_STEP1, 1, St.PENDING), _DEADLINE, T1
    )
    assert r.status == S.BUILDING
    assert r.updated_at == T1
    assert r.container_image_digest is None
    assert r.failure_code is None
    assert s.status == St.RUNNING
    assert s.started_at == T1
    assert s.deadline_at == _DEADLINE
    assert s.finished_at is None
    r.validate()
    s.validate()

    # A later attempt keeps the release building.
    before = base_release(S.BUILDING)
    r2, s2 = start_build_attempt(before, _build_step(ID_STEP2, 2, St.PENDING), _DEADLINE, T1)
    assert r2 == before
    assert s2.status == St.RUNNING
    assert s2.started_at == T1
    assert s2.deadline_at == _DEADLINE


_START_REFUSALS: dict[str, tuple[Release, ReleaseStep, datetime, type[DomainError]]] = {}
for _status in ReleaseStatus:
    if _status != S.ACCEPTED:
        _START_REFUSALS[f"attempt 1, release {_status}"] = (
            valid_row(_status),
            _build_step(ID_STEP1, 1, St.PENDING),
            _DEADLINE,
            IllegalTransition,
        )
    if _status != S.BUILDING:
        _START_REFUSALS[f"attempt 2, release {_status}"] = (
            valid_row(_status),
            _build_step(ID_STEP2, 2, St.PENDING),
            _DEADLINE,
            IllegalTransition,
        )
for _step_status in (St.RUNNING, St.SUCCEEDED, St.FAILED):
    _START_REFUSALS[f"step {_step_status}"] = (
        base_release(S.ACCEPTED),
        _build_step(ID_STEP1, 1, _step_status),
        _DEADLINE,
        IllegalTransition,
    )
_START_REFUSALS["step of another release"] = (
    base_release(S.ACCEPTED),
    dataclasses.replace(_build_step(ID_STEP1, 1, St.PENDING), release_id=ID_RELEASE2),
    _DEADLINE,
    IllegalTransition,
)
_START_REFUSALS["not the build step"] = (
    base_release(S.ACCEPTED),
    dataclasses.replace(_build_step(ID_STEP1, 1, St.PENDING), name=ReleaseStepName.CANDIDATE),
    _DEADLINE,
    IllegalTransition,
)
_START_REFUSALS["step holds a deadline"] = (
    base_release(S.ACCEPTED),
    dataclasses.replace(_build_step(ID_STEP1, 1, St.PENDING), deadline_at=_DEADLINE),
    _DEADLINE,
    ImmutableField,
)
_START_REFUSALS["attempt 0"] = (
    base_release(S.ACCEPTED),
    _build_step(ID_STEP1, 0, St.PENDING),
    _DEADLINE,
    InvalidAttempt,
)
_START_REFUSALS["deadline at the start"] = (
    base_release(S.ACCEPTED),
    _build_step(ID_STEP1, 1, St.PENDING),
    T1,
    MissingDeadline,
)
_START_REFUSALS["deadline before the start"] = (
    base_release(S.ACCEPTED),
    _build_step(ID_STEP1, 1, St.PENDING),
    T0,
    MissingDeadline,
)
# The zero deadline: the earliest datetime, 0001-01-01 00:00 UTC.
_START_REFUSALS["zero deadline"] = (
    base_release(S.ACCEPTED),
    _build_step(ID_STEP1, 1, St.PENDING),
    datetime(1, 1, 1, tzinfo=UTC),
    MissingDeadline,
)


@pytest.mark.parametrize(
    ("release", "step", "deadline", "want"),
    list(_START_REFUSALS.values()),
    ids=list(_START_REFUSALS),
)
def test_start_build_attempt_refused(
    release: Release, step: ReleaseStep, deadline: datetime, want: type[DomainError]
) -> None:
    r_before, s_before = _copies(release, step)
    with pytest.raises(want):
        start_build_attempt(release, step, deadline, T1)
    assert (release, step) == (r_before, s_before)


def test_apply_build_result_success() -> None:
    s = _build_step(ID_STEP1, 1, St.RUNNING)
    res = dataclasses.replace(_result_for(s), log_ref="builds/42")
    r, s = apply_build_result(base_release(S.BUILDING), s, res, T1)
    assert r.status == S.BUILT
    assert r.container_image_digest == image_digest("a")
    assert r.updated_at == T1
    assert r.failure_code is None
    assert r.failure_summary is None
    assert r.promoted_at is None
    assert s.status == St.SUCCEEDED
    assert s.finished_at == T1
    assert s.failure_code is None
    assert s.log_ref == "builds/42"
    r.validate()
    s.validate()

    # Without a log reference the step keeps none.
    s2 = _build_step(ID_STEP1, 1, St.RUNNING)
    _, s2 = apply_build_result(base_release(S.BUILDING), s2, _result_for(s2), T1)
    assert s2.log_ref is None


_RUNNING_STEP = _build_step(ID_STEP1, 1, St.RUNNING)
_BUILDING = base_release(S.BUILDING)
_MISMATCHES: dict[str, tuple[Release, ReleaseStep, dict[str, object]]] = {
    "other project": (_BUILDING, _RUNNING_STEP, {"project_id": ID_PROJECT2}),
    "zero project": (_BUILDING, _RUNNING_STEP, {"project_id": NIL_ID}),
    "other release": (_BUILDING, _RUNNING_STEP, {"release_id": ID_RELEASE2}),
    "zero release": (_BUILDING, _RUNNING_STEP, {"release_id": NIL_ID}),
    "other commit": (_BUILDING, _RUNNING_STEP, {"git_commit_sha": "f" * 40}),
    "longer commit": (_BUILDING, _RUNNING_STEP, {"git_commit_sha": SHA64}),
    "empty commit": (_BUILDING, _RUNNING_STEP, {"git_commit_sha": ""}),
    "other repository": (_BUILDING, _RUNNING_STEP, {"repository_id": REPO_ID + 1}),
    "no repository": (_BUILDING, _RUNNING_STEP, {"repository_id": 0}),
    "other step": (_BUILDING, _RUNNING_STEP, {"step_id": ID_STEP2}),
    "zero step": (_BUILDING, _RUNNING_STEP, {"step_id": NIL_ID}),
    "other attempt": (_BUILDING, _RUNNING_STEP, {"attempt": 2}),
    "attempt 0": (_BUILDING, _RUNNING_STEP, {"attempt": 0}),
    "other project no digest": (
        _BUILDING,
        _RUNNING_STEP,
        {"project_id": ID_PROJECT2, "container_image_digest": None},
    ),
    "step of another release": (
        _BUILDING,
        dataclasses.replace(_RUNNING_STEP, release_id=ID_RELEASE2),
        {},
    ),
    "not the build step": (
        _BUILDING,
        dataclasses.replace(_RUNNING_STEP, name=ReleaseStepName.CANDIDATE),
        {},
    ),
}
for _step_status in (St.PENDING, St.SUCCEEDED, St.FAILED):
    _MISMATCHES[f"step {_step_status}"] = (_BUILDING, _build_step(ID_STEP1, 1, _step_status), {})
for _status in ReleaseStatus:
    if _status != S.BUILDING:
        _MISMATCHES[f"release {_status}"] = (valid_row(_status), _RUNNING_STEP, {})


@pytest.mark.parametrize(
    ("release", "step", "changes"), list(_MISMATCHES.values()), ids=list(_MISMATCHES)
)
def test_apply_build_result_mismatch(
    release: Release, step: ReleaseStep, changes: dict[str, object]
) -> None:
    """Decision 0008 section 4: anything that does not match the release and its running build
    attempt is ResultMismatch and changes neither row."""
    res = dataclasses.replace(_result_for(_RUNNING_STEP), **changes)  # type: ignore[arg-type]  # field values vary by case
    r_before, s_before = _copies(release, step)
    with pytest.raises(ResultMismatch):
        apply_build_result(release, step, res, T1)
    assert (release, step) == (r_before, s_before)

    failed = dataclasses.replace(res, container_image_digest=None, failure_code="build_failed")
    with pytest.raises(ResultMismatch):
        apply_failed_build_result(release, step, failed, _policy(3), ID_STEP3, T1)
    assert (release, step) == (r_before, s_before)


def test_late_result_of_an_earlier_attempt_changes_nothing() -> None:
    """A late result of attempt 1, after the sweeper failed it and attempt 2 started, changes
    nothing, even with a valid digest (decision 0008 section 4; review A-H1, security H3)."""
    r, s1 = start_build_attempt(
        base_release(S.ACCEPTED), _build_step(ID_STEP1, 1, St.PENDING), STEP_DEADLINE, T0
    )
    late = _result_for(s1)
    after_deadline = STEP_DEADLINE + timedelta(seconds=1)
    swept = fail_build_attempt(r, s1, CODE_DEADLINE_EXCEEDED, _policy(3), ID_STEP2, after_deadline)
    assert swept.next_step is not None
    r, s1 = swept.release, swept.step
    r, s2 = start_build_attempt(
        r, swept.next_step, after_deadline + timedelta(hours=1), after_deadline
    )
    now = after_deadline + timedelta(minutes=1)

    r_before, s1_before, s2_before = (
        dataclasses.replace(r),
        dataclasses.replace(s1),
        dataclasses.replace(s2),
    )
    # Applied against the running attempt 2.
    with pytest.raises(ResultMismatch):
        apply_build_result(r, s2, late, now)
    # Applied against the failed attempt 1 it names.
    with pytest.raises(ResultMismatch):
        apply_build_result(r, s1, late, now)
    # Attempt 2's step ID with attempt 1's number, and the reverse.
    with pytest.raises(ResultMismatch):
        apply_build_result(r, s2, dataclasses.replace(late, step_id=s2.id), now)
    with pytest.raises(ResultMismatch):
        apply_build_result(r, s2, dataclasses.replace(late, attempt=2), now)
    assert (r, s1, s2) == (r_before, s1_before, s2_before)
    assert s1.status == St.FAILED
    assert r.status == S.BUILDING
    # Attempt 2's own result is applied.
    built, _ = apply_build_result(r, s2, _result_for(s2), now)
    assert built.status == S.BUILT


_OUTCOME_REFUSALS: dict[
    str, tuple[dict[str, object], dict[str, object], dict[str, object], datetime, type[DomainError]]
] = {
    "no digest": ({}, {}, {"container_image_digest": None}, T1, MissingImageDigest),
    "empty digest": ({}, {}, {"container_image_digest": ""}, T1, MissingImageDigest),
    "tag digest": (
        {},
        {},
        {"container_image_digest": "registry.example/x:latest"},
        T1,
        InvalidImageDigest,
    ),
    "uppercase digest": (
        {},
        {},
        {"container_image_digest": "sha256:" + "A" * 64},
        T1,
        InvalidImageDigest,
    ),
    "failure code goes through ApplyFailedBuildResult": (
        {},
        {},
        {"container_image_digest": None, "failure_code": "build_failed"},
        T1,
        IllegalTransition,
    ),
    "invalid details reference": ({}, {}, {"log_ref": "https://logs.example/1"}, T1, InvalidLogRef),
    "finish before the start": ({}, {}, {}, T0 - timedelta(seconds=1), IllegalTransition),
    "recorded digest differs": (
        {"container_image_digest": image_digest("b")},
        {},
        {},
        T1,
        ImmutableField,
    ),
    "recorded reference other": ({}, {"log_ref": "a/1"}, {"log_ref": "a/2"}, T1, ImmutableField),
}


@pytest.mark.parametrize(
    ("release_changes", "step_changes", "result_changes", "now", "want"),
    list(_OUTCOME_REFUSALS.values()),
    ids=list(_OUTCOME_REFUSALS),
)
def test_apply_build_result_outcome_refusals(
    release_changes: dict[str, object],
    step_changes: dict[str, object],
    result_changes: dict[str, object],
    now: datetime,
    want: type[DomainError],
) -> None:
    r = dataclasses.replace(base_release(S.BUILDING), **release_changes)  # type: ignore[arg-type]  # field values vary by case
    s = dataclasses.replace(_build_step(ID_STEP1, 1, St.RUNNING), **step_changes)  # type: ignore[arg-type]  # field values vary by case
    res = dataclasses.replace(_result_for(s), **result_changes)  # type: ignore[arg-type]  # field values vary by case
    r_before, s_before = _copies(r, s)
    with pytest.raises(want):
        apply_build_result(r, s, res, now)
    assert (r, s) == (r_before, s_before)


def _failed_result(s: ReleaseStep) -> BuildResult:
    return dataclasses.replace(
        _result_for(s), container_image_digest=None, failure_code="build_failed", log_ref="builds/9"
    )


def test_apply_failed_build_result() -> None:
    # Below the maximum the next attempt is queued.
    r = base_release(S.BUILDING)
    s = _build_step(ID_STEP1, 1, St.RUNNING)
    out = apply_failed_build_result(r, s, _failed_result(s), _policy(2), ID_STEP2, T1)
    assert out.release == r
    assert out.step.status == St.FAILED
    assert out.step.failure_code == "build_failed"
    assert out.step.log_ref == "builds/9"
    assert out.step.finished_at == T1
    assert out.next_step == ReleaseStep(
        id=ID_STEP2,
        release_id=r.id,
        name=ReleaseStepName.BUILD,
        attempt=2,
        status=St.PENDING,
    )

    # At the maximum the release fails.
    s2 = _build_step(ID_STEP2, 2, St.RUNNING)
    out = apply_failed_build_result(r, s2, _failed_result(s2), _policy(2), ID_STEP3, T1)
    assert out.next_step is None
    assert out.release.status == S.FAILED
    assert out.release.failure_code == CODE_BUILD_ATTEMPTS_EXHAUSTED
    assert out.release.failure_summary is None
    assert out.release.container_image_digest is None
    assert out.release.updated_at == T1
    assert out.step.status == St.FAILED
    assert out.step.failure_code == "build_failed"
    out.release.validate()


_FAILED_RESULT_REFUSALS: dict[str, tuple[dict[str, object], type[DomainError]]] = {
    "failure with digest": ({"container_image_digest": image_digest("a")}, ImmutableField),
    "no failure code": ({"failure_code": ""}, MissingFailureCode),
    "malformed code": ({"failure_code": "Build"}, InvalidFailureCode),
    "bad reference": ({"log_ref": "//x"}, InvalidLogRef),
}


@pytest.mark.parametrize(
    ("changes", "want"), list(_FAILED_RESULT_REFUSALS.values()), ids=list(_FAILED_RESULT_REFUSALS)
)
def test_apply_failed_build_result_refused(
    changes: dict[str, object], want: type[DomainError]
) -> None:
    r = base_release(S.BUILDING)
    s = _build_step(ID_STEP2, 2, St.RUNNING)
    res = dataclasses.replace(_failed_result(s), **changes)  # type: ignore[arg-type]  # field values vary by case
    r_before, s_before = _copies(r, s)
    with pytest.raises(want):
        apply_failed_build_result(r, s, res, _policy(2), ID_STEP3, T1)
    assert (r, s) == (r_before, s_before)


_STEP_IDS = (ID_STEP1, ID_STEP2, ID_STEP3, ID_COMMAND)
_MATRIX = [(m, a) for m in range(1, 4) for a in range(1, 5)]


@pytest.mark.parametrize(
    ("max_attempts", "attempt"), _MATRIX, ids=[f"attempt {a} of {m}" for m, a in _MATRIX]
)
def test_fail_build_attempt_matrix(max_attempts: int, attempt: int) -> None:
    """fail_build_attempt over attempt x maximum (decision 0008 section 4, owner question B).

    FR-12.1, FR-12.2."""
    r = base_release(S.BUILDING)
    s = _build_step(_STEP_IDS[attempt - 1], attempt, St.RUNNING)
    next_id = ID_PREV
    out = fail_build_attempt(r, s, CODE_DEADLINE_EXCEEDED, _policy(max_attempts), next_id, T1)
    assert out.step.status == St.FAILED
    assert out.step.failure_code == CODE_DEADLINE_EXCEEDED
    assert out.step.finished_at == T1
    assert out.step.log_ref is None
    out.step.validate()
    if attempt < max_attempts:
        assert out.next_step == ReleaseStep(
            id=next_id,
            release_id=r.id,
            name=ReleaseStepName.BUILD,
            attempt=attempt + 1,
            status=St.PENDING,
        )
        out.next_step.validate()
        assert out.release == r
        return
    assert out.next_step is None
    assert out.release.status == S.FAILED
    assert out.release.failure_code == CODE_BUILD_ATTEMPTS_EXHAUSTED
    assert out.release.updated_at == T1
    out.release.validate()


def test_fail_build_attempt_matrix_zero_next_id_at_the_maximum_is_not_needed() -> None:
    out = fail_build_attempt(
        base_release(S.BUILDING),
        _build_step(ID_STEP1, 1, St.RUNNING),
        "build_failed",
        _policy(1),
        NIL_ID,
        T1,
    )
    assert out.next_step is None
    assert out.release.status == S.FAILED


_FAIL_REFUSALS: dict[
    str, tuple[Release, ReleaseStep, str, BuildAttemptPolicy, UUID, datetime, type[DomainError]]
] = {
    "policy 0": (
        _BUILDING,
        _RUNNING_STEP,
        "build_failed",
        _policy(0),
        ID_STEP2,
        T1,
        InvalidAttempt,
    ),
    "policy 11": (
        _BUILDING,
        _RUNNING_STEP,
        "build_failed",
        _policy(11),
        ID_STEP2,
        T1,
        InvalidAttempt,
    ),
    "no reason": (_BUILDING, _RUNNING_STEP, "", _policy(3), ID_STEP2, T1, MissingFailureCode),
    "malformed reason": (
        _BUILDING,
        _RUNNING_STEP,
        "Deadline",
        _policy(3),
        ID_STEP2,
        T1,
        InvalidFailureCode,
    ),
    "zero next ID": (_BUILDING, _RUNNING_STEP, "build_failed", _policy(3), NIL_ID, T1, InvalidId),
    "next ID is the step's": (
        _BUILDING,
        _RUNNING_STEP,
        "build_failed",
        _policy(3),
        _RUNNING_STEP.id,
        T1,
        InvalidId,
    ),
    "finish before start": (
        _BUILDING,
        _RUNNING_STEP,
        "build_failed",
        _policy(3),
        ID_STEP2,
        T0 - timedelta(seconds=1),
        IllegalTransition,
    ),
}
for _status in ReleaseStatus:
    if _status != S.BUILDING:
        _FAIL_REFUSALS[f"release {_status}"] = (
            valid_row(_status),
            _RUNNING_STEP,
            "build_failed",
            _policy(1),
            ID_STEP2,
            T1,
            IllegalTransition,
        )
for _step_status in (St.PENDING, St.SUCCEEDED, St.FAILED):
    _FAIL_REFUSALS[f"step {_step_status}"] = (
        _BUILDING,
        _build_step(ID_STEP1, 1, _step_status),
        "build_failed",
        _policy(1),
        ID_STEP2,
        T1,
        IllegalTransition,
    )
_FAIL_REFUSALS["step of another release"] = (
    _BUILDING,
    dataclasses.replace(_RUNNING_STEP, release_id=ID_RELEASE2),
    "build_failed",
    _policy(1),
    ID_STEP2,
    T1,
    IllegalTransition,
)
_FAIL_REFUSALS["not the build step"] = (
    _BUILDING,
    dataclasses.replace(_RUNNING_STEP, name=ReleaseStepName.CANDIDATE),
    "build_failed",
    _policy(1),
    ID_STEP2,
    T1,
    IllegalTransition,
)


@pytest.mark.parametrize(
    ("release", "step", "reason", "policy", "next_id", "now", "want"),
    list(_FAIL_REFUSALS.values()),
    ids=list(_FAIL_REFUSALS),
)
def test_fail_build_attempt_refusals(
    release: Release,
    step: ReleaseStep,
    reason: str,
    policy: BuildAttemptPolicy,
    next_id: UUID,
    now: datetime,
    want: type[DomainError],
) -> None:
    r_before, s_before = _copies(release, step)
    with pytest.raises(want):
        fail_build_attempt(release, step, reason, policy, next_id, now)
    assert (release, step) == (r_before, s_before)
