"""Per-project serialization across two APIs (task H5b; TR-39, one command in flight, decision
0004 sections 4 and 7).

Two API processes share nothing but PostgreSQL, so "one command in flight per project" and
"same key, same command" must hold through the acceptance transaction (the hosting row's lock, the
partial unique index ``commands_one_in_flight`` and the idempotency index), never through process
memory:

- Eight deploy requests for one release with eight different keys, half through each API, sent at
  once: exactly one is accepted (202), the seven others are 409 ``operation_in_progress``, and one
  command exists. While it runs, the other API refuses a new command as well.
- Six promote requests with one key, three through each API, sent at once: all are 202 with the
  same command, and one command holds the key. Then the site is live, and the second API's MCP
  endpoint reads the same deployment.

Needs ``make proto-up proto-run-replicas``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from tests.e2e._stack import Command, Stack
from tests.e2e.faults._recovery_support import Site
from tests.e2e.replicas._replicas import require_replicas

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


async def _burst(
    stack: Stack, site: Site, release: uuid.UUID, action: str, keys: list[str]
) -> list[Command]:
    """One request per key, alternating between the two APIs, all started together."""
    start = asyncio.Event()

    async def one(index: int, key: str) -> Command:
        await start.wait()
        return await stack.post(site.id, release, action, key, api=stack.apis[index % 2])

    tasks = [asyncio.create_task(one(i, k)) for i, k in enumerate(keys)]
    await asyncio.sleep(0)
    start.set()
    return list(await asyncio.gather(*tasks))


async def _commands(stack: Stack, site: Site, kind: str) -> list[tuple[str, str]]:
    rows = await stack.query(
        "SELECT command_id::text, idempotency_key FROM provisioning.commands "
        "WHERE project_id = %s AND kind::text = %s",
        site.id,
        kind,
    )
    return [(str(c), str(k)) for c, k in rows]


async def test_one_command_in_flight_and_one_command_per_key_across_two_apis(
    stack: Stack,
) -> None:
    site = Site(stack)
    release = await site.build("v1")

    results = await _burst(stack, site, release, "deploy", [uuid.uuid4().hex for _ in range(8)])
    accepted = [r for r in results if r.status_code == 202]
    refused = [r for r in results if r.status_code != 202]
    assert len(accepted) == 1, [(r.status_code, r.body) for r in results]
    assert all(
        r.status_code == 409 and r.body.get("code") == "COMMAND_IN_PROGRESS" for r in refused
    ), [(r.status_code, r.body) for r in refused]
    deploys = await _commands(stack, site, "deploy")
    assert [c for c, _ in deploys] == [accepted[0].command_id], deploys

    # While it runs, a new command through either API is refused the same way.
    for api in stack.apis:
        late = await stack.post(site.id, release, "deploy", uuid.uuid4().hex, api=api)
        if (await stack.command(site.id, accepted[0].command_id))["status"] in {
            "accepted",
            "running",
        }:
            assert late.status_code == 409, (api, late.status_code, late.body)
            assert late.body.get("code") == "COMMAND_IN_PROGRESS", late.body
    deployed = await stack.wait_command(site.id, accepted[0].command_id, 300)
    assert deployed["status"] == "succeeded", deployed

    key = uuid.uuid4().hex
    same = await _burst(stack, site, release, "promote", [key] * 6)
    assert all(r.status_code == 202 for r in same), [(r.status_code, r.body) for r in same]
    assert len({r.command_id for r in same}) == 1, [r.command_id for r in same]
    promotes = await _commands(stack, site, "promote")
    assert promotes == [(same[0].command_id, key)], promotes
    promoted = await stack.wait_command(site.id, same[0].command_id, 300)
    assert promoted["status"] == "succeeded", promoted
    assert await site.answering() == str(release)

    # The second API's MCP endpoint serves the same records (its own host allow-list).
    async with stack.mcp(api=stack.apis[1]) as mcp:
        result = await mcp.call("get_deployment", projectId=str(site.id))
    assert not result.is_error, result.body
    assert result.body["liveReleaseId"] == str(release), result.body
