"""The release intake use case (decision 0008 sections 1 to 3), plus the build start of each
accepted row (decision 0014)."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.application import (
    INTAKE_REASON_SUBDOMAIN_TAKEN,
    MAX_INBOX_BATCH_SIZE,
    MAX_INT32,
    REGISTRY_REASON_INVALID_ANSWER,
    REGISTRY_REASON_TIMEOUT,
    REGISTRY_REASON_UNAVAILABLE,
    BuildDispatch,
    BuildInProgress,
    BuildStarted,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    InboxBatch,
    InboxBatchParams,
    InboxEntry,
    InboxRowOutcome,
    InboxRowResult,
    InboxTake,
    IntakeOutcome,
    IntakeParams,
    ReceiveSourcePublishedCommand,
    ReceiveSourcePublishedHandler,
    RegisteredRepository,
    ReleaseAcceptanceDecider,
    ReleaseAcceptanceDecision,
    ReleaseAcceptanceFacts,
    ReleaseAcceptanceParams,
    ReleaseAcceptanceResult,
    ReleaseIntake,
    ReleaseNotBuildable,
    SourceEventStatus,
    SubdomainTaken,
    TakeInboxParams,
    Timeout,
    inbox_visibility_floor,
)
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.core.domain import (
    CODE_RELEASE_CONFLICT,
    CODE_REPOSITORY_MISMATCH,
    CODE_REPOSITORY_NOT_REGISTERED,
    CODE_REPOSITORY_UNCONFIRMED,
    MAX_REPOSITORY_ID,
    NIL_ID,
    IdempotencyConflict,
    IllegalTransition,
    InvalidCommitSha,
    InvalidId,
    InvalidRepositoryId,
    Release,
    ReleaseStatus,
    SourcePublished,
    SourcePublishedFields,
    is_error,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_T2 = _T0 + timedelta(minutes=2)

_ID_PROJECT_TEXT = "0190a1b2-0000-7000-8000-000000000001"
_ID_REQUEST_TEXT = "0190a1b2-0000-7000-8000-0000000000d1"
_ID_PROJECT = UUID(_ID_PROJECT_TEXT)
_ID_PROJECT2 = UUID("0190a1b2-0000-7000-8000-000000000002")
_ID_PROJECT_B = UUID("0190a1b2-0000-7000-8000-0000000000f2")
_ID_PROJECT_C = UUID("0190a1b2-0000-7000-8000-0000000000f3")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_REQUEST = UUID(_ID_REQUEST_TEXT)
_ID_REQUEST2 = UUID("0190a1b2-0000-7000-8000-0000000000d2")
_ID_REQUEST3 = UUID("0190a1b2-0000-7000-8000-0000000000d3")
_ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_SHA40B = "fedcba9876543210fedcba9876543210fedcba98"
_REPO_ID = 4242
_REPO_OTHER = 777
_SUBDOMAIN = "shop-1"
_INTAKE_CAP = 5
_WAIT = 5.0  # seconds a test waits for an event before it fails

_MS = timedelta(milliseconds=1)


class _EntropyGone(Exception):
    """The ID source's failure."""


class _NoAnswer(Exception):
    """The registry double has no scripted answer for a project."""


def _event(project_id: UUID, request_id: UUID) -> SourcePublished:
    return SourcePublished(
        project_id=project_id,
        generation_request_id=request_id,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
    )


def _pending_entry(e: SourcePublished, attempts: int) -> InboxEntry:
    """A taken inbox row of the event, at the given attempts, received at t0; the intake wrote
    the configured maximum (``_batch_params``: max_attempts 3)."""
    return InboxEntry(
        event=e,
        status=SourceEventStatus.PENDING,
        attempts=attempts,
        next_attempt_at=_T1,
        received_at=_T0,
        updated_at=_T1,
        max_attempts=3,
    )


def _take_of(e: InboxEntry) -> InboxTake:
    return InboxTake(
        project_id=e.event.project_id,
        generation_request_id=e.event.generation_request_id,
        attempts=e.attempts,
    )


def _fixed_delay(d: timedelta) -> Callable[[int], timedelta]:
    return lambda attempts: d * attempts


def _batch_params() -> InboxBatchParams:
    """Batch 4, 2 per project, concurrency 2, registry 50ms, transaction 50ms, visibility 1s
    (> (ceil(4/2) + 1) x 100ms), 3 attempts, deadline 1h."""
    return InboxBatchParams(
        take=TakeInboxParams(batch_size=4, max_per_project=2, visibility=timedelta(seconds=1)),
        concurrency=2,
        registry_timeout=50 * _MS,
        transaction_timeout=50 * _MS,
        max_attempts=3,
        deadline=timedelta(hours=1),
        retry_delay=_fixed_delay(timedelta(seconds=1)),
    )


def _with_take(p: InboxBatchParams, **changes: object) -> InboxBatchParams:
    return replace(p, take=replace(p.take, **changes))  # type: ignore[arg-type]  # field values vary by case


async def _yield_loop(times: int = 20) -> None:
    """Let every ready task run (a yield to the event loop, not a timed sleep)."""
    for _ in range(times):
        await asyncio.sleep(0)


async def _wait(ev: asyncio.Event) -> None:
    async with asyncio.timeout(_WAIT):
        await ev.wait()


# --- doubles ---


class _Ids:
    """Distinct non-nil identifiers; ``fail`` makes every call fail, ``fail_at`` only that call
    (counting from 1)."""

    def __init__(self) -> None:
        self.n = 0
        self.fail = False
        self.fail_at = 0

    def new_id(self) -> UUID:
        if self.fail:
            raise _EntropyGone
        self.n += 1
        if self.n == self.fail_at:
            raise _EntropyGone
        return UUID(f"02000000-0000-7000-8000-{self.n:012x}")


@dataclass
class _Answer:
    """One scripted registry answer: the registered ID (None: not registered) with its
    sub-domain, an error, or a call that blocks until it is cancelled."""

    repository_id: int | None = None
    subdomain: str = _SUBDOMAIN
    error: Exception | None = None
    block: bool = False


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


class _Registry:
    def __init__(self) -> None:
        self.answers: dict[UUID, _Answer] = {}
        self.calls: Counter[UUID] = Counter()
        self.cancelled: list[UUID] = []
        self.gate: asyncio.Event | None = None
        self.load = _Counter()

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        self.calls[project_id] += 1
        self.load.enter()
        try:
            if self.gate is not None:
                await self.gate.wait()
            a = self.answers.get(project_id)
            if a is None:
                raise _NoAnswer
            if a.block:
                await asyncio.Event().wait()
            if a.error is not None:
                raise a.error
            if a.repository_id is None:
                return None
            return RegisteredRepository(repository_id=a.repository_id, subdomain=a.subdomain)
        except asyncio.CancelledError:
            self.cancelled.append(project_id)
            raise
        finally:
            self.load.leave()

    def total_calls(self) -> int:
        return sum(self.calls.values())


