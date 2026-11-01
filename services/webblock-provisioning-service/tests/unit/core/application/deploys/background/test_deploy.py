"""DeployCandidate, the deploy command's use case (decisions 0011 sections 3, 4 and 12, 0013
section 3, 0014 sections 3 and 4).

The fakes: an in-memory deploy store that applies the domain's candidate functions to its rows, as
the PostgreSQL store does under its locks; a scripted cluster; a clock that the pauser advances.
Store writes and cluster calls go to one shared log, so their order is observable.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application.deploys.background.deploy import (
    DeployCandidate,
    DeployParams,
)
from provisioning.core.application.deploys.ports import (
    CandidateAttempt,
    CandidateAttemptStart,
    DeployFacts,
)
from provisioning.core.application.jobs.claims import ClaimLost, PermanentFailure, RetryLater
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    SiteInventory,
    SiteObject,
    SiteObjectKind,
    TenantNotReady,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.domain import (
    MAX_CANDIDATE_ATTEMPTS,
    CandidateFailure,
    CandidatePlanKind,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    IllegalTransition,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    ReplacedCandidateInput,
    RoutingStatus,
    StaleFence,
    deploy_failure_summary,
    end_candidate_attempt,
    exhaust_candidate_attempts,
    new_command,
    plan_candidate_attempt,
    return_replaced_candidate,
    start_candidate_attempt,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
OLD_CANDIDATE = UUID("0190a1b2-0000-7000-8000-0000000000a0")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
OLD_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e0")
DIGEST = "sha256:" + "b" * 64
SHA = "0123456789abcdef0123456789abcdef01234567"
REGISTRY = "registry.webblock.test:5000"
IMAGE = f"{REGISTRY}/webblock/sites/{PROJECT}@{DIGEST}"
NUMBER = 4
FENCE = Fence(project_id=PROJECT, command_id=COMMAND, command_number=NUMBER, claim_number=1)
DEADLINE = T0 + timedelta(minutes=30)
WORKLOAD = ReleaseWorkload(
    project_id=PROJECT, release_id=RELEASE, image=IMAGE, command_number=NUMBER
)

PARAMS = DeployParams(
    image_registry=REGISTRY,
    ready_timeout=timedelta(minutes=5),
    poll_interval=timedelta(seconds=10),
    call_timeout=timedelta(seconds=5),
    delete_timeout=timedelta(seconds=30),
    finish_margin=timedelta(minutes=2),
    claim_ttl=timedelta(minutes=1),
)

READY = WorkloadStatus(WorkloadState.READY)
PROGRESSING = WorkloadStatus(WorkloadState.PROGRESSING)


class _Clock:
    def __init__(self) -> None:
        self.at = T0

    def now(self) -> datetime:
        return self.at


class _Pauser:
    def __init__(self, clock: _Clock, log: list[str]) -> None:
        self.clock = clock
        self.log = log
        self.pauses: list[timedelta] = []

    async def pause(self, delay: timedelta) -> None:
        assert delay > timedelta(0)
        self.pauses.append(delay)
        self.clock.at += delay


class _Ids:
    def __init__(self) -> None:
        self.issued: list[UUID] = []

    def new_id(self) -> UUID:
        value = UUID(int=0x0190A1B2_0000_7000_8000_0000_0000_F000 + len(self.issued) + 1)
        self.issued.append(value)
        return value


def _release(release_id: UUID = RELEASE, status: ReleaseStatus = ReleaseStatus.BUILT) -> Release:
    return Release(
        id=release_id,
        project_id=PROJECT,
        generation_request_id=REQUEST,
        repository_id=4242,
        git_commit_sha=SHA,
        status=status,
        created_at=T0 - timedelta(hours=1),
        updated_at=T0 - timedelta(hours=1),
        container_image_digest=None if status is ReleaseStatus.ACCEPTED else DIGEST,
    )


def _command() -> Command:
    command = new_command(
        command_id=COMMAND,
        request=CommandRequest(
            project_id=PROJECT, kind=CommandKind.DEPLOY, release_id=RELEASE, idempotency_key="k-1"
        ),
        requested_by_subject="user-7f3a",
        command_number=NUMBER,
        now=T0,
    )
    return dataclasses.replace(command, status=CommandStatus.RUNNING)


class _Store:
    """An in-memory DeployStore: the domain functions applied as the SQL store applies them."""

    def __init__(self, clock: _Clock, log: list[str]) -> None:
        self.clock = clock
        self.log = log
        self.hosting = ProjectHosting(
            project_id=PROJECT,
            last_command_number=NUMBER,
            routing_status=RoutingStatus.STABLE,
            hosting_status=HostingStatus.ONLINE,
            updated_at=T0,
            subdomain="shop",
        )
        self.command = _command()
        self.releases: dict[UUID, Release] = {RELEASE: _release()}
        self.steps: dict[UUID, ReleaseStep] = {}
        self.claim_lost = False
        self.lose_claim_at_renewal: int | None = None
        self.renewals = 0
        self.writes = 0

    # --- the three checks
    def _check(self, fence: Fence) -> None:
        if fence.command_number != self.hosting.last_command_number:
            raise StaleFence("stale")
        if self.claim_lost:
            raise ClaimLost("lost")
        if self.command.status is not CommandStatus.RUNNING:
            raise IllegalTransition("not running")

    def _last_step(self) -> ReleaseStep | None:
        steps = [s for s in self.steps.values() if s.release_id == RELEASE]
        return max(steps, key=lambda s: s.attempt, default=None)

    def _input(self) -> OutcomeInput:
        return OutcomeInput(
            hosting=self.hosting, command=self.command, release=self.releases[RELEASE]
        )

    def _apply(self, outcome_command: Command, release: Release | None) -> None:
        self.command = outcome_command
        if release is not None:
            self.releases[release.id] = release

    # --- the port
    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        self.log.append("load")
        self._check(fence)
        return DeployFacts(
            hosting=self.hosting,
            command=self.command,
            release=self.releases[RELEASE],
            last_candidate_step=self._last_step(),
        )

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        assert ttl == PARAMS.claim_ttl
        self.renewals += 1
        if self.lose_claim_at_renewal is not None and self.renewals >= self.lose_claim_at_renewal:
            self.claim_lost = True
        self._check(fence)
        self.log.append("renew")

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        self._check(fence)
        now = self.clock.now()
        plan = plan_candidate_attempt(self.releases[RELEASE], self._last_step(), start.step_id, now)
        self.writes += 1
        if plan.ended is not None:
            self.steps[plan.ended.id] = plan.ended
        if plan.kind is CandidatePlanKind.EXHAUSTED:
            out = exhaust_candidate_attempts(self._input(), now)
            self._apply(out.command, out.release)
            self.log.append("exhausted")
            return CandidateAttempt(plan.kind)
        assert plan.step is not None
        step = plan.step
        if plan.kind is CandidatePlanKind.START:
            step = start_candidate_attempt(
                self.releases[RELEASE], plan.step, now + start.timeout, now
            )
            self.steps[step.id] = step
        self.log.append(f"begin:{plan.kind}:{step.attempt}")
        return CandidateAttempt(plan.kind, step)

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        self._check(fence)
        returned: list[UUID] = []
        for rid, rel in list(self.releases.items()):
            if rid != RELEASE and rel.status is ReleaseStatus.CANDIDATE_READY:
                inp = ReplacedCandidateInput(
                    hosting=self.hosting, command=self.command, release=rel
                )
                self.releases[rid] = return_replaced_candidate(inp, self.clock.now())
                returned.append(rid)
        self.log.append(f"replaced:{len(returned)}")
        return tuple(returned)

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        self._check(fence)
        end = end_candidate_attempt(
            self._input(), self.steps[step_id], failure_code, self.clock.now()
        )
        self.writes += 1
        self.steps[step_id] = end.step
        if end.outcome is not None:
            self._apply(end.outcome.command, end.outcome.release)
        self.log.append(f"end:{failure_code}")
        return end.failure


class _Cluster:
    """A scripted SiteCluster: ``statuses`` are returned in order (the last one repeats); an
    entry that is an exception is raised instead."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.apply_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.statuses: list[WorkloadStatus | Exception] = [READY]
        self.applied: list[ReleaseWorkload] = []
        self.status_reads = 0
        self.deleted: list[tuple[UUID, UUID, int]] = []
        self.reclaimed: list[tuple[UUID, RouteTarget, int]] = []
        self.hang_status = False

    async def apply_release(self, workload: ReleaseWorkload) -> None:
        self.log.append("apply")
        self.applied.append(workload)
        if self.apply_error is not None:
            raise self.apply_error

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        raise AssertionError("a deploy never restarts a release")

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        self.log.append("status")
        assert workload == WORKLOAD
        if self.hang_status:
            self.hang_status = False
            await asyncio.Event().wait()
        entry = self.statuses[min(self.status_reads, len(self.statuses) - 1)]
        self.status_reads += 1
        if isinstance(entry, Exception):
            raise entry
        return entry

    async def delete_release(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        self.log.append(f"delete:{release_id}")
        self.deleted.append((project_id, release_id, command_number))
        if self.delete_error is not None:
            raise self.delete_error

    async def apply_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        raise AssertionError("a deploy never writes the route")

    async def route_status(self, project_id: UUID) -> RouteStatus:
        raise AssertionError("a deploy never reads the route")

    async def apply_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        hostname: str,
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        raise AssertionError("a deploy never writes the preview route")

    async def preview_status(self, project_id: UUID) -> RouteStatus:
        raise AssertionError("a deploy never reads the preview route")

    async def probe_site(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        *,
        preview_of: str | None = None,
    ) -> bool:
        raise AssertionError("a deploy never probes the site")

    async def list_tenant_projects(self) -> tuple[UUID, ...]:
        self.log.append("list_tenant_projects")
        return (PROJECT,)

    async def inventory(self, project_id: UUID) -> SiteInventory:
        """A Deployment and a Service for each applied workload that was not deleted since."""
        self.log.append("inventory")
        deleted = {release_id for _, release_id, _ in self.deleted}
        present = {w.release_id: w for w in self.applied if w.release_id not in deleted}
        return SiteInventory(
            project_id=project_id,
            objects=tuple(
                SiteObject(kind, f"rel-{w.release_id}", w.release_id, w.command_number, ())
                for w in present.values()
                for kind in (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE)
            ),
        )

    async def reclaim_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        self.log.append(f"reclaim_route:{command_number}")
        self.reclaimed.append((project_id, target, command_number))


@dataclasses.dataclass
class _World:
    log: list[str]
    clock: _Clock
    store: _Store
    cluster: _Cluster
    pauser: _Pauser
    ids: _Ids
    deploy: DeployCandidate

    def step(self) -> ReleaseStep:
        (only,) = self.store.steps.values()
        return only


def _world(params: DeployParams = PARAMS) -> _World:
    log: list[str] = []
    clock = _Clock()
    store = _Store(clock, log)
    cluster = _Cluster(log)
    pauser = _Pauser(clock, log)
    ids = _Ids()
    deploy = DeployCandidate(
        store=store,
        cluster=cluster,
        clock=clock,
        ids=ids,
        pauser=pauser,
        params=params,
        logger=logging.getLogger("test.deploy"),
    )
    return _World(log, clock, store, cluster, pauser, ids, deploy)


@pytest.fixture
def w() -> Iterator[_World]:
    yield _world()


def _assert_command(w: _World, status: CommandStatus, code: str | None) -> None:
    assert (w.store.command.status, w.store.command.failure_code) == (status, code)


# --- success ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_on_the_first_poll(w: _World) -> None:
    """FR-17.1: ready on the first poll."""
    await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.applied == [WORKLOAD]
    assert w.store.releases[RELEASE].status is ReleaseStatus.CANDIDATE_READY
    _assert_command(w, CommandStatus.SUCCEEDED, None)
    step = w.step()
    assert (step.name, step.attempt, step.status) == (
        ReleaseStepName.CANDIDATE,
        1,
        ReleaseStepStatus.SUCCEEDED,
    )
    assert w.cluster.deleted == []
    assert w.log == [
        "load",
        "begin:start:1",
        "replaced:0",
        "renew",
        "apply",
        "renew",
        "status",
        "end:None",
    ]


@pytest.mark.asyncio
async def test_ready_after_progressing_renews_the_claim_before_every_poll(w: _World) -> None:
    w.cluster.statuses = [PROGRESSING, PROGRESSING, PROGRESSING, READY]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.status_reads == 4
    assert w.pauser.pauses == [PARAMS.poll_interval] * 3
    polls = [i for i, entry in enumerate(w.log) if entry == "status"]
    assert all(w.log[i - 1] == "renew" for i in polls)
    _assert_command(w, CommandStatus.SUCCEEDED, None)
    assert w.store.releases[RELEASE].status is ReleaseStatus.CANDIDATE_READY


@pytest.mark.asyncio
async def test_the_attempt_deadline_is_the_ready_timeout(w: _World) -> None:
    await w.deploy.run(FENCE, DEADLINE)
    step = w.step()
    assert step.started_at == T0
    assert step.deadline_at == T0 + PARAMS.ready_timeout


@pytest.mark.asyncio
async def test_a_transient_status_read_failure_is_polled_through(w: _World) -> None:
    w.cluster.statuses = [ClusterUnavailable("5xx"), TenantNotReady("403"), READY]
    await w.deploy.run(FENCE, DEADLINE)
    _assert_command(w, CommandStatus.SUCCEEDED, None)


@pytest.mark.asyncio
async def test_a_hanging_status_read_is_bounded_by_the_call_timeout() -> None:
    w = _world(dataclasses.replace(PARAMS, call_timeout=timedelta(milliseconds=50)))
    w.cluster.hang_status = True
    await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.status_reads == 1  # the hanging read never returned; the second one did
    _assert_command(w, CommandStatus.SUCCEEDED, None)


# --- the candidate fails ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_never_ready_candidate_fails_at_the_deadline_and_is_deleted(w: _World) -> None:
    """FR-17.3: a never ready candidate fails at the deadline and is deleted."""
    w.cluster.statuses = [PROGRESSING]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.clock.now() == T0 + PARAMS.ready_timeout
    assert w.step().failure_code == "candidate_not_ready"
    _assert_command(w, CommandStatus.FAILED, "candidate_not_ready")
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]
    assert w.log.index(f"delete:{RELEASE}") < w.log.index("end:candidate_not_ready")


