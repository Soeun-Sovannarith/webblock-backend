"""The prototype's development identity (decision 0014 section 3, "Prototype settings"): a static
bearer token per caller, compared in constant time against a configured file. The file's contents
are never logged or put into an error."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import Principal, Unauthenticated
from provisioning.infrastructure.standins.dev_tokens import (
    DevTokenFileError,
    PrototypeDevTokenIdentity,
)

ALICE = "dev-token-alice-0123456789abcdefghijklmnop"
BOB = "dev-token-bob-0123456789abcdefghijklmnopqrs"


def _file(entries: list[dict[str, object]]) -> bytes:
    return json.dumps({"tokens": entries}).encode()


def _identity() -> PrototypeDevTokenIdentity:
    return PrototypeDevTokenIdentity.from_json(
        _file(
            [
                {"token": ALICE, "subject": "alice", "scopes": ["prov.deploy", "prov.read"]},
                {"token": BOB, "subject": "agent:bob", "scopes": []},
            ]
        )
    )


@pytest.mark.asyncio
async def test_a_good_token_names_its_subject_and_scopes() -> None:
    identity = _identity()
    assert await identity.verify(ALICE) == Principal(
        subject="alice", scopes=("prov.deploy", "prov.read")
    )
    assert await identity.verify(BOB) == Principal(subject="agent:bob", scopes=())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    ["", ALICE[:-1], ALICE + "x", ALICE.upper(), "x" * 5000, "dev-token-unknown-" + "0" * 30],
)
async def test_any_other_token_is_unauthenticated(raw: str) -> None:
    with pytest.raises(Unauthenticated) as exc_info:
        await _identity().verify(raw)
    assert raw[:20] not in str(exc_info.value) or raw == ""
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (b"not json", "is not a JSON object"),
        (b'{"tokens": []}', "holds no token"),
        (b'{"tokens": [], "tokens": []}', "duplicate key"),
        (b'{"tokens": {}}', "tokens must be a list"),
        (b'{"tokens": [], "extra": 1}', "unknown key"),
        (_file([{"token": ALICE, "subject": "alice"}]), "entry 0"),
        (_file([{"token": ALICE, "subject": "alice", "scopes": [], "x": 1}]), "entry 0"),
        (_file([{"token": "short-token", "subject": "alice", "scopes": []}]), "entry 0: token"),
        (_file([{"token": ALICE + " ", "subject": "alice", "scopes": []}]), "entry 0: token"),
        (_file([{"token": ALICE, "subject": "", "scopes": []}]), "entry 0: subject"),
        (_file([{"token": ALICE, "subject": "a\nb", "scopes": []}]), "entry 0: subject"),
        (_file([{"token": ALICE, "subject": "a", "scopes": ["a b"]}]), "entry 0: scopes"),
        (_file([{"token": ALICE, "subject": "a", "scopes": "prov"}]), "entry 0: scopes"),
        (
            _file(
                [
                    {"token": ALICE, "subject": "a", "scopes": []},
                    {"token": ALICE, "subject": "b", "scopes": []},
                ]
            ),
            "entry 1: token is listed twice",
        ),
    ],
)
def test_a_bad_file_is_refused_without_its_contents(raw: bytes, fragment: str) -> None:
    with pytest.raises(DevTokenFileError) as exc_info:
        PrototypeDevTokenIdentity.from_json(raw)
    text = str(exc_info.value)
    assert fragment in text
    assert ALICE not in text
    assert "short-token" not in text
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None or exc_info.value.__suppress_context__


def test_reading_a_missing_file_names_no_contents(tmp_path: Path) -> None:
    with pytest.raises(DevTokenFileError) as exc_info:
        PrototypeDevTokenIdentity.from_file(tmp_path / "missing.json")
    assert "cannot be read" in str(exc_info.value)


@pytest.mark.asyncio
async def test_reading_a_file(tmp_path: Path) -> None:
    path = tmp_path / "tokens.json"
    path.write_bytes(_file([{"token": ALICE, "subject": "alice", "scopes": []}]))
    principal = await PrototypeDevTokenIdentity.from_file(path).verify(ALICE)
    assert principal.subject == "alice"


def test_the_identity_prints_no_token() -> None:
    identity = _identity()
    assert ALICE not in repr(identity)
    assert ALICE not in str(identity)


# --- task PA: the user's platform ID (decision 0017 section 2) ---------------------------------

USER = "0199e7a0-0000-7000-8000-0000000000aa"


@pytest.mark.asyncio
async def test_an_entry_may_name_the_users_platform_id() -> None:
    identity = PrototypeDevTokenIdentity.from_json(
        _file(
            [
                {"token": ALICE, "subject": "alice", "scopes": ["prov.read"], "user_id": USER},
                {"token": BOB, "subject": "agent:bob", "scopes": []},
            ]
        )
    )
    alice = await identity.verify(ALICE)
    assert alice == Principal(subject="alice", scopes=("prov.read",), user_id=UUID(USER))
    assert (await identity.verify(BOB)).user_id is None


@pytest.mark.parametrize(
    "user_id",
    [
        USER.upper(),
        USER.replace("-", ""),
        "00000000-0000-0000-0000-000000000000",
        "not-a-uuid",
        "",
        42,
        None,
        [USER],
    ],
)
def test_a_user_id_that_is_not_a_canonical_uuid_refuses_the_file(user_id: object) -> None:
    raw = _file([{"token": ALICE, "subject": "alice", "scopes": [], "user_id": user_id}])
    with pytest.raises(DevTokenFileError) as exc_info:
        PrototypeDevTokenIdentity.from_json(raw)
    text = str(exc_info.value)
    assert "entry 0: user_id" in text
    assert ALICE not in text
    if isinstance(user_id, str) and user_id:
        assert user_id not in text
