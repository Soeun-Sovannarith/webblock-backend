"""The build attempts use case (start an attempt, apply a result, one round of the deadline sweep;
decision 0008 section 4)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.application import (
    MAX_SWEEP_LIMIT,
    BuildAttemptParams,
    BuildAttempts,
    BuildStepKey,
    BuildTarget,
    BuildWrite,
    Contention,
    OverdueBuild,
    RunningBuilds,
    StartBuildParams,
    SweepOutcome,
    SweepRoundParams,
    Timeout,
)
from provisioning.core.application.builds.ports import PendingBuilds
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.core.application.shared.audit import AuditEvent
from provisioning.core.domain import (
    CODE_DEADLINE_EXCEEDED,
    MAX_BUILD_ATTEMPTS,
    NIL_ID,
    BuildAttemptPolicy,
    BuildResult,
    IllegalTransition,
    InvalidAttempt,
    InvalidId,
    MissingDeadline,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ResultMismatch,
    is_error,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

_ID_PROJECT_TEXT = "0190a1b2-0000-7000-8000-000000000001"
_ID_RELEASE_TEXT = "0190a1b2-0000-7000-8000-0000000000a1"
_ID_STEP_TEXT = "0190a1b2-0000-7000-8000-0000000000e1"
_ID_PROJECT = UUID(_ID_PROJECT_TEXT)
_ID_RELEASE = UUID(_ID_RELEASE_TEXT)
_ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
_ID_STEP = UUID(_ID_STEP_TEXT)
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_REPO_ID = 4242
_DIGEST = "sha256:" + "a" * 64
_LOGGER = "tests.unit.build_attempts"
_WAIT = 5.0  # seconds a test waits for an event before it fails


class _EntropyGone(Exception):
    """The ID source's failure."""


def _step_key() -> BuildStepKey:
    return BuildStepKey(project_id=_ID_PROJECT, release_id=_ID_RELEASE, step_id=_ID_STEP)


def _release(status: ReleaseStatus) -> Release:
    return Release(
        id=_ID_RELEASE,
        project_id=_ID_PROJECT,
        generation_request_id=_ID_REQUEST,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
        status=status,
        created_at=_T0,
        updated_at=_T0,
    )


def _build_write(status: ReleaseStatus) -> BuildWrite:
    return BuildWrite(
        release=_release(status),
        step=ReleaseStep(
            id=_ID_STEP,
            release_id=_ID_RELEASE,
            name=ReleaseStepName.BUILD,
            attempt=1,
            status=ReleaseStepStatus.RUNNING,
        ),
    )


def _build_result() -> BuildResult:
    return BuildResult(
        project_id=_ID_PROJECT,
        release_id=_ID_RELEASE,
        step_id=_ID_STEP,
        attempt=2,
        git_commit_sha=_SHA40,
        repository_id=_REPO_ID,
        container_image_digest=_DIGEST,
        log_ref="builds/SECRETREF",
    )


def _overdue(n: int) -> list[OverdueBuild]:
    return [
        OverdueBuild(
            step=BuildStepKey(
                project_id=_ID_PROJECT,
                release_id=UUID(int=(_ID_RELEASE.int & ~0xFF) | (0x10 + i)),
                step_id=UUID(int=(_ID_STEP.int & ~0xFF) | (0x40 + i)),
            ),
            attempt=i + 1,
            deadline_at=_T0 + timedelta(minutes=i),
        )
        for i in range(n)
    ]


def _sweep_params() -> SweepRoundParams:
    return SweepRoundParams(limit=10, concurrency=2, transaction_timeout=timedelta(seconds=1))


async def _yield_loop(times: int = 20) -> None:
    """Let every ready task run (a yield to the event loop, not a timed sleep)."""
    for _ in range(times):
        await asyncio.sleep(0)


async def _wait(ev: asyncio.Event) -> None:
    async with asyncio.timeout(_WAIT):
        await ev.wait()


# --- doubles ---


class _Ids:
    def __init__(self) -> None:
        self.n = 0
        self.fail = False

    def new_id(self) -> UUID:
        if self.fail:
            raise _EntropyGone
        self.n += 1
        return UUID(f"02000000-0000-7000-8000-{self.n:012x}")


