"""StartBuild and PollBuilds, the prototype's build path (decision 0014 sections 3 and 4).

The fakes: an in-memory build store that applies the domain's build functions to its rows, as the
PostgreSQL store does under its locks, and a fake build system that remembers the builds it
started, so that ``find`` after a crash returns the build ``start`` queued. Every fake call is
appended to one shared log, so the order of store and build-system calls is observable.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application import (
    ActionDenied,
    BuildAttemptParams,
    BuildDispatch,
    BuildInProgress,
    BuildReleaseCommand,
    BuildStarted,
    BuildStepKey,
    BuildTarget,
    BuildWrite,
    CommandBus,
    CommandKey,
    DependencyUnavailable,
    Deployment,
    EnabledOperations,
    NotFound,
    OperationDeps,
    OverdueBuild,
    PollBuilds,
    PollOutcome,
    PollRoundParams,
    Principal,
    QueryBus,
    ReleaseKey,
    ReleaseNotBuildable,
    ReleaseWithSteps,
    RunningBuild,
    RunningBuilds,
    StartBuild,
    StartBuildParams,
    SweepOutcome,
    register_operations,
)
from provisioning.core.application.builds.background.poll_builds import (
    PendingOutcome,
    PendingStartResult,
    StartPendingBuilds,
)
from provisioning.core.application.builds.ports import PendingBuild, PendingBuilds
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildState,
    BuildStatus,
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.core.application.shared.action import Action
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditResult,
)
from provisioning.core.domain import (
    CODE_BUILD_ATTEMPTS_EXHAUSTED,
    CODE_BUILD_FAILED,
    CODE_BUILD_LOST,
    CODE_BUILD_REJECTED,
    CODE_BUILD_RESULT_MISMATCH,
    BuildAttemptPolicy,
    BuildResult,
    Command,
    CommandKind,
    CommandRequest,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ResultMismatch,
    apply_build_result,
    apply_failed_build_result,
    new_release,
    new_release_step,
    start_build_attempt,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
OTHER_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000002")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
STEP1 = UUID("0190a1b2-0000-7000-8000-0000000000e1")
SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
REPO = 4242
DIGEST = "sha256:" + "a" * 64
REGISTRY = "registry.webblock.test:5000"
IMAGE_REPOSITORY = f"{REGISTRY}/webblock/sites/{PROJECT}"
REPO_URL = f"http://webblock-git/{PROJECT}.git"
DURATION = timedelta(minutes=30)
GRACE = timedelta(minutes=2)
PENDING_GRACE = timedelta(seconds=30)
ACTOR = "build-worker"


class _Ids:
    def __init__(self) -> None:
        self.issued: list[UUID] = []

    def new_id(self) -> UUID:
        value = UUID(int=0x0190A1B2_0000_7000_8000_0000_0000_F000 + len(self.issued) + 1)
        self.issued.append(value)
        return value


class _Store:
    """An in-memory BuildStore: rows in dicts, the domain functions applied as the SQL store
    applies them, ``now`` controlled by the test."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.now = T0
        self.releases: dict[UUID, Release] = {}
        self.steps: dict[UUID, ReleaseStep] = {}
        self.fail_apply: Exception | None = None
        self.before_apply: Callable[[], None] | None = None

    def add_release(self, release: Release) -> None:
        self.releases[release.id] = release

    def add_step(self, step: ReleaseStep) -> None:
        self.steps[step.id] = step

    def build_steps(self, release_id: UUID) -> list[ReleaseStep]:
        return sorted(
            (
                s
                for s in self.steps.values()
                if s.release_id == release_id and s.name is ReleaseStepName.BUILD
            ),
            key=lambda s: s.attempt,
        )

    def latest(self, release_id: UUID = RELEASE) -> ReleaseStep:
        return self.build_steps(release_id)[-1]

    def _release(self, project_id: UUID, release_id: UUID) -> Release | None:
        r = self.releases.get(release_id)
        return r if r is not None and r.project_id == project_id else None

    async def start_attempt(self, params: StartBuildParams) -> BuildWrite:
        self.log.append("store.start_attempt")
        r = self._release(params.step.project_id, params.step.release_id)
        s = self.steps.get(params.step.step_id)
        if r is None or s is None or s.release_id != r.id:
            raise NotFound("build step")
        r2, s2 = start_build_attempt(r, s, self.now + params.duration, self.now)
        self.releases[r2.id], self.steps[s2.id] = r2, s2
        return BuildWrite(release=r2, step=s2)

    async def apply_result(self, result: BuildResult, attempt: BuildAttemptParams) -> BuildWrite:
        self.log.append(f"store.apply_result:{result.failure_code or 'success'}")
        if self.before_apply is not None:
            self.before_apply()
        if self.fail_apply is not None:
            raise self.fail_apply
        r = self._release(result.project_id, result.release_id)
        s = self.steps.get(result.step_id)
        if r is None or s is None:
            raise ResultMismatch("unknown")
        if result.failure_code:
            failed = apply_failed_build_result(
                r, s, result, attempt.policy, attempt.next_step_id, self.now
            )
            self.releases[r.id], self.steps[s.id] = failed.release, failed.step
            if failed.next_step is not None:
                self.steps[failed.next_step.id] = failed.next_step
            return BuildWrite(release=failed.release, step=failed.step, next_step=failed.next_step)
        r2, s2 = apply_build_result(r, s, result, self.now)
        self.releases[r2.id], self.steps[s2.id] = r2, s2
        return BuildWrite(release=r2, step=s2)

    async def scan_overdue(self, limit: int) -> list[OverdueBuild]:
        raise AssertionError("not used")

    async def sweep_overdue(
        self, candidate: OverdueBuild, attempt: BuildAttemptParams
    ) -> SweepOutcome:
        raise AssertionError("not used")

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        self.log.append("store.load_build_target")
        r = self._release(project_id, release_id)
        if r is None:
            raise NotFound("release")
        steps = self.build_steps(release_id)
        return BuildTarget(release=r, step=steps[-1] if steps else None)

    async def record_log_ref(self, step: BuildStepKey, log_ref: str) -> bool:
        self.log.append(f"store.record_log_ref:{log_ref}")
        s = self.steps.get(step.step_id)
        if s is None or s.status is not ReleaseStepStatus.RUNNING or s.log_ref is not None:
            return False
        self.steps[s.id] = dataclasses.replace(s, log_ref=log_ref)
        return True

    async def scan_running(self, limit: int) -> RunningBuilds:
        self.log.append("store.scan_running")
        running = sorted(
            (s for s in self.steps.values() if s.status is ReleaseStepStatus.RUNNING),
            key=lambda s: (s.started_at or T0, s.id),
        )[:limit]
        builds = []
        for s in running:
            r = self.releases[s.release_id]
            assert s.started_at is not None
            assert s.deadline_at is not None
            builds.append(
                RunningBuild(
                    step=BuildStepKey(project_id=r.project_id, release_id=r.id, step_id=s.id),
                    attempt=s.attempt,
                    git_commit_sha=r.git_commit_sha,
                    repository_id=r.repository_id,
                    started_at=s.started_at,
                    deadline_at=s.deadline_at,
                    log_ref=s.log_ref,
                )
            )
        return RunningBuilds(now=self.now, builds=tuple(builds))

    async def scan_pending(self, limit: int, older_than: timedelta) -> PendingBuilds:
        """Pending build steps of accepted or building releases, pending since the later of the
        release's last write and the end of the attempt before, older than ``older_than``."""
        self.log.append("store.scan_pending")
        found: list[PendingBuild] = []
        for s in self.steps.values():
            r = self.releases[s.release_id]
            if s.status is not ReleaseStepStatus.PENDING or r.status not in (
                ReleaseStatus.ACCEPTED,
                ReleaseStatus.BUILDING,
            ):
                continue
            ends = [
                p.finished_at
                for p in self.build_steps(r.id)
                if p.attempt < s.attempt and p.finished_at is not None
            ]
            since = max([r.updated_at, *ends])
            if since < self.now - older_than:
                key = BuildStepKey(project_id=r.project_id, release_id=r.id, step_id=s.id)
                found.append(PendingBuild(step=key, attempt=s.attempt, pending_since=since))
        found.sort(key=lambda b: (b.pending_since, b.step.step_id))
        return PendingBuilds(now=self.now, builds=tuple(found[:limit]))


