"""An incoming command, normalized, and its request fingerprint (decision 0001 section 4; value;
cut from the commands row module, decision 0025 CQ1)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from provisioning.core.domain.enums.states import CommandKind, parse_command_kind
from provisioning.core.domain.errors import InvalidCommandTarget, InvalidId
from provisioning.core.domain.values.text import validate_idempotency_key

# The first line of the text the request fingerprint covers (decision 0001 section 4).
REQUEST_FINGERPRINT_DOMAIN = "provisioning.command.v1"


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """An incoming command, normalized. For deploy, promote and rollback ``release_id`` names the
    target and ``idempotency_key`` is the client's key; for the hosting kinds ``release_id`` is
    None and ``idempotency_key`` is the Payment event key."""

    project_id: UUID
    kind: CommandKind
    release_id: UUID | None
    idempotency_key: str


def compute_request_fingerprint(request: CommandRequest) -> str:
    """The lowercase hexadecimal SHA-256 of the UTF-8 lines "provisioning.command.v1", the kind,
    the project ID and the release ID (or, for a hosting kind, the Payment event key), each ended
    by a line feed (decision 0001 section 4)."""
    _validate_request(request)
    target = str(request.release_id) if request.release_id is not None else request.idempotency_key
    text = "".join(
        line + "\n"
        for line in (REQUEST_FINGERPRINT_DOMAIN, str(request.kind), str(request.project_id), target)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_request(request: CommandRequest) -> None:
    """Everything the fingerprint reads: a non-nil project, a known kind, a target that matches
    the kind, and the key."""
    if request.project_id.int == 0:
        raise InvalidId("project ID is required")
    kind = parse_command_kind(request.kind)
    check_target_by_kind(kind, request.release_id)
    validate_idempotency_key(request.idempotency_key)


def check_target_by_kind(kind: CommandKind, release_id: UUID | None) -> None:
    """commands_release_by_kind: a hosting kind or sync_domains names no release, every other kind
    a non-nil one."""
    if kind.names_no_release():
        if release_id is not None:
            raise InvalidCommandTarget(f"{kind} names no release")
        return
    if release_id is None:
        raise InvalidCommandTarget(f"{kind} requires a release")
    if release_id.int == 0:
        raise InvalidId(f"{kind} release ID")
