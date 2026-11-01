"""An agent's deploy and promote through the MCP endpoint with the SDK client (task G, part 3.6;
decision 0014 section 2): the same use cases as REST, the same bearer token."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from tests.e2e._stack import Stack, new_project, new_repository_id, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]


def _content(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    body: dict[str, Any] = (
        result.structured_content
        if result.structured_content is not None
        else json.loads(result.content[0].text)
    )
    return body


async def test_an_agent_deploys_and_promotes_through_mcp() -> None:
    stack = Stack()
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "mcp")
    release = await stack.release_of(await stack.publish(project, repository_id, sha))
    async with (
        httpx2.AsyncClient(headers=stack.auth(), timeout=30, trust_env=False) as http,
        Client(streamable_http_client(f"{stack.api}/mcp", http_client=http), mode="legacy") as sdk,
    ):

        async def get_release() -> dict[str, Any]:
            return _content(
                await sdk.call_tool(
                    "get_release", {"projectId": str(project), "releaseId": str(release)}
                )
            )

        await until("the release built", get_release, lambda b: b["status"] == "built", 300)
        for tool in ("deploy_release", "promote_release"):
            accepted = _content(
                await sdk.call_tool(
                    tool,
                    {
                        "projectId": str(project),
                        "releaseId": str(release),
                        "idempotencyKey": f"mcp-{tool}",
                    },
                )
            )

            async def get_command(command_id: str = accepted["commandId"]) -> dict[str, Any]:
                return _content(
                    await sdk.call_tool(
                        "get_command", {"projectId": str(project), "commandId": command_id}
                    )
                )

            done = await until(
                f"{tool} finished",
                get_command,
                lambda b: b["status"] in {"succeeded", "failed"},
                300,
            )
            assert done["status"] == "succeeded", done
        assert (await get_release())["status"] == "live"
    await until(
        "the Gateway serving the MCP release",
        lambda: stack.visit(project),
        lambda r: r[0] == 200 and "sample-site version: mcp" in r[1],
        60,
    )
