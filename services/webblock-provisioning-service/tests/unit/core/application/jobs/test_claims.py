"""The work reference, the claim and dispatch fences, the permanent failure, the retry-later
result, the claim-lost error, the fixed failure codes and the enabled kinds (decision 0005;
revision 19 names)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.application import (
    FAILURE_CODE_EXECUTOR_UNAVAILABLE,
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    FAILURE_CODE_TRANSIENT,
    Claim,
    ClaimLost,
    Contention,
    DueDispatch,
    EnabledKinds,
    Fence,
    NotFound,
    PermanentFailure,
    RetryLater,
    WorkRef,
    ref_of,
)
from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    NIL_ID,
    Command,
    CommandKind,
    CommandRequest,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidId,
    StaleFence,
    UnknownEnumValue,
    is_error,
    new_command,
    validate_code,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_T2 = _T0 + timedelta(hours=1)

_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")


def _deploy_command() -> Command:
    return new_command(
        command_id=_ID_COMMAND,
        request=CommandRequest(
            project_id=_ID_PROJECT,
            kind=CommandKind.DEPLOY,
            release_id=_ID_RELEASE,
            idempotency_key="key-1",
        ),
        requested_by_subject="user-1",
        command_number=4,
        now=_T0,
    )


def _hosting_command() -> Command:
    return new_command(
        command_id=_ID_COMMAND,
        request=CommandRequest(
            project_id=_ID_PROJECT,
            kind=CommandKind.SUSPEND_HOSTING,
            release_id=None,
            idempotency_key="evt-1",
        ),
        requested_by_subject=None,
        command_number=4,
        now=_T0,
    )


def _deploy_ref() -> WorkRef:
    return WorkRef(
        command_id=_ID_COMMAND,
        project_id=_ID_PROJECT,
        kind=CommandKind.DEPLOY,
        command_number=4,
        release_id=_ID_RELEASE,
    )


def test_ref_of_copies_the_command_identity() -> None:
    cmd = _deploy_command()
    ref = ref_of(cmd)
    assert ref == WorkRef(
        command_id=cmd.id,
        project_id=cmd.project_id,
        kind=CommandKind.DEPLOY,
        command_number=4,
        release_id=_ID_RELEASE,
    )
    ref.validate()

    hosting = ref_of(_hosting_command())
    assert hosting.release_id is None
    assert hosting.kind is CommandKind.SUSPEND_HOSTING
    hosting.validate()


# A reference is parsed through the domain (decision 0005 section 6): non-nil IDs, a known kind,
# the command number range, and the release present exactly when the kind is not a hosting kind.
_WORK_REF_CASES: list[tuple[str, WorkRef, type[Exception]]] = [
    ("no command ID", replace(_deploy_ref(), command_id=NIL_ID), InvalidId),
    ("no project ID", replace(_deploy_ref(), project_id=NIL_ID), InvalidId),
    ("nil release ID", replace(_deploy_ref(), release_id=NIL_ID), InvalidId),
    ("unknown kind", replace(_deploy_ref(), kind=cast(CommandKind, "build")), UnknownEnumValue),
    ("empty kind", replace(_deploy_ref(), kind=cast(CommandKind, "")), UnknownEnumValue),
    ("generation 0", replace(_deploy_ref(), command_number=0), InvalidCommandNumber),
    (
        "generation over maximum",
        replace(_deploy_ref(), command_number=MAX_COMMAND_NUMBER + 1),
        InvalidCommandNumber,
    ),
    ("deploy without release", replace(_deploy_ref(), release_id=None), InvalidCommandTarget),
    (
        "rollback without release",
        replace(_deploy_ref(), kind=CommandKind.ROLLBACK, release_id=None),
        InvalidCommandTarget,
    ),
    (
        "suspend with release",
        replace(_deploy_ref(), kind=CommandKind.SUSPEND_HOSTING, release_id=_ID_RELEASE),
        InvalidCommandTarget,
    ),
    (
        "resume with release",
        replace(_deploy_ref(), kind=CommandKind.RESUME_HOSTING),
        InvalidCommandTarget,
    ),
    (
        "zero value",
        WorkRef(
            command_id=NIL_ID,
            project_id=NIL_ID,
            kind=cast(CommandKind, ""),
            command_number=0,
        ),
        InvalidId,
    ),
]


@pytest.mark.parametrize(
    ("ref", "want"),
    [(ref, want) for _, ref, want in _WORK_REF_CASES],
    ids=[name for name, _, _ in _WORK_REF_CASES],
)
def test_work_ref_validate(ref: WorkRef, want: type[Exception]) -> None:
    with pytest.raises(want):
        ref.validate()


@pytest.mark.parametrize(
    "kind",
    [CommandKind.DEPLOY, CommandKind.PROMOTE, CommandKind.ROLLBACK],
    ids=["deploy", "promote", "rollback"],
)
def test_work_ref_validate_accepts_release_kinds(kind: CommandKind) -> None:
    ref = replace(_deploy_ref(), kind=kind)
    ref.validate()
    assert ref.release_id == _ID_RELEASE


@pytest.mark.parametrize(
    "kind",
    [CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING],
    ids=["suspend_hosting", "resume_hosting"],
)
def test_work_ref_validate_accepts_hosting_kinds_without_release(kind: CommandKind) -> None:
    ref = replace(_deploy_ref(), kind=kind, release_id=None)
    ref.validate()
    assert ref.release_id is None


def test_work_ref_validate_accepts_maximum_command_number() -> None:
    ref = replace(_deploy_ref(), command_number=MAX_COMMAND_NUMBER)
    ref.validate()
    assert ref.command_number == MAX_COMMAND_NUMBER


# The fence of a claim is the three identifiers of its command plus the claim number, so a fenced
# write cannot swap the project and command IDs by position.
def test_claim_fence() -> None:
    claim = Claim(
        ref=_deploy_ref(),
        claimed_by_worker="worker-a",
        claim_number=3,
        execution_attempt=2,
        expires_at=_T1,
        deadline_at=_T2,
    )
    want = Fence(project_id=_ID_PROJECT, command_id=_ID_COMMAND, command_number=4, claim_number=3)
    got = claim.fence()
    assert got == want
    got.validate()


# The dispatcher fences a never-claimed command with the claim number it read from the dispatch row.
def test_due_dispatch_fence() -> None:
    due = DueDispatch(
        ref=_deploy_ref(),
        correlation_id="corr-1",
        publish_attempts=2,
        claim_number=0,
        execution_attempts=0,
        deadline_at=_T2,
        deadline_passed=True,
    )
    want = Fence(project_id=_ID_PROJECT, command_id=_ID_COMMAND, command_number=4, claim_number=0)
    assert due.fence() == want
    assert replace(due, claim_number=5).fence().claim_number == 5


def test_permanent_failure() -> None:
    cause = RuntimeError("no executor in this phase")
    with pytest.raises(PermanentFailure) as exc_info:
        raise PermanentFailure(FAILURE_CODE_EXECUTOR_UNAVAILABLE) from cause
    perm = exc_info.value
    code = perm.code
    assert code == FAILURE_CODE_EXECUTOR_UNAVAILABLE
    # The cause is in the chain.
    assert perm.__cause__ is cause
    assert is_error(perm, RuntimeError)
    assert FAILURE_CODE_EXECUTOR_UNAVAILABLE in str(perm)

    bare = PermanentFailure("deadline_exceeded")
    assert "deadline_exceeded" in str(bare)
    bare_code = bare.code
    assert bare_code == "deadline_exceeded"
    assert bare.__cause__ is None

    # A transient error is anything that is not permanent and not a lost claim.
    assert not is_error(ConnectionResetError("connection reset"), PermanentFailure)
    assert not is_error(ClaimLost(), PermanentFailure)


def test_tr57_retry_later_refuses_a_zero_or_negative_wait() -> None:
    """TR-57 (G11): a set wait must be positive, as ``release_claim`` requires; None (the generic
    backoff) and a positive wait are kept as given, with the code."""
    for after in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError, match="wait"):
            RetryLater("route_refused", after)
    for kept in (None, timedelta(seconds=30)):
        retry = RetryLater("tenant_not_ready", kept)
        assert (retry.code, retry.after) == ("tenant_not_ready", kept)
        assert "tenant_not_ready" in str(retry)
    assert RetryLater("tenant_not_ready").after is None
    # Neither permanent nor a lost claim: the consumer gives the claim back.
    assert not is_error(RetryLater("tenant_not_ready"), PermanentFailure)
    assert not is_error(RetryLater("tenant_not_ready"), ClaimLost)


@pytest.mark.parametrize(
    "other",
    [StaleFence, Contention, NotFound],
    ids=["ErrStaleFence", "ErrContention", "ErrNotFound"],
)
def test_claim_lost_is_its_own_sentinel(other: type[Exception]) -> None:
    assert not issubclass(other, ClaimLost)
    assert not issubclass(ClaimLost, other)
    assert not is_error(other(), ClaimLost)
    assert not is_error(ClaimLost(), other)
    assert ClaimLost.__doc__, "ClaimLost has no text"


# The codes decision 0005 section 5 fixes must fit the failure code format, because they are
# written to commands.failure_code and command_dispatch.last_failure_code.
def test_fixed_failure_codes_are_valid() -> None:
    for code in (
        FAILURE_CODE_EXECUTOR_UNAVAILABLE,
        FAILURE_CODE_OPERATION_UNAVAILABLE,
        FAILURE_CODE_TRANSIENT,
    ):
        validate_code(code)
    assert FAILURE_CODE_EXECUTOR_UNAVAILABLE == "executor_unavailable"
    assert FAILURE_CODE_OPERATION_UNAVAILABLE == "operation_unavailable"
    assert FAILURE_CODE_TRANSIENT == "transient_failure"


def test_enabled_kinds_zero_value_enables_nothing() -> None:
    for none in (EnabledKinds(), EnabledKinds([])):
        for kind in CommandKind:
            assert not none.enabled(kind), f"empty EnabledKinds enables {kind}"
        assert none.kinds() == ()


def test_enabled_kinds_holds_exactly_what_was_passed() -> None:
    enabled = EnabledKinds([CommandKind.PROMOTE, CommandKind.DEPLOY, CommandKind.DEPLOY])
    for kind in CommandKind:
        want = kind in (CommandKind.DEPLOY, CommandKind.PROMOTE)
        assert enabled.enabled(kind) is want, f"enabled({kind})"
    # In enum order, not the order passed, without duplicates.
    assert enabled.kinds() == (CommandKind.DEPLOY, CommandKind.PROMOTE)

    every = EnabledKinds(CommandKind)
    assert every.kinds() == tuple(CommandKind)


@pytest.mark.parametrize("bad", ["", "build", "Deploy"], ids=["empty", "build", "Deploy"])
def test_enabled_kinds_refuses_unknown_kinds(bad: str) -> None:
    with pytest.raises(UnknownEnumValue):
        EnabledKinds([CommandKind.DEPLOY, cast(CommandKind, bad)])


def test_enabled_kinds_reports_unknown_kinds_as_disabled() -> None:
    every = EnabledKinds(CommandKind)
    assert not every.enabled(cast(CommandKind, "build"))
    assert not every.enabled(cast(CommandKind, ""))


def test_enabled_kinds_cannot_be_changed_after_construction() -> None:
    passed = [CommandKind.DEPLOY]
    enabled = EnabledKinds(passed)
    passed[0] = CommandKind.ROLLBACK
    passed.append(CommandKind.PROMOTE)
    assert isinstance(enabled.kinds(), tuple)
    assert not enabled.enabled(CommandKind.ROLLBACK)
    assert not enabled.enabled(CommandKind.PROMOTE)
    assert enabled.enabled(CommandKind.DEPLOY)
    assert enabled.kinds() == (CommandKind.DEPLOY,)
