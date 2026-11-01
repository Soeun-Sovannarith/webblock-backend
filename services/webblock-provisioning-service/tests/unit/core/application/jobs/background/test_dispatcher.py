"""The dispatcher (decision 0005 section 3), with in-memory fakes."""

import asyncio
import dataclasses
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.unit._loop_fakes import (
    MS,
    NOW,
    FakeJobStore,
    FakeWorkerStore,
    FixedClock,
    SequentialIds,
    eventually,
    logger_with_handler,
    ref,
)

from provisioning.core.application import ClaimLost, DueDispatch, WorkRef
from provisioning.core.application.jobs.background.dispatcher import (
    FAILURE_CODE_DEADLINE_EXCEEDED,
    Dispatcher,
    DispatcherConfig,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import CommandKind, IllegalTransition, StaleFence

pytestmark = pytest.mark.asyncio


def config(**changes: Any) -> DispatcherConfig:
    base = DispatcherConfig(
        poll_interval=5 * MS,
        batch_size=10,
        publish_concurrency=4,
        confirm_timeout=200 * MS,
        visibility_timeout=500 * MS,
        redelivery_interval=timedelta(seconds=30),
        backoff=Backoff(base=10 * MS, max=80 * MS),
    )
    return dataclasses.replace(base, **changes)


def due(
    n: int, *, attempts: int = 1, deadline_passed: bool = False, claims: int = 0
) -> DueDispatch:
    return DueDispatch(
        ref=ref(CommandKind.DEPLOY, command_id=UUID(int=n)),
        correlation_id=f"corr-{n}",
        publish_attempts=attempts,
        claim_number=claims,
        execution_attempts=claims,
        deadline_at=NOW,
        deadline_passed=deadline_passed,
    )


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[UUID, WorkRef, str, datetime]] = []
        self.fail_next: list[Exception] = []
        self.delay = 0.0
        self.in_flight = 0
        self.max_in_flight = 0

    async def publish_command_job(
        self, *, message_id: UUID, ref: WorkRef, correlation_id: str, occurred_at: datetime
    ) -> None:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail_next:
                raise self.fail_next.pop(0)
            self.published.append((message_id, ref, correlation_id, occurred_at))
        finally:
            self.in_flight -= 1


def build(
    jobs: FakeJobStore, *, cfg: DispatcherConfig | None = None, ids: SequentialIds | None = None
) -> tuple[Dispatcher, FakePublisher, FakeWorkerStore, list[str]]:
    publisher = FakePublisher()
    worker = FakeWorkerStore()
    logger, handler = logger_with_handler("dispatcher")
    d = Dispatcher(
        cfg or config(),
        jobs=jobs,
        worker=worker,
        publisher=publisher,
        ids=ids or SequentialIds(),
        clock=FixedClock(),
        logger=logger,
    )
    return d, publisher, worker, handler.lines


async def test_poll_publishes_each_due_row_and_records_the_broker_ack() -> None:
    """FR-15: poll publishes each due row and records the broker ack."""
    rows = [due(1), due(2), due(3)]
    jobs = FakeJobStore(due=[rows])
    d, publisher, worker, _ = build(jobs)
    await d.poll()
    assert sorted(r.command_id.int for _, r, _, _ in publisher.published) == [1, 2, 3]
    assert {c for _, _, c, _ in publisher.published} == {"corr-1", "corr-2", "corr-3"}
    assert all(at == NOW for _, _, _, at in publisher.published)
    assert sorted(c.int for c, _ in jobs.acks) == [1, 2, 3]
    assert {after for _, after in jobs.acks} == {timedelta(seconds=30)}
    assert jobs.takes == [(10, 500 * MS)]
    assert worker.calls == []


async def test_every_publish_gets_a_fresh_message_id() -> None:
    jobs = FakeJobStore(due=[[due(1), due(1)], [due(1)]])
    d, publisher, _, _ = build(jobs)
    await d.poll()
    await d.poll()
    ids = [m for m, _, _, _ in publisher.published]
    assert len(ids) == 3 and len(set(ids)) == 3


async def test_a_failed_publish_is_recorded_with_backoff_and_retried_on_a_later_poll() -> None:
    row = due(5, attempts=3)
    jobs = FakeJobStore(due=[[row], [dataclasses.replace(row, publish_attempts=4)]])
    d, publisher, _, lines = build(jobs)
    publisher.fail_next = [ConnectionError("nacked")]
    await d.poll()
    [(command, retry)] = jobs.publish_failures
    assert command == UUID(int=5)
    # attempt 3: min(80ms, 10ms * 4) = 40ms, jittered into (20ms, 40ms].
    assert 20 * MS < retry <= 40 * MS
    assert "record_broker_ack" not in jobs.names()
    assert publisher.published == []
    await d.poll()
    assert [r.command_id for _, r, _, _ in publisher.published] == [UUID(int=5)]
    assert jobs.names().count("record_broker_ack") == 1
    assert any("publish failed" in line for line in lines)


