"""The prototype's development identity (decision 0014 section 3, "Prototype settings"; OD-05
stays open). Selected only by IDENTITY_MODE=dev-tokens, in the prototype profile, and built only by
``hosts.shared.stand_ins`` (import-linter contract ``standins-only-through-hosts-stand-ins``;
task H3 moved it here from ``infrastructure.identity``, which holds the JWT verifier).

A configured file maps each static bearer token to a subject and its scopes:

    {"tokens": [{"token": "<at least 32 characters>", "subject": "alice", "scopes": ["..."],
                 "user_id": "<the user's platform ID, optional>"}]}

``user_id`` (task PA, decision 0017 section 2) is optional: a canonical lowercase UUID, the user's
platform ID that the Project service's access check needs. An entry without it (an operator, an
agent) has no user ID, so ``PROJECT_ACCESS_MODE=project-service`` denies it project access.

A presented token is compared in constant time against every configured token (SHA-256 digests
compared with ``hmac.compare_digest``, with no early exit), so neither the match nor its position
shows in the timing. The file's contents and the presented token are never logged or put into an
error.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID

from provisioning.core.application import Principal, Unauthenticated
from provisioning.core.domain import DomainError, parse_id, validate_requester
from provisioning.infrastructure.config import scope_token
from provisioning.infrastructure.config.files import (
    ConfigFileError,
    check_keys,
    json_object,
    read_file,
)

MIN_TOKEN_LEN: Final = 32
MAX_TOKEN_LEN: Final = 512
MAX_TOKENS: Final = 1000
_TOKEN: Final = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)
_ENTRY_KEYS: Final = frozenset({"token", "subject", "scopes"})
_USER_ID_KEY: Final = "user_id"
_WHAT: Final = "dev token file"


class DevTokenFileError(ConfigFileError):
    """The token file cannot be read or is invalid; the message never holds a value from it."""


@dataclass(frozen=True, slots=True, repr=False)
class _Entry:
    digest: bytes
    principal: Principal


class PrototypeDevTokenIdentity:
    """Verifies bearer tokens against the configured map (the HTTP transport's TokenVerifier)."""

    __slots__ = ("_entries",)

    def __init__(self, entries: Sequence[_Entry]) -> None:
        if not entries:
            raise DevTokenFileError(f"{_WHAT} holds no token")
        self._entries = tuple(entries)

    @classmethod
    def from_file(cls, path: Path) -> PrototypeDevTokenIdentity:
        """Read and check the file at ``path`` (DevTokenFileError)."""
        try:
            raw = read_file(path, _WHAT)
        except ConfigFileError as err:
            raise DevTokenFileError(str(err)) from None
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: bytes) -> PrototypeDevTokenIdentity:
        """Parse and check the file's bytes (DevTokenFileError naming the entry and the field)."""
        doc = json_object(raw, _WHAT, frozenset({"tokens"}), DevTokenFileError)
        items = doc["tokens"]
        if not isinstance(items, list):
            raise DevTokenFileError(f"{_WHAT}: tokens must be a list")
        if not items:
            raise DevTokenFileError(f"{_WHAT} holds no token")
        if len(items) > MAX_TOKENS:
            raise DevTokenFileError(f"{_WHAT} holds more than {MAX_TOKENS} tokens")
        entries: list[_Entry] = []
        seen: set[bytes] = set()
        for index, item in enumerate(items):
            entry = _entry(index, item)
            if entry.digest in seen:
                raise DevTokenFileError(f"{_WHAT}: entry {index}: token is listed twice")
            seen.add(entry.digest)
            entries.append(entry)
        return cls(entries)

    async def verify(self, raw: str) -> Principal:
        """The principal of ``raw``; ``Unauthenticated`` (with no cause) for any other token."""
        if not raw or len(raw) > MAX_TOKEN_LEN or not raw.isascii():
            raise Unauthenticated("unknown token")
        digest = hashlib.sha256(raw.encode("ascii")).digest()
        found: Principal | None = None
        for entry in self._entries:
            if hmac.compare_digest(digest, entry.digest):
                found = entry.principal
        if found is None:
            raise Unauthenticated("unknown token")
        return found

    def __repr__(self) -> str:
        return f"PrototypeDevTokenIdentity({len(self._entries)} tokens)"


def _entry(index: int, item: Any) -> _Entry:
    where = f"{_WHAT}: entry {index}"
    if not isinstance(item, dict):
        raise DevTokenFileError(f"{where} is not an object")
    check_keys(
        {k: v for k, v in item.items() if k != _USER_ID_KEY}, _ENTRY_KEYS, where, DevTokenFileError
    )
    token, subject, scopes = item["token"], item["subject"], item["scopes"]
    if (
        not isinstance(token, str)
        or not MIN_TOKEN_LEN <= len(token) <= MAX_TOKEN_LEN
        or _TOKEN.fullmatch(token) is None
    ):
        raise DevTokenFileError(
            f"{where}: token must be {MIN_TOKEN_LEN} to {MAX_TOKEN_LEN} token68 characters"
        )
    if not isinstance(subject, str):
        raise DevTokenFileError(f"{where}: subject is not a valid requester")
    try:
        validate_requester(subject)
    except DomainError:
        raise DevTokenFileError(f"{where}: subject is not a valid requester") from None
    if not isinstance(scopes, list) or not all(
        isinstance(s, str) and scope_token(s) for s in scopes
    ):
        raise DevTokenFileError(f"{where}: scopes must be a list of scope tokens")
    user_id: UUID | None = None
    if _USER_ID_KEY in item:
        value = item[_USER_ID_KEY]
        if not isinstance(value, str):
            raise DevTokenFileError(f"{where}: user_id must be a canonical lowercase UUID")
        try:
            user_id = parse_id(value)
        except DomainError:
            raise DevTokenFileError(
                f"{where}: user_id must be a canonical lowercase UUID"
            ) from None
    return _Entry(
        digest=hashlib.sha256(token.encode("ascii")).digest(),
        principal=Principal(subject=subject, scopes=tuple(scopes), user_id=user_id),
    )
