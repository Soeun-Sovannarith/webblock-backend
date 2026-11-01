"""RFC 8785 JSON Canonicalization Scheme (JCS), for the platform's input hash.

Project and AI Codegen agree on `capturedInputHash` = SHA-256 (lowercase hex) of the UTF-8 bytes of the JCS form of
`capturedInput` (Project's service-communication.md 2.2), so the hash does not depend on either service's JSON
library. JCS: object members sorted by their UTF-16 code units, no whitespace, strings as ECMAScript JSON.stringify
writes them, numbers as ECMAScript Number.prototype.toString writes IEEE doubles.
"""
import hashlib
import json
import math
from decimal import Decimal
from typing import Any

_MAX_SAFE_INTEGER = 2**53


def _number(value: int | float) -> str:
    if isinstance(value, int) and abs(value) < _MAX_SAFE_INTEGER:
        return str(value)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("JCS has no form for NaN or Infinity")
    if value == 0:
        return "0"
    # repr() is the shortest string that round-trips, like ECMAScript; reformat it by ECMAScript's rules.
    sign, digits, exponent = Decimal(repr(abs(value))).normalize().as_tuple()
    s = "".join(map(str, digits))
    k = len(s)
    n = exponent + k  # value = 0.s × 10^n
    if k <= n <= 21:
        text = s + "0" * (n - k)
    elif 0 < n <= 21:
        text = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        text = "0." + "0" * -n + s
    else:
        text = s[0] + ("." + s[1:] if k > 1 else "") + "e" + ("+" if n - 1 >= 0 else "-") + str(abs(n - 1))
    return ("-" if value < 0 else "") + text


def canonicalize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(value[k]) for k in keys) + "}"
    raise TypeError(f"JCS cannot encode {type(value).__name__}")


def sha256_hex(value: Any) -> str:
    """The platform's input hash of a parsed JSON value."""
    return hashlib.sha256(canonicalize(value).encode("utf-8")).hexdigest()
