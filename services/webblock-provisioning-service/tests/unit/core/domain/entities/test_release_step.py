"""The release step, and ``validate_log_ref``: an opaque reference, never a URL (revision 19; the
build system's handle is such a reference, decision 0014)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.domain import (
    MAX_ATTEMPT,
    MAX_LOG_REF_LEN,
    NIL_ID,
    IllegalTransition,
    InvalidAttempt,
    InvalidFailureCode,
    InvalidId,
    InvalidLogRef,
    MissingDeadline,
    MissingFailureCode,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    StepInput,
    UnknownEnumValue,
    legal_step_transition,
    new_release_step,
    validate_attempt,
    validate_log_ref,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_STEP_DEADLINE = _T0 + timedelta(hours=1)
_INT64_MAX = (1 << 63) - 1

_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
_ID_RETRY = UUID("0190a1b2-0000-7000-8000-0000000000e1")

_PENDING = ReleaseStepStatus.PENDING
_RUNNING = ReleaseStepStatus.RUNNING
_SUCCEEDED = ReleaseStepStatus.SUCCEEDED
_FAILED = ReleaseStepStatus.FAILED

_LEGAL_STEP_PAIRS = {
    (_PENDING, _RUNNING),
    (_PENDING, _FAILED),
    (_RUNNING, _SUCCEEDED),
    (_RUNNING, _FAILED),
}


def _base_step(status: ReleaseStepStatus) -> ReleaseStep:
    """A step in the status; a step that has left pending started at t0 with a deadline."""
    s = ReleaseStep(
        id=_ID_COMMAND,
        release_id=_ID_RELEASE,
        name=ReleaseStepName.BUILD,
        attempt=1,
        status=status,
    )
    if status is not _PENDING:
        s = replace(s, started_at=_T0, deadline_at=_STEP_DEADLINE)
    return s


def _filled_step(status: ReleaseStepStatus) -> ReleaseStep:
    """``_base_step`` with every optional field set. The values are fixtures for rejection
    checks, not a state the transitions produce."""
    return replace(
        _base_step(status),
        started_at=_T0,
        finished_at=_T0,
        deadline_at=_T0,
        failure_code="earlier_reason",
        log_ref="details/ref",
    )


def _finish_input(to: ReleaseStepStatus) -> StepInput:
    """The input a transition to ``to`` takes: a failure code only on failed."""
    if to is _FAILED:
        return StepInput(failure_code="timeout")
    return StepInput()


def _valid_step_row(status: ReleaseStepStatus) -> ReleaseStep:
    """A step as storage holds it in the status: running has a start and a deadline, a finished
    step a start and a finish, and failed a failure code."""
    s = _base_step(status)
    if status is _SUCCEEDED:
        s = replace(s, finished_at=_T1)
    elif status is _FAILED:
        s = replace(s, finished_at=_T1, failure_code="deadline_exceeded")
    return s


_PAIRS = [(f, t) for f in ReleaseStepStatus for t in ReleaseStepStatus]


def test_step_status_matrix_size() -> None:
    assert len(_PAIRS) == 16


@pytest.mark.parametrize(("from_", "to"), _PAIRS, ids=[f"{f}->{t}" for f, t in _PAIRS])
def test_step_status_matrix(from_: ReleaseStepStatus, to: ReleaseStepStatus) -> None:
    want = (from_, to) in _LEGAL_STEP_PAIRS
    assert legal_step_transition(from_, to) is want
    s = _filled_step(from_)
    if want:
        assert s.transition(to, _finish_input(to), _T1).status is to
    else:
        with pytest.raises(IllegalTransition):
            s.transition(to, _finish_input(to), _T1)
        assert s == _filled_step(from_), "the step changed"


def test_step_timestamps_and_reason() -> None:
    s = _base_step(_PENDING).transition(_RUNNING, StepInput(deadline_at=_STEP_DEADLINE), _T0)
    assert s.started_at == _T0
    assert s.finished_at is None
    assert s.deadline_at == _STEP_DEADLINE
    s = s.transition(_SUCCEEDED, StepInput(), _T1)
    assert s.finished_at == _T1
    assert s.failure_code is None

    f = _base_step(_RUNNING).transition(_FAILED, StepInput(failure_code="build_timeout"), _T1)
    assert f.failure_code == "build_timeout"
    assert f.finished_at is not None

    # pending -> failed (deadline passed before start) records start and finish as the same
    # instant, so a finished step always has both (decision 0001 section 8).
    p = _base_step(_PENDING).transition(_FAILED, StepInput(failure_code="deadline_exceeded"), _T1)
    assert p.started_at == _T1
    assert p.finished_at == _T1


# -> running requires a deadline, from the input or already on the row (decision 0001 section 8).
def test_step_running_requires_deadline() -> None:
    s = _base_step(_PENDING)
    with pytest.raises(MissingDeadline):
        s.transition(_RUNNING, StepInput(), _T0)
    assert s == _base_step(_PENDING)
    # A deadline recorded when the step was created satisfies the rule.
    r = replace(_base_step(_PENDING), deadline_at=_STEP_DEADLINE)
    moved = r.transition(_RUNNING, StepInput(), _T0)
    assert moved.status is _RUNNING
    assert moved.deadline_at == _STEP_DEADLINE


# A failure code supplied where the resulting status carries none is refused, not ignored
# (validation finding VR-02), whether well-formed or not, and the step is unchanged.
_OUTSIDE_FAILED = [
    (_PENDING, _RUNNING, StepInput(deadline_at=_STEP_DEADLINE)),
    (_RUNNING, _SUCCEEDED, StepInput()),
]


@pytest.mark.parametrize(
    ("from_", "to", "inp"),
    _OUTSIDE_FAILED,
    ids=[f"{f}->{t}" for f, t, _ in _OUTSIDE_FAILED],
)
def test_step_reason_code_refused_outside_failed(
    from_: ReleaseStepStatus, to: ReleaseStepStatus, inp: StepInput
) -> None:
    for code in ("timeout", "Not A Code"):
        s = _base_step(from_)
        with pytest.raises(InvalidFailureCode):
            s.transition(to, replace(inp, failure_code=code), _T1)
        assert s == _base_step(from_)
    assert _base_step(from_).transition(to, inp, _T1).status is to


# A finish is never earlier than the start (release_steps_finish_after_start).
@pytest.mark.parametrize("to", [_SUCCEEDED, _FAILED], ids=["succeeded", "failed"])
def test_step_finish_not_before_start(to: ReleaseStepStatus) -> None:
    early = _T0 - timedelta(seconds=1)
    s = _base_step(_RUNNING)
    with pytest.raises(IllegalTransition):
        s.transition(to, _finish_input(to), early)
    assert s == _base_step(_RUNNING)
    # Finishing at the start instant is allowed.
    assert _base_step(_RUNNING).transition(to, _finish_input(to), _T0).finished_at == _T0
    # A running row without a start (not a valid stored state) cannot finish.
    broken = replace(_base_step(_RUNNING), started_at=None)
    with pytest.raises(IllegalTransition):
        broken.transition(to, _finish_input(to), _T1)


@pytest.mark.parametrize("from_", [_PENDING, _RUNNING], ids=["pending", "running"])
@pytest.mark.parametrize("reason", ["", "  "], ids=["empty", "blank"])
def test_step_failure_requires_reason(from_: ReleaseStepStatus, reason: str) -> None:
    s = _filled_step(from_)
    with pytest.raises(MissingFailureCode):
        s.transition(_FAILED, StepInput(failure_code=reason), _T1)
    assert s == _filled_step(from_)


@pytest.mark.parametrize(
    "reason", ["a", "build_timeout", "a" + "1" * 99], ids=["a", "build_timeout", "100 chars"]
)
def test_step_reason_code_format_accepted(reason: str) -> None:
    f = _base_step(_RUNNING).transition(_FAILED, StepInput(failure_code=reason), _T1)
    assert f.failure_code == reason


_BAD_REASONS = ["Timeout", "1x", "a-b", "a b", " a", "a ", "a\nb", "a\x00", "a" + "b" * 100, "<b>"]


@pytest.mark.parametrize("reason", _BAD_REASONS, ids=[repr(r) for r in _BAD_REASONS])
def test_step_reason_code_format(reason: str) -> None:
    s = _filled_step(_RUNNING)
    with pytest.raises(InvalidFailureCode):
        s.transition(_FAILED, StepInput(failure_code=reason), _T1)
    assert s == _filled_step(_RUNNING)


def test_step_attempt_validation() -> None:
    assert MAX_ATTEMPT == 2147483647
    for a in (0, -1, -100, 2147483648, _INT64_MAX):
        with pytest.raises(InvalidAttempt):
            validate_attempt(a)
    for a in (1, 2, 99, 2147483647):
        validate_attempt(a)
    pending = _base_step(_PENDING)
    with pytest.raises(InvalidAttempt):
        replace(pending, attempt=2147483648).validate()
    with pytest.raises(InvalidAttempt):
        replace(pending, attempt=0).validate()
    with pytest.raises(UnknownEnumValue):
        replace(pending, name=cast(ReleaseStepName, "bogus")).validate()
    with pytest.raises(UnknownEnumValue):
        replace(pending, status=cast(ReleaseStepStatus, "bogus")).validate()
    pending.validate()


def test_retry_is_new_row() -> None:
    failed = replace(_base_step(_FAILED), attempt=2)
    retry = failed.next_attempt(_ID_RETRY)
    assert retry.attempt == 3
    assert retry.status is _PENDING
    assert retry.id == _ID_RETRY
    assert retry.release_id == failed.release_id
    assert retry.name == failed.name
    assert retry.started_at is None
    assert retry.finished_at is None
    assert retry.failure_code is None
    assert failed.status is _FAILED
    assert failed.attempt == 2
    # Only a failed attempt can be retried; the retry needs a fresh ID.
    for st in (_PENDING, _RUNNING, _SUCCEEDED):
        with pytest.raises(IllegalTransition):
            _base_step(st).next_attempt(_ID_RETRY)
    with pytest.raises(InvalidAttempt):
        replace(_base_step(_FAILED), attempt=2147483647).next_attempt(_ID_RETRY)
    for bad in (0, -1, 2147483648):
        with pytest.raises(InvalidAttempt):
            replace(_base_step(_FAILED), attempt=bad).next_attempt(_ID_RETRY)
    with pytest.raises(InvalidId):
        failed.next_attempt(NIL_ID)
    with pytest.raises(InvalidId):
        failed.next_attempt(failed.id)


def test_new_release_step() -> None:
    s = new_release_step(
        step_id=_ID_COMMAND, release_id=_ID_RELEASE, name=ReleaseStepName.CANDIDATE
    )
    assert s.id == _ID_COMMAND, "IDs landed in the wrong fields"
    assert s.release_id == _ID_RELEASE, "IDs landed in the wrong fields"
    assert s.attempt == 1
    assert s.status is _PENDING
    assert s.name is ReleaseStepName.CANDIDATE
    with pytest.raises(UnknownEnumValue):
        new_release_step(
            step_id=_ID_COMMAND, release_id=_ID_RELEASE, name=cast(ReleaseStepName, "bogus")
        )
    with pytest.raises(InvalidId):
        new_release_step(step_id=NIL_ID, release_id=_ID_RELEASE, name=ReleaseStepName.BUILD)
    with pytest.raises(InvalidId):
        new_release_step(step_id=_ID_COMMAND, release_id=NIL_ID, name=ReleaseStepName.BUILD)
    s.validate()


# ReleaseStep.validate checks a row loaded from storage (decision 0001 section 8).
@pytest.mark.parametrize("status", list(ReleaseStepStatus), ids=[str(s) for s in ReleaseStepStatus])
def test_step_validate_valid_rows(status: ReleaseStepStatus) -> None:
    _valid_step_row(status).validate()


def test_step_validate_accepts() -> None:
    # pending -> failed records start and finish as the same instant; a pending step may carry a
    # deadline.
    replace(_valid_step_row(_FAILED), started_at=_T1, finished_at=_T1).validate()
    replace(_valid_step_row(_PENDING), deadline_at=_STEP_DEADLINE).validate()
    # Rows produced by the transitions validate.
    p = _valid_step_row(_PENDING).transition(
        _FAILED, StepInput(failure_code="deadline_exceeded"), _T1
    )
    p.validate()
    r = _valid_step_row(_PENDING).transition(_RUNNING, StepInput(deadline_at=_STEP_DEADLINE), _T0)
    r.validate()
    r = r.transition(_SUCCEEDED, StepInput(), _T1)
    r.validate()


_ONE_TICK = timedelta(microseconds=1)

_VALIDATE_CASES: dict[str, tuple[ReleaseStep, type[Exception]]] = {
    "zero id": (replace(_valid_step_row(_PENDING), id=NIL_ID), InvalidId),
    "zero release": (replace(_valid_step_row(_PENDING), release_id=NIL_ID), InvalidId),
    "pending with start": (replace(_valid_step_row(_PENDING), started_at=_T0), IllegalTransition),
    "pending with finish": (
        replace(_valid_step_row(_PENDING), finished_at=_T0),
        IllegalTransition,
    ),
    "running without start": (
        replace(_valid_step_row(_RUNNING), started_at=None),
        IllegalTransition,
    ),
    "running without deadline": (
        replace(_valid_step_row(_RUNNING), deadline_at=None),
        MissingDeadline,
    ),
    "running with finish": (
        replace(_valid_step_row(_RUNNING), finished_at=_T1),
        IllegalTransition,
    ),
    "succeeded without start": (
        replace(_valid_step_row(_SUCCEEDED), started_at=None),
        IllegalTransition,
    ),
    "succeeded without finish": (
        replace(_valid_step_row(_SUCCEEDED), finished_at=None),
        IllegalTransition,
    ),
    "succeeded finish before": (
        replace(_valid_step_row(_SUCCEEDED), finished_at=_T0 - _ONE_TICK),
        IllegalTransition,
    ),
    "failed without start": (
        replace(_valid_step_row(_FAILED), started_at=None),
        IllegalTransition,
    ),
    "failed without finish": (
        replace(_valid_step_row(_FAILED), finished_at=None),
        IllegalTransition,
    ),
    "failed finish before": (
        replace(_valid_step_row(_FAILED), finished_at=_T0 - _ONE_TICK),
        IllegalTransition,
    ),
    "failed without reason": (
        replace(_valid_step_row(_FAILED), failure_code=None),
        MissingFailureCode,
    ),
    "failed with empty reason": (
        replace(_valid_step_row(_FAILED), failure_code=""),
        MissingFailureCode,
    ),
    "failed with malformed reason": (
        replace(_valid_step_row(_FAILED), failure_code="Timeout"),
        InvalidFailureCode,
    ),
    "unknown status": (
        replace(_valid_step_row(_PENDING), status=cast(ReleaseStepStatus, "queued")),
        UnknownEnumValue,
    ),
}


@pytest.mark.parametrize(("row", "want"), list(_VALIDATE_CASES.values()), ids=list(_VALIDATE_CASES))
def test_step_validate(row: ReleaseStep, want: type[Exception]) -> None:
    with pytest.raises(want):
        row.validate()


# --- validate_log_ref ---

_MARKER = "SECRET"

_URL_500 = "https://ci.example.test/" + "a" * (500 - len("https://ci.example.test/"))

_GOOD_LOG_REFS = {
    # The opaque identifier of decision 0008 section 4.
    "a": "a",
    "builds/42": "builds/42",
    "build-log/1": "build-log/1",
    "A.b_c-D/9": "A.b_c-D/9",
    "a.b/..c/d..": "a.b/..c/d..",
    "...": "...",
    "500 bytes": "x." + "y" * 498,
    # The build system's handles (decision 0014), opaque references.
    "jenkins build": "jenkins/build/42",
    "jenkins queue item": "jenkins/queue/7",
}

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
    "non-ASCII": _MARKER + "é",
    "query": _MARKER + "?a=1",
    "fragment": _MARKER + "#x",
    "backslash": _MARKER + "\\x",
    "percent": _MARKER + "%2F",
    "bidi control": _MARKER + "‮",
    # _MARKER followed by the byte 0xff is invalid UTF-8; in a str a lone surrogate stands for it.
    "invalid UTF-8": _MARKER + "\udcff",
    "at sign": "user@" + _MARKER,
    "two-byte runes": "é" * 250,
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
    # More URL forms, all refused (revision 19: never a URL).
    "ftp URL": "ftp://x/y",
    "URL with user information": "http://user:pw@host/x",
    "URL with query": "http://host/x?y=1",
    "URL with fragment": "http://host/x#f",
    "URL without host": "http:///x",
    "URL with space": "http://host/a b",
    "URL of 500 bytes": _URL_500,
    "URL non-ASCII": "http://host/é",
}


def test_max_log_ref_len() -> None:
    assert MAX_LOG_REF_LEN == 500
    assert len(_URL_500) == 500


@pytest.mark.parametrize("ref", list(_GOOD_LOG_REFS.values()), ids=list(_GOOD_LOG_REFS))
def test_validate_log_ref_accepts(ref: str) -> None:
    validate_log_ref(ref)


@pytest.mark.parametrize("ref", list(_BAD_LOG_REFS.values()), ids=list(_BAD_LOG_REFS))
def test_validate_log_ref(ref: str) -> None:
    with pytest.raises(InvalidLogRef) as exc_info:
        validate_log_ref(ref)
    assert _MARKER not in str(exc_info.value), "error repeats the input"
