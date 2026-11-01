"""The operator's manual retry of an inbox row (migration 00010, decision 0011
section 12; revision 19 names): the inbox processor's retry check uses the row's max_attempts
instead of the configured one, and counts the retry deadline from last_manual_retry_at when the row
was retried by an operator, otherwise from received_at. Every processing case runs with
max_attempts 3 and deadline 1h configured, batch.now t2, against an unavailable registry, so the
only question is: retried or failed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application import (
    MAX_INT32,
    BuildDispatch,
    BuildStarted,
    CorruptRow,
    DependencyUnavailable,
    InboxBatch,
    InboxBatchParams,
    InboxEntry,
    InboxRowOutcome,
    InboxTake,
    IntakeOutcome,
    IntakeParams,
    RegisteredRepository,
    ReleaseAcceptanceDecider,
    ReleaseAcceptanceParams,
    ReleaseAcceptanceResult,
    ReleaseIntake,
    SourceEventStatus,
    TakeInboxParams,
)
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.domain import (
    CODE_REPOSITORY_MISMATCH,
    CODE_REPOSITORY_UNCONFIRMED,
    SourcePublished,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_T2 = _T0 + timedelta(minutes=2)

_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
_ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_REPO_ID = 4242
_REPO_OTHER = 777
_US = timedelta(microseconds=1)

_EVENT = SourcePublished(
    project_id=_ID_PROJECT,
    generation_request_id=_ID_REQUEST,
    repository_id=_REPO_ID,
    git_commit_sha=_SHA40,
)


def _pending_entry(attempts: int) -> InboxEntry:
    """A taken inbox row at the given attempts, received at t0, with the configured budget."""
    return InboxEntry(
        event=_EVENT,
        status=SourceEventStatus.PENDING,
        attempts=attempts,
        next_attempt_at=_T1,
        received_at=_T0,
        updated_at=_T1,
        max_attempts=3,
    )


def _inbox_row(status: SourceEventStatus) -> InboxEntry:
    """A valid stored inbox row in the status."""
    e = _pending_entry(1)
    if status is SourceEventStatus.ACCEPTED:
        e = replace(e, status=status, release_id=_ID_RELEASE, finished_at=_T1)
    elif status is SourceEventStatus.REJECTED:
        e = replace(e, status=status, outcome_code=CODE_REPOSITORY_MISMATCH, finished_at=_T1)
    elif status is SourceEventStatus.FAILED:
        e = replace(e, status=status, outcome_code=CODE_REPOSITORY_UNCONFIRMED, finished_at=_T1)
    return e


def _take_of(e: InboxEntry) -> InboxTake:
    return InboxTake(
        project_id=e.event.project_id,
        generation_request_id=e.event.generation_request_id,
        attempts=e.attempts,
    )


def _batch_params() -> InboxBatchParams:
    return InboxBatchParams(
        take=TakeInboxParams(batch_size=4, max_per_project=2, visibility=timedelta(seconds=1)),
        concurrency=2,
        registry_timeout=timedelta(milliseconds=50),
        transaction_timeout=timedelta(milliseconds=50),
        max_attempts=3,
        deadline=timedelta(hours=1),
        retry_delay=lambda attempts: timedelta(seconds=attempts),
    )


# --- doubles ---


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def new_id(self) -> UUID:
        self.n += 1
        return UUID(f"02000000-0000-7000-8000-{self.n:012x}")


class _Registry:
    """Answers every project with ``repository_id`` (sub-domain ``shop``), or raises ``error``
    when set."""

    def __init__(self, repository_id: int | None = None, error: Exception | None = None) -> None:
        self.repository_id = repository_id
        self.error = error

    async def registered_repository(self, project_id: UUID) -> RegisteredRepository | None:
        if self.error is not None:
            raise self.error
        if self.repository_id is None:
            return None
        return RegisteredRepository(repository_id=self.repository_id, subdomain="shop")


@dataclass
class _Write:
    op: str
    take: InboxTake
    code: str = ""


@dataclass
class _IntakeStore:
    batch: InboxBatch
    writes: list[_Write] = field(default_factory=list)

    async def intake(self, params: IntakeParams) -> IntakeOutcome:
        raise AssertionError("intake is not called by process_batch")

    async def take_due(self, params: TakeInboxParams) -> InboxBatch:
        return self.batch

    async def accept_release(
        self, params: ReleaseAcceptanceParams, decide: ReleaseAcceptanceDecider
    ) -> ReleaseAcceptanceResult:
        raise AssertionError("no row of these tests is accepted")

    async def reject(self, take: InboxTake, code: str) -> bool:
        self.writes.append(_Write("reject", take, code))
        return True

    async def backoff(self, take: InboxTake, delay: timedelta) -> bool:
        self.writes.append(_Write("backoff", take))
        return True

    async def fail(self, take: InboxTake, code: str) -> bool:
        self.writes.append(_Write("fail", take, code))
        return True


class _Builds:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.calls.append((project_id, release_id))
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=_ID_STEP,
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


def _intake(store: _IntakeStore, registry: _Registry) -> ReleaseIntake:
    return ReleaseIntake(
        store=store,
        registry=registry,
        ids=_Ids(),
        builds=_Builds(),
        audit=NullAuditLog(),
    )


# --- processing ---

_LONG_AGO = _T2 - timedelta(hours=48)
_RETRIED, _FAILED = InboxRowOutcome.RETRIED, InboxRowOutcome.FAILED

# Each case gives attempts, max_attempts, received_at, last_manual_retry_at and the outcome.
_BUDGET_CASES: dict[str, tuple[int, int, datetime, datetime | None, InboxRowOutcome]] = {
    # Never retried by an operator: as before, with the budget the intake wrote (the configured 3).
    "never re-driven, below the budget": (2, 3, _T0, None, _RETRIED),
    "never re-driven, at the budget": (3, 3, _T0, None, _FAILED),
    "never re-driven, deadline from received_at": (1, 3, _T2 - timedelta(hours=1), None, _FAILED),
    "never re-driven, just inside the deadline": (
        1,
        3,
        _T2 - timedelta(hours=1) + _US,
        None,
        _RETRIED,
    ),
    # The row's budget replaces the configured maximum, in both directions.
    "row budget above the configured maximum": (4, 6, _T0, None, _RETRIED),
    "row budget below the configured maximum": (2, 2, _T0, None, _FAILED),
    # Retried by an operator: the raised budget gives the extra attempts, and the deadline counts
    # from the manual retry although received_at + deadline has long passed.
    "re-driven, extra attempts": (5, 8, _LONG_AGO, _T2 - timedelta(minutes=1), _RETRIED),
    "re-driven, the raised budget spent": (8, 8, _LONG_AGO, _T2 - timedelta(minutes=1), _FAILED),
    "re-driven, deadline from the re-drive": (5, 8, _LONG_AGO, _T2 - timedelta(hours=1), _FAILED),
    "re-driven, just inside the re-drive's one": (
        5,
        8,
        _LONG_AGO,
        _T2 - timedelta(hours=1) + _US,
        _RETRIED,
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attempts", "budget", "received", "retried_at", "want"),
    list(_BUDGET_CASES.values()),
    ids=list(_BUDGET_CASES),
)
async def test_process_batch_retries_by_the_row_budget_and_the_redrive_time(
    attempts: int,
    budget: int,
    received: datetime,
    retried_at: datetime | None,
    want: InboxRowOutcome,
) -> None:
    entry = replace(_pending_entry(attempts), max_attempts=budget, received_at=received)
    if retried_at is not None:
        entry = replace(entry, last_manual_retry_by="operator-7", last_manual_retry_at=retried_at)
    store = _IntakeStore(batch=InboxBatch(now=_T2, entries=(entry,)))
    registry = _Registry(error=DependencyUnavailable("project down"))
    res = await _intake(store, registry).process_batch(_batch_params())
    assert len(res.rows) == 1
    assert res.rows[0].outcome is want
    want_write = (
        _Write("fail", _take_of(entry), CODE_REPOSITORY_UNCONFIRMED)
        if want is _FAILED
        else _Write("backoff", _take_of(entry))
    )
    assert store.writes == [want_write]


@pytest.mark.asyncio
async def test_process_batch_applies_a_definite_answer_beyond_the_budget() -> None:
    """A definite registry answer is applied whatever the budget (decision 0008 section 11)."""
    entry = replace(_pending_entry(9), max_attempts=2)
    store = _IntakeStore(batch=InboxBatch(now=_T2, entries=(entry,)))
    res = await _intake(store, _Registry(repository_id=_REPO_OTHER)).process_batch(_batch_params())
    assert len(res.rows) == 1
    assert res.rows[0].outcome is InboxRowOutcome.REJECTED
    assert store.writes == [_Write("reject", _take_of(entry), CODE_REPOSITORY_MISMATCH)]


# --- InboxEntry.validate ---

_VALID: dict[str, InboxEntry] = {
    "budget 1": replace(_inbox_row(SourceEventStatus.PENDING), max_attempts=1),
    "budget INTEGER max": replace(_inbox_row(SourceEventStatus.PENDING), max_attempts=MAX_INT32),
    "re-driven": replace(
        _inbox_row(SourceEventStatus.PENDING),
        max_attempts=9,
        last_manual_retry_by="operator-7",
        last_manual_retry_at=_T1,
    ),
    "failed, re-driven": replace(
        _inbox_row(SourceEventStatus.FAILED), last_manual_retry_by="o", last_manual_retry_at=_T0
    ),
}


@pytest.mark.parametrize("entry", list(_VALID.values()), ids=list(_VALID))
def test_inbox_entry_validate_budget_and_redrive_accepts(entry: InboxEntry) -> None:
    entry.validate()


_INVALID: dict[str, Callable[[InboxEntry], InboxEntry]] = {
    "budget 0": lambda e: replace(e, max_attempts=0),
    "negative budget": lambda e: replace(e, max_attempts=-1),
    "budget over integer": lambda e: replace(e, max_attempts=MAX_INT32 + 1),
    "re-driver without time": lambda e: replace(e, last_manual_retry_by="operator-7"),
    "time without re-driver": lambda e: replace(e, last_manual_retry_at=_T1),
    "empty re-driver": lambda e: replace(e, last_manual_retry_by="", last_manual_retry_at=_T1),
}


@pytest.mark.parametrize("mutate", list(_INVALID.values()), ids=list(_INVALID))
def test_inbox_entry_validate_budget_and_redrive(
    mutate: Callable[[InboxEntry], InboxEntry],
) -> None:
    with pytest.raises(CorruptRow):
        mutate(_inbox_row(SourceEventStatus.PENDING)).validate()