class _Audit:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)

    def alert(self, event: AuditEvent) -> None:
        raise AssertionError("a build never alerts")

    def brief(self) -> list[tuple[str, str, str | None]]:
        """(action, result, failure_code) of each event, in order."""
        return [(e.action, e.result, e.failure_code) for e in self.events]


class _BuildSystem:
    """A fake build system. ``builds`` maps (release, attempt) to the handle of the build it
    queued, so ``find`` sees a build that ``start`` queued before a crash."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.builds: dict[tuple[UUID, int], BuildHandle] = {}
        self.requests: list[BuildRequest] = []
        self.statuses: dict[str, BuildStatus | Exception] = {}
        self.start_error: Exception | None = None
        self.find_error: Exception | None = None
        self.cancel_error: Exception | None = None
        self.cancelled: list[str] = []
        self.handle_ref: Callable[[BuildRequest], str] = lambda req: f"jenkins/build/{req.attempt}"

    async def start(self, request: BuildRequest) -> BuildHandle:
        self.log.append("bs.start")
        self.requests.append(request)
        if self.start_error is not None:
            raise self.start_error
        handle = BuildHandle(ref=self.handle_ref(request))
        self.builds[(request.release_id, request.attempt)] = handle
        return handle

    async def find(self, request: BuildRequest) -> BuildHandle | None:
        self.log.append("bs.find")
        self.requests.append(request)
        if self.find_error is not None:
            raise self.find_error
        return self.builds.get((request.release_id, request.attempt))

    async def status(self, handle: BuildHandle) -> BuildStatus:
        self.log.append(f"bs.status:{handle.ref}")
        answer = self.statuses.get(handle.ref, BuildStatus(state=BuildState.RUNNING))
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def cancel(self, handle: BuildHandle) -> None:
        """Aborts a queued or running build, which then reports FAILED as Jenkins' ABORTED does;
        a finished or unknown build is left as it is."""
        self.log.append(f"bs.cancel:{handle.ref}")
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(handle.ref)
        current = self.statuses.get(handle.ref, BuildStatus(state=BuildState.RUNNING))
        if isinstance(current, BuildStatus) and current.state in (
            BuildState.QUEUED,
            BuildState.RUNNING,
        ):
            self.statuses[handle.ref] = BuildStatus(
                state=BuildState.FAILED, failure_code="pipeline_failed"
            )


class _Locator:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.error: Exception | None = None

    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        self.log.append("locator")
        if self.error is not None:
            raise self.error
        assert repository_id == REPO
        return f"http://webblock-git/{project_id}.git"


class _World:
    def __init__(self, max_attempts: int = 3) -> None:
        self.log: list[str] = []
        self.store = _Store(self.log)
        self.bs = _BuildSystem(self.log)
        self.locator = _Locator(self.log)
        self.ids = _Ids()
        self.logger = logging.getLogger("test.build_dispatch")
        self.policy = BuildAttemptPolicy(max_attempts=max_attempts)
        self.audit = _Audit()

    def starter(self) -> StartBuild:
        return StartBuild(
            store=self.store,
            build_system=self.bs,
            repositories=self.locator,
            ids=self.ids,
            logger=self.logger,
            policy=self.policy,
            image_registry=REGISTRY,
            build_duration=DURATION,
            call_timeout=timedelta(seconds=5),
            audit=self.audit,
            actor=ACTOR,
        )

    def pending(self, grace: timedelta = PENDING_GRACE) -> StartPendingBuilds:
        return StartPendingBuilds(store=self.store, starter=self.starter(), grace=grace)

    def poller(self) -> PollBuilds:
        return PollBuilds(
            store=self.store,
            build_system=self.bs,
            repositories=self.locator,
            ids=self.ids,
            logger=self.logger,
            policy=self.policy,
            image_registry=REGISTRY,
            call_timeout=timedelta(seconds=5),
            dispatch_grace=GRACE,
            audit=self.audit,
            actor=ACTOR,
        )

    def accepted_release(self, project_id: UUID = PROJECT) -> None:
        self.store.add_release(
            new_release(
                release_id=RELEASE,
                project_id=project_id,
                generation_request_id=REQUEST,
                repository_id=REPO,
                git_commit_sha=SHA,
                now=T0,
            )
        )
        self.store.add_step(
            new_release_step(step_id=STEP1, release_id=RELEASE, name=ReleaseStepName.BUILD)
        )

    def key(self, step_id: UUID = STEP1) -> BuildStepKey:
        return BuildStepKey(project_id=PROJECT, release_id=RELEASE, step_id=step_id)

    def release(self) -> Release:
        return self.store.releases[RELEASE]


def _status_ok(**changes: object) -> BuildStatus:
    base = BuildStatus(
        state=BuildState.SUCCEEDED,
        project_id=PROJECT,
        release_id=RELEASE,
        attempt=1,
        commit_sha=SHA,
        image_digest=DIGEST,
    )
    return dataclasses.replace(base, **changes)  # type: ignore[arg-type]  # test-only field overrides


async def _started(world: _World) -> BuildStarted:
    world.accepted_release()
    return await world.starter().start(PROJECT, RELEASE)


ROUND = PollRoundParams(limit=10, concurrency=2)
HANDLE1 = "jenkins/build/1"

# --- StartBuild -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_build_runs_the_step_first_then_find_then_start_then_stores_the_handle() -> (
    None
):
    world = _World()
    started = await _started(world)

    assert started == BuildStarted(
        project_id=PROJECT,
        release_id=RELEASE,
        step_id=STEP1,
        attempt=1,
        dispatch=BuildDispatch.DISPATCHED,
    )
    assert world.log == [
        "store.load_build_target",
        "store.start_attempt",
        "locator",
        "bs.find",
        "bs.start",
        f"store.record_log_ref:{HANDLE1}",
    ]
    step = world.store.latest()
    assert step.status is ReleaseStepStatus.RUNNING
    assert step.started_at == T0
    assert step.deadline_at == T0 + DURATION
    assert step.log_ref == HANDLE1
    assert world.release().status is ReleaseStatus.BUILDING
    assert world.bs.requests[-1] == BuildRequest(
        project_id=PROJECT,
        release_id=RELEASE,
        attempt=1,
        repository_url=REPO_URL,
        commit_sha=SHA,
        image_repository=IMAGE_REPOSITORY,
    )


@pytest.mark.asyncio
async def test_a_duplicate_start_build_is_build_in_progress_and_starts_nothing() -> None:
    world = _World()
    await _started(world)
    with pytest.raises(BuildInProgress):
        await world.starter().start(PROJECT, RELEASE)
    assert world.log.count("bs.start") == 1
    assert len(world.bs.builds) == 1


@pytest.mark.asyncio
async def test_concurrent_start_builds_start_one_build() -> None:
    world = _World()
    world.accepted_release()
    starter = world.starter()
    results = await asyncio.gather(
        starter.start(PROJECT, RELEASE), starter.start(PROJECT, RELEASE), return_exceptions=True
    )
    assert sum(isinstance(r, BuildStarted) for r in results) == 1
    assert sum(isinstance(r, BuildInProgress) for r in results) == 1
    assert world.log.count("bs.start") == 1


@pytest.mark.asyncio
async def test_start_build_uses_a_build_that_find_already_knows() -> None:
    world = _World()
    world.accepted_release()
    world.bs.builds[(RELEASE, 1)] = BuildHandle(ref="jenkins/queue/7")
    started = await world.starter().start(PROJECT, RELEASE)
    assert started.dispatch is BuildDispatch.DISPATCHED
    assert "bs.start" not in world.log
    assert world.store.latest().log_ref == "jenkins/queue/7"


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["find", "start"])
async def test_build_system_unavailable_leaves_the_step_running_without_a_handle(
    where: str,
) -> None:
    world = _World()
    if where == "find":
        world.bs.find_error = BuildSystemUnavailable("down")
    else:
        world.bs.start_error = BuildSystemUnavailable("down")
    started = await _started(world)
    assert started.dispatch is BuildDispatch.PENDING
    step = world.store.latest()
    assert step.id == STEP1
    assert step.status is ReleaseStepStatus.RUNNING
    assert step.log_ref is None
    assert not any(e.startswith("store.record_log_ref") for e in world.log)


@pytest.mark.asyncio
async def test_a_build_system_call_past_its_timeout_counts_as_unavailable() -> None:
    world = _World()
    blocked = asyncio.Event()

    async def hang(request: BuildRequest) -> BuildHandle | None:
        await blocked.wait()
        return None

    world.bs.find = hang  # type: ignore[method-assign]  # a find that never answers
    world.accepted_release()
    starter = StartBuild(
        store=world.store,
        build_system=world.bs,
        repositories=world.locator,
        ids=world.ids,
        logger=world.logger,
        policy=world.policy,
        image_registry=REGISTRY,
        build_duration=DURATION,
        call_timeout=timedelta(milliseconds=10),
    )
    started = await starter.start(PROJECT, RELEASE)
    assert started.dispatch is BuildDispatch.PENDING
    assert world.store.latest().status is ReleaseStepStatus.RUNNING


@pytest.mark.asyncio
async def test_locator_unavailable_leaves_the_step_running_and_calls_no_build_system() -> None:
    world = _World()
    world.locator.error = DependencyUnavailable("project down")
    started = await _started(world)
    assert started.dispatch is BuildDispatch.PENDING
    assert not any(e.startswith("bs.") for e in world.log)
    assert world.store.latest().status is ReleaseStepStatus.RUNNING


@pytest.mark.asyncio
async def test_build_system_rejected_fails_the_attempt_and_queues_the_next() -> None:
    world = _World(max_attempts=3)
    world.bs.start_error = BuildSystemRejected("401")
    started = await _started(world)
    assert started.dispatch is BuildDispatch.REJECTED
    first = world.store.steps[STEP1]
    assert first.status is ReleaseStepStatus.FAILED
    assert first.failure_code == CODE_BUILD_REJECTED
    nxt = world.store.latest()
    assert (nxt.attempt, nxt.status) == (2, ReleaseStepStatus.PENDING)
    assert world.release().status is ReleaseStatus.BUILDING


@pytest.mark.asyncio
async def test_a_handle_that_is_not_a_valid_log_ref_fails_the_attempt_rejected() -> None:
    world = _World()
    world.bs.handle_ref = lambda req: "jenkins//build/1"
    started = await _started(world)
    assert started.dispatch is BuildDispatch.REJECTED
    assert world.store.steps[STEP1].failure_code == CODE_BUILD_REJECTED
    assert world.store.steps[STEP1].log_ref is None


@pytest.mark.asyncio
async def test_the_next_attempt_of_a_building_release_starts_through_the_endpoint() -> None:
    world = _World(max_attempts=3)
    world.bs.start_error = BuildSystemRejected("401")
    await _started(world)
    world.bs.start_error = None
    world.store.now = T0 + timedelta(minutes=1)

    started = await world.starter().start(PROJECT, RELEASE)

    assert (started.attempt, started.dispatch) == (2, BuildDispatch.DISPATCHED)
    assert started.step_id == world.store.latest().id
    assert world.bs.requests[-1].attempt == 2
    assert world.store.latest().log_ref == ("jenkins/build/2")


@pytest.mark.asyncio
async def test_a_rejected_last_attempt_fails_the_release() -> None:
    world = _World(max_attempts=1)
    world.bs.start_error = BuildSystemRejected("401")
    started = await _started(world)
    assert started.dispatch is BuildDispatch.REJECTED
    assert world.release().status is ReleaseStatus.FAILED
    assert world.release().failure_code == CODE_BUILD_ATTEMPTS_EXHAUSTED
    assert len(world.store.build_steps(RELEASE)) == 1
    with pytest.raises(ReleaseNotBuildable):
        await world.starter().start(PROJECT, RELEASE)


@pytest.mark.asyncio
async def test_start_build_of_a_missing_release_or_another_projects_release_is_not_found() -> None:
    world = _World()
    with pytest.raises(NotFound):
        await world.starter().start(PROJECT, RELEASE)
    world.accepted_release(project_id=OTHER_PROJECT)
    with pytest.raises(NotFound):
        await world.starter().start(PROJECT, RELEASE)
    assert not any(e.startswith("bs.") for e in world.log)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [ReleaseStatus.BUILT, ReleaseStatus.FAILED, ReleaseStatus.LIVE], ids=str
)
async def test_start_build_of_a_release_past_building_is_not_buildable(
    status: ReleaseStatus,
) -> None:
    world = _World()
    world.accepted_release()
    world.store.releases[RELEASE] = dataclasses.replace(world.release(), status=status)
    with pytest.raises(ReleaseNotBuildable):
        await world.starter().start(PROJECT, RELEASE)
    assert "store.start_attempt" not in world.log


@pytest.mark.asyncio
async def test_build_release_runs_the_operation_gate_before_the_build() -> None:
    calls: list[str] = []

    class Policy:
        def __init__(self, allow: bool) -> None:
            self.allow = allow

        def permit(self, principal: Principal, action: Action) -> None:
            calls.append(f"permit:{action}")
            if not self.allow:
                raise ActionDenied("no")

    class Access:
        async def check_access(
            self, principal: Principal, project_id: UUID, action: Action
        ) -> None:
            calls.append(f"access:{project_id}")

    class Starter:
        async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
            calls.append(f"start:{project_id}:{release_id}")
            return BuildStarted(
                project_id=project_id,
                release_id=release_id,
                step_id=STEP1,
                attempt=1,
                dispatch=BuildDispatch.DISPATCHED,
            )

    class Unused:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"unexpected call {name}")

    class Entitlement:
        async def check(self, project_id: UUID, kind: CommandKind) -> None:
            raise AssertionError("unexpected")

    class Acceptance:
        async def find_command_by_key(self, request: CommandRequest) -> Command | None:
            raise AssertionError("unexpected")

        async def accept_command(self, params: object, decide: object) -> object:
            raise AssertionError("unexpected")

    class Reads:
        async def read_release(self, key: ReleaseKey) -> ReleaseWithSteps:
            calls.append(f"read:{key.project_id}:{key.release_id}")
            release = new_release(
                release_id=key.release_id,
                project_id=key.project_id,
                generation_request_id=REQUEST,
                repository_id=REPO,
                git_commit_sha=SHA,
                now=T0,
            )
            return ReleaseWithSteps(release=release, steps=())

        async def read_command(self, key: CommandKey) -> Command:
            raise AssertionError("unexpected")

        async def read_deployment(self, project_id: UUID) -> Deployment:
            raise AssertionError("unexpected")

    class Clock:
        def now(self) -> datetime:
            return T0

    def commands(allow: bool) -> CommandBus:
        bus = CommandBus()
        register_operations(
            bus,
            QueryBus(),
            OperationDeps(
                policy=Policy(allow),
                access=Access(),
                entitlement=Entitlement(),
                acceptance=Acceptance(),  # type: ignore[arg-type]  # never called here
                reads=Reads(),
                clock=Clock(),
                ids=_Ids(),
                builds=Starter(),
                enabled=EnabledOperations([Action.BUILD_RELEASE]),
                command_deadline=timedelta(minutes=10),
            ),
        )
        return bus

    principal = Principal(subject="agent-1", scopes=("provisioning:build",))
    command = BuildReleaseCommand(principal, project_id=PROJECT, release_id=RELEASE)
    result = await commands(True).send(command)
    # The answer is the release read after the start (E1 report, concern 2), with no further
    # permission asked.
    assert (result.project_id, result.release_id) == (PROJECT, RELEASE)
    assert calls == [
        "permit:build_release",
        f"access:{PROJECT}",
        f"start:{PROJECT}:{RELEASE}",
        f"read:{PROJECT}:{RELEASE}",
    ]
    calls.clear()
    with pytest.raises(ActionDenied):
        await commands(False).send(command)
    assert calls == ["permit:build_release"]


# --- PollBuilds -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_waits_while_the_build_runs() -> None:
    world = _World()
    await _started(world)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.WAITING]
    assert world.store.latest().status is ReleaseStepStatus.RUNNING


@pytest.mark.asyncio
async def test_poll_accepts_a_matching_success_after_a_restart() -> None:
    """FR-11.1: poll accepts a matching success after a restart."""
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = _status_ok()
    world.store.now = T0 + timedelta(minutes=5)

    # A new PollBuilds, as after a restart of the build worker: only log_ref carries the build.
    results = await world.poller().poll_round(ROUND)

    assert [r.outcome for r in results] == [PollOutcome.SUCCEEDED]
    release = world.release()
    assert release.status is ReleaseStatus.BUILT
    assert release.container_image_digest == DIGEST
    step = world.store.latest()
    assert step.status is ReleaseStepStatus.SUCCEEDED
    assert step.log_ref == HANDLE1
    assert "bs.start" not in world.log[world.log.index("store.scan_running") :]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"commit_sha": OTHER_SHA},
        {"release_id": UUID("0190a1b2-0000-7000-8000-0000000000a9")},
        {"project_id": OTHER_PROJECT},
        {"attempt": 2},
        {"commit_sha": None},
        {"image_digest": "sha512:" + "a" * 128},
        {"image_digest": "a" * 64},
        {"image_digest": "sha256:" + "A" * 64},
        {"image_digest": None},
    ],
    ids=[
        "wrong_commit",
        "wrong_release",
        "wrong_project",
        "wrong_attempt",
        "no_commit",
        "other_algorithm",
        "bare_hex",
        "uppercase_digest",
        "no_digest",
    ],
)
async def test_poll_fails_the_attempt_on_a_result_that_does_not_match(
    changes: dict[str, object], caplog: pytest.LogCaptureFixture
) -> None:
    """FR-11.2: poll fails the attempt on a result that does not match."""
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = _status_ok(**changes)

    with caplog.at_level(logging.WARNING, logger="test.build_dispatch"):
        results = await world.poller().poll_round(ROUND)

    assert [r.outcome for r in results] == [PollOutcome.MISMATCH]
    failed = world.store.steps[STEP1]
    assert failed.status is ReleaseStepStatus.FAILED
    assert failed.failure_code == CODE_BUILD_RESULT_MISMATCH
    assert world.release().status is ReleaseStatus.BUILDING
    assert world.release().container_image_digest is None
    assert world.store.latest().attempt == 2
    text = caplog.text
    assert str(RELEASE) in text
    assert OTHER_SHA not in text
    assert SHA not in text
    assert DIGEST not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_code", "expected"),
    [
        ("commit_not_found", "commit_not_found"),
        ("build_failed", "build_failed"),
        ("push_failed", "push_failed"),
        (None, CODE_BUILD_FAILED),
        ("Not A Code; rm -rf", CODE_BUILD_FAILED),
        # Task H5a: only the catalogue's build codes are kept, so every stored code is listed.
        ("compile_error", CODE_BUILD_FAILED),
        ("candidate_refused", CODE_BUILD_FAILED),
    ],
    ids=[
        "commit_not_found",
        "build_failed",
        "push_failed",
        "no_code",
        "malformed_code",
        "unlisted_code",
        "not_a_build_code",
    ],
)
async def test_poll_fails_the_attempt_with_the_build_systems_code(
    failure_code: str | None, expected: str
) -> None:
    """FR-11.3, FR-11.5: poll fails the attempt with the build systems code."""
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = BuildStatus(state=BuildState.FAILED, failure_code=failure_code)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.FAILED]
    assert world.store.steps[STEP1].failure_code == expected


@pytest.mark.asyncio
async def test_poll_fails_a_lost_build() -> None:
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = BuildStatus(state=BuildState.LOST)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.FAILED]
    assert world.store.steps[STEP1].failure_code == CODE_BUILD_LOST


@pytest.mark.asyncio
async def test_poll_leaves_the_step_unchanged_while_the_build_system_is_unavailable() -> None:
    world = _World()
    await _started(world)
    before = dict(world.store.steps)
    world.bs.statuses[HANDLE1] = BuildSystemUnavailable("down")
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.UNAVAILABLE]
    assert world.store.steps == before
    assert not any(e.startswith("store.apply_result") for e in world.log)


@pytest.mark.asyncio
async def test_poll_fails_the_attempt_when_the_build_system_rejects_the_status_read() -> None:
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = BuildSystemRejected("foreign handle")
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.REJECTED]
    assert world.store.steps[STEP1].failure_code == CODE_BUILD_REJECTED


@pytest.mark.asyncio
async def test_poll_fails_the_release_when_its_last_attempt_fails() -> None:
    """FR-12.2: poll fails the release when its last attempt fails."""
    world = _World(max_attempts=1)
    await _started(world)
    world.bs.statuses[HANDLE1] = _status_ok(commit_sha=OTHER_SHA)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.MISMATCH]
    assert world.release().status is ReleaseStatus.FAILED
    assert world.release().failure_code == CODE_BUILD_ATTEMPTS_EXHAUSTED
    assert len(world.store.build_steps(RELEASE)) == 1


@pytest.mark.asyncio
async def test_poll_leaves_a_fresh_step_without_handle_to_start_build() -> None:
    world = _World()
    world.bs.start_error = BuildSystemUnavailable("down")
    await _started(world)
    world.bs.start_error = None
    mark = len(world.log)
    world.store.now = T0 + GRACE - timedelta(seconds=1)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.WAITING]
    assert not any(e.startswith("bs.") for e in world.log[mark:])


@pytest.mark.asyncio
async def test_poll_after_a_crash_between_start_and_storing_the_handle_finds_the_build() -> None:
    world = _World()
    world.accepted_release()
    starter = world.starter()

    original = world.store.record_log_ref

    async def crash(step: BuildStepKey, log_ref: str) -> bool:
        raise RuntimeError("process killed")

    world.store.record_log_ref = crash  # type: ignore[method-assign]  # the crash
    with pytest.raises(RuntimeError):
        await starter.start(PROJECT, RELEASE)
    world.store.record_log_ref = original  # type: ignore[method-assign]  # restarted process
    assert world.store.latest().log_ref is None
    assert len(world.bs.builds) == 1

    world.store.now = T0 + GRACE
    results = await world.poller().poll_round(ROUND)

    assert [r.outcome for r in results] == [PollOutcome.DISPATCHED]
    assert world.log.count("bs.start") == 1
    assert world.store.latest().log_ref == HANDLE1


@pytest.mark.asyncio
async def test_poll_starts_the_build_when_nothing_was_started() -> None:
    world = _World()
    world.bs.find_error = BuildSystemUnavailable("down")
    await _started(world)
    world.bs.find_error = None
    world.store.now = T0 + GRACE
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.DISPATCHED]
    assert world.log.count("bs.start") == 1
    assert world.store.latest().log_ref == HANDLE1


@pytest.mark.asyncio
async def test_poll_fails_the_attempt_when_the_start_is_rejected() -> None:
    world = _World()
    world.bs.find_error = BuildSystemUnavailable("down")
    await _started(world)
    world.bs.find_error = None
    world.bs.start_error = BuildSystemRejected("401")
    world.store.now = T0 + GRACE
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.REJECTED]
    assert world.store.steps[STEP1].failure_code == CODE_BUILD_REJECTED


@pytest.mark.asyncio
async def test_poll_reports_a_step_that_stopped_running_as_stale() -> None:
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = _status_ok()

    def swept() -> None:
        # The sweeper failed the attempt between the scan and the write.
        s = world.store.steps[STEP1]
        world.store.steps[STEP1] = dataclasses.replace(
            s,
            status=ReleaseStepStatus.FAILED,
            finished_at=world.store.now,
            failure_code="deadline_exceeded",
        )

    world.store.before_apply = swept
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.STALE]
    assert world.release().status is ReleaseStatus.BUILDING


@pytest.mark.asyncio
async def test_poll_reports_a_store_error_per_step_and_handles_the_others() -> None:
    world = _World()
    await _started(world)
    other_release = UUID("0190a1b2-0000-7000-8000-0000000000a2")
    other_step = UUID("0190a1b2-0000-7000-8000-0000000000e2")
    world.store.add_release(
        new_release(
            release_id=other_release,
            project_id=PROJECT,
            generation_request_id=UUID("0190a1b2-0000-7000-8000-0000000000d2"),
            repository_id=REPO,
            git_commit_sha=SHA,
            now=T0,
        )
    )
    world.store.add_step(
        new_release_step(step_id=other_step, release_id=other_release, name=ReleaseStepName.BUILD)
    )
    world.store.now = T0 + timedelta(seconds=1)
    world.bs.handle_ref = lambda req: f"jenkins/build/other-{req.attempt}"
    await world.starter().start(PROJECT, other_release)
    world.bs.statuses[HANDLE1] = _status_ok()
    world.store.fail_apply = RuntimeError("connection reset")

    results = await world.poller().poll_round(ROUND)

    assert [(r.step.step_id, r.outcome) for r in results] == [
        (STEP1, PollOutcome.ERROR),
        (other_step, PollOutcome.WAITING),
    ]
    assert isinstance(results[0].error, RuntimeError)


@pytest.mark.parametrize(
    ("limit", "concurrency"),
    [(0, 1), (1001, 1), (10, 0), (10, 11)],
    ids=["zero_limit", "limit_over_max", "zero_concurrency", "concurrency_over_limit"],
)
def test_poll_round_params_are_validated(limit: int, concurrency: int) -> None:
    with pytest.raises(ValueError, match="poll round"):
        PollRoundParams(limit=limit, concurrency=concurrency).validate()


def test_poll_round_params_accept_the_bounds() -> None:
    PollRoundParams(limit=1000, concurrency=1000).validate()
    PollRoundParams(limit=1, concurrency=1).validate()


# --- the pending attempts nothing started (QA-10, OD-51) -------------------------------------


async def _fail_running_attempt(world: _World, code: str = "build_failed") -> None:
    """The running attempt's build fails; the poll applies it (the next attempt is queued)."""
    step = world.store.latest()
    assert step.log_ref is not None
    world.bs.statuses[step.log_ref] = BuildStatus(state=BuildState.FAILED, failure_code=code)
    results = await world.poller().poll_round(ROUND)
    assert [r.outcome for r in results] == [PollOutcome.FAILED]


