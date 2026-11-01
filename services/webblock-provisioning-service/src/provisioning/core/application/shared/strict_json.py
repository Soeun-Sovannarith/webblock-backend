"""The one strict JSON decoder of the service. The HTTP and MCP request bodies
(``presentation/operations/body.py``) and the prototype's JSON configuration files
(``infrastructure/config/files.py``) both decode through it and map its errors to their own
fixed text. Standard library only, so both layers may import it."""

from __future__ import annotations

import json
from typing import Any


class DuplicateKeyError(Exception):
    """An object repeats a key, at any depth."""


def loads_strict(raw: bytes) -> Any:
    """Decode one JSON value: valid UTF-8, no duplicate key at any depth, no NaN or Infinity,
    nothing after the value but white space.

    Raises ``DuplicateKeyError`` for a repeated key, ``ValueError`` for anything else that is not
    such a value (bad UTF-8 included), and ``RecursionError`` when the nesting is too deep.
    Callers replace each with fixed text, so no part of ``raw`` reaches a reply or a log."""
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant
    )


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateKeyError
        out[key] = value
    return out


def _no_constant(_text: str) -> object:
    raise ValueError("non-finite number")
