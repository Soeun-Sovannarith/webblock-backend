"""The JSON Schema subset the message codecs validate with (decision 0005 section 6, OD-27).

The codecs validate against the contract documents of ``src/contracts/events`` themselves. No JSON
Schema library is a dependency of the package (``jsonschema`` arrives only transitively, without
type information, and checks ``date-time`` only with an optional dependency, review-port-arch.md
L4), so this module interprets the keywords those documents use and nothing else. A schema with any
other keyword is refused when it is compiled, so a contract that grows a keyword fails loudly
instead of being half checked.

Supported: the annotations ``$schema``, ``$id``, ``title`` and ``description``; ``type`` (object,
string, integer); ``const``; ``enum``; ``not``; ``properties``; ``required``;
``additionalProperties: false``; ``minLength`` and ``maxLength`` (code points); ``pattern``
(searched, as JSON Schema does); ``minimum`` and ``maximum``; ``if``, ``then`` and ``else``; and
``format`` asserted for ``uuid``, ``date-time`` (RFC 3339, a real instant) and ``int64``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from datetime import datetime

_ANNOTATIONS = frozenset({"$schema", "$id", "title", "description"})
_KEYWORDS = frozenset(
    {
        "type",
        "const",
        "enum",
        "not",
        "properties",
        "required",
        "additionalProperties",
        "minLength",
        "maxLength",
        "pattern",
        "minimum",
        "maximum",
        "if",
        "then",
        "else",
        "format",
    }
)
_TYPES = frozenset({"object", "string", "integer"})

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DATE_TIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(\.\d+)?([Zz]|[+-]\d{2}:\d{2})"
)
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class UnsupportedSchema(Exception):
    """A schema document uses something outside the supported subset."""


def parse_json(body: bytes) -> object:
    """Parse exactly one JSON value from UTF-8 ``body``. Refused, with a ``ValueError`` whose text
    is one of "not UTF-8", "not one JSON value", "duplicate object key" or "not a finite number"
    and never quotes the body: invalid UTF-8 or a byte-order mark, malformed JSON, trailing data,
    a repeated key in one object, NaN and the infinities."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("not UTF-8") from None
    if text.startswith("﻿"):
        raise ValueError("not UTF-8")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except _DuplicateKey:
        raise ValueError("duplicate object key") from None
    except _NonFinite:
        raise ValueError("not a finite number") from None
    except ValueError:
        raise ValueError("not one JSON value") from None
    except RecursionError:
        raise ValueError("not one JSON value") from None


class _DuplicateKey(Exception):
    pass


class _NonFinite(Exception):
    pass


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey
        out[key] = value
    return out


def _no_constant(_: str) -> object:
    raise _NonFinite


type _Check = Callable[[object, list[str]], None]


class JsonSchema:
    """One compiled schema document."""

    __slots__ = ("_root",)

    def __init__(self, document: Mapping[str, object]) -> None:
        """Compile ``document``; anything outside the subset is ``UnsupportedSchema``."""
        self._root = _compile(document, "")

    def failures(self, instance: object) -> tuple[str, ...]:
        """The schema keyword locations (JSON pointers into the schema, such as
        ``/properties/kind/enum``) that failed for ``instance``, sorted and distinct; empty when
        it is valid. Locations come from the schema, never from the instance, so they are safe to
        log."""
        found: list[str] = []
        self._root(instance, found)
        return tuple(sorted(set(found)))


def _compile(node: object, at: str) -> _Check:
    if not isinstance(node, Mapping):
        raise UnsupportedSchema(f"{at or '/'}: a schema must be an object")
    unknown = set(node) - _ANNOTATIONS - _KEYWORDS
    if unknown:
        raise UnsupportedSchema(f"{at or '/'}: unsupported keywords {sorted(unknown)}")
    if ("then" in node or "else" in node) and "if" not in node:
        raise UnsupportedSchema(f"{at or '/'}: then or else without if")
    checks: list[_Check] = []
    for keyword, value in node.items():
        if keyword in _ANNOTATIONS or keyword in ("then", "else"):
            continue
        checks.append(_keyword(node, keyword, value, f"{at}/{keyword}"))

    def check(instance: object, found: list[str]) -> None:
        for one in checks:
            one(instance, found)

    return check


def _keyword(node: Mapping[str, object], keyword: str, value: object, at: str) -> _Check:
    builders: dict[str, Callable[[object, str], _Check]] = {
        "type": _type,
        "const": _const,
        "enum": _enum,
        "not": _not,
        "properties": _properties,
        "required": _required,
        "additionalProperties": lambda v, a: _additional(v, a, node.get("properties", {})),
        "minLength": lambda v, a: _length(v, a, minimum=True),
        "maxLength": lambda v, a: _length(v, a, minimum=False),
        "pattern": _pattern,
        "minimum": lambda v, a: _bound(v, a, minimum=True),
        "maximum": lambda v, a: _bound(v, a, minimum=False),
        "format": _format,
        "if": lambda v, a: _if(v, a, node, at.rsplit("/", 1)[0]),
    }
    return builders[keyword](value, at)


def _type(value: object, at: str) -> _Check:
    if not isinstance(value, str) or value not in _TYPES:
        raise UnsupportedSchema(f"{at}: unsupported type")
    test = {"object": _is_object, "string": _is_string, "integer": _is_integer}[value]

    def check(instance: object, found: list[str]) -> None:
        if not test(instance):
            found.append(at)

    return check


def _is_object(x: object) -> bool:
    return isinstance(x, dict)


def _is_string(x: object) -> bool:
    return isinstance(x, str)


