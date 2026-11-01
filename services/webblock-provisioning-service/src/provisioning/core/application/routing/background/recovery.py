"""Recovery on the worker (decision 0014 section 21 rulings 8 and 9; task W6-B).

``RouteAbandoner`` is the abandon hook of the commands that move the route: promote, operator
rollback, suspend_hosting and resume_hosting. The consumer calls it before it fails a command for
its deadline or its attempt budget (decision 0011 section 3). Without it, a command that ran out
of time while ``routing_status`` was ``changing`` stayed ``running`` for ever: the consumer's own
failure write refuses a changing route, and every new command was refused (the gap analysis's
first defect). The hook ends such a command ``stable`` or ``uncertain``, never stuck:

- it points the route at the recorded target, never forced, within ``restore_deadline``: the live
  release, or the offline page without one, for a promote; where the recorded state says for an
  operator rollback (decision 0014 section 13); the offline page for a suspend; the live release
  (or the offline page) for a resume;
- confirmed, it records the matching outcome: ``restore_verified`` (the candidate ``rolled_back``)
  or ``route_removed_verified`` (a first publish, the release ``failed``) for a promote, with the
  last go-live try's code when the dispatch row still holds one (decision 0027 T9), else the
  consumer's code; ``rollback_verified`` for a rollback, ``hosting_verified`` for a hosting change;
- not confirmed, it records ``route_uncertain``: the operator rollback is then accepted. The
  alert (decision 0014 section 23) comes from the audited store the host wraps around ``store``
  (``audited.AuditedRouteCommandStore``), the single source of it.

A command whose route never changed (routing ``stable``) is left to the consumer, which fails it.

``ReleaseCleanup`` deletes the Deployments and Services of superseded, rolled-back and failed
releases through ``SiteCluster.delete_release`` (ruling 8), fenced by the command's number. It
never deletes the release the route points at, nor the live release: both are checked here,
before any delete, against the route as the cluster reports it and the recorded live release; the
adapter and the admission policy refuse the routed release again. It is best effort: a failed
delete is logged and audited, and the reconciler's sweep tries again later. A refusal of the
admission policy ``webblock-site-routed-release`` (``ClusterRefused``: it reads the route from the
API server's cache, so it may refuse for a moment after a route move) or of the adapter's own
guard (``FenceRejected``) means "not now": the release is skipped this round, audited
``skipped``, and the next sweep deletes it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID

from provisioning.core.application.jobs.claims import Claim
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.ports.site_cluster import (
    DELETE_ERRORS,
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    RouteTarget,
    RouteTargetKind,
    SiteCluster,
    TenantNotReady,
)
from provisioning.core.application.routing.background.routing import (
    CODE_OPERATOR_ROLLBACK,
    WORKER_ACTOR,
    PointResult,
    RoutePointer,
    RoutingSettings,
    restore_target_of,
)
from provisioning.core.application.routing.ports import RouteCommandStore, WorkloadCleanup
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
)
from provisioning.core.domain import (
    GO_LIVE_TRY_CODES,
    CommandKind,
    Failure,
    HostingStatus,
    Outcome,
    OutcomeInput,
    ReleaseStatus,
    RoutingStatus,
    hosting_verified,
    restore_verified,
    rollback_verified,
    route_removed_verified,
    route_uncertain,
    validate_code,
)

CLEANABLE: Final = frozenset(
    {ReleaseStatus.SUPERSEDED, ReleaseStatus.ROLLED_BACK, ReleaseStatus.FAILED}
)
"""The release statuses whose workload is deleted: all three are terminal (ruling 8)."""

_ROUTE_KINDS: Final = frozenset(
    {
        CommandKind.PROMOTE,
        CommandKind.ROLLBACK,
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
    }
)

type _Verified = Callable[[OutcomeInput], Outcome]


class RouteAbandoner:
    """The abandon hook of the route-moving commands (module docstring)."""

    __slots__ = ("_cleanup", "_clock", "_pointer", "_settings", "_store")

    def __init__(
        self,
        *,
        store: RouteCommandStore,
        cluster: SiteCluster,
        clock: Clock,
        settings: RoutingSettings,
        cleanup: WorkloadCleanup | None = None,
    ) -> None:
        """Validates ``settings`` (``ValueError``). Calls no port."""
        settings.validate()
        self._store = store
        self._clock = clock
        self._settings = settings
        self._cleanup = cleanup
        self._pointer = RoutePointer(cluster, settings)

    @property
    def longest_run(self) -> timedelta:
        """The longest an ``abandon`` waits for the cluster: one restore deadline. The caller's
        bound adds the time of its two database transactions."""
        return self._settings.restore_deadline

    async def abandon(self, claim: Claim, failure_code: str) -> str | None:
        """End ``claim``'s command, which the consumer is about to fail with ``failure_code``
        (``deadline_exceeded`` or ``attempts_exhausted``), when its route is ``changing``;
        otherwise do nothing. Returns the failure code the outcome wrote to the command, None
        when it wrote none (routing not ``changing``, another kind, or a confirmed rollback or
        hosting change, whose command succeeds).

        A promote whose restore is confirmed ends with the dispatch row's last failure code when
        it is a go-live try code (decision 0027 T9): the deadline passed after a failed try, and
        the client sees why the go-live failed. Any other row code (none, ``worker_stopping``,
        ``transient_failure``) gives ``failure_code``. An unconfirmed restore writes
        ``route_uncertain`` with ``failure_code``. ``ClaimLost`` and ``StaleFence`` from the store
        propagate: then a newer claim or command owns the project and nothing more is written."""
        if claim.ref.kind not in _ROUTE_KINDS:
            return None
        validate_code(failure_code)
        fence = claim.fence()
        inp = await self._store.load_route_command(fence)
        if inp.hosting.routing_status is not RoutingStatus.CHANGING:
            return None
        target, verified = self._plan(inp, Failure(_end_code(claim, failure_code)))
        result = await self._pointer.point(
            fence.project_id,
            target,
            fence.command_number,
            self._settings.restore_deadline,
            subdomain=inp.hosting.subdomain,
        )
        if result is PointResult.CONFIRMED:
            out = await self._store.apply_route_outcome(fence, verified)
            if self._cleanup is not None:
                await self._cleanup.after_outcome(fence, out)
            return out.command.failure_code
        failure = Failure(failure_code)
        out = await self._store.apply_route_outcome(
            fence, lambda i: route_uncertain(i, failure, self._clock.now())
        )
        return out.command.failure_code

    def _plan(self, inp: OutcomeInput, failure: Failure) -> tuple[RouteTarget, _Verified]:
        """The recorded target of the command's route and the outcome a confirmed route
        records."""
        kind = inp.command.kind
        live = inp.hosting.live_release_id
        now = self._clock.now
        if kind is CommandKind.PROMOTE:
            target = restore_target_of(live)
            if target.kind is RouteTargetKind.RELEASE:
                return target, lambda i: restore_verified(i, failure, now())
            return target, lambda i: route_removed_verified(i, failure, now())
        if kind is CommandKind.ROLLBACK:
            target = (
                RouteTarget(RouteTargetKind.OFFLINE)
                if inp.hosting.hosting_status is HostingStatus.OFFLINE
                else restore_target_of(live)
            )
            rollback = Failure(CODE_OPERATOR_ROLLBACK)
            return target, lambda i: rollback_verified(i, rollback, now())
        if kind is CommandKind.SUSPEND_HOSTING:
            return RouteTarget(RouteTargetKind.OFFLINE), lambda i: hosting_verified(i, now())
        return restore_target_of(live), lambda i: hosting_verified(i, now())


def _end_code(claim: Claim, failure_code: str) -> str:
    """The code a confirmed restore of ``claim``'s command ends with: the last go-live try's code
    for a promote whose dispatch row still holds one, else the consumer's ``failure_code``."""
    last = claim.last_failure_code
    if claim.ref.kind is CommandKind.PROMOTE and last in GO_LIVE_TRY_CODES:
        return last
    return failure_code


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """What one ``delete_releases`` did: the releases deleted, and those deferred ("not now")
    with the short code of their refusal."""

    deleted: tuple[UUID, ...] = ()
    deferred: tuple[tuple[UUID, str], ...] = ()


