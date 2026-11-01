"""Promote and operator rollback, run by the worker for a claimed command (decision 0001 sections 3
and 7, decision 0014 section 3).

Both move only the project's HTTPRoute; a candidate's Deployment is never changed. Each follows
"store intent, act outside any transaction, inspect, record the verified outcome":

1. The first fenced write records the intent (``start_route_change``: a promote's release goes
   ``promoting`` and ``routing_status`` ``changing``; an operator rollback moves ``uncertain`` to
   ``changing``).
2. Outside any transaction the route is pointed at the target with the command's number and
   confirmed through ``route_status``; a promotion also waits until the site answers through the
   Gateway with the candidate's release header.
3. One fenced write records what was verified (``confirm_live``, ``restore_verified``,
   ``route_removed_verified`` or ``route_uncertain``).

A promotion proves the candidate before the site's route moves (decision 0014 section 17): it
points the per-project route ``preview-<projectId>`` (the one hostname
``<subdomain>-preview.<site domain>``, FR-20) at the candidate, confirms it at the
command's number, and probes the preview hostname until it answers 200 with
``X-Webblock-Release`` naming the candidate. Every probe names the release it expects, so a
response from the old backend, which the Gateway may still use for a moment after a route is
accepted, never counts.

One promote command makes up to ``GO_LIVE_TRIES`` (3) tries, one per claim (FR-21, FR-22;
decision 0027). A try is the preview, then the switch. A failed try 1 or 2 puts the route back on
the live release, or on the offline page without one, and gives the claim back with
``RetryLater`` and the try's wait (30 s, then 60 s): the release stays ``promoting``, routing
``changing`` and the command ``running``. Only try 3 ends the release ``rolled_back`` or
``failed`` with its code. A preview that fails therefore never ends the command on try 1 or 2,
and the site's route never moves before the preview passes.

A claim that finds ``routing_status`` ``changing`` puts the route back first: a try that was
interrupted may have left it on the unproven candidate. An interrupted try counts and has no wait.
Promotion and rollback write no step rows (decision 0014 section 8).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from provisioning.core.application.jobs.claims import (
    FAILURE_CODE_TRANSIENT,
    Claim,
    PermanentFailure,
    RetryLater,
)
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.dependencies import Clock, Entitlement
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    RouteStatus,
    RouteTarget,
    RouteTargetKind,
    SiteCluster,
    SiteDependencyUnavailable,
    SiteObjectKind,
    TenantNotReady,
)
from provisioning.core.application.routing.ports import RouteCommandStore, WorkloadCleanup
from provisioning.core.application.shared.audit import (
    AuditAction,
    AuditActorKind,
    AuditEvent,
    AuditLog,
    AuditResult,
    NullAuditLog,
)
from provisioning.core.application.shared.errors import CorruptRow, LimitReached
from provisioning.core.domain import (
    CODE_ATTEMPTS_EXHAUSTED,
    CODE_CANDIDATE_NOT_ANSWERING,
    CODE_DEPENDENCY_UNAVAILABLE,
    CODE_ROUTE_NOT_ACCEPTED,
    CODE_ROUTE_REFUSED,
    CODE_SITE_NOT_ANSWERING,
    GO_LIVE_TRIES,
    CommandKind,
    Failure,
    HostingStatus,
    InvalidCommandTarget,
    Outcome,
    OutcomeInput,
    RoutingStatus,
    confirm_live,
    is_error,
    preview_label,
    restore_verified,
    rollback_verified,
    route_removed_verified,
    route_uncertain,
    start_route_change,
    wait_after_failed_try,
)

# Failure codes this module records (decision 0001 section 9 format).
CODE_ENTITLEMENT_LOST = "entitlement_lost"
CODE_PROMOTION_INTERRUPTED = "promotion_interrupted"
CODE_ROUTE_RESTORE_UNCONFIRMED = "route_restore_unconfirmed"
CODE_OPERATOR_ROLLBACK = "operator_rollback"
# The audit failure code of a reclaimed route that was not confirmed (decision 0014 section 23).
CODE_ROUTE_RECLAIM_UNCONFIRMED = "route_reclaim_unconfirmed"

PROBE_CONFIRMATIONS = 3
"""QA-08: after the first passing probe run (``SiteCluster.probe_site``, itself
``SITE_PROBE_SUCCESSES`` consecutive 200s), this many more passing runs confirm the answer."""
PROBE_FAILURES_AFTER_ANSWER = 2
"""QA-08: after the first passing run, at most this many failed runs are tolerated before the
probe gives up (a Gateway node that has not yet taken the new route); one more ends it with its
failure code, however much of the deadline is left. Fewer than ``PROBE_CONFIRMATIONS``, so a site
that answers every other run never passes. Before the first passing run (pods starting, the
Gateway still on the old backend) failures are retried until the deadline."""

WORKER_ACTOR = "worker"
"""The ``actor`` of the audit events the worker writes for itself."""
OPERATOR_ACTOR = "operator"
"""The ``actor`` of an operator's audit event when its command names no requester."""