@dataclass
class _Write:
    op: str  # reject, backoff, fail
    take: InboxTake
    code: str = ""
    delay: timedelta | None = None


@dataclass
class _AcceptCall:
    params: ReleaseAcceptanceParams
    decision: ReleaseAcceptanceDecision | None
    decide_error: Exception | None


@dataclass
class _IntakeStore:
    intake_params: list[IntakeParams] = field(default_factory=list)
    intake_outcome: IntakeOutcome = IntakeOutcome.STORED
    intake_error: Exception | None = None

    take_params: list[TakeInboxParams] = field(default_factory=list)
    batch: InboxBatch = field(default_factory=lambda: InboxBatch(now=_T2, entries=()))
    take_error: Exception | None = None
    take_block: bool = False

    # The facts accept_release hands decide; None means the batch's entry of the take's key with
    # no existing release, at t2.
    facts: Callable[[ReleaseAcceptanceParams], ReleaseAcceptanceFacts] | None = None
    accept_result: ReleaseAcceptanceResult | None = None
    accept_error: Exception | None = None
    accepts: list[_AcceptCall] = field(default_factory=list)
    # Releases the double reported accepted, in order: what was written before a build start.
    accepted_release_ids: list[UUID] = field(default_factory=list)

    write_applied: bool = True
    write_error: Exception | None = None
    writes: list[_Write] = field(default_factory=list)

    # Each per-row call waits on row_gate, when set, after it starts.
    row_gate: asyncio.Event | None = None
    rows: _Counter = field(default_factory=_Counter)

    async def intake(self, params: IntakeParams) -> IntakeOutcome:
        self.intake_params.append(params)
        if self.intake_error is not None:
            raise self.intake_error
        return self.intake_outcome

    async def take_due(self, params: TakeInboxParams) -> InboxBatch:
        self.take_params.append(params)
        if self.take_block:
            await asyncio.Event().wait()
        if self.take_error is not None:
            raise self.take_error
        return self.batch

    async def _row_start(self) -> None:
        self.rows.enter()
        if self.row_gate is not None:
            await self.row_gate.wait()

    async def accept_release(
        self, params: ReleaseAcceptanceParams, decide: ReleaseAcceptanceDecider
    ) -> ReleaseAcceptanceResult:
        try:
            await self._row_start()
        finally:
            self.rows.leave()
        if self.facts is not None:
            facts = self.facts(params)
        else:
            entry = next(
                e
                for e in self.batch.entries
                if e.event.project_id == params.take.project_id
                and e.event.generation_request_id == params.take.generation_request_id
            )
            facts = ReleaseAcceptanceFacts(
                entry=entry,
                existing=None,
                release_id=params.release_id,
                step_id=params.step_id,
                now=_T2,
            )
        decision: ReleaseAcceptanceDecision | None = None
        decide_error: Exception | None = None
        try:
            decision = decide(facts)
        except Exception as exc:
            decide_error = exc
        self.accepts.append(_AcceptCall(params, decision, decide_error))
        if self.accept_error is not None:
            raise self.accept_error
        if self.accept_result is not None:
            if self.accept_result.status is SourceEventStatus.ACCEPTED:
                assert self.accept_result.release_id is not None
                self.accepted_release_ids.append(self.accept_result.release_id)
            return self.accept_result
        if decide_error is not None:
            raise decide_error
        assert decision is not None
        if not decision.accepted:
            return ReleaseAcceptanceResult(
                superseded=False,
                status=SourceEventStatus.REJECTED,
                outcome_code=decision.outcome_code,
            )
        assert decision.release is not None
        self.accepted_release_ids.append(decision.release.id)
        return ReleaseAcceptanceResult(
            superseded=False,
            status=SourceEventStatus.ACCEPTED,
            release_id=decision.release.id,
            created=decision.created,
        )

    async def _write(self, w: _Write) -> bool:
        try:
            await self._row_start()
        finally:
            self.rows.leave()
        self.writes.append(w)
        if self.write_error is not None:
            raise self.write_error
        return self.write_applied

    async def reject(self, take: InboxTake, code: str) -> bool:
        return await self._write(_Write("reject", take, code=code))

    async def backoff(self, take: InboxTake, delay: timedelta) -> bool:
        return await self._write(_Write("backoff", take, delay=delay))

    async def fail(self, take: InboxTake, code: str) -> bool:
        return await self._write(_Write("fail", take, code=code))


class _Builds:
    """A recording BuildStarter. ``errors`` are raised by the next calls, in order; afterwards
    each call returns a dispatched BuildStarted."""

    def __init__(self, store: _IntakeStore) -> None:
        self.store = store
        self.calls: list[tuple[UUID, UUID]] = []
        self.written_before: list[bool] = []
        self.errors: list[Exception] = []
        self.started: list[BuildStarted] = []

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.calls.append((project_id, release_id))
        self.written_before.append(release_id in self.store.accepted_release_ids)
        if self.errors:
            raise self.errors.pop(0)
        s = BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=_ID_STEP,
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )
        self.started.append(s)
        return s


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("the intake never alerts")


class _Fixture:
    def __init__(self) -> None:
        self.store = _IntakeStore()
        self.registry = _Registry()
        self.ids = _Ids()
        self.builds = _Builds(self.store)
        self.audit = _Audit()
        self.intake = ReleaseIntake(
            store=self.store,
            registry=self.registry,
            ids=self.ids,
            builds=self.builds,
            audit=self.audit,
        )
        self.receive = ReceiveSourcePublishedHandler(
            store=self.store, max_pending_per_project=_INTAKE_CAP
        )

    def one_row(self, entry: InboxEntry, answer: _Answer) -> None:
        self.store.batch = InboxBatch(now=_T2, entries=(entry,))
        self.registry.answers[entry.event.project_id] = answer


def _registered(repository_id: int = _REPO_ID) -> _Answer:
    return _Answer(repository_id=repository_id)


def _unavailable() -> _Answer:
    return _Answer(error=DependencyUnavailable("project down"))


# --- construction and receive ---


@pytest.mark.parametrize("cap", [0, -1], ids=["zero cap", "negative"])
def test_new_release_intake_refuses_missing_dependencies_and_cap(cap: int) -> None:
    store = _IntakeStore()
    with pytest.raises(ValueError, match=r"."):
        ReceiveSourcePublishedHandler(store=store, max_pending_per_project=cap)


def test_new_release_intake_accepts_cap_one() -> None:
    store = _IntakeStore()
    ReceiveSourcePublishedHandler(store=store, max_pending_per_project=1)
    assert store.intake_params == []


