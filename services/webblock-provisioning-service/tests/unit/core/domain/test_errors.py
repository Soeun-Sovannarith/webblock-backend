"""The domain errors and the cause-chain helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.domain import (
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
    ReleaseTransition,
    ResultMismatch,
    RouteNotStable,
    StaleFence,
    UnknownEnumValue,
    error_chain,
    is_error,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)

_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_REPO_ID = 4242

# Decision 0001 section 10: 15 kept and 11 added.
_EARLIER: list[type[DomainError]] = [
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
]
# No release intent error: it belonged to the Crossplane XR, which is gone (decision 0013).
_LATER: list[type[DomainError]] = [InvalidLogRef]


def _base_release(status: ReleaseStatus) -> Release:
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


def _matches(a: type[DomainError], b: type[DomainError]) -> bool:
    """An instance of ``a`` is taken for a ``b``, directly or through its cause chain."""
    return isinstance(a(), b) or is_error(a(), b)


def test_sentinels_distinct() -> None:
    assert len(_EARLIER) == 26
    for i, a in enumerate(_EARLIER):
        assert issubclass(a, DomainError)
        assert a.__doc__, f"sentinel {i} has empty text"
        for j, b in enumerate(_EARLIER):
            if i != j:
                assert not _matches(a, b), f"{a.__name__} matches {b.__name__}"


def test_transition_error_context() -> None:
    r = _base_release(ReleaseStatus.LIVE)
    with pytest.raises(IllegalTransition) as exc_info:
        r.transition(ReleaseStatus.ACCEPTED, ReleaseTransition(), _T1)
    msg = str(exc_info.value)
    for want in ("live", "accepted"):
        assert want in msg, f"message {msg!r} lacks {want!r}"


def test_later_sentinels_distinct() -> None:
    every = _EARLIER + _LATER
    for i, a in enumerate(_LATER):
        assert issubclass(a, DomainError)
        assert a.__doc__, f"later sentinel {i} has empty text"
        for j, b in enumerate(every):
            if j != len(_EARLIER) + i:
                assert not _matches(a, b), f"{a.__name__} matches {b.__name__}"
                assert not _matches(b, a), f"{b.__name__} matches {a.__name__}"


# --- error_chain and is_error (Python only) ---


def _raise_from(exc: BaseException, cause: BaseException | None) -> BaseException:
    """Raise ``exc`` from ``cause`` and return the caught exception, with its traceback set."""
    try:
        raise exc from cause
    except BaseException as caught:
        return caught


def test_error_chain_none_is_empty() -> None:
    assert list(error_chain(None)) == []


def test_error_chain_single() -> None:
    e = InvalidId("project")
    assert list(error_chain(e)) == [e]


def test_error_chain_follows_explicit_causes_in_order() -> None:
    root = InvalidId("project")
    middle = _raise_from(IllegalTransition("middle"), root)
    top = _raise_from(StaleFence("top"), middle)
    assert list(error_chain(top)) == [top, middle, root]


def test_error_chain_ignores_implicit_context() -> None:
    inner = InvalidId("project")
    try:
        try:
            raise inner
        except InvalidId:
            raise TypeError("while handling") from None
    except TypeError as caught:
        suppressed = caught
    assert list(error_chain(suppressed)) == [suppressed]

    try:
        try:
            raise InvalidId("project")
        except InvalidId:
            raise TypeError("while handling")  # noqa: B904 - the implicit context is the case under test
    except TypeError as caught:
        implicit = caught
    assert isinstance(implicit.__context__, InvalidId)
    assert implicit.__cause__ is None
    assert list(error_chain(implicit)) == [implicit]


def test_is_error_matches_itself_and_its_base() -> None:
    e = InvalidId("project")
    assert is_error(e, InvalidId)
    assert is_error(e, DomainError)
    assert is_error(e, Exception)
    assert not is_error(e, StaleFence)


def test_is_error_none() -> None:
    assert not is_error(None, InvalidId)
    assert not is_error(None, BaseException)


def test_is_error_walks_explicit_causes() -> None:
    root = InvalidId("project")
    top = _raise_from(IllegalTransition("wrapped"), root)
    assert is_error(top, IllegalTransition)
    assert is_error(top, InvalidId)
    assert not is_error(top, StaleFence)
    # The cause chain is followed one way only: the root is not the wrapper.
    assert not is_error(root, IllegalTransition)
    deeper = _raise_from(ValueError("outer"), top)
    assert is_error(deeper, InvalidId)
    assert is_error(deeper, IllegalTransition)


def test_is_error_ignores_implicit_context() -> None:
    try:
        try:
            raise InvalidId("project")
        except InvalidId:
            raise TypeError("while handling")  # noqa: B904 - the implicit context is the case under test
    except TypeError as caught:
        implicit = caught
    assert isinstance(implicit.__context__, InvalidId)
    assert not is_error(implicit, InvalidId)
    assert not is_error(implicit, DomainError)
    assert is_error(implicit, TypeError)
