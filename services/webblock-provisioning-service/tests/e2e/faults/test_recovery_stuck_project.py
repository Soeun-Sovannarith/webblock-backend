"""Live: a promotion that passes its deadline while routing is ``changing`` ends ``stable`` or
``uncertain``, never stuck (task W6-B; decision 0014 section 21 ruling 9, the gap analysis's first
defect).

The route is held in ``changing`` on purpose, with no race: v2 is the sample-site variant
``preview-only`` (a test fixture), which answers ``/readyz`` only on its preview hostname. The
promotion records its intent first (routing ``changing`` from the start of try 1, before the
preview; decision 0027), its preview passes, it switches the route, and then the probe of
the site's own hostname fails until ``ROUTE_SWITCH_DEADLINE`` (20 s on the stack). So the kill
below lands inside try 1, before that try can fail and give the claim back for try 2: the command
ends through the deadline, with ``deadline_exceeded``, not with a try's code. Inside that
window the worker is killed; while no worker runs, the command's deadline passes (moved into the
past on the stack's own database, as fifteen minutes would); the worker is started again. Its
dispatcher publishes the command, its consumer claims it past the deadline and runs the routing
executor's abandon hook, which points the route back at the live release and records the outcome.
A new command is then accepted.

On a replica run every worker is killed and started again, and every worker's log is read (task
H5c): with one worker left running it would take the command over before the deadline moved.

Needs ``make proto-up proto-run`` (or ``make proto-run-replicas``).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from tests.e2e._stack import Stack
from tests.e2e.faults._recovery_support import (
    Site,
    as_superuser,
    audit_events,
    eventually,
    kill_workers,
    log_marks,
    logs_since,
    workloads,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]


async def test_a_promotion_stuck_while_changing_ends_stable_and_new_commands_are_accepted() -> None:
    """FR-21, FR-23: a promote whose worker died inside try 1 and whose deadline then passed ends
    deadline_exceeded with the route restored, never stuck."""
    stack = Stack()
    site = Site(stack)
    v1 = await site.live("v1")
    v2 = await site.build("v2", variant="preview-only")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    mark = log_marks()

    command_id = await site.accept(v2, "promote")
    await site.wait_routing("changing", within=180, interval=0.2)
    kill_workers()  # every worker dies while the switch can never be confirmed
    try:
        assert await site.routing() == "changing"
        moved = await as_superuser(
            "UPDATE provisioning_ops.command_dispatch "
            "SET deadline_at = clock_timestamp() - interval '1 second' "
            "WHERE command_id = %s AND finished_at IS NULL",
            uuid.UUID(command_id),
        )
        assert moved == 1
    finally:
        await stack.process_all("start-process", "worker")

    done = await stack.wait_command(site.id, command_id, 300)
    assert (done["status"], done.get("failureCode")) == ("failed", "deadline_exceeded"), done
    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "stable", deployment
    assert (await stack.release(site.id, v2))["status"] == "rolled_back"
    assert (await stack.release(site.id, v1))["status"] == "live"
    await eventually(f"release {v1} answering", site.answering, lambda name: name == str(v1))
    # The rolled-back candidate's workload is cleaned up after the verified restore (ruling 8).
    await eventually(
        "only the live workload", lambda: _workloads(site.id), lambda names: names == {f"rel-{v1}"}
    )
    lines = logs_since(mark)
    assert any("abandon hook ran" in line for line in lines), "the abandon hook did not run"
    assert not [e for e in audit_events(lines) if e.get("alert") is True]

    # Never stuck: Payment's next event becomes a command that runs.
    await stack.simulate("entitlement-ended", "--project", str(site.id))
    await eventually(
        "the site offline",
        lambda: stack.deployment(site.id),
        lambda body: body.get("hostingStatus") == "offline" and body["routingStatus"] == "stable",
        within=180,
    )
    await stack.simulate("entitlement-restored", "--project", str(site.id))
    await eventually(
        "the site online again",
        lambda: stack.deployment(site.id),
        lambda body: body.get("hostingStatus") == "online" and body["routingStatus"] == "stable",
        within=180,
    )


async def _workloads(project: uuid.UUID) -> set[str]:
    return await asyncio.to_thread(workloads, project)
