"""Requester rules of decision 0001 section 4 (owner, OD-53); the requester is stored as
``requested_by_subject``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    InvalidIdempotencyKey,
    InvalidRequester,
    is_error,
    new_command,
    validate_idempotency_key,
    validate_requester,
)

_T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
_T1 = _T0 + timedelta(minutes=1)
_ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
_ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
_ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")

# Command status expectation, by hand from decision 0001 section 4. running -> skipped is legal
# only for hosting kinds.
_LEGAL_COMMAND_PAIRS = [
    (CommandStatus.ACCEPTED, CommandStatus.RUNNING),
    (CommandStatus.ACCEPTED, CommandStatus.FAILED),
    (CommandStatus.RUNNING, CommandStatus.SUCCEEDED),
    (CommandStatus.RUNNING, CommandStatus.FAILED),
    (CommandStatus.RUNNING, CommandStatus.SKIPPED),
]


def _request_for(kind: CommandKind) -> CommandRequest:
    if kind.names_no_release():
        return CommandRequest(
            project_id=_ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
        )
    return CommandRequest(
        project_id=_ID_PROJECT, kind=kind, release_id=_ID_RELEASE, idempotency_key="key-1"
    )


def _requester_for(kind: CommandKind) -> str | None:
    """The token subject for a client kind, none for a hosting kind."""
    return None if kind.names_no_release() else "user-7f3a"


def _command_in(kind: CommandKind, status: CommandStatus) -> Command:
    """An accepted command of the kind with command number 4, moved to ``status`` with a failure
    code where the status needs one."""
    c = new_command(
        command_id=_ID_COMMAND,
        request=_request_for(kind),
        requested_by_subject=_requester_for(kind),
        command_number=4,
        now=_T0,
    )
    code = "earlier_failure" if status in (CommandStatus.FAILED, CommandStatus.SKIPPED) else None
    return replace(c, status=status, failure_code=code)


@pytest.mark.parametrize(
    "subject",
    [
        "a",
        "user-7f3a",
        "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
        "svc account",
        "s" * 255,
        "ユーザー",
    ],
    ids=["a", "user", "uuid", "space inside", "255 bytes", "multibyte"],
)
def test_validate_requester_accepts(subject: str) -> None:
    validate_requester(subject)


_BAD_REQUESTERS = {
    "empty": "",
    "256 bytes": "s" * 256,
    "256 multibyte": "é" * 128,
    "leading space": " user",
    "trailing space": "user ",
    "only spaces": "   ",
    "newline": "us\ner",
    "nul": "us\x00er",
    "del": "us\x7fer",
    "c1 control": "us\u0085er",
    # "us\xffer" is invalid UTF-8; a Python str holding a lone surrogate cannot be UTF-8.
    "invalid utf-8": "us\udcffer",
}


@pytest.mark.parametrize("subject", list(_BAD_REQUESTERS.values()), ids=list(_BAD_REQUESTERS))
def test_validate_requester(subject: str) -> None:
    with pytest.raises(InvalidRequester) as exc_info:
        validate_requester(subject)
    # Its own error, not the idempotency key's, although the character rule is shared.
    assert not is_error(exc_info.value, InvalidIdempotencyKey)


def test_validate_requester_error_has_no_value() -> None:
    marker = "SECRET-SUBJECT"
    with pytest.raises(InvalidRequester) as exc_info:
        validate_requester(" " + marker)
    assert marker not in str(exc_info.value)


# A hosting command comes from a Payment event and has no requester; every other kind needs one.
_NEW_COMMAND_CASES: list[tuple[str, CommandKind, str | None]] = []
for _kind in CommandKind:
    if _kind in (  # names_no_release(), by hand
        CommandKind.SUSPEND_HOSTING,
        CommandKind.RESUME_HOSTING,
        CommandKind.SYNC_DOMAINS,
    ):
        _bad: dict[str, str | None] = {"with requester": "user-7f3a", "with empty requester": ""}
    else:
        _bad = {
            "without requester": None,
            "empty": "",
            "padded": " user",
            "control": "u\tser",
            "too long": "s" * 256,
        }
    _NEW_COMMAND_CASES.extend((f"{_kind}/{name}", _kind, value) for name, value in _bad.items())


@pytest.mark.parametrize(
    ("kind", "requester"),
    [(k, r) for _, k, r in _NEW_COMMAND_CASES],
    ids=[i for i, _, _ in _NEW_COMMAND_CASES],
)
def test_new_command_requester(kind: CommandKind, requester: str | None) -> None:
    with pytest.raises(InvalidRequester):
        new_command(
            command_id=_ID_COMMAND,
            request=_request_for(kind),
            requested_by_subject=requester,
            command_number=1,
            now=_T0,
        )


# Keys and requesters refuse the characters a failure summary refuses: line and paragraph
# separators and the bidirectional controls (validation finding VR-06). Their neighbours, and the
# other format characters such as U+200B, stay legal.
_REFUSED_RUNES = [
    0x2028,
    0x2029,
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
]
_LEGAL_RUNES = [0x200B, 0x200C, 0x200D, 0x061B, 0x061D, 0x2010, 0x202F, 0x206A, 0x00AD]
_VALIDATORS: dict[str, tuple[Callable[[str], None], type[Exception]]] = {
    "validate_idempotency_key": (validate_idempotency_key, InvalidIdempotencyKey),
    "validate_requester": (validate_requester, InvalidRequester),
}


@pytest.mark.parametrize(("validate", "want"), list(_VALIDATORS.values()), ids=list(_VALIDATORS))
def test_key_and_requester_refuse_separators_and_bidi_controls(
    validate: Callable[[str], None], want: type[Exception]
) -> None:
    for r in _REFUSED_RUNES:
        c = chr(r)
        for s in ("a" + c + "b", c + "ab", "ab" + c):
            with pytest.raises(want):
                validate(s)
    for r in _LEGAL_RUNES:
        validate("a" + chr(r) + "b")


# The requester is recorded, not hashed: a replay by another principal returns the stored command
# with its original requester.
@pytest.mark.parametrize(
    "kind",
    [CommandKind.DEPLOY, CommandKind.PROMOTE, CommandKind.ROLLBACK],
    ids=["deploy", "promote", "rollback"],
)
def test_requester_not_hashed_and_replay(kind: CommandKind) -> None:
    req = _request_for(kind)
    a = new_command(
        command_id=_ID_COMMAND,
        request=req,
        requested_by_subject="alice",
        command_number=1,
        now=_T0,
    )
    b = new_command(
        command_id=_ID_COMMAND,
        request=req,
        requested_by_subject="bob",
        command_number=1,
        now=_T0,
    )
    assert a.request_fingerprint == b.request_fingerprint, "requester changed the fingerprint"
    stored = a
    # bob retries alice's request with the same key: a replay of the stored command.
    stored.check_replay(req)
    assert stored.requested_by_subject == "alice"
    assert stored == a


# The requester never changes: no command transition touches it.
def test_command_transitions_keep_requester() -> None:
    for kind in CommandKind:
        for from_, to in _LEGAL_COMMAND_PAIRS:
            if to is CommandStatus.SKIPPED and not kind.is_hosting():
                continue
            c = _command_in(kind, from_)
            code = "x" if to in (CommandStatus.FAILED, CommandStatus.SKIPPED) else ""
            moved = c.transition(to, code, _T1)
            assert moved.status is to
            assert moved.requested_by_subject == c.requested_by_subject, f"{kind} {from_}->{to}"


_VALIDATE_CASES: dict[str, tuple[CommandKind, str | None]] = {
    "deploy without requester": (CommandKind.DEPLOY, None),
    "rollback without requester": (CommandKind.ROLLBACK, None),
    "promote empty requester": (CommandKind.PROMOTE, ""),
    "promote padded requester": (CommandKind.PROMOTE, "user "),
    "suspend with requester": (CommandKind.SUSPEND_HOSTING, "user"),
    "resume with requester": (CommandKind.RESUME_HOSTING, "user"),
}


@pytest.mark.parametrize(
    ("kind", "requester"), list(_VALIDATE_CASES.values()), ids=list(_VALIDATE_CASES)
)
def test_command_validate_requester(kind: CommandKind, requester: str | None) -> None:
    cmd = replace(_command_in(kind, CommandStatus.ACCEPTED), requested_by_subject=requester)
    with pytest.raises(InvalidRequester):
        cmd.validate()
