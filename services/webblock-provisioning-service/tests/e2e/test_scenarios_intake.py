"""Project's "source published" event and Payment's hosting events at the edge, live (task W6-F;
TR-14, the repository check and the dead-letter queues of decision 0005 section 6).

- Repeat: a repeat of the event with another commit for the same generation request is counted and
  refused; no second release appears and the first release keeps its commit.
- Another repository: an event naming a repository that the registry does not confirm for the
  project is rejected in the inbox (``repository_mismatch``, or ``repository_not_registered`` for
  a project the registry does not know); no release, no build, and one refused
  ``release_accepted`` audit line in Loki.
- Dead letters: a malformed event is parked in the dead-letter queue of its own queue, creates
  nothing, and does not stop the consumer: a valid event published after it is accepted.

The operator CLI's re-drive of a parked inbox row is rehearsed by ``test_operator.py`` (task
W6-E); the CLI has no command for the broker's dead-letter queues, so none is tested here.

Needs ``make proto-up proto-run``. Each test uses its own project.
"""

from __future__ import annotations

import asyncio
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

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

SOURCE_DLQ = "provisioning.source_published.v1.dlq"
ENTITLEMENT_DLQ = "provisioning.hosting_entitlement_ended.v1.dlq"


@pytest.fixture(scope="module")
def stack() -> Stack:
    return Stack()


async def _inbox(stack: Stack, project: uuid.UUID, request: uuid.UUID) -> list[tuple[Any, ...]]:
    return await stack.admin_query(
        "SELECT status::text, outcome_code, release_id, mismatched_repeat_count, git_commit_sha"
        " FROM provisioning_ops.source_published_events"
        " WHERE project_id = %s AND generation_request_id = %s",
        project,
        request,
    )


async def _releases(stack: Stack, project: uuid.UUID) -> list[tuple[Any, ...]]:
    return await stack.query(
        "SELECT release_id, git_commit_sha FROM provisioning.releases WHERE project_id = %s",
        project,
    )


async def test_a_repeat_with_another_commit_is_refused_and_creates_no_second_release(
    stack: Stack,
) -> None:
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-pr02-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    first = await stack.commit(name, "site", "pr02-a")
    request = uuid.uuid7()
    await stack.publish(project, repository_id, first, request)
    release = await stack.release_of(request)
    # Another commit for the same generation request: the same event key, a different content.
    second = await stack.commit(name, "site", "pr02-b")
    assert second != first
    await stack.publish(project, repository_id, second, request)

    rows = await until(
        "the mismatched repeat counted",
        lambda: _inbox(stack, project, request),
        lambda r: len(r) == 1 and r[0][3] >= 1,
        90,
    )
    status, _outcome, row_release, repeats, row_sha = rows[0]
    assert status == "accepted" and row_release == release, rows
    assert repeats == 1, rows
    assert row_sha == first, "the event row must keep the first commit"
    releases = await _releases(stack, project)
    assert [(r[0], r[1]) for r in releases] == [(release, first)], releases
    body = await stack.release(project, release)
    assert body["gitCommitSha"] == first
    # The first commit still builds: the refused repeat disturbed nothing.
    built = await stack.wait_release(project, release, {"built", "failed"}, 300)
    assert built["status"] == "built", built
    assert await _releases(stack, project) == releases


async def test_an_event_naming_another_repository_is_rejected_with_no_release(
    stack: Stack,
) -> None:
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-pr05-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "pr05")
    request = uuid.uuid7()
    await stack.publish(project, repository_id + 1, sha, request)

    rows = await until(
        "the event rejected",
        lambda: _inbox(stack, project, request),
        lambda r: len(r) == 1 and r[0][0] in {"rejected", "accepted", "failed"},
        90,
    )
    assert rows[0][:3] == ("rejected", "repository_mismatch", None), rows
    assert await _releases(stack, project) == []

    # A project the registry does not know at all is rejected the same way.
    stranger = new_project()
    other_request = uuid.uuid7()
    await stack.publish(stranger, new_repository_id(), sha, other_request)
    rows = await until(
        "the stranger's event rejected",
        lambda: _inbox(stack, stranger, other_request),
        lambda r: len(r) == 1 and r[0][0] != "pending",
        90,
    )
    assert rows[0][:3] == ("rejected", "repository_not_registered", None), rows
    assert await _releases(stack, stranger) == []

    # One refused release_accepted audit line each, by the project's event, in Loki.
    def refused(lines: list[dict[str, object]]) -> list[tuple[object, object, object]]:
        return [
            (e["action"], e["result"], e.get("failure_code"))
            for e in lines
            if e["action"] == "release_accepted"
        ]

    lines = await until(
        "the audit line in Loki",
        lambda: stack.audit(project),
        lambda found: len(refused(found)) >= 1,
        120,
        5,
    )
    assert refused(lines) == [("release_accepted", "refused", "repository_mismatch")], lines
    assert {(e["actor_kind"], e["actor"]) for e in lines} == {("event", "project")}


async def _dead_letters(queue: str) -> int:
    def count() -> int:
        out = docker(
            "exec",
            "-u",
            "rabbitmq",
            "webblock-rabbitmq",
            "rabbitmqctl",
            "-q",
            "list_queues",
            "name",
            "messages",
        )
        for line in out.splitlines():
            name, _, messages = line.partition("\t")
            if name.strip() == queue:
                return int(messages)
        raise AssertionError(f"queue {queue} is not declared; the worker declares it at start")

    return await asyncio.to_thread(count)


async def test_a_malformed_event_is_parked_in_the_dead_letter_queue_and_stops_nothing(
    stack: Stack,
) -> None:
    source_before = await _dead_letters(SOURCE_DLQ)
    hosting_before = await _dead_letters(ENTITLEMENT_DLQ)
    await stack.publish_raw("project.events", "source_published.v1", b'{"not": "an event"')
    await stack.publish_raw("payment.events", "hosting_entitlement_ended.v1", b"\x00 not json")

    await until(
        "the source-published event in its dead-letter queue",
        lambda: _dead_letters(SOURCE_DLQ),
        lambda n: n == source_before + 1,
        60,
    )
    await until(
        "the hosting event in its dead-letter queue",
        lambda: _dead_letters(ENTITLEMENT_DLQ),
        lambda n: n == hosting_before + 1,
        60,
    )
    # Parked, not retried: the counts stay put, and the main queues are empty of them.
    assert await _dead_letters(SOURCE_DLQ) == source_before + 1
    assert await _dead_letters(ENTITLEMENT_DLQ) == hosting_before + 1

    # The consumers still run: a valid event after the bad one becomes a release.
    project, repository_id = new_project(), new_repository_id()
    name = f"e2e-dlq-{str(project)[-12:]}"
    stack.register(project, repository_id, name)
    sha = await stack.commit(name, "site", "dlq")
    release = await stack.release_of(await stack.publish(project, repository_id, sha))
    built = await stack.wait_release(project, release, {"built", "failed"}, 300)
    assert built["status"] == "built", built
