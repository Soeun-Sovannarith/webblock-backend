"""PostgreSQL's SCRAM-SHA-256 password verifier, computed here so that the cleartext password never
appears in a SQL statement or a log (decision 0011 section 5; RFC 5802, RFC 7677).

``SCRAM-SHA-256$<iterations>:<base64 salt>$<base64 StoredKey>:<base64 ServerKey>``. PostgreSQL
stores a password given in this form as it is. It normalises a cleartext password with SASLprep
before hashing; printable ASCII is unchanged by SASLprep, so only printable ASCII (0x20 to 0x7e) is
accepted. The defaults are PostgreSQL's: 4096 iterations and a 16-byte salt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from provisioning.infrastructure.catalog.errors import InvalidRuntimeCredential

SCRAM_ITERATIONS = 4096
SCRAM_SALT_BYTES = 16


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def check_password(password: str) -> None:
    """Refuse a password the verifier would refuse; the error never contains it."""
    if not password:
        raise InvalidRuntimeCredential("the password is empty")
    if not all(0x20 <= ord(c) <= 0x7E for c in password):
        raise InvalidRuntimeCredential("the password must be printable ASCII")


def scram_verifier(password: str, salt: bytes, iterations: int) -> str:
    """The verifier of ``password``. Refuses an empty or non-printable-ASCII password, a salt under
    16 bytes and fewer than 4096 iterations; the error never contains the password."""
    check_password(password)
    if len(salt) < SCRAM_SALT_BYTES:
        raise InvalidRuntimeCredential(f"the salt must be at least {SCRAM_SALT_BYTES} bytes")
    if iterations < SCRAM_ITERATIONS:
        raise InvalidRuntimeCredential(f"at least {SCRAM_ITERATIONS} iterations are required")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


def new_scram_verifier(password: str) -> str:
    """The verifier of ``password`` with a fresh random 16-byte salt and 4096 iterations."""
    return scram_verifier(password, secrets.token_bytes(SCRAM_SALT_BYTES), SCRAM_ITERATIONS)
