"""The commands row and its transition table (decision 0001 section 4). The
request and its fingerprint are in ``values/command_request.py``, the acceptance preconditions in
``rules/command_preconditions.py`` (decision 0025 CQ1)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from provisioning.core.domain.enums.states import (
    CommandKind,
    CommandStatus,
    OfflineReason,
    parse_command_kind,
    parse_command_status,
)
from provisioning.core.domain.errors import (
    IdempotencyConflict,
    IllegalTransition,
    InvalidCommandTarget,
    InvalidFailureCode,
    InvalidId,
    InvalidRequester,
    InvalidRequestFingerprint,
    MissingFailureCode,
    ProjectMismatch,
)
from provisioning.core.domain.values.command_request import (
    CommandRequest,
    check_target_by_kind,
    compute_request_fingerprint,
)
from provisioning.core.domain.values.hosting_reason import hosting_reason_of_key
from provisioning.core.domain.values.identifiers import (
    parse_request_fingerprint,
    validate_command_number,
)
from provisioning.core.domain.values.text import (
    validate_code,
    validate_idempotency_key,
    validate_requester,
)


@dataclass(frozen=True, slots=True)
class Command:
    """The columns of provisioning.commands (revision 19 names). ``release_id`` is None exactly
    for the hosting kinds. ``command_number`` is assigned by the server at acceptance.
    ``requested_by_subject`` is the token subject of a deploy, promote, rollback or redeploy and of
    a hosting kind with ``admin_suspended`` (the admin), None for the other hosting commands and
    sync_domains; it is not part of the fingerprint and never changes. ``hosting_reason`` is the
    offline reason of a hosting kind, fixed by its idempotency key (``hosting_reason_of_key``), and
    None for every other kind; it is not part of the fingerprint either."""

    id: UUID
    project_id: UUID
    release_id: UUID | None
    kind: CommandKind
    idempotency_key: str
    request_fingerprint: str
    command_number: int
    status: CommandStatus
    created_at: datetime
    updated_at: datetime
    failure_code: str | None = None
    requested_by_subject: str | None = None
    hosting_reason: OfflineReason | None = None

    def transition(self, to: CommandStatus, failure_code: str, now: datetime) -> Command:
        """Return the command moved to ``to``. skipped is legal only for hosting kinds. A failure
        code is required on failed and skipped and refused otherwise."""
        if not legal_command_transition(self.status, to) or (
            to == CommandStatus.SKIPPED and not _is_hosting(self.kind)
        ):
            raise IllegalTransition(f"{self.kind} command {self.id} {self.status} -> {to}")
        code: str | None = None
        if to in (CommandStatus.FAILED, CommandStatus.SKIPPED):
            validate_code(failure_code)
            code = failure_code
        elif failure_code != "":
            raise InvalidFailureCode(
                f"command {self.id} {self.status} -> {to} takes no failure code"
            )
        return replace(self, status=CommandStatus(to), failure_code=code, updated_at=now)

    def check_replay(self, request: CommandRequest) -> None:
        """Return when ``request`` repeats this command under the same key (a replay, whatever the
        status). Another project is ``ProjectMismatch``; another key or fingerprint is
        ``IdempotencyConflict``."""
        fingerprint = compute_request_fingerprint(request)
        if self.project_id != request.project_id:
            raise ProjectMismatch(f"command {self.id}")
        if (
            self.idempotency_key != request.idempotency_key
            or self.request_fingerprint != fingerprint
        ):
            raise IdempotencyConflict(f"command {self.id}")

    def validate(self) -> None:
        """Check a row loaded from storage: IDs, enum labels, the release against the kind, the
        key, the fingerprint's format and recomputed value, the command number range, the requester
        against the kind, and the failure code against the status."""
        if self.id.int == 0 or self.project_id.int == 0:
            raise InvalidId("command and project IDs are required")
        kind = parse_command_kind(self.kind)
        status = parse_command_status(self.status)
        check_target_by_kind(kind, self.release_id)
        validate_idempotency_key(self.idempotency_key)
        parse_request_fingerprint(self.request_fingerprint)
        fingerprint = compute_request_fingerprint(
            CommandRequest(
                project_id=self.project_id,
                kind=kind,
                release_id=self.release_id,
                idempotency_key=self.idempotency_key,
            )
        )
        if fingerprint != self.request_fingerprint:
            raise InvalidRequestFingerprint(
                f"command {self.id} request fingerprint does not match its columns"
            )
        validate_command_number(self.command_number)
        _check_hosting_reason(kind, self.idempotency_key, self.hosting_reason)
        _check_requester_by_kind(kind, self.hosting_reason, self.requested_by_subject)
        if status not in (CommandStatus.FAILED, CommandStatus.SKIPPED):
            if self.failure_code is not None:
                raise InvalidFailureCode(f"command {self.id} holds a failure code while {status}")
            return
        if self.failure_code is None:
            raise MissingFailureCode(f"command {self.id} is {status}")
        validate_code(self.failure_code)


def new_command(
    *,
    command_id: UUID,
    request: CommandRequest,
    requested_by_subject: str | None,
    command_number: int,
    now: datetime,
    hosting_reason: OfflineReason | None = None,
) -> Command:
    """An accepted command with its fingerprint, created at ``now``. A hosting kind carries the
    reason its key fixes (``hosting_reason`` when given, which must match the key); any other
    kind carries none."""
    if command_id.int == 0:
        raise InvalidId("command ID is required")
    fingerprint = compute_request_fingerprint(request)
    validate_command_number(command_number)
    kind = parse_command_kind(request.kind)
    if kind.is_hosting() and hosting_reason is None:
        hosting_reason = hosting_reason_of_key(kind, request.idempotency_key)
    _check_hosting_reason(kind, request.idempotency_key, hosting_reason)
    _check_requester_by_kind(kind, hosting_reason, requested_by_subject)
    return Command(
        id=command_id,
        project_id=request.project_id,
        release_id=request.release_id,
        kind=kind,
        idempotency_key=request.idempotency_key,
        request_fingerprint=fingerprint,
        command_number=command_number,
        status=CommandStatus.ACCEPTED,
        created_at=now,
        updated_at=now,
        requested_by_subject=requested_by_subject,
        hosting_reason=hosting_reason,
    )


def legal_command_transition(from_: CommandStatus, to: CommandStatus) -> bool:
    """accepted -> running | failed; running -> succeeded | failed | skipped (skipped further
    limited to hosting kinds by ``Command.transition``)."""
    return (str(from_), str(to)) in _COMMAND_TRANSITIONS


_COMMAND_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (CommandStatus.ACCEPTED, CommandStatus.RUNNING),
        (CommandStatus.ACCEPTED, CommandStatus.FAILED),
        (CommandStatus.RUNNING, CommandStatus.SUCCEEDED),
        (CommandStatus.RUNNING, CommandStatus.FAILED),
        (CommandStatus.RUNNING, CommandStatus.SKIPPED),
    }
)


def _is_hosting(kind: str) -> bool:
    return kind in (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING)


def _check_requester_by_kind(
    kind: CommandKind, hosting_reason: OfflineReason | None, requested_by_subject: str | None
) -> None:
    """commands_requested_by_subject_by_kind (migration 00020): a requester on deploy, promote,
    rollback and redeploy and on a hosting kind with ``admin_suspended`` (the admin); none on any
    other hosting command or sync_domains."""
    needed = not kind.names_no_release() or (
        kind.is_hosting() and hosting_reason is OfflineReason.ADMIN_SUSPENDED
    )
    if not needed:
        if requested_by_subject is not None:
            raise InvalidRequester(f"{kind} has no requester")
        return
    if requested_by_subject is None:
        raise InvalidRequester(f"{kind} requires a requester")
    validate_requester(requested_by_subject)


def _check_hosting_reason(
    kind: CommandKind, key: str, hosting_reason: OfflineReason | None
) -> None:
    """A hosting kind holds exactly the reason its key fixes; every other kind holds none."""
    if not kind.is_hosting():
        if hosting_reason is not None:
            raise InvalidCommandTarget(f"{kind} has no offline reason")
        return
    if hosting_reason is None:
        raise InvalidCommandTarget(f"{kind} requires an offline reason")
    if hosting_reason != hosting_reason_of_key(kind, key):
        raise InvalidCommandTarget(f"{kind} offline reason does not match its key")
