"""The deploy use case against task A's Kind stack (decision 0014; brief of task F1, "Live").

Written by task F1 and not run until the lead says the stack is merged (``make proto-up``). It
uses the fixtures of ``conftest.py`` (task D): the worker's project-local kubeconfig only, a
poisoned ``HOME`` and ``KUBECONFIG``, and the two images of one project. The SQL side is an
in-memory store that applies the same domain functions as the PostgreSQL store; the fenced SQL
writes are proven by ``tests/integration/test_deploy_store.py``.

1. The sample site deploys to READY: the release becomes candidate_ready, the command succeeds.
2. With the sample site live behind the route, the never-ready variant fails its deploy: the
   command fails, its objects are deleted, and the live release, the route and the site through
   the Gateway are untouched.
"""

import dataclasses
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from provisioning.core.application.deploys.background.deploy import DeployCandidate, DeployParams
from provisioning.core.application.deploys.background.deploy_executor import AsyncioPauser
from provisioning.core.application.deploys.ports import (
    CandidateAttempt,
    CandidateAttemptStart,
    DeployFacts,
)
from provisioning.core.application.jobs.ports import Fence
from provisioning.core.application.ports.site_cluster import (
    ReleaseWorkload,
    RouteTarget,
    RouteTargetKind,
    TenantState,
    WorkloadState,
)
from provisioning.core.domain import (
    CandidateFailure,
    CandidatePlanKind,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    RoutingStatus,
    StaleFence,
    end_candidate_attempt,
    exhaust_candidate_attempts,
    new_command,
    plan_candidate_attempt,
    start_candidate_attempt,
)
from provisioning.infrastructure.kubernetes.bootstrap import KubernetesTenantBootstrap
from provisioning.infrastructure.kubernetes.site_cluster import KubernetesSiteCluster

if TYPE_CHECKING:
    from tests.integration.kubernetes.conftest import Stack

_IMAGE = re.compile(
    r"(?P<registry>.+)/webblock/sites/[0-9a-f-]{36}@(?P<digest>sha256:[0-9a-f]{64})"
)
SHA = "0123456789abcdef0123456789abcdef01234567"


def command_number() -> int:
    """Increasing across runs: milliseconds since the epoch (below 2^53)."""
    return time.time_ns() // 1_000_000


def split_image(image: str) -> tuple[str, str]:
    match = _IMAGE.fullmatch(image)
    assert match is not None, "an image of the stack is not <registry>/webblock/sites/<id>@sha256"
    return match.group("registry"), match.group("digest")


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _Ids:
    def new_id(self) -> UUID:
        return uuid4()


class _Store:
    """One project's rows in memory, under the same three checks and domain functions as the
    PostgreSQL store."""

    def __init__(self, project_id: UUID, release: Release, number: int) -> None:
        self.hosting = ProjectHosting(
            project_id=project_id,
            last_command_number=number,
            routing_status=RoutingStatus.STABLE,
            hosting_status=HostingStatus.ONLINE,
            updated_at=datetime.now(UTC),
            subdomain="shop",
        )
        command = new_command(
            command_id=uuid4(),
            request=CommandRequest(
                project_id=project_id,
                kind=CommandKind.DEPLOY,
                release_id=release.id,
                idempotency_key=f"live-{number}",
            ),
            requested_by_subject="live-test",
            command_number=number,
            now=datetime.now(UTC),
        )
        self.command = dataclasses.replace(command, status=CommandStatus.RUNNING)
        self.release = release
        self.steps: dict[UUID, ReleaseStep] = {}

    def fence(self) -> Fence:
        return Fence(
            project_id=self.hosting.project_id,
            command_id=self.command.id,
            command_number=self.command.command_number,
            claim_number=1,
        )

    def _check(self, fence: Fence) -> None:
        if fence.command_number != self.hosting.last_command_number:
            raise StaleFence("stale")

    def _input(self) -> OutcomeInput:
        return OutcomeInput(hosting=self.hosting, command=self.command, release=self.release)

    def _last(self) -> ReleaseStep | None:
        return max(self.steps.values(), key=lambda s: s.attempt, default=None)

    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        self._check(fence)
        return DeployFacts(self.hosting, self.command, self.release, self._last())

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        self._check(fence)

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        self._check(fence)
        now = datetime.now(UTC)
        plan = plan_candidate_attempt(self.release, self._last(), start.step_id, now)
        if plan.ended is not None:
            self.steps[plan.ended.id] = plan.ended
        if plan.kind is CandidatePlanKind.EXHAUSTED:
            out = exhaust_candidate_attempts(self._input(), now)
            self.command, self.release = out.command, out.release or self.release
            return CandidateAttempt(plan.kind)
        assert plan.step is not None
        step = plan.step
        if plan.kind is CandidatePlanKind.START:
            step = start_candidate_attempt(self.release, step, now + start.timeout, now)
            self.steps[step.id] = step
        return CandidateAttempt(plan.kind, step)

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        self._check(fence)
        return ()

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        self._check(fence)
        end = end_candidate_attempt(
            self._input(), self.steps[step_id], failure_code, datetime.now(UTC)
        )
        self.steps[step_id] = end.step
        if end.outcome is not None:
            self.command = end.outcome.command
            self.release = end.outcome.release or self.release
        return end.failure


