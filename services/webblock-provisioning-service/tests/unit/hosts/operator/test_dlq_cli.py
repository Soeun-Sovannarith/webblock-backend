"""The operator's dead-letter commands (task W6-E2): ``dlq list`` shows parked messages without
their bodies, ``dlq redrive`` moves one back and writes one audit event per attempt, whatever its
result. Exit status 0 done, 1 refused or failed, 2 usage. The tool is the real ``DeadLetterTool``
on the in-memory broker of ``_dlq_fakes``."""

from __future__ import annotations

import io
from uuid import UUID

import pytest
from tests.unit.hosts.operator._dlq_fakes import (
    AT,
    CONTENT_MARKER,
    PROJECT,
    FakeBroker,
    death,
    parked,
)

from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.hosts.operator.cli import parse_args
from provisioning.hosts.operator.dlq_cli import (
    DEFAULT_DLQ_LIST_LIMIT,
    NO_PROJECT,
    execute_dlq,
)
from provisioning.infrastructure.rabbitmq import (
    ENTITLEMENT_ENDED_NAMES,
    PROJECT_DELETED_NAMES,
)
from provisioning.infrastructure.rabbitmq.dlq import (
    MAX_LIST_LIMIT,
    MAX_SCAN_LIMIT,
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    BrokerUnavailable,
    DeadLetterTool,
    PublishRefused,
)

DLQ = ENTITLEMENT_ENDED_NAMES.dead_letter_queue


class Clock:
    def now(self):  # type: ignore[no-untyped-def]  # the Clock protocol's shape
        return AT


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("a re-drive is never an alert")


async def run(
    broker: FakeBroker, *argv: str, audit: Audit | None = None
) -> tuple[int, str, str, Audit]:
    log = Audit() if audit is None else audit
    out, err = io.StringIO(), io.StringIO()
    code = await execute_dlq(
        parse_args(list(argv)),
        tool=DeadLetterTool(PLATFORM_DEAD_LETTER_TOPOLOGIES, broker.session),
        audit=log,
        clock=Clock(),
        out=out,
        err=err,
    )
    return code, out.getvalue(), err.getvalue(), log


REDRIVE = ("dlq", "redrive", "--queue", DLQ, "--message-id", "m-1", "--by", "alice")


# --- the command line -------------------------------------------------------------------------


def test_the_listing_defaults() -> None:
    args = parse_args(["dlq", "list"])
    assert (args.command, args.dlq_command) == ("dlq", "list")
    assert args.queue is None
    assert args.limit == DEFAULT_DLQ_LIST_LIMIT


def test_the_redrive_arguments() -> None:
    args = parse_args(list(REDRIVE))
    assert (args.queue, args.message_id, args.by) == (DLQ, "m-1", "alice")
    assert args.scan_limit == MAX_SCAN_LIMIT


@pytest.mark.parametrize(
    "argv",
    [
        ["dlq"],
        ["dlq", "list", "--limit", "0"],
        ["dlq", "list", "--limit", str(MAX_LIST_LIMIT + 1)],
        ["dlq", "list", "--queue", ENTITLEMENT_ENDED_NAMES.queue],
        ["dlq", "list", "--queue", "payment.publish_completed.v1.dlq"],
        ["dlq", "redrive", "--queue", DLQ, "--message-id", "m-1"],  # no --by
        ["dlq", "redrive", "--queue", DLQ, "--by", "alice"],  # no --message-id
        ["dlq", "redrive", "--message-id", "m-1", "--by", "alice"],  # no --queue
        ["dlq", "redrive", "--queue", "other", "--message-id", "m-1", "--by", "alice"],
        ["dlq", "redrive", "--queue", DLQ, "--message-id", "a b", "--by", "alice"],
        ["dlq", "redrive", "--queue", DLQ, "--message-id", "m-1", "--by", " alice"],
        [*REDRIVE, "--scan-limit", "0"],
        [*REDRIVE, "--scan-limit", str(MAX_SCAN_LIMIT + 1)],
    ],
)
def test_a_usage_error_exits_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_args(argv)
    assert caught.value.code == 2


# --- list -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_listing_prints_one_line_per_parked_message_and_never_the_body() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", deaths=[death(count=3)]), parked("m-2"))
    code, out, err, audit = await run(broker, "dlq", "list", "--queue", DLQ)
    assert (code, err) == (0, "")
    lines = [line.split("\t") for line in out.splitlines()]
    assert lines[0] == [
        "queue",
        "message_id",
        "death_reason",
        "death_count",
        "exchange",
        "routing_key",
        "message_type",
        "event_key",
        "project_id",
    ]
    assert lines[1] == [
        DLQ,
        "m-1",
        "delivery_limit",
        "3",
        ENTITLEMENT_ENDED_NAMES.exchange,
        "hosting_entitlement_ended.v1",
        "payment.hosting_entitlement_ended.v1",
        "payment-event-1",
        str(PROJECT),
    ]
    assert len(lines) == 3
    assert CONTENT_MARKER not in out
    assert audit.events == []  # a read is not audited
    assert len(broker.queues[DLQ]) == 2


