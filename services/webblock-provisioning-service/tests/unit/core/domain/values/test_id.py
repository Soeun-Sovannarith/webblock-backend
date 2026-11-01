"""Identifiers are ``uuid.UUID``."""

from __future__ import annotations

from uuid import UUID

import pytest

from provisioning.core.domain import NIL_ID, InvalidId, is_nil, parse_id

_VALID = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"

_INVALID = {
    "empty": "",
    "uppercase": "0190A1B2-C3D4-7E5F-8A9B-0C1D2E3F4A5B",
    "mixed case": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4A5b",
    "no hyphens": "0190a1b2c3d47e5f8a9b0c1d2e3f4a5b",
    "braces": "{0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b}",
    "urn prefix": "urn:uuid:0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
    "too short": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5",
    "too long": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b0",
    "non hex": "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5g",
    "misplaced hyphen": "0190a1b2c-3d4-7e5f-8a9b-0c1d2e3f4a5b",
    "whitespace": " 0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
    "plus sign": "+190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
}


def test_parse_id_valid() -> None:
    got = parse_id(_VALID)
    assert isinstance(got, UUID)
    assert str(got) == _VALID
    assert not is_nil(got)


@pytest.mark.parametrize("text", list(_INVALID.values()), ids=list(_INVALID))
def test_parse_id(text: str) -> None:
    with pytest.raises(InvalidId):
        parse_id(text)


def test_id_zero_value() -> None:
    assert is_nil(NIL_ID)
    assert is_nil(UUID(int=0))
    assert str(NIL_ID) == "00000000-0000-0000-0000-000000000000"
    with pytest.raises(InvalidId):
        parse_id("00000000-0000-0000-0000-000000000000")


def test_id_comparable() -> None:
    a = parse_id("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")
    b = parse_id("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b")
    c = parse_id("0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c")
    assert a == b
    assert a != c
