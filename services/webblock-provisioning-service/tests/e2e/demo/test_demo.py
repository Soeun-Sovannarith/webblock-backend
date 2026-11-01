"""The shop demo and the worker's effectiveness (task G, part 4; decision 0014 section 14).

1. The website is registered in the catalog and S's clothing shop is published to the local Git
   server (Project's stand-ins).
2. "Source published" → the build on Jenkins → bootstrap and access_setup (the Secret
   ``catalog-runtime``) → the products are seeded as the site's own role → deploy → promote, with
   /readyz as the promotion probe (decision 0014 section 15).
3. The Gateway shows the shop with 12 product cards, and a details page renders, read through
   Drizzle as the site's own role.
4. The shop over HTTPS on its platform subdomain, verified against the local CA alone (the
   Gateway's wildcard certificate; decision 0014 section 23: no custom domains).

The durations come from the database rows (one clock, the host's) and are written to
bin/kind-state/proto/provisioning/timings/. Then five sample sites are published at once.

Needs ``make proto-up proto-run`` and network access from the stack's BuildKit to Docker Hub and
the npm registry (the shop's build runs ``npm ci``).
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
import uuid
from typing import Any

import pytest
from tests.e2e._stack import ROOT, Stack, Timer, new_project, new_repository_id, run, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1800)]

SEED = ROOT / "deploy" / "prototype" / "demo-sites" / "clothing-shop" / "seed" / "seed.json"


async def catalog_tool(*args: str) -> str:
    return await run([sys.executable, "-m", "provisioning.hosts.devtools.catalog_platform", *args])


async def _rows(stack: Stack, release: uuid.UUID) -> dict[str, Any]:
    """The DB timestamps of a release's build and candidate steps and its commands."""
    rel = await stack.query(
        "SELECT created_at, promoted_at FROM provisioning.releases WHERE release_id = %s", release
    )
    steps = await stack.query(
        "SELECT step_name, attempt, started_at, finished_at FROM provisioning.release_steps"
        " WHERE release_id = %s ORDER BY step_name, attempt",
        release,
    )
    commands = await stack.query(
        "SELECT command_id, kind, status, created_at, updated_at FROM provisioning.commands"
        " WHERE release_id = %s ORDER BY created_at",
        release,
    )
    return {"release": rel[0], "steps": steps, "commands": commands}


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


async def test_the_clothing_shop_from_commit_to_live(stack: Stack) -> None:
    project, repository_id = new_project(), new_repository_id()
    timer = Timer("demo-clothing-shop")
    await catalog_tool("register", "--project", str(project), "--business-type", "clothing_shop")
    sha = (
        (
            await run(
                [
                    "bash",
                    str(ROOT / "scripts" / "prototype" / "publish-demo-site.sh"),
                    "clothing-shop",
                ]
            )
        )
        .strip()
        .splitlines()[-1]
    )
    stack.register(project, repository_id, "clothing-shop")
    timer.mark("event_published")
    request = await stack.publish(project, repository_id, sha)
    release = await stack.release_of(request)
    timer.mark("release_seen")
    built = await stack.wait_release(project, release, {"built", "failed"}, 900)
    timer.mark("built")
    assert built["status"] == "built", built
    # access_setup has written the Secret once the tenant is ready; the CMS stand-in then loads the
    # products as the site's own role.
    await until(
        "the site role's Secret (bootstrap and access_setup)",
        _seed_once(project),
        lambda ok: ok,
        120,
        interval=2,
    )
    timer.mark("seeded")
    deploy = await stack.post(project, release, "deploy", f"demo-deploy-{release}")
    assert deploy.status_code == 202, deploy.body
    running_at = await _observed_running(stack, project, deploy.command_id)
    deployed = await stack.wait_command(project, deploy.command_id, 600)
    timer.mark("candidate_ready")
    assert deployed["status"] == "succeeded", deployed
    promote = await stack.post(project, release, "promote", f"demo-promote-{release}")
    assert promote.status_code == 202, promote.body
    promoted = await stack.wait_command(project, promote.command_id, 300)
    timer.mark("live")
    assert promoted["status"] == "succeeded", promoted
    # The shop answers 503 while it cannot read its catalog: wait for it, then fail with the status
    # and the start of the body.
    status, page = await stack.visit_when_available(project)
    assert status == 200, f"shop answered {status}: {page[:500]}"
    cards = page.count('<article class="card"')
    assert cards == 12, f"{cards} cards (status {status}): {page[:500]}"
    links = re.findall(r'href="(/products/[0-9a-f-]{36})"', page)
    assert links, "no details link"
    detail_status, detail = await stack.visit_when_available(project, links[0])
    assert detail_status == 200 and "detail__info" in detail, (
        f"details page answered {detail_status}: {detail[:500]}"
    )
    ready_status, _ = await stack.visit(project, "/readyz")
    assert ready_status == 200
    timer.mark("verified")

    # The shop over HTTPS on its platform subdomain, verified against the local CA alone.
    https_page = await stack.visit_https(stack.host(project))
    assert https_page is not None and https_page.status_code == 200, https_page
    assert https_page.text.count('<article class="card"') == 12
    assert https_page.headers.get_list("X-Webblock-Release") == [str(release)]

    rows = await _rows(stack, release)
    publish_deploy = await _publish_latency(stack, deploy.command_id)
    publish_promote = await _publish_latency(stack, promote.command_id)
    build = next(s for s in rows["steps"] if s[0] == "build")
    candidate = next(s for s in rows["steps"] if s[0] == "candidate")
    by_kind = {c[1]: c for c in rows["commands"]}
    released_at = rows["release"][0].timestamp()
    extra = {
        "project_id": str(project),
        "release_id": str(release),
        "commit": sha,
        "event_to_release_accepted_s": released_at - timer.marks["event_published"],
        "build_queued_to_built_s": (build[3] - build[2]).total_seconds(),
        "deploy_accepted_to_running_s": running_at - by_kind["deploy"][3].timestamp(),
        "deploy_accepted_to_candidate_ready_s": (
            by_kind["deploy"][4] - by_kind["deploy"][3]
        ).total_seconds(),
        "candidate_step_s": (candidate[3] - candidate[2]).total_seconds(),
        "promote_accepted_to_live_s": (
            by_kind["promote"][4] - by_kind["promote"][3]
        ).total_seconds(),
        "commit_to_live_s": rows["release"][1].timestamp() - timer.marks["event_published"],
        "dispatcher_publish_latency_deploy_s": publish_deploy,
        "dispatcher_publish_latency_promote_s": publish_promote,
        "product_cards": cards,
    }
    timer.save(extra)


