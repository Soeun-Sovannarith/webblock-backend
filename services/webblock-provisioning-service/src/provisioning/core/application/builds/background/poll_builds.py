"""PollBuilds: one round of the build worker's poll loop (decision 0014 section 3, "Build").

For each running build step:

- with a ``log_ref``: ``BuildSystem.status`` of the handle. A success is accepted only when the
  reported project, release, attempt and commit SHA match the attempt and the digest is
  ``sha256:<64 hex>``; otherwise the attempt fails ``build_result_mismatch``. A failure fails the
  attempt with the build system's code (``build_failed`` when it has no well-formed one); a lost
  build fails it ``build_lost``. Queued and running builds wait.
- without a ``log_ref`` (a crash between start and storing the handle), once it has been running
  for ``dispatch_grace``: ``find``, then ``start`` only when nothing is found, then the handle is
  stored. The grace keeps the poller from racing ``StartBuild`` between its own find and store.

``BuildSystemUnavailable`` (or a call timeout) leaves the step unchanged for the next round;
``BuildSystemRejected`` fails the attempt ``build_rejected``. Deadlines stay with the sweeper
(``BuildAttempts.sweep_round``). Each finished attempt is audited as ``build_attempt_finished``.

``StartPendingBuilds`` is the same loop's other half (QA-10, OD-51): a pending build step that
nothing started, pending for longer than ``BUILD_PENDING_GRACE`` (a failed attempt queued it, or
the intake stopped before it started the first one), is started through ``StartBuild.start``, with
its attempt limit and its conditional pending -> running write. The grace leaves a fresh attempt
to the intake or the build endpoint that is about to start it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from provisioning.core.application.builds.background.start_build import BuildDispatcher, StartBuild
from provisioning.core.application.builds.ports import (
    BuildAttemptParams,
    BuildStepKey,
    BuildStore,
    PendingBuild,
    RunningBuild,
    finished_at,
    record_build_event,
)
from provisioning.core.application.operations.views import BuildDispatch
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildState,
    BuildStatus,
    BuildSystem,
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.core.application.ports.dependencies import IdSource, RepositoryLocator
from provisioning.core.application.shared.audit import AuditAction, AuditLog, AuditResult
from provisioning.core.application.shared.bounded import for_each_bounded
from provisioning.core.application.shared.errors import (
    BuildInProgress,
    Contention,
    NotFound,
    ReleaseNotBuildable,
)
from provisioning.core.domain import (
    CODE_BUILD_FAILED,
    CODE_BUILD_LOST,
    CODE_BUILD_REJECTED,
    CODE_BUILD_RESULT_MISMATCH,
    BuildAttemptPolicy,
    BuildResult,
    DomainError,
    FailurePlace,
    FailureStage,
    IllegalTransition,
    ResultMismatch,
    failure_code_entry,
    parse_image_digest,
    validate_code,
)

# Bounds the running steps one poll round reads.
MAX_POLL_LIMIT = 1000


@dataclass(frozen=True, slots=True)
class PollRoundParams:
    limit: int
    concurrency: int

    def validate(self) -> None:
        """1 <= limit <= MAX_POLL_LIMIT and 1 <= concurrency <= limit (``ValueError``)."""
        if not 1 <= self.limit <= MAX_POLL_LIMIT:
            raise ValueError(f"poll round: limit must be 1 to {MAX_POLL_LIMIT}")
        if not 1 <= self.concurrency <= self.limit:
            raise ValueError("poll round: concurrency must be 1 to the limit")


class PollOutcome(StrEnum):
    WAITING = "waiting"  # queued or running at the build system, or inside the dispatch grace
    SUCCEEDED = "succeeded"  # the result was accepted: the release is built
    FAILED = "failed"  # the attempt failed with the build system's failure, or build_lost
    MISMATCH = "mismatch"  # the attempt failed build_result_mismatch
    DISPATCHED = "dispatched"  # a handle was found or a build started, and stored
    REJECTED = "rejected"  # the build system refused the attempt; it failed build_rejected
    UNAVAILABLE = "unavailable"  # the build system or the locator was unavailable; unchanged
    STALE = "stale"  # the step was no longer running when the result was written
    ERROR = "error"  # a store error; nothing written


@dataclass(frozen=True, slots=True)
class PollResult:
    step: BuildStepKey
    attempt: int
    outcome: PollOutcome
    error: Exception | None = None


_DISPATCH_OUTCOME = {
    BuildDispatch.DISPATCHED: PollOutcome.DISPATCHED,
    BuildDispatch.PENDING: PollOutcome.UNAVAILABLE,
    BuildDispatch.REJECTED: PollOutcome.REJECTED,
}


class PollBuilds:
    """``dispatch_grace`` is required configuration: how long a running step without a handle is
    left to ``StartBuild`` before the poller looks it up."""

    __slots__ = (
        "_actor",
        "_audit",
        "_build_system",
        "_call_timeout",
        "_dispatch_grace",
        "_dispatcher",
        "_ids",
        "_logger",
        "_policy",
        "_store",
    )

    def __init__(
        self,
        *,
        store: BuildStore,
        build_system: BuildSystem,
        repositories: RepositoryLocator,
        ids: IdSource,
        logger: logging.Logger,
        policy: BuildAttemptPolicy,
        image_registry: str,
        call_timeout: timedelta,
        dispatch_grace: timedelta,
        audit: AuditLog,
        actor: str,
    ) -> None:
        if dispatch_grace <= timedelta(0):
            raise ValueError("poll builds: the dispatch grace must be positive")
        self._dispatcher = BuildDispatcher(
            store=store,
            build_system=build_system,
            repositories=repositories,
            ids=ids,
            policy=policy,
            image_registry=image_registry,
            call_timeout=call_timeout,
            audit=audit,
            actor=actor,
        )
        self._audit = audit
        self._actor = actor
        self._store = store
        self._build_system = build_system
        self._ids = ids
        self._logger = logger
        self._policy = policy
        self._call_timeout = call_timeout.total_seconds()
        self._dispatch_grace = dispatch_grace

    async def poll_round(self, params: PollRoundParams) -> list[PollResult]:
        """Scan up to ``params.limit`` running build steps and handle each, at most
        ``params.concurrency`` at a time; results in scan order. The scan's error is raised; a
        step's error is in its result."""
        params.validate()
        scan = await self._store.scan_running(params.limit)
        builds = scan.builds
        results: list[PollResult | None] = [None] * len(builds)

        async def poll(index: int) -> None:
            results[index] = await self._poll_one(builds[index], scan.now)

        await for_each_bounded(len(builds), params.concurrency, poll)
        return [r for r in results if r is not None]

    async def _poll_one(self, build: RunningBuild, now: datetime) -> PollResult:
        try:
            outcome = await self._handle(build, now)
        except ResultMismatch, IllegalTransition:
            outcome = PollOutcome.STALE
        except Exception as exc:  # a step's own failure is its result, never the round's
            return PollResult(build.step, build.attempt, PollOutcome.ERROR, error=exc)
        return PollResult(build.step, build.attempt, outcome)

    async def _handle(self, build: RunningBuild, now: datetime) -> PollOutcome:
        if build.log_ref is None:
            if now - build.started_at < self._dispatch_grace:
                return PollOutcome.WAITING
            dispatch = await self._dispatcher.dispatch(
                build.step, build.attempt, build.git_commit_sha, build.repository_id
            )
            return _DISPATCH_OUTCOME[dispatch]
        try:
            async with asyncio.timeout(self._call_timeout):
                status = await self._build_system.status(BuildHandle(ref=build.log_ref))
        except BuildSystemUnavailable, TimeoutError:
            return PollOutcome.UNAVAILABLE
        except BuildSystemRejected:
            await self._fail(build, CODE_BUILD_REJECTED)
            return PollOutcome.REJECTED
        match status.state:
            case BuildState.QUEUED | BuildState.RUNNING:
                return PollOutcome.WAITING
            case BuildState.SUCCEEDED:
                return await self._succeeded(build, status)
            case BuildState.FAILED:
                await self._fail(build, _failure_code(status.failure_code))
                return PollOutcome.FAILED
            case BuildState.LOST:
                await self._fail(build, CODE_BUILD_LOST)
                return PollOutcome.FAILED

    async def _succeeded(self, build: RunningBuild, status: BuildStatus) -> PollOutcome:
        digest = _matching_digest(build, status)
        if digest is None:
            self._logger.warning(
                "build result refused: it does not match the attempt "
                "(project %s, release %s, step %s, attempt %d)",
                build.step.project_id,
                build.step.release_id,
                build.step.step_id,
                build.attempt,
            )
            await self._fail(build, CODE_BUILD_RESULT_MISMATCH)
            return PollOutcome.MISMATCH
        write = await self._store.apply_result(
            BuildResult(
                project_id=build.step.project_id,
                release_id=build.step.release_id,
                step_id=build.step.step_id,
                attempt=build.attempt,
                git_commit_sha=build.git_commit_sha,
                repository_id=build.repository_id,
                container_image_digest=digest,
            ),
            BuildAttemptParams(policy=self._policy, next_step_id=self._ids.new_id()),
        )
        record_build_event(
            self._audit,
            actor=self._actor,
            action=AuditAction.BUILD_ATTEMPT_FINISHED,
            step=build.step,
            result=AuditResult.SUCCEEDED,
            failure_code=None,
            at=finished_at(write),
        )
        return PollOutcome.SUCCEEDED

    async def _fail(self, build: RunningBuild, code: str) -> None:
        await self._dispatcher.fail(
            build.step, build.attempt, build.git_commit_sha, build.repository_id, code
        )


