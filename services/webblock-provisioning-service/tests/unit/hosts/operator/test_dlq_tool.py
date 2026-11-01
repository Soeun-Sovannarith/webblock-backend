"""The dead-letter tool (task W6-E2): only the platform's own dead-letter queues, a bounded listing
that settles nothing, the ``x-death`` parse, and a re-drive that removes the parked message only
after the broker confirmed the copy. The broker is ``_dlq_fakes.FakeBroker``; the AMQP session
runs on a real RabbitMQ in ``tests/messaging/test_dlq.py``."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from tests.unit.hosts.operator._dlq_fakes import (
    CONTENT_MARKER,
    PROJECT,
    FakeBroker,
    death,
    entitlement_body,
    parked,
)

from provisioning.infrastructure.rabbitmq import (
    COMMAND_JOB_NAMES,
    ENTITLEMENT_ENDED_NAMES,
    PROJECT_DELETED_NAMES,
    SOURCE_PUBLISHED_NAMES,
)
from provisioning.infrastructure.rabbitmq.dlq import (
    MAX_LIST_LIMIT,
    MAX_SCAN_LIMIT,
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    BrokerUnavailable,
    DeadLetterTool,
    InvalidLimit,
    MessageNotFound,
    NoOrigin,
    PublishRefused,
    QueueNotAllowed,
    RedrivePublishFailed,
    RemovalUnconfirmed,
    republished_headers,
    summarize,
    valid_message_id,
)

DLQ = ENTITLEMENT_ENDED_NAMES.dead_letter_queue


def tool(broker: FakeBroker) -> DeadLetterTool:
    return DeadLetterTool(PLATFORM_DEAD_LETTER_TOPOLOGIES, broker.session)


# --- the allow-list ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_platforms_own_dead_letter_queues_are_allowed() -> None:
    assert tool(FakeBroker()).queues == (
        "provisioning.command_job.v1.dlq",
        "provisioning.source_published.v1.dlq",
        "provisioning.project_deleted.v1.dlq",
        "provisioning.owner_status_changed.v1.dlq",
        "provisioning.hosting_entitlement_ended.v1.dlq",
        "provisioning.hosting_entitlement_restored.v1.dlq",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "queue",
    [
        ENTITLEMENT_ENDED_NAMES.queue,  # a work queue, not a dead-letter queue
        "payment.publish_completed.v1.dlq",  # Payment's own queue
        "amq.rabbitmq.trace",
        "provisioning.hosting_entitlement_ended.v1.dlq ",
        "",
    ],
)
async def test_any_other_queue_is_refused_before_the_broker_is_reached(queue: str) -> None:
    broker = FakeBroker()
    with pytest.raises(QueueNotAllowed):
        await tool(broker).list(queue, 10)
    with pytest.raises(QueueNotAllowed):
        await tool(broker).redrive(queue, "m-1", scan_limit=10)
    assert broker.sessions == 0
    assert broker.calls == []


@pytest.mark.asyncio
async def test_a_tool_with_no_allowed_queue_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="at least one"):
        DeadLetterTool((), FakeBroker().session)


# --- listing ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, MAX_LIST_LIMIT + 1])
async def test_a_listing_limit_outside_its_bounds_is_refused(limit: int) -> None:
    broker = FakeBroker()
    with pytest.raises(InvalidLimit):
        await tool(broker).list(DLQ, limit)
    assert broker.sessions == 0


@pytest.mark.asyncio
async def test_a_listing_reads_at_most_the_limit_and_settles_nothing() -> None:
    broker = FakeBroker()
    broker.park(DLQ, *(parked(f"m-{i}") for i in range(5)))
    listed = await tool(broker).list(DLQ, 3)
    assert [p.message_id for p in listed] == ["m-0", "m-1", "m-2"]
    assert [c[0] for c in broker.calls] == ["get", "get", "get", "release"]
    assert len(broker.queues[DLQ]) == 5
    assert broker.published == []


@pytest.mark.asyncio
async def test_a_listing_of_an_empty_queue_is_empty() -> None:
    broker = FakeBroker()
    assert await tool(broker).list(DLQ, 10) == ()
    assert broker.calls == [("get", DLQ, "-"), ("release",)]


@pytest.mark.asyncio
async def test_a_listing_shows_the_death_and_the_envelope_fields() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", deaths=[death(count=4, reason="rejected")]))
    (item,) = await tool(broker).list(DLQ, 10)
    assert item.queue == DLQ
    assert item.message_id == "m-1"
    assert item.death is not None
    assert (item.death.reason, item.death.count) == ("rejected", 4)
    assert (item.death.exchange, item.death.routing_key) == (
        ENTITLEMENT_ENDED_NAMES.exchange,
        "hosting_entitlement_ended.v1",
    )
    assert item.envelope.message_type == "payment.hosting_entitlement_ended.v1"
    assert item.envelope.event_key == "payment-event-1"
    assert item.envelope.project_id == PROJECT


@pytest.mark.asyncio
async def test_a_summary_never_carries_the_body() -> None:
    summary = summarize(ENTITLEMENT_ENDED_NAMES, parked())
    assert CONTENT_MARKER not in repr(summary)
    assert not hasattr(summary, "body")


@pytest.mark.asyncio
async def test_a_broker_that_cannot_be_reached_is_reported() -> None:
    broker = FakeBroker(open_fault=BrokerUnavailable("broker_unreachable"))
    with pytest.raises(BrokerUnavailable):
        await tool(broker).list(DLQ, 10)


# --- the x-death parse and the envelope -------------------------------------------------------


def test_the_death_of_the_source_queue_is_taken_whatever_its_position() -> None:
    other = death(PROJECT_DELETED_NAMES, reason="expired", count=9)
    mine = death(reason="delivery_limit", count=2)
    summary = summarize(ENTITLEMENT_ENDED_NAMES, parked(deaths=[other, mine]))
    assert summary.death is not None
    assert (summary.death.reason, summary.death.count) == ("delivery_limit", 2)


def test_byte_values_in_the_death_are_read_as_text() -> None:
    entry = death(
        reason=b"maxlen",
        exchange=ENTITLEMENT_ENDED_NAMES.exchange.encode(),
        routing_keys=[b"hosting_entitlement_ended.v1"],
        queue=ENTITLEMENT_ENDED_NAMES.queue.encode(),
    )
    summary = summarize(ENTITLEMENT_ENDED_NAMES, parked(deaths=[entry]))
    assert summary.death is not None
    assert summary.death.reason == "maxlen"
    assert summary.death.routing_key == "hosting_entitlement_ended.v1"
    assert summary.death.exchange == ENTITLEMENT_ENDED_NAMES.exchange


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-death": "not-a-list"},
        {"x-death": ["not-a-table"]},
        {"x-death": [death(PROJECT_DELETED_NAMES)]},  # dead-lettered from another queue only
    ],
)
def test_a_message_without_a_death_of_its_source_queue_has_no_death(
    headers: dict[str, object],
) -> None:
    assert summarize(ENTITLEMENT_ENDED_NAMES, parked(headers=headers)).death is None


def test_unexpected_death_values_are_not_shown() -> None:
    entry = death(
        reason="reason\nwith a line break", count="4", routing_keys="hosting_entitlement_ended.v1"
    )
    summary = summarize(ENTITLEMENT_ENDED_NAMES, parked(deaths=[entry]))
    assert summary.death is not None
    assert summary.death.reason is None
    assert summary.death.count is None
    assert summary.death.routing_key is None
    entry = death(count=True, routing_keys=[])
    summary = summarize(ENTITLEMENT_ENDED_NAMES, parked(deaths=[entry]))
    assert summary.death is not None
    assert summary.death.count is None
    assert summary.death.routing_key is None


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b'{"message_type": 7, "event_key": ["x"], "project_id": "not-a-uuid"}',
        b'{"message_type": "Bad Type!", "event_key": " padded ", "project_id": "'
        + b"00000000-0000-0000-0000-000000000000"
        + b'"}',
        b'{"event_key": "line\\nbreak"}',
        b"\xff\xfe",
        b"[" * 100_000,
    ],
)
def test_a_malformed_envelope_shows_no_field(body: bytes) -> None:
    envelope = summarize(ENTITLEMENT_ENDED_NAMES, parked(body=body)).envelope
    assert envelope.message_type is None
    assert envelope.event_key is None
    assert envelope.project_id is None


def test_an_oversized_body_is_not_parsed() -> None:
    body = json.dumps(
        {"message_type": "payment.hosting_entitlement_ended.v1", "pad": "x" * 70_000}
    ).encode()
    assert summarize(ENTITLEMENT_ENDED_NAMES, parked(body=body)).envelope.message_type is None


def test_the_key_of_each_envelope_kind() -> None:
    request = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02"
    command = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e03"
    release = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e04"
    source = json.dumps(
        {
            "message_type": "project.source_published.v1",
            "project_id": str(PROJECT),
            "generation_request_id": request,
        }
    ).encode()
    job = json.dumps(
        {
            "message_type": "provisioning.command_job",
            "project_id": str(PROJECT),
            "command_id": command,
            "release_id": release,
        }
    ).encode()
    deleted = json.dumps(
        {"message_type": "project.project_deleted.v1", "project_id": str(PROJECT)}
    ).encode()
    assert summarize(SOURCE_PUBLISHED_NAMES, parked(body=source)).envelope.event_key == request
    envelope = summarize(COMMAND_JOB_NAMES, parked(body=job)).envelope
    assert envelope.event_key == command
    assert envelope.command_id == UUID(command)
    assert envelope.release_id == UUID(release)
    assert summarize(PROJECT_DELETED_NAMES, parked(body=deleted)).envelope.event_key is None


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01", True),
        ("m-1", True),
        ("a" * 100, True),
        ("a" * 101, False),
        ("", False),
        ("with space", False),
        ("tab\t", False),
        ('quote"', False),
    ],
)
def test_a_message_id_is_short_plain_ascii(value: str, ok: bool) -> None:
    assert valid_message_id(value) is ok


def test_an_unusual_message_id_is_listed_as_absent() -> None:
    assert summarize(ENTITLEMENT_ENDED_NAMES, parked("has space")).message_id is None
    assert summarize(ENTITLEMENT_ENDED_NAMES, parked(None)).message_id is None


# --- the re-drive -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_redrive_publishes_to_its_work_queue_then_removes_the_parked_message() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-0"), parked("m-1"), parked("m-2"))
    done = await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert done.message_id == "m-1"
    assert broker.calls == [
        ("get", DLQ, "m-0"),
        ("get", DLQ, "m-1"),
        # The default exchange to Provisioning's own queue, never payment.events (fix round 1).
        ("publish", "", ENTITLEMENT_ENDED_NAMES.queue, "m-1"),
        ("confirmed", "m-1"),
        ("ack", "m-1"),
        ("release",),
    ]
    assert [m.message_id for m in broker.queues[DLQ]] == ["m-0", "m-2"]
    (exchange, key, copy) = broker.published[0]
    assert (exchange, key) == ("", ENTITLEMENT_ENDED_NAMES.queue)
    assert copy.body == entitlement_body()
    assert (copy.message_type, copy.correlation_id, copy.content_type) == (
        "payment.hosting_entitlement_ended.v1",
        "corr-1",
        "application/json",
    )
    assert copy.headers == {"trace": "keep-me"}


@pytest.mark.asyncio
async def test_a_redrive_moves_exactly_one_of_two_messages_with_the_same_id() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1"), parked("m-1"))
    await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert len(broker.published) == 1
    assert len(broker.queues[DLQ]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        PublishRefused("publish_nacked"),
        PublishRefused("publish_returned"),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
async def test_an_unconfirmed_publish_leaves_the_message_parked(fault: Exception) -> None:
    broker = FakeBroker(publish_fault=fault)
    broker.park(DLQ, parked("m-1"))
    with pytest.raises(RedrivePublishFailed) as caught:
        await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    expected = fault.code if isinstance(fault, PublishRefused) else "publish_unconfirmed"
    assert caught.value.code == expected
    assert caught.value.parked.message_id == "m-1"
    assert not any(c[0] == "ack" for c in broker.calls)
    assert broker.calls[-1] == ("release",)
    assert len(broker.queues[DLQ]) == 1


@pytest.mark.asyncio
async def test_a_removal_that_fails_after_the_confirm_is_reported_and_nothing_is_lost() -> None:
    broker = FakeBroker(ack_fault=ConnectionResetError())
    broker.park(DLQ, parked("m-1"))
    with pytest.raises(RemovalUnconfirmed) as caught:
        await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert caught.value.parked.message_id == "m-1"
    assert len(broker.published) == 1  # delivered to its queue
    assert len(broker.queues[DLQ]) == 1  # and still parked: at most a duplicate later


@pytest.mark.asyncio
async def test_a_message_beyond_the_scan_limit_is_not_found() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-0"), parked("m-1"), parked("m-2"))
    with pytest.raises(MessageNotFound):
        await tool(broker).redrive(DLQ, "m-2", scan_limit=2)
    with pytest.raises(MessageNotFound):
        await tool(broker).redrive(DLQ, "absent", scan_limit=10)
    assert broker.published == []
    assert not any(c[0] == "ack" for c in broker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, MAX_SCAN_LIMIT + 1])
async def test_a_scan_limit_outside_its_bounds_is_refused(limit: int) -> None:
    broker = FakeBroker()
    with pytest.raises(InvalidLimit):
        await tool(broker).redrive(DLQ, "m-1", scan_limit=limit)
    assert broker.sessions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deaths",
    [
        [],  # no death at all: put into the dead-letter exchange by hand
        [death(PROJECT_DELETED_NAMES)],  # died in another queue
        [death(exchange="amq.direct")],  # an exchange that is not the topology's
        [death(routing_keys=["project_deleted"])],  # a key the queue is not bound to
        [death(routing_keys=[])],
    ],
)
async def test_a_message_without_an_allowed_origin_is_refused(
    deaths: list[dict[str, object]],
) -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", deaths=deaths))
    with pytest.raises(NoOrigin) as caught:
        await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert caught.value.parked.message_id == "m-1"
    assert broker.published == []
    assert len(broker.queues[DLQ]) == 1


@pytest.mark.asyncio
async def test_an_invalid_message_id_is_refused_before_the_broker_is_reached() -> None:
    broker = FakeBroker()
    with pytest.raises(ValueError, match="message ID"):
        await tool(broker).redrive(DLQ, "has space", scan_limit=10)
    assert broker.sessions == 0


def test_the_republished_headers_drop_only_the_brokers_death_records() -> None:
    headers = parked().headers
    assert republished_headers(headers) == {"trace": "keep-me"}
    assert republished_headers({}) == {}
