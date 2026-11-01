"""StartBuild: the build endpoint's use case, also called by the release intake (decision 0014
section 3, "Build").

It starts the build of an ``accepted`` release, or the next attempt of a ``building`` release
whose last attempt failed while attempts remain (that attempt is the pending build step the
failure queued). The order is a durable intent first:

1. the step goes pending -> running in the build store (``start_attempt``);
2. outside any transaction, ``BuildSystem.find`` looks the attempt up, and ``BuildSystem.start``
   runs only when ``find`` returns nothing;
3. the handle's reference is stored in the step's ``log_ref`` (``record_log_ref``).

A crash between 2 and 3 leaves a running step without a handle; ``PollBuilds`` finds the build
again with ``find``, so no second build starts. The use case holds no transaction across a call:
each store method is its own transaction. A pending attempt that nothing starts (the intake stopped
before 1, or a failed attempt queued it) is started here by the build worker's
``StartPendingBuilds`` once it has waited ``BUILD_PENDING_GRACE`` (QA-10, OD-51).

Audit (decision 0014 section 21 answer 3): every start that reached the build store records
``build_attempt_started`` (succeeded: the build system holds the build; accepted: it was
unavailable and the poller dispatches it; failed: it refused the attempt), and every attempt
failed here records ``build_attempt_finished``. The actor is the process (``api`` or
``build-worker``); the principal of ``build_release`` is the service's to record.

The build/API split (decision 0016 section 5 item 2, task H2): only the build worker runs
``StartBuild``. The API's build_release is ``RequestBuild``: the pending build step that the
intake or a failed attempt wrote is the durable wish, so the API checks it with its read grant,
writes nothing, and sends a hint (PostgreSQL ``NOTIFY`` on ``BUILD_WANTED_CHANNEL``, no grant
needed). The build worker's ``WantedBuilds`` starts each hinted release at once; a hint that is
lost or dropped only waits for ``StartPendingBuilds`` and its BUILD_PENDING_GRACE, so SQL stays
the authority and the hint only makes the start prompt.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.builds.ports import (
    BuildAttemptParams,
    BuildStepKey,
    BuildStore,
    StartBuildParams,
    finished_at,
    record_build_event,
)
from provisioning.core.application.builds.request_build import _startable_step
from provisioning.core.application.operations.views import BuildDispatch, BuildStarted
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildSystem,
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.core.application.ports.dependencies import IdSource, RepositoryLocator
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditLog,
    AuditResult,
    NullAuditLog,
)
from provisioning.core.application.shared.errors import (
    BuildInProgress,
    Contention,
    DependencyUnavailable,
    NotFound,
    ReleaseNotBuildable,
)
from provisioning.core.domain import (
    CODE_BUILD_REJECTED,
    BuildAttemptPolicy,
    BuildResult,
    IllegalTransition,
    InvalidLogRef,
    validate_log_ref,
)


class BuildDispatcher:
    """Hands one running build attempt to the build system: find, else start, then store the
    handle. Shared by ``StartBuild`` and ``PollBuilds``."""

    __slots__ = (
        "_actor",
        "_audit",
        "_build_system",
        "_call_timeout",
        "_ids",
        "_image_registry",
        "_policy",
        "_repositories",
        "_store",
    )

    def __init__(
        self,
        *,
        store: BuildStore,
        build_system: BuildSystem,
        repositories: RepositoryLocator,
        ids: IdSource,
        policy: BuildAttemptPolicy,
        image_registry: str,
        call_timeout: timedelta,
        audit: AuditLog,
        actor: str,
    ) -> None:
        policy.validate()
        if image_registry == "" or image_registry.endswith("/"):
            raise ValueError("build dispatch: the image registry is required, without a slash")
        if call_timeout <= timedelta(0):
            raise ValueError("build dispatch: the call timeout must be positive")
        self._store = store
        self._build_system = build_system
        self._repositories = repositories
        self._ids = ids
        self._policy = policy
        self._image_registry = image_registry
        self._call_timeout = call_timeout.total_seconds()
        self._audit = audit
        self._actor = actor

    def image_repository(self, project_id: UUID) -> str:
        """``<registry>/webblock/sites/<project_id>``, from configuration and the project ID only
        (decision 0009 section 3)."""
        return f"{self._image_registry}/webblock/sites/{project_id}"

    async def dispatch(
        self, step: BuildStepKey, attempt: int, git_commit_sha: str, repository_id: int
    ) -> BuildDispatch:
        """``PENDING`` when the locator or the build system is unavailable (nothing written);
        ``REJECTED`` when the build system refuses the attempt or answers with a handle that is not
        a valid log reference (the attempt fails build_rejected); ``DISPATCHED`` otherwise."""
        try:
            async with asyncio.timeout(self._call_timeout):
                repository_url = await self._repositories.repository_url(
                    step.project_id, repository_id
                )
        except DependencyUnavailable, TimeoutError:
            return BuildDispatch.PENDING
        request = BuildRequest(
            project_id=step.project_id,
            release_id=step.release_id,
            attempt=attempt,
            repository_url=repository_url,
            commit_sha=git_commit_sha,
            image_repository=self.image_repository(step.project_id),
        )
        try:
            handle = await self._find_or_start(request)
        except BuildSystemUnavailable, TimeoutError:
            return BuildDispatch.PENDING
        except BuildSystemRejected:
            await self.fail(step, attempt, git_commit_sha, repository_id, CODE_BUILD_REJECTED)
            return BuildDispatch.REJECTED
        try:
            validate_log_ref(handle.ref)
        except InvalidLogRef:
            await self.fail(step, attempt, git_commit_sha, repository_id, CODE_BUILD_REJECTED)
            return BuildDispatch.REJECTED
        await self._store.record_log_ref(step, handle.ref)
        return BuildDispatch.DISPATCHED

    async def _find_or_start(self, request: BuildRequest) -> BuildHandle:
        async with asyncio.timeout(self._call_timeout):
            found = await self._build_system.find(request)
        if found is not None:
            return found
        async with asyncio.timeout(self._call_timeout):
            return await self._build_system.start(request)

    async def fail(
        self,
        step: BuildStepKey,
        attempt: int,
        git_commit_sha: str,
        repository_id: int,
        code: str,
    ) -> None:
        """Fail the running attempt with ``code`` through the build-result path: the next attempt
        is queued, or the release fails when the attempts are spent. Audited as
        ``build_attempt_finished``, failed with ``code``."""
        write = await self._store.apply_result(
            BuildResult(
                project_id=step.project_id,
                release_id=step.release_id,
                step_id=step.step_id,
                attempt=attempt,
                git_commit_sha=git_commit_sha,
                repository_id=repository_id,
                failure_code=code,
            ),
            BuildAttemptParams(policy=self._policy, next_step_id=self._ids.new_id()),
        )
        record_build_event(
            self._audit,
            actor=self._actor,
            action=AuditAction.BUILD_ATTEMPT_FINISHED,
            step=step,
            result=AuditResult.FAILED,
            failure_code=code,
            at=finished_at(write),
        )


_STARTED_RESULT = {
    BuildDispatch.DISPATCHED: (AuditResult.SUCCEEDED, None),
    BuildDispatch.PENDING: (AuditResult.ACCEPTED, None),
    BuildDispatch.REJECTED: (AuditResult.FAILED, CODE_BUILD_REJECTED),
}


class StartBuild:
    """Construction takes required configuration: ``image_registry`` (the registry location,
    ``<registry>`` of ``<registry>/webblock/sites/<project_id>``), ``build_duration`` (a step's
    deadline), ``call_timeout`` (each build system and locator call) and the attempt policy.

    ``audit`` and ``actor`` default to dropping the events under the API's name, because the API
    host (W6-D's wiring) constructs this class too; the build worker passes both."""

    DEFAULT_ACTOR = "api"

    __slots__ = ("_actor", "_audit", "_build_duration", "_dispatcher", "_logger", "_store")

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
        build_duration: timedelta,
        call_timeout: timedelta,
        audit: AuditLog | None = None,
        actor: str = DEFAULT_ACTOR,
    ) -> None:
        if build_duration < timedelta(microseconds=1):
            raise ValueError("start build: the build duration must be at least one microsecond")
        self._store = store
        self._logger = logger
        self._build_duration = build_duration
        self._audit: AuditLog = NullAuditLog() if audit is None else audit
        self._actor = actor
        self._dispatcher = BuildDispatcher(
            store=store,
            build_system=build_system,
            repositories=repositories,
            ids=ids,
            policy=policy,
            image_registry=image_registry,
            call_timeout=call_timeout,
            audit=self._audit,
            actor=actor,
        )

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        """Start the release's next build attempt. ``NotFound`` for a release missing from the
        project; ``BuildInProgress`` while its attempt is running; ``ReleaseNotBuildable`` in any
        other status. The result's ``dispatch`` says whether the build system holds the build
        (``DISPATCHED``), was unavailable (``PENDING``: step running, retried by the poller), or
        refused it (``REJECTED``: the attempt failed build_rejected)."""
        step = _startable_step(await self._store.load_build_target(project_id, release_id))
        key = BuildStepKey(project_id=project_id, release_id=release_id, step_id=step.id)
        try:
            write = await self._store.start_attempt(
                StartBuildParams(step=key, duration=self._build_duration)
            )
        except IllegalTransition:
            # Another start won the conditional write in between: report what it left.
            _startable_step(await self._store.load_build_target(project_id, release_id))
            raise Contention(f"build start of release {release_id}") from None
        dispatch = await self._dispatcher.dispatch(
            key,
            write.step.attempt,
            write.release.git_commit_sha,
            write.release.repository_id,
        )
        if dispatch is not BuildDispatch.DISPATCHED:
            self._logger.warning(
                "build attempt %d of release %s of project %s not dispatched: %s",
                write.step.attempt,
                release_id,
                project_id,
                dispatch,
            )
        result, code = _STARTED_RESULT[dispatch]
        record_build_event(
            self._audit,
            actor=self._actor,
            action=AuditAction.BUILD_ATTEMPT_STARTED,
            step=key,
            result=result,
            failure_code=code,
            at=write.step.started_at or write.release.updated_at,
        )
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=step.id,
            attempt=write.step.attempt,
            dispatch=dispatch,
        )


