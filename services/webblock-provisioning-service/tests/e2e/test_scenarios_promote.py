"""Promotion and recovery rows live (task W6-F).

- Outdated release: a release created before the live one is refused (409 ``release_outdated``) at
  acceptance, over REST and MCP, and creates no command.
- Go-live tries: one promote command makes up to 3 tries, one per claim of its
  dispatch row; claim 2 starts no sooner than 30 s after claim 1 was given back, claim 3 no
  sooner than 60 s after claim 2 (the row's ``retry_after``).
- First publish: a candidate that passes its preview but fails after the switch is tried 3 times;
  between tries the route is on the offline page and the release ``promoting``; after try 3 the
  route is restored to the offline page, the release ``failed``, no live release, the site
  offline and the candidate's workload gone.
- Live keeps serving: during a failed promotion a client loop counts the answers. A candidate
  failing its preview never costs one non-200 answer; one failing after the switch costs only the
  Gateway's few 503s while the route moves, and the old release answers again afterwards. Each
  ends ``rolled_back`` with its last try's code, which a GET of the release returns.
- Uncertain route: a restore that cannot be confirmed (the route's command number is ahead
  of the command's, an edit by hand) leaves the route ``uncertain``, the alert is written once, new
  deploys are refused, the operator's rollback is refused (403: rollback is ``ADMIN`` only), and
  the admin's rollback over REST brings it back ``stable``.

Needs ``make proto-up proto-run``. Each test uses its own project (``Site``).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

import pytest
from tests.e2e._stack import FAILED_PROMOTE_TEST_TIMEOUT, FAILED_PROMOTE_TIMEOUT, Stack, until
from tests.e2e.faults._recovery_support import (
    Site,
    audit_events,
    eventually,
    kubectl,
    log_mark,
    namespace,
    worker_log_since,
    workloads,
)

from provisioning.core.domain import GO_LIVE_TRIES, wait_after_failed_try

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(FAILED_PROMOTE_TEST_TIMEOUT)]

# How often the dispatch row is read while a promote runs its tries.
DISPATCH_POLL = 0.2
# Each try that fails after the switch moves the route twice: to the candidate and back.
ROUTE_MOVES = 2 * GO_LIVE_TRIES


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


async def _commands_of(stack: Stack, release: uuid.UUID) -> int:
    rows = await stack.query(
        "SELECT count(*) FROM provisioning.commands WHERE release_id = %s", release
    )
    return int(rows[0][0])


async def test_a_release_older_than_the_live_one_is_refused_release_outdated(
    stack: Stack,
) -> None:
    site = Site(stack)
    older = await site.build("older")
    newer = await site.live("newer")  # built later, deployed and promoted
    assert (await stack.release(site.id, older))["status"] == "built"

    refused = await stack.post(site.id, older, "deploy", "outdated-rest")
    assert (refused.status_code, refused.body["code"]) == (422, "RELEASE_OUTDATED"), refused.body
    async with stack.mcp() as mcp:
        via_mcp = await mcp.call(
            "deploy_release",
            projectId=str(site.id),
            releaseId=str(older),
            idempotency_key="outdated-mcp",
        )
    assert via_mcp.is_error and via_mcp.body["code"] == "RELEASE_OUTDATED", via_mcp.body
    # Nothing was accepted for the outdated release, and the live one is untouched.
    assert await _commands_of(stack, older) == 0
    deployment = await stack.deployment(site.id)
    assert (deployment["liveReleaseId"], deployment["routingStatus"]) == (str(newer), "stable")
    assert await site.answering() == str(newer)


async def _count_answers_during(
    stack: Stack, site: Site, release: uuid.UUID, action: str
) -> tuple[dict[str, object], list[tuple[int, list[str]]]]:
    """Run ``action`` on ``release`` while a client loop visits the site; the finished command and
    every (status, release header values) the loop saw."""
    seen: list[tuple[int, list[str]]] = []
    stop = asyncio.Event()

    async def watch() -> None:
        while not stop.is_set():
            seen.append(await stack.visit_named(site.id))
            await asyncio.sleep(0.05)

    async with asyncio.TaskGroup() as group:
        watcher = group.create_task(watch())
        try:
            done = await site.command(release, action, FAILED_PROMOTE_TIMEOUT)
        finally:
            stop.set()
        await watcher
    return done, seen


@dataclass(frozen=True, slots=True)
class DispatchSeen:
    """One read of a command's dispatch row, at ``at`` (the test's monotonic clock)."""

    at: float
    attempts: int
    claimed: bool
    waiting: bool
    finished: bool


async def _dispatch(stack: Stack, command_id: str) -> DispatchSeen:
    rows = await stack.query_ops(
        "SELECT execution_attempts, claimed_by_worker IS NOT NULL, "
        "retry_after IS NOT NULL AND retry_after > now(), finished_at IS NOT NULL "
        "FROM provisioning_ops.command_dispatch WHERE command_id = %s",
        uuid.UUID(command_id),
    )
    assert len(rows) == 1, rows
    attempts, claimed, waiting, finished = rows[0]
    return DispatchSeen(
        time.monotonic(), int(attempts), bool(claimed), bool(waiting), bool(finished)
    )


async def _between_tries(stack: Stack, command_id: str, after_try: int) -> None:
    """Until try ``after_try`` has failed and the row waits for the next claim."""
    await until(
        f"command {command_id} waiting after try {after_try}",
        lambda: _dispatch(stack, command_id),
        lambda d: d.attempts == after_try and not d.claimed and d.waiting and not d.finished,
        FAILED_PROMOTE_TIMEOUT,
        DISPATCH_POLL,
    )


@pytest.mark.timeout(FAILED_PROMOTE_TEST_TIMEOUT)
async def test_fr21_2_and_fr21_4_the_gaps_between_tries_are_30_s_and_60_s(stack: Stack) -> None:
    """FR-21.2, FR-21.4, FR-21.6, FR-37.3: a candidate failing its preview (the "unready"
    variant: dependency_unavailable) on a site with a live release. Claim 2 starts no sooner than
    30 s after claim 1 was given back, claim 3 no sooner than 60 s after claim 2, each less one
    poll of the row; the command ends after claim 3, the release rolled_back with try 3's code,
    and the live release still serves."""
    site = Site(stack)
    v1 = await site.live("v1")
    unready = await site.build("unready", variant="unready")
    assert (await site.command(unready, "deploy"))["status"] == "succeeded"

    command_id = await site.accept(unready, "promote")
    seen: list[DispatchSeen] = []
    deadline = time.monotonic() + FAILED_PROMOTE_TIMEOUT
    while not (seen and seen[-1].finished):
        assert time.monotonic() < deadline, seen[-5:]
        seen.append(await _dispatch(stack, command_id))
        await asyncio.sleep(DISPATCH_POLL)

    def given_back(attempt: int) -> float:
        at = next(
            (d.at for d in seen if d.attempts == attempt and not d.claimed and d.waiting), None
        )
        assert at is not None, f"claim {attempt} was never seen given back with a wait"
        return at

    def started(attempt: int) -> float:
        at = next((d.at for d in seen if d.attempts == attempt), None)
        assert at is not None, f"claim {attempt} was never seen"
        return at

    for failed_try in (1, 2):
        wait = wait_after_failed_try(failed_try)
        assert wait is not None
        gap = started(failed_try + 1) - given_back(failed_try)
        assert gap >= wait.total_seconds() - DISPATCH_POLL, (failed_try, gap)
    assert seen[-1].attempts == GO_LIVE_TRIES, seen[-1]

    done = await stack.wait_command(site.id, command_id, 60)
    assert (done["status"], done.get("failureCode")) == ("failed", "dependency_unavailable"), done
    body = await stack.release(site.id, unready)
    assert (body["status"], body.get("failureCode")) == ("rolled_back", "dependency_unavailable")
    deployment = await stack.deployment(site.id)
    assert (deployment["liveReleaseId"], deployment["routingStatus"]) == (str(v1), "stable")
    assert await site.answering() == str(v1)


@pytest.mark.timeout(FAILED_PROMOTE_TEST_TIMEOUT)
async def test_a_failed_first_publish_removes_the_route_and_the_site_stays_offline(
    stack: Stack,
) -> None:
    """FR-22.1, FR-22.2: no live release; each try of the "preview-only" candidate fails after
    the switch. Between tries the route is on the offline page and the release promoting; after
    try 3 the release is failed with try 3's code and the site offline."""
    site = Site(stack)
    # "preview-only" passes its preview, so the route is switched; its primary hostname then fails.
    release = await site.build("first", variant="preview-only")
    assert (await site.command(release, "deploy"))["status"] == "succeeded"
    assert f"rel-{release}" in await _rel_workloads(site)

    command_id = await site.accept(release, "promote")
    for failed_try in (1, 2):
        await _between_tries(stack, command_id, failed_try)
        assert (await stack.release(site.id, release))["status"] == "promoting"
        assert (await stack.deployment(site.id)).get("liveReleaseId") is None
        status, names = await stack.visit_named(site.id)
        # The offline page: the Gateway names it "offline" in the release header (render.py).
        assert (status, names) == (503, ["offline"]), (failed_try, status, names)
    done = await stack.wait_command(site.id, command_id, FAILED_PROMOTE_TIMEOUT)
    assert (done["status"], done.get("failureCode")) == ("failed", "site_not_answering"), done
    body = await stack.release(site.id, release)
    # No live release to fall back to: failed, not rolled_back, with try 3's code.
    assert (body["status"], body.get("failureCode")) == ("failed", "site_not_answering"), body
    deployment = await stack.deployment(site.id)
    assert deployment.get("liveReleaseId") is None, deployment
    assert (deployment["routingStatus"], deployment["hostingStatus"]) == ("stable", "online")
    # The route points at the offline page: the visitor gets the platform's 503 page, and no
    # release answers.
    _status, text = await until(
        "the offline page",
        lambda: stack.visit(site.id),
        lambda r: r[0] == 503,
        60,
    )
    assert "offline" in text.lower(), text
    assert await site.answering() is None
    # The failed candidate's workload is deleted by the cleanup after the verified outcome.
    await eventually(
        f"workload of {release} deleted",
        lambda: _rel_workloads(site),
        lambda names: f"rel-{release}" not in names,
        120,
    )


