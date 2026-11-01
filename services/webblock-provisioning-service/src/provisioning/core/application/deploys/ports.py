"""What the deploy jobs need from our database: the deploy facts, the candidate attempt rows and
the deploy store (cut from deploy.py, decision 0025 CQ6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from provisioning.core.application.jobs.ports import Fence
from provisioning.core.domain import (
    CandidateFailure,
    CandidatePlanKind,
    Command,
    ProjectHosting,
    Release,
    ReleaseStep,
)


@dataclass(frozen=True, slots=True)
class DeployFacts:
    """The rows a deploy reads under its fence, written by nobody here: the hosting row, the
    command, the command's release, and the release's candidate step with the highest attempt
    (None when it has none)."""

    hosting: ProjectHosting
    command: Command
    release: Release
    last_candidate_step: ReleaseStep | None = None


@dataclass(frozen=True, slots=True)
class CandidateAttemptStart:
    """``step_id`` is the new step's ID if one is inserted; the step's deadline is the database
    clock plus ``timeout``."""

    step_id: UUID
    timeout: timedelta


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """The attempt a deploy runs: STARTED or RESUMED with its running ``step``, or EXHAUSTED (the
    command and the release were failed in the same write, ``step`` is None)."""

    kind: CandidatePlanKind
    step: ReleaseStep | None = None


class DeployStore(Protocol):
    """The fenced reads and writes of a deploy (decision 0004 section 7), connected as
    ``provisioning_worker``. Every method makes the three checks of ``Fence`` in its lock order
    (``StaleFence``, ``ClaimLost``, ``IllegalTransition`` for a command that is not running) and
    writes nothing when one fails. Times are the database clock. Driver errors are mapped as
    ``stores`` says (``Contention``, ``CorruptRow``, ``from None``)."""

    async def load_deploy_facts(self, fence: Fence) -> DeployFacts:
        """Read the rows under the three checks and write nothing. The release is the command's
        release, whatever its status: the use case decides whether it is deployable."""
        ...

    async def renew_claim(self, fence: Fence, ttl: timedelta) -> None:
        """Make the three checks and extend the claim to the database clock plus ``ttl``,
        conditional on the claim number. Called before every poll and every cluster write."""
        ...

    async def begin_candidate_attempt(
        self, fence: Fence, start: CandidateAttemptStart
    ) -> CandidateAttempt:
        """In one transaction, apply ``plan_candidate_attempt`` to the release and its last
        candidate step: write the leftover attempt it ends; then for START insert the new step as
        pending and start it (``start_candidate_attempt``, deadline = now + ``start.timeout``); for
        RESUME write nothing more; for EXHAUSTED write ``exhaust_candidate_attempts``."""
        ...

    async def return_replaced_candidates(self, fence: Fence) -> tuple[UUID, ...]:
        """Move every other ``candidate_ready`` release of the project back to built
        (``return_replaced_candidate``) and return their IDs. A second call returns none."""
        ...

    async def end_candidate_attempt(
        self, fence: Fence, step_id: UUID, failure_code: str | None
    ) -> CandidateFailure | None:
        """End the running attempt ``step_id`` with ``end_candidate_attempt`` and write the step
        and the outcome it returns, in one transaction. None on success; otherwise the
        classification, whose effect says what was written."""
        ...


class Pauser(Protocol):
    """Waits between polls; cancellable."""

    async def pause(self, delay: timedelta) -> None: ...
