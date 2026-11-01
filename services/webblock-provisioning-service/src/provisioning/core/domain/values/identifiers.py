"""Identifier and value formats.

Identifiers are ``uuid.UUID``; ``parse_id`` accepts only canonical lowercase text and refuses the
nil UUID. Commit SHAs, image digests and request fingerprints are plain ``str`` checked by their
parsers, which return the text unchanged. Errors never contain the input.
"""

from __future__ import annotations

from uuid import UUID

from provisioning.core.domain.errors import (
    CommandNumberOverflow,
    InvalidCommandNumber,
    InvalidCommitSha,
    InvalidId,
    InvalidImageDigest,
    InvalidRepositoryId,
    InvalidRequestFingerprint,
    StaleFence,
)

# The largest command number in the domain and in every contract: 2^53-1, the largest integer a
# JSON number carries exactly. A project's last_command_number ranges from 0 to it; a command's
# command_number from 1 to it (decision 0001 section 6).
MAX_COMMAND_NUMBER = (1 << 53) - 1

# The largest GitHub repository ID: 2^53-1, so the value survives JSON number handling.
MAX_REPOSITORY_ID = (1 << 53) - 1

NIL_ID = UUID(int=0)


def parse_id(text: str) -> UUID:
    """Accept only canonical lowercase 8-4-4-4-12 text; the nil UUID is not an identity
    (``InvalidId``)."""
    if len(text) != _ID_TEXT_LEN:
        raise InvalidId("wrong length")
    for i, c in enumerate(text):
        if i in _SEPARATORS:
            if c != "-":
                raise InvalidId("misplaced separator")
        elif c not in _LOWER_HEX:
            raise InvalidId("not lowercase hexadecimal")
    value = UUID(text)
    if value.int == 0:
        raise InvalidId("the nil UUID is not an identity")
    return value


def is_nil(value: UUID) -> bool:
    """Whether the UUID is the nil UUID."""
    return value.int == 0


def parse_image_digest(text: str) -> str:
    """Enforce ``sha256:`` plus 64 lowercase hex characters (``InvalidImageDigest``)."""
    if (
        len(text) != len(_DIGEST_PREFIX) + _DIGEST_HEX_LEN
        or not text.startswith(_DIGEST_PREFIX)
        or not _is_lower_hex(text[len(_DIGEST_PREFIX) :])
    ):
        raise InvalidImageDigest(
            f"expected sha256 digest of {_DIGEST_HEX_LEN} lowercase hex characters"
        )
    return text


def parse_commit_sha(text: str) -> str:
    """Enforce 40 or 64 lowercase hexadecimal characters (``InvalidCommitSha``)."""
    if len(text) not in (40, 64) or not _is_lower_hex(text):
        raise InvalidCommitSha("expected 40 or 64 lowercase hexadecimal characters")
    return text


def parse_repository_id(value: int) -> int:
    """Accept 1 to MAX_REPOSITORY_ID (``InvalidRepositoryId``)."""
    if isinstance(value, bool) or not 1 <= value <= MAX_REPOSITORY_ID:
        raise InvalidRepositoryId(f"outside 1..{MAX_REPOSITORY_ID}")
    return value


def parse_request_fingerprint(text: str) -> str:
    """Enforce 64 lowercase hexadecimal characters (``InvalidRequestFingerprint``)."""
    if len(text) != _FINGERPRINT_LEN or not _is_lower_hex(text):
        raise InvalidRequestFingerprint(
            f"expected {_FINGERPRINT_LEN} lowercase hexadecimal characters"
        )
    return text


def validate_last_command_number(value: int) -> None:
    """Accept a project's last_command_number: 0 to MAX_COMMAND_NUMBER
    (``InvalidCommandNumber``)."""
    if not 0 <= value <= MAX_COMMAND_NUMBER:
        raise InvalidCommandNumber(f"last command number {value} outside 0..{MAX_COMMAND_NUMBER}")


def validate_command_number(value: int) -> None:
    """Accept a command's command_number: 1 to MAX_COMMAND_NUMBER (``InvalidCommandNumber``)."""
    if not 1 <= value <= MAX_COMMAND_NUMBER:
        raise InvalidCommandNumber(f"command number {value} outside 1..{MAX_COMMAND_NUMBER}")


def check_fence(command_number: int, last_command_number: int) -> None:
    """Accept a worker write only when its command's number equals the project's
    last_command_number (decision 0001 section 6). Out of range is ``InvalidCommandNumber``; any
    difference is ``StaleFence``."""
    validate_command_number(command_number)
    validate_last_command_number(last_command_number)
    if command_number != last_command_number:
        raise StaleFence(
            f"command number {command_number}, last command number {last_command_number}"
        )


def next_command_number(current: int) -> int:
    """Return current + 1; ``InvalidCommandNumber`` out of range, ``CommandNumberOverflow`` at the
    maximum."""
    if not 0 <= current <= MAX_COMMAND_NUMBER:
        raise InvalidCommandNumber(f"command number {current}")
    if current == MAX_COMMAND_NUMBER:
        raise CommandNumberOverflow("no command number after the maximum")
    return current + 1


_ID_TEXT_LEN = 36
_SEPARATORS = frozenset({8, 13, 18, 23})
_LOWER_HEX = frozenset("0123456789abcdef")
_DIGEST_PREFIX = "sha256:"
_DIGEST_HEX_LEN = 64
_FINGERPRINT_LEN = 64


def _is_lower_hex(text: str) -> bool:
    return all(c in _LOWER_HEX for c in text)