class _Counter:
    """Calls in flight, the most at once, and an event set when ``reach`` are in flight."""

    def __init__(self) -> None:
        self.inflight = 0
        self.max_in = 0
        self.starts = 0
        self.reach = 1
        self.reached = asyncio.Event()

    def enter(self) -> None:
        self.starts += 1
        self.inflight += 1
        self.max_in = max(self.max_in, self.inflight)
        if self.inflight >= self.reach:
            self.reached.set()

    def leave(self) -> None:
        self.inflight -= 1


@dataclass
class _SweepCall:
    candidate: OverdueBuild
    attempt: BuildAttemptParams


@dataclass
class _BuildStore:
    starts: list[StartBuildParams] = field(default_factory=list)
    start_error: Exception | None = None
    write: BuildWrite = field(default_factory=lambda: _build_write(ReleaseStatus.BUILDING))

    results: list[BuildResult] = field(default_factory=list)
    attempts: list[BuildAttemptParams] = field(default_factory=list)
    result_error: Exception | None = None

    scan_limits: list[int] = field(default_factory=list)
    overdue: list[OverdueBuild] = field(default_factory=list)
    scan_error: Exception | None = None
    scan_block: bool = False

    sweeps: list[_SweepCall] = field(default_factory=list)
    # The outcome of a candidate (it may raise); None means FAILED.
    outcome: Callable[[OverdueBuild], SweepOutcome] | None = None
    # Each sweep waits on gate, when set, after it starts.
    gate: asyncio.Event | None = None
    load: _Counter = field(default_factory=_Counter)

    async def start_attempt(self, params: StartBuildParams) -> BuildWrite:
        self.starts.append(params)
        if self.start_error is not None:
            raise self.start_error
        return self.write

    async def apply_result(self, result: BuildResult, attempt: BuildAttemptParams) -> BuildWrite:
        self.results.append(result)
        self.attempts.append(attempt)
        if self.result_error is not None:
            raise self.result_error
        return self.write

    async def scan_overdue(self, limit: int) -> list[OverdueBuild]:
        self.scan_limits.append(limit)
        if self.scan_block:
            await asyncio.Event().wait()
        if self.scan_error is not None:
            raise self.scan_error
        return list(self.overdue)

    async def sweep_overdue(
        self, candidate: OverdueBuild, attempt: BuildAttemptParams
    ) -> SweepOutcome:
        self.load.enter()
        try:
            if self.gate is not None:
                await self.gate.wait()
        finally:
            self.load.leave()
        self.sweeps.append(_SweepCall(candidate, attempt))
        if self.outcome is None:
            return SweepOutcome.FAILED
        return self.outcome(candidate)

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        raise AssertionError("not used by BuildAttempts")

    async def record_log_ref(self, step: BuildStepKey, log_ref: str) -> bool:
        raise AssertionError("not used by BuildAttempts")

    async def scan_running(self, limit: int) -> RunningBuilds:
        raise AssertionError("not used by BuildAttempts")

    async def scan_pending(self, limit: int, older_than: timedelta) -> PendingBuilds:
        raise AssertionError("not used by BuildAttempts")


class _Canceller:
    """The build system's cancel only; ``block`` makes it hang until cancelled."""

    def __init__(self) -> None:
        self.handles: list[BuildHandle] = []
        self.error: Exception | None = None
        self.block = False

    async def cancel(self, handle: BuildHandle) -> None:
        self.handles.append(handle)
        if self.block:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error


class _Clock:
    def now(self) -> datetime:
        return _T0 + timedelta(hours=1)


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("a sweep never alerts")


_CANCEL_TIMEOUT = timedelta(seconds=1)