@pytest.mark.asyncio
async def test_an_image_pull_failure_ends_the_attempt_at_once(w: _World) -> None:
    w.cluster.statuses = [PROGRESSING, WorkloadStatus(WorkloadState.FAILED, "image_pull_failed")]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.status_reads == 2
    assert w.step().failure_code == "image_pull_failed"
    _assert_command(w, CommandStatus.FAILED, "image_pull_failed")
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (WorkloadStatus(WorkloadState.FAILED, "crash_loop"), "crash_loop"),
        (
            WorkloadStatus(WorkloadState.FAILED, "progress_deadline_exceeded"),
            "progress_deadline_exceeded",
        ),
        (WorkloadStatus(WorkloadState.FAILED, "something_else"), "candidate_not_ready"),
        (WorkloadStatus(WorkloadState.FAILED, None), "candidate_not_ready"),
        (WorkloadStatus(WorkloadState.FAILED, "tenant_not_ready"), "candidate_not_ready"),
        (WorkloadStatus(WorkloadState.MISSING), "candidate_not_ready"),
    ],
)
async def test_other_terminal_states_end_the_attempt(
    w: _World, status: WorkloadStatus, code: str
) -> None:
    w.cluster.statuses = [status]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.step().failure_code == code
    _assert_command(w, CommandStatus.FAILED, code)
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["insufficient_resources", "quota_exceeded"])
async def test_a_precise_cause_at_the_progress_deadline_is_the_attempts_code(
    w: _World, code: str
) -> None:
    """Task H5a: the adapter's FAILED reason is the precise cause; the outcome is unchanged."""
    w.cluster.statuses = [PROGRESSING, WorkloadStatus(WorkloadState.FAILED, code)]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.step().failure_code == code
    _assert_command(w, CommandStatus.FAILED, code)
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["insufficient_resources", "quota_exceeded"])
async def test_a_diagnosis_while_progressing_does_not_end_the_attempt_early(
    w: _World, code: str
) -> None:
    """Task H5a, lead's condition: a PROGRESSING status with a diagnosis is read to the
    attempt's deadline like any other PROGRESSING status, and only then names the code."""
    diagnosed = WorkloadStatus(WorkloadState.PROGRESSING, code)
    w.cluster.statuses = [diagnosed]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.clock.now() == T0 + PARAMS.ready_timeout
    assert w.cluster.status_reads > 2
    assert w.step().failure_code == code
    _assert_command(w, CommandStatus.FAILED, code)
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]
    assert w.log.index(f"delete:{RELEASE}") < w.log.index(f"end:{code}")


