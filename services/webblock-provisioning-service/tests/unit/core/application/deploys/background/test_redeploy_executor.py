"""The redeploy executor and use case (decision 0023 G12): a rolling restart of the live release's
Deployment, with the same image, fenced by the command's number. It never writes the route,
``routing_status``, a release or the usage outbox.

The rows are the in-memory world of the routing tests (``World``: the three checks of every worker
write and the real domain outcome functions); the cluster is its fake with the restart and the
rollout status scripted here. Every store and cluster call lands in one shared log."""

from dataclasses import dataclass, field, replace
from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    DIGEST,
    LIVE,
    PROJECT,
    FakeCluster,
    World,
    make_release,
    unavailable,
)

from provisioning.core.application import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    ClaimLost,
    PermanentFailure,
    RetryLater,
)
from provisioning.core.application.deploys.background.redeploy import (
    CODE_REDEPLOY_TARGET_CHANGED,
    CODE_REDEPLOY_UNREADY,
    Redeploy,
    RedeploySettings,
)
from provisioning.core.application.deploys.background.redeploy_executor import RedeployExecutor
from provisioning.core.application.ports.site_cluster import (
    ClusterRefused,
    ReleaseWorkload,
    TenantNotReady,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandStatus,
    HostingStatus,
    ReleaseStatus,
    RoutingStatus,
    failure_retryable,
)

REGISTRY = "registry.webblock.test:5000"
IMAGE = f"{REGISTRY}/webblock/sites/{PROJECT}@{DIGEST}"
NEWER = UUID("0190a1b2-0000-7000-8000-0000000000a9")
READY = WorkloadStatus(WorkloadState.READY)
PROGRESSING = WorkloadStatus(WorkloadState.PROGRESSING)
SETTINGS = RedeploySettings(
    image_registry=REGISTRY,
    poll_interval=timedelta(seconds=2),
    call_timeout=timedelta(seconds=5),
    finish_margin=timedelta(seconds=5),
)
# The cluster calls that would move the route or delete a workload: a redeploy makes none.
ROUTE_CALLS = ("apply_route", "apply_preview", "reclaim_route", "delete_release")


@dataclass
class RestartCluster(FakeCluster):
    """The routing fake, plus the restart and a scripted rollout: ``statuses`` are returned (or
    raised) one per ``release_status``, the last one repeated."""

    restarts: list[ReleaseWorkload] = field(default_factory=list)
    reads: list[ReleaseWorkload] = field(default_factory=list)
    statuses: list[WorkloadStatus | Exception] = field(default_factory=lambda: [READY])
    fail_restart: Exception | None = None

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        self.log.append(f"restart_release {workload.release_id} #{workload.command_number}")
        if self.fail_restart is not None:
            raise self.fail_restart
        self.restarts.append(workload)

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        self.log.append(f"release_status {workload.release_id} #{workload.command_number}")
        self.reads.append(workload)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(status, Exception):
            raise status
        return status


class CountingPauser:
    """Records each pause; the world's clock moves one second at every read anyway."""

    def __init__(self) -> None:
        self.pauses: list[timedelta] = []

    async def pause(self, delay: timedelta) -> None:
        self.pauses.append(delay)


def setup(
    *statuses: WorkloadStatus | Exception,
) -> tuple[World, RestartCluster, CountingPauser, RedeployExecutor]:
    log: list[str] = []
    world = World(log, candidate=False)
    cluster = RestartCluster(log, serving={LIVE}, answering={LIVE})
    if statuses:
        cluster.statuses = list(statuses)
    pauser = CountingPauser()
    redeploy = Redeploy(
        store=world, cluster=cluster, clock=world.clock, pauser=pauser, settings=SETTINGS
    )
    return world, cluster, pauser, RedeployExecutor(redeploy)


def accepted(world: World) -> tuple[Command, Claim]:
    command = world.accept(CommandKind.REDEPLOY, LIVE)
    return command, world.claim(command)


def cluster_calls(log: list[str]) -> list[str]:
    return [line for line in log if not line.startswith("store ")]


def route_calls(log: list[str]) -> list[str]:
    return [line for line in log if line.startswith(ROUTE_CALLS)]


