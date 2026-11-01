"""The bootstrap loop (task G; decision 0014 sections 3 and 10.1): every interval it reads the
projects that need a tenant (a release on its way to a deploy, or a live site) and ensures each
one's tenant (namespace, quota, limits, ReferenceGrant, the worker's RoleBinding last) with the
bootstrap identity.

The database records no tenant state, so the loop remembers the projects whose tenant it found
ready and does not ask the cluster about them in the next rounds. Every ``recheck_rounds`` rounds
(``BOOTSTRAP_RECHECK_ROUNDS``) it forgets them all and ensures each one once more, so a tenant
namespace deleted by hand is rebuilt, with its access setup, within ``recheck_rounds`` x
``interval`` and without a restart (QA-04). Ensuring a tenant that is intact changes nothing:
every step is an idempotent server-side apply.
Each round reads pages of ``batch_size`` projects, ensures at most ``concurrency`` tenants at once,
and bounds each ensure by ``call_timeout``. A failure is logged with the project and the error's
class and retried in the next round.
A project whose sub-domain another project's hosting row also holds gets no tenant (FR-54, TR-53):
its namespace is never labelled with that sub-domain, so the route-shape admission policy admits no
route for it. It is logged at error with its ID only, every round, until an operator fixes the row.
A sub-domain ending in ``-preview`` (``shop-preview`` is ``shop``'s preview host) or of 56 to 63
characters (its preview host would not be one DNS label) is refused the same way (FR-55);
``preview`` alone is allowed. The rows are kept and the database keeps its own check.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol
from uuid import UUID

from provisioning.core.application.ports.site_cluster import (
    TenantBootstrap,
    TenantProject,
    TenantState,
)
from provisioning.core.domain import subdomain_refusal

# The ready projects remembered at most (the oldest forgotten first; it is then ensured again).
MAX_REMEMBERED: Final = 100_000

# BOOTSTRAP_RECHECK_ROUNDS' default (``infrastructure.config.bootstrap``).
DEFAULT_RECHECK_ROUNDS: Final = 10


class TenantProjects(Protocol):
    """Implemented by ``infrastructure.postgres.tenant_projects.PgTenantProjects``."""

    async def projects_needing_tenant(
        self, *, after: UUID | None, limit: int
    ) -> list[TenantProject]: ...


type RoundObserver = Callable[[int | None], None]
"""Sees each round's end (task H4's metrics): the number of tenants it left not ready, or None
when the round failed. It must not raise; what it raises is dropped."""

type TenantReadyHook = Callable[[UUID], Awaitable[None]]
"""Run after a tenant is ready, before it is remembered (H's ``access_setup`` once merged). Its
failure is logged and the tenant is ensured again in the next round."""


@dataclass(frozen=True, slots=True)
class LoopSettings:
    interval: timedelta
    batch_size: int
    concurrency: int
    call_timeout: timedelta
    recheck_rounds: int = DEFAULT_RECHECK_ROUNDS

    def validate(self) -> None:
        if self.interval <= timedelta(0) or self.call_timeout <= timedelta(0):
            raise ValueError("bootstrap: the interval and the call timeout must be positive")
        if self.recheck_rounds < 1:
            raise ValueError("bootstrap: the recheck must be every one round or more")
        if not 1 <= self.concurrency <= self.batch_size:
            raise ValueError("bootstrap: the concurrency must be 1 to the batch size")


@dataclass(frozen=True, slots=True)
class RoundResult:
    ready: tuple[UUID, ...]
    pending: tuple[UUID, ...]  # creating, terminating, failed or timed out


class TenantLoop:
    __slots__ = (
        "_after_ready",
        "_bootstrap",
        "_logger",
        "_on_round",
        "_projects",
        "_ready",
        "_rounds",
        "_settings",
    )

    def __init__(
        self,
        settings: LoopSettings,
        *,
        projects: TenantProjects,
        bootstrap: TenantBootstrap,
        logger: logging.Logger,
        after_ready: TenantReadyHook | None = None,
        on_round: RoundObserver | None = None,
    ) -> None:
        settings.validate()
        self._settings = settings
        self._projects = projects
        self._bootstrap = bootstrap
        self._logger = logger
        self._after_ready = after_ready
        self._on_round = on_round
        self._ready: OrderedDict[UUID, None] = OrderedDict()
        self._rounds = 0

    def is_known_ready(self, project_id: UUID) -> bool:
        return project_id in self._ready

    async def round(self) -> RoundResult:
        """One pass over every project that needs a tenant and is not known to have one. Every
        ``recheck_rounds``-th round after the first forgets the known ones first, so it ensures
        every project again."""
        if self._rounds > 0 and self._rounds % self._settings.recheck_rounds == 0:
            if self._ready:
                self._logger.info("rechecking %d ready tenants", len(self._ready))
            self._ready.clear()
        self._rounds += 1
        ready: list[UUID] = []
        pending: list[UUID] = []
        slots = asyncio.Semaphore(self._settings.concurrency)
        after: UUID | None = None
        while True:
            page = await self._projects.projects_needing_tenant(
                after=after, limit=self._settings.batch_size
            )
            todo = [p for p in page if p.project_id not in self._ready]

            async def one(tenant: TenantProject) -> None:
                async with slots:
                    if await self._ensure(tenant):
                        ready.append(tenant.project_id)
                    else:
                        pending.append(tenant.project_id)

            async with asyncio.TaskGroup() as group:
                for project in todo:
                    group.create_task(one(project))
            if len(page) < self._settings.batch_size:
                break
            after = page[-1].project_id
        return RoundResult(ready=tuple(ready), pending=tuple(pending))

    async def run(self) -> None:
        """Rounds every ``interval`` until cancelled; a failed round is logged."""
        while True:
            try:
                result = await self.round()
            except Exception as exc:  # the next round runs on schedule
                self._logger.warning("bootstrap round failed (error=%s)", type(exc).__name__)
                self._observe(None)
            else:
                self._observe(len(result.pending))
            await asyncio.sleep(self._settings.interval.total_seconds())

    def _observe(self, not_ready: int | None) -> None:
        if self._on_round is not None:
            with contextlib.suppress(Exception):  # an observer never changes the loop
                self._on_round(not_ready)

    async def _ensure(self, project: TenantProject) -> bool:
        project_id = project.project_id
        if project.subdomain_taken:
            self._logger.error(
                "tenant refused: another project's hosting row holds its sub-domain"
                " (project_id=%s reason=subdomain_taken)",
                project_id,
            )
            return False
        refusal = subdomain_refusal(project.subdomain)
        if refusal is not None:
            self._logger.error(
                "tenant refused: its sub-domain cannot be hosted beside a preview host"
                " (project_id=%s reason=%s)",
                project_id,
                refusal,
            )
            return False
        try:
            async with asyncio.timeout(self._settings.call_timeout.total_seconds()):
                state = await self._bootstrap.ensure_tenant(project_id, subdomain=project.subdomain)
                if state is TenantState.READY and self._after_ready is not None:
                    await self._after_ready(project_id)
        except Exception as exc:  # retried next round
            self._logger.warning(
                "tenant not ensured; retried next round (project_id=%s error=%s)",
                project_id,
                type(exc).__name__,
            )
            return False
        if state is not TenantState.READY:
            self._logger.info("tenant not ready yet (project_id=%s state=%s)", project_id, state)
            return False
        self._ready[project_id] = None
        while len(self._ready) > MAX_REMEMBERED:
            self._ready.popitem(last=False)
        self._logger.info("tenant ready (project_id=%s)", project_id)
        return True
