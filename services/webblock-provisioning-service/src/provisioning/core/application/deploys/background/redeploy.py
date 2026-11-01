"""Redeploy: the rolling restart of the live release, run by the worker for a claimed ``redeploy``
command (decision 0023 G12; Notion "5 · Provisioning", redeploy).

An admin's redeploy restarts the live release's pods with the same image digest and the same code.
It is not a new release and not a publish: the route, ``routing_status``, every release and the
usage outbox stay as they are. The steps:

1. Load the command's rows under its fence (``RouteCommandStore.load_route_command``: the
   project's command number, the claim and the command running). When the live release is no
   longer the command's release, the command fails ``redeploy_target_changed`` and nothing is
   applied (the acceptance named the live release; this is the check that it still is).
2. Outside any transaction, apply the release's Deployment with the command's number as its fence
   and as the pod template annotation ``websites.webblock.internal/restart-command``
   (``SiteCluster.restart_release``: server-side apply, never forced). A new value starts a
   rolling restart; a repeated attempt of the same command applies the same value and changes
   nothing, so a retry never restarts twice.
3. Poll ``release_status`` at the command's number, with the readiness rules of a candidate:
   the new generation observed, every replica updated, ready and available, none unavailable, the
   old pods gone, and ready endpoints behind the Service.
4. Ready: the command succeeds (``redeploy_succeeded``). FAILED, MISSING, a refused apply, or the
   command's deadline (less ``finish_margin``) reached first: the command fails
   ``redeploy_unready``. The rolling update keeps the old pods serving until a new one is ready, so
   a failed redeploy leaves the site up.

Errors: ``TenantNotReady`` and ``ClusterUnavailable`` on the apply raise ``RetryLater`` and write
nothing (the next attempt applies the same annotation again); a status read that fails is
polled through until the deadline. The store's ``ClaimLost``, ``StaleFence`` and
``IllegalTransition`` come through unchanged. No SQL transaction is open across a cluster call.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from provisioning.core.application.deploys.background.deploy import site_image
from provisioning.core.application.deploys.ports import Pauser
from provisioning.core.application.jobs.claims import Claim, RetryLater
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.dependencies import Clock
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    ReleaseWorkload,
    SiteCluster,
    TenantNotReady,
    WorkloadState,
)
from provisioning.core.application.routing.ports import RouteCommandStore
from provisioning.core.application.shared.errors import CorruptRow
from provisioning.core.domain import (
    CODE_CLUSTER_UNAVAILABLE,
    CODE_TENANT_NOT_READY,
    CommandKind,
    Failure,
    InvalidCommandTarget,
    ReleaseStatus,
    fail_without_route_change,
    redeploy_succeeded,
)

CODE_REDEPLOY_UNREADY = "redeploy_unready"
CODE_REDEPLOY_TARGET_CHANGED = "redeploy_target_changed"


@dataclass(frozen=True, slots=True)
class RedeploySettings:
    """Every value is required (OD-15); the worker takes them from the deploy's configuration.

    - ``image_registry``: ``<registry>`` of ``<registry>/webblock/sites/<projectId>``, as the
      deploy applied the release;
    - ``poll_interval``: the pause between two status reads;
    - ``call_timeout``: bounds the apply and each status read;
    - ``finish_margin``: how long before the command's deadline the redeploy stops waiting, so
      that its outcome is written before the deadline.
    """

    image_registry: str
    poll_interval: timedelta
    call_timeout: timedelta
    finish_margin: timedelta

    def validate(self) -> None:
        """Refuse a missing or out-of-range value (``ValueError``; the message names the field,
        never its value)."""
        registry = self.image_registry
        if registry == "" or registry.endswith("/") or "://" in registry:
            raise ValueError(
                "redeploy: the image registry is required, without a scheme or a slash"
            )
        for name in ("poll_interval", "call_timeout", "finish_margin"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"redeploy: {name} must be positive")


class Redeploy:
    """The redeploy use case (module docstring)."""

    __slots__ = ("_clock", "_cluster", "_logger", "_pauser", "_settings", "_store")

    def __init__(
        self,
        *,
        store: RouteCommandStore,
        cluster: SiteCluster,
        clock: Clock,
        pauser: Pauser,
        settings: RedeploySettings,
        logger: logging.Logger | None = None,
    ) -> None:
        """Validates ``settings`` (``ValueError``). Calls no port."""
        settings.validate()
        self._store = store
        self._cluster = cluster
        self._clock = clock
        self._pauser = pauser
        self._settings = settings
        self._logger = logger or logging.getLogger(__name__)

    async def run(self, claim: Claim) -> None:
        """Run one claimed redeploy. Returns when its own fenced write ended the command; raises
        ``RetryLater`` for a transient failure of the apply, and the store's errors
        unchanged."""
        if claim.ref.kind is not CommandKind.REDEPLOY:
            raise InvalidCommandTarget(f"{claim.ref.kind} command {claim.ref.command_id}")
        fence = claim.fence()
        fence.validate()
        inp = await self._store.load_route_command(fence)
        release = inp.release
        if release is None:
            raise CorruptRow(f"redeploy command {fence.command_id} has no release")
        if release.status is not ReleaseStatus.LIVE or inp.hosting.live_release_id != release.id:
            self._logger.warning(
                "redeploy command %s of project %s: release %s is no longer live; nothing "
                "restarted",
                fence.command_id,
                fence.project_id,
                release.id,
            )
            await self._fail(fence, CODE_REDEPLOY_TARGET_CHANGED)
            return
        if release.container_image_digest is None:
            raise CorruptRow(f"live release {release.id} has no image digest")
        workload = ReleaseWorkload(
            project_id=fence.project_id,
            release_id=release.id,
            image=site_image(
                self._settings.image_registry, fence.project_id, release.container_image_digest
            ),
            command_number=fence.command_number,
        )
        own_deadline = claim.deadline_at - self._settings.finish_margin
        if self._clock.now() >= own_deadline or not await self._restart(fence, workload):
            await self._fail(fence, CODE_REDEPLOY_UNREADY)
            return
        if await self._ready(fence, workload, own_deadline):
            await self._store.apply_route_outcome(
                fence, lambda i: redeploy_succeeded(i, self._clock.now())
            )
            self._logger.info(
                "redeploy command %s of project %s: release %s restarted and ready",
                fence.command_id,
                fence.project_id,
                release.id,
            )
            return
        await self._fail(fence, CODE_REDEPLOY_UNREADY)

    async def _restart(self, fence: Fence, workload: ReleaseWorkload) -> bool:
        """Apply the restart. False when the cluster refused it (validation, admission, another
        field manager, a newer fence): the rollout never started."""
        try:
            async with asyncio.timeout(self._settings.call_timeout.total_seconds()):
                await self._cluster.restart_release(workload)
        except TenantNotReady:
            self._retry_later(fence, CODE_TENANT_NOT_READY)
        except ClusterUnavailable, TimeoutError:
            self._retry_later(fence, CODE_CLUSTER_UNAVAILABLE)
        except (ClusterRefused, ApplyConflict, FenceRejected) as exc:
            self._logger.warning(
                "redeploy command %s of project %s: the restart was refused (%s)",
                fence.command_id,
                fence.project_id,
                type(exc).__name__,
            )
            return False
        return True

    async def _ready(self, fence: Fence, workload: ReleaseWorkload, own_deadline: datetime) -> bool:
        """Poll the rollout until it is ready (True), or FAILED, MISSING, a newer fence, or the
        deadline (False). A failed read is polled through."""
        call = self._settings.call_timeout.total_seconds()
        while True:
            state: WorkloadState | None = None
            try:
                async with asyncio.timeout(call):
                    state = (await self._cluster.release_status(workload)).state
            except FenceRejected:
                return False
            except ClusterUnavailable, TenantNotReady, TimeoutError:
                state = None  # polled through; the deadline bounds it
            if state is WorkloadState.READY:
                return True
            if state in (WorkloadState.FAILED, WorkloadState.MISSING):
                self._logger.warning(
                    "redeploy command %s of project %s: the rollout is %s",
                    fence.command_id,
                    fence.project_id,
                    state,
                )
                return False
            left = own_deadline - self._clock.now()
            if left <= timedelta(0):
                return False
            await self._pauser.pause(min(self._settings.poll_interval, left))

    async def _fail(self, fence: Fence, code: str) -> None:
        """End the command failed with ``code``; the route and the releases are untouched."""
        failure = Failure(code)
        await self._store.apply_route_outcome(
            fence, lambda i: fail_without_route_change(i, failure, False, self._clock.now())
        )
        self._logger.info(
            "redeploy command %s of project %s failed: %s", fence.command_id, fence.project_id, code
        )

    def _retry_later(self, fence: Fence, code: str) -> NoReturn:
        self._logger.warning(
            "redeploy command %s of project %s retried later: %s",
            fence.command_id,
            fence.project_id,
            code,
        )
        raise RetryLater(code) from None
