"""The MCP tool set live (task W6-F; TR-3).

- Every one of the ten tools is called end to end by an agent session on one project: listings and
  reads while the release builds, the build tool on a built release (refused: nothing to build),
  deploy and promote of v1, then a second release stranded by a hand edit of the route (command
  number far ahead), its promote failing, the operator's rollback refused (it is ``ADMIN`` only)
  and the admin's rollback over MCP bringing the route back ``stable``.
- No tool bypasses a gate: a token without the action's scope gets 403 ``action_not_permitted``
  from every mutation tool and its REST twin, and nothing is written; a request without a token
  is 401.

Needs ``make proto-up proto-run`` with the development tokens of the run script (``tokens.json``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from tests.e2e._stack import ADMIN, McpCaller, Stack, ToolResult, until
from tests.e2e.faults._recovery_support import (
    Site,
    eventually,
    kubectl,
    namespace,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

TOOLS = {
    "deploy_release",
    "promote_release",
    "rollback_release",
    "get_release",
    "get_command",
    "get_deployment",
    "get_release_diagnostics",
    "build_release",
    "list_releases",
    "list_commands",
}
MUTATIONS = ("deploy_release", "promote_release", "rollback_release", "build_release")


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


class Agent:
    """An MCP session that remembers which tools it called."""

    def __init__(self, mcp: McpCaller, project: uuid.UUID) -> None:
        self.mcp = mcp
        self.project = project
        self.called: set[str] = set()

    async def call(self, tool: str, **arguments: Any) -> ToolResult:
        self.called.add(tool)
        return await self.mcp.call(tool, projectId=str(self.project), **arguments)

    async def ok(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = await self.call(tool, **arguments)
        assert not result.is_error, (tool, result.body)
        return result.body

    async def accept(self, tool: str, release: uuid.UUID) -> str:
        """A command tool; returns the accepted command's ID."""
        accepted = await self.ok(
            tool, releaseId=str(release), idempotencyKey=f"mcp-{uuid.uuid4().hex}"
        )
        return str(accepted["commandId"])

    async def run(self, tool: str, release: uuid.UUID) -> dict[str, Any]:
        """A command tool, then ``get_command`` until the command has finished."""
        return await self.wait(await self.accept(tool, release))

    async def wait(self, command_id: str) -> dict[str, Any]:
        """``get_command`` until the command has finished."""

        async def status() -> dict[str, Any]:
            return await self.ok("get_command", commandId=command_id)

        return await until(
            f"command {command_id} finished",
            status,
            lambda b: b["status"] in {"succeeded", "failed", "skipped"},
            300,
        )


