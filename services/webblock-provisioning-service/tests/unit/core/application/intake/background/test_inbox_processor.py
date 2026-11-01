"""The inbox processor (decision 0008 section 2, decision 0014 sections 3 and 10.5),
with in-memory fakes: it runs the release intake's ``process_batch`` with its
configuration, logs each row by ID, wakes at once when asked and never runs two batches at once.
Split out of the inbox consumer's tests with the module (decision 0025 FL7).
"""

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from tests.unit._loop_fakes import MS, PROJECT, eventually, logger_with_handler

from provisioning.core.application import (
    INTAKE_REASON_SUBDOMAIN_TAKEN,
    DependencyNotConfigured,
    InboxBatchParams,
    InboxBatchResult,
    InboxRowOutcome,
    InboxRowResult,
    InboxTake,
    SubdomainTaken,
    Timeout,
)
from provisioning.core.application.intake.background.inbox_processor import (
    InboxProcessor,
    InboxProcessorConfig,
)
from provisioning.core.application.shared.backoff import Backoff, InvalidConfig
from provisioning.core.domain import SourcePublishedFields

pytestmark = pytest.mark.asyncio

REQUEST = UUID("0199c4a1-0000-7000-8000-0000000000a1")
SHA = "a" * 40
FIELDS = SourcePublishedFields(
    project_id=str(PROJECT),
    generation_request_id=str(REQUEST),
    repository_id=42,
    git_commit_sha=SHA,
)


def processor_config(**changes: Any) -> InboxProcessorConfig:
    base = InboxProcessorConfig(
        poll_interval=timedelta(seconds=30),
        batch_size=10,
        max_per_project=2,
        visibility=timedelta(seconds=10),
        concurrency=5,
        registry_timeout=timedelta(seconds=1),
        transaction_timeout=timedelta(seconds=1),
        max_attempts=5,
        deadline=timedelta(hours=1),
        backoff=Backoff(base=timedelta(seconds=1), max=timedelta(seconds=8)),
    )
    return dataclasses.replace(base, **changes)


def take(n: int) -> InboxTake:
    return InboxTake(project_id=PROJECT, generation_request_id=UUID(int=n), attempts=1)


class FakeBatches:
    def __init__(self) -> None:
        self.params: list[InboxBatchParams] = []
        self.results: list[InboxBatchResult | Exception] = []
        self.delay = 0.0
        self.in_flight = 0
        self.peak = 0

    async def process_batch(self, params: InboxBatchParams) -> InboxBatchResult:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            self.params.append(params)
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.results:
                got = self.results.pop(0)
                if isinstance(got, Exception):
                    raise got
                return got
            return InboxBatchResult(now=datetime(2026, 10, 8, tzinfo=UTC), rows=())
        finally:
            self.in_flight -= 1


def processor(
    cfg: InboxProcessorConfig | None = None,
) -> tuple[InboxProcessor, FakeBatches, list[str]]:
    batches = FakeBatches()
    logger, handler = logger_with_handler("inbox-processor")
    return (
        InboxProcessor(cfg or processor_config(), intake=batches, logger=logger),
        batches,
        handler.lines,
    )


async def test_poll_passes_the_configuration_and_logs_each_row_by_id() -> None:
    p, batches, lines = processor()
    release = UUID(int=99)
    batches.results = [
        InboxBatchResult(
            now=datetime(2026, 10, 8, tzinfo=UTC),
            rows=(
                InboxRowResult(
                    take=take(1), outcome=InboxRowOutcome.ACCEPTED, release_id=release, created=True
                ),
                InboxRowResult(
                    take=take(2), outcome=InboxRowOutcome.REJECTED, code="repository_mismatch"
                ),
                InboxRowResult(
                    take=take(3),
                    outcome=InboxRowOutcome.RETRIED,
                    reason="registry_timeout",
                    retry_after=timedelta(seconds=2),
                ),
                InboxRowResult(
                    take=take(4), outcome=InboxRowOutcome.ERROR, error=ConnectionError("pw=hunter2")
                ),
            ),
        )
    ]
    await p.poll()
    [params] = batches.params
    assert params.take.batch_size == 10 and params.take.max_per_project == 2
    assert params.take.visibility == timedelta(seconds=10)
    assert params.concurrency == 5 and params.max_attempts == 5
    assert timedelta(seconds=0) < params.retry_delay(1) <= timedelta(seconds=1)
    text = "\n".join(lines)
    assert str(release) in text and "repository_mismatch" in text and "registry_timeout" in text
    assert "hunter2" not in text
    assert all(str(UUID(int=n)) in text for n in (1, 2, 3, 4))


