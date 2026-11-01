"""Suspend and resume hosting: the intake of Payment's events and the commands the worker runs
(decision 0001 sections 3 and 4, decision 0014 sections 3, 9 and 10.5, decision 0021).

- ``AcceptHostingEventHandler`` (``hosting/commands/accept_hosting_event.py``) turns a Payment
  event into a ``suspend_hosting`` or ``resume_hosting`` command whose idempotency key is the event
  key, through ``HostingCommandStore`` (B3's
  ``HostingIntakeStore``, the same acceptance decision as the client commands, connected as
  ``provisioning_hosting_intake``, migration 00013). A
  repeated event returns the stored command; the same key with the other kind is
  ``IdempotencyConflict``; a project with a command in flight or a route that is not stable is
  refused (``OperationInProgress``, ``RouteNotStable``) and the event is retried later
  (``retry_hosting_event_later``).
- ``Hosting`` runs a claimed command by the reason rules below.

Offline reasons (decision 0021). Every hosting command carries one reason, fixed by its key
(``billing``, ``user_suspended`` or ``project_deleted``). A suspend adds only its own reason to the
hosting row's ``offline_reasons``, a resume removes only its own reason, and the site is online
only when no reason is left. A claimed command, while the route is stable, is decided in this
order:

1. A suspend whose reason is already held ends ``skipped`` (``hosting_already_offline``).
2. A resume whose reason is not held ends ``skipped`` (``hosting_already_online``), also when
   another reason keeps the site offline (D5: the row shows the true state and why).
3. A ``billing`` command is rechecked with Payment (below). ``user_suspended`` and
   ``project_deleted`` are never rechecked.
4. When the reasons left give the current hosting status, or the project was never routed (no
   live release, D6), the command only records its reason (``record_hosting_reason``) and the
   cluster is not called (D7).
5. Otherwise the route changes: the intent is recorded, the route points at ``OFFLINE`` (suspend)
   or the live release (resume), is confirmed, and ``hosting_verified`` is recorded; an
   unconfirmed route is ``route_uncertain``. A resumed claim (routing ``changing``) repeats this
   step.

The recheck (decision 0014 section 17 item 2, finding M2). The command's own Payment event is the
evidence that it was needed, so the command runs unless a later event is known to contradict it:
a suspend ends ``skipped`` (``entitlement_restored``) only when the latest Payment event recorded
after the suspend's own event (its idempotency key) restored the entitlement, and a resume ends
``skipped`` (``entitlement_ended``) only when that latest later event ended it. When nothing is
known (no later event, the command's own event not recorded, a restarted worker, another replica)
the command runs. Production asks Payment instead (OD-04).

Project deletion (decision 0014 section 21 answer 4, section 23 lead's rulings). Project's
"project deleted" event becomes a ``suspend_hosting`` command with the fixed key
``project-deleted`` (``DELETION_EVENT_KEY``), accepted by the same intake. Its reason is
``project_deleted``, which no resume ever removes, so the site stays offline. A later billing
resume of that project ends ``skipped``: ``hosting_already_online`` when ``billing`` is not held,
else ``entitlement_ended``, because the entitlement record answers "ended" for a project with a
deletion command (``standins.PrototypeHostingRecord``). No image is deleted (OD-48).

Retries (QA-03; decision 0014 section 23, lead's rulings; decision 0021). A suspend or resume that
ended ``failed`` is not forgotten: ``HostingRetry`` issues a new command for the same intent with
the key ``<event_key>.retry-<n>`` while ``n`` stays within ``HOSTING_RETRY_BUDGET``. The reasons are
independent, so the retry looks at the latest hosting command of each reason: one is retried when
it failed, its reason is not yet as it asked (a suspend's reason not held, a resume's reason still
held), and, for ``user_suspended``, its key's version is still the row's ``owner_status_version``
(a newer owner status replaces it). The acceptance checks the version again on the locked row
(``OwnerStatusOutdated``), so a newer owner status that lands after the read still stops the
retry. The oldest such command goes first; the retry key keeps the root key, so it keeps the
reason. An admin's command (``admin_suspended``, decision 0023 G11) is never retried here: the
event intake refuses its key, and the admin sends a new request. The retry is driven from the
hosting event path: a repeated event that finds its command failed, and a periodic sweep. The
retry reuses the Payment intent, so its recheck asks about the events after the original event
(``root_event_key``). The operator rollback restores the recorded state, so a site whose suspend
failed may serve again until the retry runs, and a suspended site (``offline``) never serves again
through a rollback.
"""

from __future__ import annotations

from typing import Final

from provisioning.core.application.hosting.ports import HostingEntitlement
from provisioning.core.application.jobs.claims import Claim
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.ports.site_cluster import (
    RouteTarget,
    RouteTargetKind,
    SiteCluster,
)
from provisioning.core.application.routing.background.routing import (
    PointResult,
    RoutePointer,
    RoutingSettings,
    restore_target_of,
)
from provisioning.core.application.routing.ports import RouteCommandStore
from provisioning.core.application.shared.errors import CorruptRow, DependencyUnavailable
from provisioning.core.domain import (
    DELETION_KEY,
    CommandKind,
    Failure,
    InvalidCommandTarget,
    OfflineReason,
    RoutingStatus,
    apply_hosting_reason,
    hosting_status_for,
    hosting_verified,
    record_hosting_reason,
    root_key,
    route_uncertain,
    skip_hosting_command,
    start_route_change,
)

