"""The prototype's flows end to end on the sample site (task G, part 3; decision 0014 sections 2 and
4), in order on one project: publish, update, a failed candidate, a failed promotion whose
candidate never receives the site's traffic (section 17), a promotion that fails after the switch
and is restored (task T2), and suspend and resume. Each step
checks the database-backed state through REST and the site through the Gateway. A failing promote
makes 3 tries with waits of 30 s and 60 s between them before it ends.

Needs ``make proto-up proto-run``. The tests share one project and run in file order.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from tests.e2e._stack import (
    FAILED_PROMOTE_TEST_TIMEOUT,
    FAILED_PROMOTE_TIMEOUT,
    Stack,
    Timer,
    new_project,
    new_repository_id,
    until,
)

from provisioning.core.domain import GO_LIVE_TRIES

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

BUILD_TIMEOUT = 300.0
DEPLOY_TIMEOUT = 300.0
PROMOTE_TIMEOUT = 180.0
# Each try of test 4b switches the route to the candidate and back (FR-21.1).
ROUTE_MOVES = 2 * GO_LIVE_TRIES


class Site:
    """The one project the flows share."""

    def __init__(self) -> None:
        self.project = new_project()
        self.repository_id = new_repository_id()
        self.name = f"e2e-{str(self.project)[-12:]}"
        self.releases: dict[str, uuid.UUID] = {}


@pytest.fixture(scope="module")
def site() -> Site:
    return Site()


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


async def _release_from_commit(
    stack: Stack, site: Site, variant: str, version: str, timer: Timer
) -> uuid.UUID:
    sha = await stack.commit(site.name, variant, version)
    timer.mark("event_published")
    request = await stack.publish(site.project, site.repository_id, sha)
    release = await stack.release_of(request)
    timer.mark("release_accepted")
    body = await stack.wait_release(site.project, release, {"built", "failed"}, BUILD_TIMEOUT)
    timer.mark("built")
    assert body["status"] == "built", body
    assert body["gitCommitSha"] == sha
    assert body["containerImageDigest"].startswith("sha256:")
    return release


async def _deploy(stack: Stack, site: Site, release: uuid.UUID, timer: Timer) -> dict[str, Any]:
    timer.mark("deploy_posted")
    accepted = await stack.post(site.project, release, "deploy", f"deploy-{release}")
    assert accepted.status_code == 202, accepted.body
    done = await stack.wait_command(site.project, accepted.command_id, DEPLOY_TIMEOUT)
    timer.mark("deploy_finished")
    return done


async def _promote(
    stack: Stack, site: Site, release: uuid.UUID, timer: Timer, within: float = PROMOTE_TIMEOUT
) -> dict[str, Any]:
    timer.mark("promote_posted")
    accepted = await stack.post(site.project, release, "promote", f"promote-{release}")
    assert accepted.status_code == 202, accepted.body
    done = await stack.wait_command(site.project, accepted.command_id, within)
    timer.mark("promote_finished")
    return done


async def _serves(stack: Stack, site: Site, text: str) -> None:
    await until(
        f"the Gateway serving {text!r}",
        lambda: stack.visit(site.project),
        lambda r: r[0] == 200 and text in r[1],
        60,
    )


async def _named(stack: Stack, site: Site, release: uuid.UUID) -> None:
    """The Gateway names the release that answered (decision 0014 section 17)."""
    assert await stack.visit_named(site.project) == (200, [str(release)])


async def test_1_publish_builds_deploys_and_promotes_v1(stack: Stack, site: Site) -> None:
    stack.register(site.project, site.repository_id, site.name)
    timer = Timer("flow1-publish")
    release = await _release_from_commit(stack, site, "site", "v1", timer)
    deployed = await _deploy(stack, site, release, timer)
    assert deployed["status"] == "succeeded", deployed
    assert (await stack.release(site.project, release))["status"] == "candidate_ready"
    promoted = await _promote(stack, site, release, timer)
    assert promoted["status"] == "succeeded", promoted
    assert (await stack.release(site.project, release))["status"] == "live"
    deployment = await stack.deployment(site.project)
    assert deployment["liveReleaseId"] == str(release)
    assert (deployment["routingStatus"], deployment["hostingStatus"]) == ("stable", "online")
    await _serves(stack, site, "sample-site version: v1")
    timer.mark("serving")
    await _named(stack, site, release)
    timer.save(
        {
            "project_id": str(site.project),
            "release_id": str(release),
            "promote_seconds": timer.between("promote_posted", "promote_finished"),
        }
    )
    site.releases["v1"] = release


async def test_2_update_to_v2_supersedes_v1(stack: Stack, site: Site) -> None:
    timer = Timer("flow2-update")
    release = await _release_from_commit(stack, site, "site", "v2", timer)
    assert (await _deploy(stack, site, release, timer))["status"] == "succeeded"
    # The candidate runs beside the live site, which keeps serving v1.
    await _serves(stack, site, "sample-site version: v1")
    assert (await _promote(stack, site, release, timer))["status"] == "succeeded"
    await _serves(stack, site, "sample-site version: v2")
    timer.mark("serving")
    await _named(stack, site, release)
    assert (await stack.release(site.project, release))["status"] == "live"
    assert (await stack.release(site.project, site.releases["v1"]))["status"] == "superseded"
    timer.save(
        {
            "project_id": str(site.project),
            "release_id": str(release),
            "promote_seconds": timer.between("promote_posted", "promote_finished"),
        }
    )
    site.releases["v2"] = release


async def test_3_a_candidate_that_never_becomes_ready_fails_and_v2_keeps_serving(
    stack: Stack, site: Site
) -> None:
    timer = Timer("flow3-never-ready")
    release = await _release_from_commit(stack, site, "never-ready", "v3", timer)
    deployed = await _deploy(stack, site, release, timer)
    assert deployed["status"] == "failed", deployed
    assert deployed["failureCode"] in {
        "crash_loop",
        "progress_deadline_exceeded",
        "deadline_exceeded",
        "candidate_not_ready",
    }, deployed
    assert (await stack.release(site.project, release))["status"] in {"built", "failed"}
    deployment = await stack.deployment(site.project)
    assert deployment["liveReleaseId"] == str(site.releases["v2"])
    assert deployment["routingStatus"] == "stable"
    await _serves(stack, site, "sample-site version: v2")
    timer.save({"failure_code": deployed["failureCode"]})


@pytest.mark.timeout(FAILED_PROMOTE_TEST_TIMEOUT)
async def test_4_a_candidate_failing_its_preview_never_receives_the_sites_traffic(
    stack: Stack, site: Site
) -> None:
    """FR-21.6, FR-21.1: the "unready" variant is ready for Kubernetes (TCP and /healthz) but
    answers 503 on /readyz, the promotion probe (decision 0014 sections 15 and 17). Its preview
    probe fails on each of the 3 tries with dependency_unavailable (task H5a), the site's route is
    never pointed at it, and after try 3 the release ends rolled_back with that code: every visit
    during the command is answered by v2, as the Gateway's release header shows."""
    timer = Timer("flow4-failed-promotion")
    release = await _release_from_commit(stack, site, "unready", "v4", timer)
    assert (await _deploy(stack, site, release, timer))["status"] == "succeeded"
    v2 = str(site.releases["v2"])
    seen: list[tuple[int, list[str]]] = []
    stop = asyncio.Event()

    async def watch() -> None:
        while not stop.is_set():
            seen.append(await stack.visit_named(site.project))
            await asyncio.sleep(0.05)

    async with asyncio.TaskGroup() as group:
        watcher = group.create_task(watch())
        try:
            promoted = await _promote(stack, site, release, timer, FAILED_PROMOTE_TIMEOUT)
        finally:
            stop.set()
        await watcher
    assert promoted["status"] == "failed", promoted
    assert promoted["failureCode"] == "dependency_unavailable", promoted
    body = await stack.release(site.project, release)
    assert (body["status"], body.get("failureCode")) == ("rolled_back", "dependency_unavailable")
    # The site's route was never pointed at the candidate: no visit was answered by it.
    assert len(seen) > 10, len(seen)
    assert not any(str(release) in names for _, names in seen), seen
    assert all(names == [v2] for status, names in seen if status == 200), seen
    deployment = await stack.deployment(site.project)
    assert deployment["liveReleaseId"] == v2
    assert deployment["routingStatus"] == "stable"
    await _serves(stack, site, "sample-site version: v2")
    await _named(stack, site, site.releases["v2"])
    timer.save(
        {
            "failed_promote_seconds": timer.between("promote_posted", "promote_finished"),
            "visits": len(seen),
        }
    )