async def test_every_tool_is_called_end_to_end(stack: Stack) -> None:
    site = Site(stack)
    sha = await stack.commit(site.name, "site", "mcp-v1")
    request = await stack.publish(site.id, site.repository_id, sha)
    v1 = await stack.release_of(request)
    async with stack.mcp() as mcp:
        assert set(await mcp.tool_names()) == TOOLS
        agent = Agent(mcp, site.id)

        listed = await agent.ok("list_releases", limit=10)
        assert [i["releaseId"] for i in listed["items"]] == [str(v1)]
        await until(
            "v1 built",
            lambda: agent.ok("get_release", releaseId=str(v1)),
            lambda b: b["status"] in {"built", "failed"},
            300,
        )
        assert (await agent.ok("get_release", releaseId=str(v1)))["status"] == "built"

        # Nothing to build: the release is built, the refusal is a conflict, not a start.
        nothing = await agent.call("build_release", releaseId=str(v1))
        assert nothing.is_error and nothing.body["code"] == "RELEASE_NOT_BUILDABLE", nothing.body

        assert (await agent.run("deploy_release", v1))["status"] == "succeeded"
        assert (await agent.run("promote_release", v1))["status"] == "succeeded"
        deployment = await agent.ok("get_deployment")
        assert (deployment["liveReleaseId"], deployment["routingStatus"]) == (str(v1), "stable")
        diagnostics = await agent.ok("get_release_diagnostics", releaseId=str(v1))
        assert diagnostics["release"]["status"] == "live"
        commands = await agent.ok("list_commands", limit=10)
        assert [(c["kind"], c["status"]) for c in commands["items"]] == [
            ("promote", "succeeded"),
            ("deploy", "succeeded"),
        ]
        await eventually(f"{v1} answering", site.answering, lambda n: n == str(v1), 60)

        # v2, stranded: the route's command number is ahead, so the promote cannot move it.
        v2 = await site.build("mcp-v2")
        assert (await agent.run("deploy_release", v2))["status"] == "succeeded"
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
        failed = await agent.run("promote_release", v2)
        assert failed["status"] == "failed", failed
        assert (await agent.ok("get_deployment"))["routingStatus"] == "uncertain"

        # FR-50: rollback is ADMIN only. The operator's session is refused; the admin's session
        # rolls back (an ADMIN acts on any project), and the operator's reads the command, since
        # the access file gives the admin no project for the other reads.
        refused = await agent.call(
            "rollback_release", releaseId=str(v2), idempotencyKey=f"mcp-{uuid.uuid4().hex}"
        )
        assert refused.is_error and refused.body["code"] == "ACCESS_DENIED", refused.body
        async with stack.mcp(ADMIN) as admin_mcp:
            command_id = await Agent(admin_mcp, site.id).accept("rollback_release", v2)
        rolled = await agent.wait(command_id)
        assert rolled["status"] == "succeeded", rolled
        deployment = await agent.ok("get_deployment")
        assert (deployment["routingStatus"], deployment["liveReleaseId"]) == ("stable", str(v1))
        await eventually(f"{v1} answering again", site.answering, lambda n: n == str(v1), 60)

        assert agent.called == TOOLS, sorted(TOOLS - agent.called)


async def test_a_token_without_the_scope_is_refused_by_every_tool_and_route(
    stack: Stack,
) -> None:
    project, release = uuid.uuid7(), uuid.uuid7()
    key = {"idempotencyKey": "scope-probe"}
    async with stack.mcp("prototype-reader") as mcp:
        for tool in MUTATIONS:
            args = {"projectId": str(project), "releaseId": str(release)}
            if tool != "build_release":
                args |= key
            result = await mcp.call(tool, **args)
            assert result.is_error, tool
            assert result.body["code"] == "ACCESS_DENIED", (tool, result.body)
        # Reads need their scope as well.
        read = await mcp.call("get_deployment", projectId=str(project))
        assert read.is_error and read.body["code"] == "ACCESS_DENIED", read.body
        # Decision 0018: the tool result is the Problem, without instance.
        assert read.body["type"] == "urn:webblock:problem:access-denied", read.body
        assert read.body["status"] == 403, read.body
        assert "instance" not in read.body, read.body

    reader = stack.auth_as("prototype-reader")
    async with stack.http() as http:
        for action in ("deploy", "promote", "rollback", "build"):
            response = await http.post(
                f"{stack.api}/v1/projects/{project}/releases/{release}/{action}",
                headers={**reader, "Idempotency-Key": "scope-probe"},
            )
            assert response.status_code == 403, (action, response.status_code)
            assert response.json()["code"] == "ACCESS_DENIED"
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["instance"] == (
                f"/v1/projects/{project}/releases/{release}/{action}"
            )
        # No token at all: the endpoint itself refuses.
        anonymous = await http.post(
            f"{stack.api}/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        )
        assert anonymous.status_code == 401, anonymous.status_code
        # Accept names application/json and text/event-stream only, so the amended rule
        # (problem_media_type) answers the same Problem as application/json.
        assert anonymous.headers["content-type"] == "application/json"
        assert anonymous.headers["www-authenticate"] == "Bearer"
        assert anonymous.json()["instance"] == "/mcp"
    # Refused before anything was written.
    commands = await stack.query(
        "SELECT count(*) FROM provisioning.commands WHERE project_id = %s", project
    )
    releases = await stack.query(
        "SELECT count(*) FROM provisioning.releases WHERE project_id = %s", project
    )
    assert (commands, releases) == ([(0,)], [(0,)])