BUILD_WANTED_CHANNEL: Final = "provisioning_build_wanted"
"""The PostgreSQL notification channel of build hints (task H2)."""

MAX_BUILD_HINT_BYTES: Final = 73
"""Two canonical UUIDs and the slash between them."""

_CANONICAL_UUID: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_HINT: Final = re.compile(f"({_CANONICAL_UUID})/({_CANONICAL_UUID})", re.ASCII)


def build_hint(project_id: UUID, release_id: UUID) -> str:
    """The payload of a build hint: ``<project_id>/<release_id>`` in canonical lowercase form."""
    return f"{project_id}/{release_id}"


def parse_build_hint(payload: str) -> tuple[UUID, UUID] | None:
    """The project and release of a hint, or None for anything but two canonical lowercase,
    non-nil UUIDs separated by one slash. Any database login can send a notification, so the
    payload is untrusted: a valid one only names a release whose start is checked again."""
    if len(payload) > MAX_BUILD_HINT_BYTES:
        return None
    match = _HINT.fullmatch(payload)
    if match is None:
        return None
    project_id, release_id = UUID(match.group(1)), UUID(match.group(2))
    if project_id.int == 0 or release_id.int == 0:
        return None
    return project_id, release_id


class BuildStarting(Protocol):
    """``StartBuild.start``."""

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted: ...


