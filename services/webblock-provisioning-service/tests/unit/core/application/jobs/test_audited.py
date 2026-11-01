"""The audited store wrappers of the worker (decision 0014 section 21 answer 3, section 23 item 5
and the lead's ruling on the uncertain-route alert): each command outcome a store writes is
recorded once, as the worker's, and a route left uncertain is also an alert. The wrappers return
and raise exactly what the store does."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from provisioning.core.application import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    ClaimResult,
    CommandFinish,
    DeployOutcome,
    DueDispatch,
    Fence,
    WorkRef,
)
from provisioning.core.application.deploys.ports import (
    CandidateAttempt,
    CandidateAttemptStart,
    DeployFacts,
)
from provisioning.core.application.jobs.audited import (
    AuditedDeployStore,
    AuditedJobStore,
    AuditedRouteCommandStore,
)
from provisioning.core.domain import (
    CandidateFailure,
    CandidateFailureEffect,
    CandidatePlanKind,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    Failure,
    HostingStatus,
    IllegalTransition,
    Outcome,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    RoutingStatus,
    new_command,
    route_uncertain,
)

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
FENCE = Fence(project_id=PROJECT, command_id=COMMAND, command_number=4, claim_number=2)


class _Clock:
    def now(self) -> datetime:
        return T0


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.alerts: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        self.alerts.append(event)


def _command(kind: CommandKind, status: CommandStatus, code: str | None = None) -> Command:
    command = new_command(
        command_id=COMMAND,
        request=CommandRequest(
            project_id=PROJECT,
            kind=kind,
            release_id=None if kind.is_hosting() else RELEASE,
            idempotency_key="k",
        ),
        requested_by_subject=None if kind.is_hosting() else "alice",
        command_number=4,
        now=T0,
    )
    return dataclasses.replace(command, status=status, failure_code=code)


def _hosting(routing: RoutingStatus) -> ProjectHosting:
    return ProjectHosting(
        project_id=PROJECT,
        last_command_number=4,
        routing_status=routing,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        subdomain="shop",
    )


def _release() -> Release:
    return Release(
        id=RELEASE,
        project_id=PROJECT,
        generation_request_id=UUID("0190a1b2-0000-7000-8000-0000000000d1"),
        repository_id=42,
        git_commit_sha="a" * 40,
        status=ReleaseStatus.BUILT,
        created_at=T0,
        updated_at=T0,
        container_image_digest="sha256:" + "b" * 64,
    )


class _Routes:
    def __init__(self, outcome: Outcome) -> None:
        self.outcome = outcome
        self.error: Exception | None = None

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        return OutcomeInput(hosting=self.outcome.hosting, command=self.outcome.command)

    async def apply_route_outcome(self, fence: Fence, decide: Any) -> Outcome:
        if self.error is not None:
            raise self.error
        return self.outcome


def _routes(outcome: Outcome) -> tuple[AuditedRouteCommandStore, _Routes, _Audit]:
    inner, audit = _Routes(outcome), _Audit()
    return AuditedRouteCommandStore(inner, audit, _Clock()), inner, audit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status", "code", "action", "result"),
    [
        (CommandKind.PROMOTE, CommandStatus.SUCCEEDED, None, AuditAction.PROMOTE, "succeeded"),
        (
            CommandKind.PROMOTE,
            CommandStatus.FAILED,
            "promotion_failed",
            AuditAction.PROMOTE,
            "failed",
        ),
        (CommandKind.ROLLBACK, CommandStatus.SUCCEEDED, None, AuditAction.ROLLBACK, "succeeded"),
        (
            CommandKind.SUSPEND_HOSTING,
            CommandStatus.SKIPPED,
            "entitlement_restored",
            AuditAction.SUSPEND_HOSTING,
            "skipped",
        ),
        (
            CommandKind.RESUME_HOSTING,
            CommandStatus.SUCCEEDED,
            None,
            AuditAction.RESUME_HOSTING,
            "succeeded",
        ),
    ],
)
async def test_a_route_outcome_that_ends_the_command_is_recorded(
    kind: CommandKind,
    status: CommandStatus,
    code: str | None,
    action: AuditAction,
    result: str,
) -> None:
    outcome = Outcome(hosting=_hosting(RoutingStatus.STABLE), command=_command(kind, status, code))
    store, _, audit = _routes(outcome)
    assert await store.apply_route_outcome(FENCE, lambda inp: outcome) is outcome
    (event,) = audit.events
    assert event == AuditEvent(
        actor_kind=AuditActorKind.SYSTEM,
        actor="worker",
        action=action,
        project_id=PROJECT,
        release_id=None if kind.is_hosting() else RELEASE,
        command_id=COMMAND,
        result=AuditResult(result),
        failure_code=code,
        at=T0,
        correlation_id=None,
    )
    assert audit.alerts == []


@pytest.mark.asyncio
async def test_an_unfinished_command_is_not_recorded() -> None:
    # start_route_change: the command keeps running with the route changing.
    outcome = Outcome(
        hosting=_hosting(RoutingStatus.CHANGING),
        command=_command(CommandKind.PROMOTE, CommandStatus.RUNNING),
    )
    store, _, audit = _routes(outcome)
    await store.apply_route_outcome(FENCE, lambda inp: outcome)
    assert audit.events == [] and audit.alerts == []


@pytest.mark.asyncio
async def test_a_route_left_uncertain_is_recorded_and_alerted() -> None:
    outcome = Outcome(
        hosting=_hosting(RoutingStatus.UNCERTAIN),
        command=_command(CommandKind.PROMOTE, CommandStatus.FAILED, "restore_unconfirmed"),
    )
    store, _, audit = _routes(outcome)
    await store.apply_route_outcome(FENCE, lambda inp: outcome)
    (event,) = audit.events
    (alert,) = audit.alerts
    assert event.result is AuditResult.FAILED
    assert alert.action is AuditAction.ROUTE_UNCERTAIN
    assert alert.result is AuditResult.FAILED
    assert alert.failure_code == "restore_unconfirmed"
    assert (alert.project_id, alert.command_id, alert.release_id) == (PROJECT, COMMAND, RELEASE)


@pytest.mark.asyncio
async def test_a_refused_write_records_nothing_and_raises_unchanged() -> None:
    outcome = Outcome(
        hosting=_hosting(RoutingStatus.STABLE),
        command=_command(CommandKind.PROMOTE, CommandStatus.SUCCEEDED),
    )
    store, inner, audit = _routes(outcome)
    inner.error = ClaimLost("x")
    with pytest.raises(ClaimLost):
        await store.apply_route_outcome(FENCE, lambda inp: outcome)
    assert audit.events == [] and audit.alerts == []
    # Reads pass through.
    assert (await store.load_route_command(FENCE)).command.id == COMMAND


# --- deploy ---------------------------------------------------------------------------------


class _Deploys:
    def __init__(self) -> None:
        self.attempt = CandidateAttempt(kind=CandidatePlanKind.START)
        self.failure: CandidateFailure | None = None

    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        return DeployFacts(
            hosting=_hosting(RoutingStatus.STABLE),
            command=_command(CommandKind.DEPLOY, CommandStatus.RUNNING),
            release=_release(),
        )

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        return None

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        return self.attempt

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        return (RELEASE,)

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        return self.failure


def _deploys() -> tuple[AuditedDeployStore, _Deploys, _Audit]:
    inner, audit = _Deploys(), _Audit()
    return AuditedDeployStore(inner, audit, _Clock()), inner, audit


@pytest.mark.asyncio
async def test_a_ready_candidate_is_a_succeeded_deploy() -> None:
    store, _, audit = _deploys()
    await store.load_deploy_facts(FENCE)
    assert await store.end_candidate_attempt(FENCE, RELEASE, None) is None
    (event,) = audit.events
    assert (event.action, event.result, event.release_id, event.failure_code) == (
        AuditAction.DEPLOY,
        AuditResult.SUCCEEDED,
        RELEASE,
        None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect", [CandidateFailureEffect.COMMAND_FAILS, CandidateFailureEffect.RELEASE_FAILS]
)
async def test_a_failed_candidate_that_ends_the_command_is_a_failed_deploy(
    effect: CandidateFailureEffect,
) -> None:
    store, inner, audit = _deploys()
    inner.failure = CandidateFailure(effect=effect, code="crash_loop")
    assert await store.end_candidate_attempt(FENCE, RELEASE, "crash_loop") == inner.failure
    (event,) = audit.events
    assert (event.result, event.failure_code) == (AuditResult.FAILED, "crash_loop")
    # Without load_deploy_facts in this process the release is not known.
    assert event.release_id is None


@pytest.mark.asyncio
async def test_a_retried_candidate_is_not_recorded() -> None:
    store, inner, audit = _deploys()
    inner.failure = CandidateFailure(effect=CandidateFailureEffect.RETRY, code="tenant_not_ready")
    await store.end_candidate_attempt(FENCE, RELEASE, "tenant_not_ready")
    assert audit.events == []


@pytest.mark.asyncio
async def test_exhausted_attempts_are_a_failed_deploy() -> None:
    store, inner, audit = _deploys()
    inner.attempt = CandidateAttempt(kind=CandidatePlanKind.EXHAUSTED)
    start = CandidateAttemptStart(step_id=RELEASE, timeout=timedelta(seconds=1))
    assert await store.begin_candidate_attempt(FENCE, start) is inner.attempt
    (event,) = audit.events
    assert (event.result, event.failure_code) == (
        AuditResult.FAILED,
        "candidate_attempts_exhausted",
    )
    inner.attempt = CandidateAttempt(kind=CandidatePlanKind.START)
    await store.begin_candidate_attempt(FENCE, start)
    assert len(audit.events) == 1
    assert await store.return_replaced_candidates(FENCE) == (RELEASE,)
    await store.renew_claim(FENCE, timedelta(seconds=1))


# --- the dispatch row's store ---------------------------------------------------------------


REF = WorkRef(
    command_id=COMMAND,
    project_id=PROJECT,
    kind=CommandKind.PROMOTE,
    command_number=4,
    release_id=RELEASE,
)


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]:
        self.calls.append("take_due")
        return [
            DueDispatch(
                ref=REF,
                correlation_id="corr-9",
                publish_attempts=0,
                claim_number=2,
                execution_attempts=0,
                deadline_at=T0,
                deadline_passed=True,
            )
        ]

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        self.calls.append("record_broker_ack")
        return True

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool:
        self.calls.append("record_publish_failure")
        return True

    async def count_unfinished_without_dispatch(self) -> int:
        self.calls.append("count")
        return 3

    async def claim(self, request: ClaimRequest) -> ClaimResult:
        self.calls.append("claim")
        return ClaimResult(
            outcome=ClaimOutcome.CLAIMED,
            claim=Claim(
                ref=request.ref,
                claimed_by_worker="w",
                claim_number=2,
                execution_attempt=1,
                expires_at=T0,
                deadline_at=T0,
            ),
        )

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim:
        self.calls.append("extend_claim")
        return claim

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        self.calls.append("release_claim")

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None:
        self.calls.append("finish_command")

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        self.calls.append("fail_unclaimed_command")

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None:
        self.calls.append("apply_deploy_outcome")


def _jobs() -> tuple[AuditedJobStore, _Jobs, _Audit]:
    inner, audit = _Jobs(), _Audit()
    return AuditedJobStore(inner, audit, _Clock()), inner, audit


@pytest.mark.asyncio
async def test_a_claimed_command_failed_by_the_consumer_is_recorded_with_its_kind() -> None:
    store, inner, audit = _jobs()
    await store.claim(ClaimRequest(ref=REF, claimed_by_worker="w", ttl=timedelta(seconds=5)))
    await store.finish_command(
        FENCE,
        CommandFinish(status=CommandStatus.FAILED, failure_code="deadline_exceeded", at=T0),
    )
    (event,) = audit.events
    assert (event.action, event.result, event.failure_code, event.release_id) == (
        AuditAction.PROMOTE,
        AuditResult.FAILED,
        "deadline_exceeded",
        RELEASE,
    )
    assert inner.calls == ["claim", "finish_command"]


@pytest.mark.asyncio
async def test_an_unclaimed_command_failed_by_the_dispatcher_is_recorded() -> None:
    store, _, audit = _jobs()
    (due,) = await store.take_due(10, timedelta(seconds=5))
    await store.fail_unclaimed_command(due.fence(), "deadline_exceeded", T0)
    (event,) = audit.events
    assert (event.action, event.result, event.correlation_id) == (
        AuditAction.PROMOTE,
        AuditResult.FAILED,
        "corr-9",
    )


@pytest.mark.asyncio
async def test_a_refused_finish_records_nothing() -> None:
    store, inner, audit = _jobs()
    await store.claim(ClaimRequest(ref=REF, claimed_by_worker="w", ttl=timedelta(seconds=5)))

    async def refuse(fence: Fence, finish: CommandFinish) -> None:
        raise ClaimLost("x")

    inner.finish_command = refuse  # type: ignore[method-assign]  # a store that refuses
    with pytest.raises(ClaimLost):
        await store.finish_command(
            FENCE, CommandFinish(status=CommandStatus.FAILED, failure_code="x", at=T0)
        )
    assert audit.events == []


@pytest.mark.asyncio
async def test_the_other_job_store_calls_pass_through() -> None:
    store, inner, audit = _jobs()
    (due,) = await store.take_due(1, timedelta(seconds=1))
    assert await store.record_broker_ack(due, timedelta(seconds=1))
    assert await store.record_publish_failure(due, timedelta(seconds=1))
    assert await store.count_unfinished_without_dispatch() == 3
    result = await store.claim(
        ClaimRequest(ref=REF, claimed_by_worker="w", ttl=timedelta(seconds=5))
    )
    assert result.claim is not None
    await store.extend_claim(result.claim, timedelta(seconds=1))
    await store.release_claim(result.claim, timedelta(seconds=1), "x")
    await store.apply_deploy_outcome(FENCE, DeployOutcome(at=T0))
    assert inner.calls[-1] == "apply_deploy_outcome"
    # apply_deploy_outcome ends a deploy: recorded as succeeded (no failure).
    (event,) = audit.events
    assert event.result is AuditResult.SUCCEEDED
    await store.apply_deploy_outcome(FENCE, DeployOutcome(at=T0, failure=Failure("crash_loop")))
    assert audit.events[-1].result is AuditResult.FAILED
    assert audit.events[-1].failure_code == "crash_loop"


@pytest.mark.asyncio
async def test_the_remembered_commands_are_bounded() -> None:
    store, _, audit = _jobs()
    for n in range(AuditedJobStore.MAX_REMEMBERED + 5):
        ref = dataclasses.replace(REF, command_id=UUID(int=n + 1))
        await store.claim(ClaimRequest(ref=ref, claimed_by_worker="w", ttl=timedelta(seconds=1)))
    assert store.remembered() == AuditedJobStore.MAX_REMEMBERED
    # The oldest was forgotten: its finish cannot name a kind and is not recorded as another's.
    oldest = dataclasses.replace(FENCE, command_id=UUID(int=1))
    await store.finish_command(
        oldest, CommandFinish(status=CommandStatus.FAILED, failure_code="x", at=T0)
    )
    assert audit.events == []
    # A finished command is forgotten.
    newest = dataclasses.replace(FENCE, command_id=UUID(int=AuditedJobStore.MAX_REMEMBERED + 5))
    await store.finish_command(
        newest, CommandFinish(status=CommandStatus.FAILED, failure_code="x", at=T0)
    )
    assert len(audit.events) == 1
    assert store.remembered() == AuditedJobStore.MAX_REMEMBERED - 1


# --- one alert per uncertain outcome, whichever path writes it ------------------------------


class _StatefulRoutes:
    """A route store that applies the domain outcome to its rows, refusing a command that is no
    longer running as the real store does (the fence's third check)."""

    def __init__(self, inp: OutcomeInput) -> None:
        self.inp = inp

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        return self.inp

    async def apply_route_outcome(self, fence: Fence, decide: Any) -> Outcome:
        if self.inp.command.status is not CommandStatus.RUNNING:
            raise IllegalTransition("the command is not running")
        outcome: Outcome = decide(self.inp)
        self.inp = dataclasses.replace(self.inp, hosting=outcome.hosting, command=outcome.command)
        return outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING])