def _valid_fields() -> SourcePublishedFields:
    return SourcePublishedFields(
        project_id=_ID_PROJECT_TEXT,
        generation_request_id=_ID_REQUEST_TEXT,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
    )


@pytest.mark.asyncio
async def test_receive_stores_the_parsed_event_with_the_cap() -> None:
    f = _Fixture()
    f.store.intake_outcome = IntakeOutcome.STORED
    assert (
        await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields()))
        is IntakeOutcome.STORED
    )
    assert f.store.intake_params == [
        IntakeParams(event=_event(_ID_PROJECT, _ID_REQUEST), max_pending_per_project=_INTAKE_CAP)
    ]


_NIL_TEXT = "00000000-0000-0000-0000-000000000000"
_MALFORMED_EVENTS: dict[str, tuple[SourcePublishedFields, type[Exception]]] = {
    "nil project": (replace(_valid_fields(), project_id=_NIL_TEXT), InvalidId),
    "bad request": (replace(_valid_fields(), generation_request_id="nope"), InvalidId),
    "zero repository": (replace(_valid_fields(), repository_id=0), InvalidRepositoryId),
    "huge repository": (
        replace(_valid_fields(), repository_id=(1 << 63) - 1),
        InvalidRepositoryId,
    ),
    "upper-case sha": (replace(_valid_fields(), git_commit_sha=_SHA40.upper()), InvalidCommitSha),
    "short sha": (replace(_valid_fields(), git_commit_sha=_SHA40[:39]), InvalidCommitSha),
    "empty sha": (replace(_valid_fields(), git_commit_sha=""), InvalidCommitSha),
    "nil request uuid": (replace(_valid_fields(), generation_request_id=_NIL_TEXT), InvalidId),
    "negative repo id": (replace(_valid_fields(), repository_id=-5), InvalidRepositoryId),
    "over 2^53 repo id": (
        replace(_valid_fields(), repository_id=MAX_REPOSITORY_ID + 1),
        InvalidRepositoryId,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "want"), list(_MALFORMED_EVENTS.values()), ids=list(_MALFORMED_EVENTS)
)
async def test_receive_refuses_malformed_events_and_stores_nothing(
    fields: SourcePublishedFields, want: type[Exception]
) -> None:
    f = _Fixture()
    with pytest.raises(want):
        await f.receive.handle(ReceiveSourcePublishedCommand(fields))
    assert f.store.intake_params == [], "a malformed event reached the store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [IntakeOutcome.STORED, IntakeOutcome.DUPLICATE, IntakeOutcome.OVER_CAPACITY],
    ids=["stored", "duplicate", "over capacity"],
)
async def test_receive_outcomes(outcome: IntakeOutcome) -> None:
    f = _Fixture()
    f.store.intake_outcome = outcome
    assert await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields())) is outcome


@pytest.mark.asyncio
async def test_receive_outcomes_conflict() -> None:
    f = _Fixture()
    f.store.intake_outcome = IntakeOutcome.MISMATCHED_REPEAT
    with pytest.raises(IdempotencyConflict):
        await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields()))


@pytest.mark.asyncio
async def test_receive_outcomes_store_error() -> None:
    f = _Fixture()
    f.store.intake_error = Contention("store")
    with pytest.raises(Contention):
        await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields()))


@pytest.mark.asyncio
async def test_receive_conflict_error_carries_no_source() -> None:
    """A mismatched repeat is never silent, and its error names the key but not the source."""
    f = _Fixture()
    f.store.intake_outcome = IntakeOutcome.MISMATCHED_REPEAT
    with pytest.raises(IdempotencyConflict) as exc_info:
        await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields()))
    text = str(exc_info.value)
    assert _ID_PROJECT_TEXT in text
    assert _ID_REQUEST_TEXT in text
    assert _SHA40 not in text
    assert "4242" not in text


@pytest.mark.asyncio
async def test_receive_refuses_an_unknown_store_outcome() -> None:
    f = _Fixture()
    f.store.intake_outcome = cast(IntakeOutcome, "maybe")
    with pytest.raises(CorruptRow):
        await f.receive.handle(ReceiveSourcePublishedCommand(_valid_fields()))


# --- processing parameters ---

_FLOOR_CASES: list[tuple[int, int, timedelta, timedelta, timedelta | None]] = [
    # (ceil(batch / concurrency) + 1) x (r + tx): the + 1 is the take's own transaction.
    (4, 2, 50 * _MS, 50 * _MS, 300 * _MS),
    (5, 2, timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=8)),  # three rounds
    (1, 1, timedelta(seconds=1), timedelta(seconds=2), timedelta(seconds=6)),
    (3, 4, timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=4)),  # one round
    (0, 1, timedelta(seconds=1), timedelta(seconds=1), None),
    (1, 0, timedelta(seconds=1), timedelta(seconds=1), None),
    (1, 1, timedelta(0), timedelta(seconds=1), None),
    (1, 1, timedelta(seconds=1), timedelta(0), None),
]


@pytest.mark.parametrize(
    ("batch", "concurrency", "registry", "transaction", "want"),
    _FLOOR_CASES,
    ids=[
        "4 by 2",
        "5 by 2",
        "1 by 1",
        "3 by 4",
        "zero batch",
        "zero concurrency",
        "zero registry timeout",
        "zero transaction timeout",
    ],
)
def test_inbox_visibility_floor(
    batch: int,
    concurrency: int,
    registry: timedelta,
    transaction: timedelta,
    want: timedelta | None,
) -> None:
    assert inbox_visibility_floor(batch, concurrency, registry, transaction) == want


def test_inbox_batch_params_validate_accepts_valid_params() -> None:
    _batch_params().validate()
    # Just above the floor, (ceil(4/2) + 1) x (50ms + 50ms) = 300ms.
    _with_take(_batch_params(), visibility=300 * _MS + timedelta(microseconds=1)).validate()


_BAD_BATCH_PARAMS: dict[str, Callable[[InboxBatchParams], InboxBatchParams]] = {
    "zero batch": lambda p: _with_take(p, batch_size=0),
    "batch above the maximum": lambda p: _with_take(p, batch_size=MAX_INBOX_BATCH_SIZE + 1),
    "zero per project": lambda p: _with_take(p, max_per_project=0),
    "per project above batch": lambda p: _with_take(p, max_per_project=5),
    "zero visibility": lambda p: _with_take(p, visibility=timedelta(0)),
    "zero concurrency": lambda p: replace(p, concurrency=0),
    "concurrency above batch": lambda p: replace(p, concurrency=5),
    "zero registry timeout": lambda p: replace(p, registry_timeout=timedelta(0)),
    "zero transaction": lambda p: replace(p, transaction_timeout=timedelta(0)),
    "zero attempts": lambda p: replace(p, max_attempts=0),
    "attempts above int32": lambda p: replace(p, max_attempts=MAX_INT32 + 1),
    "zero deadline": lambda p: replace(p, deadline=timedelta(0)),
    # (ceil(4/2) + 1) x (50ms + 50ms) = 300ms: the visibility must exceed it.
    "visibility equal to the floor": lambda p: _with_take(p, visibility=300 * _MS),
    "visibility below the floor": lambda p: _with_take(p, visibility=150 * _MS),
    # 250ms passed the formula without the take's transaction (200ms) and must be refused.
    "visibility without the take": lambda p: _with_take(p, visibility=250 * _MS),
    "floor overflows": lambda p: replace(p, registry_timeout=timedelta.max),
}


