"""The SCRAM-SHA-256 verifier that access_setup sends instead of a password (decision 0014 section
14, decision 0011 section 5)."""

import re

import pytest

from provisioning.infrastructure.catalog import (
    InvalidRuntimeCredential,
    new_scram_verifier,
    scram_verifier,
)

SHAPE = re.compile(
    r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/]{22}==\$[A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}="
)


def test_the_verifier_matches_the_reference_vector() -> None:
    # A reference vector computed outside this code (PBKDF2-HMAC-SHA-256, 4096 iterations, salt
    # 00..0f, then HMAC "Client Key" and "Server Key" and SHA-256 of the client key; RFC 5802,
    # RFC 7677).
    salt = bytes(range(16))

    verifier = scram_verifier("correct horse battery staple 0123456789", salt, 4096)

    assert verifier == (
        "SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$"
        "ntDGNcRiwSmqeeFcF1CAKX3B0rlRk+mV1it0u0TA9ec=:"
        "K/9Igjy6gcDejMFx+CEU2rEL4tHaoQlHxQDbKPNkitM="
    )


@pytest.mark.parametrize(
    ("password", "salt", "iterations"),
    [
        ("pässword-CANARY-pässword-pässword", bytes(16), 4096),
        ("password" + chr(0xA0) + "CANARY password-password", bytes(16), 4096),
        ("password\x7fCANARY-password-password", bytes(16), 4096),
        ("password\nCANARY-password-password", bytes(16), 4096),
        ("", bytes(16), 4096),
        ("password-CANARY-password-password", bytes(15), 4096),
        ("password-CANARY-password-password", bytes(16), 4095),
    ],
    ids=["non-ascii-letter", "non-ascii-space", "delete", "newline", "empty", "salt", "iterations"],
)
def test_what_postgresql_would_normalise_or_a_weak_setting_is_refused_without_the_password(
    password: str, salt: bytes, iterations: int
) -> None:
    with pytest.raises(InvalidRuntimeCredential) as caught:
        scram_verifier(password, salt, iterations)

    assert "CANARY" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_new_verifier_has_the_postgresql_shape_and_a_fresh_salt() -> None:
    first = new_scram_verifier("password-password-password-1")
    second = new_scram_verifier("password-password-password-1")

    assert SHAPE.fullmatch(first)
    assert SHAPE.fullmatch(second)
    assert first != second
