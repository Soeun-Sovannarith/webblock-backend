"""The per-message failure budget (Task 4 fix round 1; decision 0023 G15): RabbitMQ 4 does not
count a nack with requeue towards the delivery limit, so a consumer counts each message's failures
and dead-letters it at the fifth. A database outage counts too; only contention and the
per-project cap do not."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

import pytest
from tests.unit._loop_fakes import FakeDelivery, FakeStream, logger_with_handler

from provisioning.core.application import (
    Contention,
    DependencyUnavailable,
    StoreUnreachable,
    Timeout,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.presentation.messaging.consumers.messages import (
    MAX_COUNTED_FAILURES,
    Action,
    Delivery,
    Failure,
    MessageFailures,
    StoreFailures,
    failure_of,
    message_key,
    rejected_fields,
    serve,
    settle,
)

HOLD = Backoff(base=timedelta(microseconds=1), max=timedelta(microseconds=2))
MID = "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff"


def failures() -> StoreFailures:
    return StoreFailures(hold=HOLD, limit=1000)


async def counted(f: StoreFailures, d: FakeDelivery, log: logging.Logger) -> None:
    action = await f.hold_then_requeue(Failure.COUNTED)
    assert action is Action.REQUEUE_COUNTED
    await f.settle(d, action, log, "k=v")


@pytest.mark.asyncio
async def test_the_fifth_counted_failure_of_a_message_dead_letters_it() -> None:
    f = failures()
    log, handler = logger_with_handler("budget")
    body = b'{"secret-body": 1}'
    deliveries = [FakeDelivery(body, message_id=MID) for _ in range(MAX_COUNTED_FAILURES)]
    for d in deliveries:
        await counted(f, d, log)
    assert MAX_COUNTED_FAILURES == 5
    assert [d.actions for d in deliveries] == [["requeue"]] * 4 + [["dead_letter"]]
    text = "\n".join(handler.lines)
    assert f"message_id={MID} size={len(body)} sha256={hashlib.sha256(body).hexdigest()}" in text
    assert "secret-body" not in text


@pytest.mark.asyncio
async def test_contention_does_not_count() -> None:
    """Contention and the per-project cap are waits (``Failure.WAIT``), not failures."""
    assert failure_of(Contention("lock timeout")) is Failure.WAIT
    f = failures()
    log, _ = logger_with_handler("budget-free")
    for _ in range(3 * MAX_COUNTED_FAILURES):
        d = FakeDelivery(message_id=MID)
        action = await f.hold_then_requeue(Failure.WAIT)
        assert action is Action.REQUEUE
        await f.settle(d, action, log, "")
        assert d.actions == ["requeue"]


@pytest.mark.asyncio
async def test_a_settled_message_starts_again_and_other_messages_count_apart() -> None:
    f = failures()
    log, _ = logger_with_handler("budget-apart")
    for _ in range(MAX_COUNTED_FAILURES - 1):
        await counted(f, FakeDelivery(message_id=MID), log)
    await f.settle(FakeDelivery(message_id=MID), Action.ACK, log, "")
    other = FakeDelivery(message_id="0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e00")
    await counted(f, other, log)
    again = FakeDelivery(message_id=MID)
    await counted(f, again, log)
    assert other.actions == ["requeue"] and again.actions == ["requeue"]


def test_the_counter_is_bounded_and_keys_a_message_without_id_by_its_body() -> None:
    counter = MessageFailures(limit=5, capacity=3)
    for i in range(10):
        assert not counter.failed(FakeDelivery(b"%d" % i))
    assert len(counter) == 3
    assert message_key(FakeDelivery(b"x")) == "sha256:" + hashlib.sha256(b"x").hexdigest()
    assert message_key(FakeDelivery(message_id=MID)) == "id:" + MID
    with pytest.raises(InvalidConfig):
        MessageFailures(limit=0)


def test_rejected_fields_never_carry_the_body_or_an_unsafe_id() -> None:
    line = rejected_fields(FakeDelivery(b"body-marker", message_id="evil\nline"))
    assert "body-marker" not in line and "evil" not in line
    assert line.startswith("message_id=invalid size=11 sha256=")


@pytest.mark.asyncio
async def test_serve_handles_and_settles_each_delivery_inside_its_consumer_span() -> None:
    stream = FakeStream()
    deliveries = [FakeDelivery(message_id=MID), FakeDelivery()]
    for d in deliveries:
        stream.put(d)
    stream.close()

    async def handle(d: Delivery) -> bool:
        await settle(d, Action.ACK, logging.getLogger("t"), "k=v")
        return False

    await serve(stream, 2, handle)
    assert [d.spans for d in deliveries] == [["start", "settled", "end"]] * 2


def _caused_by(cause: Exception) -> Exception:
    error = RuntimeError("wrapper")
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (StoreUnreachable("x"), Failure.OUTAGE),
        (Timeout("x"), Failure.OUTAGE),
        (_caused_by(StoreUnreachable("x")), Failure.OUTAGE),
        (_caused_by(Timeout("x")), Failure.OUTAGE),
        (Contention("x"), Failure.WAIT),
        (_caused_by(Contention("x")), Failure.WAIT),
        (RuntimeError("x"), Failure.COUNTED),
        (ConnectionError("x"), Failure.COUNTED),
        (DependencyUnavailable("x"), Failure.COUNTED),
    ],
)
def test_failure_of_sorts_each_error(error: Exception, failure: Failure) -> None:
    assert failure_of(error) is failure


@pytest.mark.asyncio
async def test_a_database_outage_counts_toward_the_bound() -> None:
    """Decision 0023 G15: an outage counts towards the message's 5 tries, then its ``.dlq``."""
    f = StoreFailures(hold=HOLD, limit=2)
    log, handler = logger_with_handler("budget-outage")
    deliveries = [FakeDelivery(message_id=MID) for _ in range(MAX_COUNTED_FAILURES)]
    for d in deliveries:
        action = await f.hold_then_requeue(failure_of(StoreUnreachable("db down")))
        assert action is Action.REQUEUE_COUNTED
        await f.settle(d, action, log, "k=v")
        assert not f.take_restart()  # an outage never restarts the stream
    assert [d.actions for d in deliveries] == [["requeue"]] * 4 + [["dead_letter"]]
    assert f"message_id={MID}" in "\n".join(handler.lines)


@pytest.mark.asyncio
async def test_an_outage_never_restarts_and_its_hold_grows_to_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holds: list[float] = []

    async def sleep(seconds: float) -> None:
        holds.append(seconds)

    monkeypatch.setattr(
        "provisioning.presentation.messaging.consumers.messages.asyncio.sleep", sleep
    )
    ms = timedelta(milliseconds=1)
    f = StoreFailures(hold=Backoff(base=ms, max=4 * ms), limit=2)
    for _ in range(12):
        assert await f.hold_then_requeue(Failure.OUTAGE) is Action.REQUEUE_COUNTED
        assert not f.take_restart()
    # Each hold is in (d/2, d] for d = 1, 2, 4, 4, ... ms (the jitter of Backoff).
    assert 0.0005 < holds[0] <= 0.001 < holds[2] <= 0.004
    assert all(0.002 < h <= 0.004 for h in holds[2:])
    f.reset()
    holds.clear()
    await f.hold_then_requeue(Failure.WAIT)
    assert 0.0005 < holds[0] <= 0.001
    # Handler failures still restart the stream at the limit.
    await f.hold_then_requeue(Failure.COUNTED)
    await f.hold_then_requeue(Failure.COUNTED)
    assert f.take_restart()
