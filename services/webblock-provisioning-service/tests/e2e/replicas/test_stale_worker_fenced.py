"""A stale worker is fenced (task H5b; decisions 0005 section 4 and 0004 section 7; the
"stale command number, lost claim" row of ``.claude/docs/architecture/acceptance-scenarios.md``).

The lease settings are the prototype's own (WORKER_LEASE_TTL=15s, WORKER_HEARTBEAT_INTERVAL=3s;
recorded in .claude/docs/runbooks/replicas.md). The worker that claimed a command is stopped with
SIGSTOP while it holds the command, past its lease, until the other worker has taken the command
over (a higher claim number, its own WORKER_ID). Then it is resumed with SIGCONT.

1. A deploy (the ``slow-start`` sample site, ready about 20 s after its container starts): the
   frozen worker is resumed while the command is still the same command, so only the claim number
   tells the two apart. Every write it attempts must be refused by the claim (lease) check: its log
   for the command after the resume holds refusals only (``claim lost during execution``, or a
   write ``fenced off`` with ``ClaimLost``/``StaleFence``), at least one, and never an outcome, a
   transient failure or a release of the claim.
2. A promote, resumed after the command ended and a newer command was accepted: its writes must be
   refused by the fence (the project's command number moved on) or the claim.

In both: the command ends exactly once (one terminal audit event, by the worker that holds the last
claim, and no later claim), the site's objects have one field manager and the fence of the command
that wrote them, the route's fence never goes down while sampled, and the site serves what the
records say.

Needs ``make proto-up proto-run-replicas``. Whatever happens, the frozen worker is resumed.
"""

from __future__ import annotations

import asyncio
import re
import signal
import uuid
from typing import Any

import pytest
from tests.e2e._stack import Stack, until
from tests.e2e.faults._recovery_support import Site, kubectl, namespace
from tests.e2e.replicas._replicas import (
    WORKER_IDS,
    WORKER_OF_ID,
    WORKERS,
    Logs,
    dispatch_row,
    fence_of,
    require_replicas,
    signal_process,
    single_writer,
    site_object,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(1500)]

# What a fenced stale worker may log about the command after it resumes.
REFUSALS = (
    re.compile(r"^claim lost during execution; writing nothing more \("),
    re.compile(r"^claim lost during the abandon hook; writing nothing more \("),
    re.compile(
        r"^executor's write was fenced off; writing nothing more "
        r"\(.* error=(ClaimLost|StaleFence)\)$"
    ),
    re.compile(r"^command failure not applied \(.* error=(ClaimLost|StaleFence)\)$"),
    re.compile(r"^claim release refused: the claim was taken over; writing nothing more \("),
)
# Not writes: the resumed worker's broker channel died while it was frozen, so acknowledging its
# old delivery fails and the broker redelivers it (the redelivery finds a finished command or a
# live claim and is acknowledged without executing).
NOT_WRITES = (
    re.compile(r"^settling a delivery failed; the broker redelivers it \(.* action=ack "),
)
# A cluster call that was in flight when the worker froze times out when it resumes; the worker
# then tries to release its claim, which the claim check refuses ("claim release refused"). A
# transient failure caused by a store refusal (IllegalTransition) is not one of these.
TRANSIENT = re.compile(
    r"^transient execution failure; releasing the claim \(.* error=(?!IllegalTransition\b)\w+ "
)
RELEASE_REFUSED = re.compile(r"^claim release refused: ")


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


def _applied(site: Site, release: uuid.UUID) -> bool:
    out = kubectl(
        "-n",
        namespace(site.id),
        "get",
        "deployment",
        f"rel-{release}",
        "--ignore-not-found",
        "-o",
        "name",
    )
    return bool(out.strip())


def _other(worker: str) -> str:
    return next(w for w in WORKERS if w != worker)


async def _holder(stack: Stack, command_id: str) -> str:
    claimed = await until(
        f"command {command_id} claimed",
        lambda: dispatch_row(stack, command_id),
        lambda d: d["by"] is not None and d["claim_number"] >= 1,
        120,
        0.05,
    )
    return WORKER_OF_ID[str(claimed["by"])]


async def _taken_over(stack: Stack, command_id: str, by: str, after: int) -> dict[str, Any]:
    return await until(
        f"command {command_id} taken over by {by}",
        lambda: dispatch_row(stack, command_id),
        lambda d: d["claim_number"] > after and d["by"] == WORKER_IDS[by],
        180,
        0.2,
    )


async def _refused_only(after: Logs, worker: str, command_id: str) -> list[str]:
    """Waits until the resumed worker logged a refusal for the command; every line it logged
    about the command since the resume must be one, or a failed acknowledgement (not a write)."""
    lines = await until(
        f"{worker} logging a refusal for {command_id}",
        lambda: asyncio.sleep(0, after.about(worker, command_id)),
        lambda found: any(p.search(m) for m in found for p in REFUSALS),
        60,
        0.5,
    )
    others = [m for m in lines if not any(p.search(m) for p in (*REFUSALS, *NOT_WRITES, TRANSIENT))]
    assert others == [], (worker, others)
    if any(TRANSIENT.search(m) for m in lines):
        assert any(RELEASE_REFUSED.search(m) for m in lines), (worker, lines)
    return lines


