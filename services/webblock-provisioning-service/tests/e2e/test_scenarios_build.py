"""The build path live (task W6-F; TR-7).

- A build that fails once is retried and then succeeds (the stack's own Git server is stopped for
  the first attempt, so the clone fails; it is started again and the second attempt builds).
- Two concurrent ``build_release`` calls (one REST, one MCP) on a release whose next attempt is
  pending: since the build/API split (decision 0016 section 5 item 2, task H2) the API writes
  nothing, so both are accepted (202), unless the build worker, woken by the first call's hint,
  already started the attempt when the second is read (then that one is ``build_in_progress``);
  either way Jenkins holds exactly one build per attempt and the release ends as before.
- Attempts used up: the release ends ``failed`` with ``build_attempts_exhausted``. Diagnostics
  and both listings over MCP then show the failure codes and nothing else: no token, no URL with
  credentials, no free text.

The all-attempts-exhausted lifecycle with its audit lines is also pinned by
``faults/test_build_failed_attempts.py`` (task W6-L); the tests here add what that file lacks.

Needs ``make proto-up proto-run``. Only the stack's own ``webblock-git`` container is stopped, and
it is started again whatever happens.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import pytest
from tests.e2e._stack import (
    Stack,
    docker,
    new_project,
    new_repository_id,
    until,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

CODE = re.compile(r"[a-z][a-z0-9_]*")
CREDENTIAL_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s\"']*@")


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


async def _publish(stack: Stack, variant: str, label: str) -> tuple[uuid.UUID, uuid.UUID]:
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-{label}-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, variant, label)
    request = await stack.publish(project, repository_id, sha)
    return project, await stack.release_of(request)


def _builds(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in body["steps"] if s["stepName"] == "build"]


async def test_a_build_that_fails_once_is_retried_and_then_succeeds(stack: Stack) -> None:
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-retry-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "retry")  # on disk: stopping the server loses nothing
    docker("stop", "webblock-git")
    try:
        request = await stack.publish(project, repository_id, sha)
        release = await stack.release_of(request)
        failed = await until(
            "attempt 1 failed",
            lambda: stack.release(project, release),
            lambda b: any(s["attempt"] == 1 and s["status"] == "failed" for s in _builds(b)),
            300,
            2,
        )
        assert failed["status"] == "building", failed  # attempts remain: not failed yet
    finally:
        docker("start", "webblock-git")

    built = await stack.wait_release(project, release, {"built", "failed"}, 600)
    assert built["status"] == "built", built
    builds = _builds(built)
    assert [(s["attempt"], s["status"]) for s in builds] == [(1, "failed"), (2, "succeeded")]
    assert builds[0]["failureCode"], builds[0]
    assert built["containerImageDigest"].startswith("sha256:")
    assert await stack.jenkins_builds(release) == 2


async def test_concurrent_build_release_starts_one_attempt_and_refuses_the_other(
    stack: Stack,
) -> None:
    project, release = await _publish(stack, "broken-build", "concurrent")
    # Attempt 1 fails; its failure queues attempt 2 as pending until the build worker's grace has
    # passed (or a build_release hint wakes it). Both calls arrive while it is pending.
    await until(
        "attempt 2 pending",
        lambda: stack.release(project, release),
        lambda b: any(s["attempt"] == 2 and s["status"] == "pending" for s in _builds(b)),
        300,
        0.25,
    )

    async def over_rest() -> str:
        accepted = await stack.post(project, release, "build", None)
        if accepted.status_code in {200, 202}:
            return "started"
        assert accepted.status_code == 409, accepted.body
        return str(accepted.body["code"])

    async def over_mcp() -> str:
        async with stack.mcp() as mcp:
            result = await mcp.call("build_release", projectId=str(project), releaseId=str(release))
        return "started" if not result.is_error else str(result.body["code"])

    outcomes = sorted(await asyncio.gather(over_rest(), over_mcp()))
    # Task H2: the API only checks the pending step and hints the build worker, so two requests
    # for one pending attempt are both accepted. The worker may start the attempt between the two
    # reads, so the second may see it running; never may both be refused.
    assert outcomes in (["started", "started"], ["BUILD_IN_PROGRESS", "started"]), outcomes
    # The hint starts attempt 2 at once: well within BUILD_PENDING_GRACE (30 s in the prototype),
    # counted from attempt 1's end, after which the grace scan alone would have started it.
    await until(
        "attempt 2 started by the build hint, before the grace",
        lambda: stack.release(project, release),
        lambda b: any(s["attempt"] == 2 and s["status"] != "pending" for s in _builds(b)),
        15,
        0.25,
    )

    # One attempt was started, so Jenkins holds one build per attempt in the end.
    done = await until(
        "the release to settle",
        lambda: stack.release(project, release),
        lambda b: b["status"] in {"failed", "built"},
        900,
        5,
    )
    assert done["status"] == "failed", done
    assert done["failureCode"] == "build_attempts_exhausted", done
    assert [s["attempt"] for s in _builds(done)] == [1, 2, 3]
    assert await stack.jenkins_builds(release) == 3

    await _diagnostics_and_listings_over_mcp(stack, project, release)


def _scan_for_leaks(stack: Stack, what: object) -> str:
    text = json.dumps(what)
    for subject in ("prototype-operator", "prototype-tenant", "prototype-reader"):
        assert stack.token_for(subject) not in text
    assert "Bearer" not in text
    assert not CREDENTIAL_URL.search(text), "a URL with credentials in the body"
    for hidden in ("failure_summary", "log_ref", "requested_by", "idempotency", "jenkins"):
        assert hidden not in text, hidden
    return text


def _codes(what: Any) -> list[str]:
    """Every ``failure_code`` and ``outcome_code`` value anywhere in ``what``."""
    found: list[str] = []
    if isinstance(what, dict):
        for key, value in what.items():
            if key in {"failure_code", "outcome_code"} and isinstance(value, str):
                found.append(value)
            else:
                found += _codes(value)
    elif isinstance(what, list):
        for item in what:
            found += _codes(item)
    return found


async def _diagnostics_and_listings_over_mcp(
    stack: Stack, project: uuid.UUID, release: uuid.UUID
) -> None:
    """A failed release is explained by codes alone, over MCP and the same over REST."""
    async with stack.mcp() as mcp:
        diag = await mcp.call(
            "get_release_diagnostics", projectId=str(project), releaseId=str(release)
        )
        releases = await mcp.call("list_releases", projectId=str(project), limit=10)
        commands = await mcp.call("list_commands", projectId=str(project), limit=10)
    for result in (diag, releases, commands):
        assert not result.is_error, result.body

    body = diag.body
    assert body["release"]["status"] == "failed"
    assert body["release"]["failureCode"] == "build_attempts_exhausted"
    steps = sorted(
        (s for s in body["steps"] if s["stepName"] == "build"), key=lambda s: s["attempt"]
    )
    assert [(s["attempt"], s["status"], s.get("failureCode")) for s in steps] == [
        (1, "failed", "build_failed"),
        (2, "failed", "build_failed"),
        (3, "failed", "build_failed"),
    ]
    assert body["commands"] == [] and body["stepsTruncated"] is False
    assert body["sourceEvent"]["status"] == "accepted"
    assert [i["status"] for i in releases.body["items"]] == ["failed"]
    assert releases.body["items"][0]["releaseId"] == str(release)
    assert releases.body["items"][0]["failureCode"] == "build_attempts_exhausted"
    assert commands.body == {"items": []}

    # The failure codes are machine codes, never text, and nothing secret is in any body.
    for what in (diag.body, releases.body, commands.body):
        _scan_for_leaks(stack, what)
    codes = _codes(diag.body) + _codes(releases.body)
    assert codes and all(CODE.fullmatch(c) for c in codes), codes

    # REST answers the same bodies (one set of use cases behind both transports).
    assert await stack.get(f"/v1/projects/{project}/releases/{release}/diagnostics") == body
    assert await stack.page(f"/v1/projects/{project}/releases?limit=10") == releases.body
