"""The operator CLI's dead-letter commands (task W6-E2;
.claude/docs/runbooks/operator.md section 6).

    dlq list    [--queue Q] [--limit N]
                            the messages parked in one allowed dead-letter queue, or in each
    dlq redrive --queue Q --message-id ID --by NAME [--scan-limit N]
                            move one parked message back to the exchange and routing key it was
                            first published to; it leaves the queue only after the broker
                            confirmed the copy

Only the platform's own dead-letter queues are accepted (``PLATFORM_DEAD_LETTER_TOPOLOGIES``). A
listing shows the queue, the message ID, the death record and a few envelope fields, never the
body. Every re-drive attempt, done, refused or failed, is one audit event (``dlq_redrive``, actor
kind ``operator``, the actor ``--by``); its project is the envelope's, or the nil UUID when the
message was not found or names none. A listing is not audited. Exit status: 0 done, 1 refused or
failed (the reason on standard error), 2 a usage or configuration error.
"""

from __future__ import annotations

import argparse
from typing import Final, Protocol, TextIO
from uuid import UUID

from provisioning.core.application import Clock
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.hosts.operator.args import bounded, operator_name
from provisioning.infrastructure.rabbitmq.dlq import (
    MAX_LIST_LIMIT,
    MAX_SCAN_LIMIT,
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    BrokerUnavailable,
    DeadLetterError,
    MessageNotFound,
    NoOrigin,
    ParkedMessage,
    QueueNotAllowed,
    RedrivePublishFailed,
    RemovalUnconfirmed,
    valid_message_id,
)

EXIT_DONE: Final = 0
EXIT_REFUSED: Final = 1
DEFAULT_DLQ_LIST_LIMIT: Final = 50
# The audit event's project when the message was not found or its envelope names none.
NO_PROJECT: Final = UUID(int=0)

_HEADER: Final = (
    "queue",
    "message_id",
    "death_reason",
    "death_count",
    "exchange",
    "routing_key",
    "message_type",
    "event_key",
    "project_id",
)


class DeadLetters(Protocol):
    """``infrastructure.rabbitmq.dlq.DeadLetterTool``."""

    @property
    def queues(self) -> tuple[str, ...]: ...

    async def list(self, queue: str, limit: int) -> tuple[ParkedMessage, ...]: ...

    async def redrive(self, queue: str, message_id: str, *, scan_limit: int) -> ParkedMessage: ...


_ALLOWED_QUEUES: Final = tuple(names.dead_letter_queue for names in PLATFORM_DEAD_LETTER_TOPOLOGIES)


def _queue(text: str) -> str:
    if text not in _ALLOWED_QUEUES:
        raise argparse.ArgumentTypeError(
            "must be one of the platform's dead-letter queues: " + ", ".join(_ALLOWED_QUEUES)
        )
    return text


def _message_id(text: str) -> str:
    if not valid_message_id(text):
        raise argparse.ArgumentTypeError(
            "must be 1 to 100 ASCII letters, digits, '.', '_', ':' or '-'"
        )
    return text


def add_dlq_parser(dlq: argparse.ArgumentParser) -> None:
    """The ``dlq`` command's own subcommands, on the parser ``cli.parse_args`` made for it."""
    commands = dlq.add_subparsers(dest="dlq_command", required=True)
    listing = commands.add_parser("list", help="the parked messages, never their bodies")
    listing.add_argument("--queue", type=_queue, default=None, help="one queue (default: each)")
    listing.add_argument(
        "--limit",
        type=bounded(1, MAX_LIST_LIMIT),
        default=DEFAULT_DLQ_LIST_LIMIT,
        help=f"at most this many per queue (default {DEFAULT_DLQ_LIST_LIMIT})",
    )
    redrive = commands.add_parser("redrive", help="move one parked message back to its queue")
    redrive.add_argument("--queue", type=_queue, required=True)
    redrive.add_argument("--message-id", type=_message_id, required=True)
    redrive.add_argument("--by", type=operator_name, required=True, help="the operator's name")
    redrive.add_argument(
        "--scan-limit",
        type=bounded(1, MAX_SCAN_LIMIT),
        default=MAX_SCAN_LIMIT,
        help=f"search at most this many parked messages (default {MAX_SCAN_LIMIT})",
    )


def _cell(value: str | int | UUID | None) -> str:
    return "-" if value is None else str(value)


def _line(item: ParkedMessage) -> str:
    death, envelope = item.death, item.envelope
    return "\t".join(
        _cell(v)
        for v in (
            item.queue,
            item.message_id,
            None if death is None else death.reason,
            None if death is None else death.count,
            None if death is None else death.exchange,
            None if death is None else death.routing_key,
            envelope.message_type,
            envelope.event_key,
            envelope.project_id,
        )
    )