def _seed_once(project: uuid.UUID) -> Any:
    async def attempt() -> bool:
        try:
            await catalog_tool("seed", "--project", str(project), "--file", str(SEED))
        except AssertionError as exc:
            if "does not exist yet" in str(exc) or "Secret" in str(exc):
                return False
            raise
        return True

    return attempt


async def _observed_running(stack: Stack, project: uuid.UUID, command_id: str) -> float:
    """The host time at which the command was first seen running (polled every 50 ms)."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = (await stack.command(project, command_id))["status"]
        if status != "accepted":
            return time.time()
        await asyncio.sleep(0.05)
    raise AssertionError("the command was never claimed")


async def _publish_latency(stack: Stack, command_id: str) -> float | None:
    """Acceptance to the broker's confirm, from the dispatch row; None when the command finished
    before the dispatcher recorded the confirm (its record is conditional on an unfinished row)."""
    rows = await stack.query_ops(
        "SELECT d.last_broker_ack_at - c.created_at FROM provisioning_ops.command_dispatch d"
        " JOIN provisioning.commands c ON c.command_id = d.command_id WHERE d.command_id = %s",
        uuid.UUID(command_id),
    )
    latency = rows[0][0]
    return None if latency is None else float(latency.total_seconds())


async def test_five_sites_published_at_once_go_live_one_command_at_a_time(stack: Stack) -> None:
    # Per-project serialization is sampled every 250 ms from the commands table: no project ever
    # has two commands accepted or running.
    sites = [(new_project(), new_repository_id()) for _ in range(5)]
    names = [f"conc-{str(p)[-12:]}" for p, _ in sites]
    for (project, repository_id), name in zip(sites, names, strict=True):
        stack.register(project, repository_id, name)
    shas = [await stack.commit(name, "site", "conc") for name in names]
    peak = {"memory_mb": 0, "in_flight_max": 0}
    stop = asyncio.Event()

    async def sample() -> None:
        while not stop.is_set():
            available = _available_mb()
            used = _total_mb() - available
            peak["memory_mb"] = max(peak["memory_mb"], used)
            rows = await stack.query(
                "SELECT project_id, count(*) FROM provisioning.commands"
                " WHERE status IN ('accepted', 'running') AND project_id = ANY(%s)"
                " GROUP BY project_id",
                [p for p, _ in sites],
            )
            peak["in_flight_max"] = max([peak["in_flight_max"], *(int(r[1]) for r in rows)])
            await asyncio.sleep(0.25)

    async def one(index: int) -> float:
        project, repository_id = sites[index]
        request = await stack.publish(project, repository_id, shas[index])
        release = await stack.release_of(request)
        await stack.wait_release(project, release, {"built"}, 600)
        deploy = await stack.post(project, release, "deploy", f"c-deploy-{release}")
        assert deploy.status_code == 202, deploy.body
        assert (await stack.wait_command(project, deploy.command_id, 600))["status"] == "succeeded"
        promote = await stack.post(project, release, "promote", f"c-promote-{release}")
        assert promote.status_code == 202, promote.body
        assert (await stack.wait_command(project, promote.command_id, 300))["status"] == "succeeded"
        await until(
            f"site {index} serving",
            lambda: stack.visit(project),
            lambda r: r[0] == 200 and "sample-site version: conc" in r[1],
            60,
        )
        return time.time()

    baseline = _total_mb() - _available_mb()
    start = time.time()
    sampler = asyncio.create_task(sample())
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(one(i)) for i in range(5)]
    finally:
        stop.set()
        await sampler
    finished = [t.result() for t in tasks]
    assert peak["in_flight_max"] <= 1, peak
    Timer("demo-concurrency-5").save(
        {
            "all_live_s": max(finished) - start,
            "first_live_s": min(finished) - start,
            "peak_machine_memory_used_mb": peak["memory_mb"],
            "baseline_machine_memory_used_mb": baseline,
            "max_commands_in_flight_per_project": peak["in_flight_max"],
        }
    )


def _meminfo(key: str) -> int:
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith(key + ":"):
                return int(line.split()[1]) // 1024
    raise AssertionError(f"/proc/meminfo has no {key}")


def _available_mb() -> int:
    return _meminfo("MemAvailable")


def _total_mb() -> int:
    return _meminfo("MemTotal")
