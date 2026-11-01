"""The reconciler (task W6-B; decision 0014 section 21 rulings 8 and 9, section 23 item 3).

It skips a project whose route is not stable or that has an unfinished command; for the others it
compares the cluster's inventory with the records, re-applies a missing or drifted live
Deployment, Service or route through the normal fenced, never-forced apply at the project's last
command number, sweeps the workloads of superseded, rolled-back and failed releases, and reports
orphan tenant namespaces, foreign field managers and fences above the database's. It never forces.
"""

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.unit.core.application.routing.background.test_recovery import RecordingAudit

from provisioning.core.application.ports.site_cluster import (
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    SiteInventory,
    SiteObject,
    SiteObjectKind,
    TenantNotReady,
    WorkloadStatus,
)
from provisioning.core.application.reconcile.background.reconcile import (
    DEFERRAL_BUDGET,
    Reconciler,
    ReconcileSettings,
)
from provisioning.core.application.reconcile.ports import ProjectRecord
from provisioning.core.application.routing.background.recovery import ReleaseCleanup
from provisioning.core.application.shared.audit import AuditAction, AuditResult
from provisioning.core.domain import HostingStatus, ReleaseStatus, RoutingStatus

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
P1 = UUID("0190a1b2-0000-7000-8000-000000000001")
P2 = UUID("0190a1b2-0000-7000-8000-000000000002")
P3 = UUID("0190a1b2-0000-7000-8000-000000000003")
ORPHAN = UUID("0190a1b2-0000-7000-8000-0000000000ff")
LIVE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
OLD = UUID("0190a1b2-0000-7000-8000-0000000000a0")
ROLLED = UUID("0190a1b2-0000-7000-8000-0000000000a2")
FAILED = UUID("0190a1b2-0000-7000-8000-0000000000a3")
CANDIDATE = UUID("0190a1b2-0000-7000-8000-0000000000a4")
UNKNOWN = UUID("0190a1b2-0000-7000-8000-0000000000a5")
DIGEST = "sha256:" + "c" * 64
REGISTRY = "registry.webblock.test:5000"
DOMAIN = "sites.webblock.test"
LAST = 12
D = SiteObjectKind.DEPLOYMENT
S = SiteObjectKind.SERVICE
R = SiteObjectKind.HTTP_ROUTE


def to(release_id: UUID | None) -> RouteTarget:
    if release_id is None:
        return RouteTarget(RouteTargetKind.OFFLINE)
    return RouteTarget(RouteTargetKind.RELEASE, release_id)


class Clock:
    def now(self) -> datetime:
        return T0