def _is_integer(x: object) -> bool:
    # A float is refused even when it is integral (1.0): the codecs parse integers into int.
    return isinstance(x, int) and not isinstance(x, bool)


def _json_equal(a: object, b: object) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) or isinstance(a, list):
        return a == b
    return type(a) is type(b) and a == b


def _const(value: object, at: str) -> _Check:
    _scalar(value, at)

    def check(instance: object, found: list[str]) -> None:
        if not _json_equal(instance, value):
            found.append(at)

    return check


def _scalar(value: object, at: str) -> None:
    if not (value is None or isinstance(value, (str, int, bool))):
        raise UnsupportedSchema(f"{at}: only string, integer, boolean or null values")


def _enum(value: object, at: str) -> _Check:
    if not isinstance(value, list) or not value:
        raise UnsupportedSchema(f"{at}: enum must be a non-empty array")
    for item in value:
        _scalar(item, at)
    options = list(value)

    def check(instance: object, found: list[str]) -> None:
        if not any(_json_equal(instance, option) for option in options):
            found.append(at)

    return check


def _not(value: object, at: str) -> _Check:
    inner = _compile(value, at)

    def check(instance: object, found: list[str]) -> None:
        if not _passes(inner, instance):
            return
        found.append(at)

    return check


def _passes(check: _Check, instance: object) -> bool:
    scratch: list[str] = []
    check(instance, scratch)
    return not scratch


def _properties(value: object, at: str) -> _Check:
    if not isinstance(value, Mapping):
        raise UnsupportedSchema(f"{at}: properties must be an object")
    compiled = {str(name): _compile(sub, f"{at}/{name}") for name, sub in value.items()}

    def check(instance: object, found: list[str]) -> None:
        if not isinstance(instance, dict):
            return
        for name, sub in compiled.items():
            if name in instance:
                sub(instance[name], found)

    return check


def _required(value: object, at: str) -> _Check:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise UnsupportedSchema(f"{at}: required must be an array of names")
    names = [str(v) for v in value]

    def check(instance: object, found: list[str]) -> None:
        if isinstance(instance, dict) and any(name not in instance for name in names):
            found.append(at)

    return check


def _additional(value: object, at: str, properties: object) -> _Check:
    if value is True:
        return lambda instance, found: None
    if value is not False:
        raise UnsupportedSchema(f"{at}: only additionalProperties false or true")
    allowed = frozenset(properties) if isinstance(properties, Mapping) else frozenset()

    def check(instance: object, found: list[str]) -> None:
        if isinstance(instance, dict) and any(key not in allowed for key in instance):
            found.append(at)

    return check


def _length(value: object, at: str, *, minimum: bool) -> _Check:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UnsupportedSchema(f"{at}: a length must be a non-negative integer")
    limit = value

    def check(instance: object, found: list[str]) -> None:
        if not isinstance(instance, str):
            return
        if (len(instance) < limit) if minimum else (len(instance) > limit):
            found.append(at)

    return check


def _pattern(value: object, at: str) -> _Check:
    if not isinstance(value, str):
        raise UnsupportedSchema(f"{at}: a pattern must be a string")
    # JSON Schema patterns are ECMA-262; the contract's patterns use only anchors, classes,
    # counts and alternation, which mean the same in Python, except that Python's "$" also
    # matches before a final newline. "\Z" is the ECMA meaning.
    try:
        compiled = re.compile(value.replace("$", r"\Z"))
    except re.error:
        raise UnsupportedSchema(f"{at}: the pattern does not compile") from None

    def check(instance: object, found: list[str]) -> None:
        if isinstance(instance, str) and compiled.search(instance) is None:
            found.append(at)

    return check


def _bound(value: object, at: str, *, minimum: bool) -> _Check:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnsupportedSchema(f"{at}: a bound must be an integer")
    limit = value

    def check(instance: object, found: list[str]) -> None:
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            return
        if isinstance(instance, float) and not math.isfinite(instance):
            found.append(at)
            return
        if (instance < limit) if minimum else (instance > limit):
            found.append(at)

    return check


def _format(value: object, at: str) -> _Check:
    tests: dict[str, Callable[[object], bool]] = {
        "uuid": lambda x: not isinstance(x, str) or _UUID.fullmatch(x) is not None,
        "date-time": lambda x: not isinstance(x, str) or _valid_date_time(x),
        "int64": _fits_int64,
    }
    if not isinstance(value, str) or value not in tests:
        raise UnsupportedSchema(f"{at}: unsupported format")
    test = tests[value]

    def check(instance: object, found: list[str]) -> None:
        if not test(instance):
            found.append(at)

    return check


def _fits_int64(x: object) -> bool:
    if isinstance(x, bool) or not isinstance(x, int):
        return True
    return _INT64_MIN <= x <= _INT64_MAX


def _valid_date_time(text: str) -> bool:
    """RFC 3339 section 5.6 ``date-time`` that names a real instant (leap seconds refused)."""
    match = _DATE_TIME.fullmatch(text)
    if match is None:
        return False
    offset = match.group(8)
    if offset not in ("Z", "z") and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        return False
    normalized = text[:10] + "T" + text[11:19]
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


def _if(value: object, at: str, node: Mapping[str, object], base: str) -> _Check:
    condition = _compile(value, at)
    then = _compile(node["then"], f"{base}/then") if "then" in node else None
    otherwise = _compile(node["else"], f"{base}/else") if "else" in node else None

    def check(instance: object, found: list[str]) -> None:
        branch = then if _passes(condition, instance) else otherwise
        if branch is not None:
            branch(instance, found)

    return check
