"""The request ID of the call in flight (spec E8). The HTTP middleware accepts or makes it and
keeps it in a context variable and in the ASGI scope's ``state``; the operation layer takes it as
the correlation ID. The MCP tool handler runs in a task the SDK started earlier, so it reads the ID
from the request's scope and re-enters the scope itself."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Final

_STATE_KEY: Final = "provisioning_request_id"
_CURRENT: Final[ContextVar[str | None]] = ContextVar("provisioning_request_id", default=None)


def current_request_id() -> str | None:
    return _CURRENT.get()


@contextmanager
def request_id_scope(value: str) -> Iterator[None]:
    token = _CURRENT.set(value)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def with_request_id(scope: MutableMapping[str, Any], value: str) -> None:
    scope.setdefault("state", {})[_STATE_KEY] = value


def request_id_from(scope: Mapping[str, Any]) -> str | None:
    state = scope.get("state")
    if not isinstance(state, Mapping):
        return None
    value = state.get(_STATE_KEY)
    return value if isinstance(value, str) else None