async def test_an_uncertain_route_alerts_exactly_once_across_executor_and_abandoner(
    kind: CommandKind,
) -> None:
    # The lead's ruling: this wrapper is the single source of the route_uncertain alert, for the
    # routing and hosting executors and the abandoner alike. The executor writes the uncertain
    # outcome; the abandoner, running after it for the same command, is refused by the fence and
    # raises no second alert; a later write of another outcome raises none either.
    inner = _StatefulRoutes(
        OutcomeInput(
            hosting=_hosting(RoutingStatus.CHANGING),
            command=_command(kind, CommandStatus.RUNNING),
        )
    )
    audit = _Audit()
    executor_store = AuditedRouteCommandStore(inner, audit, _Clock())
    abandoner_store = AuditedRouteCommandStore(inner, audit, _Clock())

    def uncertain(inp: OutcomeInput) -> Outcome:
        return route_uncertain(inp, Failure("restore_unconfirmed"), T0)

    await executor_store.apply_route_outcome(FENCE, uncertain)
    with pytest.raises(IllegalTransition):
        await abandoner_store.apply_route_outcome(FENCE, uncertain)
    assert [a.action for a in audit.alerts] == [AuditAction.ROUTE_UNCERTAIN]
    assert [e.result for e in audit.events] == [AuditResult.FAILED]


@pytest.mark.asyncio
async def test_the_abandoners_uncertain_outcome_alerts_once() -> None:
    # A command abandoned past its deadline while the route was changing: the abandoner's restore
    # was not confirmed, so it writes the uncertain outcome through the same wrapper.
    inner = _StatefulRoutes(
        OutcomeInput(
            hosting=_hosting(RoutingStatus.CHANGING),
            command=_command(CommandKind.SUSPEND_HOSTING, CommandStatus.RUNNING),
        )
    )
    audit = _Audit()
    store = AuditedRouteCommandStore(inner, audit, _Clock())
    await store.apply_route_outcome(
        FENCE, lambda inp: route_uncertain(inp, Failure("deadline_exceeded"), T0)
    )
    (alert,) = audit.alerts
    assert (alert.failure_code, alert.command_id) == ("deadline_exceeded", COMMAND)
