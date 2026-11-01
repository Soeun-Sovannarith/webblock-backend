"""DeployExecutor: the Executor for deploy commands, a thin adapter from a Claim to the deploy
use case (decision 0014 section 3)."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application.deploys.background.deploy_executor import (
    AsyncioPauser,
    DeployExecutor,
)
from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    ClaimLost,
    Executor,
    PermanentFailure,
    RetryLater,
    WorkRef,
)
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.domain import CommandKind

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
DEADLINE = T0 + timedelta(minutes=30)


def _claim(kind: CommandKind = CommandKind.DEPLOY) -> Claim:
    hosting = kind in (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)
    return Claim(
        ref=WorkRef(
            command_id=COMMAND,
            project_id=PROJECT,
            kind=kind,
            command_number=7,
            release_id=None if hosting else RELEASE,
        ),
        claimed_by_worker="worker-1",
        claim_number=3,
        execution_attempt=1,
        expires_at=T0 + timedelta(minutes=1),
        deadline_at=DEADLINE,
    )


class _Runner:
    def __init__(self) -> None:
        self.runs: list[tuple[Fence, datetime]] = []
        self.abandons: list[tuple[Fence, str]] = []
        self.error: Exception | None = None

    async def run(self, fence: Fence, deadline_at: datetime) -> None:
        self.runs.append((fence, deadline_at))
        if self.error is not None:
            raise self.error

    async def abandon(self, fence: Fence, code: str) -> None:
        self.abandons.append((fence, code))


FENCE = Fence(project_id=PROJECT, command_id=COMMAND, command_number=7, claim_number=3)


def test_it_is_an_executor() -> None:
    executor: Executor = DeployExecutor(_Runner(), abandon_timeout=timedelta(seconds=30))
    assert executor is not None


@pytest.mark.asyncio
async def test_a_deploy_claim_runs_the_use_case_with_its_fence_and_deadline() -> None:
    runner = _Runner()
    await DeployExecutor(runner, abandon_timeout=timedelta(seconds=30)).execute(_claim())
    assert runner.runs == [(FENCE, DEADLINE)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        CommandKind.PROMOTE,
        CommandKind.ROLLBACK,
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
    ],
)
async def test_another_kind_is_refused_without_running(kind: CommandKind) -> None:
    runner = _Runner()
    with pytest.raises(PermanentFailure) as raised:
        await DeployExecutor(runner, abandon_timeout=timedelta(seconds=30)).execute(_claim(kind))
    assert raised.value.code == FAILURE_CODE_OPERATION_UNAVAILABLE
    assert runner.runs == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [PermanentFailure("release_not_deployable"), RetryLater("tenant_not_ready"), ClaimLost()],
)
async def test_the_use_case_result_reaches_the_consumer_unchanged(error: Exception) -> None:
    runner = _Runner()
    runner.error = error
    with pytest.raises(type(error)) as raised:
        await DeployExecutor(runner, abandon_timeout=timedelta(seconds=30)).execute(_claim())
    assert raised.value is error


@pytest.mark.asyncio
async def test_abandon_runs_for_deploy_claims_only() -> None:
    runner = _Runner()
    executor = DeployExecutor(runner, abandon_timeout=timedelta(seconds=30))
    # TR-57 (OQ-5 b): the deploy's abandon writes no failure outcome of its own.
    assert await executor.abandon(_claim(), "deadline_exceeded") is None
    assert await executor.abandon(_claim(CommandKind.PROMOTE), "deadline_exceeded") is None
    assert runner.abandons == [(FENCE, "deadline_exceeded")]
    assert executor.abandon_timeout == timedelta(seconds=30)


def test_the_abandon_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="abandon"):
        DeployExecutor(_Runner(), abandon_timeout=timedelta(0))


@pytest.mark.asyncio
async def test_the_pauser_waits_and_can_be_cancelled() -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    await AsyncioPauser().pause(timedelta(milliseconds=20))
    assert loop.time() - started >= 0.015
    task = asyncio.create_task(AsyncioPauser().pause(timedelta(hours=1)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
