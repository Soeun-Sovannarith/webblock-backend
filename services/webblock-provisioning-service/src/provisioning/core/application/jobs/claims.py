"""Command dispatch and claim types (decision 0005; decision 0013 section 9, A2). Revision 19
names: the work row is ``command_dispatch``, a lease is a claim with a ``claim_number``,
``claim_count`` is ``execution_attempts``. The ``CommandJob`` wire message belongs to
``presentation.messaging``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.core.application.jobs.ports import Fence
from provisioning.core.domain import (
    Command,
    CommandKind,
    InvalidCommandTarget,
    InvalidId,
    parse_command_kind,
    validate_command_number,
)

# The width of command_dispatch.claimed_by_worker.
MAX_CLAIMED_BY_WORKER_LEN = 100

# Ends a command whose executor cannot execute anything in this phase (decision 0005 section 5).
FAILURE_CODE_EXECUTOR_UNAVAILABLE = "executor_unavailable"
# Ends a claimed command of a kind the worker does not enable; no executor is called.
FAILURE_CODE_OPERATION_UNAVAILABLE = "operation_unavailable"
# The last_failure_code of a dispatch row whose claim is given back after a transient failure
# without a well-formed code of its own; never a command's failure code.
FAILURE_CODE_TRANSIENT = "transient_failure"


class ClaimLost(Exception):
    """The dispatch row's claim_number is not the caller's: another worker claimed the command
    after this claim expired, or the row never held it. Worker-internal."""


class InvalidCorrelationId(Exception):
    """A correlation ID that does not fit the dispatch row and the envelope (1 to 128)."""


@dataclass(frozen=True, slots=True)
class WorkRef:
    """One unit of work: a command and the identifiers a job message carries for it."""

    command_id: UUID
    project_id: UUID
    kind: CommandKind
    command_number: int
    release_id: UUID | None = None

    def validate(self) -> None:
        """Non-nil IDs (``InvalidId``), a known kind (``UnknownEnumValue``), the command number
        range (``InvalidCommandNumber``), the release present exactly when the kind is not a
        hosting kind (``InvalidCommandTarget``)."""
        if self.command_id.int == 0 or self.project_id.int == 0:
            raise InvalidId("work reference needs command and project IDs")
        kind = parse_command_kind(self.kind)
        validate_command_number(self.command_number)
        if kind.names_no_release() and self.release_id is not None:
            raise InvalidCommandTarget(f"{kind} command {self.command_id} names a release")
        if not kind.names_no_release() and self.release_id is None:
            raise InvalidCommandTarget(f"{kind} command {self.command_id} names no release")
        if self.release_id is not None and self.release_id.int == 0:
            raise InvalidId(f"release of command {self.command_id}")


def ref_of(command: Command) -> WorkRef:
    """The reference of a command row."""
    return WorkRef(
        command_id=command.id,
        project_id=command.project_id,
        kind=command.kind,
        command_number=command.command_number,
        release_id=command.release_id,
    )


@dataclass(frozen=True, slots=True)
class NewDispatch:
    """The command_dispatch row the acceptance inserts in its transaction (decision 0005
    section 2): claim number and counters at zero, no claim, due at ``accepted_at``."""

    ref: WorkRef
    correlation_id: str
    accepted_at: datetime
    deadline_at: datetime


type AcceptanceHook[Tx] = Callable[[Tx, NewDispatch], Awaitable[None]]
"""Writes the dispatch row of a newly accepted command inside the acceptance transaction. ``Tx``
is the driver's transaction type, so the core imports no driver. An error rolls the acceptance
back and is an internal error."""


@dataclass(frozen=True, slots=True)
class Claim:
    """A worker's hold on a command (decision 0005 section 4): claimed_by_worker,
    claim_number, execution_attempts and claim_expires_at after a claim or a heartbeat, with the
    command's deadline and the dispatch row's last_failure_code as the claim found it (decision
    0027 T9). Times come from the database clock."""

    ref: WorkRef
    claimed_by_worker: str
    claim_number: int
    execution_attempt: int
    expires_at: datetime
    deadline_at: datetime
    last_failure_code: str | None = None

    def fence(self) -> Fence:
        return Fence(
            project_id=self.ref.project_id,
            command_id=self.ref.command_id,
            command_number=self.ref.command_number,
            claim_number=self.claim_number,
        )


class Executor(Protocol):
    """Runs a claimed command. Returning means the command finished through its own fenced
    writes; ``PermanentFailure`` ends it failed with its code; ``RetryLater`` gives the claim back
    with its code and wait; ``ClaimLost`` or ``StaleFence`` means write nothing more; any other
    error is transient."""

    async def execute(self, claim: Claim) -> None: ...


class PermanentFailure(Exception):
    """An executor result that must not be retried: the command ends failed with ``code``."""

    def __init__(self, code: str) -> None:
        super().__init__(f"permanent failure {code}")
        self.code = code


class RetryLater(Exception):
    """An executor result that ends this claim, not the command: the consumer releases the claim
    with ``code`` as the dispatch row's last failure code, and the command is claimed again no
    sooner than ``after``, or after the worker's generic retry backoff when ``after`` is None.
    An ``after`` that is zero or negative is ``ValueError``."""

    def __init__(self, code: str, after: timedelta | None = None) -> None:
        if after is not None and after <= timedelta(0):
            raise ValueError(f"retry wait must be positive, got {after}")
        super().__init__(f"retried later: {code}")
        self.code = code
        self.after = after


class EnabledKinds:
    """The set of command kinds a build of the worker executes, passed in code. Empty enables
    nothing. Immutable after construction."""

    __slots__ = ("_enabled",)

    def __init__(self, enabled: Iterable[CommandKind] = ()) -> None:
        """Hold exactly ``enabled``; a value that is not a ``CommandKind`` is
        ``UnknownEnumValue``."""
        self._enabled = frozenset(parse_command_kind(kind) for kind in enabled)

    def enabled(self, kind: CommandKind) -> bool:
        return kind in self._enabled

    def kinds(self) -> tuple[CommandKind, ...]:
        """The enabled kinds in enum order."""
        return tuple(kind for kind in CommandKind if kind in self._enabled)


@dataclass(frozen=True, slots=True)
class DueDispatch:
    """One dispatch row the dispatcher took (decision 0005 section 3)."""

    ref: WorkRef
    correlation_id: str
    publish_attempts: int
    claim_number: int
    execution_attempts: int
    deadline_at: datetime
    deadline_passed: bool

    def fence(self) -> Fence:
        """The fence used to fail a command that was never claimed."""
        return Fence(
            project_id=self.ref.project_id,
            command_id=self.ref.command_id,
            command_number=self.ref.command_number,
            claim_number=self.claim_number,
        )


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    ref: WorkRef
    claimed_by_worker: str
    ttl: timedelta


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    FINISHED = "finished"
    HELD = "held"
    BACKOFF = "backoff"
    STALE_COMMAND_NUMBER = "stale_command_number"
    NO_WORK = "no_work"
    MISMATCH = "mismatch"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: ClaimOutcome
    claim: Claim | None = None
    deadline_passed: bool = False


class JobStore(Protocol):
    """The dispatch row's port (decision 0005 sections 2 to 4). Times are database clock;
    durations are added by the store. Claim-dependent methods raise ``ClaimLost``."""

    async def take_due(self, limit: int, visibility: timedelta) -> list[DueDispatch]: ...

    async def record_broker_ack(self, due: DueDispatch, redeliver_after: timedelta) -> bool:
        """last_broker_ack_at = now, next_publish_at = now + redeliver_after, conditional on
        publish_attempts and no live claim."""
        ...

    async def record_publish_failure(self, due: DueDispatch, retry_after: timedelta) -> bool: ...

    async def count_unfinished_without_dispatch(self) -> int: ...

    async def claim(self, request: ClaimRequest) -> ClaimResult: ...

    async def extend_claim(self, claim: Claim, ttl: timedelta) -> Claim: ...

    async def release_claim(self, claim: Claim, retry_after: timedelta, failure_code: str) -> None:
        """Give the command back after a transient failure: retry_after and next_publish_at =
        now + retry_after, last_failure_code = failure_code."""
        ...