@pytest.mark.asyncio
async def test_qa10_the_next_attempt_is_started_once_it_was_pending_for_the_grace() -> None:
    world = _World()
    await _started(world)
    world.store.now = T0 + timedelta(minutes=1)
    await _fail_running_attempt(world)
    assert world.store.latest().attempt == 2
    assert world.store.latest().status is ReleaseStepStatus.PENDING
    pending = world.pending()

    world.store.now = T0 + timedelta(minutes=1) + PENDING_GRACE - timedelta(microseconds=1)
    assert await pending.start_round(ROUND) == []
    assert world.store.latest().status is ReleaseStepStatus.PENDING

    world.store.now = T0 + timedelta(minutes=1) + PENDING_GRACE + timedelta(microseconds=1)
    results = await pending.start_round(ROUND)

    assert [(r.build.attempt, r.outcome) for r in results] == [(2, PendingOutcome.STARTED)]
    step = world.store.latest()
    assert step.attempt == 2
    assert step.status is ReleaseStepStatus.RUNNING
    assert step.log_ref == "jenkins/build/2"
    assert world.bs.requests[-1].attempt == 2
    assert world.release().status is ReleaseStatus.BUILDING


@pytest.mark.asyncio
async def test_qa10_every_attempt_failing_ends_the_release_failed_never_building() -> None:
    world = _World(max_attempts=3)
    await _started(world)
    pending = world.pending()
    for attempt in (1, 2, 3):
        assert world.store.latest().attempt == attempt
        await _fail_running_attempt(world)
        world.store.now += PENDING_GRACE + timedelta(seconds=1)
        started = await pending.start_round(ROUND)
        if attempt < 3:
            assert [r.outcome for r in started] == [PendingOutcome.STARTED]
        else:
            assert started == []
    release = world.release()
    assert release.status is ReleaseStatus.FAILED
    assert release.failure_code == CODE_BUILD_ATTEMPTS_EXHAUSTED
    assert [s.status for s in world.store.build_steps(RELEASE)] == [ReleaseStepStatus.FAILED] * 3
    assert world.log.count("bs.start") == 3