@pytest.mark.parametrize("mutate", list(_BAD_BATCH_PARAMS.values()), ids=list(_BAD_BATCH_PARAMS))
def test_inbox_batch_params_validate(
    mutate: Callable[[InboxBatchParams], InboxBatchParams],
) -> None:
    with pytest.raises(ValueError, match=r"."):
        mutate(_batch_params()).validate()


# --- processing ---


@pytest.mark.asyncio
async def test_process_batch_refuses_invalid_params_before_taking() -> None:
    f = _Fixture()
    with pytest.raises(ValueError, match=r"."):
        await f.intake.process_batch(_with_take(_batch_params(), visibility=100 * _MS))
    assert f.store.take_params == [], "invalid params reached the store"


@pytest.mark.asyncio
async def test_process_batch_take_error_and_empty_batch() -> None:
    f = _Fixture()
    f.store.take_error = Contention("take")
    with pytest.raises(Contention):
        await f.intake.process_batch(_batch_params())

    f = _Fixture()
    f.store.batch = InboxBatch(now=_T2, entries=())
    res = await f.intake.process_batch(_batch_params())
    assert res.rows == ()
    assert res.now == _T2
    assert f.registry.total_calls() == 0, "an empty batch asked the registry"
    assert f.store.take_params == [_batch_params().take]


@pytest.mark.asyncio
async def test_process_batch_bounds_the_take_by_the_transaction_timeout() -> None:
    f = _Fixture()
    f.store.take_block = True
    p = replace(_batch_params(), transaction_timeout=20 * _MS)
    async with asyncio.timeout(_WAIT):
        with pytest.raises((TimeoutError, Timeout)):
            await f.intake.process_batch(p)
    assert len(f.store.take_params) == 1


_MATRIX_EV = _event(_ID_PROJECT, _ID_REQUEST)


@dataclass(frozen=True)
class _MatrixCase:
    answer: Callable[[], _Answer]
    attempts: int
    received: datetime
    outcome: InboxRowOutcome
    op: str  # accept, reject, backoff, fail
    code: str = ""
    reason: str = ""
    delay: timedelta | None = None


