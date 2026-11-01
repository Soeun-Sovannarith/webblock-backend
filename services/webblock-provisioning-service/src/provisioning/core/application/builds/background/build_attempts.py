"""The build attempts use case: start an attempt, apply a result, one round of the deadline sweep
(decision 0008 section 4).

An attempt the sweep fails ``deadline_exceeded`` is audited as ``build_attempt_finished``. Its build
is then aborted in the build system (``BuildSystem.cancel``, decision 0014 section 23, QA-10), so a
build that hangs (Jenkins restarted mid-build leaves a pipeline that never resumes) frees its
executor for the next attempt; the cancel is audited as ``build_cancelled``. The cancel runs after
the sweep's transaction committed and is best effort: its failure is logged and audited
``cancel_failed``, and the attempt stays failed (a result that the build still reports later is
refused, because the step no longer runs).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol

from provisioning.core.application.builds.ports import (
    BuildAttemptParams,
    BuildStore,
    BuildWrite,
    OverdueBuild,
    StartBuildParams,
    SweepOutcome,
    record_build_event,
)
from provisioning.core.application.ports.build_system import BuildHandle
from provisioning.core.application.ports.dependencies import Clock, IdSource
from provisioning.core.application.shared.audit import AuditAction, AuditLog, AuditResult
from provisioning.core.application.shared.bounded import for_each_bounded
from provisioning.core.application.shared.errors import CorruptRow, Timeout
from provisioning.core.domain import (
    CODE_DEADLINE_EXCEEDED,
    BuildAttemptPolicy,
    BuildResult,
    ResultMismatch,
)

# Bounds the candidates one sweep round scans.
MAX_SWEEP_LIMIT = 1000

# The audit failure code of a cancel that did not reach or was refused by the build system.
CODE_CANCEL_FAILED: Final = "cancel_failed"


class BuildCanceller(Protocol):
    """``BuildSystem.cancel``: abort the build behind a handle; idempotent."""

    async def cancel(self, handle: BuildHandle) -> None: ...


@dataclass(frozen=True, slots=True)
class SweepRoundParams:
    limit: int
    concurrency: int
    transaction_timeout: timedelta

    def validate(self) -> None:
        """1 <= limit <= MAX_SWEEP_LIMIT, 1 <= concurrency <= limit, a positive timeout
        (``ValueError``)."""
        if not 1 <= self.limit <= MAX_SWEEP_LIMIT:
            raise ValueError(f"sweep round: limit must be 1 to {MAX_SWEEP_LIMIT}")
        if not 1 <= self.concurrency <= self.limit:
            raise ValueError("sweep round: concurrency must be 1 to the limit")
        if self.transaction_timeout <= timedelta(0):
            raise ValueError("sweep round: transaction timeout must be positive")


@dataclass(frozen=True, slots=True)
class SweepResult:
    """A candidate's outcome, or the error of its transaction (nothing written). ``cancelled``
    says whether the failed attempt's build was aborted; None when no cancel was tried (the
    attempt was not failed, or it had no handle)."""

    candidate: OverdueBuild
    outcome: SweepOutcome | None = None
    error: Exception | None = None
    cancelled: bool | None = None


class BuildAttempts:
    """A build is not a command: no fence, no dispatch row, no principal."""

    __slots__ = (
        "_actor",
        "_audit",
        "_builds",
        "_cancel_timeout",
        "_clock",
        "_ids",
        "_logger",
        "_policy",
        "_store",
    )

    def __init__(
        self,
        *,
        store: BuildStore,
        ids: IdSource,
        logger: logging.Logger,
        policy: BuildAttemptPolicy,
        builds: BuildCanceller,
        clock: Clock,
        audit: AuditLog,
        actor: str,
        cancel_timeout: timedelta,
    ) -> None:
        """The policy is BUILD_MAX_ATTEMPTS, 1 to MAX_BUILD_ATTEMPTS (``InvalidAttempt``);
        ``cancel_timeout`` bounds one cancel and must be positive (``ValueError``)."""
        policy.validate()
        if cancel_timeout <= timedelta(0):
            raise ValueError("build attempts: the cancel timeout must be positive")
        self._store = store
        self._ids = ids
        self._logger = logger
        self._policy = policy
        self._builds = builds
        self._clock = clock
        self._audit = audit
        self._actor = actor
        self._cancel_timeout = cancel_timeout.total_seconds()

    async def start(self, params: StartBuildParams) -> BuildWrite:
        """Invalid params are refused before the store is called."""
        params.validate()
        return await self._store.start_attempt(params)

    async def apply_result(self, result: BuildResult) -> BuildWrite:
        """Apply a result with the policy and a fresh next step ID. ``ResultMismatch`` is logged
        with the project, release and step IDs and the attempt only, and re-raised."""
        next_step_id = self._ids.new_id()
        try:
            return await self._store.apply_result(
                result, BuildAttemptParams(policy=self._policy, next_step_id=next_step_id)
            )
        except ResultMismatch:
            self._logger.warning(
                "build result refused: it does not match the release and its running attempt "
                "(error_code=result_mismatch, project_id=%s, release_id=%s, step_id=%s, "
                "attempt=%d)",
                result.project_id,
                result.release_id,
                result.step_id,
                result.attempt,
            )
            raise

    async def sweep_round(self, params: SweepRoundParams) -> list[SweepResult]:
        """The unlocked scan, then one ``sweep_overdue`` per candidate, at most ``concurrency``
        at a time, each bounded by ``transaction_timeout``; results in scan order."""
        params.validate()
        try:
            async with asyncio.timeout(params.transaction_timeout.total_seconds()):
                candidates = await self._store.scan_overdue(params.limit)
        except TimeoutError:
            raise Timeout("scan overdue build steps") from None
        results: list[SweepResult | None] = [None] * len(candidates)

        async def sweep(index: int) -> None:
            results[index] = await self._sweep(candidates[index], params.transaction_timeout)

        await for_each_bounded(len(candidates), params.concurrency, sweep)
        return [r for r in results if r is not None]

    async def _sweep(self, candidate: OverdueBuild, limit: timedelta) -> SweepResult:
        """Fail one candidate whose deadline passed, when the store's re-check under the locks
        confirms it."""
        try:
            next_step_id = self._ids.new_id()
            attempt = BuildAttemptParams(policy=self._policy, next_step_id=next_step_id)
            async with asyncio.timeout(limit.total_seconds()):
                outcome: object = await self._store.sweep_overdue(candidate, attempt)
        except Exception as exc:  # a candidate's failure is its result; a later scan finds it
            return SweepResult(candidate=candidate, error=exc)
        if outcome is SweepOutcome.FAILED:
            return SweepResult(
                candidate=candidate, outcome=outcome, cancelled=await self._after_failed(candidate)
            )
        if isinstance(outcome, SweepOutcome):
            return SweepResult(candidate=candidate, outcome=outcome)
        return SweepResult(
            candidate=candidate,
            error=CorruptRow(
                f"build store returned an unknown sweep outcome for release "
                f"{candidate.step.release_id}"
            ),
        )

    async def _after_failed(self, candidate: OverdueBuild) -> bool | None:
        """Audit the attempt the sweep failed, then cancel its build when it has a handle."""
        self._record(
            candidate,
            AuditAction.BUILD_ATTEMPT_FINISHED,
            AuditResult.FAILED,
            CODE_DEADLINE_EXCEEDED,
        )
        if candidate.log_ref is None:
            return None
        try:
            async with asyncio.timeout(self._cancel_timeout):
                await self._builds.cancel(BuildHandle(ref=candidate.log_ref))
        except Exception as exc:  # best effort: the attempt stays failed either way
            self._logger.warning(
                "build of an overdue attempt not cancelled (project_id=%s release_id=%s "
                "step_id=%s attempt=%d log_ref=%s error=%s)",
                candidate.step.project_id,
                candidate.step.release_id,
                candidate.step.step_id,
                candidate.attempt,
                candidate.log_ref,
                type(exc).__name__,
            )
            self._record(
                candidate, AuditAction.BUILD_CANCELLED, AuditResult.FAILED, CODE_CANCEL_FAILED
            )
            return False
        self._record(candidate, AuditAction.BUILD_CANCELLED, AuditResult.SUCCEEDED, None)
        return True

    def _record(
        self,
        candidate: OverdueBuild,
        action: AuditAction,
        result: AuditResult,
        code: str | None,
    ) -> None:
        record_build_event(
            self._audit,
            actor=self._actor,
            action=action,
            step=candidate.step,
            result=result,
            failure_code=code,
            at=self._clock.now(),
        )