@pytest.mark.asyncio
async def test_a_diagnosis_that_cleared_before_the_deadline_is_not_used(w: _World) -> None:
    """The latest read decides: evidence that went away leaves candidate_not_ready."""
    w.cluster.statuses = [
        WorkloadStatus(WorkloadState.PROGRESSING, "insufficient_resources"),
        PROGRESSING,
    ]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.step().failure_code == "candidate_not_ready"


@pytest.mark.asyncio
async def test_an_unreadable_status_keeps_the_last_diagnosis(w: _World) -> None:
    w.cluster.statuses = [
        WorkloadStatus(WorkloadState.PROGRESSING, "quota_exceeded"),
        ClusterUnavailable("down"),
    ]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.step().failure_code == "quota_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["crash_loop", "something_else", "candidate_refused"])
async def test_a_progressing_reason_that_is_not_a_diagnosis_is_ignored(
    w: _World, reason: str
) -> None:
    w.cluster.statuses = [WorkloadStatus(WorkloadState.PROGRESSING, reason)]
    await w.deploy.run(FENCE, DEADLINE)
    assert w.clock.now() == T0 + PARAMS.ready_timeout
    assert w.step().failure_code == "candidate_not_ready"


@pytest.mark.asyncio
async def test_the_last_allowed_attempt_failing_fails_the_release(w: _World) -> None:
    for attempt in range(1, MAX_CANDIDATE_ATTEMPTS):
        sid = UUID(int=OLD_STEP.int + attempt)
        w.store.steps[sid] = ReleaseStep(
            id=sid,
            release_id=RELEASE,
            name=ReleaseStepName.CANDIDATE,
            attempt=attempt,
            status=ReleaseStepStatus.FAILED,
            started_at=T0 - timedelta(hours=1),
            finished_at=T0 - timedelta(hours=1),
            deadline_at=T0 - timedelta(minutes=50),
            failure_code="candidate_not_ready",
        )
    w.cluster.statuses = [PROGRESSING]
    await w.deploy.run(FENCE, DEADLINE)
    release = w.store.releases[RELEASE]
    assert (release.status, release.failure_code) == (
        ReleaseStatus.FAILED,
        "candidate_attempts_exhausted",
    )
    _assert_command(w, CommandStatus.FAILED, "candidate_attempts_exhausted")
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
async def test_a_release_with_no_attempt_left_fails_without_a_cluster_call(w: _World) -> None:
    w.store.steps[OLD_STEP] = ReleaseStep(
        id=OLD_STEP,
        release_id=RELEASE,
        name=ReleaseStepName.CANDIDATE,
        attempt=MAX_CANDIDATE_ATTEMPTS,
        status=ReleaseStepStatus.FAILED,
        started_at=T0 - timedelta(hours=1),
        finished_at=T0 - timedelta(hours=1),
        deadline_at=T0 - timedelta(minutes=50),
        failure_code="candidate_not_ready",
    )
    await w.deploy.run(FENCE, DEADLINE)
    release = w.store.releases[RELEASE]
    assert (release.status, release.failure_code, release.failure_summary) == (
        ReleaseStatus.FAILED,
        "candidate_attempts_exhausted",
        deploy_failure_summary("candidate_attempts_exhausted"),
    )
    _assert_command(w, CommandStatus.FAILED, "candidate_attempts_exhausted")
    assert w.log == ["load", "exhausted"]