_DECIDE_MATRIX: dict[str, _MatrixCase] = {
    "equal": _MatrixCase(_registered, 1, _T0, InboxRowOutcome.ACCEPTED, "accept"),
    "mismatch": _MatrixCase(
        lambda: _registered(_REPO_OTHER),
        1,
        _T0,
        InboxRowOutcome.REJECTED,
        "reject",
        code=CODE_REPOSITORY_MISMATCH,
    ),
    "not registered": _MatrixCase(
        _Answer, 1, _T0, InboxRowOutcome.REJECTED, "reject", code=CODE_REPOSITORY_NOT_REGISTERED
    ),
    "transient": _MatrixCase(
        _unavailable,
        2,
        _T0,
        InboxRowOutcome.RETRIED,
        "backoff",
        reason=REGISTRY_REASON_UNAVAILABLE,
        delay=timedelta(seconds=2),
    ),
    "invalid answer": _MatrixCase(
        lambda: _registered(0),
        1,
        _T0,
        InboxRowOutcome.RETRIED,
        "backoff",
        reason=REGISTRY_REASON_INVALID_ANSWER,
        delay=timedelta(seconds=1),
    ),
    "invalid subdomain answer": _MatrixCase(
        lambda: _Answer(repository_id=_REPO_ID, subdomain="Not-A-Label"),
        1,
        _T0,
        InboxRowOutcome.RETRIED,
        "backoff",
        reason=REGISTRY_REASON_INVALID_ANSWER,
        delay=timedelta(seconds=1),
    ),
    "exhausted by attempts": _MatrixCase(
        _unavailable,
        3,
        _T0,
        InboxRowOutcome.FAILED,
        "fail",
        code=CODE_REPOSITORY_UNCONFIRMED,
        reason=REGISTRY_REASON_UNAVAILABLE,
    ),
    "above the attempts": _MatrixCase(
        _unavailable,
        4,
        _T0,
        InboxRowOutcome.FAILED,
        "fail",
        code=CODE_REPOSITORY_UNCONFIRMED,
        reason=REGISTRY_REASON_UNAVAILABLE,
    ),
    # batch.now is t2; received an hour before it: the deadline (1h) has just passed.
    "exhausted by deadline": _MatrixCase(
        _unavailable,
        1,
        _T2 - timedelta(hours=1),
        InboxRowOutcome.FAILED,
        "fail",
        code=CODE_REPOSITORY_UNCONFIRMED,
        reason=REGISTRY_REASON_UNAVAILABLE,
    ),
    "just inside deadline": _MatrixCase(
        _unavailable,
        1,
        _T2 - timedelta(hours=1) + timedelta(microseconds=1),
        InboxRowOutcome.RETRIED,
        "backoff",
        reason=REGISTRY_REASON_UNAVAILABLE,
        delay=timedelta(seconds=1),
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("c", list(_DECIDE_MATRIX.values()), ids=list(_DECIDE_MATRIX))
async def test_process_batch_decide_matrix(c: _MatrixCase) -> None:
    """The decide matrix of decision 0008 sections 2 and 3, one taken row per case.

    FR-9.1, FR-9.2, FR-9.3."""
    f = _Fixture()
    entry = replace(_pending_entry(_MATRIX_EV, c.attempts), received_at=c.received)
    f.one_row(entry, c.answer())
    res = await f.intake.process_batch(_batch_params())
    assert len(res.rows) == 1
    row = res.rows[0]
    take = _take_of(entry)
    assert row.take == take
    assert row.outcome is c.outcome
    assert row.code == c.code
    assert row.reason == c.reason
    assert row.error is None
    if c.op == "accept":
        assert len(f.store.accepts) == 1
        assert f.store.writes == []
        a = f.store.accepts[0]
        assert a.params.take == take
        assert a.params.release_id != NIL_ID
        assert a.params.step_id != NIL_ID
        assert a.params.release_id != a.params.step_id
        assert a.decision is not None
        assert a.decision.created
        assert a.decision.release is not None
        assert a.decision.release.id == a.params.release_id
        assert a.decision.step is not None
        assert a.decision.step.id == a.params.step_id
        assert a.decision.hosting is not None
        assert a.decision.hosting.subdomain == _SUBDOMAIN
        assert row.release_id == a.params.release_id
        assert row.created
    else:
        assert f.store.accepts == []
        assert f.store.writes == [_Write(c.op, take, code=c.code, delay=c.delay)]
        if c.op == "backoff":
            assert row.retry_after == c.delay
    # Audit: an accepted, rejected or failed event is one release_accepted line; a retry none.
    expected = {
        "accept": [(AuditResult.ACCEPTED, None, row.release_id)],
        "reject": [(AuditResult.REFUSED, c.code, None)],
        "fail": [(AuditResult.FAILED, c.code, None)],
        "backoff": [],
    }[c.op]
    assert [(e.result, e.failure_code, e.release_id) for e in f.audit.events] == expected
    for event in f.audit.events:
        assert event.action is AuditAction.RELEASE_ACCEPTED
        assert (event.actor_kind, event.actor) == (AuditActorKind.EVENT, "project")
        assert event.project_id == _MATRIX_EV.project_id
        assert event.at == _T2
        assert event.command_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [_registered, _Answer, _unavailable],
    ids=["accept", "reject", "backoff"],
)
async def test_process_batch_bounds_each_row_by_the_transaction_timeout(
    answer: Callable[[], _Answer],
) -> None:
    """Every row's store call is bounded by the transaction timeout; a row that runs out of it is
    an error row, and the batch still returns."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, answer())
    f.store.row_gate = asyncio.Event()  # never opened
    p = replace(_batch_params(), transaction_timeout=20 * _MS)
    async with asyncio.timeout(_WAIT):
        res = await f.intake.process_batch(p)
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.ERROR
    assert isinstance(row.error, (TimeoutError, Timeout))
    assert f.builds.calls == []


@pytest.mark.asyncio
async def test_process_batch_registry_timeout_is_transient() -> None:
    """A registry call that runs out of its own timeout is transient, with the timeout reason."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _Answer(block=True))
    p = replace(_batch_params(), registry_timeout=20 * _MS)
    async with asyncio.timeout(_WAIT):
        res = await f.intake.process_batch(p)
    assert len(res.rows) == 1
    assert res.rows[0].outcome is InboxRowOutcome.RETRIED
    assert res.rows[0].reason == REGISTRY_REASON_TIMEOUT
    assert f.registry.cancelled == [_ID_PROJECT], "the timed-out registry call was not cancelled"


@pytest.mark.asyncio
async def test_process_batch_raises_a_tiny_retry_delay() -> None:
    """A retry delay below the precision of timestamptz is raised to one microsecond."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _unavailable())
    p = replace(_batch_params(), retry_delay=lambda _attempts: timedelta(0))
    res = await f.intake.process_batch(p)
    one_us = timedelta(microseconds=1)
    assert [w.delay for w in f.store.writes] == [one_us]
    assert res.rows[0].retry_after == one_us


@pytest.mark.asyncio
async def test_process_batch_asks_the_registry_once_per_project() -> None:
    """One registry call per project per batch; every row of the project is decided on it."""
    f = _Fixture()
    a1, a2, a3 = (
        _event(_ID_PROJECT, _ID_REQUEST),
        _event(_ID_PROJECT, _ID_REQUEST2),
        _event(_ID_PROJECT, _ID_REQUEST3),
    )
    b1, b2 = _event(_ID_PROJECT_B, _ID_REQUEST), _event(_ID_PROJECT_B, _ID_REQUEST2)
    entries = (
        _pending_entry(a1, 1),
        _pending_entry(b1, 1),
        _pending_entry(a2, 1),
        _pending_entry(b2, 2),
        _pending_entry(a3, 1),
    )
    f.store.batch = InboxBatch(now=_T2, entries=entries)
    f.registry.answers[_ID_PROJECT] = _registered()
    f.registry.answers[_ID_PROJECT_B] = _Answer()
    p = replace(
        _batch_params(),
        take=TakeInboxParams(batch_size=5, max_per_project=3, visibility=timedelta(seconds=1)),
    )
    res = await f.intake.process_batch(p)
    assert f.registry.calls[_ID_PROJECT] == 1
    assert f.registry.calls[_ID_PROJECT_B] == 1
    assert f.registry.total_calls() == 2
    assert len(res.rows) == 5
    for i, row in enumerate(res.rows):
        want = (
            InboxRowOutcome.REJECTED
            if row.take.project_id == _ID_PROJECT_B
            else InboxRowOutcome.ACCEPTED
        )
        assert row.take == _take_of(entries[i]), f"row {i} out of batch order"
        assert row.outcome is want
    assert len(f.store.accepts) == 3
    assert len(f.store.writes) == 2
    ids = [a.params.release_id for a in f.store.accepts] + [
        a.params.step_id for a in f.store.accepts
    ]
    assert len(set(ids)) == len(ids), "an ID was reused"


@pytest.mark.asyncio
async def test_process_batch_stops_when_the_registry_is_not_configured() -> None:
    """DependencyNotConfigured is not transient: the batch stops and no row is written, including
    rows of other projects whose answer was fine."""
    f = _Fixture()
    a, b = _event(_ID_PROJECT, _ID_REQUEST), _event(_ID_PROJECT_B, _ID_REQUEST)
    f.store.batch = InboxBatch(now=_T2, entries=(_pending_entry(a, 1), _pending_entry(b, 1)))
    f.registry.answers[_ID_PROJECT] = _registered()
    f.registry.answers[_ID_PROJECT_B] = _Answer(error=DependencyNotConfigured("adapter text OD-01"))
    with pytest.raises(DependencyNotConfigured) as exc_info:
        await f.intake.process_batch(_batch_params())
    assert "adapter text" not in str(exc_info.value), "the error carries the adapter's text"
    assert f.store.accepts == []
    assert f.store.writes == []
    assert f.builds.calls == []


@pytest.mark.asyncio
async def test_not_configured_cancels_the_other_registry_calls() -> None:
    """A not-configured answer cancels the registry calls still in flight, so the batch returns at
    once instead of waiting for their timeouts."""
    f = _Fixture()
    a, b = _event(_ID_PROJECT, _ID_REQUEST), _event(_ID_PROJECT_B, _ID_REQUEST)
    f.store.batch = InboxBatch(now=_T2, entries=(_pending_entry(a, 1), _pending_entry(b, 1)))
    f.registry.answers[_ID_PROJECT] = _Answer(block=True)
    f.registry.answers[_ID_PROJECT_B] = _Answer(error=DependencyNotConfigured("not configured"))
    p = _with_take(
        replace(_batch_params(), registry_timeout=timedelta(hours=1)), visibility=timedelta(hours=4)
    )
    async with asyncio.timeout(_WAIT):
        with pytest.raises(DependencyNotConfigured):
            await f.intake.process_batch(p)
    assert f.registry.cancelled == [_ID_PROJECT]


@pytest.mark.asyncio
async def test_process_batch_cancelled_during_registry_writes_nothing() -> None:
    """A batch cancelled while the registry is asked writes nothing."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _Answer(block=True))
    p = _with_take(
        replace(_batch_params(), registry_timeout=timedelta(hours=1)), visibility=timedelta(hours=4)
    )
    task = asyncio.create_task(f.intake.process_batch(p))
    await _wait(f.registry.load.reached)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert f.store.accepts == []
    assert f.store.writes == []
    assert f.registry.cancelled == [_ID_PROJECT]


@pytest.mark.asyncio
async def test_process_batch_cancelled_during_rows_stops_starting_rows() -> None:
    """A batch cancelled while rows are written starts no further row."""
    f = _Fixture()
    entries = tuple(
        _pending_entry(_event(_ID_PROJECT, r), 1) for r in (_ID_REQUEST, _ID_REQUEST2, _ID_REQUEST3)
    )
    f.store.batch = InboxBatch(now=_T2, entries=entries)
    f.registry.answers[_ID_PROJECT] = _Answer()
    f.store.row_gate = asyncio.Event()  # never opened: each row waits until it is cancelled
    p = replace(
        _batch_params(),
        take=TakeInboxParams(batch_size=3, max_per_project=3, visibility=timedelta(hours=1)),
        concurrency=1,
        transaction_timeout=timedelta(minutes=1),
    )
    task = asyncio.create_task(f.intake.process_batch(p))
    await _wait(f.store.rows.reached)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert f.store.rows.starts == 1, "a row started after the cancellation"
    assert f.store.writes == []


@pytest.mark.asyncio
async def test_process_batch_bounds_concurrency() -> None:
    """Registry calls and row writes each run at most ``concurrency`` at a time, and reach it."""
    f = _Fixture()
    projects = (_ID_PROJECT, _ID_PROJECT_B, _ID_PROJECT_C, _ID_PROJECT2)
    f.store.batch = InboxBatch(
        now=_T2, entries=tuple(_pending_entry(_event(p, _ID_REQUEST), 1) for p in projects)
    )
    for p in projects:
        f.registry.answers[p] = _Answer()
    f.registry.gate = asyncio.Event()
    f.registry.load.reach = 2
    f.store.row_gate = asyncio.Event()
    f.store.rows.reach = 2
    params = replace(
        _batch_params(),
        take=TakeInboxParams(batch_size=4, max_per_project=1, visibility=timedelta(hours=1)),
        concurrency=2,
        registry_timeout=timedelta(seconds=10),
        transaction_timeout=timedelta(seconds=10),
    )
    task = asyncio.create_task(f.intake.process_batch(params))
    await _wait(f.registry.load.reached)
    await _yield_loop()
    assert f.registry.load.inflight == 2
    f.registry.gate.set()
    await _wait(f.store.rows.reached)
    await _yield_loop()
    assert f.store.rows.inflight == 2
    f.store.row_gate.set()
    async with asyncio.timeout(_WAIT):
        res = await task
    assert len(res.rows) == 4
    assert f.registry.load.max_in == 2
    assert f.store.rows.max_in == 2


_STORED = Release(
    id=_ID_RELEASE,
    project_id=_ID_PROJECT,
    generation_request_id=_ID_REQUEST,
    repository_id=_REPO_ID,
    git_commit_sha=_SHA40,
    status=ReleaseStatus.ACCEPTED,
    created_at=_T0,
    updated_at=_T0,
)
_DECIDE_ENTRY = _pending_entry(_MATRIX_EV, 1)

_FactsFn = Callable[[ReleaseAcceptanceFacts], ReleaseAcceptanceFacts]


@dataclass(frozen=True)
class _DecideCase:
    facts: _FactsFn
    accept: bool = False
    code: str = ""
    error: type[Exception] | None = None


_DECIDE_FUNCTION: dict[str, _DecideCase] = {
    "existing release": _DecideCase(lambda f: replace(f, existing=_STORED), accept=True),
    "conflicting release": _DecideCase(
        lambda f: replace(f, existing=replace(_STORED, git_commit_sha=_SHA40B)),
        code=CODE_RELEASE_CONFLICT,
    ),
    "another event": _DecideCase(
        lambda f: replace(f, entry=_pending_entry(_event(_ID_PROJECT, _ID_REQUEST2), 1)),
        error=CorruptRow,
    ),
    "entry no longer pending": _DecideCase(
        lambda f: replace(f, entry=replace(f.entry, status=SourceEventStatus.ACCEPTED)),
        error=IllegalTransition,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("c", list(_DECIDE_FUNCTION.values()), ids=list(_DECIDE_FUNCTION))
async def test_process_batch_decide_function(c: _DecideCase) -> None:
    """A stored release with the event's source is accepted as it is, one with another source is
    rejected release_conflict, and facts for another event are refused."""
    f = _Fixture()
    f.one_row(_DECIDE_ENTRY, _registered())

    def facts(p: ReleaseAcceptanceParams) -> ReleaseAcceptanceFacts:
        return c.facts(
            ReleaseAcceptanceFacts(
                entry=_DECIDE_ENTRY,
                existing=None,
                release_id=p.release_id,
                step_id=p.step_id,
                now=_T2,
            )
        )

    f.store.facts = facts
    res = await f.intake.process_batch(_batch_params())
    assert len(f.store.accepts) == 1
    a, row = f.store.accepts[0], res.rows[0]
    if c.error is not None:
        assert a.decision is None
        assert is_error(a.decide_error, c.error)
        assert row.outcome is InboxRowOutcome.ERROR
        assert is_error(row.error, c.error)
        return
    assert a.decide_error is None
    assert a.decision is not None
    assert a.decision.accepted is c.accept
    assert not a.decision.created
    assert a.decision.outcome_code == c.code
    if c.accept:
        assert row.outcome is InboxRowOutcome.ACCEPTED
        assert row.release_id == _STORED.id
        assert not row.created
    else:
        assert row.outcome is InboxRowOutcome.REJECTED
        assert row.code == c.code


_ACCEPTANCE_RESULTS: dict[
    str,
    tuple[
        ReleaseAcceptanceResult | None,
        Exception | None,
        InboxRowOutcome,
        str,
        type[Exception] | None,
    ],
] = {
    "superseded": (
        ReleaseAcceptanceResult(superseded=True),
        None,
        InboxRowOutcome.SUPERSEDED,
        "",
        None,
    ),
    "rejected": (
        ReleaseAcceptanceResult(
            superseded=False, status=SourceEventStatus.REJECTED, outcome_code=CODE_RELEASE_CONFLICT
        ),
        None,
        InboxRowOutcome.REJECTED,
        CODE_RELEASE_CONFLICT,
        None,
    ),
    "error": (None, Contention("x"), InboxRowOutcome.ERROR, "", Contention),
    "pending": (
        ReleaseAcceptanceResult(superseded=False, status=SourceEventStatus.PENDING),
        None,
        InboxRowOutcome.ERROR,
        "",
        None,
    ),
    "failed": (
        ReleaseAcceptanceResult(superseded=False, status=SourceEventStatus.FAILED),
        None,
        InboxRowOutcome.ERROR,
        "",
        None,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "error", "outcome", "code", "want_error"),
    list(_ACCEPTANCE_RESULTS.values()),
    ids=list(_ACCEPTANCE_RESULTS),
)
async def test_process_batch_acceptance_results(
    result: ReleaseAcceptanceResult | None,
    error: Exception | None,
    outcome: InboxRowOutcome,
    code: str,
    want_error: type[Exception] | None,
) -> None:
    """What the acceptance transaction reports becomes the row's outcome."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 1), _registered())
    f.store.accept_result, f.store.accept_error = result, error
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is outcome
    assert row.code == code
    if outcome is InboxRowOutcome.ERROR:
        assert row.error is not None, "error row without an error"
    else:
        assert row.error is None
    if want_error is not None:
        assert is_error(row.error, want_error)


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [1, 2], ids=["first", "second"])
async def test_l2_a_taken_subdomain_within_the_budget_is_an_error_row(attempts: int) -> None:
    """FR-54 (TR-53; Task 12 security re-review L2): a sub-domain another project holds
    is retried like any row error while the row's attempt budget (3) lasts; nothing is
    written, and the row comes due after the visibility timeout."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, attempts), _registered())
    f.store.accept_error = SubdomainTaken("taken")
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.ERROR
    assert is_error(row.error, SubdomainTaken)
    assert f.store.writes == []
    assert f.audit.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [3, 4], ids=["at the budget", "past the budget"])
async def test_l2_a_taken_subdomain_with_the_budget_spent_fails_the_row(attempts: int) -> None:
    """FR-54.5 (TR-53; Task 12 security re-review L2): once the row's attempts reach its
    max_attempts, a sub-domain another project holds fails the row with the existing code
    repository_unconfirmed (no new code; ``subdomain_taken`` is the owner's catalogue decision),
    with the reason ``subdomain_taken``, so it is never taken again and an operator sees it."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, attempts)
    f.one_row(entry, _registered())
    f.store.accept_error = SubdomainTaken("taken")
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.FAILED
    assert row.code == CODE_REPOSITORY_UNCONFIRMED
    assert row.reason == INTAKE_REASON_SUBDOMAIN_TAKEN
    assert is_error(row.error, SubdomainTaken)
    assert f.store.writes == [_Write("fail", _take_of(entry), code=CODE_REPOSITORY_UNCONFIRMED)]
    assert [(e.result, e.failure_code) for e in f.audit.events] == [
        (AuditResult.FAILED, CODE_REPOSITORY_UNCONFIRMED)
    ]
    assert f.builds.calls == []


@pytest.mark.asyncio
async def test_l2_a_taken_subdomain_fail_that_no_longer_applies_is_superseded() -> None:
    """Task 12 security re-review L2: the fail is conditional on the take like every write."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 3), _registered())
    f.store.accept_error = SubdomainTaken("taken")
    f.store.write_applied = False
    res = await f.intake.process_batch(_batch_params())
    assert [w.op for w in f.store.writes] == ["fail"]
    assert res.rows[0].outcome is InboxRowOutcome.SUPERSEDED


@pytest.mark.asyncio
async def test_l2_a_taken_subdomain_fail_that_errors_is_an_error_row() -> None:
    """Task 12 security re-review L2: a failed fail write leaves an error row, as any write."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 3), _registered())
    f.store.accept_error = SubdomainTaken("taken")
    f.store.write_error = Contention("w")
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.ERROR
    assert is_error(row.error, Contention)


@pytest.mark.asyncio
async def test_l2_other_row_errors_past_the_budget_stay_error_rows() -> None:
    """Task 12 security re-review L2 is narrow: only a taken sub-domain spends the budget on the
    error path; a transient error past it stays an error row and is never failed."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 5), _registered())
    f.store.accept_error = Contention("x")
    res = await f.intake.process_batch(_batch_params())
    assert res.rows[0].outcome is InboxRowOutcome.ERROR
    assert f.store.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "applied"),
    [(_Answer, False), (_Answer, True), (_unavailable, False), (_unavailable, True)],
    ids=["reject not applied", "reject applied", "backoff not applied", "backoff applied"],
)
async def test_process_batch_conditional_writes(
    answer: Callable[[], _Answer], applied: bool
) -> None:
    """A conditional write that no longer applies is superseded."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 1), answer())
    f.store.write_applied = applied
    res = await f.intake.process_batch(_batch_params())
    assert (res.rows[0].outcome is InboxRowOutcome.SUPERSEDED) is (not applied)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [_Answer, _unavailable], ids=["reject", "backoff"])
async def test_process_batch_conditional_writes_error(answer: Callable[[], _Answer]) -> None:
    """A conditional write that fails is a row error."""
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 1), answer())
    f.store.write_error = Contention("w")
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.ERROR
    assert is_error(row.error, Contention)


@pytest.mark.asyncio
async def test_process_batch_conditional_writes_fail_superseded() -> None:
    f = _Fixture()
    f.one_row(_pending_entry(_MATRIX_EV, 3), _unavailable())
    f.store.write_applied = False
    res = await f.intake.process_batch(_batch_params())
    assert [w.op for w in f.store.writes] == ["fail"]
    assert res.rows[0].outcome is InboxRowOutcome.SUPERSEDED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail", "fail_at"), [(True, 0), (False, 2)], ids=["release ID", "step ID"]
)
async def test_process_batch_id_source_failure(fail: bool, fail_at: int) -> None:
    """Without fresh IDs nothing is accepted: the row is an error and comes due again."""
    f = _Fixture()
    f.ids.fail, f.ids.fail_at = fail, fail_at
    f.one_row(_pending_entry(_MATRIX_EV, 1), _registered())
    res = await f.intake.process_batch(_batch_params())
    assert f.store.accepts == []
    assert res.rows[0].outcome is InboxRowOutcome.ERROR
    assert res.rows[0].error is not None
    assert f.builds.calls == []


# --- build start of accepted rows (decision 0014) ---


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True], ids=["created", "existing"])
async def test_process_batch_starts_the_build_of_each_accepted_row(existing: bool) -> None:
    """An accepted row, created or existing, starts its release's build once, after the row was
    written, and carries the start's result."""
    f = _Fixture()
    f.one_row(_DECIDE_ENTRY, _registered())
    if existing:
        f.store.facts = lambda p: ReleaseAcceptanceFacts(
            entry=_DECIDE_ENTRY,
            existing=_STORED,
            release_id=p.release_id,
            step_id=p.step_id,
            now=_T2,
        )
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert row.outcome is InboxRowOutcome.ACCEPTED
    assert row.created is (not existing)
    assert row.release_id is not None
    if existing:
        assert row.release_id == _STORED.id
    assert f.builds.calls == [(_ID_PROJECT, row.release_id)]
    assert f.builds.written_before == [True], "the build started before its row was written"
    assert row.build == f.builds.started[0]
    assert row.build_error is None


