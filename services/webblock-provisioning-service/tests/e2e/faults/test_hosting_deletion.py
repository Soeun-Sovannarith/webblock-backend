"""Project deletion live (decision 0014 section 21 answer 4; task W6-E).

A live site; Project's "project deleted" event takes it offline through a ``suspend_hosting``
command with the fixed key ``project-deleted``; a repeat of the event is the same command; a later
"entitlement restored" ends ``skipped`` with ``hosting_already_online`` and the site stays
offline: the resume removes only its own reason (``billing``), which the site does not hold, and
the reason ``project_deleted`` keeps it offline (decision 0021 D5). The live release keeps its
image (nothing is deleted). Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from tests.e2e._stack import Stack, new_project, new_repository_id, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1200)]


async def live_site(stack: Stack, name_prefix: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A new project serving v1 of the sample site: (project, its live release)."""
    project, repository_id = new_project(), new_repository_id()
    name = f"{name_prefix}-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "v1")
    release = await stack.release_of(await stack.publish(project, repository_id, sha))
    built = await stack.wait_release(project, release, {"built", "failed"}, 300)
    assert built["status"] == "built", built
    for action in ("deploy", "promote"):
        accepted = await stack.post(project, release, action, f"{action}-{release}")
        assert accepted.status_code == 202, accepted.body
        done = await stack.wait_command(project, accepted.command_id, 300)
        assert done["status"] == "succeeded", done
    await until(
        "v1 serving",
        lambda: stack.visit(project),
        lambda r: r[0] == 200 and "sample-site version: v1" in r[1],
        60,
    )
    return project, release


async def hosting_commands(stack: Stack, project: uuid.UUID) -> list[tuple[Any, ...]]:
    return await stack.query(
        "SELECT idempotency_key, kind::text, status::text, failure_code"
        " FROM provisioning.commands WHERE project_id = %s"
        " AND kind IN ('suspend_hosting', 'resume_hosting') ORDER BY command_number",
        project,
    )


async def test_a_deleted_project_goes_offline_and_a_restore_never_resumes_it() -> None:
    stack = Stack()
    project, release = await live_site(stack, "e2e-deleted")

    assert await stack.simulate("project-deleted", "--project", str(project)) == "project-deleted"
    await until(
        "the offline page",
        lambda: stack.visit(project),
        lambda r: r[0] == 503 and "offline" in r[1].lower(),
        120,
    )
    await until(
        "the deletion suspend succeeded",
        lambda: hosting_commands(stack, project),
        lambda rows: rows == [("project-deleted", "suspend_hosting", "succeeded", None)],
        60,
    )
    # A repeat of the event is the same command.
    await stack.simulate("project-deleted", "--project", str(project))
    restore_key = await stack.simulate("entitlement-restored", "--project", str(project))
    rows = await until(
        "the resume after deletion finished",
        lambda: hosting_commands(stack, project),
        lambda rows: len(rows) == 2 and rows[1][2] in {"succeeded", "failed", "skipped"},
        120,
    )
    # 0021 D5: the resume's own reason (billing) is absent, so it reads hosting_already_online,
    # while project_deleted keeps the site offline (the assertions below).
    assert rows == [
        ("project-deleted", "suspend_hosting", "succeeded", None),
        (restore_key, "resume_hosting", "skipped", "hosting_already_online"),
    ]
    status, body = await stack.visit(project)
    assert status == 503 and "offline" in body.lower()
    deployment = await stack.deployment(project)
    assert (deployment["hostingStatus"], deployment["routingStatus"]) == ("offline", "stable")
    # Nothing is deleted: the live release keeps its status and its image.
    kept = await stack.release(project, release)
    assert kept["status"] == "live" and kept["containerImageDigest"].startswith("sha256:")
