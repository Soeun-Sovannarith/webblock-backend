"""The reconciler (decision 0014 section 21 rulings 8 and 9, section 23 item 3; task W6-B).

A periodic loop on the release worker (``core.application.reconcile.background.reconciler``) runs
one ``round`` every ``RECONCILE_INTERVAL``, over the next ``RECONCILE_BATCH`` projects by project
ID, wrapping round.

For each project:

- **Skip** a project whose route is not ``stable`` (``changing`` belongs to the command that
  changes it; ``uncertain`` to the operator rollback) or that has an unfinished command: its
  command owns the per-site objects. No cluster call is made for it.
- **Read** the tenant's inventory. A project without a tenant namespace yet is skipped (the
  bootstrap creates it).
- **Report**, as log events, every object with a foreign field manager and every object whose
  fence (``command-number``) is above the project's ``last_command_number`` or not a number. Such
  an object is never written: a never-forced apply would be refused anyway, and taking an object
  back is the operator rollback's alone (``reclaim_route``, section 23 item 3).
- **Re-apply**, through the normal fenced, never-forced apply at the project's last command
  number: the live release's Deployment and Service when either is missing, and the site's route
  when it is missing (and the project has a live release or is offline) or points elsewhere than
  the records say (the offline page when hosting is offline or there is no live release, else the
  live release).
- **Sweep** the Deployments and Services of superseded, rolled-back and failed releases through
  ``ReleaseCleanup`` (never the live or the routed release). A workload of a release without a row
  is reported, never deleted.

Each round also lists the tenant namespaces and reports every one without a ``project_hosting``
row (an orphan).

Fence safety. Every write is stamped with the project's last command number, which no earlier
command exceeds. A command accepted after the round read the project makes the reconciler's write
stale, so the project is read again right before each write (``still_idle``): stable, the same
last command number, no unfinished command. A command accepted between that read and the write
stamps its own objects with a higher number: the adapter's fence check refuses the reconciler's
write after it (``FenceRejected``), and W6-K's admission policy refuses a lower number in the
cluster, closing the read-then-apply window the adapter alone leaves open.

Nothing is forced, and no error of one project stops the round: it is logged by type and the
next round tries again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final
from uuid import UUID

from provisioning.core.application.deploys.background.deploy import site_image
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    SiteCluster,
    SiteObject,
    SiteObjectKind,
    TenantNotReady,
)
from provisioning.core.application.reconcile.ports import ProjectRecord, ReconcileStore
from provisioning.core.application.routing.background.recovery import CLEANABLE, ReleaseCleanup
from provisioning.core.application.routing.background.routing import (
    restore_target_of,
    site_hostname,
    site_route_name,
)
from provisioning.core.domain import HostingStatus, ReleaseStatus, RoutingStatus

DEFERRAL_BUDGET = 10
"""How many rounds in a row a release's delete may be deferred ("not now": an admission
refusal) before it is given up: recorded ``failed`` once and not tried again until the worker
restarts or the release's status changes (no retries without a budget). Ten rounds are five
minutes at the default ``RECONCILE_INTERVAL``, far beyond the moment the routed-release policy's
cached route lags behind a route move."""

_WRITE_ERRORS = (
    ClusterUnavailable,
    TenantNotReady,
    FenceRejected,
    ApplyConflict,
    ClusterRefused,
    TimeoutError,
)


@dataclass(frozen=True, slots=True)
class ReconcileSettings:
    """``site_domain`` names the site's hostname, ``image_registry`` the live release's image
    (as the deploy did), ``batch_size`` the projects of one round (``RECONCILE_BATCH``), and
    ``call_timeout`` bounds each cluster call."""

    site_domain: str
    image_registry: str
    batch_size: int
    call_timeout: timedelta

    def validate(self) -> None:
        """``ValueError`` naming the field, never the value."""
        if not self.site_domain:
            raise ValueError("reconcile settings: site_domain is required")
        if not self.image_registry:
            raise ValueError("reconcile settings: image_registry is required")
        if self.batch_size < 1:
            raise ValueError("reconcile settings: batch_size must be positive")
        if self.call_timeout <= timedelta(0):
            raise ValueError("reconcile settings: call_timeout must be positive")


type ObjectRef = tuple[UUID, SiteObjectKind, str]
type _ReleaseKey = tuple[UUID, UUID, ReleaseStatus]


@dataclass(frozen=True, slots=True)
class RoundReport:
    """What one round saw and did, for tests and the round's summary log line."""

    projects: tuple[UUID, ...] = ()
    skipped: tuple[UUID, ...] = ()
    failed: tuple[UUID, ...] = ()
    reapplied: tuple[ObjectRef, ...] = ()
    deleted: tuple[tuple[UUID, UUID], ...] = ()
    orphans: tuple[UUID, ...] = ()
    foreign: tuple[tuple[UUID, SiteObjectKind, str, tuple[str, ...]], ...] = ()
    fence_ahead: tuple[tuple[UUID, SiteObjectKind, str, int], ...] = ()
    given_up: tuple[tuple[UUID, UUID], ...] = ()