@pytest.mark.asyncio
async def test_redeploy_refuses_a_release_no_longer_live() -> None:
    """Review Focus 3: the live release changed between the acceptance and the run. The command
    fails ``redeploy_target_changed`` and nothing is restarted or read in the cluster.

    FR-35.4."""
    world, cluster, _, run = setup()
    command, claim = accepted(world)
    world.releases[LIVE] = replace(world.releases[LIVE], status=ReleaseStatus.SUPERSEDED)
    world.releases[NEWER] = make_release(NEWER, ReleaseStatus.LIVE, 9)
    world.hosting = replace(world.hosting, live_release_id=NEWER)

    await run.execute(claim)

    stored = world.command(command.id)
    assert stored.status is CommandStatus.FAILED
    assert stored.failure_code == CODE_REDEPLOY_TARGET_CHANGED
    assert cluster.restarts == []
    assert cluster_calls(world.log) == []
    assert world.writes == ["command redeploy failed"]
    assert world.release(LIVE).status is ReleaseStatus.SUPERSEDED
    assert world.release(NEWER).status is ReleaseStatus.LIVE


@pytest.mark.asyncio
async def test_redeploy_applies_the_restart_annotation_with_the_fence() -> None:
    """The restart is the live release's own workload (its image by digest) at the command's
    number, which the adapter writes as both the fence and the pod template's restart annotation;
    the rollout is read at that same number.

    FR-35.1."""
    world, cluster, _, run = setup(READY)
    command, claim = accepted(world)

    await run.execute(claim)

    expected = ReleaseWorkload(PROJECT, LIVE, IMAGE, command.command_number)
    assert cluster.restarts == [expected]
    assert cluster.reads == [expected]
    assert cluster_calls(world.log) == [
        f"restart_release {LIVE} #{command.command_number}",
        f"release_status {LIVE} #{command.command_number}",
    ]


@pytest.mark.asyncio
async def test_redeploy_succeeds_when_the_rollout_is_ready() -> None:
    """Progressing reads and a failed read are polled through until the rollout is ready."""
    world, cluster, pauser, run = setup(PROGRESSING, unavailable(), PROGRESSING, READY)
    command, claim = accepted(world)

    await run.execute(claim)

    stored = world.command(command.id)
    assert stored.status is CommandStatus.SUCCEEDED
    assert stored.failure_code is None
    assert len(cluster.reads) == 4
    assert pauser.pauses == [SETTINGS.poll_interval] * 3
    assert len(cluster.restarts) == 1  # one apply; polling never re-applies


@pytest.mark.asyncio
async def test_redeploy_fails_unready_at_the_deadline() -> None:
    """A rollout that never gets ready ends ``redeploy_unready`` before the command's deadline
    (less the finish margin); the site keeps its live release and stays online.

    FR-35.3."""
    world, _, pauser, run = setup(PROGRESSING)
    command, claim = accepted(world)
    claim = replace(claim, deadline_at=world.clock.at + timedelta(seconds=30))
    before = world.hosting

    await run.execute(claim)

    stored = world.command(command.id)
    assert stored.status is CommandStatus.FAILED
    assert stored.failure_code == CODE_REDEPLOY_UNREADY
    assert world.clock.at <= claim.deadline_at
    assert pauser.pauses and all(p <= SETTINGS.poll_interval for p in pauser.pauses)
    assert world.hosting == before
    assert world.release(LIVE).status is ReleaseStatus.LIVE
    assert route_calls(world.log) == []
    assert not failure_retryable(CODE_REDEPLOY_UNREADY)
    assert not failure_retryable(CODE_REDEPLOY_TARGET_CHANGED)


@pytest.mark.asyncio
async def test_redeploy_writes_no_route_no_release_no_usage() -> None:
    """Notion "redeploy": route and release status unchanged, no new release, not a publish.
    The one write is the command's end: no hosting row write (``routing_status`` never
    ``changing``), no release write, so the store inserts no usage event (it does so only for a
    release that goes live, ``test_routing_store.py``), and no route call.

    FR-35.1."""
    world, cluster, _, run = setup(PROGRESSING, READY)
    command, claim = accepted(world)
    hosting, releases = world.hosting, dict(world.releases)

    await run.execute(claim)

    assert world.command(command.id).status is CommandStatus.SUCCEEDED
    assert world.writes == ["command redeploy succeeded"]
    assert world.hosting == hosting
    assert world.hosting.routing_status is RoutingStatus.STABLE
    assert world.hosting.hosting_status is HostingStatus.ONLINE
    assert world.releases == releases
    assert route_calls(world.log) == []
    assert cluster.route is None and cluster.preview is None