def _new_build_attempts(
    store: _BuildStore,
    ids: _Ids,
    policy: BuildAttemptPolicy,
    canceller: _Canceller | None = None,
    audit: _Audit | None = None,
    cancel_timeout: timedelta = _CANCEL_TIMEOUT,
) -> BuildAttempts:
    return BuildAttempts(
        store=store,
        ids=ids,
        logger=logging.getLogger(_LOGGER),
        policy=policy,
        builds=canceller or _Canceller(),
        clock=_Clock(),
        audit=audit or _Audit(),
        actor="build-worker",
        cancel_timeout=cancel_timeout,
    )


class _Fixture:
    def __init__(self, max_attempts: int, cancel_timeout: timedelta = _CANCEL_TIMEOUT) -> None:
        self.store = _BuildStore()
        self.ids = _Ids()
        self.canceller = _Canceller()
        self.audit = _Audit()
        self.builds = _new_build_attempts(
            self.store,
            self.ids,
            BuildAttemptPolicy(max_attempts=max_attempts),
            self.canceller,
            self.audit,
            cancel_timeout,
        )


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _LOGGER]


_STANDARD_ATTRS = {*vars(logging.makeLogRecord({})), "message", "asctime"}


def _record_text(r: logging.LogRecord) -> str:
    """The message and every extra field of a record, as one text."""
    extras = (f"{k}={v}" for k, v in vars(r).items() if k not in _STANDARD_ATTRS)
    return " ".join([r.getMessage(), *extras])


# --- construction ---


@pytest.mark.parametrize(
    "max_attempts", [0, MAX_BUILD_ATTEMPTS + 1], ids=["zero attempts", "above the max"]
)
def test_new_build_attempts_refusals(max_attempts: int) -> None:
    with pytest.raises(InvalidAttempt):
        _new_build_attempts(_BuildStore(), _Ids(), BuildAttemptPolicy(max_attempts=max_attempts))


@pytest.mark.parametrize("timeout", [timedelta(0), timedelta(seconds=-1)])
def test_new_build_attempts_refuses_a_cancel_timeout_that_is_not_positive(
    timeout: timedelta,
) -> None:
    with pytest.raises(ValueError, match="cancel timeout"):
        _new_build_attempts(
            _BuildStore(), _Ids(), BuildAttemptPolicy(max_attempts=3), cancel_timeout=timeout
        )


@pytest.mark.parametrize("max_attempts", [1, MAX_BUILD_ATTEMPTS], ids=["one", "the max"])
def test_new_build_attempts_accepts_the_policy_range(max_attempts: int) -> None:
    f = _Fixture(max_attempts)
    assert f.store.starts == []


# --- start ---


@pytest.mark.asyncio
async def test_start_passes_the_attempt_to_the_store() -> None:
    f = _Fixture(3)
    p = StartBuildParams(step=_step_key(), duration=timedelta(minutes=20))
    w = await f.builds.start(p)
    assert w == f.store.write
    assert f.store.starts == [p]

    f.store.start_error = IllegalTransition("s")
    with pytest.raises(IllegalTransition):
        await f.builds.start(p)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "want"),
    [
        (
            StartBuildParams(
                step=BuildStepKey(project_id=NIL_ID, release_id=NIL_ID, step_id=NIL_ID),
                duration=timedelta(minutes=1),
            ),
            InvalidId,
        ),
        (StartBuildParams(step=_step_key(), duration=timedelta(0)), MissingDeadline),
    ],
    ids=["no step", "no duration"],
)
async def test_start_refuses_invalid_params_before_the_store(
    params: StartBuildParams, want: type[Exception]
) -> None:
    f = _Fixture(3)
    with pytest.raises(want):
        await f.builds.start(params)
    assert f.store.starts == []


# --- apply_result ---


@pytest.mark.asyncio
async def test_apply_result_passes_the_policy_and_a_fresh_next_step_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    f = _Fixture(4)
    res = _build_result()
    f.store.write = _build_write(ReleaseStatus.BUILT)
    w = await f.builds.apply_result(res)
    assert w.release.status is ReleaseStatus.BUILT
    await f.builds.apply_result(res)
    assert f.store.results == [res, res]
    a, b = f.store.attempts
    assert a.policy.max_attempts == 4
    assert a.next_step_id != NIL_ID
    assert a.next_step_id != b.next_step_id, "the next step ID was not fresh"
    assert _records(caplog) == [], "a successful result logged"