# --- cluster errors on apply --------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TenantNotReady("no namespace"), "tenant_not_ready"),
        (ClusterUnavailable("503"), "cluster_unavailable"),
    ],
)
async def test_a_transient_apply_failure_retries_later_and_writes_nothing(
    w: _World, error: Exception, code: str
) -> None:
    w.cluster.apply_error = error
    with pytest.raises(RetryLater) as raised:
        await w.deploy.run(FENCE, DEADLINE)
    assert raised.value.code == code
    assert raised.value.after is None  # the generic backoff, as before
    assert w.store.writes == 1  # only the attempt's begin; no outcome, no step failure
    assert w.step().status is ReleaseStepStatus.RUNNING
    _assert_command(w, CommandStatus.RUNNING, None)
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == []
    assert w.cluster.status_reads == 0


@pytest.mark.asyncio
async def test_the_next_claim_resumes_the_attempt_after_a_transient_failure(w: _World) -> None:
    w.cluster.apply_error = TenantNotReady("no namespace")
    with pytest.raises(RetryLater):
        await w.deploy.run(FENCE, DEADLINE)
    w.cluster.apply_error = None
    w.clock.at += timedelta(seconds=30)
    second = dataclasses.replace(FENCE, claim_number=2)
    await w.deploy.run(second, DEADLINE)
    step = w.step()  # still one attempt
    assert (step.attempt, step.status) == (1, ReleaseStepStatus.SUCCEEDED)
    assert "begin:resume:1" in w.log
    _assert_command(w, CommandStatus.SUCCEEDED, None)


