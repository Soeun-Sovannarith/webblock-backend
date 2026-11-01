"""The release_steps row and its status machine (decision 0001 section 8)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.enums.states import (
    ReleaseStepName,
    ReleaseStepStatus,
    parse_release_step_name,
    parse_release_step_status,
)
from provisioning.core.domain.errors import (
    IllegalTransition,
    ImmutableField,
    InvalidAttempt,
    InvalidFailureCode,
    InvalidId,
    MissingDeadline,
    MissingFailureCode,
)
from provisioning.core.domain.values.step_input import StepInput
from provisioning.core.domain.values.text import validate_code, validate_log_ref

# The largest step attempt: the INTEGER column.
MAX_ATTEMPT = 2147483647


@dataclass(frozen=True, slots=True)
class ReleaseStep:
    """The columns of provisioning.release_steps (revision 19 names: ``failure_code``,
    ``log_ref``)."""

    id: UUID
    release_id: UUID
    name: ReleaseStepName
    attempt: int
    status: ReleaseStepStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None
    failure_code: str | None = None
    log_ref: str | None = None

    def validate(self) -> None:
        """Check a row loaded from storage: non-nil IDs, the attempt, the enum labels, the timing
        and failure fields against the status, and the log reference format."""
        if self.id.int == 0 or self.release_id.int == 0:
            raise InvalidId("step and release IDs are required")
        validate_attempt(self.attempt)
        parse_release_step_name(self.name)
        status = parse_release_step_status(self.status)
        started, finished = self.started_at is not None, self.finished_at is not None
        if status is ReleaseStepStatus.PENDING:
            if started or finished:
                raise IllegalTransition(f"step {self.id} is pending but has a start or finish")
        elif status is ReleaseStepStatus.RUNNING:
            if not started or finished:
                raise IllegalTransition(
                    f"step {self.id} is running without a start or with a finish"
                )
            if self.deadline_at is None:
                raise MissingDeadline(f"step {self.id} is running")
        elif (
            self.started_at is None
            or self.finished_at is None
            or self.finished_at < self.started_at
        ):
            raise IllegalTransition(
                f"step {self.id} is {status} without a start, or a finish not after it"
            )
        if status is ReleaseStepStatus.FAILED:
            if self.failure_code is None:
                raise MissingFailureCode(f"step {self.id} is failed")
            validate_code(self.failure_code)
        if self.log_ref is not None:
            validate_log_ref(self.log_ref)

    def transition(self, to: ReleaseStepStatus, inp: StepInput, now: datetime) -> ReleaseStep:
        """Return the step moved to ``to`` (decision 0001 section 8). ``-> running`` sets
        started_at and needs a deadline (``MissingDeadline``); a finish sets finished_at, never
        before started_at; ``pending -> failed`` sets both to ``now``; failure needs a code. A
        ``log_ref`` is write-once (``ImmutableField``)."""
        from_ = self.status
        if not legal_step_transition(from_, to):
            raise IllegalTransition(f"step {from_} -> {to}")
        if to == ReleaseStepStatus.FAILED:
            validate_code(inp.failure_code)
        elif inp.failure_code != "":
            # Refused, not ignored: only a failed step carries a failure code.
            raise InvalidFailureCode(f"step {from_} -> {to} takes no failure code")
        if from_ == ReleaseStepStatus.RUNNING and (
            self.started_at is None or now < self.started_at
        ):
            raise IllegalTransition(f"step {from_} -> {to} would finish before it started")
        log_ref = self.log_ref
        if inp.log_ref != "":
            validate_log_ref(inp.log_ref)
            if self.log_ref is not None and self.log_ref != inp.log_ref:
                raise ImmutableField(f"log reference of step {self.id}")
            log_ref = inp.log_ref
        if to == ReleaseStepStatus.RUNNING:
            deadline = self.deadline_at if self.deadline_at is not None else inp.deadline_at
            if deadline is None:
                raise MissingDeadline(f"step {from_} -> {to}")
            return replace(
                self,
                status=ReleaseStepStatus.RUNNING,
                started_at=now,
                deadline_at=deadline,
                log_ref=log_ref,
            )
        if to == ReleaseStepStatus.SUCCEEDED:
            return replace(
                self, status=ReleaseStepStatus.SUCCEEDED, finished_at=now, log_ref=log_ref
            )
        started_at = now if from_ == ReleaseStepStatus.PENDING else self.started_at
        return replace(
            self,
            status=ReleaseStepStatus.FAILED,
            started_at=started_at,
            finished_at=now,
            failure_code=inp.failure_code,
            log_ref=log_ref,
        )

    def next_attempt(self, new_id: UUID) -> ReleaseStep:
        """A new pending row for the next attempt of a failed step; the failed row is not reset."""
        if self.status != ReleaseStepStatus.FAILED:
            raise IllegalTransition(f"step {self.status} cannot be retried")
        if new_id.int == 0 or new_id == self.id:
            raise InvalidId("retry needs a fresh step ID")
        validate_attempt(self.attempt)
        if self.attempt == MAX_ATTEMPT:
            raise InvalidAttempt(f"no attempt after {self.attempt}")
        return ReleaseStep(
            id=new_id,
            release_id=self.release_id,
            name=self.name,
            attempt=self.attempt + 1,
            status=ReleaseStepStatus.PENDING,
        )


_STEP_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (ReleaseStepStatus.PENDING, ReleaseStepStatus.RUNNING),
        (ReleaseStepStatus.PENDING, ReleaseStepStatus.FAILED),
        (ReleaseStepStatus.RUNNING, ReleaseStepStatus.SUCCEEDED),
        (ReleaseStepStatus.RUNNING, ReleaseStepStatus.FAILED),
    }
)


def legal_step_transition(from_: ReleaseStepStatus, to: ReleaseStepStatus) -> bool:
    """pending -> running | failed; running -> succeeded | failed."""
    return (str(from_), str(to)) in _STEP_TRANSITIONS


def validate_attempt(attempt: int) -> None:
    """Require 1 <= attempt <= MAX_ATTEMPT (``InvalidAttempt``)."""
    if not 1 <= attempt <= MAX_ATTEMPT:
        raise InvalidAttempt(str(attempt))


def new_release_step(*, step_id: UUID, release_id: UUID, name: ReleaseStepName) -> ReleaseStep:
    """Attempt 1 in pending status. Nil IDs are ``InvalidId``; an unknown name is
    ``UnknownEnumValue``."""
    if step_id.int == 0 or release_id.int == 0:
        raise InvalidId("step and release IDs are required")
    return ReleaseStep(
        id=step_id,
        release_id=release_id,
        name=parse_release_step_name(name),
        attempt=1,
        status=ReleaseStepStatus.PENDING,
    )
