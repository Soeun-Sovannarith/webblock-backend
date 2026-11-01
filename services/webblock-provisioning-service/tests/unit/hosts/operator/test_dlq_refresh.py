"""The refresh of an often-taken parked message (task W6-E2, found in the slot on RabbitMQ 4.3.6):
every return of a fetched quorum-queue message counts towards the delivery limit (20 by default),
and a dead-letter queue, which has no dead-letter exchange, then drops it. So a message taken
``REFRESH_AT`` times or more is copied back into its dead-letter queue with a confirm, its
``x-death`` kept and the broker's counters removed, and only then is the original acknowledged."""

from __future__ import annotations

import pytest
from tests.unit.hosts.operator._dlq_fakes import FakeBroker, death, parked

from provisioning.infrastructure.rabbitmq import ENTITLEMENT_ENDED_NAMES
from provisioning.infrastructure.rabbitmq.dlq import (
    PLATFORM_DEAD_LETTER_TOPOLOGIES,
    REFRESH_AT,
    DeadLetterTool,
    NoOrigin,
    PublishRefused,
    RedrivePublishFailed,
    refreshed_headers,
    times_taken,
)

DLQ = ENTITLEMENT_ENDED_NAMES.dead_letter_queue


def tool(broker: FakeBroker) -> DeadLetterTool:
    return DeadLetterTool(PLATFORM_DEAD_LETTER_TOPOLOGIES, broker.session)


def test_the_refresh_starts_at_half_the_default_delivery_limit() -> None:
    assert REFRESH_AT == 10


@pytest.mark.parametrize(
    ("headers", "taken"),
    [
        ({}, 0),
        ({"x-delivery-count": 3}, 3),
        ({"x-delivery-count": 3, "x-acquired-count": 7}, 7),
        ({"x-delivery-count": True}, 0),
        ({"x-delivery-count": "12"}, 0),
    ],
)
def test_times_taken_is_the_larger_counter(headers: dict[str, object], taken: int) -> None:
    assert times_taken(headers) == taken


def test_a_refreshed_copy_keeps_the_death_record_and_drops_the_counters() -> None:
    headers = {**parked(taken=12).headers, "x-acquired-count": 11}
    kept = refreshed_headers(headers)
    assert "x-delivery-count" not in kept and "x-acquired-count" not in kept
    assert kept["x-death"] == headers["x-death"]
    assert kept["trace"] == "keep-me"


@pytest.mark.asyncio
async def test_a_message_taken_often_is_copied_back_then_removed_and_listed_once() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", taken=REFRESH_AT))
    listed = await tool(broker).list(DLQ, 10)
    assert [p.message_id for p in listed] == ["m-1"]
    assert broker.calls == [
        ("get", DLQ, "m-1"),
        ("publish", "", DLQ, "m-1"),
        ("confirmed", "m-1"),
        ("ack", "m-1"),
        ("get", DLQ, "m-1"),  # its own copy at the tail: the listing stops there
        ("release",),
    ]
    (copy,) = broker.queues[DLQ]
    assert "x-delivery-count" not in copy.headers
    assert copy.headers["x-death"] == [death()]
    assert copy.body == parked().body


@pytest.mark.asyncio
async def test_a_message_taken_less_often_is_only_returned() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", taken=REFRESH_AT - 1))
    await tool(broker).list(DLQ, 10)
    assert [c[0] for c in broker.calls] == ["get", "get", "release"]
    assert broker.published == []


@pytest.mark.asyncio
async def test_a_refresh_that_is_not_confirmed_leaves_the_original_parked() -> None:
    broker = FakeBroker(publish_fault=PublishRefused("publish_nacked"))
    broker.park(DLQ, parked("m-1", taken=15), parked("m-2"))
    listed = await tool(broker).list(DLQ, 10)
    assert [p.message_id for p in listed] == ["m-1", "m-2"]
    assert not any(c[0] == "ack" for c in broker.calls)
    assert [m.message_id for m in broker.queues[DLQ]] == ["m-1", "m-2"]


@pytest.mark.asyncio
async def test_a_redrive_search_refreshes_the_often_taken_messages_it_passes() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-0", taken=REFRESH_AT), parked("m-1", taken=REFRESH_AT))
    await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    # m-0 refreshed (copied to its own queue), m-1 moved to its work queue, not refreshed.
    assert [(e, k, m.message_id) for e, k, m in broker.published] == [
        ("", DLQ, "m-0"),
        ("", ENTITLEMENT_ENDED_NAMES.queue, "m-1"),
    ]
    assert [m.message_id for m in broker.queues[DLQ]] == ["m-0"]
    assert "x-delivery-count" not in broker.queues[DLQ][0].headers


@pytest.mark.asyncio
async def test_a_redrive_stops_at_its_own_copy() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-0", taken=REFRESH_AT))
    with pytest.raises(Exception, match="no parked message"):
        await tool(broker).redrive(DLQ, "absent", scan_limit=10)
    assert [c for c in broker.calls if c[0] == "get"] == [("get", DLQ, "m-0"), ("get", DLQ, "m-0")]
    assert len(broker.queues[DLQ]) == 1


@pytest.mark.asyncio
async def test_an_often_taken_target_that_cannot_move_is_refreshed() -> None:
    broker = FakeBroker()
    broker.park(DLQ, parked("m-1", deaths=[], taken=REFRESH_AT))
    with pytest.raises(NoOrigin):
        await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert [(e, k) for e, k, _ in broker.published] == [("", DLQ)]
    assert len(broker.queues[DLQ]) == 1


@pytest.mark.asyncio
async def test_an_often_taken_target_whose_copy_is_returned_stays_parked() -> None:
    broker = FakeBroker(publish_fault=PublishRefused("publish_returned"))
    broker.park(DLQ, parked("m-1", taken=REFRESH_AT))
    with pytest.raises(RedrivePublishFailed):
        await tool(broker).redrive(DLQ, "m-1", scan_limit=10)
    assert not any(c[0] == "ack" for c in broker.calls)
    assert len(broker.queues[DLQ]) == 1