@pytest.mark.asyncio
async def test_an_apply_timeout_retries_later() -> None:
    w = _world(dataclasses.replace(PARAMS, call_timeout=timedelta(milliseconds=50)))

    async def hang(workload: ReleaseWorkload) -> None:
        await asyncio.Event().wait()

    w.cluster.apply_release = hang  # type: ignore[method-assign]  # a hanging fake for this test
    with pytest.raises(RetryLater) as raised:
        await w.deploy.run(FENCE, DEADLINE)
    assert raised.value.code == "cluster_unavailable"
    assert raised.value.after is None


@pytest.mark.asyncio
async def test_a_fence_rejected_on_apply_ends_the_attempt_and_deletes_nothing(w: _World) -> None:
    w.cluster.apply_error = FenceRejected("newer")
    await w.deploy.run(FENCE, DEADLINE)
    assert w.step().failure_code == "candidate_fence_ahead"
    _assert_command(w, CommandStatus.FAILED, "candidate_fence_ahead")
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == []


@pytest.mark.asyncio
async def test_a_fence_rejected_on_status_ends_the_attempt_and_deletes_nothing(w: _World) -> None:
    w.cluster.statuses = [PROGRESSING, FenceRejected("newer")]
    await w.deploy.run(FENCE, DEADLINE)
    _assert_command(w, CommandStatus.FAILED, "candidate_fence_ahead")
    assert w.cluster.deleted == []


