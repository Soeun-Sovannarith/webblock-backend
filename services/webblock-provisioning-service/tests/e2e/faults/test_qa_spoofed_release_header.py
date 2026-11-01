"""QA probe, adopted (task W6-F; QA branch ``d487207``): a generated site sets its own
``X-Webblock-Release`` header naming a made-up release (the ``qa-spoof`` fixture in
``tests/e2e/sites``). The Gateway must overwrite it: the promotion verifies the real release header
and visitors see the real one, once.

Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import pytest
from tests.e2e._stack import SITES, Stack, new_project, new_repository_id

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]

SPOOFED = "11111111-1111-4111-8111-111111111111"


async def test_the_sites_own_release_header_never_reaches_the_visitor() -> None:
    stack = Stack()
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-spoof-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit_dir(name, SITES / "qa-spoof", "spoof")
    release = await stack.release_of(await stack.publish(project, repository_id, sha))
    built = await stack.wait_release(project, release, {"built", "failed"}, 400)
    assert built["status"] == "built", built
    deploy = await stack.post(project, release, "deploy", "spoof-deploy")
    assert deploy.status_code == 202, deploy.body
    assert (await stack.wait_command(project, deploy.command_id, 300))["status"] == "succeeded"
    promote = await stack.post(project, release, "promote", "spoof-promote")
    assert promote.status_code == 202, promote.body
    done = await stack.wait_command(project, promote.command_id, 300)
    assert done["status"] == "succeeded", done

    status, names = await stack.visit_named(project)
    assert status == 200
    assert names == [str(release)], names  # one value, the real release; never the spoofed one
    assert SPOOFED not in names
