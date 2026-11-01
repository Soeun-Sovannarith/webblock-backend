"""Builds with two build workers (task H5b; decisions 0016 section 6 item 1 and 0014 section 3).

- Both build workers LISTEN for build hints: a malformed hint sent once on
  ``provisioning_build_wanted`` is dropped by each of them (each logs it), so a notification
  reaches both.
- ``build_release`` through each API at once, on a release whose next attempt is pending: the two
  hints reach both build workers, yet the attempt starts once (the conditional pending -> running
  write), Jenkins holds exactly one build per attempt, and each attempt has one
  ``build_attempt_started`` audit event across the two build workers.
- A burst of releases (four sources published at once): each release is built once, by one build
  worker; which build worker took which is recorded (the intake takes inbox rows in batches, so
  one build worker may take them all; that is allowed, a duplicate is not).

Needs ``make proto-up proto-run-replicas``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import Counter
from typing import Any

import pytest
from tests.e2e._stack import TIMINGS, Stack, new_project, new_repository_id, until
from tests.e2e.replicas._replicas import BUILD_WORKERS, Logs, require_replicas

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1800)]

CHANNEL = "provisioning_build_wanted"


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


async def _publish(stack: Stack, variant: str, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    project, repository_id = new_project(), new_repository_id()
    name = f"h5b-{label}-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, variant, label)
    request = await stack.publish(project, repository_id, sha)
    return project, await stack.release_of(request)


def _builds(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in body["steps"] if s["stepName"] == "build"]


def _started(logs: Logs, release: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, e)
        for name in BUILD_WORKERS
        for e in logs.audit(name)
        if e.get("action") == "build_attempt_started" and e.get("release_id") == str(release)
    ]


async def test_both_build_workers_listen_for_hints(stack: Stack) -> None:
    logs = Logs(BUILD_WORKERS)
    await stack.notify(CHANNEL, "h5b-probe")
    for name in BUILD_WORKERS:

        async def read(n: str = name) -> list[dict[str, Any]]:
            return logs.entries(n)

        await until(
            f"{name} dropping the malformed hint",
            read,
            lambda entries: any(
                str(e.get("msg")) == "malformed build hint dropped (bytes=9)" for e in entries
            ),
            30,
            0.5,
        )


async def test_build_release_through_both_apis_starts_each_attempt_once(stack: Stack) -> None:
    logs = Logs(BUILD_WORKERS)
    project, release = await _publish(stack, "broken-build", "hint")
    await until(
        "attempt 2 pending",
        lambda: stack.release(project, release),
        lambda b: any(s["attempt"] == 2 and s["status"] == "pending" for s in _builds(b)),
        300,
        0.25,
    )
    results = await asyncio.gather(
        *(stack.post(project, release, "build", None, api=api) for api in stack.apis)
    )
    outcomes = sorted(
        "started" if r.status_code in {200, 202} else str(r.body.get("code")) for r in results
    )
    assert outcomes in (["started", "started"], ["BUILD_IN_PROGRESS", "started"]), outcomes
    done = await until(
        "the release to settle",
        lambda: stack.release(project, release),
        lambda b: b["status"] in {"failed", "built"},
        900,
        5,
    )
    assert done["status"] == "failed", done
    assert [s["attempt"] for s in _builds(done)] == [1, 2, 3]
    assert await stack.jenkins_builds(release) == 3
    # One started event per attempt (the audit event names the release, not the attempt).
    started = _started(logs, release)
    assert len(started) == 3, started


async def test_a_burst_of_releases_is_built_once_each(stack: Stack) -> None:
    logs = Logs(BUILD_WORKERS)
    published = await asyncio.gather(*(_publish(stack, "site", f"burst{i}") for i in range(4)))
    built = await asyncio.gather(
        *(stack.wait_release(p, r, {"built", "failed"}, 900) for p, r in published)
    )
    assert all(b["status"] == "built" for b in built), built
    by_worker: Counter[str] = Counter()
    for _, release in published:
        assert await stack.jenkins_builds(release) == 1, release
        started = _started(logs, release)
        assert len(started) == 1, (release, started)
        by_worker[started[0][0]] += 1
    TIMINGS.mkdir(parents=True, exist_ok=True)
    (TIMINGS / "h5b-build-burst.json").write_text(
        json.dumps({"builds_started_per_build_worker": dict(by_worker)}, indent=2)
    )