class WantedBuilds:
    """The build worker's half of the hint (task H2): ``offer`` takes a payload from the
    listener and queues it, at most ``queue_size`` at a time; ``run`` starts each queued release
    through ``StartBuild``, one at a time. A malformed payload or one beyond the bound is dropped
    with a log line that never quotes it. A start that is no longer possible (started in
    between, the release moved on, or contention) is not an error; any other failure is logged.
    Either way the release's pending step, if any, is found again by the grace scan."""

    __slots__ = ("_logger", "_queue", "_starter")

    def __init__(self, *, starter: BuildStarting, queue_size: int, logger: logging.Logger) -> None:
        if queue_size < 1:
            raise ValueError("wanted builds: the queue size must be at least 1")
        self._starter = starter
        self._logger = logger
        self._queue: asyncio.Queue[tuple[UUID, UUID]] = asyncio.Queue(maxsize=queue_size)

    def offer(self, payload: str) -> bool:
        """Queue a hint; False when it was dropped."""
        hint = parse_build_hint(payload)
        if hint is None:
            self._logger.info("malformed build hint dropped (bytes=%d)", len(payload))
            return False
        try:
            self._queue.put_nowait(hint)
        except asyncio.QueueFull:
            self._logger.warning(
                "build hint dropped: the queue is full; the grace scan starts it "
                "(project_id=%s release_id=%s)",
                hint[0],
                hint[1],
            )
            return False
        return True

    async def run(self) -> None:
        """Start queued releases until cancelled."""
        while True:
            project_id, release_id = await self._queue.get()
            try:
                started = await self._starter.start(project_id, release_id)
            except BuildInProgress, ReleaseNotBuildable, Contention, NotFound:
                self._logger.debug(
                    "hinted build no longer startable (project_id=%s release_id=%s)",
                    project_id,
                    release_id,
                )
            except Exception as exc:  # one start's failure never stops the loop
                self._logger.warning(
                    "hinted build not started (project_id=%s release_id=%s error=%s)",
                    project_id,
                    release_id,
                    type(exc).__name__,
                )
            else:
                self._logger.info(
                    "hinted build started (project_id=%s release_id=%s attempt=%d dispatch=%s)",
                    project_id,
                    release_id,
                    started.attempt,
                    started.dispatch,
                )
