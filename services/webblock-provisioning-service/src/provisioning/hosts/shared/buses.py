"""The start-up check of a process's command and query buses (decision 0025 CQ11, TR-54):
each process registers only the handlers it may run, and fails at start, never at the first
request or message, when one it needs is missing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from provisioning.core.application import Action, CommandBus, QueryBus


class MissingHandler(RuntimeError):
    """A command or query type the process needs has no registered handler."""


def require_handlers(bus: CommandBus | QueryBus, required: Iterable[type], *, process: str) -> None:
    """Raise ``MissingHandler`` naming every type of ``required`` the bus has no handler for."""
    registered = bus.kinds()
    missing = sorted(kind.__name__ for kind in required if kind not in registered)
    if missing:
        raise MissingHandler(f"{process}: no handler registered for {', '.join(missing)}")


def require_operations(
    commands: CommandBus,
    queries: QueryBus,
    actions: Iterable[Action],
    *,
    command_of: Mapping[Action, type],
    query_of: Mapping[Action, type],
    process: str,
) -> None:
    """Every action of the operation table has exactly one intent, command or query, and its
    handler is registered on that bus (a bus never takes a second handler for one type).
    ``MissingHandler`` names what is missing."""
    actions = tuple(actions)
    both = sorted(str(a) for a in actions if a in command_of and a in query_of)
    neither = sorted(str(a) for a in actions if a not in command_of and a not in query_of)
    if both or neither:
        raise MissingHandler(f"{process}: operations with two intents {both} or none {neither}")
    require_handlers(commands, (command_of[a] for a in actions if a in command_of), process=process)
    require_handlers(queries, (query_of[a] for a in actions if a in query_of), process=process)
