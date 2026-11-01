"""No duplicate work across two workers (task H5b; decision 0005 section 4, the claim).

Four projects are deployed at once, the requests split between the two APIs, and each command's
job message is published six more times at once, as an at-least-once broker may deliver it (a
dispatcher republish, a redelivery). Both workers consume the queue, so copies of one command reach
both at the same moment. The claim (dispatch row and command locked, claim number advanced
conditionally) must let exactly one execution happen. Then the four are promoted the same way.

For every command:

- it succeeded, and its dispatch row shows one claim and one execution attempt
  (``claim_number = 1``, ``execution_attempts = 1``): no second claim ever happened. The row keeps
  only the last owner (``claimed_by_worker``), so there is no lease owner history to read;
- the worker logs agree: exactly one worker logged lines under a claim of the command, all under
  claim 1, and exactly one audit event ended it;
- the site's objects (Deployment and Service ``rel-<release>``, HTTPRoute ``route-<project>``) are
  applied by the worker's field manager only, owned by no other manager, and carry the fence of
  the command that last wrote them.

Needs ``make proto-up proto-run-replicas``.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from tests.e2e._stack import TIMINGS, Stack
from tests.e2e.faults._recovery_support import Site
from tests.e2e.replicas._replicas import (
    WORKERS,
    Logs,
    command_row,
    dispatch_row,
    inject_command_jobs,
    require_replicas,
    single_writer,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1800)]

SITES = 4
COPIES = 6


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


async def _run_all(
    stack: Stack, sites: list[Site], releases: list[uuid.UUID], action: str, logs: Logs
) -> list[tuple[str, int]]:
    """``action`` for every site at once (alternating APIs), each job duplicated COPIES times at
    once; waits for every command and checks it ran once. Returns (command_id, number)."""
    accepted = await asyncio.gather(
        *(
            stack.post(site.id, release, action, uuid.uuid4().hex, api=stack.apis[i % 2])
            for i, (site, release) in enumerate(zip(sites, releases, strict=True))
        )
    )
    assert all(c.status_code == 202 for c in accepted), [c.body for c in accepted]
    rows = await asyncio.gather(*(command_row(stack, c.command_id) for c in accepted))
    await asyncio.gather(*(inject_command_jobs(stack, row, COPIES) for row in rows))
    done = await asyncio.gather(
        *(
            stack.wait_command(site.id, c.command_id, 400)
            for site, c in zip(sites, accepted, strict=True)
        )
    )
    assert all(d["status"] == "succeeded" for d in done), done
    evidence: list[tuple[str, int]] = []
    for c, row in zip(accepted, rows, strict=True):
        dispatch = await dispatch_row(stack, c.command_id)
        assert (dispatch["claim_number"], dispatch["attempts"]) == (1, 1), (action, c, dispatch)
        claims = {w: logs.claims(w, c.command_id) for w in WORKERS}
        holders = [w for w, found in claims.items() if found]
        assert len(holders) == 1 and claims[holders[0]] == {1}, (action, c.command_id, claims)
        ended = logs.ended(WORKERS, c.command_id)
        assert len(ended) == 1 and ended[0][0] == holders[0], (action, c.command_id, ended)
        evidence.append((c.command_id, row.command_number))
    return evidence


async def test_each_command_runs_on_exactly_one_worker_despite_duplicate_deliveries(
    stack: Stack,
) -> None:
    sites = [Site(stack) for _ in range(SITES)]
    releases = await asyncio.gather(*(site.build("v1") for site in sites))
    logs = Logs(WORKERS)

    deploys = await _run_all(stack, sites, list(releases), "deploy", logs)
    for site, release, (_, number) in zip(sites, releases, deploys, strict=True):
        single_writer(site.id, "deployment", f"rel-{release}", number)
        single_writer(site.id, "service", f"rel-{release}", number)

    promotes = await _run_all(stack, sites, list(releases), "promote", logs)
    for site, release, (_, number) in zip(sites, releases, promotes, strict=True):
        single_writer(site.id, "httproute", f"route-{site.id}", number)
        assert await site.answering() == str(release)

    per_worker = {
        w: sum(1 for cid, _ in deploys + promotes if logs.claims(w, cid)) for w in WORKERS
    }
    TIMINGS.mkdir(parents=True, exist_ok=True)
    (TIMINGS / "h5b-no-duplicate-work.json").write_text(
        json.dumps({"commands_per_worker": per_worker, "copies_per_command": COPIES}, indent=2)
    )