class PendingOutcome(StrEnum):
    STARTED = "started"  # the attempt runs and the build system holds its build
    UNAVAILABLE = "unavailable"  # the attempt runs; the build system was not reached (poll retries)
    REJECTED = "rejected"  # the build system refused it; the attempt failed build_rejected
    STALE = "stale"  # no longer pending: started in between, or the release moved on
    ERROR = "error"  # a store error; the step is found again by a later scan


@dataclass(frozen=True, slots=True)
class PendingStartResult:
    build: PendingBuild
    outcome: PendingOutcome
    error: Exception | None = None


_PENDING_OUTCOME = {
    BuildDispatch.DISPATCHED: PendingOutcome.STARTED,
    BuildDispatch.PENDING: PendingOutcome.UNAVAILABLE,
    BuildDispatch.REJECTED: PendingOutcome.REJECTED,
}


class StartPendingBuilds:
    """Starts the pending build attempts that nothing started (QA-10, OD-51). ``grace`` is
    BUILD_PENDING_GRACE: how long an attempt is left pending before this loop starts it."""

    __slots__ = ("_grace", "_starter", "_store")

    def __init__(self, *, store: BuildStore, starter: StartBuild, grace: timedelta) -> None:
        if grace <= timedelta(0):
            raise ValueError("start pending builds: the pending grace must be positive")
        self._store = store
        self._starter = starter
        self._grace = grace

    async def start_round(self, params: PollRoundParams) -> list[PendingStartResult]:
        """Scan up to ``params.limit`` attempts pending for longer than the grace and start each
        through ``StartBuild.start``, at most ``params.concurrency`` at a time; results in scan
        order. The scan's error is raised; a start's error is in its result."""
        params.validate()
        scan = await self._store.scan_pending(params.limit, self._grace)
        builds = scan.builds
        results: list[PendingStartResult | None] = [None] * len(builds)

        async def start(index: int) -> None:
            results[index] = await self._start_one(builds[index])

        await for_each_bounded(len(builds), params.concurrency, start)
        return [r for r in results if r is not None]

    async def _start_one(self, build: PendingBuild) -> PendingStartResult:
        try:
            started = await self._starter.start(build.step.project_id, build.step.release_id)
        except BuildInProgress, ReleaseNotBuildable, Contention, NotFound:
            return PendingStartResult(build, PendingOutcome.STALE)
        except Exception as exc:  # a start's own failure is its result, never the round's
            return PendingStartResult(build, PendingOutcome.ERROR, error=exc)
        return PendingStartResult(build, _PENDING_OUTCOME[started.dispatch])


def _matching_digest(build: RunningBuild, status: BuildStatus) -> str | None:
    """The reported digest when the pipeline built exactly this attempt, else None."""
    if (
        status.project_id != build.step.project_id
        or status.release_id != build.step.release_id
        or status.attempt != build.attempt
        or status.commit_sha != build.git_commit_sha
        or status.image_digest is None
    ):
        return None
    try:
        return parse_image_digest(status.image_digest)
    except DomainError:
        return None


def _failure_code(reported: str | None) -> str:
    """The build system's failure code when it is a build code of the failure-code catalogue,
    else build_failed (task H5a: every stored code is catalogued)."""
    if reported is None:
        return CODE_BUILD_FAILED
    try:
        validate_code(reported)
    except DomainError:
        return CODE_BUILD_FAILED
    entry = failure_code_entry(reported)
    if entry is None or entry.stage is not FailureStage.BUILD:
        return CODE_BUILD_FAILED
    return reported if FailurePlace.STEP in entry.places else CODE_BUILD_FAILED