CODE_ENTITLEMENT_RESTORED = "entitlement_restored"
CODE_ENTITLEMENT_ENDED = "entitlement_ended"
CODE_HOSTING_ALREADY_OFFLINE = "hosting_already_offline"
CODE_HOSTING_ALREADY_ONLINE = "hosting_already_online"
CODE_ROUTE_CHANGE_UNCONFIRMED = "route_change_unconfirmed"

DELETION_EVENT_KEY: Final = DELETION_KEY
"""The idempotency key of the suspend a "project deleted" event becomes (lead's ruling)."""


root_event_key = root_key
"""The event key a hosting command's key retries, or the key itself."""


class Hosting:
    """The suspend_hosting and resume_hosting use cases."""

    __slots__ = ("_clock", "_entitlement", "_pointer", "_settings", "_store")

    def __init__(
        self,
        *,
        store: RouteCommandStore,
        cluster: SiteCluster,
        entitlement: HostingEntitlement,
        clock: Clock,
        settings: RoutingSettings,
    ) -> None:
        """Validates ``settings`` (``ValueError``). Calls no port."""
        settings.validate()
        self._store = store
        self._entitlement = entitlement
        self._clock = clock
        self._settings = settings
        self._pointer = RoutePointer(cluster, settings)

    async def suspend(self, claim: Claim) -> None:
        """Run a claimed suspend_hosting command: it adds its own reason. Skipped
        (``hosting_already_offline``, ``entitlement_restored``) when no longer needed; on a site
        already offline or never routed it only records the reason; else the route goes to
        ``OFFLINE``, confirmed within ``switch_deadline``, and hosting becomes ``offline``."""
        await self._run(claim, CommandKind.SUSPEND_HOSTING)

    async def resume(self, claim: Claim) -> None:
        """Run a claimed resume_hosting command: it removes its own reason. Skipped
        (``hosting_already_online``, ``entitlement_ended``) when no longer needed; while another
        reason is left, or on a site never routed, it only records the change; else the route
        goes to the live release, confirmed within ``switch_deadline``, and hosting becomes
        ``online``."""
        await self._run(claim, CommandKind.RESUME_HOSTING)

    async def _run(self, claim: Claim, kind: CommandKind) -> None:
        if claim.ref.kind is not kind:
            raise InvalidCommandTarget(f"{claim.ref.kind} command {claim.ref.command_id}")
        fence = claim.fence()
        inp = await self._store.load_route_command(fence)
        suspend = kind is CommandKind.SUSPEND_HOSTING
        if inp.hosting.routing_status is RoutingStatus.STABLE:
            reason = inp.command.hosting_reason
            if reason is None:
                raise CorruptRow(f"{kind} command {fence.command_id} has no offline reason")
            held = inp.hosting.offline_reasons
            skip = await self._skip_code(fence, held, reason, inp.command.idempotency_key, suspend)
            if skip is not None:
                failure = Failure(skip)
                await self._store.apply_route_outcome(
                    fence, lambda i: skip_hosting_command(i, failure, self._clock.now())
                )
                return
            after = hosting_status_for(apply_hosting_reason(held, kind, reason))
            if after is inp.hosting.hosting_status or inp.hosting.live_release_id is None:
                await self._store.apply_route_outcome(
                    fence, lambda i: record_hosting_reason(i, self._clock.now())
                )
                return
            await self._store.apply_route_outcome(
                fence, lambda i: start_route_change(i, self._clock.now())
            )
        elif inp.hosting.routing_status is not RoutingStatus.CHANGING:
            raise CorruptRow(f"{kind} command {fence.command_id} while routing is uncertain")
        # A resumed claim (changing) repeats the route change, which is idempotent.
        target = (
            RouteTarget(RouteTargetKind.OFFLINE)
            if suspend
            else restore_target_of(inp.hosting.live_release_id)
        )
        result = await self._pointer.point(
            fence.project_id,
            target,
            fence.command_number,
            self._settings.switch_deadline,
            subdomain=inp.hosting.subdomain,
        )
        if result is PointResult.CONFIRMED:
            await self._store.apply_route_outcome(
                fence, lambda i: hosting_verified(i, self._clock.now())
            )
            return
        unconfirmed = Failure(CODE_ROUTE_CHANGE_UNCONFIRMED)
        await self._store.apply_route_outcome(
            fence, lambda i: route_uncertain(i, unconfirmed, self._clock.now())
        )

    async def _skip_code(
        self,
        fence: Fence,
        held: frozenset[OfflineReason],
        reason: OfflineReason,
        event_key: str,
        suspend: bool,
    ) -> str | None:
        """The code a command that is no longer needed ends ``skipped`` with, or None (steps 1 to
        3 of the module docstring). A suspend whose reason is held and a resume whose reason is
        not held ask Payment nothing. Only a ``billing`` command is rechecked (decision 0001
        section 4: a shutdown notice is rechecked before acting) against the events after the
        command's own (``event_key``); unknown runs the command (decision 0014 section 17 item
        2). A retry asks about the events after its original event."""
        if suspend and reason in held:
            return CODE_HOSTING_ALREADY_OFFLINE
        if not suspend and reason not in held:
            return CODE_HOSTING_ALREADY_ONLINE
        if reason is not OfflineReason.BILLING:
            return None
        try:
            later = await self._entitlement.entitlement_after(
                fence.project_id, root_event_key(event_key)
            )
        except Exception:
            raise DependencyUnavailable(
                f"hosting entitlement of project {fence.project_id}"
            ) from None
        if suspend and later is True:
            return CODE_ENTITLEMENT_RESTORED
        if not suspend and later is False:
            return CODE_ENTITLEMENT_ENDED
        return None
