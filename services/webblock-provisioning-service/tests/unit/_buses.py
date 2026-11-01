"""A command bus around the intakes the consumer tests give (decision 0025 CQ11): a real
handler is registered as it is; a test fake keeps its own method, and the bus hands it the fields
of the command it receives."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from provisioning.core.application import (
    AcceptedCommand,
    AcceptHostingEventCommand,
    AcceptOwnerStatusCommand,
    CommandBus,
    IntakeOutcome,
    OwnerStatus,
    OwnerStatusResult,
    ReceiveSourcePublishedCommand,
)
from provisioning.core.domain import CommandKind, SourcePublishedFields


class SourceIntake(Protocol):
    async def receive(self, fields: SourcePublishedFields) -> IntakeOutcome: ...


class HostingEventIntake(Protocol):
    async def accept_hosting_event(
        self, event_key: str, project_id: UUID, kind: CommandKind
    ) -> AcceptedCommand: ...


class OwnerStatusIntake(Protocol):
    async def accept_owner_status(
        self, project_id: UUID, status: OwnerStatus, version: int, subdomain: str
    ) -> OwnerStatusResult: ...


class HostingEventHandler(Protocol):
    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand: ...


class OwnerStatusHandler(Protocol):
    async def handle(self, command: AcceptOwnerStatusCommand, /) -> OwnerStatusResult: ...


class _Receive:
    def __init__(self, intake: SourceIntake) -> None:
        self._intake = intake

    async def handle(self, command: ReceiveSourcePublishedCommand, /) -> IntakeOutcome:
        return await self._intake.receive(command.fields)


class _HostingEvent:
    def __init__(self, intake: HostingEventIntake) -> None:
        self._intake = intake

    async def handle(self, command: AcceptHostingEventCommand, /) -> AcceptedCommand:
        return await self._intake.accept_hosting_event(
            command.event_key, command.project_id, command.kind
        )


class _OwnerStatus:
    def __init__(self, intake: OwnerStatusIntake) -> None:
        self._intake = intake

    async def handle(self, command: AcceptOwnerStatusCommand, /) -> OwnerStatusResult:
        return await self._intake.accept_owner_status(
            command.project_id, command.status, command.version, command.subdomain
        )


def intake_bus(
    *,
    source: SourceIntake | None = None,
    hosting: HostingEventIntake | None = None,
    hosting_handler: HostingEventHandler | None = None,
    owner_status: OwnerStatusIntake | None = None,
    owner_status_handler: OwnerStatusHandler | None = None,
) -> CommandBus:
    """A bus with a handler for each intake given: a fake by its own method, a real handler as
    it is."""
    bus = CommandBus()
    if source is not None:
        bus.register(ReceiveSourcePublishedCommand, _Receive(source))
    if hosting is not None:
        bus.register(AcceptHostingEventCommand, _HostingEvent(hosting))
    if hosting_handler is not None:
        bus.register(AcceptHostingEventCommand, hosting_handler)
    if owner_status is not None:
        bus.register(AcceptOwnerStatusCommand, _OwnerStatus(owner_status))
    if owner_status_handler is not None:
        bus.register(AcceptOwnerStatusCommand, owner_status_handler)
    return bus