def _rejected_by_conflict(f: _Fixture) -> None:
    f.one_row(_DECIDE_ENTRY, _registered())
    f.store.facts = lambda p: ReleaseAcceptanceFacts(
        entry=_DECIDE_ENTRY,
        existing=replace(_STORED, git_commit_sha=_SHA40B),
        release_id=p.release_id,
        step_id=p.step_id,
        now=_T2,
    )


def _superseded(f: _Fixture) -> None:
    f.one_row(_DECIDE_ENTRY, _registered())
    f.store.accept_result = ReleaseAcceptanceResult(superseded=True)


def _accept_error(f: _Fixture) -> None:
    f.one_row(_DECIDE_ENTRY, _registered())
    f.store.accept_error = Contention("x")


_NO_BUILD: dict[str, tuple[Callable[[_Fixture], None], InboxRowOutcome]] = {
    "rejected": (lambda f: f.one_row(_DECIDE_ENTRY, _Answer()), InboxRowOutcome.REJECTED),
    "rejected release_conflict": (_rejected_by_conflict, InboxRowOutcome.REJECTED),
    "retried": (lambda f: f.one_row(_DECIDE_ENTRY, _unavailable()), InboxRowOutcome.RETRIED),
    "failed": (
        lambda f: f.one_row(_pending_entry(_MATRIX_EV, 3), _unavailable()),
        InboxRowOutcome.FAILED,
    ),
    "superseded": (_superseded, InboxRowOutcome.SUPERSEDED),
    "error": (_accept_error, InboxRowOutcome.ERROR),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(("setup", "outcome"), list(_NO_BUILD.values()), ids=list(_NO_BUILD))
async def test_process_batch_starts_no_build_for_other_rows(
    setup: Callable[[_Fixture], None], outcome: InboxRowOutcome
) -> None:
    f = _Fixture()
    setup(f)
    res = await f.intake.process_batch(_batch_params())
    assert res.rows[0].outcome is outcome
    assert f.builds.calls == []
    assert res.rows[0].build is None
    assert res.rows[0].build_error is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [BuildInProgress, ReleaseNotBuildable],
    ids=["build in progress", "release not buildable"],
)
async def test_process_batch_build_already_started_is_not_an_error(
    error: type[Exception],
) -> None:
    """BuildInProgress and ReleaseNotBuildable mean the build runs or ran: the row stays accepted
    with no build error."""
    f = _Fixture()
    f.one_row(_DECIDE_ENTRY, _registered())
    f.builds.errors = [error()]
    res = await f.intake.process_batch(_batch_params())
    row = res.rows[0]
    assert len(f.builds.calls) == 1
    assert row.outcome is InboxRowOutcome.ACCEPTED
    assert row.release_id is not None
    assert row.build_error is None
    assert row.error is None