def _ended_once(logs: Logs, command_id: str, by: str) -> None:
    ended = logs.ended(WORKERS, command_id)
    assert len(ended) == 1, ended
    assert ended[0][0] == by, ended


async def test_a_resumed_worker_cannot_write_for_a_command_another_worker_took_over(
    stack: Stack,
) -> None:
    site = Site(stack)
    v1 = await site.live("v1")
    route_fence = fence_of(site_object(site.id, "httproute", f"route-{site.id}"))
    v2 = await site.build("v2", variant="slow-start")
    logs = Logs(WORKERS)

    command_id = await site.accept(v2, "deploy")
    first = await _holder(stack, command_id)
    second = _other(first)
    # Freeze in the readiness poll: the candidate attempt runs and its Deployment is applied, so
    # the frozen worker is between polls (renew the claim, read the status, pause). Resumed, its
    # next step is a store call, which must be refused by the claim check (finding from the
    # planted defect p1: frozen inside the apply call, it timed out first and never reached one).
    await until(
        "the candidate attempt running",
        lambda: stack.release(site.id, v2),
        lambda b: any(
            s["stepName"] == "candidate" and s["status"] == "running" for s in b["steps"]
        ),
        60,
        0.05,
    )
    await until(
        f"deployment rel-{v2} applied",
        lambda: asyncio.to_thread(_applied, site, v2),
        bool,
        60,
        0.1,
    )
    await asyncio.sleep(1.5)
    signal_process(first, signal.SIGSTOP)
    try:
        taken = await _taken_over(stack, command_id, second, after=1)
        assert (await stack.command(site.id, command_id))["status"] in {"running", "succeeded"}
        after = Logs((first,))
    finally:
        signal_process(first, signal.SIGCONT)

    await _refused_only(after, first, command_id)
    done = await stack.wait_command(site.id, command_id, 300)
    assert done["status"] == "succeeded", done
    final = await dispatch_row(stack, command_id)
    assert (final["claim_number"], final["by"]) == (taken["claim_number"], WORKER_IDS[second])
    assert logs.claims(first, command_id) == {1}, logs.about(first, command_id)
    _ended_once(logs, command_id, second)

    number = int(
        (
            await stack.query(
                "SELECT command_number FROM provisioning.commands WHERE command_id = %s",
                uuid.UUID(command_id),
            )
        )[0][0]
    )
    single_writer(site.id, "deployment", f"rel-{v2}", number)
    single_writer(site.id, "service", f"rel-{v2}", number)
    route = single_writer(site.id, "httproute", f"route-{site.id}", route_fence)
    assert route["spec"]["rules"][0]["backendRefs"][0]["name"] == f"rel-{v1}"
    assert await site.answering() == str(v1)

    promoted = await site.command(v2, "promote")
    assert promoted["status"] == "succeeded", promoted
    assert await site.answering() == str(v2)


async def test_a_worker_resumed_after_a_newer_command_is_refused_by_the_fence(
    stack: Stack,
) -> None:
    site = Site(stack)
    await site.live("v1")
    v2 = await site.build("v2")
    assert (await site.command(v2, "deploy"))["status"] == "succeeded"
    v3 = await site.build("v3")
    logs = Logs(WORKERS)

    command_id = await site.accept(v2, "promote")
    first = await _holder(stack, command_id)
    second = _other(first)
    await site.wait_routing("changing", within=120)
    signal_process(first, signal.SIGSTOP)
    fences: list[int] = []
    try:
        await _taken_over(stack, command_id, second, after=1)
        promote = await stack.wait_command(site.id, command_id, 300)
        assert promote["status"] in {"succeeded", "failed"}, promote
        fences.append(fence_of(site_object(site.id, "httproute", f"route-{site.id}")))
        # A newer command moves the project's command number past the frozen worker's.
        newer = await site.accept(v3, "deploy")
        await until(
            "the newer command running",
            lambda: stack.command(site.id, newer),
            lambda b: b["status"] in {"running", "succeeded", "failed"},
            120,
            0.2,
        )
        after = Logs((first,))
    finally:
        signal_process(first, signal.SIGCONT)

    await _refused_only(after, first, command_id)
    for _ in range(10):  # the route's fence never goes down while the stale worker runs again
        fences.append(fence_of(site_object(site.id, "httproute", f"route-{site.id}")))
        await asyncio.sleep(1)
    assert fences == sorted(fences), fences
    assert logs.claims(first, command_id) == {1}, logs.about(first, command_id)
    _ended_once(logs, command_id, second)
    assert (await stack.wait_command(site.id, newer, 300))["status"] == "succeeded"

    deployment = await stack.deployment(site.id)
    assert deployment["routingStatus"] == "stable", deployment
    route = site_object(site.id, "httproute", f"route-{site.id}")
    backend = route["spec"]["rules"][0]["backendRefs"][0]["name"]
    assert backend == f"rel-{deployment['liveReleaseId']}", (backend, deployment)
    assert await site.answering() == deployment["liveReleaseId"]
