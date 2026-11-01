"""Project's "source published" event (decision 0008 sections 2 and 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from provisioning.core.domain import (
    MAX_REPOSITORY_ID,
    NIL_ID,
    IdempotencyConflict,
    InvalidCommitSha,
    InvalidId,
    InvalidRepositoryId,
    Release,
    ReleaseStatus,
    SourcePublished,
    SourcePublishedFields,
    parse_source_published,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

_ID_PROJECT_TEXT = "0190a1b2-0000-7000-8000-000000000001"
_ID_REQUEST_TEXT = "0190a1b2-0000-7000-8000-0000000000d1"
_ID_PROJECT = UUID(_ID_PROJECT_TEXT)
_ID_PROJECT2 = UUID("0190a1b2-0000-7000-8000-000000000002")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_RELEASE2 = UUID("0190a1b2-0000-7000-8000-0000000000a2")
_ID_REQUEST = UUID(_ID_REQUEST_TEXT)
_SHA40 = "0123456789abcdef0123456789abcdef01234567"
_SHA64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
_REPO_ID = 4242
_DIGEST_A = "sha256:" + "a" * 64
_NIL_TEXT = "00000000-0000-0000-0000-000000000000"
_MARKER = "SECRET-INPUT"


def _source_fields() -> SourcePublishedFields:
    """A well-formed "source published" event matching ``_base_release``."""
    return SourcePublishedFields(
        project_id=_ID_PROJECT_TEXT,
        generation_request_id=_ID_REQUEST_TEXT,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
    )


def _source() -> SourcePublished:
    return SourcePublished(
        project_id=_ID_PROJECT,
        generation_request_id=_ID_REQUEST,
        repository_id=_REPO_ID,
        git_commit_sha=_SHA40,
    )


def _base_release(status: ReleaseStatus) -> Release:
    """A release in the status with only its identity set."""
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


def _valid_row(status: ReleaseStatus) -> Release:
    """``_base_release`` with the fields the database constraints require for the status."""
    r = _base_release(status)
    if status in (
        ReleaseStatus.BUILT,
        ReleaseStatus.CANDIDATE_READY,
        ReleaseStatus.PROMOTING,
        ReleaseStatus.LIVE,
        ReleaseStatus.SUPERSEDED,
        ReleaseStatus.ROLLED_BACK,
    ):
        r = replace(r, container_image_digest=_DIGEST_A)
    if status in (ReleaseStatus.LIVE, ReleaseStatus.SUPERSEDED):
        r = replace(r, promoted_at=_T0)
    if status in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK):
        r = replace(r, failure_code="promotion_failed")
    return r


def test_parse_source_published_valid() -> None:
    e = parse_source_published(_source_fields())
    assert e == _source()
    e.validate()
    widest = replace(_source_fields(), git_commit_sha=_SHA64, repository_id=MAX_REPOSITORY_ID)
    got = parse_source_published(widest)
    assert got.git_commit_sha == _SHA64
    assert got.repository_id == MAX_REPOSITORY_ID


_PARSE_CASES: dict[str, tuple[SourcePublishedFields, type[Exception]]] = {
    "nil project": (replace(_source_fields(), project_id=_NIL_TEXT), InvalidId),
    "empty project": (replace(_source_fields(), project_id=""), InvalidId),
    "uppercase project": (
        replace(_source_fields(), project_id=_ID_PROJECT_TEXT.upper()),
        InvalidId,
    ),
    "project with input": (replace(_source_fields(), project_id=_MARKER), InvalidId),
    "nil generation request": (
        replace(_source_fields(), generation_request_id=_NIL_TEXT),
        InvalidId,
    ),
    "bad generation request": (
        replace(_source_fields(), generation_request_id=_MARKER),
        InvalidId,
    ),
    "zero repository": (replace(_source_fields(), repository_id=0), InvalidRepositoryId),
    "negative repository": (
        replace(_source_fields(), repository_id=-1),
        InvalidRepositoryId,
    ),
    "repository over 2^53-1": (
        replace(_source_fields(), repository_id=MAX_REPOSITORY_ID + 1),
        InvalidRepositoryId,
    ),
    "short SHA": (replace(_source_fields(), git_commit_sha=_SHA40[:7]), InvalidCommitSha),
    "uppercase SHA": (replace(_source_fields(), git_commit_sha=_SHA40.upper()), InvalidCommitSha),
    "branch name": (replace(_source_fields(), git_commit_sha=_MARKER), InvalidCommitSha),
}


@pytest.mark.parametrize(("fields", "want"), list(_PARSE_CASES.values()), ids=list(_PARSE_CASES))
def test_parse_source_published(fields: SourcePublishedFields, want: type[Exception]) -> None:
    with pytest.raises(want) as exc_info:
        parse_source_published(fields)
    assert _MARKER not in str(exc_info.value), "error repeats the input"


_VALIDATE_CASES: dict[str, tuple[SourcePublished, type[Exception]]] = {
    "zero project": (replace(_source(), project_id=NIL_ID), InvalidId),
    "zero generation request": (replace(_source(), generation_request_id=NIL_ID), InvalidId),
    "zero repository": (replace(_source(), repository_id=0), InvalidRepositoryId),
    "repository too large": (
        replace(_source(), repository_id=MAX_REPOSITORY_ID + 1),
        InvalidRepositoryId,
    ),
    "empty SHA": (replace(_source(), git_commit_sha=""), InvalidCommitSha),
}


@pytest.mark.parametrize(
    ("event", "want"), list(_VALIDATE_CASES.values()), ids=list(_VALIDATE_CASES)
)
def test_source_published_validate(event: SourcePublished, want: type[Exception]) -> None:
    with pytest.raises(want):
        event.validate()


# Decision 0008 section 2: the same key with the same repository and SHA is a duplicate; any
# difference is a conflict, never silent.
def test_source_published_check_same_source_identical() -> None:
    _source().check_same_source(_source())


_OTHER_SOURCES: dict[str, SourcePublished] = {
    "other repository": replace(_source(), repository_id=_REPO_ID + 1),
    "other SHA": replace(_source(), git_commit_sha="f" * 40),
    "SHA-256 of another": replace(_source(), git_commit_sha=_SHA64),
    "other project": replace(_source(), project_id=_ID_PROJECT2),
    "other generation request": replace(_source(), generation_request_id=_ID_RELEASE2),
}


@pytest.mark.parametrize("other", list(_OTHER_SOURCES.values()), ids=list(_OTHER_SOURCES))
def test_source_published_check_same_source(other: SourcePublished) -> None:
    with pytest.raises(IdempotencyConflict):
        _source().check_same_source(other)
    with pytest.raises(IdempotencyConflict):
        other.check_same_source(_source())


# Decision 0008 section 3: a stored release with a different repository or SHA under the event's
# key is release_conflict; the event's own release matches.
@pytest.mark.parametrize("status", list(ReleaseStatus), ids=[str(s) for s in ReleaseStatus])
def test_source_published_check_release_matches(status: ReleaseStatus) -> None:
    _source().check_release(_valid_row(status))


_OTHER_RELEASES: dict[str, Release] = {
    "other repository": replace(_base_release(ReleaseStatus.ACCEPTED), repository_id=_REPO_ID + 1),
    "other SHA": replace(_base_release(ReleaseStatus.ACCEPTED), git_commit_sha=_SHA64),
    "other project": replace(_base_release(ReleaseStatus.ACCEPTED), project_id=_ID_PROJECT2),
    "other generation request": replace(
        _base_release(ReleaseStatus.ACCEPTED), generation_request_id=_ID_RELEASE2
    ),
}


@pytest.mark.parametrize("release", list(_OTHER_RELEASES.values()), ids=list(_OTHER_RELEASES))
def test_source_published_check_release(release: Release) -> None:
    with pytest.raises(IdempotencyConflict):
        _source().check_release(release)


def test_source_published_new_release() -> None:
    e = _source()
    r = e.new_release(_ID_RELEASE, _T0)
    assert r == _base_release(ReleaseStatus.ACCEPTED)
    r.validate()
    e.check_release(r)
    with pytest.raises(InvalidId):
        e.new_release(NIL_ID, _T0)
    bad = replace(e, git_commit_sha="main")
    with pytest.raises(InvalidCommitSha):
        bad.new_release(_ID_RELEASE, _T0)