@dataclass
class Cluster:
    """The per-site objects of several tenant namespaces. ``apply_*`` refuse a higher fence (as
    the adapter does) and record; ``reclaim_route`` records a forced write, which the reconciler
    must never make."""

    log: list[str]
    objects: dict[UUID, dict[tuple[SiteObjectKind, str], SiteObject]] = field(default_factory=dict)
    routes: dict[UUID, RouteTarget] = field(default_factory=dict)
    extra_tenants: tuple[UUID, ...] = ()
    fail_inventory: dict[UUID, Exception] = field(default_factory=dict)
    fail_route: dict[UUID, Exception] = field(default_factory=dict)  # a write that loses a race
    refuse_delete_once: set[UUID] = field(default_factory=set)  # the policy's stale cache
    refuse_delete_always: set[UUID] = field(default_factory=set)  # another admission rule
    forced: list[str] = field(default_factory=list)

    def tenant(self, project_id: UUID) -> dict[tuple[SiteObjectKind, str], SiteObject]:
        return self.objects.setdefault(project_id, {})

    def put(
        self,
        project_id: UUID,
        kind: SiteObjectKind,
        name: str,
        *,
        number: int | None = 5,
        foreign: tuple[str, ...] = (),
    ) -> None:
        release_id = UUID(name.removeprefix("rel-")) if name.startswith("rel-") else None
        self.tenant(project_id)[(kind, name)] = SiteObject(kind, name, release_id, number, foreign)

    def workload(self, project_id: UUID, release_id: UUID, **kw: object) -> None:
        for kind in (D, S):
            self.put(project_id, kind, f"rel-{release_id}", **kw)  # type: ignore[arg-type]

    def route(self, project_id: UUID, target: RouteTarget, **kw: object) -> None:
        self.routes[project_id] = target
        self.put(project_id, R, f"route-{project_id}", **kw)  # type: ignore[arg-type]

    def _fence(self, project_id: UUID, kind: SiteObjectKind, name: str, number: int) -> None:
        current = self.tenant(project_id).get((kind, name))
        if current is not None and current.command_number is not None:
            if current.command_number < 0 or current.command_number > number:
                raise FenceRejected("higher command number")

    async def apply_release(self, workload: ReleaseWorkload) -> None:
        name = f"rel-{workload.release_id}"
        self.log.append(f"apply_release {name} {workload.image} #{workload.command_number}")
        for kind in (D, S):
            self._fence(workload.project_id, kind, name, workload.command_number)
        self.workload(workload.project_id, workload.release_id, number=workload.command_number)

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        raise AssertionError("the reconciler never restarts a release")

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        raise AssertionError("the reconciler never waits for a release")

    async def delete_release(self, project_id: UUID, release_id: UUID, command_number: int) -> None:
        self.log.append(f"delete_release {release_id} #{command_number}")
        if release_id in self.refuse_delete_always:
            raise ClusterRefused("admission: another rule")
        if release_id in self.refuse_delete_once:
            self.refuse_delete_once.discard(release_id)
            raise ClusterRefused("admission webblock-site-routed-release")
        if self.routes.get(project_id) == to(release_id):
            raise FenceRejected("the route points at this release")
        for kind in (D, S):
            self._fence(project_id, kind, f"rel-{release_id}", command_number)
            self.tenant(project_id).pop((kind, f"rel-{release_id}"), None)

    async def apply_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        self.log.append(f"apply_route {project_id} {target.release_id} #{command_number}")
        # The record's sub-domain names the host (decision 0023 G18), never the project ID.
        assert subdomain == subdomain_of(project_id)
        assert hostnames == (f"{subdomain_of(project_id)}.{DOMAIN}",)
        if project_id in self.fail_route:
            raise self.fail_route[project_id]
        self._fence(project_id, R, f"route-{project_id}", command_number)
        self.route(project_id, target, number=command_number)

    async def route_status(self, project_id: UUID) -> RouteStatus:
        target = self.routes.get(project_id)
        return RouteStatus(accepted=target is not None, target=target, command_number=None)

    async def apply_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        hostname: str,
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        raise AssertionError("the reconciler never touches the preview route")

    async def preview_status(self, project_id: UUID) -> RouteStatus:
        raise AssertionError("the reconciler never reads the preview route")

    async def probe_site(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        *,
        preview_of: str | None = None,
    ) -> bool:
        raise AssertionError("the reconciler never probes")

    async def list_tenant_projects(self) -> tuple[UUID, ...]:
        return (*self.objects, *self.extra_tenants)

    async def inventory(self, project_id: UUID) -> SiteInventory:
        self.log.append(f"inventory {project_id}")
        if project_id in self.fail_inventory:
            raise self.fail_inventory[project_id]
        if project_id not in self.objects:
            raise TenantNotReady("no namespace")
        return SiteInventory(project_id, tuple(self.objects[project_id].values()))

    async def reclaim_route(
        self,
        project_id: UUID,
        target: RouteTarget,
        hostnames: tuple[str, ...],
        command_number: int,
        *,
        subdomain: str,
    ) -> None:
        self.forced.append(f"reclaim_route {project_id}")
        self.route(project_id, target, number=command_number)


def subdomain_of(project_id: UUID) -> str:
    """Each test project's sub-domain, as its hosting row holds it."""
    return f"site-{project_id.hex}"


