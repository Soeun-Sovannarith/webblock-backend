"""Release intake types and the store port (decision 0008 sections 1 to 3). The
inbox is the table provisioning_ops.source_published_events (revision 19 names)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from provisioning.core.application.shared.errors import CorruptRow
from provisioning.core.domain import (
    DomainError,
    IllegalTransition,
    InvalidAttempt,
    InvalidId,
    MissingFailureCode,
    ProjectHosting,
    Release,
    ReleaseStep,
    ReleaseStepName,
    SourcePublished,
    UnknownEnumValue,
    new_project_hosting,
    new_release_step,
    validate_code,
)

# The INTEGER maximum of the counters.
MAX_INT32 = 2147483647


class SourceEventStatus(StrEnum):
    """provisioning_ops.source_event_status. pending is the only status that changes."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"

    def terminal(self) -> bool:
        return self in (
            SourceEventStatus.ACCEPTED,
            SourceEventStatus.REJECTED,
            SourceEventStatus.FAILED,
        )


def parse_source_event_status(text: object) -> SourceEventStatus:
    """``UnknownEnumValue`` naming the enum, never the input; a value that is no string is
    unknown too."""
    if isinstance(text, str):
        for member in SourceEventStatus:
            if member.value == text:
                return member
    raise UnknownEnumValue("source event status") from None


def legal_source_event_transition(from_: SourceEventStatus, to: SourceEventStatus) -> bool:
    """pending -> accepted | rejected | failed, and nothing else."""
    return from_ == SourceEventStatus.PENDING and to in (
        SourceEventStatus.ACCEPTED,
        SourceEventStatus.REJECTED,
        SourceEventStatus.FAILED,
    )


@dataclass(frozen=True, slots=True)
class InboxTake:
    """An inbox row as one take left it; a write conditional on it changes the row only while it
    is pending with these attempts."""

    project_id: UUID
    generation_request_id: UUID
    attempts: int

    def validate(self) -> None:
        """Non-nil IDs (``InvalidId``) and 1 <= attempts <= MAX_INT32 (``InvalidAttempt``)."""
        if self.project_id.int == 0 or self.generation_request_id.int == 0:
            raise InvalidId("inbox take needs project and generation request IDs")
        if not 1 <= self.attempts <= MAX_INT32:
            raise InvalidAttempt(f"inbox take of project {self.project_id}")


@dataclass(frozen=True, slots=True)
class InboxEntry:
    """The columns of provisioning_ops.source_published_events (decision 0008 section 2,
    migration 00010, revision 19 names)."""

    event: SourcePublished
    status: SourceEventStatus
    attempts: int
    next_attempt_at: datetime
    received_at: datetime
    updated_at: datetime
    max_attempts: int
    outcome_code: str | None = None
    release_id: UUID | None = None
    mismatched_repeat_count: int = 0
    last_mismatched_repeat_at: datetime | None = None
    finished_at: datetime | None = None
    last_manual_retry_by: str | None = None
    last_manual_retry_at: datetime | None = None

    def validate(self) -> None:
        """Check a row loaded from storage against the table rules; every error is
        ``CorruptRow``, raised from the domain cause when there is one."""
        key = (
            f"inbox row of project {self.event.project_id}, "
            f"generation request {self.event.generation_request_id}"
        )
        try:
            self.event.validate()
            status = parse_source_event_status(self.status)
        except DomainError as exc:
            raise CorruptRow(f"{key}: {exc}") from exc
        if not (0 <= self.attempts <= MAX_INT32 and 0 <= self.mismatched_repeat_count <= MAX_INT32):
            raise CorruptRow(f"{key}: attempts or mismatched repeat count out of range")
        if not 1 <= self.max_attempts <= MAX_INT32:
            raise CorruptRow(f"{key}: max attempts out of range")
        if (self.last_manual_retry_by is None) != (self.last_manual_retry_at is None) or (
            self.last_manual_retry_by == ""
        ):
            raise CorruptRow(f"{key}: last_manual_retry_by and last_manual_retry_at do not match")
        if (self.mismatched_repeat_count > 0) != (self.last_mismatched_repeat_at is not None):
            raise CorruptRow(f"{key}: last_mismatched_repeat_at does not match the count")
        if status.terminal() != (self.finished_at is not None):
            raise CorruptRow(f"{key}: finished_at does not match status {status}")
        if (status == SourceEventStatus.ACCEPTED) != (self.release_id is not None):
            raise CorruptRow(f"{key}: release_id does not match status {status}")
        if self.release_id is not None and self.release_id.int == 0:
            raise CorruptRow(f"{key}: release") from InvalidId("release")
        if status not in (SourceEventStatus.REJECTED, SourceEventStatus.FAILED):
            if self.outcome_code is not None:
                raise CorruptRow(f"{key}: outcome_code while {status}")
            return
        if self.outcome_code is None:
            raise CorruptRow(f"{key}: {status} without an outcome code") from MissingFailureCode(
                status
            )
        try:
            validate_code(self.outcome_code)
        except DomainError as exc:
            raise CorruptRow(f"{key}: {exc}") from exc

    def take(self) -> InboxTake:
        return InboxTake(
            project_id=self.event.project_id,
            generation_request_id=self.event.generation_request_id,
            attempts=self.attempts,
        )


