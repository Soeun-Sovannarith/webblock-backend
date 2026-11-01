"""Strict reading of the JSON configuration files a process is pointed at (the prototype's token
and repository files). A problem is reported with fixed text, never with the file's contents."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from provisioning.core.application.shared.strict_json import DuplicateKeyError, loads_strict

MAX_FILE_BYTES: Final = 1 << 20


class ConfigFileError(Exception):
    """A configuration file that cannot be read or does not have the expected shape. The message
    names the file's role and the problem, never a value from the file."""


def read_file(path: Path, what: str) -> bytes:
    """The file's bytes, at most MAX_FILE_BYTES; ``ConfigFileError`` without the operating
    system's text otherwise."""
    try:
        with path.open("rb") as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise ConfigFileError(f"{what} cannot be read") from None
    if len(data) > MAX_FILE_BYTES:
        raise ConfigFileError(f"{what} is larger than {MAX_FILE_BYTES} bytes")
    return data


def json_object(
    raw: bytes, what: str, keys: frozenset[str], error: Callable[[str], Exception]
) -> dict[str, Any]:
    """Decode one JSON object with exactly ``keys`` (no duplicate key at any level, no
    non-finite number); ``error(text)`` with fixed text otherwise."""
    try:
        doc = loads_strict(raw)
    except DuplicateKeyError:
        raise error(f"{what} has a duplicate key") from None
    except ValueError:
        raise error(f"{what} is not a JSON object") from None
    if not isinstance(doc, dict):
        raise error(f"{what} is not a JSON object")
    check_keys(doc, keys, what, error)
    return doc


def check_keys(
    obj: dict[str, Any], keys: frozenset[str], where: str, error: Callable[[str], Exception]
) -> None:
    """Refuse an object whose keys are not exactly ``keys``, naming no key from the file."""
    if set(obj) - keys:
        raise error(f"{where} has an unknown key")
    if keys - set(obj):
        raise error(f"{where} lacks " + ", ".join(sorted(keys - set(obj))))