@pytest.mark.asyncio
async def test_a_listing_without_a_queue_reads_every_allowed_queue() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1"))
    broker.park(PROJECT_DELETED_NAMES.dead_letter_queue, parked("m-9", body=b"not json"))
    code, out, _, _ = await run(broker, "dlq", "list", "--limit", "5")
    assert code == 0
    rows = [line.split("\t") for line in out.splitlines()[1:]]
    assert [(r[0], r[1]) for r in rows] == [
        (PROJECT_DELETED_NAMES.dead_letter_queue, "m-9"),
        (DLQ, "m-1"),
    ]
    assert rows[0][2:] == ["-"] * 7  # no death of its work queue, no envelope
    gets = [c for c in broker.calls if c[0] == "get"]
    assert {c[1] for c in gets} == set(
        DeadLetterTool(PLATFORM_DEAD_LETTER_TOPOLOGIES, broker.session).queues
    )


@pytest.mark.asyncio
async def test_a_listing_on_an_unreachable_broker_exits_1() -> None:
    broker = FakeBroker(open_fault=BrokerUnavailable("x"))
    code, out, err, _ = await run(broker, "dlq", "list")
    assert code == 1
    assert out == ""
    assert "broker_unreachable" in err


# --- redrive ----------------------------------------------------------------------------------


def only(audit: Audit) -> AuditEvent:
    assert len(audit.events) == 1, audit.events
    event = audit.events[0]
    assert event.action is AuditAction.DLQ_REDRIVE
    assert event.actor_kind is AuditActorKind.OPERATOR
    assert event.actor == "alice"
    assert event.at == AT
    assert event.correlation_id == "dlq-redrive-m-1"
    return event


@pytest.mark.asyncio
async def test_a_redrive_prints_the_origin_and_is_one_succeeded_audit_event() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1"))
    code, out, err, audit = await run(broker, *REDRIVE)
    assert (code, err) == (0, "")
    assert out == (
        f"redriven queue={DLQ} message_id=m-1 exchange={ENTITLEMENT_ENDED_NAMES.exchange} "
        f"routing_key=hosting_entitlement_ended.v1 project_id={PROJECT}\n"
    )
    event = only(audit)
    assert (event.result, event.failure_code, event.project_id) == (
        AuditResult.SUCCEEDED,
        None,
        PROJECT,
    )
    assert broker.queues[DLQ] == []
    assert len(broker.published) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "result", "code_name", "project"),
    [
        ("absent", AuditResult.REFUSED, "not_found", NO_PROJECT),
        ("no_origin", AuditResult.REFUSED, "no_origin", PROJECT),
        ("nacked", AuditResult.FAILED, "publish_nacked", PROJECT),
        ("returned", AuditResult.FAILED, "publish_returned", PROJECT),
        ("timeout", AuditResult.FAILED, "publish_unconfirmed", PROJECT),
        ("ack_lost", AuditResult.FAILED, "removal_unconfirmed", PROJECT),
        ("unreachable", AuditResult.FAILED, "broker_unreachable", NO_PROJECT),
    ],
)
async def test_a_refused_or_failed_redrive_exits_1_with_one_audit_event(
    setup: str, result: AuditResult, code_name: str, project: UUID
) -> None:
    broker = FakeBroker()
    if setup != "absent":
        broker.park(DLQ, parked("m-1", deaths=[] if setup == "no_origin" else None))
    broker.publish_fault = {
        "nacked": PublishRefused("publish_nacked"),
        "returned": PublishRefused("publish_returned"),
        "timeout": TimeoutError(),
    }.get(setup)
    if setup == "ack_lost":
        broker.ack_fault = ConnectionResetError()
    if setup == "unreachable":
        broker.open_fault = BrokerUnavailable("x")
    code, out, err, audit = await run(broker, *REDRIVE)
    assert code == 1
    assert out == ""
    assert err.startswith(f"re-drive {code_name}: ")
    assert CONTENT_MARKER not in err
    event = only(audit)
    assert (event.result, event.failure_code, event.project_id) == (result, code_name, project)


@pytest.mark.asyncio
async def test_an_unexpected_error_still_writes_its_audit_event() -> None:
    broker = FakeBroker(open_fault=RuntimeError("boom"))
    audit = Audit()
    with pytest.raises(RuntimeError):
        await run(broker, *REDRIVE, audit=audit)
    event = only(audit)
    assert (event.result, event.failure_code) == (AuditResult.FAILED, "operator_error")