_LOGGER = logging.getLogger(__name__)
"""Writes one info line per failed go-live try (decision 0027; design G15)."""

_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
# Security review S5: every host the worker builds stays within 253 characters, even for a
# sub-domain of 63 characters that the database allows and FR-55 refuses: its preview host
# ``<63 characters>-preview.<site domain>`` is 72 characters plus the site domain, so the site
# domain is at most 253 - 72 = 181 characters.
_MAX_SITE_DOMAIN_LEN = 181


@dataclass(frozen=True, slots=True)
class RoutingSettings:
    """Every value is required (OD-15).

    - ``site_domain``: the suffix of a site's hostname ``<subdomain>.<site_domain>``
      (``sites.webblock.test`` in the prototype; OD-36 stays open);
    - ``switch_deadline``: how long a promotion waits for its preview route to be accepted and
      the candidate to answer on it, then again for the switched route to be accepted and the site
      to answer, and a hosting change for its route to be accepted;
    - ``restore_deadline``: how long a restore keeps applying and confirming the route, retrying
      transient cluster errors;
    - ``poll_interval``: the pause between two reads of the route or two probes.
    """

    site_domain: str
    switch_deadline: timedelta
    restore_deadline: timedelta
    poll_interval: timedelta

    def validate(self) -> None:
        """A lowercase DNS suffix, positive durations and a poll interval shorter than both
        deadlines (``ValueError``, naming the field, never the value)."""
        if len(self.site_domain) > _MAX_SITE_DOMAIN_LEN or not all(
            _DNS_LABEL.fullmatch(label) for label in self.site_domain.split(".")
        ):
            raise ValueError(
                "routing settings: site_domain must be a lowercase DNS name"
                f" of at most {_MAX_SITE_DOMAIN_LEN} characters"
            )
        for name, value in (
            ("switch_deadline", self.switch_deadline),
            ("restore_deadline", self.restore_deadline),
            ("poll_interval", self.poll_interval),
        ):
            if value <= timedelta(0):
                raise ValueError(f"routing settings: {name} must be positive")
        if self.poll_interval >= min(self.switch_deadline, self.restore_deadline):
            raise ValueError("routing settings: poll_interval must be shorter than the deadlines")


def site_hostname(subdomain: str, site_domain: str) -> str:
    """``<subdomain>.<site_domain>``: the one hostname of a site, from its own copy of the
    sub-domain in ``project_hosting.subdomain`` (Notion "5 · Provisioning"; decision 0023 G18)."""
    return f"{subdomain}.{site_domain}"


def preview_hostname(subdomain: str, site_domain: str) -> str:
    """``<subdomain>-preview.<site_domain>``: the hostname of a project's preview route, through
    which a promotion probes its candidate (decision 0014 section 17; FR-20). It is one label under
    the site domain; it equals no site host because the bootstrap hosts no sub-domain ending in
    ``-preview``, and it is one DNS label because it hosts none over 55 characters (FR-55)."""
    return f"{preview_label(subdomain)}.{site_domain}"


def site_route_name(project_id: UUID) -> str:
    """``route-<projectId>``: the name of a project's one site route (the ``SiteCluster`` port)."""
    return f"route-{project_id}"