async def _rel_workloads(site: Site) -> set[str]:
    return {n for n in await asyncio.to_thread(workloads, site.id) if n.startswith("rel-")}


@pytest.mark.timeout(2 * FAILED_PROMOTE_TIMEOUT + 600)
async def test_the_live_release_keeps_serving_while_a_promotion_fails(stack: Stack) -> None:
    """FR-21.6, FR-21.1, FR-37.3: two promotes, each failing all 3 tries; v1 keeps serving and
    each candidate ends rolled_back with its last try's code."""
    site = Site(stack)
    v1 = await site.live("v1")

    # A candidate failing its preview (its /readyz answers 503: dependency_unavailable, task
    # H5a): the route is never pointed at it.
    unready = await site.build("unready", variant="unready")
    assert (await site.command(unready, "deploy"))["status"] == "succeeded"
    done, seen = await _count_answers_during(stack, site, unready, "promote")
    assert done["failureCode"] == "dependency_unavailable", done
    assert len(seen) > 20, len(seen)
    non_200 = [x for x in seen if x[0] != 200]
    assert non_200 == [], f"{len(non_200)} of {len(seen)} answers were not 200: {non_200[:5]}"
    assert all(names == [str(v1)] for _, names in seen), seen[:5]
    body = await stack.release(site.id, unready)
    assert (body["status"], body.get("failureCode")) == ("rolled_back", "dependency_unavailable")

    # A candidate failing after the switch: the route is restored after each try. Only the
    # Gateway's short 503s while the route moves are tolerated (measured by task T2: a handful
    # for one try's 2 moves, allowed 5 per move), and v1 answers again.
    late = await site.build("late", variant="preview-only")
    assert (await site.command(late, "deploy"))["status"] == "succeeded"
    done, seen = await _count_answers_during(stack, site, late, "promote")
    assert done["failureCode"] == "site_not_answering", done
    assert len(seen) > 20, len(seen)
    bad = [x for x in seen if x[0] not in (200, 503)]
    assert bad == [], bad
    transitions = [x for x in seen if x[0] == 503]
    assert len(transitions) <= 5 * ROUTE_MOVES, (len(seen), len(transitions))
    assert all(names in ([str(v1)], [str(late)]) for status, names in seen if status == 200)
    body = await stack.release(site.id, late)
    assert (body["status"], body.get("failureCode")) == ("rolled_back", "site_not_answering")
    deployment = await stack.deployment(site.id)
    assert (deployment["liveReleaseId"], deployment["routingStatus"]) == (str(v1), "stable")
    await eventually("v1 answering again", site.answering, lambda name: name == str(v1), 60)


