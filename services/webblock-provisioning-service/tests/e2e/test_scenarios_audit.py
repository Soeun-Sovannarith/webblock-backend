"""The audit trail of one full cycle in Loki (task W6-F; decision 0014 section 21 answer 3).

One project goes through publish, build, deploy (by an agent over MCP), promote (by a user over
REST), suspend and resume (Payment events). Every audit action of that cycle appears exactly once,
with the right actor kind, none appears twice, and no line carries a token.

Needs ``make proto-up proto-run`` (Loki and the log shipper run on the stack). Lines the cycle may
also write, depending on what has merged (the hosting events' own "accepted" line and the usage
event of task W6-E), are allowed but must also appear at most once.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest
from tests.e2e._stack import Stack, new_project, new_repository_id, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]

# (action, result) -> the actor kind that writes it.
REQUIRED = {
    ("release_accepted", "accepted"): "event",
    ("build_attempt_started", "succeeded"): "system",
    ("build_attempt_finished", "succeeded"): "system",
    ("deploy", "accepted"): "agent",
    ("deploy", "succeeded"): "system",
    ("promote", "accepted"): "user",
    ("promote", "succeeded"): "system",
    ("suspend_hosting", "succeeded"): "system",
    ("resume_hosting", "succeeded"): "system",
}
OPTIONAL = {
    ("suspend_hosting", "accepted"): "event",
    ("resume_hosting", "accepted"): "event",
    ("usage_event_published", "succeeded"): "system",
}


def _kinds(lines: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    return Counter((e["action"], e["result"]) for e in lines)


async def test_a_full_cycle_writes_each_audit_action_exactly_once_and_no_token() -> None:
    stack = Stack()
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-audit-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "audit")
    release = await stack.release_of(await stack.publish(project, repository_id, sha))
    built = await stack.wait_release(project, release, {"built", "failed"}, 300)
    assert built["status"] == "built", built

    async with stack.mcp() as mcp:
        deploy = await mcp.call(
            "deploy_release",
            projectId=str(project),
            releaseId=str(release),
            idempotency_key="audit-deploy",
        )
    assert not deploy.is_error, deploy.body
    assert (await stack.wait_command(project, deploy.body["commandId"], 300))[
        "status"
    ] == "succeeded"
    promote = await stack.post(project, release, "promote", "audit-promote")
    assert promote.status_code == 202, promote.body
    assert (await stack.wait_command(project, promote.command_id, 300))["status"] == "succeeded"

    await stack.simulate("entitlement-ended", "--project", str(project))
    await until(
        "the offline page",
        lambda: stack.visit(project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )
    await stack.simulate("entitlement-restored", "--project", str(project))
    await until(
        "the site serving again",
        lambda: stack.visit(project),
        lambda r: r[0] == 200 and "sample-site version: audit" in r[1],
        120,
    )

    lines = await until(
        "every required audit line in Loki",
        lambda: stack.audit(project),
        lambda found: set(REQUIRED) <= set(_kinds(found)),
        180,
        5,
    )
    counts = _kinds(lines)
    assert set(counts) <= set(REQUIRED) | set(OPTIONAL), sorted(
        set(counts) - set(REQUIRED) - set(OPTIONAL)
    )
    assert {k: n for k, n in counts.items() if n != 1} == {}, "an audit action was written twice"
    expected_kind = REQUIRED | OPTIONAL
    for line in lines:
        key = (line["action"], line["result"])
        assert line["actor_kind"] == expected_kind[key], (key, line["actor_kind"])
        assert line["project_id"] == str(project)
        assert line.get("failure_code") in (None, ""), line
    # The commands' lines name their command, the build lines name the release.
    by_key = {(e["action"], e["result"]): e for e in lines}
    assert by_key[("deploy", "accepted")]["command_id"] == deploy.body["commandId"]
    assert by_key[("deploy", "succeeded")]["command_id"] == deploy.body["commandId"]
    assert by_key[("promote", "accepted")]["command_id"] == promote.command_id
    assert by_key[("promote", "succeeded")]["command_id"] == promote.command_id
    assert by_key[("build_attempt_finished", "succeeded")]["release_id"] == str(release)

    raw = json.dumps(lines)
    for subject in ("prototype-operator", "prototype-tenant", "prototype-reader"):
        assert stack.token_for(subject) not in raw
    assert "Bearer" not in raw and "password" not in raw.lower()
