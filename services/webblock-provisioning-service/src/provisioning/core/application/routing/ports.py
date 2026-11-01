"""What the routing jobs need from our database: the route command store, its outcome decider and
the workload cleanup (cut from routing.py, decision 0025 CQ6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from provisioning.core.application.jobs.ports import Fence
from provisioning.core.domain import Outcome, OutcomeInput

type OutcomeDecider = Callable[[OutcomeInput], Outcome]
"""A domain outcome function bound to its failure and time, called by the store under its
locks with the rows it loaded."""


class RouteCommandStore(Protocol):
    """The fenced reads and writes of a command that moves the route (decision 0004 section 7),
    connected as ``provisioning_worker``. Both methods lock in the order project_hosting,
    command_dispatch, commands, releases, and check the three conditions of every worker write:
    ``last_command_number`` equals the fence's number (``StaleFence``), the dispatch row's
    ``claim_number`` equals the fence's (``ClaimLost``), and the command is ``running``
    (``IllegalTransition``)."""

    async def load_route_command(self, fence: Fence) -> OutcomeInput:
        """The hosting row, the command, the command's release (None for a hosting kind), the
        live release (None without one) and the project's promoting release (None without one),
        under the three checks. Writes nothing."""
        ...

    async def apply_route_outcome(self, fence: Fence, decide: OutcomeDecider) -> Outcome:
        """One transaction: the three checks, load the ``OutcomeInput`` (as
        ``load_route_command``), call ``decide``, and write exactly what the outcome changed, by
        row ID, the superseded release first (one live release per project); an operator
        rollback's ``Outcome.release`` may be the promoting release, not the command's own:
        ``releases`` (status, failure_code, failure_summary, promoted_at,
        updated_at), ``project_hosting`` (live_release_id, routing_status, hosting_status,
        updated_at), ``commands`` (status, failure_code, updated_at), and for a terminal command
        the dispatch row's finished_at and last_failure_code. ``decide`` raising rolls back and
        propagates. Returns the outcome written."""
        ...


class WorkloadCleanup(Protocol):
    """Deletes the workloads a committed outcome left behind: the superseded, rolled-back or
    failed release's Deployment and Service, never the live or the routed release (decision 0014
    section 21 ruling 8). Implemented by ``recovery.ReleaseCleanup``; best effort, it never raises
    for a cluster error."""

    async def after_outcome(self, fence: Fence, outcome: Outcome) -> None: ...