async def test_an_unconfirmed_restore_leaves_the_route_uncertain_until_the_operator_rolls_back(
    stack: Stack,
) -> None:
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    mark = log_mark()

    # An edit by hand puts the route's command number far ahead: the switch and the restore are
    # both refused by the fence, so the restore cannot be confirmed.
    await asyncio.to_thread(
        kubectl,
        "-n",
        namespace(site.id),
        "annotate",
        "httproute",
        f"route-{site.id}",
        "websites.webblock.internal/command-number=999999",
        "--overwrite",
    )
    # The switch is refused on each try (the route stays on v1, so tries 1 and 2 put nothing
    # back); try 3's restore is refused too, so the route ends uncertain (FR-21.1, FR-23).
    promote = await site.command(v2, "promote", FAILED_PROMOTE_TIMEOUT)
    assert promote["status"] == "failed", promote
    assert promote["failureCode"] in {"route_restore_unconfirmed", "route_refused"}, promote
    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "uncertain", deployment
    assert deployment["liveReleaseId"] == str(v1), deployment  # the recorded rollback target

    # The project accepts no ordinary command while the route is uncertain.
    other = await stack.post(site.id, v2, "deploy", uuid.uuid4().hex)
    assert (other.status_code, other.body["code"]) == (422, "ROUTE_NOT_STABLE"), other.body

    # FR-50: rollback is ADMIN only, so the operator's token, without the role, is refused.
    refused = await stack.post(site.id, v2, "rollback", uuid.uuid4().hex)
    assert (refused.status_code, refused.body["code"]) == (403, "ACCESS_DENIED"), refused.body
    # The admin's rollback over REST (Site.command sends it with the ADMIN token): accepted
    # although the route is uncertain.
    rollback = await site.command(v2, "rollback")
    assert rollback["status"] == "succeeded", rollback
    deployment = await stack.deployment(site.id)
    assert (deployment["routingStatus"], deployment["liveReleaseId"]) == ("stable", str(v1))
    await eventually(f"{v1} answering", site.answering, lambda name: name == str(v1), 60)

    # One alert for the uncertain route, no more.
    events = audit_events(await asyncio.to_thread(worker_log_since, mark))
    alerts = [e for e in events if e.get("alert") is True and e.get("project_id") == str(site.id)]
    assert [e["action"] for e in alerts] == ["route_uncertain"], alerts