async def test_a_confirm_timeout_is_a_failure() -> None:
    jobs = FakeJobStore(due=[[due(1)]])
    d, publisher, _, _ = build(jobs, cfg=config(confirm_timeout=5 * MS, visibility_timeout=50 * MS))
    publisher.delay = 1.0
    async with asyncio.timeout(1):
        await d.poll()
    assert jobs.names() == ["take_due", "record_publish_failure"]


async def test_an_unclaimed_command_past_its_deadline_is_failed_not_published() -> None:
    """FR-15.2: an unclaimed command past its deadline is failed not published."""
    jobs = FakeJobStore(
        due=[[due(1, deadline_passed=True), due(2, deadline_passed=True, claims=1)]]
    )
    d, publisher, worker, _ = build(jobs)
    await d.poll()
    [(fence, code, at)] = worker.unclaimed
    assert worker.finishes == []
    assert fence == due(1, deadline_passed=True).fence()
    assert (code, at) == (FAILURE_CODE_DEADLINE_EXCEEDED, NOW)
    # A command claimed before is published: only the consumer fails it after a claim.
    assert [r.command_id for _, r, _, _ in publisher.published] == [UUID(int=2)]


@pytest.mark.parametrize("refusal", [ClaimLost("x"), StaleFence("x"), IllegalTransition("x")])
async def test_a_refused_deadline_failure_is_quiet(refusal: Exception) -> None:
    jobs = FakeJobStore(due=[[due(1, deadline_passed=True)]])
    d, publisher, worker, lines = build(jobs)
    worker.unclaimed_error = refusal
    await d.poll()
    assert publisher.published == []
    assert not any(line.startswith(("WARNING", "ERROR")) for line in lines)


async def test_a_failed_take_fails_the_poll() -> None:
    jobs = FakeJobStore(due=[ConnectionError("db down")])
    d, publisher, _, _ = build(jobs)
    with pytest.raises(ConnectionError):
        await d.poll()
    assert publisher.published == []


async def test_an_id_failure_is_recorded_as_a_publish_failure() -> None:
    ids = SequentialIds()
    ids.fail = True
    jobs = FakeJobStore(due=[[due(1)]])
    d, publisher, _, _ = build(jobs, ids=ids)
    await d.poll()
    assert jobs.names() == ["take_due", "record_publish_failure"]
    assert publisher.published == []


async def test_record_errors_are_logged_and_the_poll_goes_on() -> None:
    jobs = FakeJobStore(
        due=[[due(1), due(2)]],
        ack_error=ConnectionError("db"),
        failure_error=ConnectionError("db"),
    )
    d, publisher, _, lines = build(jobs)
    publisher.fail_next = [ConnectionError("nacked")]
    await d.poll()
    assert jobs.names().count("record_broker_ack") == 1
    assert jobs.names().count("record_publish_failure") == 1
    assert sum("visibility timeout" in line for line in lines) == 2


async def test_unfinished_commands_without_a_dispatch_row_are_reported() -> None:
    jobs = FakeJobStore(orphans=2)
    d, _, _, lines = build(jobs)
    await d.poll()
    assert sum(line.startswith("ERROR") and "count=2" in line for line in lines) == 1
    jobs.orphans = ConnectionError("db")
    await d.poll()
    assert any(line.startswith("WARNING") and "error=ConnectionError" in line for line in lines)
    assert sum(line.startswith("ERROR") for line in lines) == 1


async def test_publishes_are_bounded_and_the_poll_waits_for_the_batch() -> None:
    jobs = FakeJobStore(due=[[due(n) for n in range(1, 11)]])
    d, publisher, _, _ = build(jobs, cfg=config(publish_concurrency=3))
    publisher.delay = 0.01
    await d.poll()
    assert publisher.max_in_flight == 3
    assert len(publisher.published) == 10
    assert jobs.names().count("record_broker_ack") == 10


async def test_run_polls_until_cancelled() -> None:
    jobs = FakeJobStore(due=[ConnectionError("db"), [due(1)], [due(2)]])
    d, publisher, _, _ = build(jobs)
    task = asyncio.create_task(d.run())
    await eventually(lambda: len(publisher.published) == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    takes = jobs.names().count("take_due")
    await asyncio.sleep(0.02)
    assert jobs.names().count("take_due") == takes


@pytest.mark.parametrize(
    "changes",
    [
        {"poll_interval": timedelta(0)},
        {"batch_size": 0},
        {"batch_size": 1001},
        {"publish_concurrency": 0},
        {"publish_concurrency": 11},
        {"confirm_timeout": timedelta(0)},
        {"visibility_timeout": 200 * MS},
        {"redelivery_interval": timedelta(0)},
        {"backoff": Backoff(base=timedelta(0), max=MS)},
    ],
)
async def test_the_config_refuses_missing_and_inconsistent_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(InvalidConfig):
        config(**changes).validate()
    with pytest.raises(InvalidConfig):
        build(FakeJobStore(), cfg=config(**changes))
