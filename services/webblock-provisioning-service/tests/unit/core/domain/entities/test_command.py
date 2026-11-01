"""The request fingerprint, the commands row and the acceptance preconditions (decision 0001 section
4)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest

from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    NIL_ID,
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    DomainError,
    IdempotencyConflict,
    IllegalTransition,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidFailureCode,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidImageDigest,
    InvalidRequester,
    InvalidRequestFingerprint,
    MissingFailureCode,
    OfflineReason,
    ProjectMismatch,
    UnknownEnumValue,
    compute_request_fingerprint,
    legal_command_transition,
    new_command,
    parse_id,
    parse_image_digest,
    parse_release_status,
    parse_request_fingerprint,
    validate_idempotency_key,
)

# ---- fixtures ----

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)

ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
ID_PROJECT2 = UUID("0190a1b2-0000-7000-8000-000000000002")
ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
ID_RELEASE2 = UUID("0190a1b2-0000-7000-8000-0000000000a2")
ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")

# Hand-computed vectors (decision 0001 section 4), produced outside Python with
#
#   printf 'provisioning.command.v1\ndeploy\n0190a1b2-0000-7000-8000-000000000001\n'\
#   '0190a1b2-0000-7000-8000-0000000000a1\n' | sha256sum
#   printf 'provisioning.command.v1\nsuspend_hosting\n'\
#   '0190a1b2-0000-7000-8000-000000000001\npay-evt-42\n' | sha256sum
VECTOR_DEPLOY_FINGERPRINT = "110fe85db7db6ec954b2ed3cb1dc1ce235ad54f815a2ab113d91c532a3751aa4"
VECTOR_SUSPEND_FINGERPRINT = "2b22fa69dc789fcfc0fdf6cddf321e003cd1496817b86b9fe77765ebe044176b"

# The hosting kinds, by hand (decision 0001 section 4): the test does not take them from the code
# under test.
HOSTING_KINDS = frozenset({CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING})

ALL_KINDS = list(CommandKind)
ALL_COMMAND_STATUSES = list(CommandStatus)


# Kinds that name no release and have no requester: the hosting kinds and sync_domains (decision
# 0014 section 16 item 1). Only the hosting kinds may end skipped.
NO_RELEASE_KINDS = HOSTING_KINDS | {CommandKind.SYNC_DOMAINS}


def _no_release(kind: CommandKind) -> bool:
    return kind in NO_RELEASE_KINDS


def _is_hosting(kind: CommandKind) -> bool:
    return kind in HOSTING_KINDS


def _release_request(kind: CommandKind) -> CommandRequest:
    return CommandRequest(
        project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
    )


def _hosting_request(kind: CommandKind) -> CommandRequest:
    return CommandRequest(
        project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
    )


def _request_for(kind: CommandKind) -> CommandRequest:
    if _no_release(kind):
        return _hosting_request(kind)
    return _release_request(kind)


def _requester_for(kind: CommandKind) -> str | None:
    """The requester a command of the kind records: the token subject for a client kind, none for
    a hosting kind."""
    if _no_release(kind):
        return None
    return "user-7f3a"


def _new_command(kind: CommandKind) -> Command:
    """An accepted command of the kind with command number 4."""
    return new_command(
        command_id=ID_COMMAND,
        request=_request_for(kind),
        requested_by_subject=_requester_for(kind),
        command_number=4,
        now=T0,
    )


def _command_in(kind: CommandKind, status: CommandStatus) -> Command:
    """A command of the kind in the status, with a failure code where the status needs one."""
    c = replace(_new_command(kind), status=status)
    if status in (CommandStatus.FAILED, CommandStatus.SKIPPED):
        c = replace(c, failure_code="earlier_failure")
    return c


# ---- request fingerprint ----


def test_compute_request_hash_vectors() -> None:
    assert compute_request_fingerprint(_release_request(CommandKind.DEPLOY)) == (
        VECTOR_DEPLOY_FINGERPRINT
    )
    got = compute_request_fingerprint(_hosting_request(CommandKind.SUSPEND_HOSTING))
    assert got == VECTOR_SUSPEND_FINGERPRINT
    assert parse_request_fingerprint(got) == got


@pytest.mark.parametrize("kind", ALL_KINDS, ids=[k.value for k in ALL_KINDS])
def test_request_hash_differs_exactly_on_kind_project_target(kind: CommandKind) -> None:
    """The fingerprint differs exactly when kind, project or target differ. The idempotency key of
    a deploy, promote or rollback is not hashed; for a hosting kind the key is the target."""
    base = _request_for(kind)
    h = compute_request_fingerprint(base)

    other_key = replace(base, idempotency_key="key-2")
    if _no_release(kind):
        assert compute_request_fingerprint(other_key) != h, "another Payment event key, same hash"
    else:
        assert compute_request_fingerprint(other_key) == h, "the idempotency key changed the hash"

    assert compute_request_fingerprint(replace(base, project_id=ID_PROJECT2)) != h
    if not _no_release(kind):
        assert compute_request_fingerprint(replace(base, release_id=ID_RELEASE2)) != h
    for other in ALL_KINDS:
        if other == kind or _no_release(other) != _no_release(kind):
            continue
        assert compute_request_fingerprint(replace(base, kind=other)) != h, f"{kind} == {other}"
    # Repeatable.
    assert compute_request_fingerprint(base) == h


_REJECTED_REQUESTS: dict[str, tuple[CommandRequest, type[DomainError]]] = {
    "zero project": (replace(_release_request(CommandKind.DEPLOY), project_id=NIL_ID), InvalidId),
    "unknown kind": (
        replace(_release_request(CommandKind.DEPLOY), kind=cast(CommandKind, "build")),
        UnknownEnumValue,
    ),
    "deploy without release": (
        replace(_release_request(CommandKind.DEPLOY), release_id=None),
        InvalidCommandTarget,
    ),
    "rollback without release": (
        replace(_release_request(CommandKind.ROLLBACK), release_id=None),
        InvalidCommandTarget,
    ),
    "zero release": (replace(_release_request(CommandKind.PROMOTE), release_id=NIL_ID), InvalidId),
    "suspend naming a release": (
        replace(_hosting_request(CommandKind.SUSPEND_HOSTING), release_id=ID_RELEASE),
        InvalidCommandTarget,
    ),
    "resume naming a zero release": (
        replace(_hosting_request(CommandKind.RESUME_HOSTING), release_id=NIL_ID),
        InvalidCommandTarget,
    ),
    "empty key": (
        replace(_release_request(CommandKind.DEPLOY), idempotency_key=""),
        InvalidIdempotencyKey,
    ),
    # A line feed in a Payment event key would break the framing.
    "hosting key with line feed": (
        replace(_hosting_request(CommandKind.SUSPEND_HOSTING), idempotency_key="a\nb"),
        InvalidIdempotencyKey,
    ),
}


@pytest.mark.parametrize(
    ("request_", "want"), list(_REJECTED_REQUESTS.values()), ids=list(_REJECTED_REQUESTS)
)
def test_compute_request_hash_rejects(request_: CommandRequest, want: type[DomainError]) -> None:
    with pytest.raises(want):
        compute_request_fingerprint(request_)


# ---- new_command ----


@pytest.mark.parametrize("kind", ALL_KINDS, ids=[k.value for k in ALL_KINDS])
def test_new_command(kind: CommandKind) -> None:
    req = _request_for(kind)
    requester = _requester_for(kind)
    c = new_command(
        command_id=ID_COMMAND,
        request=req,
        requested_by_subject=requester,
        command_number=7,
        now=T0,
    )
    assert c.id == ID_COMMAND
    assert c.project_id == req.project_id
    assert c.kind == kind
    assert c.idempotency_key == req.idempotency_key
    assert c.request_fingerprint == compute_request_fingerprint(req)
    assert c.command_number == 7
    assert c.status == CommandStatus.ACCEPTED
    assert c.failure_code is None
    assert c.created_at == T0
    assert c.updated_at == T0
    assert _no_release(kind) == (c.release_id is None)
    assert c.release_id == req.release_id
    assert _no_release(kind) == (c.requested_by_subject is None)
    assert c.requested_by_subject == requester
    c.validate()


_BAD_NEW_COMMANDS: dict[str, tuple[UUID, CommandRequest, str | None, int, type[DomainError]]] = {
    "zero command id": (NIL_ID, _release_request(CommandKind.DEPLOY), "u", 1, InvalidId),
    "fencing 0": (ID_COMMAND, _release_request(CommandKind.DEPLOY), "u", 0, InvalidCommandNumber),
    "fencing 2^53": (
        ID_COMMAND,
        _release_request(CommandKind.DEPLOY),
        "u",
        MAX_COMMAND_NUMBER + 1,
        InvalidCommandNumber,
    ),
    "hosting naming a release": (
        ID_COMMAND,
        CommandRequest(
            project_id=ID_PROJECT,
            kind=CommandKind.RESUME_HOSTING,
            release_id=ID_RELEASE,
            idempotency_key="e",
        ),
        None,
        1,
        InvalidCommandTarget,
    ),
}


@pytest.mark.parametrize(
    ("command_id", "request_", "requester", "number", "want"),
    list(_BAD_NEW_COMMANDS.values()),
    ids=list(_BAD_NEW_COMMANDS),
)
def test_new_command_rejects(
    command_id: UUID,
    request_: CommandRequest,
    requester: str | None,
    number: int,
    want: type[DomainError],
) -> None:
    with pytest.raises(want):
        new_command(
            command_id=command_id,
            request=request_,
            requested_by_subject=requester,
            command_number=number,
            now=T0,
        )


# ---- command status transitions ----

# Command status expectation, by hand from decision 0001 section 4. running -> skipped is legal
# only for hosting kinds.
LEGAL_COMMAND_PAIRS = frozenset(
    {
        (CommandStatus.ACCEPTED, CommandStatus.RUNNING),
        (CommandStatus.ACCEPTED, CommandStatus.FAILED),
        (CommandStatus.RUNNING, CommandStatus.SUCCEEDED),
        (CommandStatus.RUNNING, CommandStatus.FAILED),
        (CommandStatus.RUNNING, CommandStatus.SKIPPED),
    }
)

_STATUS_PAIRS = [(f, t) for f in ALL_COMMAND_STATUSES for t in ALL_COMMAND_STATUSES]


def test_command_status_matrix_size() -> None:
    assert len(_STATUS_PAIRS) == 25


@pytest.mark.parametrize(
    ("from_", "to"), _STATUS_PAIRS, ids=[f"{f.value}->{t.value}" for f, t in _STATUS_PAIRS]
)
def test_command_status_matrix(from_: CommandStatus, to: CommandStatus) -> None:
    want = (from_, to) in LEGAL_COMMAND_PAIRS
    assert legal_command_transition(from_, to) == want
    for kind in ALL_KINDS:
        legal = want and (to != CommandStatus.SKIPPED or _is_hosting(kind))
        code = "entitlement_restored" if to in (CommandStatus.FAILED, CommandStatus.SKIPPED) else ""
        c = _command_in(kind, from_)
        if legal:
            got = c.transition(to, code, T1)
            assert got.status == to, kind
            assert got.updated_at == T1, kind
            continue
        with pytest.raises(IllegalTransition):
            c.transition(to, code, T1)


def test_command_status_matrix_unknown_status() -> None:
    assert not legal_command_transition(cast(CommandStatus, "bogus"), CommandStatus.RUNNING)
    assert not legal_command_transition(CommandStatus.RUNNING, cast(CommandStatus, "bogus"))


_NEED_CODE = [
    (CommandKind.DEPLOY, CommandStatus.ACCEPTED, CommandStatus.FAILED),
    (CommandKind.PROMOTE, CommandStatus.RUNNING, CommandStatus.FAILED),
    (CommandKind.SUSPEND_HOSTING, CommandStatus.RUNNING, CommandStatus.SKIPPED),
    (CommandKind.RESUME_HOSTING, CommandStatus.RUNNING, CommandStatus.FAILED),
]

_BAD_CODES: dict[str, type[DomainError]] = {
    "": MissingFailureCode,
    "  ": MissingFailureCode,
    "Bad": InvalidFailureCode,
    "a-b": InvalidFailureCode,
}


@pytest.mark.parametrize(
    ("kind", "from_", "to"),
    _NEED_CODE,
    ids=[f"{k.value}/{f.value}->{t.value}" for k, f, t in _NEED_CODE],
)
def test_command_failure_code(kind: CommandKind, from_: CommandStatus, to: CommandStatus) -> None:
    for code, want in _BAD_CODES.items():
        cmd = _command_in(kind, from_)
        with pytest.raises(want):
            cmd.transition(to, code, T1)
    got = _command_in(kind, from_).transition(to, "entitlement_lost", T1)
    assert got.failure_code == "entitlement_lost"


_NO_CODE = [
    (CommandKind.DEPLOY, CommandStatus.ACCEPTED, CommandStatus.RUNNING),
    (CommandKind.ROLLBACK, CommandStatus.RUNNING, CommandStatus.SUCCEEDED),
]


@pytest.mark.parametrize(
    ("kind", "from_", "to"),
    _NO_CODE,
    ids=[f"{k.value}/{f.value}->{t.value}" for k, f, t in _NO_CODE],
)
def test_command_failure_code_absent_otherwise(
    kind: CommandKind, from_: CommandStatus, to: CommandStatus
) -> None:
    cmd = _command_in(kind, from_)
    with pytest.raises(InvalidFailureCode):
        cmd.transition(to, "some_code", T1)
    got = cmd.transition(to, "", T1)
    assert got.status == to
    assert got.failure_code is None


# ---- replay ----


@pytest.mark.parametrize("kind", ALL_KINDS, ids=[k.value for k in ALL_KINDS])
def test_command_replay_and_conflict(kind: CommandKind) -> None:
    req = _request_for(kind)
    for status in ALL_COMMAND_STATUSES:
        # A replay returns the stored command whatever its status.
        _command_in(kind, status).check_replay(req)

    stored = _new_command(kind)
    for other in ALL_KINDS:
        if other == kind:
            continue
        k = replace(_request_for(other), idempotency_key=req.idempotency_key)
        with pytest.raises(IdempotencyConflict):
            stored.check_replay(k)
    if not _no_release(kind):
        with pytest.raises(IdempotencyConflict):
            stored.check_replay(replace(req, release_id=ID_RELEASE2))
    # The fingerprint is what is compared: a stored command whose fingerprint differs is a
    # conflict even when every other column matches.
    tampered = replace(stored, request_fingerprint="0" * 64)
    with pytest.raises(IdempotencyConflict):
        tampered.check_replay(req)
    # Another key is another slot, never a replay.
    with pytest.raises(IdempotencyConflict):
        stored.check_replay(replace(req, idempotency_key="other-key"))
    with pytest.raises(ProjectMismatch):
        stored.check_replay(replace(req, project_id=ID_PROJECT2))
    # An invalid request is never a match, even against a stored command holding the same values.
    with pytest.raises(InvalidIdempotencyKey):
        stored.check_replay(replace(req, idempotency_key=" padded"))


def test_command_replay_zero_request_against_zero_command() -> None:
    zero_time = datetime(1, 1, 1, tzinfo=UTC)
    zero_command = Command(
        id=NIL_ID,
        project_id=NIL_ID,
        release_id=None,
        kind=cast(CommandKind, ""),
        idempotency_key="",
        request_fingerprint="",
        command_number=0,
        status=cast(CommandStatus, ""),
        created_at=zero_time,
        updated_at=zero_time,
    )
    zero_request = CommandRequest(
        project_id=NIL_ID, kind=cast(CommandKind, ""), release_id=None, idempotency_key=""
    )
    with pytest.raises(InvalidId):
        zero_command.check_replay(zero_request)


# ---- validate ----

_VALID_PAIRS = [(k, s) for k in ALL_KINDS for s in ALL_COMMAND_STATUSES]


@pytest.mark.parametrize(
    ("kind", "status"), _VALID_PAIRS, ids=[f"{k.value}/{s.value}" for k, s in _VALID_PAIRS]
)
def test_command_validate_accepts(kind: CommandKind, status: CommandStatus) -> None:
    _command_in(kind, status).validate()


_Mutate = Callable[[Command], Command]

_VALIDATE_CASES: dict[str, tuple[CommandKind, CommandStatus, _Mutate, type[DomainError]]] = {
    "zero id": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, id=NIL_ID),
        InvalidId,
    ),
    "zero project": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, project_id=NIL_ID),
        InvalidId,
    ),
    "zero release": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, release_id=NIL_ID),
        InvalidId,
    ),
    "deploy no release": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, release_id=None),
        InvalidCommandTarget,
    ),
    "suspend with release": (
        CommandKind.SUSPEND_HOSTING,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, release_id=ID_RELEASE),
        InvalidCommandTarget,
    ),
    "unknown kind": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, kind=cast(CommandKind, "build")),
        UnknownEnumValue,
    ),
    "unknown status": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, status=cast(CommandStatus, "done")),
        UnknownEnumValue,
    ),
    "bad key": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, idempotency_key=""),
        InvalidIdempotencyKey,
    ),
    "hash format": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, request_fingerprint="abc"),
        InvalidRequestFingerprint,
    ),
    "hash uppercase": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, request_fingerprint=VECTOR_DEPLOY_FINGERPRINT.upper()),
        InvalidRequestFingerprint,
    ),
    # The fingerprint is recomputed from the columns: a row whose hashed column changed fails.
    "hash of other release": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, release_id=ID_RELEASE2),
        InvalidRequestFingerprint,
    ),
    "hash of other kind": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, kind=CommandKind.PROMOTE),
        InvalidRequestFingerprint,
    ),
    "hash of other key": (
        CommandKind.RESUME_HOSTING,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, idempotency_key="pay-evt-43"),
        InvalidRequestFingerprint,
    ),
    "fencing 0": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, command_number=0),
        InvalidCommandNumber,
    ),
    "fencing 2^53": (
        CommandKind.DEPLOY,
        CommandStatus.ACCEPTED,
        lambda c: replace(c, command_number=MAX_COMMAND_NUMBER + 1),
        InvalidCommandNumber,
    ),
    "failed without code": (
        CommandKind.DEPLOY,
        CommandStatus.FAILED,
        lambda c: replace(c, failure_code=None),
        MissingFailureCode,
    ),
    "skipped without code": (
        CommandKind.SUSPEND_HOSTING,
        CommandStatus.SKIPPED,
        lambda c: replace(c, failure_code=None),
        MissingFailureCode,
    ),
    "failed bad code": (
        CommandKind.DEPLOY,
        CommandStatus.FAILED,
        lambda c: replace(c, failure_code="Bad"),
        InvalidFailureCode,
    ),
    "succeeded with code": (
        CommandKind.DEPLOY,
        CommandStatus.SUCCEEDED,
        lambda c: replace(c, failure_code="x"),
        InvalidFailureCode,
    ),
    "running with code": (
        CommandKind.DEPLOY,
        CommandStatus.RUNNING,
        lambda c: replace(c, failure_code="x"),
        InvalidFailureCode,
    ),
}


@pytest.mark.parametrize(
    ("kind", "status", "mutate", "want"), list(_VALIDATE_CASES.values()), ids=list(_VALIDATE_CASES)
)
def test_command_validate(
    kind: CommandKind, status: CommandStatus, mutate: _Mutate, want: type[DomainError]
) -> None:
    cmd = mutate(_command_in(kind, status))
    with pytest.raises(want):
        cmd.validate()


# ---- idempotency key ----

_OK_KEYS = ["a", "key-1", "0190a1b2-c3d4", "k" * 255, "with inner space", "unicode-é"]


@pytest.mark.parametrize("key", _OK_KEYS, ids=["a", "key-1", "uuid-like", "255", "inner", "é"])
def test_validate_idempotency_key(key: str) -> None:
    validate_idempotency_key(key)


_BAD_KEYS: dict[str, str] = {
    "empty": "",
    "too long": "k" * 256,
    "leading space": " k",
    "trailing space": "k ",
    "leading tab": "\tk",
    "trailing newline": "k\n",
    "nul": "a\x00b",
    "inner newline": "a\nb",
    "del": "a\x7fb",
    "c1 control": "a\u0085b",
    "only space": "   ",
    # 255 bytes counts bytes, not characters.
    "256-byte multibyte": "é" * 128,
    # "a\xffb" is invalid UTF-8; a Python str holds it as a lone surrogate (surrogateescape),
    # which has no UTF-8 encoding.
    "invalid utf-8": "a\udcffb",
}


@pytest.mark.parametrize("key", list(_BAD_KEYS.values()), ids=list(_BAD_KEYS))
def test_validate_idempotency_key_rejects(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        validate_idempotency_key(key)


# ---- error text ----

LEAK_MARKER = "SECRET-KEY-VALUE"


def test_error_text_has_no_request_content() -> None:
    with pytest.raises(InvalidIdempotencyKey) as key_exc:
        validate_idempotency_key(" " + LEAK_MARKER)
    assert LEAK_MARKER not in str(key_exc.value)

    with pytest.raises(InvalidImageDigest) as digest_exc:
        parse_image_digest("registry/" + LEAK_MARKER)
    assert LEAK_MARKER not in str(digest_exc.value)

    with pytest.raises(InvalidId) as id_exc:
        parse_id(LEAK_MARKER)
    assert LEAK_MARKER not in str(id_exc.value)

    with pytest.raises(UnknownEnumValue) as enum_exc:
        parse_release_status(LEAK_MARKER)
    assert LEAK_MARKER not in str(enum_exc.value)

    # A replay conflict never echoes the key.
    stored = _new_command(CommandKind.SUSPEND_HOSTING)
    req = replace(_hosting_request(CommandKind.SUSPEND_HOSTING), idempotency_key=LEAK_MARKER)
    with pytest.raises(DomainError) as replay_exc:
        stored.check_replay(req)
    assert LEAK_MARKER not in str(replay_exc.value)

    c = replace(_new_command(CommandKind.RESUME_HOSTING), idempotency_key=LEAK_MARKER)
    with pytest.raises(DomainError) as validate_exc:
        c.validate()
    assert LEAK_MARKER not in str(validate_exc.value)


# ---- sync_domains (decision 0014 section 16 item 1) ----


def test_sync_domains_command_names_no_release_and_has_no_requester() -> None:
    request = CommandRequest(
        project_id=ID_PROJECT,
        kind=CommandKind.SYNC_DOMAINS,
        release_id=None,
        idempotency_key="domain-evt-7",
    )
    command = new_command(
        command_id=ID_COMMAND, request=request, requested_by_subject=None, command_number=4, now=T0
    )
    assert command.release_id is None
    assert command.requested_by_subject is None
    command.validate()
    with pytest.raises(InvalidCommandTarget):
        compute_request_fingerprint(replace(request, release_id=ID_RELEASE))
    with pytest.raises(InvalidRequester):
        new_command(
            command_id=ID_COMMAND,
            request=request,
            requested_by_subject="user-7f3a",
            command_number=4,
            now=T0,
        )


def test_sync_domains_fingerprint_is_the_event_key() -> None:
    request = CommandRequest(
        project_id=ID_PROJECT,
        kind=CommandKind.SYNC_DOMAINS,
        release_id=None,
        idempotency_key="domain-evt-7",
    )
    other = replace(request, idempotency_key="domain-evt-8")
    assert compute_request_fingerprint(request) != compute_request_fingerprint(other)


def test_redeploy_requires_a_requester() -> None:
    request = _release_request(CommandKind.REDEPLOY)
    with pytest.raises(InvalidRequester):
        new_command(
            command_id=ID_COMMAND,
            request=request,
            requested_by_subject=None,
            command_number=4,
            now=T0,
        )
    c = new_command(
        command_id=ID_COMMAND,
        request=request,
        requested_by_subject="admin-1",
        command_number=4,
        now=T0,
    )
    assert c.hosting_reason is None
    c.validate()


@pytest.mark.parametrize("kind", [CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING])
def test_admin_hosting_command_needs_a_requester(kind: CommandKind) -> None:
    """Notion: requested_by_subject is required for admin_suspended and stays NULL for the system
    reasons."""
    admin = CommandRequest(
        project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="admin:k-1"
    )
    with pytest.raises(InvalidRequester):
        new_command(
            command_id=ID_COMMAND,
            request=admin,
            requested_by_subject=None,
            command_number=4,
            now=T0,
        )
    c = new_command(
        command_id=ID_COMMAND,
        request=admin,
        requested_by_subject="admin-1",
        command_number=4,
        now=T0,
    )
    assert c.hosting_reason is OfflineReason.ADMIN_SUSPENDED
    assert c.requested_by_subject == "admin-1"
    c.validate()
    with pytest.raises(InvalidRequester):
        replace(c, requested_by_subject=None).validate()
    with pytest.raises(InvalidRequester):
        replace(c, requested_by_subject="").validate()
    # A system reason has no requester.
    system = replace(admin, idempotency_key="evt-1")
    with pytest.raises(InvalidRequester):
        new_command(
            command_id=ID_COMMAND,
            request=system,
            requested_by_subject="admin-1",
            command_number=4,
            now=T0,
        )


def test_validate_requires_the_reason_of_the_key() -> None:
    suspend = _command_in(CommandKind.SUSPEND_HOSTING, CommandStatus.ACCEPTED)
    assert suspend.hosting_reason is OfflineReason.BILLING  # the fixture key is a Payment event
    suspend.validate()
    with pytest.raises(InvalidCommandTarget):
        replace(suspend, hosting_reason=None).validate()
    with pytest.raises(InvalidCommandTarget):
        replace(suspend, hosting_reason=OfflineReason.USER_SUSPENDED).validate()
    deploy = _command_in(CommandKind.DEPLOY, CommandStatus.ACCEPTED)
    assert deploy.hosting_reason is None
    with pytest.raises(InvalidCommandTarget):
        replace(deploy, hosting_reason=OfflineReason.BILLING).validate()


def test_new_command_takes_the_reason_from_the_key() -> None:
    for key, want in (
        ("project-deleted", OfflineReason.PROJECT_DELETED),
        ("owner-status:3", OfflineReason.USER_SUSPENDED),
        ("evt-1", OfflineReason.BILLING),
    ):
        c = new_command(
            command_id=ID_COMMAND,
            request=CommandRequest(
                project_id=ID_PROJECT,
                kind=CommandKind.SUSPEND_HOSTING,
                release_id=None,
                idempotency_key=key,
            ),
            requested_by_subject=None,
            command_number=1,
            now=T0,
        )
        assert c.hosting_reason is want


def test_a_resume_of_project_deleted_cannot_be_made() -> None:
    with pytest.raises(InvalidCommandTarget):
        new_command(
            command_id=ID_COMMAND,
            request=CommandRequest(
                project_id=ID_PROJECT,
                kind=CommandKind.RESUME_HOSTING,
                release_id=None,
                idempotency_key="project-deleted",
            ),
            requested_by_subject=None,
            command_number=1,
            now=T0,
        )