@pytest.mark.asyncio
async def test_apply_result_mismatch_is_logged_with_identifiers_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A mismatched result changes nothing, is re-raised as ResultMismatch, and is logged with
    its identifiers only (decision 0008 section 4: the log line is the record)."""
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    f = _Fixture(3)
    f.store.result_error = ResultMismatch(f"STORE-ERROR-TEXT release {_ID_RELEASE}")
    with pytest.raises(ResultMismatch):
        await f.builds.apply_result(_build_result())
    records = _records(caplog)
    assert len(records) == 1
    r = records[0]
    assert r.levelno == logging.WARNING
    assert r.exc_info is None, "the record carries the store's error"
    text = _record_text(r)
    for want in (_ID_PROJECT_TEXT, _ID_RELEASE_TEXT, _ID_STEP_TEXT, "result_mismatch"):
        assert want in text, f"log record lacks {want!r}: {text}"
    assert re.search(r"attempt\W{0,3}2\b", text), f"log record lacks the attempt: {text}"
    for secret in (_SHA40, "4242", "SECRETREF", "sha256:", "aaaa", "STORE-ERROR-TEXT"):
        assert secret not in text, f"log record carries {secret!r}: {text}"


@pytest.mark.asyncio
async def test_apply_result_other_errors_are_returned_without_the_mismatch_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    f = _Fixture(3)
    f.store.result_error = Contention("x")
    with pytest.raises(Contention):
        await f.builds.apply_result(_build_result())
    assert _records(caplog) == []

    f = _Fixture(3)
    f.ids.fail = True
    with pytest.raises(_EntropyGone):
        await f.builds.apply_result(_build_result())
    assert f.store.results == [], "the store was called without a next step ID"


# --- sweep ---


def test_sweep_round_params_validate_accepts_valid_params() -> None:
    _sweep_params().validate()
    SweepRoundParams(
        limit=MAX_SWEEP_LIMIT, concurrency=MAX_SWEEP_LIMIT, transaction_timeout=timedelta(seconds=1)
    ).validate()


_BAD_SWEEP_PARAMS: dict[str, SweepRoundParams] = {
    "zero limit": SweepRoundParams(
        limit=0, concurrency=2, transaction_timeout=timedelta(seconds=1)
    ),
    "limit above the maximum": SweepRoundParams(
        limit=MAX_SWEEP_LIMIT + 1, concurrency=2, transaction_timeout=timedelta(seconds=1)
    ),
    "zero concurrency": SweepRoundParams(
        limit=10, concurrency=0, transaction_timeout=timedelta(seconds=1)
    ),
    "concurrency above limit": SweepRoundParams(
        limit=10, concurrency=11, transaction_timeout=timedelta(seconds=1)
    ),
    "zero transaction": SweepRoundParams(limit=10, concurrency=2, transaction_timeout=timedelta(0)),
}


@pytest.mark.parametrize("params", list(_BAD_SWEEP_PARAMS.values()), ids=list(_BAD_SWEEP_PARAMS))
def test_sweep_round_params_validate(params: SweepRoundParams) -> None:
    with pytest.raises(ValueError, match=r"."):
        params.validate()


@pytest.mark.asyncio
async def test_sweep_round_refuses_invalid_params_before_the_store() -> None:
    f = _Fixture(3)
    with pytest.raises(ValueError, match=r"."):
        await f.builds.sweep_round(
            SweepRoundParams(limit=0, concurrency=0, transaction_timeout=timedelta(0))
        )
    assert f.store.scan_limits == [], "invalid sweep params reached the store"


@pytest.mark.asyncio
async def test_sweep_round_outcomes() -> None:
    """One round: the scan, then one sweep_overdue per candidate with the policy and a fresh step
    ID; failed, busy, stale and error outcomes are each reported against their candidate."""
    f = _Fixture(3)
    cands = _overdue(4)
    f.store.overdue = cands

    def outcome(c: OverdueBuild) -> SweepOutcome:
        match c.attempt:
            case 1:
                return SweepOutcome.FAILED
            case 2:
                return SweepOutcome.BUSY
            case 3:
                return SweepOutcome.STALE
        raise Contention("sweep")

    f.store.outcome = outcome
    res = await f.builds.sweep_round(_sweep_params())
    assert f.store.scan_limits == [10]
    want: list[SweepOutcome | None] = [
        SweepOutcome.FAILED,
        SweepOutcome.BUSY,
        SweepOutcome.STALE,
        None,
    ]
    assert len(res) == 4
    for i, r in enumerate(res):
        assert r.candidate == cands[i], f"result {i} out of scan order"
        assert r.outcome is want[i]
        assert (r.error is not None) is (i == 3)
    assert is_error(res[3].error, Contention)
    seen: set[UUID] = set()
    assert len(f.store.sweeps) == 4
    for c in f.store.sweeps:
        assert c.attempt.policy.max_attempts == 3
        assert c.attempt.next_step_id != NIL_ID
        assert c.attempt.next_step_id not in seen, "a next step ID was reused"
        seen.add(c.attempt.next_step_id)


@pytest.mark.asyncio
async def test_sweep_round_refuses_an_outcome_the_store_cannot_return() -> None:
    f = _Fixture(3)
    f.store.overdue = _overdue(1)
    f.store.outcome = lambda _c: cast(SweepOutcome, "done")
    res = await f.builds.sweep_round(_sweep_params())
    assert len(res) == 1
    assert res[0].error is not None
    assert res[0].outcome is None


@pytest.mark.asyncio
async def test_sweep_round_scan_error_and_id_failure() -> None:
    f = _Fixture(3)
    f.store.scan_error = Contention("scan")
    with pytest.raises(Contention):
        await f.builds.sweep_round(_sweep_params())

    f = _Fixture(3)
    f.store.overdue = _overdue(2)
    f.ids.fail = True
    res = await f.builds.sweep_round(_sweep_params())
    assert len(res) == 2
    assert all(r.error is not None for r in res)
    assert f.store.sweeps == []
    assert f.store.load.starts == 0


@pytest.mark.asyncio
async def test_sweep_round_bounds_the_scan_by_the_transaction_timeout() -> None:
    f = _Fixture(3)
    f.store.scan_block = True
    p = SweepRoundParams(limit=10, concurrency=2, transaction_timeout=timedelta(milliseconds=20))
    async with asyncio.timeout(_WAIT):
        with pytest.raises((TimeoutError, Timeout)):
            await f.builds.sweep_round(p)
    assert f.store.scan_limits == [10]


@pytest.mark.asyncio
async def test_sweep_round_bounds_each_candidate_by_the_transaction_timeout() -> None:
    f = _Fixture(3)
    f.store.overdue = _overdue(1)
    f.store.gate = asyncio.Event()  # never opened
    p = SweepRoundParams(limit=10, concurrency=2, transaction_timeout=timedelta(milliseconds=20))
    async with asyncio.timeout(_WAIT):
        res = await f.builds.sweep_round(p)
    assert len(res) == 1
    assert res[0].outcome is None
    assert isinstance(res[0].error, (TimeoutError, Timeout))


@pytest.mark.asyncio
async def test_sweep_round_bounds_concurrency() -> None:
    f = _Fixture(3)
    f.store.overdue = _overdue(5)
    f.store.gate = asyncio.Event()
    f.store.load.reach = 2
    p = SweepRoundParams(limit=5, concurrency=2, transaction_timeout=timedelta(seconds=10))
    task = asyncio.create_task(f.builds.sweep_round(p))
    await _wait(f.store.load.reached)
    await _yield_loop()
    assert f.store.load.inflight == 2
    f.store.gate.set()
    async with asyncio.timeout(_WAIT):
        res = await task
    assert len(res) == 5
    assert f.store.load.max_in == 2


@pytest.mark.asyncio
async def test_sweep_round_stops_when_cancelled() -> None:
    """Cancelled while the first candidate is in flight: no further candidate starts."""
    f = _Fixture(3)
    f.store.overdue = _overdue(3)
    f.store.gate = asyncio.Event()  # never opened
    p = SweepRoundParams(limit=3, concurrency=1, transaction_timeout=timedelta(minutes=1))
    task = asyncio.create_task(f.builds.sweep_round(p))
    await _wait(f.store.load.reached)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert f.store.load.starts == 1, "a candidate started after the cancellation"
    assert f.store.sweeps == []


# --- an overdue attempt's build is cancelled (QA-10) ---


def _with_handle(candidate: OverdueBuild, ref: str | None) -> OverdueBuild:
    return OverdueBuild(
        step=candidate.step,
        attempt=candidate.attempt,
        deadline_at=candidate.deadline_at,
        log_ref=ref,
    )


def _brief(audit: _Audit) -> list[tuple[str, str, str | None]]:
    return [(e.action, e.result, e.failure_code) for e in audit.events]


@pytest.mark.asyncio
async def test_a_swept_attempt_is_audited_and_its_build_cancelled() -> None:
    f = _Fixture(3)
    f.store.overdue = [_with_handle(_overdue(1)[0], "jenkins/build/7")]
    [res] = await f.builds.sweep_round(_sweep_params())
    assert res.outcome is SweepOutcome.FAILED
    assert res.cancelled is True
    assert f.canceller.handles == [BuildHandle("jenkins/build/7")]
    assert _brief(f.audit) == [
        ("build_attempt_finished", "failed", CODE_DEADLINE_EXCEEDED),
        ("build_cancelled", "succeeded", None),
    ]
    event = f.audit.events[0]
    assert event.actor == "build-worker"
    assert event.release_id == f.store.overdue[0].step.release_id
    assert event.at == _Clock().now()


@pytest.mark.asyncio
async def test_a_swept_attempt_without_a_handle_cancels_nothing() -> None:
    f = _Fixture(3)
    f.store.overdue = _overdue(1)
    [res] = await f.builds.sweep_round(_sweep_params())
    assert res.outcome is SweepOutcome.FAILED
    assert res.cancelled is None
    assert f.canceller.handles == []
    assert _brief(f.audit) == [("build_attempt_finished", "failed", CODE_DEADLINE_EXCEEDED)]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [SweepOutcome.BUSY, SweepOutcome.STALE])
async def test_a_candidate_the_sweep_did_not_fail_is_neither_cancelled_nor_audited(
    outcome: SweepOutcome,
) -> None:
    f = _Fixture(3)
    f.store.overdue = [_with_handle(_overdue(1)[0], "jenkins/build/7")]
    f.store.outcome = lambda _c: outcome
    [res] = await f.builds.sweep_round(_sweep_params())
    assert res.outcome is outcome
    assert f.canceller.handles == []
    assert f.audit.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [BuildSystemUnavailable("down"), BuildSystemRejected("401"), RuntimeError("x")]
)
async def test_a_failed_cancel_keeps_the_attempt_failed_and_is_audited_failed(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    f = _Fixture(3)
    f.store.overdue = [_with_handle(_overdue(1)[0], "jenkins/build/7")]
    f.canceller.error = error
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        [res] = await f.builds.sweep_round(_sweep_params())
    assert res.outcome is SweepOutcome.FAILED
    assert res.error is None
    assert res.cancelled is False
    assert _brief(f.audit)[-1] == ("build_cancelled", "failed", "cancel_failed")
    assert "jenkins/build/7" in caplog.text  # the handle, an opaque reference, names the build


@pytest.mark.asyncio
async def test_a_cancel_that_hangs_is_bounded_by_the_cancel_timeout() -> None:
    f = _Fixture(3, cancel_timeout=timedelta(milliseconds=20))
    f.store.overdue = [_with_handle(_overdue(1)[0], "jenkins/build/7")]
    f.canceller.block = True
    async with asyncio.timeout(_WAIT):
        [res] = await f.builds.sweep_round(_sweep_params())
    assert res.outcome is SweepOutcome.FAILED
    assert res.cancelled is False
    assert _brief(f.audit)[-1] == ("build_cancelled", "failed", "cancel_failed")