def restore_target_of(live_release_id: UUID | None) -> RouteTarget:
    """Where a restore points the route: the live release, or the offline page without one
    (ruling K9: the route is never deleted)."""
    if live_release_id is None:
        return RouteTarget(RouteTargetKind.OFFLINE)
    return RouteTarget(RouteTargetKind.RELEASE, live_release_id)


class PointResult(StrEnum):
    """How pointing the route ended."""

    CONFIRMED = "confirmed"  # accepted by our Gateway, at the target, at the command's number
    REFUSED = "refused"  # the cluster refused the route (validation, admission, conflict, fence)
    UNCONFIRMED = "unconfirmed"  # not confirmed before the deadline


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What ``RoutePointer.probe`` saw: whether ``expected`` answered steadily, and whether the
    last failed run was ``expected`` answering 503 (``SiteDependencyUnavailable``)."""

    answered: bool
    outage: bool


class RoutePointer:
    """Points a project's route (or its preview route) at a target and confirms it, retrying
    transient cluster errors (``ClusterUnavailable``, ``TenantNotReady``) until a deadline. Shared
    by the routing and the hosting use cases."""

    __slots__ = ("_cluster", "_interval", "_site_domain")

    def __init__(self, cluster: SiteCluster, settings: RoutingSettings) -> None:
        self._cluster = cluster
        self._interval = settings.poll_interval.total_seconds()
        self._site_domain = settings.site_domain

    async def point(
        self,
        project_id: UUID,
        target: RouteTarget,
        command_number: int,
        within: timedelta,
        *,
        subdomain: str,
    ) -> PointResult:
        """Apply the site's route on ``<subdomain>.<site domain>`` (once it succeeds, not
        again), then read ``route_status`` until it is accepted at ``target`` with
        ``command_number``. A refusal ends at once; a transient error is retried; the deadline is
        ``UNCONFIRMED``, also when it ends a cluster call. ``subdomain`` is the hosting row's."""
        hostnames = (site_hostname(subdomain, self._site_domain),)
        return await self._confirm(
            lambda: self._cluster.apply_route(
                project_id, target, hostnames, command_number, subdomain=subdomain
            ),
            lambda: self._cluster.route_status(project_id),
            target,
            command_number,
            within,
        )

    async def reclaim(
        self,
        project_id: UUID,
        target: RouteTarget,
        command_number: int,
        within: timedelta,
        *,
        subdomain: str,
    ) -> PointResult:
        """As ``point``, with the forced ``reclaim_route`` in place of ``apply_route``: only the
        operator rollback calls it (decision 0014 section 23 item 3)."""
        hostnames = (site_hostname(subdomain, self._site_domain),)
        return await self._confirm(
            lambda: self._cluster.reclaim_route(
                project_id, target, hostnames, command_number, subdomain=subdomain
            ),
            lambda: self._cluster.route_status(project_id),
            target,
            command_number,
            within,
        )

    async def point_preview(
        self,
        project_id: UUID,
        release_id: UUID,
        command_number: int,
        within: timedelta,
        *,
        subdomain: str,
    ) -> PointResult:
        """As ``point``, for the preview route ``preview-<projectId>`` on
        ``<subdomain>-preview.<site domain>`` and the candidate."""
        hostname = preview_hostname(subdomain, self._site_domain)
        return await self._confirm(
            lambda: self._cluster.apply_preview(
                project_id, release_id, hostname, command_number, subdomain=subdomain
            ),
            lambda: self._cluster.preview_status(project_id),
            RouteTarget(RouteTargetKind.RELEASE, release_id),
            command_number,
            within,
        )

    async def _confirm(
        self,
        apply: Callable[[], Awaitable[None]],
        read: Callable[[], Awaitable[RouteStatus]],
        target: RouteTarget,
        command_number: int,
        within: timedelta,
    ) -> PointResult:
        applied = False
        try:
            async with asyncio.timeout(within.total_seconds()):
                while True:
                    try:
                        if not applied:
                            await apply()
                            applied = True
                        status = await read()
                        if (
                            status.accepted
                            and status.target == target
                            and status.command_number == command_number
                        ):
                            return PointResult.CONFIRMED
                    except ClusterRefused, ApplyConflict, FenceRejected:
                        return PointResult.REFUSED
                    except ClusterUnavailable, TenantNotReady:
                        pass  # transient: try again after the pause, within the deadline
                    await asyncio.sleep(self._interval)
        except TimeoutError:
            return PointResult.UNCONFIRMED

    async def answers(
        self, project_id: UUID, hostname: str, expected: RouteTarget, within: timedelta
    ) -> bool:
        """``probe(...).answered``."""
        return (await self.probe(project_id, hostname, expected, within)).answered

    async def probe(
        self,
        project_id: UUID,
        hostname: str,
        expected: RouteTarget,
        within: timedelta,
        *,
        preview_of: str | None = None,
    ) -> ProbeResult:
        """Probe ``hostname`` through the Gateway until ``expected`` answers it steadily (200 and
        the release header naming ``expected``), within ``within`` (QA-08). ``preview_of`` names
        the project's sub-domain when ``hostname`` is its preview host, so only that probe carries
        the preview token (FR-52.1).

        Before the first passing run every failure is retried until the deadline (slow start).
        After it, ``PROBE_CONFIRMATIONS`` more passing runs are needed, and more than
        ``PROBE_FAILURES_AFTER_ANSWER`` failed runs end the probe False at once: a site that
        answers only some of the time is never taken for one that answers. An unreachable API
        server (``ClusterUnavailable``) is neither a pass nor a failure. A run that raises
        ``SiteDependencyUnavailable`` (``expected`` answered 503) is a failed run; ``outage``
        says whether the last failed run was one (task H5a)."""
        answered = outage = False
        confirmed = failed = 0
        try:
            async with asyncio.timeout(within.total_seconds()):
                while True:
                    try:
                        passed = await self._cluster.probe_site(
                            project_id, hostname, expected, preview_of=preview_of
                        )
                    except ClusterUnavailable:
                        passed = None  # transient: probe again after the pause
                    except SiteDependencyUnavailable:
                        passed = False
                        outage = True
                    else:
                        if passed is False:
                            outage = False
                    if passed is True:
                        if answered:
                            confirmed += 1
                        answered = True
                        if confirmed >= PROBE_CONFIRMATIONS:
                            return ProbeResult(answered=True, outage=False)
                    elif passed is False and answered:
                        failed += 1
                        if failed > PROBE_FAILURES_AFTER_ANSWER:
                            return ProbeResult(answered=False, outage=outage)
                    await asyncio.sleep(self._interval)
        except TimeoutError:
            return ProbeResult(answered=False, outage=outage)


