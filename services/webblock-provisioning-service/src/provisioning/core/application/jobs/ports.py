"""Store ports of command acceptance, reads and the worker's fenced writes (decision 0004
sections 4, 6 and 7). Every implementation maps driver errors before raising:
lock timeouts, deadlocks and serialization failures become ``Contention``, a row that fails
validation on load ``CorruptRow``, and no driver exception leaves the adapter (``raise ... from
None``). Every query filters on the project ID.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from provisioning.core.domain import (
    Command,
    CommandRequest,
    CommandStatus,
    Failure,
    InvalidId,
    ProjectHosting,
    ProjectMismatch,
    Release,
    hosting_reason_of_key,
    new_command,
    next_command_number,
    validate_command_number,
)


@dataclass(frozen=True, slots=True)
class AcceptCommandParams:
    """The input of ``AcceptanceStore.accept_command``.

    ``request`` is normalized and validated. ``command_id`` is the fresh ID the command gets if one
    is inserted. ``requested_by_subject`` is the principal's subject, ``""`` exactly for the
    hosting kinds that come from an event (an admin's suspend or resume carries the admin's).
    ``now`` is the acceptance time; ``correlation_id`` (1 to 128 characters) and ``deadline_at``
    (now + COMMAND_DEADLINE) go into the dispatch row."""

    request: CommandRequest
    command_id: UUID
    requested_by_subject: str
    now: datetime
    correlation_id: str
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class AcceptanceFacts:
    """What the store loaded inside the acceptance transaction with the hosting row locked:
    ``by_key`` is the command holding the request's key in its key namespace, ``in_flight`` the
    project's command in flight, ``target`` the release the request names (None when missing or
    for a hosting kind), ``live`` the release ``hosting.live_release_id`` names."""

    hosting: ProjectHosting
    by_key: Command | None = None
    in_flight: Command | None = None
    target: Release | None = None
    live: Release | None = None


class AcceptanceDecision:
    """A replay of a stored command, or the command to insert at the next command number. Made
    only by ``replay_decision`` and ``insert_decision``."""

    __slots__ = ("_command", "_insert")

    def __init__(self, command: Command, insert: bool) -> None:
        self._command = command
        self._insert = insert

    @property
    def command(self) -> Command:
        return self._command

    @property
    def insert(self) -> bool:
        """True to insert the command, False to replay it."""
        return self._insert


def replay_decision(stored: Command) -> AcceptanceDecision:
    """Return ``stored`` unchanged, whatever its status, and write nothing."""
    return AcceptanceDecision(stored, insert=False)


def insert_decision(params: AcceptCommandParams, hosting: ProjectHosting) -> AcceptanceDecision:
    """The command to insert on the locked hosting row: the next command number
    (``CommandNumberOverflow`` at the maximum) and ``new_command`` with the request,
    ``params.command_id``, the requester (``""`` is none) and ``params.now``. A hosting row of
    another project is ``ProjectMismatch``."""
    if hosting.project_id != params.request.project_id:
        raise ProjectMismatch(
            f"hosting row of project {hosting.project_id} for a request of project "
            f"{params.request.project_id}"
        )
    command = new_command(
        command_id=params.command_id,
        request=params.request,
        requested_by_subject=params.requested_by_subject or None,
        command_number=next_command_number(hosting.last_command_number),
        now=params.now,
        hosting_reason=(
            hosting_reason_of_key(params.request.kind, params.request.idempotency_key)
            if params.request.kind.is_hosting()
            else None
        ),
    )
    return AcceptanceDecision(command, insert=True)


type AcceptanceDecider = Callable[[AcceptanceFacts], AcceptanceDecision]
"""The pure function the store calls with the facts it loaded under the lock; it raises a typed
refusal that the store raises after rolling back. It may be called more than once."""


@dataclass(frozen=True, slots=True)
class AcceptedCommand:
    """The stored command, and whether it was already there (a replay)."""

    command: Command
    replayed: bool


class AcceptanceStore(Protocol):
    """Accepts deploy, promote, rollback, suspend_hosting and resume_hosting commands (decision
    0004 section 4)."""

    async def find_command_by_key(self, request: CommandRequest) -> Command | None:
        """The unlocked lookup before the transaction: the command holding the request's key in
        the request's key namespace, validated on load, or None."""
        ...

    async def accept_command(
        self, params: AcceptCommandParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        """The acceptance transaction (READ COMMITTED, configured lock timeout):

        1. Lock the project's hosting row FOR UPDATE; no row is ``NotFound``, decide not called.
        2. Load ``AcceptanceFacts``.
        3. Call decide. A raise rolls back and propagates. A replay rolls back and returns the
           stored command with ``replayed=True``.
        4. For an insert: advance last_command_number to the command's number, conditional on the
           value read; insert the command as accepted with requested_by_subject; insert the
           command_dispatch row (``NewDispatch``, due at once); commit.

        A decision whose command is not for the request's project or does not carry
        ``params.command_id`` is refused without writing. A unique violation of
        commands_idempotency_key, commands_hosting_event_key or commands_one_in_flight that races
        past the checks reloads the facts and calls decide again; a second insert decision is
        ``Contention``. commands_command_number_key is an internal error.
        """
        ...


@dataclass(frozen=True, slots=True)
class AcceptRedeployParams:
    """The input of ``RedeployStore.accept_redeploy`` (decision 0023 G9): as
    ``AcceptCommandParams`` without a release, because the admin names none. The release is the
    project's live release, read under the lock. ``idempotency_key`` is the admin's key in the
    client key class (no prefix)."""

    project_id: UUID
    idempotency_key: str
    command_id: UUID
    requested_by_subject: str
    now: datetime
    correlation_id: str
    deadline_at: datetime


class RedeployStore(Protocol):
    """Accepts redeploy commands (decision 0023 G9, G12) on the API's login."""

    async def accept_redeploy(
        self, params: AcceptRedeployParams, decide: AcceptanceDecider
    ) -> AcceptedCommand:
        """The acceptance transaction of ``AcceptanceStore.accept_command``, with these facts:
        ``by_key`` is the command holding the key in the client key class, ``live`` the release
        ``hosting.live_release_id`` names and ``target`` None. No hosting row is ``NotFound``,
        decide not called. An inserted command must be a redeploy of ``params.project_id`` under
        the key, naming the locked row's live release; anything else is refused without writing.
        """
        ...


@dataclass(frozen=True, slots=True)
class Fence:
    """What every worker write for a command carries (decision 0004 section 7): the store locks
    the hosting row where last_command_number equals ``command_number`` (else ``StaleFence``), the
    dispatch row whose claim_number equals ``claim_number`` (else ``ClaimLost``), and checks the
    command's and release's expected statuses (else ``IllegalTransition``). Lock order:
    project_hosting, provisioning_ops.command_dispatch, commands, releases, release_steps."""

    project_id: UUID
    command_id: UUID
    command_number: int
    claim_number: int

    def validate(self) -> None:
        """Non-nil IDs (``InvalidId``) and the command number range (``InvalidCommandNumber``)."""
        if self.project_id.int == 0 or self.command_id.int == 0:
            raise InvalidId("fence needs project and command IDs")
        validate_command_number(self.command_number)


@dataclass(frozen=True, slots=True)
class CommandFinish:
    """End a running command without touching its release or the route: ``status`` failed
    (``fail_without_route_change`` with release_failed False) or skipped
    (``skip_hosting_command``); succeeded is ``IllegalTransition``. ``failure_code`` is recorded
    on the command and as the dispatch row's last_failure_code; ``at`` is ``Clock.now``."""

    status: CommandStatus
    failure_code: str
    at: datetime


@dataclass(frozen=True, slots=True)
class DeployOutcome:
    """A deploy command's outcome: ``failure`` None means the candidate is ready
    (``deploy_succeeded``); set means the release fails (``fail_without_route_change`` with
    release_failed True)."""

    at: datetime
    failure: Failure | None = None


class WorkerStore(Protocol):
    """The fenced write paths (decision 0004 section 7, decision 0005 sections 3 and 4). Each
    method loads the rows under the three checks, applies the named domain function and writes
    exactly the rows and columns it changed; a terminal command also sets the dispatch row's
    finished_at (and last_failure_code for a failure)."""

    async def finish_command(self, fence: Fence, finish: CommandFinish) -> None: ...

    async def fail_unclaimed_command(self, fence: Fence, failure_code: str, at: datetime) -> None:
        """The dispatcher's deadline failure of a command never claimed: accepted -> failed."""
        ...

    async def apply_deploy_outcome(self, fence: Fence, outcome: DeployOutcome) -> None: ...