async def test_l2_a_taken_subdomain_that_spent_the_budget_is_logged_at_error() -> None:
    """Task 12 security re-review L2 (FR-54.5, TR-53): the row a taken sub-domain failed
    after its budget is one ERROR line with its IDs, code, reason and the error's type, never
    the error's text; a repository-unconfirmed failure stays a warning."""
    p, batches, lines = processor()
    batches.results = [
        InboxBatchResult(
            now=datetime(2026, 10, 8, tzinfo=UTC),
            rows=(
                InboxRowResult(
                    take=take(1),
                    outcome=InboxRowOutcome.FAILED,
                    code="repository_unconfirmed",
                    reason=INTAKE_REASON_SUBDOMAIN_TAKEN,
                    error=SubdomainTaken("victim-shop"),
                ),
                InboxRowResult(
                    take=take(2),
                    outcome=InboxRowOutcome.FAILED,
                    code="repository_unconfirmed",
                    reason="registry_timeout",
                ),
            ),
        )
    ]
    await p.poll()
    errors = [line for line in lines if line.startswith("ERROR ")]
    assert len(errors) == 1
    assert str(UUID(int=1)) in errors[0]
    assert "subdomain_taken" in errors[0] and "SubdomainTaken" in errors[0]
    assert "repository_unconfirmed" in errors[0]
    assert "victim-shop" not in "\n".join(lines)
    warnings = [line for line in lines if line.startswith("WARNING ")]
    assert len(warnings) == 1 and str(UUID(int=2)) in warnings[0]


async def test_wake_runs_the_next_batch_at_once_and_batches_never_overlap() -> None:
    p, batches, _ = processor()
    batches.delay = 0.01
    task = asyncio.create_task(p.run())
    await eventually(lambda: len(batches.params) == 1)
    for _ in range(5):
        p.wake()
        await asyncio.sleep(0.002)
    await eventually(lambda: len(batches.params) >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert batches.peak == 1


async def test_a_failed_batch_is_logged_and_the_next_one_runs() -> None:
    p, batches, lines = processor(processor_config(poll_interval=5 * MS))
    batches.results = [Timeout("take"), ConnectionError("db")]
    task = asyncio.create_task(p.run())
    await eventually(lambda: len(batches.params) >= 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sum("inbox batch failed" in line for line in lines) == 2


async def test_an_unconfigured_registry_stops_the_processor() -> None:
    p, batches, lines = processor(processor_config(poll_interval=5 * MS))
    batches.results = [DependencyNotConfigured("OD-01")]
    async with asyncio.timeout(1):
        with pytest.raises(DependencyNotConfigured):
            await p.run()
    assert len(batches.params) == 1
    assert any(line.startswith("ERROR") for line in lines)


@pytest.mark.parametrize(
    "changes",
    [
        {"poll_interval": timedelta(0)},
        {"batch_size": 0},
        {"batch_size": 1001},
        {"max_per_project": 11},
        {"concurrency": 11},
        {"visibility": timedelta(seconds=1)},
        {"max_attempts": 0},
        {"deadline": timedelta(0)},
        {"backoff": Backoff(base=timedelta(0), max=MS)},
    ],
)
async def test_the_processor_config_refuses_bad_values(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidConfig):
        processor_config(**changes).validate()
    with pytest.raises(InvalidConfig):
        processor(processor_config(**changes))
