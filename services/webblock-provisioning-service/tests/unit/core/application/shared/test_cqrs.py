"""TR-54 (decision 0025 CQ10): the command and query buses of core/application/shared/cqrs.py.
One handler per intent type, a second one is refused, an unknown type is refused, the result is
the handler's, and the query bus never takes a command."""

from dataclasses import dataclass

import pytest

from provisioning.core.application.shared import cqrs


@dataclass(frozen=True, slots=True)
class _Rename(cqrs.Command[str]):
    name: str


@dataclass(frozen=True, slots=True)
class _Other(cqrs.Command[int]):
    value: int


@dataclass(frozen=True, slots=True)
class _Lookup(cqrs.Query[int]):
    key: str


@dataclass(frozen=True, slots=True)
class _SubRename(_Rename):
    """A subclass is its own intent type, not its base's."""


class _RenameHandler:
    def __init__(self) -> None:
        self.seen: list[_Rename] = []

    async def handle(self, command: _Rename, /) -> str:
        self.seen.append(command)
        return command.name.upper()


class _OtherHandler:
    async def handle(self, command: _Other, /) -> int:
        return command.value + 1


class _LookupHandler:
    async def handle(self, query: _Lookup, /) -> int:
        return len(query.key)


@pytest.mark.asyncio
async def test_tr54_a_command_reaches_its_one_handler_and_returns_its_result() -> None:
    bus = cqrs.CommandBus()
    handler = _RenameHandler()
    bus.register(_Rename, handler)
    bus.register(_Other, _OtherHandler())
    result: str = await bus.send(_Rename(name="web"))
    assert result == "WEB"
    assert handler.seen == [_Rename(name="web")]
    assert await bus.send(_Other(value=41)) == 42
    assert bus.kinds() == frozenset({_Rename, _Other})


@pytest.mark.asyncio
async def test_tr54_a_query_reaches_its_one_handler() -> None:
    bus = cqrs.QueryBus()
    bus.register(_Lookup, _LookupHandler())
    assert await bus.send(_Lookup(key="abcd")) == 4
    assert bus.kinds() == frozenset({_Lookup})


def test_tr54_a_second_handler_for_one_type_is_refused() -> None:
    bus = cqrs.CommandBus()
    first = _RenameHandler()
    bus.register(_Rename, first)
    with pytest.raises(ValueError, match="_Rename already has a handler"):
        bus.register(_Rename, _RenameHandler())
    queries = cqrs.QueryBus()
    queries.register(_Lookup, _LookupHandler())
    with pytest.raises(ValueError, match="_Lookup already has a handler"):
        queries.register(_Lookup, _LookupHandler())


@pytest.mark.asyncio
async def test_tr54_an_unregistered_type_is_refused_and_no_handler_runs() -> None:
    bus = cqrs.CommandBus()
    handler = _RenameHandler()
    bus.register(_Rename, handler)
    with pytest.raises(cqrs.NotRegistered, match="no handler for _Other"):
        await bus.send(_Other(value=1))
    with pytest.raises(cqrs.NotRegistered, match="no handler for _SubRename"):
        await bus.send(_SubRename(name="x"))
    assert handler.seen == []
    with pytest.raises(cqrs.NotRegistered, match="no handler for _Lookup"):
        await cqrs.QueryBus().send(_Lookup(key="k"))
    assert issubclass(cqrs.NotRegistered, LookupError)


@pytest.mark.asyncio
async def test_tr54_the_query_bus_never_takes_a_command() -> None:
    queries = cqrs.QueryBus()
    with pytest.raises(TypeError, match="not a Query type"):
        queries.register(_Rename, _RenameHandler())  # type: ignore[type-var]
    with pytest.raises(TypeError, match="_Rename is not a Query"):
        await queries.send(_Rename(name="x"))  # type: ignore[arg-type]
    assert queries.kinds() == frozenset()


@pytest.mark.asyncio
async def test_tr54_the_command_bus_never_takes_a_query() -> None:
    commands = cqrs.CommandBus()
    with pytest.raises(TypeError, match="not a Command type"):
        commands.register(_Lookup, _LookupHandler())  # type: ignore[type-var]
    with pytest.raises(TypeError, match="_Lookup is not a Command"):
        await commands.send(_Lookup(key="k"))  # type: ignore[arg-type]


def test_tr54_the_bases_themselves_cannot_be_registered() -> None:
    with pytest.raises(TypeError, match="not a Command type"):
        cqrs.CommandBus().register(cqrs.Command, _RenameHandler())  # type: ignore[misc]
    with pytest.raises(TypeError, match="not a Query type"):
        cqrs.QueryBus().register(cqrs.Query, _LookupHandler())  # type: ignore[misc]


def test_tr54_intents_are_frozen_and_slotted() -> None:
    intent = _Rename(name="a")
    with pytest.raises(AttributeError):
        intent.name = "b"  # type: ignore[misc]
    assert not hasattr(intent, "__dict__")