@pytest.mark.asyncio
async def test_an_accepted_release_whose_first_attempt_was_never_started_is_started() -> None:
    """The intake accepted the release and its process stopped before StartBuild."""
    world = _World()
    world.accepted_release()
    world.store.now = T0 + PENDING_GRACE + timedelta(seconds=1)
    results = await world.pending().start_round(ROUND)
    assert [(r.build.step.step_id, r.outcome) for r in results] == [(STEP1, PendingOutcome.STARTED)]
    assert world.release().status is ReleaseStatus.BUILDING
    assert world.store.latest().log_ref == HANDLE1


@pytest.mark.asyncio
async def test_a_pending_attempt_started_in_between_is_stale_and_starts_no_second_build() -> None:
    world = _World()
    world.accepted_release()
    world.store.now = T0 + PENDING_GRACE + timedelta(seconds=1)
    pending = world.pending()
    original = world.store.scan_pending

    async def scan_then_endpoint_start(limit: int, older_than: timedelta) -> PendingBuilds:
        scanned = await original(limit, older_than)
        await world.starter().start(PROJECT, RELEASE)  # build_release wins the race
        return scanned

    world.store.scan_pending = scan_then_endpoint_start  # type: ignore[method-assign]  # the race
    results = await pending.start_round(ROUND)
    assert [r.outcome for r in results] == [PendingOutcome.STALE]
    assert world.log.count("bs.start") == 1


