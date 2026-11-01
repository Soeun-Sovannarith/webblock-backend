"""The listings' page inputs (decision 0023 G4): ``limit`` and the opaque ``cursor``. A cursor
names a position in one list, never a project: the project always comes from the path, and the read
filters on it, so a cursor of another project only moves the position.

A cursor is the URL-safe base64 of a kind byte and a fixed-width position, without padding (the
page token of decision 0014 section 21 ruling 7, renamed):

- releases: ``r``, then microseconds since the Unix epoch (8 bytes, signed) and the release ID
  (16 bytes);
- commands: ``c``, then the command number (8 bytes).

A bad limit is a field error of ``limit`` (``INVALID_FORMAT`` or ``OUT_OF_RANGE``); anything else
than a cursor this module made, a wrong kind or a value out of range is a field error of
``cursor`` (``INVALID_FORMAT``). Both are ``MalformedRequest`` with no cause and no text from the
input.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from provisioning.core.application import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ReleaseCursor
from provisioning.core.domain import MAX_COMMAND_NUMBER
from provisioning.presentation.operations.errors import FieldCode, FieldError, MalformedRequest

MAX_CURSOR_LEN: Final = 200
LIMIT_FIELD: Final = "limit"
CURSOR_FIELD: Final = "cursor"
_INTEGER: Final = re.compile(r"-?(?:0|[1-9][0-9]*)", re.ASCII)
_CURSOR: Final = re.compile(rf"[A-Za-z0-9_-]{{1,{MAX_CURSOR_LEN}}}", re.ASCII)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_RELEASES: Final = b"r"
_COMMANDS: Final = b"c"


def parse_limit(value: str | None) -> int:
    """DEFAULT_PAGE_SIZE (20) when absent. A decimal integer without a plus sign or a leading zero
    is in range from 1 to MAX_PAGE_SIZE (50), else ``OUT_OF_RANGE``; anything else is
    ``INVALID_FORMAT``."""
    if value is None:
        return DEFAULT_PAGE_SIZE
    if not _INTEGER.fullmatch(value):
        raise _limit(FieldCode.INVALID_FORMAT)
    if value.startswith("-") or len(value) > 2 or not 1 <= int(value) <= MAX_PAGE_SIZE:
        raise _limit(FieldCode.OUT_OF_RANGE)
    return int(value)


def release_cursor(cursor: ReleaseCursor) -> str:
    micros = (cursor.created_at - _EPOCH) // timedelta(microseconds=1)
    raw = _RELEASES + micros.to_bytes(8, "big", signed=True) + cursor.release_id.bytes
    return _encode(raw)


def parse_release_cursor(cursor: str | None) -> ReleaseCursor | None:
    if cursor is None:
        return None
    raw = _decode(cursor, _RELEASES, 1 + 8 + 16)
    micros = int.from_bytes(raw[1:9], "big", signed=True)
    try:
        created_at = _EPOCH + timedelta(microseconds=micros)
    except OverflowError:
        raise _bad_cursor() from None
    release_id = UUID(bytes=raw[9:])
    if release_id.int == 0:
        raise _bad_cursor()
    return ReleaseCursor(created_at=created_at, release_id=release_id)


def command_cursor(after: int) -> str:
    return _encode(_COMMANDS + after.to_bytes(8, "big", signed=True))


def parse_command_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    raw = _decode(cursor, _COMMANDS, 1 + 8)
    number = int.from_bytes(raw[1:], "big", signed=True)
    if not 1 <= number <= MAX_COMMAND_NUMBER:
        raise _bad_cursor()
    return number


def _limit(code: FieldCode) -> MalformedRequest:
    return MalformedRequest("limit", errors=(FieldError(LIMIT_FIELD, code),))


def _bad_cursor() -> MalformedRequest:
    return MalformedRequest("cursor", errors=(FieldError(CURSOR_FIELD, FieldCode.INVALID_FORMAT),))


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(cursor: str, kind: bytes, length: int) -> bytes:
    if not _CURSOR.fullmatch(cursor):
        raise _bad_cursor()
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    except binascii.Error, ValueError:
        raise _bad_cursor() from None
    if len(raw) != length or raw[:1] != kind or _encode(raw) != cursor:
        raise _bad_cursor()
    return raw
