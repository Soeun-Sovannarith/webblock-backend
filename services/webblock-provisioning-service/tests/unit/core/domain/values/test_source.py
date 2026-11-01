"""Commit SHA, repository ID and request fingerprint formats."""

from __future__ import annotations

import pytest

from provisioning.core.domain import (
    MAX_REPOSITORY_ID,
    InvalidCommitSha,
    InvalidRepositoryId,
    InvalidRequestFingerprint,
    parse_commit_sha,
    parse_repository_id,
    parse_request_fingerprint,
)

_INT64_MAX = (1 << 63) - 1
_INT64_MIN = -(1 << 63)

_VALID_SHAS = [
    "a" * 40,
    "0" * 40,
    "0123456789abcdef0123456789abcdef01234567",
    "f" * 64,
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
]

_INVALID_SHAS = {
    "empty": "",
    "abbreviated": "0123456",
    "39 characters": "a" * 39,
    "41 characters": "a" * 41,
    "63 characters": "a" * 63,
    "65 characters": "a" * 65,
    "52 characters": "a" * 52,
    "uppercase": "A" * 40,
    "mixed case": "a" * 39 + "B",
    "not hex": "g" * 40,
    "branch name": "main",
    "ref": "refs/heads/main",
    "leading space": " " + "a" * 39,
    "trailing space": "a" * 39 + " ",
    "newline": "a" * 39 + "\n",
    "sha prefix": "sha1:" + "a" * 35,
}


@pytest.mark.parametrize(
    "sha", _VALID_SHAS, ids=["40 a", "40 zeros", "40 mixed", "64 f", "64 mixed"]
)
def test_parse_commit_sha_valid(sha: str) -> None:
    assert parse_commit_sha(sha) == sha


@pytest.mark.parametrize("sha", list(_INVALID_SHAS.values()), ids=list(_INVALID_SHAS))
def test_parse_commit_sha(sha: str) -> None:
    with pytest.raises(InvalidCommitSha):
        parse_commit_sha(sha)


def test_parse_repository_id() -> None:
    assert MAX_REPOSITORY_ID == 9007199254740991
    for v in (1, 2, 123456789, MAX_REPOSITORY_ID):
        assert parse_repository_id(v) == v
    for v in (0, -1, _INT64_MIN, MAX_REPOSITORY_ID + 1, _INT64_MAX):
        with pytest.raises(InvalidRepositoryId):
            parse_repository_id(v)


_GOOD_FINGERPRINT = "0123456789abcdef" * 4

_INVALID_FINGERPRINTS = {
    "empty": "",
    "63 characters": _GOOD_FINGERPRINT[:63],
    "65 characters": _GOOD_FINGERPRINT + "0",
    "uppercase": _GOOD_FINGERPRINT.upper(),
    "not hex": "z" * 64,
    "prefixed": "sha256:" + _GOOD_FINGERPRINT[:57],
    "trailing nl": _GOOD_FINGERPRINT[:63] + "\n",
}


def test_parse_request_hash_valid() -> None:
    assert parse_request_fingerprint(_GOOD_FINGERPRINT) == _GOOD_FINGERPRINT


@pytest.mark.parametrize(
    "text", list(_INVALID_FINGERPRINTS.values()), ids=list(_INVALID_FINGERPRINTS)
)
def test_parse_request_hash(text: str) -> None:
    with pytest.raises(InvalidRequestFingerprint):
        parse_request_fingerprint(text)


def test_source_errors_have_no_input() -> None:
    marker = "secretbranchname"
    with pytest.raises(InvalidCommitSha) as sha_exc:
        parse_commit_sha(marker)
    assert marker not in str(sha_exc.value)
    with pytest.raises(InvalidRequestFingerprint) as fp_exc:
        parse_request_fingerprint(marker)
    assert marker not in str(fp_exc.value)
