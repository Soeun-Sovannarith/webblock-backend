"""The reconciler with two workers (task H5b; decision 0014 section 21 rulings 8 and 9).

Both workers run a reconciler over the same projects. Each re-applies from SQL, at the project's
last command number, through the same field manager and never forced, so two of them can only
write the same intent. A live site's HTTPRoute and its live Service are deleted by hand: both come
back, then the route is watched for three reconcile intervals:

- it is not rewritten again and again (its ``metadata.generation`` and its spec stay the same:
  no fight);
- its fence never goes down and equals the project's last command number;
- only the worker's field manager applies it.

Needs ``make proto-up proto-run-replicas``.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from tests.e2e._stack import TIMINGS, Stack
from tests.e2e.faults._recovery_support import Site, eventually, kubectl, namespace
from tests.e2e.replicas._replicas import (
    WORKERS,
    Logs,
    fence_of,
    require_replicas,
    single_writer,
    site_object,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(900)]

RECONCILE_INTERVAL = 30  # the worker's default RECONCILE_INTERVAL; the run script sets none


@pytest.fixture(scope="module")
def stack() -> Stack:
    stack = Stack()
    require_replicas(stack)
    return stack


def _exists(site: Site, kind: str, name: str) -> bool:
    out = kubectl("-n", namespace(site.id), "get", kind, name, "--ignore-not-found", "-o", "name")
    return bool(out.strip())


async def test_two_reconcilers_restore_a_route_without_fighting(stack: Stack) -> None:
    site = Site(stack)
    live = await site.live("v1")
    rows = await stack.query(
        "SELECT last_command_number FROM provisioning.project_hosting WHERE project_id = %s",
        site.id,
    )
    last = int(rows[0][0])
    logs = Logs(WORKERS)

    kubectl("-n", namespace(site.id), "delete", "httproute", f"route-{site.id}", "--wait=true")
    kubectl("-n", namespace(site.id), "delete", "service", f"rel-{live}", "--wait=true")
    for kind, name in (("httproute", f"route-{site.id}"), ("service", f"rel-{live}")):
        await eventually(
            f"{kind} {name} restored",
            lambda k=kind, n=name: asyncio.to_thread(_exists, site, k, n),
            bool,
            RECONCILE_INTERVAL * 3,
        )

    first = single_writer(site.id, "httproute", f"route-{site.id}", last)
    samples = []
    for _ in range(RECONCILE_INTERVAL * 3 // 5):
        route = site_object(site.id, "httproute", f"route-{site.id}")
        samples.append((route["metadata"]["generation"], fence_of(route), route["spec"]))
        await asyncio.sleep(5)
    assert all(s == samples[0] for s in samples), [(g, f) for g, f, _ in samples]
    assert samples[0][0] == first["metadata"]["generation"]
    assert samples[0][1] == last
    single_writer(site.id, "service", f"rel-{live}", last)
    status, _ = await stack.visit_when_available(site.id)
    assert status == 200
    assert await site.answering() == str(live)
    # Neither reconciler saw the other's write as foreign, a fence ahead, or a refusal; which of
    # them re-applied is recorded (both may, with the same intent).
    project = str(site.id)
    reapplied: dict[str, list[str]] = {}
    for worker in WORKERS:
        mine = [str(e.get("msg")) for e in logs.entries(worker) if project in str(e.get("msg"))]
        fights = [
            m
            for m in mine
            if "foreign field managers" in m
            or "command number above" in m
            or "not re-applied" in m
            or "reconcile of project" in m
        ]
        assert fights == [], (worker, fights)
        reapplied[worker] = [m for m in mine if "re-applied at command number" in m]
    assert any(reapplied.values()), reapplied
    assert all(m.endswith(f"at command number {last}") for ms in reapplied.values() for m in ms)
    TIMINGS.mkdir(parents=True, exist_ok=True)
    (TIMINGS / "h5b-reconciler.json").write_text(json.dumps(reapplied, indent=2))
