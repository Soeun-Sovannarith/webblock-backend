"""Correlation IDs. The current ID travels in
a context variable, so each asyncio task sees its own."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

MAX_CORRELATION_ID_LEN: Final = 128
"""The longest correlation ID: the width of the column that stores it (decision 0005 section 2)
and the application's MAX_CORRELATION_ID_LEN (a test keeps them equal)."""

_FORM: Final = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)

_CURRENT: Final[ContextVar[str | None]] = ContextVar("provisioning_correlation_id", default=None)


def valid_correlation_id(value: str) -> bool:
    """1 to 128 ASCII letters, digits, '.', '_', ':' and '-'. A caller-supplied ID is untrusted:
    it is stored, logged and sent in a message, so it must be unable to carry a line break, a quote
    or any other character with a meaning there."""
    return _FORM.fullmatch(value) is not None


def new_correlation_id() -> str:
    """A fresh ID: a UUIDv7 (spec E8)."""
    return str(uuid.uuid7())


def correlation_id(supplied: str) -> str:
    """``supplied`` when it is valid, a generated ID otherwise."""
    return supplied if valid_correlation_id(supplied) else new_correlation_id()


def current_correlation_id() -> str | None:
    """The ID of the current context, or None outside any scope."""
    return _CURRENT.get()


@contextmanager
def correlation_scope(value: str) -> Iterator[str]:
    """Make ``correlation_id(value)`` the current ID for the block and restore the previous one
    after it; yields the ID in force."""
    current = correlation_id(value)
    token = _CURRENT.set(current)
    try:
        yield current
    finally:
        _CURRENT.reset(token)