@pytest.mark.asyncio
async def test_an_apply_conflict_is_permanent_and_the_candidate_is_deleted(w: _World) -> None:
    w.cluster.apply_error = ApplyConflict("409")
    await w.deploy.run(FENCE, DEADLINE)
    _assert_command(w, CommandStatus.FAILED, "candidate_apply_conflict")
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
async def test_a_refused_candidate_fails_the_release(w: _World) -> None:
    """FR-17.2: a refused candidate fails the release."""
    w.cluster.apply_error = ClusterRefused("422")
    await w.deploy.run(FENCE, DEADLINE)
    release = w.store.releases[RELEASE]
    assert (release.status, release.failure_code) == (ReleaseStatus.FAILED, "candidate_refused")
    _assert_command(w, CommandStatus.FAILED, "candidate_refused")
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]


@pytest.mark.asyncio
async def test_a_failed_delete_does_not_keep_the_attempt_open(w: _World) -> None:
    w.cluster.statuses = [WorkloadStatus(WorkloadState.FAILED, "crash_loop")]
    w.cluster.delete_error = ClusterUnavailable("delete lost five times")
    await w.deploy.run(FENCE, DEADLINE)
    _assert_command(w, CommandStatus.FAILED, "crash_loop")


# --- the claim and the fence --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lost_claim_mid_poll_stops_without_writing(w: _World) -> None:
    w.cluster.statuses = [PROGRESSING]
    w.store.lose_claim_at_renewal = 4  # before apply, then polls 1 and 2, then lost
    with pytest.raises(ClaimLost):
        await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.status_reads == 2
    assert w.cluster.deleted == []
    assert w.store.writes == 1
    assert w.step().status is ReleaseStepStatus.RUNNING
    _assert_command(w, CommandStatus.RUNNING, None)


@pytest.mark.asyncio
async def test_a_lost_claim_before_the_delete_deletes_nothing(w: _World) -> None:
    w.cluster.statuses = [WorkloadStatus(WorkloadState.FAILED, "crash_loop")]
    w.store.lose_claim_at_renewal = 3  # before apply, before the poll, then before the delete
    with pytest.raises(ClaimLost):
        await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.deleted == []
    _assert_command(w, CommandStatus.RUNNING, None)


