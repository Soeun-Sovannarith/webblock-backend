"""The edge cases of decision 0014 section 4 on the running prototype (task G, part 3.7): a
duplicate "source published" event, idempotent retries and conflicts, busy refusal, a worker that
crashes during a deploy, a build worker killed while its Jenkins build runs (review finding L5),
and a command that was committed while no dispatcher could publish it.

Needs ``make proto-up proto-run``. Each test uses its own project.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from tests.e2e._stack import Stack, Timer, new_project, new_repository_id, until

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


class Project:
    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.id = new_project()
        self.repository_id = new_repository_id()
        self.name = f"e2e-{str(self.id)[-12:]}"
        stack.register(self.id, self.repository_id, self.name)

    async def built(self, variant: str = "site", version: str = "v1") -> uuid.UUID:
        sha = await self.stack.commit(self.name, variant, version)
        request = await self.stack.publish(self.id, self.repository_id, sha)
        release = await self.stack.release_of(request)
        body = await self.stack.wait_release(self.id, release, {"built", "failed"}, 300)
        assert body["status"] == "built", body
        return release


async def test_a_duplicate_source_published_event_creates_one_release(stack: Stack) -> None:
    project = Project(stack)
    sha = await stack.commit(project.name, "site", "dup")
    request = uuid.uuid7()
    await stack.publish(project.id, project.repository_id, sha, request)
    await stack.publish(project.id, project.repository_id, sha, request)
    release = await stack.release_of(request)
    await stack.wait_release(project.id, release, {"built"}, 300)
    await asyncio.sleep(3)  # let the inbox processor handle the redelivered event
    rows = await stack.query(
        "SELECT count(*) FROM provisioning.releases WHERE project_id = %s", project.id
    )
    assert rows == [(1,)]
    steps = await stack.query(
        "SELECT count(*) FROM provisioning.release_steps WHERE release_id = %s"
        " AND step_name = 'build'",
        release,
    )
    assert steps == [(1,)]


async def test_retries_conflicts_and_busy_refusal(stack: Stack) -> None:
    project = Project(stack)
    first = await project.built("site", "a")
    second = await project.built("site", "b")
    accepted = await stack.post(project.id, first, "deploy", "same-key")
    assert accepted.status_code == 202, accepted.body
    # The same key and the same request: the original command, not a second one.
    retried = await stack.post(project.id, first, "deploy", "same-key")
    assert retried.status_code == 202, retried.body
    assert retried.command_id == accepted.command_id
    # The same key with another request: a conflict.
    conflict = await stack.post(project.id, second, "deploy", "same-key")
    assert conflict.status_code == 422, conflict.body
    assert conflict.body["code"] == "IDEMPOTENCY_KEY_REUSED"
    # Another command while the first is in flight: busy.
    busy = await stack.post(project.id, second, "deploy", "other-key")
    assert busy.status_code == 409, busy.body
    assert busy.body["code"] == "COMMAND_IN_PROGRESS"
    done = await stack.wait_command(project.id, accepted.command_id, 300)
    assert done["status"] == "succeeded", done
    rows = await stack.query(
        "SELECT count(*) FROM provisioning.commands WHERE project_id = %s", project.id
    )
    assert rows == [(1,)]


async def test_a_worker_killed_during_a_deploy_is_resumed_without_a_second_step(
    stack: Stack,
) -> None:
    # The slow-start variant keeps the deploy in progress for about 20 seconds.
    project = Project(stack)
    release = await project.built("slow-start", "crash")
    timer = Timer("edge-worker-crash")
    accepted = await stack.post(project.id, release, "deploy", "crash-key")
    assert accepted.status_code == 202, accepted.body
    await until(
        "a running candidate step",
        lambda: stack.query(
            "SELECT status FROM provisioning.release_steps WHERE release_id = %s"
            " AND step_name = 'candidate'",
            release,
        ),
        lambda rows: rows == [("running",)],
        60,
        interval=0.2,
    )
    timer.mark("killed")
    await stack.process("kill-process", "worker")
    status = await stack.command(project.id, accepted.command_id)
    assert status["status"] == "running", status  # the crash happened mid-command
    await stack.process("start-process", "worker")
    timer.mark("restarted")
    done = await stack.wait_command(project.id, accepted.command_id, 300)
    timer.mark("finished")
    assert done["status"] == "succeeded", done
    steps = await stack.query(
        "SELECT attempt, status FROM provisioning.release_steps WHERE release_id = %s"
        " AND step_name = 'candidate'",
        release,
    )
    assert steps == [(1, "succeeded")]
    assert (await stack.release(project.id, release))["status"] == "candidate_ready"
    timer.save({"recovery_seconds": timer.between("restarted", "finished")})


async def test_a_build_worker_killed_during_a_build_picks_it_up_from_its_log_ref(
    stack: Stack,
) -> None:
    """Decision 0014 section 4 and section 17 item 5 (L5): SIGKILL the build worker while the
    build step is running and its Jenkins build is recorded in ``log_ref``; the restarted worker
    polls that build instead of starting another: one build step, one attempt, one Jenkins build,
    and the release built."""
    project = Project(stack)
    sha = await stack.commit(project.name, "site", "build-crash")
    request = await stack.publish(project.id, project.repository_id, sha)
    release = await stack.release_of(request)
    timer = Timer("edge-build-worker-crash")
    await until(
        "a running build step with its log_ref",
        lambda: stack.query(
            "SELECT status, log_ref IS NOT NULL FROM provisioning.release_steps"
            " WHERE release_id = %s AND step_name = 'build'",
            release,
        ),
        lambda rows: rows == [("running", True)],
        120,
        interval=0.2,
    )
    timer.mark("killed")
    await stack.process("kill-process", "build_worker")
    try:
        killed = await stack.release(project.id, release)
        assert killed["status"] == "building", killed  # the crash happened mid-build
    finally:
        await stack.process("start-process", "build_worker")
    timer.mark("restarted")
    body = await stack.wait_release(project.id, release, {"built", "failed"}, 300)
    timer.mark("built")
    assert body["status"] == "built", body
    assert body["gitCommitSha"] == sha
    steps = await stack.query(
        "SELECT attempt, status FROM provisioning.release_steps WHERE release_id = %s"
        " AND step_name = 'build'",
        release,
    )
    assert steps == [(1, "succeeded")]
    assert await stack.jenkins_builds(release) == 1
    timer.save({"recovery_seconds": timer.between("restarted", "built")})


async def test_a_command_committed_while_no_dispatcher_runs_is_dispatched_from_sql(
    stack: Stack,
) -> None:
    project = Project(stack)
    release = await project.built("site", "lost")
    # Every worker replica (task H5c): a second worker's dispatcher would publish the command.
    await stack.process_all("stop-process", "worker")
    try:
        accepted = await stack.post(project.id, release, "deploy", "lost-key")
        assert accepted.status_code == 202, accepted.body
        await asyncio.sleep(3)
        # Committed, never published: nothing ran it.
        assert (await stack.command(project.id, accepted.command_id))["status"] == "accepted"
    finally:
        await stack.process_all("start-process", "worker")
    done = await stack.wait_command(project.id, accepted.command_id, 300)
    assert done["status"] == "succeeded", done