@pytest.mark.asyncio
async def test_a_pending_start_while_the_build_system_is_down_leaves_it_to_the_poll() -> None:
    world = _World()
    world.accepted_release()
    world.bs.find_error = BuildSystemUnavailable("down")
    world.store.now = T0 + PENDING_GRACE + timedelta(seconds=1)
    results = await world.pending().start_round(ROUND)
    assert [r.outcome for r in results] == [PendingOutcome.UNAVAILABLE]
    # The step runs without a handle; the poll dispatches it once the build system is back.
    assert world.store.latest().status is ReleaseStepStatus.RUNNING
    world.bs.find_error = None
    world.store.now += GRACE
    assert [r.outcome for r in await world.poller().poll_round(ROUND)] == [PollOutcome.DISPATCHED]


@pytest.mark.asyncio
async def test_a_pending_start_the_build_system_refuses_fails_the_attempt() -> None:
    world = _World()
    world.accepted_release()
    world.bs.start_error = BuildSystemRejected("401")
    world.store.now = T0 + PENDING_GRACE + timedelta(seconds=1)
    results = await world.pending().start_round(ROUND)
    assert [r.outcome for r in results] == [PendingOutcome.REJECTED]
    assert world.store.steps[STEP1].failure_code == CODE_BUILD_REJECTED
    assert world.store.latest().attempt == 2


