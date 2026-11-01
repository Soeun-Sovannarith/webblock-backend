"""Live QA-05 (task W6-B): a deploy refused inside its finish margin cleans up the candidate an
earlier claim left, instead of leaving its Deployment and a ``running`` step.

The candidate is the ``slow-start`` sample variant (nginx starts 20 s after its container), so
its deploy is still waiting for readiness when the worker is killed, after the Deployment
exists. On the stack's own database (test-only fault injection, see ``as_superuser``) the
command's deadline is then set inside ``DEPLOY_FINISH_MARGIN`` (30 s) but not passed, and the
dead worker's claim made claimable at once. The restarted worker claims the command before its
deadline, refuses it ``deadline_exceeded``, and deletes the candidate on the way: the abandon
hook (which runs only after the deadline) is not what cleans up.

Needs ``make proto-up proto-run``.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import (
    Site,
    as_superuser,
    eventually,
    kill_worker,
    log_mark,
    worker_log_since,
    workloads,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]


async def _workloads(project: uuid.UUID) -> set[str]:
    return await asyncio.to_thread(workloads, project)


async def test_qa05_a_deploy_refused_in_its_margin_removes_the_candidate_and_ends_its_step() -> (
    None
):
    stack = Stack()
    site = Site(stack)
    release = await site.build("v1", variant="slow-start")
    mark = log_mark()
    command_id = await site.accept(release, "deploy")

    await eventually(
        "the candidate's Deployment applied",
        lambda: _workloads(site.id),
        lambda names: f"rel-{release}" in names,
        within=180,
    )
    kill_worker()  # the attempt stays recorded as running
    try:
        moved = await as_superuser(
            "UPDATE provisioning_ops.command_dispatch "
            "SET deadline_at = clock_timestamp() + interval '25 seconds', "
            "claim_expires_at = clock_timestamp(), next_publish_at = clock_timestamp(), "
            "retry_after = NULL "
            "WHERE command_id = %s AND finished_at IS NULL",
            uuid.UUID(command_id),
        )
        assert moved == 1
    finally:
        await stack.process("start-process", "worker")

    done = await stack.wait_command(site.id, command_id, 120)
    assert (done["status"], done.get("failureCode")) == ("failed", "deadline_exceeded"), done
    rows = await stack.query_ops(
        "SELECT c.updated_at < d.deadline_at FROM provisioning.commands AS c "
        "JOIN provisioning_ops.command_dispatch AS d USING (command_id) WHERE c.command_id = %s",
        uuid.UUID(command_id),
    )
    assert rows == [(True,)], "the command was not refused before its deadline"
    steps = await stack.query(
        "SELECT status::text, failure_code FROM provisioning.release_steps "
        "WHERE release_id = %s AND step_name = 'candidate'",
        release,
    )
    assert steps and all(status != "running" for status, _ in steps), steps
    assert ("failed", "deadline_exceeded") in steps, steps
    await eventually(
        "the candidate's Deployment deleted",
        lambda: _workloads(site.id),
        lambda names: f"rel-{release}" not in names,
        within=60,
    )
    lines = await asyncio.to_thread(worker_log_since, mark)
    assert not any("abandon hook ran" in line and command_id in line for line in lines)