@pytest.mark.asyncio
async def test_a_stale_fence_stops_before_any_cluster_call(w: _World) -> None:
    w.store.hosting = dataclasses.replace(w.store.hosting, last_command_number=NUMBER + 1)
    with pytest.raises(StaleFence):
        await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.applied == []
    assert w.store.writes == 0


@pytest.mark.asyncio
async def test_a_stale_fence_at_the_end_writes_nothing(w: _World) -> None:
    w.cluster.statuses = [PROGRESSING, READY]

    async def newer_command_meanwhile(delay: timedelta) -> None:
        w.store.hosting = dataclasses.replace(w.store.hosting, last_command_number=NUMBER + 1)
        w.clock.at += delay

    w.pauser.pause = newer_command_meanwhile  # type: ignore[method-assign]  # one-off fake
    with pytest.raises(StaleFence):
        await w.deploy.run(FENCE, DEADLINE)
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT
    _assert_command(w, CommandStatus.RUNNING, None)


# --- refusals before the attempt ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [ReleaseStatus.ACCEPTED, ReleaseStatus.CANDIDATE_READY, ReleaseStatus.FAILED],
)
async def test_a_release_not_built_is_refused_without_a_cluster_call(
    w: _World, status: ReleaseStatus
) -> None:
    w.store.releases[RELEASE] = _release(status=status)
    with pytest.raises(PermanentFailure) as raised:
        await w.deploy.run(FENCE, DEADLINE)
    assert raised.value.code == "release_not_deployable"
    assert w.log == ["load"]


@pytest.mark.asyncio
async def test_a_built_release_without_a_digest_is_refused(w: _World) -> None:
    w.store.releases[RELEASE] = dataclasses.replace(_release(), container_image_digest=None)
    with pytest.raises(PermanentFailure) as raised:
        await w.deploy.run(FENCE, DEADLINE)
    assert raised.value.code == "release_not_deployable"
    assert w.log == ["load"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("routing", "hosting", "code"),
    [
        (RoutingStatus.CHANGING, HostingStatus.ONLINE, "route_not_stable"),
        (RoutingStatus.UNCERTAIN, HostingStatus.ONLINE, "route_not_stable"),
        (RoutingStatus.STABLE, HostingStatus.OFFLINE, "hosting_offline"),
    ],
)
async def test_a_project_not_ready_for_a_deploy_is_refused(
    w: _World, routing: RoutingStatus, hosting: HostingStatus, code: str
) -> None:
    w.store.hosting = dataclasses.replace(
        w.store.hosting, routing_status=routing, hosting_status=hosting
    )
    with pytest.raises(PermanentFailure) as raised:
        await w.deploy.run(FENCE, DEADLINE)
    assert raised.value.code == code
    assert w.log == ["load"]


@pytest.mark.asyncio
async def test_a_command_too_close_to_its_deadline_is_refused(w: _World) -> None:
    with pytest.raises(PermanentFailure) as raised:
        await w.deploy.run(FENCE, T0 + PARAMS.finish_margin)
    assert raised.value.code == "deadline_exceeded"
    assert w.log == ["load"]


@pytest.mark.asyncio
async def test_the_command_deadline_bounds_the_attempt(w: _World) -> None:
    deadline = T0 + PARAMS.finish_margin + timedelta(minutes=1)
    w.cluster.statuses = [PROGRESSING]
    await w.deploy.run(FENCE, deadline)
    step = w.step()
    assert step.deadline_at == T0 + timedelta(minutes=1)
    assert step.failure_code == "candidate_not_ready"
    assert w.clock.now() == T0 + timedelta(minutes=1)


# --- replaced candidates ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_older_candidate_is_returned_to_built_and_deleted_before_the_apply(
    w: _World,
) -> None:
    """FR-17.4: an older candidate is returned to built and deleted before the apply."""
    w.store.releases[OLD_CANDIDATE] = _release(OLD_CANDIDATE, ReleaseStatus.CANDIDATE_READY)
    await w.deploy.run(FENCE, DEADLINE)
    assert w.store.releases[OLD_CANDIDATE].status is ReleaseStatus.BUILT
    assert w.cluster.deleted == [(PROJECT, OLD_CANDIDATE, NUMBER)]
    assert w.log.index(f"delete:{OLD_CANDIDATE}") < w.log.index("apply")
    _assert_command(w, CommandStatus.SUCCEEDED, None)