@pytest.mark.asyncio
async def test_a_pending_start_error_is_its_result_and_the_others_go_on() -> None:
    world = _World()
    world.accepted_release()
    other_release = UUID("0190a1b2-0000-7000-8000-0000000000a2")
    world.store.add_release(
        new_release(
            release_id=other_release,
            project_id=PROJECT,
            generation_request_id=UUID("0190a1b2-0000-7000-8000-0000000000d2"),
            repository_id=REPO,
            git_commit_sha=SHA,
            now=T0 + timedelta(seconds=1),
        )
    )
    world.store.add_step(
        new_release_step(
            step_id=UUID("0190a1b2-0000-7000-8000-0000000000e2"),
            release_id=other_release,
            name=ReleaseStepName.BUILD,
        )
    )
    world.store.now = T0 + timedelta(minutes=5)
    original = world.store.start_attempt
    calls = 0

    async def first_fails(params: StartBuildParams) -> BuildWrite:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("connection reset")
        return await original(params)

    world.store.start_attempt = first_fails  # type: ignore[method-assign]  # one store failure
    results = await world.pending().start_round(ROUND)
    assert [(r.build.step.release_id, r.outcome) for r in results] == [
        (RELEASE, PendingOutcome.ERROR),
        (other_release, PendingOutcome.STARTED),
    ]
    assert isinstance(results[0].error, RuntimeError)