def record(project_id: UUID, **changes: object) -> ProjectRecord:
    base = ProjectRecord(
        project_id=project_id,
        last_command_number=LAST,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        live_release_id=LIVE,
        live_release_digest=DIGEST,
        unfinished_command=False,
        subdomain=subdomain_of(project_id),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@dataclass
class Store:
    projects: dict[UUID, ProjectRecord] = field(default_factory=dict)
    statuses: dict[UUID, ReleaseStatus] = field(default_factory=dict)
    busy_before_write: set[UUID] = field(default_factory=set)  # a command arrives meanwhile
    pages: list[tuple[UUID | None, int]] = field(default_factory=list)

    async def projects_after(self, after: UUID | None, limit: int) -> tuple[ProjectRecord, ...]:
        self.pages.append((after, limit))
        ordered = sorted(self.projects.values(), key=lambda p: p.project_id)
        return tuple(p for p in ordered if after is None or p.project_id > after)[:limit]

    async def known_projects(self, project_ids: Sequence[UUID]) -> frozenset[UUID]:
        return frozenset(p for p in project_ids if p in self.projects)

    async def release_statuses(
        self, project_id: UUID, release_ids: Sequence[UUID]
    ) -> Mapping[UUID, ReleaseStatus]:
        return {r: self.statuses[r] for r in release_ids if r in self.statuses}

    async def still_idle(self, project_id: UUID, last_command_number: int) -> bool:
        return project_id not in self.busy_before_write


class World:
    def __init__(self, batch: int = 50) -> None:
        self.log: list[str] = []
        self.cluster = Cluster(self.log)
        self.store = Store()
        self.audit = RecordingAudit()
        self.store.statuses = {
            LIVE: ReleaseStatus.LIVE,
            OLD: ReleaseStatus.SUPERSEDED,
            ROLLED: ReleaseStatus.ROLLED_BACK,
            FAILED: ReleaseStatus.FAILED,
            CANDIDATE: ReleaseStatus.CANDIDATE_READY,
        }
        self.reconciler = Reconciler(
            store=self.store,
            cluster=self.cluster,
            cleanup=ReleaseCleanup(
                cluster=self.cluster,
                audit=self.audit,
                clock=Clock(),
                delete_timeout=timedelta(seconds=1),
                logger=logging.getLogger("test.cleanup"),
            ),
            settings=ReconcileSettings(
                site_domain=DOMAIN,
                image_registry=REGISTRY,
                batch_size=batch,
                call_timeout=timedelta(seconds=1),
            ),
            logger=logging.getLogger("test.reconcile"),
        )

    def healthy(self, project_id: UUID = P1) -> None:
        self.store.projects[project_id] = record(project_id)
        self.cluster.workload(project_id, LIVE)
        self.cluster.route(project_id, to(LIVE))

    def writes(self) -> list[str]:
        return [line for line in self.log if not line.startswith("inventory")]


@pytest.fixture
def w() -> Iterator[World]:
    world = World()
    yield world
    assert world.cluster.forced == [], "the reconciler forced a write"


IMAGE = f"{REGISTRY}/webblock/sites/{P1}@{DIGEST}"


async def test_a_healthy_project_is_left_alone(w: World) -> None:
    w.healthy()
    report = await w.reconciler.round()
    assert w.writes() == []
    assert report.reapplied == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"routing_status": RoutingStatus.CHANGING},
        {"routing_status": RoutingStatus.UNCERTAIN},
        {"unfinished_command": True},
    ],
    ids=["changing", "uncertain", "unfinished-command"],
)
async def test_a_project_that_is_not_idle_is_skipped_without_any_cluster_call(
    w: World, changes: dict[str, object]
) -> None:
    """FR-21.2: a project whose promote waits between tries is skipped (routing ``changing`` and
    an unfinished command, design G14), so the reconciler never touches its route or candidate."""
    w.store.projects[P1] = record(P1, **changes)
    w.cluster.workload(P1, OLD)  # cleanable, and the live workload is missing: still untouched
    report = await w.reconciler.round()
    assert w.log == []
    assert report.skipped == (P1,)


async def test_a_deleted_live_deployment_is_reapplied_at_the_last_command_number(
    w: World,
) -> None:
    w.healthy()
    del w.cluster.objects[P1][(D, f"rel-{LIVE}")]

    report = await w.reconciler.round()

    assert w.writes() == [f"apply_release rel-{LIVE} {IMAGE} #{LAST}"]
    assert report.reapplied == ((P1, D, f"rel-{LIVE}"),)


async def test_a_drifted_route_is_pointed_back_at_the_live_release(w: World) -> None:
    w.healthy()
    w.cluster.routes[P1] = to(OLD)

    await w.reconciler.round()

    assert w.writes() == [f"apply_route {P1} {LIVE} #{LAST}"]
    assert w.cluster.routes[P1] == to(LIVE)


async def test_a_missing_route_is_applied_and_offline_hosting_points_at_the_offline_page(
    w: World,
) -> None:
    w.store.projects[P1] = record(P1, hosting_status=HostingStatus.OFFLINE)
    w.cluster.workload(P1, LIVE)

    await w.reconciler.round()

    assert w.writes() == [f"apply_route {P1} None #{LAST}"]
    assert w.cluster.routes[P1] == to(None)


