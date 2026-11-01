"""The Payment-event path of the worker's command bus (task G; F2 report, "Contract of the
in-process entitlement record"). Kept apart from the wiring so that it loads no cluster client."""

from __future__ import annotations

from provisioning.core.application import AcceptHostingEventCommand
from provisioning.core.application.shared import cqrs
from provisioning.core.domain import CommandKind
from provisioning.hosts.shared.stand_ins import PaymentEventRecord
from provisioning.presentation.messaging.consumers.messages import CommandSender


class RecordingHostingEvents:
    """Records a Payment event, by its key, in the in-process record first ("ended" is not
    entitled, "restored" is), then sends its command through the worker's command bus. A
    redelivered event keeps its first position; a refused event (retried later) has still been
    recorded (decision 0014 section 17 item 2). Only the Payment consumer is given it: the
    deletion consumer and the retry are not Payment events and are never recorded."""

    __slots__ = ("_commands", "_record")

    def __init__(self, commands: CommandSender, record: PaymentEventRecord) -> None:
        self._commands = commands
        self._record = record

    async def send[R](self, command: cqrs.Command[R], /) -> R:
        if isinstance(command, AcceptHostingEventCommand):
            if command.kind is CommandKind.SUSPEND_HOSTING:
                self._record.record(command.project_id, event_key=command.event_key, entitled=False)
            elif command.kind is CommandKind.RESUME_HOSTING:
                self._record.record(command.project_id, event_key=command.event_key, entitled=True)
        return await self._commands.send(command)