@pytest.mark.asyncio
async def test_the_pending_scan_asks_for_the_grace_and_the_round_limit() -> None:
    world = _World()
    seen: list[tuple[int, timedelta]] = []

    async def scan(limit: int, older_than: timedelta) -> PendingBuilds:
        seen.append((limit, older_than))
        return PendingBuilds(now=T0, builds=())

    world.store.scan_pending = scan  # type: ignore[method-assign]  # observe the arguments
    assert await world.pending(timedelta(seconds=45)).start_round(ROUND) == []
    assert seen == [(ROUND.limit, timedelta(seconds=45))]


@pytest.mark.parametrize("grace", [timedelta(0), timedelta(seconds=-1)])
def test_the_pending_grace_must_be_positive(grace: timedelta) -> None:
    with pytest.raises(ValueError, match="pending grace"):
        _World().pending(grace)


@pytest.mark.asyncio
async def test_the_pending_round_refuses_invalid_params_before_the_scan() -> None:
    world = _World()
    with pytest.raises(ValueError, match="poll round"):
        await world.pending().start_round(PollRoundParams(limit=0, concurrency=1))
    assert "store.scan_pending" not in world.log


def test_a_pending_start_result_carries_no_error_by_default() -> None:
    build = PendingBuild(step=_World().key(), attempt=1, pending_since=T0)
    assert PendingStartResult(build=build, outcome=PendingOutcome.STALE).error is None