@pytest.mark.asyncio
async def test_process_batch_reports_a_build_start_error_in_the_row() -> None:
    """Any other starter error is reported in the row's build_error; the row stays accepted and
    the batch goes on."""
    f = _Fixture()
    entries = (
        _pending_entry(_event(_ID_PROJECT, _ID_REQUEST), 1),
        _pending_entry(_event(_ID_PROJECT, _ID_REQUEST2), 1),
    )
    f.store.batch = InboxBatch(now=_T2, entries=entries)
    f.registry.answers[_ID_PROJECT] = _registered()
    f.builds.errors = [Contention("build store")]
    res = await f.intake.process_batch(_batch_params())
    assert len(res.rows) == 2
    assert len(f.builds.calls) == 2
    assert all(r.outcome is InboxRowOutcome.ACCEPTED for r in res.rows)
    assert all(r.error is None for r in res.rows)
    failed: list[InboxRowResult] = [r for r in res.rows if r.build_error is not None]
    started = [r for r in res.rows if r.build is not None]
    assert len(failed) == 1
    assert len(started) == 1
    assert is_error(failed[0].build_error, Contention)
    assert failed[0].build is None


# --- the sub-domain (decision 0021 D8: the repository lookup answers it) -----------------------


@pytest.mark.asyncio
async def test_release_intake_copies_the_subdomain_from_the_registry() -> None:
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _Answer(repository_id=_REPO_ID, subdomain="my-site"))
    res = await f.intake.process_batch(_batch_params())
    assert [r.outcome for r in res.rows] == [InboxRowOutcome.ACCEPTED]
    (accept,) = f.store.accepts
    assert accept.decision is not None
    assert accept.decision.hosting is not None
    assert accept.decision.hosting.subdomain == "my-site"