def built_release(project_id: UUID, digest: str) -> Release:
    now = datetime.now(UTC) - timedelta(minutes=1)
    return Release(
        id=uuid4(),
        project_id=project_id,
        generation_request_id=uuid4(),
        repository_id=4242,
        git_commit_sha=SHA,
        status=ReleaseStatus.BUILT,
        created_at=now,
        updated_at=now,
        container_image_digest=digest,
    )


def deploy_of(cluster: KubernetesSiteCluster, store: _Store, registry: str) -> DeployCandidate:
    return DeployCandidate(
        store=store,
        cluster=cluster,
        clock=_Clock(),
        ids=_Ids(),
        pauser=AsyncioPauser(),
        params=DeployParams(
            image_registry=registry,
            ready_timeout=timedelta(seconds=150),
            poll_interval=timedelta(seconds=2),
            call_timeout=timedelta(seconds=10),
            delete_timeout=timedelta(seconds=30),
            finish_margin=timedelta(seconds=30),
            claim_ttl=timedelta(minutes=1),
        ),
        logger=logging.getLogger("test.deploy.live"),
    )


async def deployed(
    cluster: KubernetesSiteCluster, stack: Stack, image: str
) -> tuple[_Store, ReleaseWorkload]:
    registry, digest = split_image(image)
    store = _Store(stack.project_id, built_release(stack.project_id, digest), command_number())
    await deploy_of(cluster, store, registry).run(
        store.fence(), datetime.now(UTC) + timedelta(minutes=5)
    )
    workload = ReleaseWorkload(
        stack.project_id, store.release.id, image, store.command.command_number
    )
    return store, workload


@pytest.mark.asyncio
async def test_the_sample_site_deploys_to_ready(
    cluster: KubernetesSiteCluster, bootstrap: KubernetesTenantBootstrap, stack: Stack
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    store, workload = await deployed(cluster, stack, stack.site_image)
    try:
        assert (store.command.status, store.command.failure_code) == (CommandStatus.SUCCEEDED, None)
        assert store.release.status is ReleaseStatus.CANDIDATE_READY
        assert (await cluster.release_status(workload)).state is WorkloadState.READY
    finally:
        await cluster.delete_release(stack.project_id, workload.release_id, workload.command_number)


@pytest.mark.asyncio
async def test_a_never_ready_candidate_fails_and_the_live_site_is_untouched(
    cluster: KubernetesSiteCluster, bootstrap: KubernetesTenantBootstrap, stack: Stack
) -> None:
    assert (
        await bootstrap.ensure_tenant(stack.project_id, subdomain=stack.subdomain)
        is TenantState.READY
    )
    host = f"{stack.subdomain}.sites.webblock.test"
    live_store, live = await deployed(cluster, stack, stack.site_image)
    assert live_store.release.status is ReleaseStatus.CANDIDATE_READY
    route_number = command_number()
    await cluster.apply_route(
        stack.project_id,
        RouteTarget(RouteTargetKind.RELEASE, live.release_id),
        (host,),
        route_number,
        subdomain=stack.subdomain,
    )
    try:
        bad_store, bad = await deployed(cluster, stack, stack.never_ready_image)
        assert bad_store.command.status is CommandStatus.FAILED
        assert bad_store.command.failure_code in (
            "crash_loop",
            "candidate_not_ready",
            "progress_deadline_exceeded",
        )
        assert bad_store.release.status is ReleaseStatus.BUILT
        assert (await cluster.release_status(bad)).state is WorkloadState.MISSING
        # The live release, the route and the site through the Gateway are untouched.
        assert (await cluster.release_status(live)).state is WorkloadState.READY
        route = await cluster.route_status(stack.project_id)
        assert route.target == RouteTarget(RouteTargetKind.RELEASE, live.release_id)
        assert route.command_number == route_number
        assert await cluster.probe_site(
            stack.project_id, host, RouteTarget(RouteTargetKind.RELEASE, live.release_id)
        )
    finally:
        last = command_number()
        await cluster.apply_route(
            stack.project_id,
            RouteTarget(RouteTargetKind.OFFLINE),
            (host,),
            last,
            subdomain=stack.subdomain,
        )
        await cluster.delete_release(stack.project_id, live.release_id, last)