@pytest.mark.asyncio
async def test_a_failed_delete_of_an_older_candidate_does_not_stop_the_deploy(w: _World) -> None:
    w.store.releases[OLD_CANDIDATE] = _release(OLD_CANDIDATE, ReleaseStatus.CANDIDATE_READY)
    w.cluster.delete_error = ClusterUnavailable("503")
    await w.deploy.run(FENCE, DEADLINE)
    _assert_command(w, CommandStatus.SUCCEEDED, None)


# --- abandon ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_abandon_deletes_the_candidate_and_ends_the_unfinished_attempt(w: _World) -> None:
    w.cluster.apply_error = ClusterUnavailable("503")
    with pytest.raises(RetryLater):
        await w.deploy.run(FENCE, DEADLINE)
    await w.deploy.abandon(FENCE, "attempts_exhausted")
    assert w.cluster.deleted == [(PROJECT, RELEASE, NUMBER)]
    assert w.step().failure_code == "attempts_exhausted"
    _assert_command(w, CommandStatus.FAILED, "attempts_exhausted")
    assert w.store.releases[RELEASE].status is ReleaseStatus.BUILT


@pytest.mark.asyncio
async def test_abandon_without_an_unfinished_attempt_does_nothing(w: _World) -> None:
    await w.deploy.abandon(FENCE, "deadline_exceeded")
    assert w.cluster.deleted == []
    assert w.store.writes == 0


# --- configuration -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"image_registry": ""},
        {"image_registry": "registry.webblock.test:5000/"},
        {"image_registry": "https://registry.webblock.test"},
        {"ready_timeout": timedelta(0)},
        {"poll_interval": timedelta(0)},
        {"poll_interval": timedelta(minutes=2)},
        {"call_timeout": timedelta(0)},
        {"delete_timeout": timedelta(seconds=-1)},
        {"finish_margin": timedelta(0)},
        {"claim_ttl": timedelta(0)},
    ],
)
def test_invalid_params_are_refused(change: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="deploy"):
        dataclasses.replace(PARAMS, **change).validate()  # type: ignore[arg-type]  # table of bad values


def test_the_image_comes_from_the_registry_the_project_and_the_digest(w: _World) -> None:
    assert w.deploy.image(PROJECT, DIGEST) == IMAGE


# --- QA-05 (copied from the QA branch d487207, tests/qa/test_qa_deploy_refusal_in_margin_...) ---


@pytest.mark.asyncio
async def test_a_deploy_refused_for_its_deadline_cleans_up_the_attempt_an_earlier_claim_left() -> (
    None
):
    """QA-05: a claim that starts after ``deadline - finish_margin`` but before the deadline was
    refused ``deadline_exceeded`` without looking at the attempt an earlier claim left, and the
    consumer calls ``abandon`` only once the deadline itself passed: the candidate's Deployment
    and Service stayed (tenant quota) and its step stayed ``running``. The refusal now cleans up
    like ``abandon``: the objects are deleted and the step ends with a failure code."""
    w = _world()

    # Claim 1: the attempt is recorded and the workload applied, then the process dies.
    await w.store.begin_candidate_attempt(
        FENCE, CandidateAttemptStart(step_id=w.ids.new_id(), timeout=PARAMS.ready_timeout)
    )
    await w.cluster.apply_release(WORKLOAD)
    (step,) = w.store.steps.values()
    assert step.status is ReleaseStepStatus.RUNNING

    # Claim 2 comes after deadline - finish_margin, but before the deadline.
    w.clock.at = DEADLINE - PARAMS.finish_margin / 2
    with pytest.raises(PermanentFailure):
        await w.deploy.run(FENCE, DEADLINE)

    (step,) = w.store.steps.values()
    assert w.cluster.deleted, f"the candidate rel-{RELEASE} was left in the cluster"
    assert step.status is ReleaseStepStatus.FAILED
    assert step.failure_code == "deadline_exceeded"
    assert w.store.command.status is CommandStatus.FAILED


@pytest.mark.asyncio
async def test_a_deploy_refused_for_its_deadline_without_an_attempt_deletes_nothing() -> None:
    w = _world()
    w.clock.at = DEADLINE - PARAMS.finish_margin / 2
    with pytest.raises(PermanentFailure):
        await w.deploy.run(FENCE, DEADLINE)
    assert w.cluster.deleted == []
    assert w.store.steps == {}
