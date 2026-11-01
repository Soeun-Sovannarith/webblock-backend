"""The offline reasons of a site and the key rule (decision 0021).

The idempotency key of a hosting command fixes the reason it adds (suspend) or removes (resume):
``admin:<key>`` is ``admin_suspended`` (migration 00020), ``project-deleted`` is
``project_deleted``, ``owner-status:<version>`` is ``user_suspended`` and any other key (a Payment
event key) is ``billing``. Each may carry ``.retry-<n>``. A resume never names
``project_deleted``. A site is offline exactly when it holds at least one reason.
"""

from __future__ import annotations

import re
from typing import Final

from provisioning.core.domain.enums.states import CommandKind, HostingStatus, OfflineReason
from provisioning.core.domain.errors import (
    IllegalTransition,
    InvalidCommandTarget,
    InvalidIdempotencyKey,
)
from provisioning.core.domain.values.identifiers import MAX_COMMAND_NUMBER
from provisioning.core.domain.values.text import validate_idempotency_key

DELETION_KEY: Final = "project-deleted"
"""The idempotency key of the suspend a "project deleted" event becomes."""

OWNER_STATUS_KEY_PREFIX: Final = "owner-status:"
"""The prefix of the key of an owner-status hosting command; reserved for that intake."""

ADMIN_KEY_PREFIX: Final = "admin:"
"""The prefix of the key of an admin's suspend or resume; an admin key never takes a system key."""

_RETRY_SUFFIX = re.compile(r"^(.+)\.retry-([1-9][0-9]{0,8})$", re.DOTALL)
_VERSION = re.compile(r"^[1-9][0-9]{0,15}$")


def split_retry_key(key: str) -> tuple[str, int]:
    """``(root key, retry attempt)``: ``("k", 2)`` for ``k.retry-2``, ``("k", 0)`` for a key that
    is not a retry."""
    match = _RETRY_SUFFIX.match(key)
    if match is None:
        return key, 0
    return match.group(1), int(match.group(2))


def root_key(key: str) -> str:
    """The key a hosting command's key retries, or the key itself."""
    return split_retry_key(key)[0]


def owner_status_key(version: int) -> str:
    """``owner-status:<version>``; the version is 1 to 2^53-1 (``InvalidIdempotencyKey``)."""
    if not 1 <= version <= MAX_COMMAND_NUMBER:
        raise InvalidIdempotencyKey(f"owner status version outside 1..{MAX_COMMAND_NUMBER}")
    return f"{OWNER_STATUS_KEY_PREFIX}{version}"


def admin_hosting_key(client_key: str) -> str:
    """``admin:<client_key>``, the stored key of an admin's suspend or resume (Notion: so it can
    never take a system key such as ``project-deleted``). The client key and the result both
    follow the idempotency key rule, so a client key is at most 249 bytes
    (``InvalidIdempotencyKey``)."""
    validate_idempotency_key(client_key)
    key = ADMIN_KEY_PREFIX + client_key
    validate_idempotency_key(key)
    return key


def owner_status_version_of_key(key: str) -> int | None:
    """The version of ``owner-status:<v>`` or ``owner-status:<v>.retry-<n>``, else None."""
    root = root_key(key)
    if not root.startswith(OWNER_STATUS_KEY_PREFIX):
        return None
    text = root[len(OWNER_STATUS_KEY_PREFIX) :]
    if _VERSION.match(text) is None:
        return None
    version = int(text)
    return version if version <= MAX_COMMAND_NUMBER else None


def hosting_reason_of_key(kind: CommandKind, key: str) -> OfflineReason:
    """The reason a hosting command with ``key`` adds or removes; the ``admin:`` prefix is checked
    first, so ``admin:project-deleted`` is ``admin_suspended``. A kind that is not a hosting
    kind, and a resume whose key gives ``project_deleted``, are ``InvalidCommandTarget``; a key
    that starts with the reserved owner-status prefix but holds no valid version is
    ``InvalidIdempotencyKey``."""
    if not kind.is_hosting():
        raise InvalidCommandTarget(f"{kind} has no offline reason")
    root = root_key(key)
    if root.startswith(ADMIN_KEY_PREFIX):
        return OfflineReason.ADMIN_SUSPENDED
    if root == DELETION_KEY:
        if kind is CommandKind.RESUME_HOSTING:
            raise InvalidCommandTarget("a resume cannot name project_deleted")
        return OfflineReason.PROJECT_DELETED
    if root.startswith(OWNER_STATUS_KEY_PREFIX):
        if owner_status_version_of_key(key) is None:
            raise InvalidIdempotencyKey("owner status key without a valid version")
        return OfflineReason.USER_SUSPENDED
    return OfflineReason.BILLING


def apply_hosting_reason(
    reasons: frozenset[OfflineReason], kind: CommandKind, reason: OfflineReason
) -> frozenset[OfflineReason]:
    """A suspend adds ``reason``, a resume removes it. Removing ``project_deleted`` is
    ``IllegalTransition``; a kind that is not a hosting kind is ``InvalidCommandTarget``."""
    if kind is CommandKind.SUSPEND_HOSTING:
        return reasons | {reason}
    if kind is CommandKind.RESUME_HOSTING:
        if reason is OfflineReason.PROJECT_DELETED:
            raise IllegalTransition("project_deleted is never removed")
        return reasons - {reason}
    raise InvalidCommandTarget(f"{kind} has no offline reason")


def hosting_status_for(reasons: frozenset[OfflineReason]) -> HostingStatus:
    """``OFFLINE`` exactly when ``reasons`` is not empty."""
    return HostingStatus.OFFLINE if reasons else HostingStatus.ONLINE
