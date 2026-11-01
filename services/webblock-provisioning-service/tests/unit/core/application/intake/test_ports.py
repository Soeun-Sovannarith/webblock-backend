"""The inbox row of Project's "source published" event (provisioning_ops.source_published_events)
and the release acceptance decisions (decision 0008 sections 1 to 3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.application import (
    MAX_INT32,
    CorruptRow,
    InboxEntry,
    InboxTake,
    IntakeOutcome,
    IntakeParams,
    ReleaseAcceptanceFacts,
    SourceEventStatus,
    TakeInboxParams,
    accept_existing_release,
    accept_new_release,
    legal_source_event_transition,
    parse_source_event_status,
    reject_release,
)
from provisioning.core.domain import (
    CODE_RELEASE_CONFLICT,
    CODE_REPOSITORY_MISMATCH,
    CODE_REPOSITORY_NOT_REGISTERED,
    CODE_REPOSITORY_UNCONFIRMED,
    NIL_ID,
    DomainError,
    HostingStatus,
    IdempotencyConflict,
    IllegalTransition,
    InvalidAttempt,
    InvalidCommitSha,
    InvalidFailureCode,
    InvalidId,
    InvalidRepositoryId,
    InvalidSubdomain,
    MissingFailureCode,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
    SourcePublished,
    UnknownEnumValue,
    is_error,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_T2 = _T0 + timedelta(minutes=2)

_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_RELEASE2 = UUID("0190a1b2-0000-7000-8000-0000000000a2")
_ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
_ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_REPO_ID = 4242
_DIGEST = "sha256:" + "a" * 64

_S = SourceEventStatus


def _source_event() -> SourcePublished:
    return SourcePublished(
        project_id=_ID_PROJECT,
        generation_request_id=_ID_REQUEST,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
    )


def _inbox_row(status: SourceEventStatus) -> InboxEntry:
    """A valid stored inbox row in the status: a pending row as a take left it, a terminal row
    with its outcome, release and finish as the table requires."""
    e = InboxEntry(
        event=_source_event(),
        status=status,
        attempts=1,
        next_attempt_at=_T1,
        received_at=_T0,
        updated_at=_T1,
        max_attempts=3,
    )
    if status is _S.ACCEPTED:
        e = replace(e, release_id=_ID_RELEASE, finished_at=_T1)
    elif status is _S.REJECTED:
        e = replace(e, outcome_code=CODE_REPOSITORY_MISMATCH, finished_at=_T1)
    elif status is _S.FAILED:
        e = replace(e, outcome_code=CODE_REPOSITORY_UNCONFIRMED, finished_at=_T1)
    return e


def _with_event(e: InboxEntry, **changes: object) -> InboxEntry:
    return replace(e, event=replace(e.event, **changes))  # type: ignore[arg-type]  # field values vary by case


# --- statuses ---


def test_inbox_statuses() -> None:
    assert [str(s) for s in SourceEventStatus] == ["pending", "accepted", "rejected", "failed"]
    for s in SourceEventStatus:
        assert parse_source_event_status(str(s)) is s
        assert s.terminal() is (s is not _S.PENDING)
    for bad in ("", "Pending", "done", "pending "):
        with pytest.raises(UnknownEnumValue) as exc_info:
            parse_source_event_status(bad)
        if bad.strip():
            assert bad not in str(exc_info.value)


def test_inbox_transition_matrix() -> None:
    """Decision 0008 section 2: pending -> accepted | rejected | failed only."""
    legal = {(_S.PENDING, _S.ACCEPTED), (_S.PENDING, _S.REJECTED), (_S.PENDING, _S.FAILED)}
    count = 0
    for from_ in SourceEventStatus:
        for to in SourceEventStatus:
            count += 1
            assert legal_source_event_transition(from_, to) is ((from_, to) in legal), (from_, to)
    assert count == 16
    bogus = cast(SourceEventStatus, "bogus")
    assert not legal_source_event_transition(bogus, _S.ACCEPTED)
    assert not legal_source_event_transition(_S.PENDING, bogus)


# --- InboxEntry.validate ---


@pytest.mark.parametrize("status", list(SourceEventStatus), ids=[str(s) for s in SourceEventStatus])
def test_inbox_entry_validate_accepts_valid_rows(status: SourceEventStatus) -> None:
    _inbox_row(status).validate()


def test_inbox_entry_validate_accepts_fresh_and_conflicted_rows() -> None:
    fresh = replace(_inbox_row(_S.PENDING), attempts=0, updated_at=_T0, next_attempt_at=_T0)
    conflicted = replace(
        _inbox_row(_S.ACCEPTED), mismatched_repeat_count=3, last_mismatched_repeat_at=_T2
    )
    fresh.validate()
    conflicted.validate()


_Mutate = Callable[[InboxEntry], InboxEntry]

_INVALID_ROWS: dict[str, tuple[SourceEventStatus, _Mutate, type[DomainError] | None]] = {
    "zero project": (_S.PENDING, lambda e: _with_event(e, project_id=NIL_ID), InvalidId),
    "zero generation request": (
        _S.PENDING,
        lambda e: _with_event(e, generation_request_id=NIL_ID),
        InvalidId,
    ),
    "repository out of range": (
        _S.PENDING,
        lambda e: _with_event(e, repository_id=0),
        InvalidRepositoryId,
    ),
    "malformed SHA": (
        _S.PENDING,
        lambda e: _with_event(e, git_commit_sha="main"),
        InvalidCommitSha,
    ),
    "unknown status": (
        _S.PENDING,
        lambda e: replace(e, status=cast(SourceEventStatus, "done")),
        UnknownEnumValue,
    ),
    "negative attempts": (_S.PENDING, lambda e: replace(e, attempts=-1), None),
    "attempts over integer": (_S.PENDING, lambda e: replace(e, attempts=MAX_INT32 + 1), None),
    "negative conflicts": (_S.PENDING, lambda e: replace(e, mismatched_repeat_count=-1), None),
    "conflicts over integer": (
        _S.PENDING,
        lambda e: replace(e, mismatched_repeat_count=MAX_INT32 + 1, last_mismatched_repeat_at=_T1),
        None,
    ),
    "conflicts without time": (_S.PENDING, lambda e: replace(e, mismatched_repeat_count=1), None),
    "conflict time without one": (
        _S.PENDING,
        lambda e: replace(e, last_mismatched_repeat_at=_T1),
        None,
    ),
    "pending with outcome": (
        _S.PENDING,
        lambda e: replace(e, outcome_code="repository_mismatch"),
        None,
    ),
    "pending with release": (_S.PENDING, lambda e: replace(e, release_id=_ID_RELEASE), None),
    "pending finished": (_S.PENDING, lambda e: replace(e, finished_at=_T1), None),
    "accepted with outcome": (
        _S.ACCEPTED,
        lambda e: replace(e, outcome_code="repository_mismatch"),
        None,
    ),
    "accepted without release": (_S.ACCEPTED, lambda e: replace(e, release_id=None), None),
    "accepted with nil release": (_S.ACCEPTED, lambda e: replace(e, release_id=NIL_ID), InvalidId),
    "accepted not finished": (_S.ACCEPTED, lambda e: replace(e, finished_at=None), None),
    "rejected without outcome": (
        _S.REJECTED,
        lambda e: replace(e, outcome_code=None),
        MissingFailureCode,
    ),
    "rejected malformed code": (
        _S.REJECTED,
        lambda e: replace(e, outcome_code="Mismatch"),
        InvalidFailureCode,
    ),
    "rejected with release": (_S.REJECTED, lambda e: replace(e, release_id=_ID_RELEASE), None),
    "rejected not finished": (_S.REJECTED, lambda e: replace(e, finished_at=None), None),
    "failed without outcome": (
        _S.FAILED,
        lambda e: replace(e, outcome_code=None),
        MissingFailureCode,
    ),
    "failed empty code": (_S.FAILED, lambda e: replace(e, outcome_code=""), MissingFailureCode),
    "failed with release": (_S.FAILED, lambda e: replace(e, release_id=_ID_RELEASE), None),
    "failed not finished": (_S.FAILED, lambda e: replace(e, finished_at=None), None),
}


@pytest.mark.parametrize(
    ("status", "mutate", "cause"), list(_INVALID_ROWS.values()), ids=list(_INVALID_ROWS)
)
def test_inbox_entry_validate(
    status: SourceEventStatus, mutate: _Mutate, cause: type[DomainError] | None
) -> None:
    e = mutate(_inbox_row(status))
    with pytest.raises(CorruptRow) as exc_info:
        e.validate()
    if cause is not None:
        assert is_error(exc_info.value, cause)


# --- InboxEntry.take and InboxTake.validate ---


def test_inbox_entry_take() -> None:
    e = replace(_inbox_row(_S.PENDING), attempts=7)
    want = InboxTake(project_id=_ID_PROJECT, generation_request_id=_ID_REQUEST, attempts=7)
    assert e.take() == want
    want.validate()


_TAKE_REFUSALS: dict[str, tuple[Callable[[InboxTake], InboxTake], type[DomainError]]] = {
    "zero project": (lambda k: replace(k, project_id=NIL_ID), InvalidId),
    "zero generation request": (lambda k: replace(k, generation_request_id=NIL_ID), InvalidId),
    "never taken": (lambda k: replace(k, attempts=0), InvalidAttempt),
    "over integer": (lambda k: replace(k, attempts=MAX_INT32 + 1), InvalidAttempt),
}


@pytest.mark.parametrize(
    ("mutate", "want"), list(_TAKE_REFUSALS.values()), ids=list(_TAKE_REFUSALS)
)
def test_inbox_take_validate_refusals(
    mutate: Callable[[InboxTake], InboxTake], want: type[DomainError]
) -> None:
    k = mutate(InboxTake(project_id=_ID_PROJECT, generation_request_id=_ID_REQUEST, attempts=7))
    with pytest.raises(want):
        k.validate()


# --- outcomes and params ---


def test_intake_outcomes() -> None:
    # Revision 19: a repeat that does not match is MISMATCHED_REPEAT, not "conflict".
    assert {o: str(o) for o in IntakeOutcome} == {
        IntakeOutcome.STORED: "stored",
        IntakeOutcome.DUPLICATE: "duplicate",
        IntakeOutcome.MISMATCHED_REPEAT: "mismatched_repeat",
        IntakeOutcome.OVER_CAPACITY: "over_capacity",
    }
    assert len(IntakeOutcome) == 4


def test_intake_params_validate() -> None:
    IntakeParams(event=_source_event(), max_pending_per_project=1).validate()
    for cap in (0, -1):
        with pytest.raises(ValueError, match=r"."):
            IntakeParams(event=_source_event(), max_pending_per_project=cap).validate()
    bad_event = replace(_source_event(), git_commit_sha="")
    with pytest.raises(InvalidCommitSha):
        IntakeParams(event=bad_event, max_pending_per_project=1).validate()


_BAD_TAKE_PARAMS: dict[str, TakeInboxParams] = {
    "zero batch": TakeInboxParams(batch_size=0, max_per_project=1, visibility=timedelta(minutes=1)),
    "zero per project": TakeInboxParams(
        batch_size=10, max_per_project=0, visibility=timedelta(minutes=1)
    ),
    "per project over batch": TakeInboxParams(
        batch_size=2, max_per_project=3, visibility=timedelta(minutes=1)
    ),
    "zero visibility": TakeInboxParams(batch_size=10, max_per_project=1, visibility=timedelta(0)),
    "negative visibility": TakeInboxParams(
        batch_size=10, max_per_project=1, visibility=timedelta(seconds=-1)
    ),
}


def test_take_inbox_params_validate_accepts_valid_params() -> None:
    TakeInboxParams(batch_size=10, max_per_project=2, visibility=timedelta(minutes=1)).validate()
    # One microsecond, the precision of timestamptz, is the smallest visibility.
    TakeInboxParams(
        batch_size=10, max_per_project=1, visibility=timedelta(microseconds=1)
    ).validate()


@pytest.mark.parametrize("params", list(_BAD_TAKE_PARAMS.values()), ids=list(_BAD_TAKE_PARAMS))
def test_take_inbox_params_validate_refusals(params: TakeInboxParams) -> None:
    with pytest.raises(ValueError, match=r"."):
        params.validate()


# --- acceptance decisions ---


def _acceptance_facts() -> ReleaseAcceptanceFacts:
    return ReleaseAcceptanceFacts(
        entry=_inbox_row(_S.PENDING),
        existing=None,
        release_id=_ID_RELEASE,
        step_id=_ID_STEP,
        now=_T2,
    )


def test_accept_new_release() -> None:
    d = accept_new_release(_acceptance_facts(), subdomain="shop")
    assert d.accepted
    assert d.created
    assert d.outcome_code == ""
    assert d.hosting == ProjectHosting(
        project_id=_ID_PROJECT,
        last_command_number=0,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=_T2,
        subdomain="shop",
    )
    assert d.release == Release(
        id=_ID_RELEASE,
        project_id=_ID_PROJECT,
        generation_request_id=_ID_REQUEST,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
        status=ReleaseStatus.ACCEPTED,
        created_at=_T2,
        updated_at=_T2,
    )
    assert d.step == ReleaseStep(
        id=_ID_STEP,
        release_id=_ID_RELEASE,
        name=ReleaseStepName.BUILD,
        attempt=1,
        status=ReleaseStepStatus.PENDING,
    )
    assert d.hosting is not None
    assert d.release is not None
    assert d.step is not None
    d.hosting.validate()
    d.release.validate()
    d.step.validate()


def _existing_release() -> Release:
    return Release(
        id=_ID_RELEASE2,
        project_id=_ID_PROJECT,
        generation_request_id=_ID_REQUEST,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
        status=ReleaseStatus.ACCEPTED,
        created_at=_T0,
        updated_at=_T0,
    )


_NEW_RELEASE_REFUSALS: dict[
    str, tuple[Callable[[ReleaseAcceptanceFacts], ReleaseAcceptanceFacts], type[DomainError]]
] = {
    "release exists": (lambda f: replace(f, existing=_existing_release()), IllegalTransition),
    "entry not pending": (lambda f: replace(f, entry=_inbox_row(_S.REJECTED)), IllegalTransition),
    "zero release ID": (lambda f: replace(f, release_id=NIL_ID), InvalidId),
    "zero step ID": (lambda f: replace(f, step_id=NIL_ID), InvalidId),
    "same release and step": (lambda f: replace(f, step_id=f.release_id), InvalidId),
    "invalid event": (
        lambda f: replace(f, entry=_with_event(f.entry, git_commit_sha="main")),
        InvalidCommitSha,
    ),
}


@pytest.mark.parametrize(
    ("mutate", "want"), list(_NEW_RELEASE_REFUSALS.values()), ids=list(_NEW_RELEASE_REFUSALS)
)
def test_accept_new_release_refusals(
    mutate: Callable[[ReleaseAcceptanceFacts], ReleaseAcceptanceFacts], want: type[DomainError]
) -> None:
    with pytest.raises(want):
        accept_new_release(mutate(_acceptance_facts()), subdomain="shop")


def test_accept_new_release_refuses_an_invalid_subdomain() -> None:
    with pytest.raises(InvalidSubdomain):
        accept_new_release(_acceptance_facts(), subdomain="Shop")


def _built_existing() -> Release:
    return replace(_existing_release(), status=ReleaseStatus.BUILT, container_image_digest=_DIGEST)


def test_accept_existing_release() -> None:
    existing = _built_existing()
    d = accept_existing_release(replace(_acceptance_facts(), existing=existing))
    assert d.accepted
    assert not d.created
    assert d.outcome_code == ""
    assert d.release == existing
    # Accepting an existing release carries no rows to insert.
    assert d.step is None
    assert d.hosting is None


_EXISTING_REFUSALS: dict[str, tuple[Release | None, InboxEntry, type[DomainError]]] = {
    "no release": (None, _inbox_row(_S.PENDING), IllegalTransition),
    "another source": (
        replace(_built_existing(), git_commit_sha="f" + _SHA40[1:]),
        _inbox_row(_S.PENDING),
        IdempotencyConflict,
    ),
    "entry not pending": (_built_existing(), _inbox_row(_S.ACCEPTED), IllegalTransition),
}


@pytest.mark.parametrize(
    ("existing", "entry", "want"), list(_EXISTING_REFUSALS.values()), ids=list(_EXISTING_REFUSALS)
)
def test_accept_existing_release_refusals(
    existing: Release | None, entry: InboxEntry, want: type[DomainError]
) -> None:
    facts = replace(_acceptance_facts(), existing=existing, entry=entry)
    with pytest.raises(want):
        accept_existing_release(facts)


@pytest.mark.parametrize(
    "code",
    [CODE_RELEASE_CONFLICT, CODE_REPOSITORY_MISMATCH, CODE_REPOSITORY_NOT_REGISTERED],
    ids=[CODE_RELEASE_CONFLICT, CODE_REPOSITORY_MISMATCH, CODE_REPOSITORY_NOT_REGISTERED],
)
def test_reject_release(code: str) -> None:
    d = reject_release(code)
    assert not d.accepted
    assert not d.created
    assert d.outcome_code == code
    assert d.release is None
    assert d.step is None
    assert d.hosting is None


@pytest.mark.parametrize(
    ("code", "want"),
    [("", MissingFailureCode), ("Conflict", InvalidFailureCode)],
    ids=["empty", "malformed"],
)
def test_reject_release_refusals(code: str, want: type[DomainError]) -> None:
    with pytest.raises(want):
        reject_release(code)
