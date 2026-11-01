"""The insert and replay decisions of an acceptance (decision 0004 section 4) and the worker's fence
(decision 0004 section 7)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import timedelta

import pytest
from tests.unit.core.application.operations.fakes import (
    ID_COMMAND,
    ID_COMMAND2,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    REQUESTER,
    T0,
    T1,
    project_hosting,
    stored_command,
)

from provisioning.core.application import (
    AcceptCommandParams,
    Fence,
    insert_decision,
    replay_decision,
)
from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    NIL_ID,
    CommandKind,
    CommandNumberOverflow,
    CommandRequest,
    CommandStatus,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidRequester,
    ProjectHosting,
    ProjectMismatch,
)

# ---- fixtures ----

T2 = T0 + timedelta(minutes=2)


def _accept_params() -> AcceptCommandParams:
    return AcceptCommandParams(
        request=CommandRequest(
            project_id=ID_PROJECT,
            kind=CommandKind.DEPLOY,
            release_id=ID_RELEASE,
            idempotency_key="key-1",
        ),
        command_id=ID_COMMAND2,
        requested_by_subject=REQUESTER,
        now=T1,
        correlation_id="corr-1",
        deadline_at=T2,
    )


# The command to insert carries the command number after the advance (decision 0004 section 4
# steps 6 and 7), the new command ID, the request and the acceptance time, and is a valid accepted
# row.
@pytest.mark.parametrize("current", [0, 1, 41, MAX_COMMAND_NUMBER - 1])
def test_insert_decision_builds_the_command_with_the_advanced_number(current: int) -> None:
    params = _accept_params()
    decision = insert_decision(params, project_hosting(current))
    assert decision.insert
    cmd = decision.command
    assert cmd.command_number == current + 1
    assert cmd.id == params.command_id
    assert cmd.project_id == params.request.project_id
    assert cmd.kind is CommandKind.DEPLOY
    assert cmd.release_id == params.request.release_id
    assert cmd.idempotency_key == params.request.idempotency_key
    assert cmd.status is CommandStatus.ACCEPTED
    assert cmd.created_at == params.now
    assert cmd.updated_at == params.now
    cmd.validate()  # the command to insert is a valid row


# The command to insert records who issued it (decision 0001 section 4, OD-53), and the requester is
# not part of the request fingerprint: two requesters with the same request get the same one.
def test_insert_decision_stores_the_requester() -> None:
    cmd = insert_decision(_accept_params(), project_hosting(3)).command
    assert cmd.requested_by_subject == REQUESTER

    other = dataclasses.replace(_accept_params(), requested_by_subject="user-2")
    got = insert_decision(other, project_hosting(3)).command
    assert got.requested_by_subject == "user-2"
    assert got.request_fingerprint == cmd.request_fingerprint


# A hosting command comes from a Payment event and has no requester: an empty requester becomes
# none, and a hosting command with one is refused.
def test_insert_decision_hosting_command_has_no_requester() -> None:
    params = dataclasses.replace(
        _accept_params(),
        request=CommandRequest(
            project_id=ID_PROJECT,
            kind=CommandKind.SUSPEND_HOSTING,
            release_id=None,
            idempotency_key="evt-1",
        ),
        requested_by_subject="",
    )
    decision = insert_decision(params, project_hosting(3))
    assert decision.command.requested_by_subject is None

    with pytest.raises(InvalidRequester):
        insert_decision(
            dataclasses.replace(params, requested_by_subject=REQUESTER), project_hosting(3)
        )


_MALFORMED_REQUESTERS = {
    "empty": "",
    "leading space": " user-1",
    "trailing space": "user-1 ",
    "control character": "user\x00-1",
    "line feed": "user-1\n",
    "over 255 bytes": "u" * 256,
    # "user-\xff": a Python str holding a lone surrogate cannot be UTF-8.
    "invalid UTF-8": "user-\udcff",
    "only whitespace": "   ",
    "trailing tab": "user-1\t",
    "leading no-break sp": "\u00a0user-1",
}


# A client command without a requester, or with one that breaks the rule, is refused and nothing is
# returned to insert; the error never repeats the requester.
@pytest.mark.parametrize(
    "by", list(_MALFORMED_REQUESTERS.values()), ids=list(_MALFORMED_REQUESTERS)
)
def test_insert_decision_refuses_a_malformed_requester(by: str) -> None:
    params = dataclasses.replace(_accept_params(), requested_by_subject=by)
    with pytest.raises(InvalidRequester) as exc_info:
        insert_decision(params, project_hosting(3))
    if by:
        assert by not in str(exc_info.value), "the error repeats the requester"


def test_insert_decision_accepts_the_longest_requester() -> None:
    params = dataclasses.replace(_accept_params(), requested_by_subject="u" * 255)
    decision = insert_decision(params, project_hosting(3))
    assert decision.command.requested_by_subject == "u" * 255


# At the maximum the counter cannot advance; nothing is returned to insert.
def test_insert_decision_refuses_at_the_maximum_command_number() -> None:
    """FR-14.7: insert decision refuses at the maximum command number."""
    with pytest.raises(CommandNumberOverflow):
        insert_decision(_accept_params(), project_hosting(MAX_COMMAND_NUMBER))


def _hosting_of_project2() -> ProjectHosting:
    return dataclasses.replace(project_hosting(3), project_id=ID_PROJECT2)


def _no_command_id() -> AcceptCommandParams:
    return dataclasses.replace(_accept_params(), command_id=NIL_ID)


def _bad_key() -> AcceptCommandParams:
    params = _accept_params()
    return dataclasses.replace(
        params, request=dataclasses.replace(params.request, idempotency_key=" padded ")
    )


def _no_release() -> AcceptCommandParams:
    params = _accept_params()
    return dataclasses.replace(params, request=dataclasses.replace(params.request, release_id=None))


@pytest.mark.parametrize(
    ("params", "hosting", "want"),
    [
        (_accept_params, _hosting_of_project2, ProjectMismatch),
        (_no_command_id, lambda: project_hosting(3), InvalidId),
        (_bad_key, lambda: project_hosting(3), InvalidIdempotencyKey),
        (_no_release, lambda: project_hosting(3), InvalidCommandTarget),
        (_accept_params, lambda: project_hosting(-1), InvalidCommandNumber),
    ],
    ids=[
        "hosting row of another project",
        "no command ID",
        "invalid key",
        "deploy without a release",
        "command number out of range",
    ],
)
def test_insert_decision_refuses_inconsistent_input(
    params: Callable[[], AcceptCommandParams],
    hosting: Callable[[], ProjectHosting],
    want: type[Exception],
) -> None:
    with pytest.raises(want):
        insert_decision(params(), hosting())


# A replay returns the stored command as it is, whatever its status, and writes nothing.
def test_replay_decision_returns_the_stored_command() -> None:
    stored = (
        stored_command(7)
        .transition(CommandStatus.RUNNING, "", T1)
        .transition(CommandStatus.FAILED, "executor_unavailable", T2)
    )
    decision = replay_decision(stored)
    assert not decision.insert
    got = decision.command
    assert got == stored
    assert got.status is CommandStatus.FAILED
    assert got.command_number == 7
    assert got.failure_code == "executor_unavailable"
    assert got.updated_at == T2


def _valid_fence() -> Fence:
    return Fence(project_id=ID_PROJECT, command_id=ID_COMMAND, command_number=1, claim_number=1)


@pytest.mark.parametrize(
    "fence",
    [
        _valid_fence,
        lambda: dataclasses.replace(_valid_fence(), command_number=MAX_COMMAND_NUMBER),
        # The deadline failer fences a command that was never claimed with the claim number of a
        # fresh dispatch row.
        lambda: dataclasses.replace(_valid_fence(), claim_number=0),
    ],
    ids=["valid", "maximum command number", "claim number of an unclaimed row"],
)
def test_fence_validate_accepts(fence: Callable[[], Fence]) -> None:
    fence().validate()


@pytest.mark.parametrize(
    ("fence", "want"),
    [
        (lambda: dataclasses.replace(_valid_fence(), project_id=NIL_ID), InvalidId),
        (lambda: dataclasses.replace(_valid_fence(), command_id=NIL_ID), InvalidId),
        (lambda: dataclasses.replace(_valid_fence(), command_number=0), InvalidCommandNumber),
        (lambda: dataclasses.replace(_valid_fence(), command_number=-1), InvalidCommandNumber),
        (
            lambda: dataclasses.replace(_valid_fence(), command_number=MAX_COMMAND_NUMBER + 1),
            InvalidCommandNumber,
        ),
        (
            lambda: Fence(project_id=NIL_ID, command_id=NIL_ID, command_number=0, claim_number=0),
            InvalidId,
        ),
    ],
    ids=[
        "no project ID",
        "no command ID",
        "command number 0",
        "negative command number",
        "command number over the maximum",
        "zero value",
    ],
)
def test_fence_validate_refuses(fence: Callable[[], Fence], want: type[Exception]) -> None:
    with pytest.raises(want):
        fence().validate()