@dataclass(slots=True)
class _Round:
    projects: list[UUID] = field(default_factory=list)
    skipped: list[UUID] = field(default_factory=list)
    failed: list[UUID] = field(default_factory=list)
    reapplied: list[ObjectRef] = field(default_factory=list)
    deleted: list[tuple[UUID, UUID]] = field(default_factory=list)
    orphans: list[UUID] = field(default_factory=list)
    foreign: list[tuple[UUID, SiteObjectKind, str, tuple[str, ...]]] = field(default_factory=list)
    fence_ahead: list[tuple[UUID, SiteObjectKind, str, int]] = field(default_factory=list)
    given_up: list[tuple[UUID, UUID]] = field(default_factory=list)

    def report(self) -> RoundReport:
        return RoundReport(
            projects=tuple(self.projects),
            skipped=tuple(self.skipped),
            failed=tuple(self.failed),
            reapplied=tuple(self.reapplied),
            deleted=tuple(self.deleted),
            orphans=tuple(self.orphans),
            foreign=tuple(self.foreign),
            fence_ahead=tuple(self.fence_ahead),
            given_up=tuple(self.given_up),
        )


class Reconciler:
    """One round at a time (module docstring); keeps only its paging cursor."""

    __slots__ = (
        "_after",
        "_cleanup",
        "_cluster",
        "_deferrals",
        "_given_up",
        "_logger",
        "_settings",
        "_store",
    )

    def __init__(
        self,
        *,
        store: ReconcileStore,
        cluster: SiteCluster,
        cleanup: ReleaseCleanup,
        settings: ReconcileSettings,
        logger: logging.Logger,
    ) -> None:
        """Validates ``settings`` (``ValueError``). Calls no port."""
        settings.validate()
        self._store = store
        self._cluster = cluster
        self._cleanup = cleanup
        self._settings = settings
        self._logger = logger
        self._after: UUID | None = None
        # Consecutive deferred deletes and given-up deletes, by (project, release, status); in
        # memory, so a restart starts again. Pruned when the release leaves the sweep.
        self._deferrals: dict[_ReleaseKey, int] = {}
        self._given_up: set[_ReleaseKey] = set()

    async def round(self) -> RoundReport:
        """Reconcile the next batch of projects and report orphan tenants. A store error reading
        the batch is raised (the loop logs it); a project's error is logged and the round goes
        on."""
        acc = _Round()
        batch = await self._store.projects_after(self._after, self._settings.batch_size)
        full = len(batch) >= self._settings.batch_size
        self._after = batch[-1].project_id if full and batch else None
        for project in batch:
            acc.projects.append(project.project_id)
            try:
                await self._project(project, acc)
            except Exception as exc:  # one project's failure must not end the round
                acc.failed.append(project.project_id)
                self._logger.warning(
                    "reconcile of project %s failed (error=%s)",
                    project.project_id,
                    type(exc).__name__,
                )
        await self._orphans(acc)
        report = acc.report()
        self._logger.info(
            "reconcile round (projects=%d skipped=%d failed=%d reapplied=%d deleted=%d "
            "orphans=%d foreign=%d fence_ahead=%d)",
            len(report.projects),
            len(report.skipped),
            len(report.failed),
            len(report.reapplied),
            len(report.deleted),
            len(report.orphans),
            len(report.foreign),
            len(report.fence_ahead),
        )
        return report

    async def _project(self, project: ProjectRecord, acc: _Round) -> None:
        pid = project.project_id
        if project.routing_status is not RoutingStatus.STABLE or project.unfinished_command:
            acc.skipped.append(pid)
            return
        try:
            inventory = await self._call(lambda: self._cluster.inventory(pid))
        except TenantNotReady:
            acc.skipped.append(pid)
            return
        objects = {(o.kind, o.name): o for o in inventory.objects}
        held = self._report_held(project, inventory.objects, acc)
        await self._live_workload(project, objects, held, acc)
        await self._route(project, objects, held, acc)
        await self._sweep(project, inventory.objects, acc)

    def _report_held(
        self, project: ProjectRecord, objects: Sequence[SiteObject], acc: _Round
    ) -> set[tuple[SiteObjectKind, str]]:
        """Report the objects another manager wrote or that carry a fence above the database's;
        they are never written. Returns their keys."""
        held: set[tuple[SiteObjectKind, str]] = set()
        pid = project.project_id
        for obj in objects:
            if obj.foreign_managers:
                held.add((obj.kind, obj.name))
                acc.foreign.append((pid, obj.kind, obj.name, obj.foreign_managers))
                self._logger.warning(
                    "reconcile: foreign field managers on %s %s of project %s, not overwritten "
                    "(managers=%s)",
                    obj.kind.value,
                    obj.name,
                    pid,
                    ",".join(obj.foreign_managers),
                )
            number = obj.command_number
            if number is not None and (number < 0 or number > project.last_command_number):
                held.add((obj.kind, obj.name))
                acc.fence_ahead.append((pid, obj.kind, obj.name, number))
                self._logger.warning(
                    "reconcile: %s %s of project %s carries a command number above the "
                    "database's or not a number, not overwritten (command_number=%d "
                    "last_command_number=%d)",
                    obj.kind.value,
                    obj.name,
                    pid,
                    number,
                    project.last_command_number,
                )
        return held

    async def _live_workload(
        self,
        project: ProjectRecord,
        objects: Mapping[tuple[SiteObjectKind, str], SiteObject],
        held: set[tuple[SiteObjectKind, str]],
        acc: _Round,
    ) -> None:
        live, digest = project.live_release_id, project.live_release_digest
        if live is None or digest is None:
            return
        name = f"rel-{live}"
        kinds = (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE)
        missing = [k for k in kinds if (k, name) not in objects]
        if not missing or any((k, name) in held for k in kinds):
            return
        pid = project.project_id
        workload = ReleaseWorkload(
            project_id=pid,
            release_id=live,
            image=site_image(self._settings.image_registry, pid, digest),
            command_number=project.last_command_number,
        )
        if await self._write(project, lambda: self._cluster.apply_release(workload), name):
            acc.reapplied.extend((pid, kind, name) for kind in missing)

    async def _route(
        self,
        project: ProjectRecord,
        objects: Mapping[tuple[SiteObjectKind, str], SiteObject],
        held: set[tuple[SiteObjectKind, str]],
        acc: _Round,
    ) -> None:
        pid = project.project_id
        key = (SiteObjectKind.HTTP_ROUTE, site_route_name(pid))
        if key in held:
            return
        expected = (
            RouteTarget(RouteTargetKind.OFFLINE)
            if project.hosting_status is HostingStatus.OFFLINE
            else restore_target_of(project.live_release_id)
        )
        if key not in objects:
            needed = (
                project.live_release_id is not None
                or project.hosting_status is HostingStatus.OFFLINE
            )
        else:
            status = await self._call(lambda: self._cluster.route_status(pid))
            needed = status.target != expected
        if not needed:
            return
        subdomain = project.subdomain
        hostnames = (site_hostname(subdomain, self._settings.site_domain),)
        number = project.last_command_number
        if await self._write(
            project,
            lambda: self._cluster.apply_route(
                pid, expected, hostnames, number, subdomain=subdomain
            ),
            key[1],
        ):
            acc.reapplied.append((pid, SiteObjectKind.HTTP_ROUTE, key[1]))

    async def _sweep(
        self, project: ProjectRecord, objects: Sequence[SiteObject], acc: _Round
    ) -> None:
        pid = project.project_id
        found = _workload_releases(objects)
        if not found:
            return
        statuses = await self._store.release_statuses(pid, found)
        for release_id in found:
            if release_id not in statuses:
                self._logger.warning(
                    "reconcile: the workload of release %s has no row in project %s, not deleted",
                    release_id,
                    pid,
                )
        keys = {r: (pid, r, statuses[r]) for r in found if statuses.get(r) in CLEANABLE}
        self._prune(pid, set(keys.values()))
        finished = [r for r, key in keys.items() if key not in self._given_up]
        if not finished or not await self._idle(project):
            return
        result = await self._cleanup.delete_releases(
            pid,
            finished,
            project.last_command_number,
            live_release_id=project.live_release_id,
        )
        for release_id in result.deleted:
            self._deferrals.pop(keys[release_id], None)
            acc.deleted.append((pid, release_id))
        for release_id, code in result.deferred:
            key = keys[release_id]
            count = self._deferrals.get(key, 0) + 1
            if count < DEFERRAL_BUDGET:
                self._deferrals[key] = count
                continue
            self._deferrals.pop(key, None)
            self._given_up.add(key)
            self._cleanup.give_up(pid, release_id, code, count)
            acc.given_up.append((pid, release_id))

    def _prune(self, project_id: UUID, current: set[_ReleaseKey]) -> None:
        """Forget the counts and give-ups of this project's releases that are no longer swept
        with the same status (deleted, or their status changed)."""
        for key in [k for k in self._deferrals if k[0] == project_id and k not in current]:
            del self._deferrals[key]
        self._given_up = {k for k in self._given_up if k[0] != project_id or k in current}

    async def _orphans(self, acc: _Round) -> None:
        try:
            tenants = await self._call(self._cluster.list_tenant_projects)
            known = await self._store.known_projects(tenants)
        except Exception as exc:  # reporting only; the next round tries again
            self._logger.warning(
                "reconcile: listing tenant namespaces failed (error=%s)", type(exc).__name__
            )
            return
        for project_id in tenants:
            if project_id not in known:
                acc.orphans.append(project_id)
                self._logger.warning(
                    "reconcile: orphan tenant namespace wb-%s has no project_hosting row",
                    project_id,
                )

    async def _idle(self, project: ProjectRecord) -> bool:
        if await self._store.still_idle(project.project_id, project.last_command_number):
            return True
        self._logger.info(
            "reconcile: project %s changed since the round read it; left to its command",
            project.project_id,
        )
        return False

    async def _write(
        self, project: ProjectRecord, write: Callable[[], Awaitable[None]], name: str
    ) -> bool:
        """Re-read the project, then make one never-forced write; a refusal or a transient error
        is logged and False."""
        if not await self._idle(project):
            return False
        try:
            await self._call(write)
        except _WRITE_ERRORS as exc:
            self._logger.warning(
                "reconcile: %s of project %s not re-applied (error=%s)",
                name,
                project.project_id,
                type(exc).__name__,
            )
            return False
        self._logger.info(
            "reconcile: %s of project %s re-applied at command number %d",
            name,
            project.project_id,
            project.last_command_number,
        )
        return True

    async def _call[T](self, call: Callable[[], Awaitable[T]]) -> T:
        async with asyncio.timeout(self._settings.call_timeout.total_seconds()):
            return await call()


_WORKLOAD_KINDS: Final = (SiteObjectKind.DEPLOYMENT, SiteObjectKind.SERVICE)


def _workload_releases(objects: Sequence[SiteObject]) -> list[UUID]:
    """The distinct releases of the Deployments and Services in ``objects``, sorted."""
    found: set[UUID] = set()
    for o in objects:
        release_id = o.release_id
        if release_id is not None and o.kind in _WORKLOAD_KINDS:
            found.add(release_id)
    return sorted(found)
