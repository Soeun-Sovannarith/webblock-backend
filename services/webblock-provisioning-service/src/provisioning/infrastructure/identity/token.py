"""The claims Provisioning checks on top of web-block-commons (decision 0024 G5).

The library checks the token's shape, header, signature, ``iss``, ``aud``, ``typ``, ``exp`` and
``nbf`` (G4). It reads no ``sub``, no ``iat`` and no lifetime, so these are checked here on the
claims it verified, with the library's clock skew: ``sub`` present and a valid requester (it is
stored as ``requested_by_subject``), ``iat`` not after now, ``exp`` after ``iat`` and ``nbf``, and
the lifetime cap measured from ``iat`` and from now.

A refusal is ``Refusal`` with a fixed cause for the log; it never carries a token or a claim
value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from webblock_commons.core.token import CLOCK_SKEW_SECONDS, numeric_date
from webblock_commons.core.token import Refusal as LibraryRefusal

from provisioning.core.domain import DomainError, validate_requester


class Refusal(Exception):
    """An authentication failure with a fixed ``cause`` for the log (never a value)."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


def subject_of(claims: Mapping[str, Any], *, now: float, max_lifetime: float) -> str:
    """The token's subject once the times and the subject pass; ``Refusal`` otherwise."""
    try:
        exp = numeric_date(claims, "exp")
        nbf = numeric_date(claims, "nbf")
        iat = numeric_date(claims, "iat")
    except LibraryRefusal:
        raise Refusal("claims_malformed") from None
    if exp is None:  # the library requires it; never trust that here
        raise Refusal("exp_missing")
    skew = CLOCK_SKEW_SECONDS
    if iat is not None and iat > now + skew:
        raise Refusal("iat_in_future")
    if (iat is not None and exp <= iat) or (nbf is not None and exp <= nbf):
        raise Refusal("claims_malformed")
    if iat is not None and exp - iat > max_lifetime:
        raise Refusal("lifetime_exceeded")
    if exp - now > max_lifetime + skew:
        # No iat to measure from, or a generous one: the token may not outlive the cap from now,
        # with the skew the issuer's clock may be ahead by.
        raise Refusal("lifetime_exceeded")
    subject = claims.get("sub")
    if subject is None:
        raise Refusal("sub_missing")
    if not isinstance(subject, str):
        raise Refusal("sub_invalid")
    try:
        validate_requester(subject)
    except DomainError:
        raise Refusal("sub_invalid") from None
    return subject
