"""An image digest is a ``str`` checked by ``parse_image_digest``."""

from __future__ import annotations

import pytest

from provisioning.core.domain import InvalidImageDigest, parse_image_digest

_HEX = "ab12" * 16

_INVALID = {
    "empty": "",
    "tag": "latest",
    "repo with tag": "registry.example/site:v1",
    "uppercase hex": "sha256:" + _HEX.upper(),
    "uppercase algo": "SHA256:" + _HEX,
    "too short": "sha256:" + _HEX[:63],
    "too long": "sha256:" + _HEX + "0",
    "sha512": "sha512:" + _HEX,
    "missing prefix": _HEX,
    "full reference": "registry.example/team/site@sha256:" + _HEX,
    "non hex": "sha256:" + _HEX[:63] + "g",
    "trailing space": "sha256:" + _HEX + " ",
    "leading space": " sha256:" + _HEX,
    "trailing newline": "sha256:" + _HEX + "\n",
}


def test_parse_image_digest_valid() -> None:
    assert parse_image_digest("sha256:" + _HEX) == "sha256:" + _HEX


@pytest.mark.parametrize("text", list(_INVALID.values()), ids=list(_INVALID))
def test_parse_image_digest(text: str) -> None:
    with pytest.raises(InvalidImageDigest):
        parse_image_digest(text)


def test_image_digest_zero_value() -> None:
    # The empty string never parses; the all-zero hex is a digest.
    zeros = "sha256:" + "0" * 64
    assert parse_image_digest(zeros) == zeros
    with pytest.raises(InvalidImageDigest):
        parse_image_digest("")