# --- audit of the build mutations ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dispatched_start_is_audited_as_an_attempt_started() -> None:
    world = _World()
    await _started(world)
    [event] = world.audit.events
    assert event == AuditEvent(
        actor_kind=AuditActorKind.SYSTEM,
        actor=ACTOR,
        action=AuditAction.BUILD_ATTEMPT_STARTED,
        project_id=PROJECT,
        release_id=RELEASE,
        command_id=None,
        result=AuditResult.SUCCEEDED,
        failure_code=None,
        at=T0,
        correlation_id=None,
    )


@pytest.mark.asyncio
async def test_a_start_the_build_system_could_not_take_is_audited_as_accepted() -> None:
    world = _World()
    world.bs.find_error = BuildSystemUnavailable("down")
    await _started(world)
    assert world.audit.brief() == [("build_attempt_started", "accepted", None)]


@pytest.mark.asyncio
async def test_a_refused_start_is_audited_as_started_failed_and_finished_failed() -> None:
    world = _World()
    world.bs.start_error = BuildSystemRejected("401")
    await _started(world)
    assert world.audit.brief() == [
        ("build_attempt_finished", "failed", CODE_BUILD_REJECTED),
        ("build_attempt_started", "failed", CODE_BUILD_REJECTED),
    ]


@pytest.mark.asyncio
async def test_a_start_that_never_ran_records_nothing() -> None:
    world = _World()
    await _started(world)
    world.audit.events.clear()
    with pytest.raises(BuildInProgress):
        await world.starter().start(PROJECT, RELEASE)
    assert world.audit.events == []


@pytest.mark.asyncio
async def test_a_successful_build_is_audited_as_finished_succeeded_at_the_database_time() -> None:
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = _status_ok()
    world.store.now = T0 + timedelta(minutes=3)
    await world.poller().poll_round(ROUND)
    finished = world.audit.events[-1]
    assert (finished.action, finished.result, finished.failure_code) == (
        AuditAction.BUILD_ATTEMPT_FINISHED,
        AuditResult.SUCCEEDED,
        None,
    )
    assert finished.at == T0 + timedelta(minutes=3)
    assert (finished.project_id, finished.release_id) == (PROJECT, RELEASE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (BuildStatus(state=BuildState.FAILED, failure_code="commit_not_found"), "commit_not_found"),
        (BuildStatus(state=BuildState.LOST), CODE_BUILD_LOST),
        (_status_ok(commit_sha=OTHER_SHA), CODE_BUILD_RESULT_MISMATCH),
    ],
    ids=["failed", "lost", "mismatch"],
)
async def test_a_failed_build_is_audited_as_finished_failed_with_its_code(
    status: BuildStatus, code: str
) -> None:
    world = _World()
    await _started(world)
    world.bs.statuses[HANDLE1] = status
    await world.poller().poll_round(ROUND)
    assert world.audit.brief()[-1] == ("build_attempt_finished", "failed", code)


@pytest.mark.asyncio
async def test_a_poll_that_only_waits_records_nothing() -> None:
    world = _World()
    await _started(world)
    world.audit.events.clear()
    await world.poller().poll_round(ROUND)
    assert world.audit.events == []


@pytest.mark.asyncio
async def test_the_api_default_actor_and_a_null_audit_log() -> None:
    """The API host builds StartBuild without the build worker's actor; until it passes its
    audit log the default drops the events (W6-D wires it)."""
    world = _World()
    world.accepted_release()
    starter = StartBuild(
        store=world.store,
        build_system=world.bs,
        repositories=world.locator,
        ids=world.ids,
        logger=world.logger,
        policy=world.policy,
        image_registry=REGISTRY,
        build_duration=DURATION,
        call_timeout=timedelta(seconds=5),
    )
    started = await starter.start(PROJECT, RELEASE)
    assert started.dispatch is BuildDispatch.DISPATCHED
    assert StartBuild.DEFAULT_ACTOR == "api"
