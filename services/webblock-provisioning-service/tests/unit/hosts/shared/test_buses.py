"""TR-54 (decision 0025 CQ11): each process checks at start that every command its consumers
send, and every operation it serves, has a registered handler; a missing one fails the start."""

from dataclasses import dataclass

import pytest
from tests.unit.presentation.memory import MemoryStores, memory_operations

from provisioning.core.application import (
    OPERATION_COMMANDS,
    OPERATION_QUERIES,
    Action,
    CommandBus,
    QueryBus,
)
from provisioning.core.application.shared import cqrs
from provisioning.hosts.build_worker.wiring import BUILD_WORKER_COMMANDS
from provisioning.hosts.shared.buses import MissingHandler, require_handlers, require_operations
from provisioning.hosts.worker.events import WORKER_COMMANDS
from provisioning.presentation.operations.operations import OPERATIONS


@dataclass(frozen=True, slots=True)
class _Ping(cqrs.Command[str]):
    pass


@dataclass(frozen=True, slots=True)
class _Pong(cqrs.Command[str]):
    pass


@dataclass(frozen=True, slots=True)
class _Look(cqrs.Query[int]):
    pass


class _Handler:
    async def handle(self, intent: object, /) -> str:
        return "ok"


def test_tr54_a_missing_handler_fails_the_start_and_names_every_type() -> None:
    bus = CommandBus()
    bus.register(_Ping, _Handler())
    with pytest.raises(MissingHandler, match=r"^worker: no handler registered for _Look, _Pong$"):
        require_handlers(bus, (_Ping, _Pong, _Look), process="worker")


def test_tr54_every_required_handler_present_passes() -> None:
    commands, queries = CommandBus(), QueryBus()
    commands.register(_Ping, _Handler())
    commands.register(_Pong, _Handler())
    queries.register(_Look, _Handler())
    require_handlers(commands, (_Ping, _Pong), process="api")
    require_handlers(queries, (_Look,), process="api")
    require_handlers(commands, (), process="api")


def test_tr54_the_worker_processes_require_their_consumers_commands() -> None:
    assert {kind.__name__ for kind in WORKER_COMMANDS} == {
        "AcceptHostingEventCommand",
        "AcceptOwnerStatusCommand",
    }
    assert {kind.__name__ for kind in BUILD_WORKER_COMMANDS} == {"ReceiveSourcePublishedCommand"}


def test_tr54_the_api_buses_answer_every_operation_of_the_table_once() -> None:
    service = memory_operations(MemoryStores())
    require_operations(
        service.commands,
        service.queries,
        (op.action for op in OPERATIONS),
        command_of=OPERATION_COMMANDS,
        query_of=OPERATION_QUERIES,
        process="api",
    )
    assert len(service.commands.kinds()) == 7
    assert len(service.queries.kinds()) == 7
    assert set(OPERATION_COMMANDS).isdisjoint(OPERATION_QUERIES)
    assert set(OPERATION_COMMANDS) | set(OPERATION_QUERIES) == {op.action for op in OPERATIONS}


def test_tr54_an_operation_without_a_handler_or_intent_fails_the_api_start() -> None:
    commands, queries = CommandBus(), QueryBus()
    with pytest.raises(MissingHandler, match="DeployReleaseCommand"):
        require_operations(
            commands,
            queries,
            (Action.DEPLOY_RELEASE,),
            command_of=OPERATION_COMMANDS,
            query_of=OPERATION_QUERIES,
            process="api",
        )
    with pytest.raises(MissingHandler, match=r"none \['get_release'\]"):
        require_operations(
            commands,
            queries,
            (Action.GET_RELEASE,),
            command_of=OPERATION_COMMANDS,
            query_of={},
            process="api",
        )
    with pytest.raises(MissingHandler, match=r"two intents \['get_release'\]"):
        require_operations(
            commands,
            queries,
            (Action.GET_RELEASE,),
            command_of={Action.GET_RELEASE: _Ping},
            query_of=OPERATION_QUERIES,
            process="api",
        )