class ReleaseCleanup:
    """The guarded, fenced delete of finished releases' workloads (module docstring).
    Implements ``routing.WorkloadCleanup``."""

    __slots__ = ("_audit", "_clock", "_cluster", "_delete_timeout", "_logger")

    def __init__(
        self,
        *,
        cluster: SiteCluster,
        audit: AuditLog,
        clock: Clock,
        delete_timeout: timedelta,
        logger: logging.Logger,
    ) -> None:
        if delete_timeout <= timedelta(0):
            raise ValueError("release cleanup: delete_timeout must be positive")
        self._cluster = cluster
        self._audit = audit
        self._clock = clock
        self._delete_timeout = delete_timeout
        self._logger = logger

    async def after_outcome(self, fence: Fence, outcome: Outcome) -> None:
        """Delete the workload of each release ``outcome`` moved to superseded, rolled_back or
        failed, at the command's number. Called after the outcome is committed, so a release the
        recorded state still needs is never gone first. Never raises for a cluster error."""
        finished = [
            release.id
            for release in (outcome.superseded_release, outcome.release)
            if release is not None and release.status in CLEANABLE
        ]
        await self.delete_releases(
            fence.project_id,
            finished,
            fence.command_number,
            live_release_id=outcome.hosting.live_release_id,
            command_id=fence.command_id,
        )

    async def delete_releases(
        self,
        project_id: UUID,
        release_ids: Iterable[UUID],
        command_number: int,
        *,
        live_release_id: UUID | None,
        command_id: UUID | None = None,
    ) -> CleanupResult:
        """Delete each release's Deployment and Service, fenced by ``command_number``, except
        the live release and the release the route points at now (read first; when the route
        cannot be read nothing is deleted). Returns what was deleted and deferred. Each delete
        is bounded by the delete timeout and audited (``workload_cleanup``); a failed one is
        logged and audited ``failed`` (``skipped`` when deferred) with a short code, never
        raised."""
        ids = tuple(dict.fromkeys(release_ids))
        if not ids:
            return CleanupResult()
        try:
            async with asyncio.timeout(self._delete_timeout.total_seconds()):
                status = await self._cluster.route_status(project_id)
        except (ClusterUnavailable, TenantNotReady, TimeoutError) as exc:
            self._logger.warning(
                "cleanup of project %s skipped: the route cannot be read (%s)",
                project_id,
                type(exc).__name__,
            )
            return CleanupResult()
        routed = status.target.release_id if status.target is not None else None
        keep = {r for r in (live_release_id, routed) if r is not None}
        deleted: list[UUID] = []
        deferred: list[tuple[UUID, str]] = []
        for release_id in ids:
            if release_id in keep:
                self._logger.warning(
                    "cleanup kept release %s of project %s: it is live or routed",
                    release_id,
                    project_id,
                )
                continue
            code = await self._delete(project_id, release_id, command_number)
            self._audit.record(
                AuditEvent(
                    actor_kind=AuditActorKind.SYSTEM,
                    actor=WORKER_ACTOR,
                    action=AuditAction.WORKLOAD_CLEANUP,
                    project_id=project_id,
                    release_id=release_id,
                    command_id=command_id,
                    result=_cleanup_result(code),
                    failure_code=code,
                    at=self._clock.now(),
                    correlation_id=None,
                )
            )
            if code is None:
                deleted.append(release_id)
            elif code in _DEFERRED:
                deferred.append((release_id, code))
        return CleanupResult(deleted=tuple(deleted), deferred=tuple(deferred))

    def give_up(self, project_id: UUID, release_id: UUID, code: str, deferrals: int) -> None:
        """Record once that a release's delete was deferred ``deferrals`` rounds in a row and
        will not be tried again by this process: a warning and a ``failed`` audit event whose
        ``failure_code`` is the refusal's code. The port's ``ClusterRefused`` carries no
        structured policy name, so none is recorded (its message is free text)."""
        self._logger.warning(
            "cleanup of release %s of project %s given up after %d deferred rounds (%s); "
            "not tried again until the worker restarts or the release changes",
            release_id,
            project_id,
            deferrals,
            code,
        )
        self._audit.record(
            AuditEvent(
                actor_kind=AuditActorKind.SYSTEM,
                actor=WORKER_ACTOR,
                action=AuditAction.WORKLOAD_CLEANUP,
                project_id=project_id,
                release_id=release_id,
                command_id=None,
                result=AuditResult.FAILED,
                failure_code=code,
                at=self._clock.now(),
                correlation_id=None,
            )
        )

    async def _delete(self, project_id: UUID, release_id: UUID, command_number: int) -> str | None:
        """One guarded delete; None when done, else the short code of its failure."""
        try:
            async with asyncio.timeout(self._delete_timeout.total_seconds()):
                await self._cluster.delete_release(project_id, release_id, command_number)
        except DELETE_ERRORS as exc:
            code = _delete_failure(exc)
            if code in _DEFERRED:
                # W6-K's admission policy webblock-site-routed-release reads the route from the
                # API server's cache: right after a route move it may refuse a legitimate delete
                # for a moment. Not now: the reconciler's next sweep tries again (bounded by its
                # rounds, never an inline loop).
                self._logger.info(
                    "workload of release %s of project %s: delete deferred to the next sweep (%s)",
                    release_id,
                    project_id,
                    code,
                )
                return code
            self._logger.warning(
                "workload of release %s of project %s not deleted: %s",
                release_id,
                project_id,
                code,
            )
            return code
        self._logger.info("workload of release %s of project %s deleted", release_id, project_id)
        return None


_DELETE_FAILURES: Final[tuple[tuple[type[Exception], str], ...]] = (
    (ClusterUnavailable, "cluster_unavailable"),
    (TenantNotReady, "tenant_not_ready"),
    (FenceRejected, "fence_rejected"),
    (ApplyConflict, "apply_conflict"),
    (ClusterRefused, "cluster_refused"),
    (TimeoutError, "timeout"),
)


_DEFERRED: Final = frozenset({"cluster_refused", "fence_rejected"})
"""Refusals that mean "not now" (the routed release, as the admission policy or the adapter sees
it): the release is skipped this round, audited ``skipped``, and swept again later."""


def _cleanup_result(code: str | None) -> AuditResult:
    if code is None:
        return AuditResult.SUCCEEDED
    return AuditResult.SKIPPED if code in _DEFERRED else AuditResult.FAILED


def _delete_failure(exc: Exception) -> str:
    """The short code of a failed delete (an audit ``failure_code``, never error text)."""
    return next(code for cls, code in _DELETE_FAILURES if isinstance(exc, cls))
