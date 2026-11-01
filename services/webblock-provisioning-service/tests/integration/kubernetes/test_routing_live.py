"""Promote, automatic restore, suspend and resume against task A's Kind stack (task F2; decision
0014 sections 3 and 4).

Written by task F2 and **not run by it**: it runs once the lead says task A's stack is merged
(``make proto-up``). It uses task D's fixtures (``conftest.py`` here: the project-local worker and
bootstrap kubeconfigs, the poisoned ``HOME`` and ``KUBECONFIG``, ``WEBBLOCK_KIND_SITE_IMAGE``,
``WEBBLOCK_KIND_NEVER_READY_IMAGE`` and ``WEBBLOCK_KIND_GATEWAY_URL``, each required). The use cases
are the real ``Routing`` and ``Hosting`` over the real ``KubernetesSiteCluster``; the SQL rows are
the in-memory world of the unit tests, which applies the real domain outcome functions, so this
suite checks the cluster behaviour, not the store (``routing_store.py`` has its own PostgreSQL
tests). Command numbers come from the clock, so a rerun is never fenced by an earlier run; the
tenant and its route (left pointing at the live release) stay for the next run.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx2
import pytest
from tests.unit.core.application.routing.background.test_routing_support import (
    FakeEntitlement,
    FakeHostingEntitlement,
    World,
    make_release,
)

from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.jobs.claims import RetryLater
from provisioning.core.application.ports.site_cluster import (
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    TenantState,
    WorkloadState,
)
from provisioning.core.application.routing.background.routing import (
    Routing,
    RoutingSettings,
)
from provisioning.core.domain import (
    CODE_CANDIDATE_NOT_ANSWERING,
    CommandKind,
    CommandStatus,
    HostingStatus,
    ReleaseStatus,
    RoutingStatus,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

# Readiness (180 s), two promotes of up to 3 tries, each try's preview and switch within one
# switch_deadline (60 s) and its restore within restore_deadline (60 s), then suspend and resume:
# each step is bounded, the sum stays below this (go-live plan Task 10, FR-21).
pytestmark = pytest.mark.timeout(900)

SITE_DOMAIN = "sites.webblock.test"
READY_WITHIN = 180.0
POLL = 2.0
SETTINGS = RoutingSettings(
    site_domain=SITE_DOMAIN,
    switch_deadline=timedelta(seconds=60),
    restore_deadline=timedelta(seconds=60),
    poll_interval=timedelta(seconds=1),
)


async def until[T](read: Callable[[], Awaitable[T]], done: Callable[[T], bool], within: float) -> T:
    value = await read()
    try:
        async with asyncio.timeout(within):
            while not done(value):
                await asyncio.sleep(POLL)
                value = await read()
    except TimeoutError:
        pass
    return value


class Site:
    """One project's world, starting from the clock's command number, with real use cases."""

    def __init__(self, cluster: KubernetesSiteCluster, stack: Stack) -> None:
        self.log: list[str] = []
        self.stack = stack
        self.cluster = cluster
        self.world = World(self.log, live=False, candidate=False, project_id=stack.project_id)
        # The hosting row's sub-domain is the one the namespace carries (decision 0023 G18).
        self.world.hosting = replace(
            self.world.hosting,
            last_command_number=time.time_ns() // 1_000_000,
            subdomain=stack.subdomain,
        )
        self.entitled = FakeHostingEntitlement(self.log)
        self.routing = Routing(
            store=self.world,
            cluster=cluster,
            entitlement=FakeEntitlement(self.log),
            clock=self.world.clock,
            settings=SETTINGS,
        )
        self.hosting = Hosting(
            store=self.world,
            cluster=cluster,
            entitlement=self.entitled,
            clock=self.world.clock,
            settings=SETTINGS,
        )
        self.minutes = 0

    async def candidate(self, image: str, *, wait_ready: bool) -> UUID:
        """Apply a release's Deployment and Service (as a deploy does) and record it
        candidate_ready, ready or not: the never-ready variant is forced behind the route."""
        release_id = uuid4()
        workload = ReleaseWorkload(
            self.stack.project_id, release_id, image, self.world.hosting.last_command_number
        )
        await self.cluster.apply_release(workload)
        if wait_ready:
            status = await until(
                lambda: self.cluster.release_status(workload),
                lambda s: s.state is not WorkloadState.PROGRESSING,
                READY_WITHIN,
            )
            assert status.state is WorkloadState.READY, status
        self.minutes += 1
        self.world.releases[release_id] = make_release(
            release_id, ReleaseStatus.CANDIDATE_READY, self.minutes, self.stack.project_id
        )
        return release_id

    async def promote(self, release_id: UUID) -> CommandStatus:
        command = self.world.accept(CommandKind.PROMOTE, release_id)
        await self.routing.promote(self.world.claim(command))
        return self.world.command(command.id).status

    async def hosting_command(self, kind: CommandKind) -> CommandStatus:
        command = self.world.accept(kind, key=f"payment-{uuid4()}")
        claim = self.world.claim(command)
        if kind is CommandKind.SUSPEND_HOSTING:
            await self.hosting.suspend(claim)
        else:
            await self.hosting.resume(claim)
        return self.world.command(command.id).status

    async def answers(self) -> bool:
        async with httpx2.AsyncClient(trust_env=False, timeout=5.0) as http:
            response = await http.get(
                self.stack.gateway_url,
                headers={"Host": f"{self.stack.subdomain}.{SITE_DOMAIN}"},
                follow_redirects=False,
            )
            return response.status_code < 500


async def ensure_tenant(bootstrap: KubernetesTenantBootstrap, stack: Stack) -> None:
    state = await until(
        lambda: bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain),
        lambda s: s is TenantState.READY,
        60.0,
    )
    assert state is TenantState.READY


@pytest.mark.asyncio
async def test_promote_restore_suspend_and_resume_on_kind(
    cluster: KubernetesSiteCluster, bootstrap: KubernetesTenantBootstrap, stack: Stack
) -> None:
    """FR-19.1, FR-21.1, FR-21.6, FR-33, FR-34: a promote goes live; a never-ready candidate fails
    3 tries and is rolled back while the live release keeps serving; suspend and resume."""
    await ensure_tenant(bootstrap, stack)
    site = Site(cluster, stack)

    # 1. Promote the sample site: it answers through the Gateway.
    first = await site.candidate(stack.site_image, wait_ready=True)
    assert await site.promote(first) is CommandStatus.SUCCEEDED
    assert site.world.release(first).status is ReleaseStatus.LIVE
    assert site.world.routing() is RoutingStatus.STABLE
    assert await site.answers()

    # 2. Promote the never-ready variant (FR-21.1, FR-21.6): each claim of the one command is a
    # try, and its preview never answers. Tries 1 and 2 give the claim back (RetryLater with
    # candidate_not_answering and the 30 s, then 60 s wait, which the consumer and the store hold;
    # this suite claims at once): the site's route stays untouched on the first release, the
    # release promoting, routing changing. Try 3 restores the route to the first release at this
    # command's number, checked: the release ends rolled_back with try 3's code.
    before = await cluster.route_status(stack.project_id)
    broken = await site.candidate(stack.never_ready_image, wait_ready=False)
    command = site.world.accept(CommandKind.PROMOTE, broken)
    for try_number, wait in ((1, 30), (2, 60)):
        claim = site.world.claim(command)
        assert claim.execution_attempt == try_number
        with pytest.raises(RetryLater) as later:
            await site.routing.promote(claim)
        assert (later.value.code, later.value.after) == (
            CODE_CANDIDATE_NOT_ANSWERING,
            timedelta(seconds=wait),
        )
        assert site.world.release(broken).status is ReleaseStatus.PROMOTING
        assert site.world.routing() is RoutingStatus.CHANGING
        assert site.world.command(command.id).status is CommandStatus.RUNNING
        assert await cluster.route_status(stack.project_id) == before
        preview = await cluster.preview_status(stack.project_id)
        assert preview.target == RouteTarget(RouteTargetKind.RELEASE, broken)
        assert preview.command_number == command.command_number
        assert await site.answers()
    claim = site.world.claim(command)
    assert claim.execution_attempt == 3
    await site.routing.promote(claim)
    rolled_back = site.world.release(broken)
    assert (rolled_back.status, rolled_back.failure_code) == (
        ReleaseStatus.ROLLED_BACK,
        CODE_CANDIDATE_NOT_ANSWERING,
    )
    assert site.world.command(command.id).status is CommandStatus.FAILED
    assert site.world.routing() is RoutingStatus.STABLE
    route = await cluster.route_status(stack.project_id)
    assert route.accepted
    assert route.target == RouteTarget(RouteTargetKind.RELEASE, first)
    assert route.command_number == command.command_number
    assert site.world.hosting.live_release_id == first
    assert site.world.release(first).status is ReleaseStatus.LIVE
    assert await site.answers()

    # 3. Suspend: the route shows the offline page.
    assert await site.hosting_command(CommandKind.SUSPEND_HOSTING) is CommandStatus.SUCCEEDED
    assert site.world.hosting_status() is HostingStatus.OFFLINE
    route = await cluster.route_status(stack.project_id)
    assert route.target == RouteTarget(RouteTargetKind.OFFLINE)
    assert route.accepted

    # 4. Resume: the site is back.
    assert await site.hosting_command(CommandKind.RESUME_HOSTING) is CommandStatus.SUCCEEDED
    assert site.world.hosting_status() is HostingStatus.ONLINE
    route = await cluster.route_status(stack.project_id)
    assert route.target == RouteTarget(RouteTargetKind.RELEASE, first)
    assert await until(site.answers, bool, 60.0)