@pytest.mark.timeout(FAILED_PROMOTE_TEST_TIMEOUT)
async def test_4b_a_candidate_failing_after_the_switch_is_restored_and_rolled_back(
    stack: Stack, site: Site
) -> None:
    """FR-19.3, FR-21.1 (task T2): the "preview-only" variant (a test fixture; a real site must
    never behave differently on its preview hostname) answers /readyz 200 only on its preview
    hostname <subdomain>-preview.<site domain>. Each of the 3 tries passes the preview, switches
    the site's route, finds the primary hostname not answering, and puts the route back on v2:
    after try 3 the command fails with site_not_answering, the candidate ends rolled_back, v2
    stays the live release, and every visit during the command is answered, by v2 or, inside a
    switch window, by the candidate (the Gateway's few 503s while the route moves are counted in
    the timings)."""
    timer = Timer("flow4b-restored-after-switch")
    release = await _release_from_commit(stack, site, "preview-only", "v4b", timer)
    assert (await _deploy(stack, site, release, timer))["status"] == "succeeded"
    v2 = str(site.releases["v2"])
    seen: list[tuple[int, list[str]]] = []
    stop = asyncio.Event()

    async def watch() -> None:
        while not stop.is_set():
            seen.append(await stack.visit_named(site.project))
            await asyncio.sleep(0.05)

    async with asyncio.TaskGroup() as group:
        watcher = group.create_task(watch())
        try:
            promoted = await _promote(stack, site, release, timer, FAILED_PROMOTE_TIMEOUT)
        finally:
            stop.set()
        await watcher
    assert promoted["status"] == "failed", promoted
    assert promoted["failureCode"] == "site_not_answering", promoted
    body = await stack.release(site.project, release)
    assert (body["status"], body.get("failureCode")) == ("rolled_back", "site_not_answering")
    # The site never went dark: every visit was answered by v2 or by the candidate.
    assert len(seen) > 10, len(seen)
    # Every visit got an answer from the Gateway naming v2 or the candidate. Cilium's data plane
    # answers a few requests 503 while a route moves between two backends (measured by T2:
    # 3 of 417 visits, all naming v2, at the switch and at the restore); nothing else. The
    # allowance is 5 per route move, as when one try made 2 moves (10); 3 tries make 6 moves.
    unanswered = [(i, x) for i, x in enumerate(seen) if x[0] not in (200, 503)]
    assert not unanswered, (len(seen), unanswered)
    transitions = [x for x in seen if x[0] == 503]
    assert len(transitions) <= 5 * ROUTE_MOVES, (len(seen), transitions)
    others = [x for x in seen if x[1] not in ([v2], [str(release)])]
    assert not others, others
    deployment = await stack.deployment(site.project)
    assert deployment["liveReleaseId"] == v2
    assert deployment["routingStatus"] == "stable"
    await _serves(stack, site, "sample-site version: v2")
    await _named(stack, site, site.releases["v2"])
    timer.save(
        {
            "failed_switch_promote_seconds": timer.between("promote_posted", "promote_finished"),
            "visits": len(seen),
            "visits_by_candidate": sum(1 for _, names in seen if names == [str(release)]),
            "visits_503_in_transitions": sum(1 for status, _ in seen if status == 503),
        }
    )


async def test_5_suspend_shows_the_offline_page_and_resume_brings_v2_back(
    stack: Stack, site: Site
) -> None:
    timer = Timer("flow5-suspend-resume")
    timer.mark("ended_published")
    await stack.simulate("entitlement-ended", "--project", str(site.project))
    await until(
        "the offline page",
        lambda: stack.visit(site.project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )
    timer.mark("offline")
    deployment = await stack.deployment(site.project)
    assert (deployment["hostingStatus"], deployment["routingStatus"]) == ("offline", "stable")
    timer.mark("restored_published")
    await stack.simulate("entitlement-restored", "--project", str(site.project))
    await _serves(stack, site, "sample-site version: v2")
    timer.mark("serving")
    deployment = await stack.deployment(site.project)
    assert (deployment["hostingStatus"], deployment["routingStatus"]) == ("online", "stable")
    assert deployment["liveReleaseId"] == str(site.releases["v2"])
    timer.save({})