class IntakeOutcome(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    # A row with the same key holds another repository or SHA; its mismatched_repeat_count and
    # last_mismatched_repeat_at were updated.
    MISMATCHED_REPEAT = "mismatched_repeat"
    OVER_CAPACITY = "over_capacity"


@dataclass(frozen=True, slots=True)
class IntakeParams:
    event: SourcePublished
    max_pending_per_project: int

    def validate(self) -> None:
        self.event.validate()
        if self.max_pending_per_project < 1:
            raise ValueError("intake: the per-project cap of pending rows must be positive")


@dataclass(frozen=True, slots=True)
class TakeInboxParams:
    batch_size: int
    max_per_project: int
    visibility: timedelta

    def validate(self) -> None:
        """batch_size >= 1, 1 <= max_per_project <= batch_size, visibility >= 1 microsecond
        (``ValueError``)."""
        if self.batch_size < 1 or not 1 <= self.max_per_project <= self.batch_size:
            raise ValueError(
                "inbox take: batch size and per-project limit must be positive, "
                "the limit at most the batch"
            )
        if self.visibility < timedelta(microseconds=1):
            raise ValueError("inbox take: visibility timeout must be at least one microsecond")


@dataclass(frozen=True, slots=True)
class InboxBatch:
    """``now`` is the database clock of the take; ``entries`` the rows as the take left them."""

    now: datetime
    entries: tuple[InboxEntry, ...]


@dataclass(frozen=True, slots=True)
class ReleaseAcceptanceParams:
    take: InboxTake
    release_id: UUID
    step_id: UUID


@dataclass(frozen=True, slots=True)
class ReleaseAcceptanceFacts:
    """What the store loaded inside the acceptance transaction after locking the inbox row."""

    entry: InboxEntry
    existing: Release | None
    release_id: UUID
    step_id: UUID
    now: datetime


class ReleaseAcceptanceDecision:
    """Accept a new release, accept the stored one, or reject with a code. Made only by
    ``accept_new_release``, ``accept_existing_release`` and ``reject_release``."""

    __slots__ = ("_code", "_created", "_hosting", "_release", "_step")

    def __init__(
        self,
        *,
        created: bool,
        hosting: ProjectHosting | None,
        release: Release | None,
        step: ReleaseStep | None,
        code: str,
    ) -> None:
        self._created = created
        self._hosting = hosting
        self._release = release
        self._step = step
        self._code = code

    @property
    def accepted(self) -> bool:
        return self._release is not None

    @property
    def created(self) -> bool:
        return self._created

    @property
    def outcome_code(self) -> str:
        """The rejection's code, ``""`` when accepted."""
        return self._code

    @property
    def hosting(self) -> ProjectHosting | None:
        """The initial hosting row to insert ON CONFLICT DO NOTHING when created."""
        return self._hosting

    @property
    def release(self) -> Release | None:
        return self._release

    @property
    def step(self) -> ReleaseStep | None:
        return self._step


def accept_new_release(
    facts: ReleaseAcceptanceFacts, *, subdomain: str
) -> ReleaseAcceptanceDecision:
    """A new release, its first build step, and the project's hosting row with ``subdomain`` (the
    repository lookup's answer, decision 0021 D8). The store inserts the hosting row only when
    the project has none, so an existing row keeps its own sub-domain."""
    _pending_facts(facts)
    if facts.existing is not None:
        raise IllegalTransition(
            f"release {facts.existing.id} already exists for generation request "
            f"{facts.entry.event.generation_request_id}"
        )
    if facts.release_id == facts.step_id:
        raise InvalidId("release and step need distinct IDs")
    release = facts.entry.event.new_release(facts.release_id, facts.now)
    hosting = new_project_hosting(facts.entry.event.project_id, facts.now, subdomain=subdomain)
    step = new_release_step(
        step_id=facts.step_id, release_id=facts.release_id, name=ReleaseStepName.BUILD
    )
    return ReleaseAcceptanceDecision(
        created=True, hosting=hosting, release=release, step=step, code=""
    )


def accept_existing_release(facts: ReleaseAcceptanceFacts) -> ReleaseAcceptanceDecision:
    _pending_facts(facts)
    if facts.existing is None:
        raise IllegalTransition(
            f"no release stored for generation request {facts.entry.event.generation_request_id}"
        )
    facts.entry.event.check_release(facts.existing)
    return ReleaseAcceptanceDecision(
        created=False, hosting=None, release=facts.existing, step=None, code=""
    )


def reject_release(code: str) -> ReleaseAcceptanceDecision:
    validate_code(code)
    return ReleaseAcceptanceDecision(
        created=False, hosting=None, release=None, step=None, code=code
    )


type ReleaseAcceptanceDecider = Callable[[ReleaseAcceptanceFacts], ReleaseAcceptanceDecision]


@dataclass(frozen=True, slots=True)
class ReleaseAcceptanceResult:
    """``superseded``: a newer take owns the row; nothing written."""

    superseded: bool
    status: SourceEventStatus | None = None
    outcome_code: str = ""
    release_id: UUID | None = None
    created: bool = False


class ReleaseIntakeStore(Protocol):
    """The inbox and the release acceptance (decision 0008 sections 2 and 3); the transaction
    steps and their lock order are in ``infrastructure/postgres/intake_store.py``."""

    async def intake(self, params: IntakeParams) -> IntakeOutcome: ...

    async def take_due(self, params: TakeInboxParams) -> InboxBatch: ...

    async def accept_release(
        self, params: ReleaseAcceptanceParams, decide: ReleaseAcceptanceDecider
    ) -> ReleaseAcceptanceResult: ...

    async def reject(self, take: InboxTake, code: str) -> bool: ...

    async def backoff(self, take: InboxTake, delay: timedelta) -> bool: ...

    async def fail(self, take: InboxTake, code: str) -> bool: ...


def _pending_facts(facts: ReleaseAcceptanceFacts) -> None:
    if facts.entry.status != SourceEventStatus.PENDING:
        raise IllegalTransition(f"inbox row is {facts.entry.status}, not pending")
    if facts.release_id.int == 0 or facts.step_id.int == 0:
        raise InvalidId("release acceptance needs release and step IDs")
