"""An in-memory job and worker store for the broker suite: the durable state the dispatcher and the
consumer read, kept in a dict, so that the broker tests prove the loops' broker behaviour without
PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from provisioning.core.application import (
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

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


@dataclass
class Row:
    ref: WorkRef
    correlation_id: str
    due: bool = True
    publish_attempts: int = 0
    claim_number: int = 0
    execution_attempts: int = 0
    claimed: bool = False
    finished: bool = False
    acks: int = 0
    last_failure_code: str | None = None


@dataclass
class MemStore:
    rows: dict[str, Row] = field(default_factory=dict)
    finishes: list[CommandFinish] = field(default_factory=list)

    def add(self, ref: WorkRef, correlation_id: str) -> None:
        self.rows[str(ref.command_id)] = Row(ref=ref, correlation_id=correlation_id)

    def _due(self, row: Row) -> DueDispatch:
        return DueDispatch(
            ref=row.ref,
            correlation_id=row.correlation_id,
            publish_attempts=row.publish_attempts,
            claim_number=row.claim_number,
            execution_attempts=row.execution_attempts,
            deadline_at=NOW + timedelta(hours=1),
            deadline_passed=False,
        )

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]:
        out: list[DueDispatch] = []
        for row in self.rows.values():
            if row.due and not row.finished and len(out) < limit:
                row.due = False
                row.publish_attempts += 1
                out.append(self._due(row))
        return out

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        self.rows[str(due.ref.command_id)].acks += 1
        return True

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool:
        self.rows[str(due.ref.command_id)].due = True
        return True

    async def count_unfinished_without_dispatch(self) -> int:
        return 0

    async def claim(self, request: ClaimRequest) -> ClaimResult:
        row = self.rows.get(str(request.ref.command_id))
        if row is None:
            return ClaimResult(outcome=ClaimOutcome.NO_WORK)
        if row.ref != request.ref:
            return ClaimResult(outcome=ClaimOutcome.MISMATCH)
        if row.finished:
            return ClaimResult(outcome=ClaimOutcome.FINISHED)
        if row.claimed:
            return ClaimResult(outcome=ClaimOutcome.HELD)
        row.claimed = True
        row.claim_number += 1
        row.execution_attempts += 1
        return ClaimResult(
            outcome=ClaimOutcome.CLAIMED,
            claim=Claim(
                ref=row.ref,
                claimed_by_worker=request.claimed_by_worker,
                claim_number=row.claim_number,
                execution_attempt=row.execution_attempts,
                expires_at=NOW + request.ttl,
                deadline_at=NOW + timedelta(hours=1),
                last_failure_code=row.last_failure_code,
            ),
        )

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim:
        return claim

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        row = self.rows[str(claim.ref.command_id)]
        if row.claim_number != claim.claim_number:
            raise ClaimLost("claim taken over")
        row.claimed = False
        row.due = True
        row.last_failure_code = failure_code

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None:
        row = self.rows[str(fence.command_id)]
        if row.claim_number != fence.claim_number:
            raise ClaimLost("claim taken over")
        row.finished = True
        self.finishes.append(finish)

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        self.rows[str(fence.command_id)].finished = True

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None:
        raise AssertionError("not used by the broker suite")