def _refusal(exc: Exception) -> tuple[AuditResult, str, str, ParkedMessage | None] | None:
    """(audit result, failure code, message, the parked message if known) of a refused or failed
    re-drive, or None for an error that is not the tool's answer."""
    if isinstance(exc, MessageNotFound):
        return (
            AuditResult.REFUSED,
            "not_found",
            "no parked message with this ID among those searched",
            None,
        )
    if isinstance(exc, QueueNotAllowed):
        return (
            AuditResult.REFUSED,
            "queue_not_allowed",
            "not one of the platform's dead-letter queues",
            None,
        )
    if isinstance(exc, NoOrigin):
        return (
            AuditResult.REFUSED,
            "no_origin",
            "the message has no death record naming its queue's exchange and routing key; "
            "nothing was published",
            exc.parked,
        )
    if isinstance(exc, RedrivePublishFailed):
        return (
            AuditResult.FAILED,
            exc.code,
            "the broker did not confirm the copy; the message stays parked",
            exc.parked,
        )
    if isinstance(exc, RemovalUnconfirmed):
        return (
            AuditResult.FAILED,
            "removal_unconfirmed",
            "the copy was delivered but the message may still be parked: list the queue before "
            "re-driving it again (a second re-drive delivers a duplicate)",
            exc.parked,
        )
    if isinstance(exc, BrokerUnavailable):
        return AuditResult.FAILED, "broker_unreachable", "the broker could not be reached", None
    if isinstance(exc, DeadLetterError):
        return AuditResult.FAILED, "dlq_error", "the dead-letter tool failed", None
    return None


async def execute_dlq(
    args: argparse.Namespace,
    *,
    tool: DeadLetters,
    audit: AuditLog,
    clock: Clock,
    out: TextIO,
    err: TextIO,
) -> int:
    """Run one parsed ``dlq`` command; returns the exit status."""
    if args.dlq_command == "list":
        return await _list(args, tool, out, err)
    return await _redrive(args, tool, audit, clock, out, err)


async def _list(args: argparse.Namespace, tool: DeadLetters, out: TextIO, err: TextIO) -> int:
    queues = tool.queues if args.queue is None else (args.queue,)
    lines: list[str] = []
    try:
        for queue in queues:
            lines += [_line(item) for item in await tool.list(queue, args.limit)]
    except DeadLetterError as exc:
        _, code, message, _ = _refusal(exc) or (None, "dlq_error", "the tool failed", None)
        err.write(f"list {code}: {message}\n")
        return EXIT_REFUSED
    out.write("\t".join(_HEADER) + "\n")
    for line in lines:
        out.write(line + "\n")
    return EXIT_DONE


async def _redrive(
    args: argparse.Namespace,
    tool: DeadLetters,
    audit: AuditLog,
    clock: Clock,
    out: TextIO,
    err: TextIO,
) -> int:
    queue: str = args.queue
    message_id: str = args.message_id

    def record(outcome: AuditResult, failure: str | None, found: ParkedMessage | None) -> None:
        envelope = None if found is None else found.envelope
        project = None if envelope is None else envelope.project_id
        audit.record(
            AuditEvent(
                actor_kind=AuditActorKind.OPERATOR,
                actor=args.by,
                action=AuditAction.DLQ_REDRIVE,
                project_id=NO_PROJECT if project is None else project,
                release_id=None if envelope is None else envelope.release_id,
                command_id=None if envelope is None else envelope.command_id,
                result=outcome,
                failure_code=failure,
                at=clock.now(),
                correlation_id=f"dlq-redrive-{message_id}",
            )
        )

    try:
        parked = await tool.redrive(queue, message_id, scan_limit=args.scan_limit)
    except Exception as exc:
        refusal = _refusal(exc)
        if refusal is None:
            record(AuditResult.FAILED, "operator_error", None)
            raise
        result, code, message, known = refusal
        record(result, code, known)
        err.write(f"re-drive {code}: {message}\n")
        return EXIT_REFUSED
    record(AuditResult.SUCCEEDED, None, parked)
    death = parked.death
    out.write(
        f"redriven queue={queue} message_id={message_id} "
        f"exchange={_cell(None if death is None else death.exchange)} "
        f"routing_key={_cell(None if death is None else death.routing_key)} "
        f"project_id={_cell(parked.envelope.project_id)}\n"
    )
    return EXIT_DONE