@pytest.mark.parametrize(
    "status",
    [
        WorkloadStatus(WorkloadState.FAILED, "crash_loop"),
        WorkloadStatus(WorkloadState.MISSING),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_or_missing_rollout_ends_unready_at_once(status: WorkloadStatus) -> None:
    world, cluster, pauser, run = setup(PROGRESSING, status)
    command, claim = accepted(world)

    await run.execute(claim)

    assert world.command(command.id).failure_code == CODE_REDEPLOY_UNREADY
    assert len(cluster.reads) == 2
    assert len(pauser.pauses) == 1


@pytest.mark.asyncio
async def test_a_refused_restart_ends_unready_without_reading_the_rollout() -> None:
    world, cluster, _, run = setup()
    cluster.fail_restart = ClusterRefused("admission")
    command, claim = accepted(world)

    await run.execute(claim)

    assert world.command(command.id).failure_code == CODE_REDEPLOY_UNREADY
    assert cluster.reads == []


@pytest.mark.parametrize(
    ("error", "code"),
    [(TenantNotReady("no access"), "tenant_not_ready"), (unavailable(), "cluster_unavailable")],
)
@pytest.mark.asyncio
async def test_a_transient_restart_failure_is_retried_later_and_writes_nothing(
    error: Exception, code: str
) -> None:
    world, cluster, _, run = setup()
    cluster.fail_restart = error
    command, claim = accepted(world)

    with pytest.raises(RetryLater) as raised:
        await run.execute(claim)

    assert raised.value.code == code
    assert raised.value.after is None  # the generic backoff, as before
    assert world.command(command.id).status is CommandStatus.RUNNING
    assert world.writes == []

    # The next attempt applies the same command number again: the same annotation, no second
    # restart of the pods.
    cluster.fail_restart = None
    await run.execute(world.claim(command))
    assert world.command(command.id).status is CommandStatus.SUCCEEDED
    assert [w.command_number for w in cluster.restarts] == [command.command_number]


@pytest.mark.asyncio
async def test_a_redeploy_past_its_own_deadline_ends_unready_without_a_restart() -> None:
    world, _, _, run = setup()
    command, claim = accepted(world)
    claim = replace(claim, deadline_at=world.clock.at + SETTINGS.finish_margin)

    await run.execute(claim)

    assert world.command(command.id).failure_code == CODE_REDEPLOY_UNREADY
    assert cluster_calls(world.log) == []


@pytest.mark.asyncio
async def test_a_lost_claim_restarts_nothing() -> None:
    world, _, _, run = setup()
    command, claim = accepted(world)
    world.steal_claim(command.id)

    with pytest.raises(ClaimLost):
        await run.execute(claim)

    assert cluster_calls(world.log) == []
    assert world.command(command.id).status is CommandStatus.RUNNING


@pytest.mark.parametrize("kind", [CommandKind.PROMOTE, CommandKind.SUSPEND_HOSTING])
@pytest.mark.asyncio
async def test_another_kind_is_refused_without_calling_anything(kind: CommandKind) -> None:
    world, _, _, run = setup()
    _, claim = accepted(world)
    other = replace(
        claim,
        ref=replace(
            claim.ref,
            kind=kind,
            release_id=None if kind.names_no_release() else claim.ref.release_id,
        ),
    )
    world.log.clear()

    with pytest.raises(PermanentFailure) as raised:
        await run.execute(other)

    assert raised.value.code == FAILURE_CODE_OPERATION_UNAVAILABLE
    assert world.log == []


def test_settings_refuse_missing_or_non_positive_values() -> None:
    with pytest.raises(ValueError, match="image registry"):
        replace(SETTINGS, image_registry="").validate()
    with pytest.raises(ValueError, match="poll_interval"):
        replace(SETTINGS, poll_interval=timedelta(0)).validate()
    with pytest.raises(ValueError, match="finish_margin"):
        replace(SETTINGS, finish_margin=timedelta(seconds=-1)).validate()
