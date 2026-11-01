"""Audited wrappers around the worker's command-outcome stores (decision 0014 section 21 answer 3,
section 23 item 5 and the lead's ruling on the uncertain-route alert).

Each wrapper delegates every call to the store it wraps and, after a write that ended a command
(succeeded, failed or skipped), records one audit event as the worker's (``AuditActorKind.SYSTEM``,
actor ``worker``). A route outcome that leaves the project's route ``uncertain`` is also an alert
(``AuditAction.ROUTE_UNCERTAIN``, ``alert=true``). A write the store refuses (stale fence, lost
claim, illegal transition) records nothing and raises unchanged; the audit log never raises, so a
recorded event never changes an outcome.

The worker host wraps its stores with these: ``AuditedRouteCommandStore`` (promote, rollback,
suspend_hosting, resume_hosting), ``AuditedDeployStore`` (deploy) and ``AuditedJobStore`` (the
consumer's and the dispatcher's failures of a command: deadline, attempts, unknown kind). The
dispatch store's ``finish_command`` and ``fail_unclaimed_command`` name no command kind, so
``AuditedJobStore`` remembers the work reference of each command it saw taken or claimed (a
bounded memory); the same wrapper must therefore be given to the dispatcher and the consumer as
both their ``JobStore`` and their ``WorkerStore``.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.deploys.ports import (
    CandidateAttempt,
    CandidateAttemptStart,
    DeployFacts,
    DeployStore,
)
from provisioning.core.application.jobs.claims import (
    Claim,
    ClaimRequest,
    ClaimResult,
    DueDispatch,
    JobStore,
    WorkRef,
)
from provisioning.core.application.jobs.ports import (
    CommandFinish,
    DeployOutcome,
    Fence,
    WorkerStore,
)
from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.routing.ports import OutcomeDecider, RouteCommandStore
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.domain import (
    CODE_CANDIDATE_ATTEMPTS_EXHAUSTED,
    CandidateFailure,
    CandidateFailureEffect,
    CandidatePlanKind,
    CommandKind,
    CommandStatus,
    Outcome,
    OutcomeInput,
    RoutingStatus,
)

WORKER_ACTOR: Final = "worker"

_ACTION_OF_KIND: Final = {
    CommandKind.DEPLOY: AuditAction.DEPLOY,
    CommandKind.PROMOTE: AuditAction.PROMOTE,
    CommandKind.ROLLBACK: AuditAction.ROLLBACK,
    CommandKind.SUSPEND_HOSTING: AuditAction.SUSPEND_HOSTING,
    CommandKind.RESUME_HOSTING: AuditAction.RESUME_HOSTING,
    CommandKind.REDEPLOY: AuditAction.REDEPLOY,
}
_RESULT_OF_STATUS: Final = {
    CommandStatus.SUCCEEDED: AuditResult.SUCCEEDED,
    CommandStatus.FAILED: AuditResult.FAILED,
    CommandStatus.SKIPPED: AuditResult.SKIPPED,
}


class _Recorder:
    """Builds and writes the worker's audit events."""

    __slots__ = ("_audit", "_clock")

    def __init__(self, audit: AuditLog, clock: Clock) -> None:
        self._audit = audit
        self._clock = clock

    def event(
        self,
        *,
        kind: CommandKind,
        project_id: UUID,
        release_id: UUID | None,
        command_id: UUID,
        result: AuditResult,
        failure_code: str | None,
        correlation_id: str | None = None,
        action: AuditAction | None = None,
    ) -> AuditEvent | None:
        """The event, or None for a kind the prototype never runs (sync_domains)."""
        chosen = action if action is not None else _ACTION_OF_KIND.get(kind)
        if chosen is None:
            return None
        return AuditEvent(
            actor_kind=AuditActorKind.SYSTEM,
            actor=WORKER_ACTOR,
            action=chosen,
            project_id=project_id,
            release_id=release_id,
            command_id=command_id,
            result=result,
            failure_code=failure_code or None,
            at=self._clock.now(),
            correlation_id=correlation_id,
        )

    def record(self, event: AuditEvent | None) -> None:
        if event is not None:
            self._audit.record(event)

    def alert(self, event: AuditEvent | None) -> None:
        if event is not None:
            self._audit.alert(event)


class AuditedRouteCommandStore:
    """A ``RouteCommandStore`` that records each command outcome it writes, and alerts when the
    route is left uncertain."""

    __slots__ = ("_inner", "_recorder")

    def __init__(self, inner: RouteCommandStore, audit: AuditLog, clock: Clock) -> None:
        self._inner = inner
        self._recorder = _Recorder(audit, clock)

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        return await self._inner.load_route_command(fence)

    async def apply_route_outcome(self, fence: Fence, decide: OutcomeDecider) -> Outcome:
        outcome = await self._inner.apply_route_outcome(fence, decide)
        command = outcome.command
        result = _RESULT_OF_STATUS.get(command.status)
        if result is None:  # the command keeps running (the route is changing)
            return outcome
        self._recorder.record(
            self._recorder.event(
                kind=command.kind,
                project_id=command.project_id,
                release_id=command.release_id,
                command_id=command.id,
                result=result,
                failure_code=command.failure_code,
            )
        )
        if outcome.hosting.routing_status is RoutingStatus.UNCERTAIN:
            self._recorder.alert(
                self._recorder.event(
                    kind=command.kind,
                    project_id=command.project_id,
                    release_id=command.release_id,
                    command_id=command.id,
                    result=AuditResult.FAILED,
                    failure_code=command.failure_code or "route_uncertain",
                    action=AuditAction.ROUTE_UNCERTAIN,
                )
            )
        return outcome


