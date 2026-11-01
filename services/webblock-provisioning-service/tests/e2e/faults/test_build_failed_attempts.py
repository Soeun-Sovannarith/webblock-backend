"""QA-10 live (task W6-L): a build that fails once no longer leaves the release ``building``.

The release is built from the stock ``broken-build`` sample, whose Dockerfile fails every time.
``BUILD_MAX_ATTEMPTS`` is 3 in the prototype: each failed attempt queues the next as ``pending``,
the build worker's loop starts it once ``BUILD_PENDING_GRACE`` (30 s) has passed, and the third
failure ends the release ``failed`` with ``build_attempts_exhausted``. Before the fix, attempt 2
stayed ``pending`` and the release ``building`` for good.

The first test is QA's reproduction (``tests/qa/test_qa_live_failed_build_is_not_retried.py`` at
``d487207``); the second pins the whole lifecycle and its audit lines in Loki (W6-K's
``deploy/kind/proto-loki-query.sh``): one ``release_accepted``, and three ``build_attempt_started``
and ``build_attempt_finished`` (failed ``build_failed``), actor ``build-worker``. Needs
``make proto-up proto-run``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from tests.e2e._stack import ROOT, Stack, new_project, new_repository_id, run, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

LOKI_QUERY = ROOT / "deploy" / "kind" / "proto-loki-query.sh"


async def audit_lines(project: uuid.UUID) -> list[dict[str, Any]]:
    """The project's audit events in Loki, oldest first."""
    query = (
        '{job="webblock-provisioning"} | json | audit="true" | project_id="' + str(project) + '"'
    )
    out = await run(["bash", str(LOKI_QUERY), "--since", "1h", "--limit", "500", query])
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def _broken_release(stack: Stack, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    project, repository_id = new_project(), new_repository_id()
    name = f"w6l-{label}-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "broken-build", "broken")
    request = await stack.publish(project, repository_id, sha)
    return project, await stack.release_of(request)


async def test_a_failed_build_attempt_is_followed_by_another_or_by_failure() -> None:
    stack = Stack()
    project, release = await _broken_release(stack, "qa10")
    body = await until(
        "the release to settle",
        lambda: stack.release(project, release),
        lambda b: (
            b["status"] == "failed"
            or any(s["attempt"] == 2 and s["status"] != "pending" for s in b["steps"])
        ),
        240,
        3,
    )
    assert body["status"] == "failed" or any(s["attempt"] == 2 for s in body["steps"])


async def test_every_attempt_of_a_broken_build_runs_and_the_release_ends_failed() -> None:
    stack = Stack()
    project, release = await _broken_release(stack, "exhaust")
    body = await until(
        "the release to end failed",
        lambda: stack.release(project, release),
        lambda b: b["status"] in {"failed", "built"},
        780,
        5,
    )
    builds = [s for s in body["steps"] if s["stepName"] == "build"]
    assert body["status"] == "failed", body
    assert body["failureCode"] == "build_attempts_exhausted", body
    assert [(s["attempt"], s["status"], s.get("failureCode")) for s in builds] == [
        (1, "failed", "build_failed"),
        (2, "failed", "build_failed"),
        (3, "failed", "build_failed"),
    ]
    # One Jenkins build per attempt: a pending attempt was started once, never twice.
    assert await stack.jenkins_builds(release) == 3

    def summary(lines: list[dict[str, Any]]) -> list[tuple[str, str, str | None]]:
        return [(e["action"], e["result"], e.get("failure_code")) for e in lines]

    want = [("release_accepted", "accepted", None)] + [
        ("build_attempt_started", "succeeded", None),
        ("build_attempt_finished", "failed", "build_failed"),
    ] * 3
    lines = await until(
        "the audit lines in Loki",
        lambda: audit_lines(project),
        lambda found: len(found) >= len(want),
        120,
        5,
    )
    assert summary(lines) == want, summary(lines)
    assert all(e["release_id"] == str(release) for e in lines)
    assert {e["actor"] for e in lines} == {"project", "build-worker"}