class Routing:
    """The promote and rollback use cases."""

    __slots__ = (
        "_audit",
        "_cleanup",
        "_clock",
        "_cluster",
        "_entitlement",
        "_pointer",
        "_settings",
        "_store",
    )

    def __init__(
        self,
        *,
        store: RouteCommandStore,
        cluster: SiteCluster,
        entitlement: Entitlement,
        clock: Clock,
        settings: RoutingSettings,
        audit: AuditLog | None = None,
        cleanup: WorkloadCleanup | None = None,
    ) -> None:
        """Validates ``settings`` (``ValueError``). Calls no port. ``audit`` receives the
        operator rollback's reclaim (None: dropped); ``cleanup``
        deletes the workloads a verified outcome left behind (None: they stay for the
        reconciler's sweep)."""
        settings.validate()
        self._store = store
        self._cluster = cluster
        self._entitlement = entitlement
        self._clock = clock
        self._settings = settings
        self._audit: AuditLog = audit if audit is not None else NullAuditLog()
        self._cleanup = cleanup
        self._pointer = RoutePointer(cluster, settings)

    async def promote(self, claim: Claim) -> None:
        """Run one claim of a promote command: try n, n being ``claim.execution_attempt``
        (FR-21; decision 0027).

        - Routing ``changing`` (an earlier claim recorded the intent): first put the route back on
          the restore target (``_to_restore_target``); not confirmed is ``route_uncertain`` with
          ``route_restore_unconfirmed`` and the command ends.
        - A claim past the last try (try 3 was interrupted) runs no try: with routing
          ``changing`` it ends through the restore with ``promotion_interrupted``; with routing
          ``stable`` (no intent was ever recorded) it is ``PermanentFailure(attempts_exhausted)``,
          the release still ``candidate_ready``.
        - Routing ``stable`` (a fresh start): recheck entitlement (``_check_entitlement``:
          ``LimitReached`` is ``PermanentFailure(entitlement_lost)``; Payment unreachable is a
          failed try that touched nothing), then record the intent (release ``promoting``, routing
          ``changing``).
        - The try: the preview, then the switch (``_preview``, ``_switch``). Passed is
          ``confirm_live`` and the cleanup of the superseded release.
        - A failed try 1 or 2: ``_between_tries`` puts the route back and raises ``RetryLater``
          with the try's code and wait; nothing else is written.
        - Each failed try writes one info log line (``_log_failed_try``).
        - A failed try 3: ``_restore`` points the route back, within ``restore_deadline``;
          confirmed is ``restore_verified`` (release ``rolled_back``) or
          ``route_removed_verified`` (release ``failed``, decision 0001 section 2) with the try's
          code, the command failed; unconfirmed is ``route_uncertain``.

        ``ClaimLost`` and ``StaleFence`` from the store propagate: nothing more is written."""
        fence = _fence_of(claim, CommandKind.PROMOTE)
        inp = await self._store.load_route_command(fence)
        release_id = _release_id(inp)
        subdomain = inp.hosting.subdomain
        restore_target = restore_target_of(inp.hosting.live_release_id)
        try_number = claim.execution_attempt
        routing = inp.hosting.routing_status
        if routing is RoutingStatus.UNCERTAIN:
            raise CorruptRow(f"promote command {fence.command_id} while routing is uncertain")
        if routing is RoutingStatus.CHANGING and not await self._to_restore_target(fence, inp):
            await self._uncertain(fence, CODE_ROUTE_RESTORE_UNCONFIRMED)
            return
        if try_number > GO_LIVE_TRIES:
            if routing is RoutingStatus.STABLE:
                raise PermanentFailure(CODE_ATTEMPTS_EXHAUSTED)
            interrupted = Failure(CODE_PROMOTION_INTERRUPTED)
            await self._restore(fence, restore_target, interrupted, subdomain)
            return
        if routing is RoutingStatus.STABLE:
            await self._check_entitlement(fence, release_id, try_number)
            await self._store.apply_route_outcome(
                fence, lambda i: start_route_change(i, self._clock.now())
            )
        failure_code = await self._preview(fence, release_id, subdomain)
        if failure_code is None:
            failure_code = await self._switch(fence, release_id, subdomain)
        if failure_code is None:
            out = await self._store.apply_route_outcome(
                fence, lambda i: confirm_live(i, self._clock.now())
            )
            await self._clean(fence, out)
            return
        if try_number < GO_LIVE_TRIES:
            await self._between_tries(fence, inp, try_number, failure_code)
            return
        _log_failed_try(fence, release_id, try_number, failure_code, None)
        await self._restore(fence, restore_target, Failure(failure_code), subdomain)

    async def _to_restore_target(self, fence: Fence, inp: OutcomeInput) -> bool:
        """Put the site's route on the restore target (the live release, or ``OFFLINE`` without
        one), within one ``restore_deadline`` in all; True when confirmed there. A route already
        accepted at the target is not written (a preview that failed on a fresh start never moved
        it, FR-21.6). A route anywhere else, absent (a first deployment, FR-22.2) or unreadable is
        pointed at the target with this command's number (FR-21.5)."""
        target = restore_target_of(inp.hosting.live_release_id)
        within = self._settings.restore_deadline.total_seconds()
        loop = asyncio.get_running_loop()
        until = loop.time() + within
        try:
            async with asyncio.timeout(within):
                observed = await self._cluster.route_status(fence.project_id)
        except ClusterUnavailable, TenantNotReady, TimeoutError:
            observed = None  # unreadable: point the route all the same
        if observed is not None and observed.accepted and observed.target == target:
            return True
        remaining = until - loop.time()
        if remaining <= 0:
            return False
        result = await self._pointer.point(
            fence.project_id,
            target,
            fence.command_number,
            timedelta(seconds=remaining),
            subdomain=inp.hosting.subdomain,
        )
        return result is PointResult.CONFIRMED

    async def _between_tries(
        self, fence: Fence, inp: OutcomeInput, try_number: int, failure_code: str
    ) -> None:
        """After failed try ``try_number`` (1 or 2): the route goes back to the restore target and
        the claim is given back with ``RetryLater`` (the try's code and its wait, FR-21.2,
        FR-21.4); nothing is written to SQL (decision 0027). A route not confirmed there is
        ``route_uncertain`` with ``route_restore_unconfirmed`` (FR-23.1), and the command ends."""
        wait = wait_after_failed_try(try_number)
        if wait is None:
            raise ValueError(f"try {try_number} is the last try; it has no next try")
        release_id = _release_id(inp)
        if await self._to_restore_target(fence, inp):
            _log_failed_try(fence, release_id, try_number, failure_code, wait)
            raise RetryLater(failure_code, wait)
        _log_failed_try(fence, release_id, try_number, failure_code, None)
        await self._uncertain(fence, CODE_ROUTE_RESTORE_UNCONFIRMED)

    async def rollback(self, claim: Claim) -> None:
        """Run a claimed operator rollback of any uncertain route (decision 0014 section 13):
        record the intent, point the route where the recorded state says (``OFFLINE`` when hosting
        is offline or there is no live release, else the live release), confirm it at the
        command's number within ``restore_deadline``, then ``rollback_verified``: the route
        stable, the command succeeded, and a promoting release still recorded ``rolled_back``
        (``failed`` without a live release). Unconfirmed is ``route_uncertain`` again, the command
        failed. A resumed claim (routing ``changing``) repeats the restore, which is idempotent.

        A route edited by hand (QA-07, decision 0014 section 23 item 3): the rollback reads the
        observed route first. When another field manager wrote it, or its command number is above
        this command's (or not a number), the rollback takes it back with the forced
        ``reclaim_route``, the only forced write, and audits it (``route_reclaim``). A
        never-forced apply that the cluster refuses is followed by the same check and, when it
        holds, by the reclaim."""
        fence = _fence_of(claim, CommandKind.ROLLBACK)
        inp = await self._store.load_route_command(fence)
        _release_id(inp)
        if inp.hosting.routing_status is RoutingStatus.UNCERTAIN:
            await self._store.apply_route_outcome(
                fence, lambda i: start_route_change(i, self._clock.now())
            )
        elif inp.hosting.routing_status is not RoutingStatus.CHANGING:
            raise CorruptRow(f"rollback command {fence.command_id} while routing is stable")
        target = (
            RouteTarget(RouteTargetKind.OFFLINE)
            if inp.hosting.hosting_status is HostingStatus.OFFLINE
            else restore_target_of(inp.hosting.live_release_id)
        )
        result = await self._point_or_reclaim(fence, inp, target)
        if result is PointResult.CONFIRMED:
            out = await self._store.apply_route_outcome(
                fence,
                lambda i: rollback_verified(i, Failure(CODE_OPERATOR_ROLLBACK), self._clock.now()),
            )
            await self._clean(fence, out)
            return
        await self._uncertain(fence, CODE_ROUTE_RESTORE_UNCONFIRMED)

    async def _point_or_reclaim(
        self, fence: Fence, inp: OutcomeInput, target: RouteTarget
    ) -> PointResult:
        """The operator rollback's route write: the forced reclaim when the observed route was
        taken (another manager, or a higher or malformed number), else the never-forced apply,
        and the reclaim after it when the cluster refused it and the route was taken."""
        within = self._settings.restore_deadline
        subdomain = inp.hosting.subdomain
        if not await self._route_taken(fence):
            result = await self._pointer.point(
                fence.project_id, target, fence.command_number, within, subdomain=subdomain
            )
            if result is not PointResult.REFUSED or not await self._route_taken(fence):
                return result
        result = await self._pointer.reclaim(
            fence.project_id, target, fence.command_number, within, subdomain=subdomain
        )
        confirmed = result is PointResult.CONFIRMED
        self._audit.record(
            AuditEvent(
                actor_kind=AuditActorKind.OPERATOR,
                actor=inp.command.requested_by_subject or OPERATOR_ACTOR,
                action=AuditAction.ROUTE_RECLAIM,
                project_id=fence.project_id,
                release_id=inp.command.release_id,
                command_id=fence.command_id,
                result=AuditResult.SUCCEEDED if confirmed else AuditResult.FAILED,
                failure_code=None if confirmed else CODE_ROUTE_RECLAIM_UNCONFIRMED,
                at=self._clock.now(),
                correlation_id=None,
            )
        )
        return result

    async def _route_taken(self, fence: Fence) -> bool:
        """Whether the observed site route was taken from the worker: written by another field
        manager, or stamped with a command number above this command's or not a number. Unknown
        (the cluster unreachable or slow) is False: the never-forced apply runs, and its refusal
        asks again."""
        try:
            async with asyncio.timeout(self._settings.restore_deadline.total_seconds()):
                inventory = await self._cluster.inventory(fence.project_id)
        except ClusterUnavailable, TenantNotReady, TimeoutError:
            return False
        name = site_route_name(fence.project_id)
        route = next(
            (
                o
                for o in inventory.objects
                if o.kind is SiteObjectKind.HTTP_ROUTE and o.name == name
            ),
            None,
        )
        if route is None:
            return False
        number = route.command_number
        return bool(route.foreign_managers) or (
            number is not None and (number < 0 or number > fence.command_number)
        )

    async def _uncertain(self, fence: Fence, code: str) -> None:
        """Record ``route_uncertain`` with ``code`` (the command failed). The alert is raised by
        the audited store (``audited.AuditedRouteCommandStore``), the single source of it for
        every path that leaves the route uncertain (lead's ruling, wave 6)."""
        unconfirmed = Failure(code)
        await self._store.apply_route_outcome(
            fence, lambda i: route_uncertain(i, unconfirmed, self._clock.now())
        )

    async def _clean(self, fence: Fence, out: Outcome) -> None:
        """Delete the workloads the committed outcome left behind, when a cleanup is wired."""
        if self._cleanup is not None:
            await self._cleanup.after_outcome(fence, out)

    async def _check_entitlement(self, fence: Fence, release_id: UUID, try_number: int) -> None:
        """Recheck the project's entitlement before the intent of try ``try_number``.
        ``LimitReached`` is ``PermanentFailure(entitlement_lost)``. Any other error (Payment
        cannot answer) is a failed try with ``transient_failure`` that touched nothing (design
        G10): ``RetryLater`` with the try's wait on try 1 or 2, ``PermanentFailure``
        (``attempts_exhausted``) on the last try. The error's text is never carried on."""
        try:
            await self._entitlement.check(fence.project_id, CommandKind.PROMOTE)
        except Exception as exc:
            if is_error(exc, LimitReached):
                raise PermanentFailure(CODE_ENTITLEMENT_LOST) from None
            wait = wait_after_failed_try(try_number)
            _log_failed_try(fence, release_id, try_number, FAILURE_CODE_TRANSIENT, wait)
            if wait is None:
                raise PermanentFailure(CODE_ATTEMPTS_EXHAUSTED) from None
            raise RetryLater(FAILURE_CODE_TRANSIENT, wait) from None

    async def _preview(self, fence: Fence, release_id: UUID, subdomain: str) -> str | None:
        """Point the preview route at the candidate and wait until the candidate answers on the
        preview hostname, both within one ``switch_deadline``. None when it answered;
        ``route_refused`` when the cluster refused the preview route; ``route_not_accepted`` when
        the preview route was not confirmed in time (FR-19.2); ``dependency_unavailable`` when
        the confirmed preview's last failed run was the candidate answering 503 (task H5a);
        ``candidate_not_answering`` when it never answered with the candidate's header otherwise.
        Each is a failed try's code (design G5)."""
        loop = asyncio.get_running_loop()
        until = loop.time() + self._settings.switch_deadline.total_seconds()
        result = await self._pointer.point_preview(
            fence.project_id,
            release_id,
            fence.command_number,
            self._settings.switch_deadline,
            subdomain=subdomain,
        )
        if result is PointResult.REFUSED:
            return CODE_ROUTE_REFUSED
        if result is PointResult.UNCONFIRMED:
            return CODE_ROUTE_NOT_ACCEPTED
        remaining = until - loop.time()
        hostname = preview_hostname(subdomain, self._settings.site_domain)
        candidate = RouteTarget(RouteTargetKind.RELEASE, release_id)
        if remaining <= 0:
            return CODE_CANDIDATE_NOT_ANSWERING
        probed = await self._pointer.probe(
            fence.project_id,
            hostname,
            candidate,
            timedelta(seconds=remaining),
            preview_of=subdomain,
        )
        if probed.answered:
            return None
        # Task H5a: the candidate's readiness reported a dependency down (503 naming itself).
        return CODE_DEPENDENCY_UNAVAILABLE if probed.outage else CODE_CANDIDATE_NOT_ANSWERING

    async def _switch(self, fence: Fence, release_id: UUID, subdomain: str) -> str | None:
        """Point the route at the candidate and wait for the site to answer with the candidate's
        release header, both within one ``switch_deadline``. None when confirmed, else the failure
        code."""
        loop = asyncio.get_running_loop()
        until = loop.time() + self._settings.switch_deadline.total_seconds()
        candidate = RouteTarget(RouteTargetKind.RELEASE, release_id)
        result = await self._pointer.point(
            fence.project_id,
            candidate,
            fence.command_number,
            self._settings.switch_deadline,
            subdomain=subdomain,
        )
        if result is PointResult.REFUSED:
            return CODE_ROUTE_REFUSED
        if result is PointResult.UNCONFIRMED:
            return CODE_ROUTE_NOT_ACCEPTED
        remaining = until - loop.time()
        hostname = site_hostname(subdomain, self._settings.site_domain)
        if remaining <= 0 or not await self._pointer.answers(
            fence.project_id, hostname, candidate, timedelta(seconds=remaining)
        ):
            return CODE_SITE_NOT_ANSWERING
        return None

    async def _restore(
        self, fence: Fence, target: RouteTarget, failure: Failure, subdomain: str
    ) -> None:
        """Point the route at the restore target and record what was verified: with a live
        release ``restore_verified``, on a first publish ``route_removed_verified`` (then the
        candidate's workload is deleted); unconfirmed ``route_uncertain``."""
        result = await self._pointer.point(
            fence.project_id,
            target,
            fence.command_number,
            self._settings.restore_deadline,
            subdomain=subdomain,
        )
        if result is not PointResult.CONFIRMED:
            await self._uncertain(fence, CODE_ROUTE_RESTORE_UNCONFIRMED)
            return
        verified = (
            restore_verified if target.kind is RouteTargetKind.RELEASE else route_removed_verified
        )
        out = await self._store.apply_route_outcome(
            fence, lambda i: verified(i, failure, self._clock.now())
        )
        await self._clean(fence, out)


def _log_failed_try(
    fence: Fence, release_id: UUID, try_number: int, failure_code: str, wait: timedelta | None
) -> None:
    """One info line per failed go-live try, with these named fields only: never an error's
    text, a token, a credential or site content (design G15). ``next_try_after_seconds`` is
    ``none`` when no try follows."""
    _LOGGER.info(
        "go-live try failed: command_id=%s project_id=%s release_id=%s try=%d of=%d "
        "failure_code=%s next_try_after_seconds=%s",
        fence.command_id,
        fence.project_id,
        release_id,
        try_number,
        GO_LIVE_TRIES,
        failure_code,
        "none" if wait is None else int(wait.total_seconds()),
    )


def _fence_of(claim: Claim, kind: CommandKind) -> Fence:
    if claim.ref.kind is not kind:
        raise InvalidCommandTarget(f"{claim.ref.kind} command {claim.ref.command_id} is no {kind}")
    return claim.fence()


def _release_id(inp: OutcomeInput) -> UUID:
    if inp.release is None or inp.command.release_id != inp.release.id:
        raise CorruptRow(f"command {inp.command.id} without its release")
    return inp.release.id