class AuditedDeployStore:
    """A ``DeployStore`` that records the end of each deploy command it writes: a ready candidate
    (succeeded), a failed candidate that ends the command, and exhausted attempts. The release is
    named when this process loaded the command's facts."""

    __slots__ = ("_inner", "_recorder", "_releases")

    def __init__(self, inner: DeployStore, audit: AuditLog, clock: Clock) -> None:
        self._inner = inner
        self._recorder = _Recorder(audit, clock)
        self._releases = _Memory[UUID]()

    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        facts = await self._inner.load_deploy_facts(fence)
        if facts.command.id == fence.command_id and facts.command.release_id is not None:
            self._releases.put(fence.command_id, facts.command.release_id)
        return facts

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        await self._inner.renew_claim(fence, ttl)

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        attempt = await self._inner.begin_candidate_attempt(fence, start)
        if attempt.kind is CandidatePlanKind.EXHAUSTED:
            self._ended(fence, AuditResult.FAILED, CODE_CANDIDATE_ATTEMPTS_EXHAUSTED)
        return attempt

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        return await self._inner.return_replaced_candidates(fence)

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        failure = await self._inner.end_candidate_attempt(fence, step_id, failure_code)
        if failure is None:
            self._ended(fence, AuditResult.SUCCEEDED, None)
        elif failure.effect is not CandidateFailureEffect.RETRY:
            self._ended(fence, AuditResult.FAILED, failure.code)
        return failure

    def _ended(self, fence: Fence, result: AuditResult, code: str | None) -> None:
        self._recorder.record(
            self._recorder.event(
                kind=CommandKind.DEPLOY,
                project_id=fence.project_id,
                release_id=self._releases.pop(fence.command_id),
                command_id=fence.command_id,
                result=result,
                failure_code=code,
            )
        )


class JobAndWorkerStore(JobStore, WorkerStore, Protocol):
    """The dispatch row's store as the worker host builds it: one object for both ports."""


class AuditedJobStore:
    """A ``JobStore`` and ``WorkerStore`` that records the commands the consumer and the
    dispatcher end through the dispatch store (failed or skipped). It remembers the work
    reference of each command taken or claimed, at most ``MAX_REMEMBERED`` (the oldest is
    forgotten first, a finished one at once); a finish of a command it does not remember names no
    kind and is not recorded."""

    MAX_REMEMBERED: Final = 4096

    __slots__ = ("_correlations", "_inner", "_recorder", "_refs")

    def __init__(self, inner: JobAndWorkerStore, audit: AuditLog, clock: Clock) -> None:
        self._inner = inner
        self._recorder = _Recorder(audit, clock)
        self._refs = _Memory[WorkRef](self.MAX_REMEMBERED)
        self._correlations = _Memory[str](self.MAX_REMEMBERED)

    def remembered(self) -> int:
        """The number of commands remembered."""
        return len(self._refs)

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]:
        due = await self._inner.take_due(limit, visibility)
        for row in due:
            self._refs.put(row.ref.command_id, row.ref)
            self._correlations.put(row.ref.command_id, row.correlation_id)
        return due

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        return await self._inner.record_broker_ack(due, redeliver_after)

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool:
        return await self._inner.record_publish_failure(due, retry_after)

    async def count_unfinished_without_dispatch(self) -> int:
        return await self._inner.count_unfinished_without_dispatch()

    async def claim(self, request: ClaimRequest) -> ClaimResult:
        self._refs.put(request.ref.command_id, request.ref)
        return await self._inner.claim(request)

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim:
        return await self._inner.extend_claim(claim, ttl)

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        await self._inner.release_claim(claim, retry_after, failure_code)

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None:
        await self._inner.finish_command(fence, finish)
        result = _RESULT_OF_STATUS.get(finish.status, AuditResult.FAILED)
        self._ended(fence, result, finish.failure_code)

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        await self._inner.fail_unclaimed_command(fence, failure_code, at)
        self._ended(fence, AuditResult.FAILED, failure_code)

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None:
        await self._inner.apply_deploy_outcome(fence, outcome)
        if outcome.failure is None:
            self._ended(fence, AuditResult.SUCCEEDED, None, kind=CommandKind.DEPLOY)
        else:
            self._ended(fence, AuditResult.FAILED, outcome.failure.code, kind=CommandKind.DEPLOY)

    def _ended(
        self,
        fence: Fence,
        result: AuditResult,
        code: str | None,
        *,
        kind: CommandKind | None = None,
    ) -> None:
        ref = self._refs.pop(fence.command_id)
        correlation = self._correlations.pop(fence.command_id)
        if ref is not None and ref.project_id != fence.project_id:
            ref = None  # never name another project's command
        chosen = kind if kind is not None else (None if ref is None else ref.kind)
        if chosen is None:
            return
        self._recorder.record(
            self._recorder.event(
                kind=chosen,
                project_id=fence.project_id,
                release_id=None if ref is None else ref.release_id,
                command_id=fence.command_id,
                result=result,
                failure_code=code,
                correlation_id=correlation,
            )
        )


class _Memory[V]:
    """A bounded map from a command ID to a value; the oldest entry is forgotten first."""

    __slots__ = ("_items", "_limit")

    def __init__(self, limit: int = 4096) -> None:
        self._items: OrderedDict[UUID, V] = OrderedDict()
        self._limit = limit

    def __len__(self) -> int:
        return len(self._items)

    def put(self, key: UUID, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def pop(self, key: UUID) -> V | None:
        return self._items.pop(key, None)
