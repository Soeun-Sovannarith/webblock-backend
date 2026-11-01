"""Commands, queries and their two buses (decision 0025 CQ10, TR-54).

A caller's write intent is a ``Command[R]`` and a read intent a ``Query[R]``: frozen, slotted
dataclasses whose base names the result type ``R``. Each intent type has exactly one handler, an
object with one method ``async def handle(self, intent, /) -> R`` that takes its ports in its
constructor. The hosts build the handlers and register them by hand; there is no discovery, no
container and no middleware.

Two buses, so a read path can be given the ``QueryBus`` only and cannot send a command. A second
handler for one type is ``ValueError``; sending a type with no handler is ``NotRegistered``, which
the hosts' start-up check makes unreachable in a running process.

The domain has its own ``Command`` (the row of ``provisioning.commands``). This base is always used
through its module, ``cqrs.Command[R]``, never imported bare (a unit test checks it).

This module imports nothing of ours (import-linter ``cqrs-is-a-leaf``).
"""

from __future__ import annotations

from typing import Any, Protocol


class Command[R]:
    """Base of every write intent; ``R`` is what its handler returns."""

    __slots__ = ()


class Query[R]:
    """Base of every read intent; ``R`` is what its handler returns. A query changes nothing."""

    __slots__ = ()


class CommandHandler[C, R](Protocol):
    """The one use case of a command type."""

    async def handle(self, command: C, /) -> R: ...


class QueryHandler[Q, R](Protocol):
    """The one use case of a query type."""

    async def handle(self, query: Q, /) -> R: ...


class NotRegistered(LookupError):
    """No handler is registered for the intent's type on this bus."""


class _Handler(Protocol):
    async def handle(self, intent: Any, /) -> Any: ...


class _Registry:
    """Intent type to handler, exact types only (a subclass is not its base's intent)."""

    __slots__ = ("_base", "_handlers", "_name")

    def __init__(self, base: type, name: str) -> None:
        self._base = base
        self._name = name
        self._handlers: dict[type, _Handler] = {}

    def register(self, kind: type, handler: _Handler) -> None:
        if not (isinstance(kind, type) and issubclass(kind, self._base)) or kind is self._base:
            raise TypeError(f"{self._name}: {kind!r} is not a {self._base.__name__} type")
        if kind in self._handlers:
            raise ValueError(f"{self._name}: {kind.__name__} already has a handler")
        self._handlers[kind] = handler

    def handler_of(self, intent: object) -> _Handler:
        if not isinstance(intent, self._base):
            raise TypeError(f"{self._name}: {type(intent).__name__} is not a {self._base.__name__}")
        handler = self._handlers.get(type(intent))
        if handler is None:
            raise NotRegistered(f"{self._name}: no handler for {type(intent).__name__}")
        return handler

    def kinds(self) -> frozenset[type]:
        return frozenset(self._handlers)


class CommandBus:
    """Sends a command to its one registered handler."""

    __slots__ = ("_registry",)

    def __init__(self) -> None:
        self._registry = _Registry(Command, "command bus")

    def register[C: Command[Any], R](self, kind: type[C], handler: CommandHandler[C, R]) -> None:
        """Register the handler of ``kind``: ``TypeError`` when ``kind`` is not a command type,
        ``ValueError`` when it already has one."""
        self._registry.register(kind, handler)

    async def send[R](self, command: Command[R]) -> R:
        """Run the command's handler and return its result. ``NotRegistered`` when its type has
        no handler; ``TypeError`` when it is not a command."""
        result: R = await self._registry.handler_of(command).handle(command)
        return result

    def kinds(self) -> frozenset[type]:
        """The command types that have a handler (for the hosts' start-up check)."""
        return self._registry.kinds()


class QueryBus:
    """Sends a query to its one registered handler; it never takes a command."""

    __slots__ = ("_registry",)

    def __init__(self) -> None:
        self._registry = _Registry(Query, "query bus")

    def register[Q: Query[Any], R](self, kind: type[Q], handler: QueryHandler[Q, R]) -> None:
        """Register the handler of ``kind``: ``TypeError`` when ``kind`` is not a query type,
        ``ValueError`` when it already has one."""
        self._registry.register(kind, handler)

    async def send[R](self, query: Query[R]) -> R:
        """Run the query's handler and return its result. ``NotRegistered`` when its type has no
        handler; ``TypeError`` when it is not a query."""
        result: R = await self._registry.handler_of(query).handle(query)
        return result

    def kinds(self) -> frozenset[type]:
        """The query types that have a handler (for the hosts' start-up check)."""
        return self._registry.kinds()