@pytest.mark.asyncio
@pytest.mark.parametrize("subdomain", ["", "-a", "a" * 64, "a.b", "UPPER"])
async def test_invalid_subdomain_answer_retries(subdomain: str) -> None:
    """A sub-domain that is not a DNS label is an unusable answer, like an out-of-range repository
    ID: the row is retried (transient), never accepted or rejected."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _Answer(repository_id=_REPO_ID, subdomain=subdomain))
    res = await f.intake.process_batch(_batch_params())
    (row,) = res.rows
    assert row.outcome is InboxRowOutcome.RETRIED
    assert row.reason == REGISTRY_REASON_INVALID_ANSWER
    assert f.store.accepts == []


@pytest.mark.asyncio
async def test_fr54_a_bug_in_the_answer_check_is_not_an_invalid_answer() -> None:
    """FR-54, TR-53: only a domain refusal of the registry's answer is ``invalid_answer``. A
    non-domain error while checking it (here a repository ID of the wrong type, as a broken
    adapter would return) is a bug: the batch fails loudly and no row is retried or written."""
    f = _Fixture()
    entry = _pending_entry(_MATRIX_EV, 1)
    f.one_row(entry, _Answer(repository_id=cast("int", "7")))
    with pytest.raises(ExceptionGroup) as caught:
        await f.intake.process_batch(_batch_params())
    assert caught.group_contains(TypeError)
    assert f.store.accepts == []
    assert f.store.writes == []