async def test_a_project_never_published_gets_no_route(w: World) -> None:
    w.store.projects[P1] = record(P1, live_release_id=None, live_release_digest=None)
    w.cluster.objects[P1] = {}
    await w.reconciler.round()
    assert w.writes() == []


async def test_a_foreign_field_manager_is_reported_and_its_object_never_written(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    """The live test's second half: an object another manager wrote is reported, not
    overwritten, even when it drifted; nothing is forced (the fixture checks)."""
    w.healthy()
    w.cluster.route(P1, to(OLD), foreign=("kubectl-patch",))

    with caplog.at_level(logging.WARNING, logger="test.reconcile"):
        report = await w.reconciler.round()

    assert w.writes() == []
    assert report.foreign == ((P1, R, f"route-{P1}", ("kubectl-patch",)),)
    assert "foreign field managers" in caplog.text
    assert "kubectl-patch" in caplog.text


async def test_a_fence_above_the_database_is_reported_and_never_written(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    w.healthy()
    w.cluster.route(P1, to(OLD), number=999_999)
    w.cluster.put(P1, D, f"rel-{LIVE}", number=-1)
    del w.cluster.objects[P1][(S, f"rel-{LIVE}")]

    with caplog.at_level(logging.WARNING, logger="test.reconcile"):
        report = await w.reconciler.round()

    assert w.writes() == []
    assert {(kind, number) for _, kind, _, number in report.fence_ahead} == {(R, 999_999), (D, -1)}
    assert "command number above the database" in caplog.text


async def test_orphan_tenant_namespaces_are_reported(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    w.healthy()
    w.cluster.extra_tenants = (ORPHAN,)

    with caplog.at_level(logging.WARNING, logger="test.reconcile"):
        report = await w.reconciler.round()

    assert report.orphans == (ORPHAN,)
    assert f"orphan tenant namespace wb-{ORPHAN}" in caplog.text
    assert w.writes() == []


async def test_the_sweep_deletes_finished_workloads_and_keeps_the_others(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    w.healthy()
    for release_id in (OLD, ROLLED, FAILED, CANDIDATE, UNKNOWN):
        w.cluster.workload(P1, release_id)

    with caplog.at_level(logging.WARNING, logger="test.reconcile"):
        report = await w.reconciler.round()

    assert sorted(report.deleted) == sorted([(P1, OLD), (P1, ROLLED), (P1, FAILED)])
    names = {name for _, name in w.cluster.objects[P1]}
    assert names == {f"rel-{LIVE}", f"rel-{CANDIDATE}", f"rel-{UNKNOWN}", f"route-{P1}"}
    assert all(f"#{LAST}" in line for line in w.writes())
    assert f"release {UNKNOWN} has no row" in caplog.text


async def test_the_sweep_never_deletes_the_routed_release_even_when_its_row_is_finished(
    w: World,
) -> None:
    """Planted defect: cleanup deleting the routed release. A drifted route held by another
    manager points at a superseded release: the sweep keeps that release."""
    w.healthy()
    w.cluster.workload(P1, OLD)
    w.cluster.route(P1, to(OLD), foreign=("kubectl-edit",))

    await w.reconciler.round()

    assert (D, f"rel-{OLD}") in w.cluster.objects[P1]
    assert not any(line.startswith(f"delete_release {OLD}") for line in w.log)


async def test_a_command_accepted_meanwhile_stops_every_write(w: World) -> None:
    """Fence safety: the project is re-read right before each write; a command accepted after
    the round's read leaves the project to it."""
    w.healthy()
    del w.cluster.objects[P1][(D, f"rel-{LIVE}")]
    w.cluster.routes[P1] = to(OLD)
    w.cluster.workload(P1, OLD)
    w.store.busy_before_write.add(P1)

    await w.reconciler.round()

    assert w.writes() == []


@pytest.mark.parametrize(
    "error", [FenceRejected("a newer command"), ClusterUnavailable("503")], ids=["fence", "503"]
)
async def test_a_refused_write_is_logged_not_raised(w: World, error: Exception) -> None:
    """A command that stamped the route between the re-read and the write wins: the adapter's
    fence check (or the admission policy) refuses the reconciler's lower number."""
    w.healthy()
    w.cluster.routes[P1] = to(OLD)
    w.cluster.fail_route[P1] = error

    report = await w.reconciler.round()

    assert report.reapplied == ()
    assert report.failed == ()
    assert w.cluster.routes[P1] == to(OLD)


async def test_one_failing_project_does_not_stop_the_round(w: World) -> None:
    w.healthy(P1)
    w.healthy(P2)
    del w.cluster.objects[P2][(D, f"rel-{LIVE}")]
    w.cluster.fail_inventory[P1] = ClusterUnavailable("503")

    report = await w.reconciler.round()

    assert report.reapplied == ((P2, D, f"rel-{LIVE}"),)
    assert report.failed == (P1,)


async def test_a_project_without_a_tenant_yet_is_skipped(w: World) -> None:
    w.store.projects[P3] = record(P3)
    report = await w.reconciler.round()
    assert report.skipped == (P3,)
    assert w.writes() == []


async def test_rounds_page_through_the_projects_in_batches_and_wrap() -> None:
    world = World(batch=2)
    for project_id in (P1, P2, P3):
        world.healthy(project_id)

    seen = [(await world.reconciler.round()).projects for _ in range(3)]

    assert seen == [(P1, P2), (P3,), (P1, P2)]
    assert world.store.pages == [(None, 2), (P2, 2), (None, 2)]


async def test_a_delete_the_admission_policy_refuses_for_now_is_skipped_then_swept_later(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    """W6-K's ``webblock-site-routed-release`` reads the route from the API server's cache, so
    right after a route move a legitimate delete of the previous release may be refused (403,
    ``ClusterRefused``) for a moment. The cleanup skips it this round without failing anything,
    and the next sweep deletes it: a bounded retry by rounds, never an inline loop."""
    w.healthy()
    w.cluster.workload(P1, OLD)
    w.cluster.refuse_delete_once.add(OLD)

    with caplog.at_level(logging.INFO):
        first = await w.reconciler.round()
    assert first.deleted == ()
    assert first.failed == ()
    assert (D, f"rel-{OLD}") in w.cluster.objects[P1]
    assert [line for line in w.log if line.startswith(f"delete_release {OLD}")] == [
        f"delete_release {OLD} #{LAST}"
    ]
    assert "deferred" in caplog.text

    second = await w.reconciler.round()
    assert second.deleted == ((P1, OLD),)
    assert (D, f"rel-{OLD}") not in w.cluster.objects[P1]


async def test_a_delete_refused_every_round_is_given_up_once_after_its_budget(
    w: World, caplog: pytest.LogCaptureFixture
) -> None:
    """No retries without a budget: a delete some other admission rule always refuses is deferred
    ``DEFERRAL_BUDGET`` rounds in a row, then recorded ``failed`` once (a warning and an audit
    event with the refusal's code) and never tried again by this process."""
    w.healthy()
    w.cluster.workload(P1, OLD)
    w.cluster.refuse_delete_always.add(OLD)

    with caplog.at_level(logging.WARNING):
        reports = [await w.reconciler.round() for _ in range(DEFERRAL_BUDGET + 3)]

    attempts = [line for line in w.log if line.startswith(f"delete_release {OLD}")]
    assert len(attempts) == DEFERRAL_BUDGET
    cleanups = [e for e in w.audit.records if e.action is AuditAction.WORKLOAD_CLEANUP]
    failed = [e for e in cleanups if e.result is AuditResult.FAILED]
    assert len(failed) == 1
    assert (failed[0].release_id, failed[0].failure_code) == (OLD, "cluster_refused")
    assert [e.result for e in cleanups].count(AuditResult.SKIPPED) == DEFERRAL_BUDGET
    assert [r.given_up for r in reports].count(((P1, OLD),)) == 1
    assert "given up" in caplog.text
    assert (D, f"rel-{OLD}") in w.cluster.objects[P1]


async def test_a_successful_delete_resets_the_deferral_count(w: World) -> None:
    w.healthy()
    w.cluster.workload(P1, OLD)
    w.cluster.workload(P1, ROLLED)
    w.cluster.refuse_delete_once.add(OLD)
    for _ in range(DEFERRAL_BUDGET - 1):
        w.cluster.refuse_delete_once.add(ROLLED)
        await w.reconciler.round()
    report = await w.reconciler.round()
    assert (P1, ROLLED) in report.deleted
    assert not any(e.result is AuditResult.FAILED for e in w.audit.records)
