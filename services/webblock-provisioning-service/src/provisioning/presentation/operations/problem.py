"""The one error body of REST and MCP: an RFC 9457 Problem Details object (decision 0018, amended
by decision 0023 G5 to Notion 2.2).

Members in this order: ``type`` (``urn:webblock:problem:`` and the code in lower case with ``_``
replaced by ``-``), ``title`` (the code's fixed message), ``status`` (the code's HTTP status, the
same number on MCP), ``instance`` (REST only: the request path, percent-encoded), ``code``
(UPPER_SNAKE), ``requestId`` (the X-Request-Id), ``traceId`` (only when the request is sampled),
``errors`` (always; one ``{field, code, message}`` per bad input of a 400), then ``commandId`` when
a command is involved. There is no ``detail``, and a field error carries fixed text, so nothing a
provider, a token or a caller's input says can reach the body.

The content type of a REST error follows the request's ``Accept`` header exactly as in the Project
service (its spec 6.3, owner ruling of 15 October 2026): ``problem_media_type``. An error is never
a 406; the status and the body do not depend on ``Accept``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final
from urllib.parse import quote
from uuid import UUID

from provisioning.presentation.operations.errors import FieldError, message

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
PROBLEM_TYPE_PREFIX: Final = "urn:webblock:problem:"
_JSON_MEDIA_TYPE: Final = "application/json"

_PATH_SAFE: Final = "/-._~!$&'()*+,;=:@"
"""RFC 3986 ``pchar`` and ``/``: kept as they are; every other character is percent-encoded."""

_MEDIA_NAME: Final = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_RANGE: Final = re.compile(rf"({_MEDIA_NAME})(?:/({_MEDIA_NAME}))?((?:\s*;[^;]*)*)")
_QUALITY: Final = re.compile(r"[01](?:\.\d*)?")
"""A number from 0 to 1 with any number of decimals, or none after the dot (``1.``): RFC 9110's
``qvalue`` is stricter (3 decimals), but Spring's ``Double.parseDouble`` is not and parity wins.
A leading dot (``.5``) stays refused: the value must start with 0 or 1.
The range is checked with the value."""


def _split_outside_quotes(text: str, separator: str) -> list[str]:
    """The pieces of ``text`` between ``separator`` characters outside double quotes; a backslash
    escapes the next character (Spring's ``MimeTypeUtils.tokenize``). One pass, linear in the
    length of ``text``."""
    items: list[str] = []
    start, quoted, i = 0, False, 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif ch == separator and not quoted:
            items.append(text[start:i])
            start = i + 1
        elif ch == "\\":
            i += 1
        i += 1
    items.append(text[start:])
    return items


def problem_type(code: str) -> str:
    """The URN of a code: one per code, an identifier and not a page."""
    return PROBLEM_TYPE_PREFIX + code.lower().replace("_", "-")


def problem_instance(path: str) -> str:
    """The request path as an ASCII URI reference. ASGI keeps the query string out of ``path``;
    a quote, a control character, a space or a non-ASCII character is percent-encoded as UTF-8
    (a character UTF-8 cannot encode becomes ``%3F``), so the member is never raw caller text."""
    return quote(path, safe=_PATH_SAFE, errors="replace")


def request_accept(scope: Mapping[str, Any]) -> str | None:
    """The request's ``Accept`` header lines of an ASGI scope as one list, or ``None``."""
    headers = scope.get("headers", ())
    lines = [value.decode("latin-1") for name, value in headers if name == b"accept"]
    return ", ".join(lines) if lines else None


def problem_media_type(accept: str | None) -> str:
    """The content type of an error response for an ``Accept`` header value (spec 6.3 of the
    Project service): ``application/problem+json`` whenever the client allows it (absent, ``*/*``,
    ``application/*``, ``application/problem+json``); ``application/json`` for a client that
    allows that but none of those; ``application/problem+json`` for any other ``Accept`` and for
    an unreadable one. A range allows a type only with a quality above 0. Pure; never raises."""
    ranges = _ranges(accept)
    if not ranges or _allows(ranges, PROBLEM_MEDIA_TYPE):
        return PROBLEM_MEDIA_TYPE
    return _JSON_MEDIA_TYPE if _allows(ranges, _JSON_MEDIA_TYPE) else PROBLEM_MEDIA_TYPE


def _ranges(accept: str | None) -> list[tuple[str, str, float]]:
    """The media ranges of an ``Accept`` value as (type, subtype, quality), lower-cased. An
    unreadable value (a bad range, a quality that is not a number from 0 to 1) gives ``[]``, like
    an absent one, as the Project service does."""
    ranges: list[tuple[str, str, float]] = []
    for raw in _split_outside_quotes(accept or "", ","):
        item = raw.strip()
        if not item:
            continue
        match = _RANGE.fullmatch(item)
        if match is None:
            return []
        kind, subtype = match.group(1).lower(), (match.group(2) or "").lower()
        if not subtype and kind != "*":
            return []
        subtype = subtype or "*"
        if kind == "*" and subtype != "*":
            return []
        quality = 1.0
        for parameter in _split_outside_quotes(match.group(3), ";")[1:]:
            name, _, value = parameter.partition("=")
            if name.strip().lower() == "q":
                value = value.strip()
                if _QUALITY.fullmatch(value) is None or float(value) > 1.0:
                    return []
                quality = float(value)
        ranges.append((kind, subtype, quality))
    return ranges


def _allows(ranges: list[tuple[str, str, float]], media_type: str) -> bool:
    kind, _, subtype = media_type.partition("/")
    return any(q > 0 and _includes(k, s, kind, subtype) for k, s, q in ranges)


def _includes(range_kind: str, range_subtype: str, kind: str, subtype: str) -> bool:
    """Whether a media range covers a type: ``*/*``, ``kind/*``, ``kind/*+suffix`` or equal."""
    if range_kind == "*":
        return True
    if range_kind != kind:
        return False
    if range_subtype in ("*", subtype):
        return True
    return range_subtype.startswith("*+") and subtype.endswith(range_subtype[1:])


def problem_body(
    code: str,
    status: int,
    *,
    request_id: str,
    trace_id: str | None,
    errors: tuple[FieldError, ...] = (),
    command_id: UUID | None = None,
    instance: str | None = None,
    title: str | None = None,
) -> bytes:
    """The Problem of ``code`` as compact JSON, members in the order of decision 0023 G5. Without
    ``instance`` it is the MCP body; with the request path it is the REST body. ``title`` defaults
    to the code's fixed message (an upstream reason, decision 0023 G14, keeps the title of the code
    it replaces). Every member is fixed text, a validated identifier or the percent-encoded path,
    so encoding cannot fail."""
    body: dict[str, object] = {
        "type": problem_type(code),
        "title": message(code) if title is None else title,
        "status": status,
    }
    if instance is not None:
        body["instance"] = problem_instance(instance)
    body["code"] = str(code)
    body["requestId"] = request_id
    if trace_id is not None:
        body["traceId"] = trace_id
    body["errors"] = [error.as_json() for error in errors]
    if command_id is not None:
        body["commandId"] = str(command_id)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
