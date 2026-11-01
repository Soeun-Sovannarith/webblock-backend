"""The hosting retry sweep (QA-03; task W6-E): every interval it asks the hosting retry for the
failed suspends and resumes and logs each result; a failed sweep does not end the loop, and its
settings are checked. Split out of the hosting event consumers' tests with the module (decision
0025 FL7).
"""

import asyncio
from datetime import UTC, datetime

import pytest
from tests.unit._loop_fakes import MS, PROJECT, eventually, logger_with_handler

from provisioning.core.application.hosting.background.retry import RetryOutcome, RetryResult
from provisioning.core.application.hosting.background.retry_sweeper import (
    HostingRetrySweeper,
    RetrySweepConfig,
)
from provisioning.core.application.shared.audit import AuditEvent
from provisioning.core.application.shared.backoff import InvalidConfig

pytestmark = pytest.mark.asyncio


class Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("no alert expected")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 9, 12, tzinfo=UTC)


AUDITS: list[Audit] = []


def audit() -> Audit:
    """A fresh audit record, also kept as the last one built (``last_audit``)."""
    AUDITS.append(Audit())
    return AUDITS[-1]


def last_audit() -> Audit:
    return AUDITS[-1]


class FakeSweeps:
    def __init__(self, results: tuple[RetryResult, ...] = ()) -> None:
        self.results = results
        self.faults: list[Exception] = []
        self.limits: list[int] = []

    async def sweep(self, limit: int) -> tuple[RetryResult, ...]:
        self.limits.append(limit)
        if self.faults:
            raise self.faults.pop(0)
        return self.results


async def test_the_sweep_retries_and_logs_each_project() -> None:
    sweeps = FakeSweeps(
        (
            RetryResult(PROJECT, RetryOutcome.RETRIED, "pay-1.retry-2"),
            RetryResult(PROJECT, RetryOutcome.BUDGET_SPENT, "pay-9.retry-3"),
        )
    )
    logger, handler = logger_with_handler("sweep")
    sweeper = HostingRetrySweeper(
        RetrySweepConfig(interval=MS, batch_size=7),
        retries=sweeps,
        audit=audit(),
        clock=Clock(),
        logger=logger,
    )
    results = await sweeper.sweep()
    assert [r.outcome for r in results] == [RetryOutcome.RETRIED, RetryOutcome.BUDGET_SPENT]
    assert sweeps.limits == [7]
    assert any("retried" in line and "pay-1.retry-2" in line for line in handler.lines)
    assert any(line.startswith("ERROR") and "pay-9.retry-3" in line for line in handler.lines)


async def test_a_failed_sweep_does_not_end_the_loop() -> None:
    sweeps = FakeSweeps()
    sweeps.faults = [ConnectionError("db")]
    logger, handler = logger_with_handler("sweep-loop")
    sweeper = HostingRetrySweeper(
        RetrySweepConfig(interval=MS, batch_size=1),
        retries=sweeps,
        audit=audit(),
        clock=Clock(),
        logger=logger,
    )
    task = asyncio.create_task(sweeper.run())
    await eventually(lambda: len(sweeps.limits) >= 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("sweep failed" in line and "ConnectionError" in line for line in handler.lines)


@pytest.mark.parametrize(
    "config",
    [
        RetrySweepConfig(interval=0 * MS, batch_size=1),
        RetrySweepConfig(interval=MS, batch_size=0),
        RetrySweepConfig(interval=MS, batch_size=1001),
    ],
)
async def test_the_sweep_settings_are_checked(config: RetrySweepConfig) -> None:
    with pytest.raises(InvalidConfig):
        HostingRetrySweeper(
            config,
            retries=FakeSweeps(),
            audit=audit(),
            clock=Clock(),
            logger=logger_with_handler("x")[0],
        )
