"""The key rule: the idempotency key of a hosting command fixes its offline reason (decision 0021),
and how a reason set changes the hosting status."""

from __future__ import annotations

import pytest

from provisioning.core.domain import (
    ADMIN_KEY_PREFIX,
    DELETION_KEY,
    OWNER_STATUS_KEY_PREFIX,
    CommandKind,
    HostingStatus,
    IllegalTransition,
    InvalidCommandTarget,
    InvalidIdempotencyKey,
    OfflineReason,
    admin_hosting_key,
    apply_hosting_reason,
    hosting_reason_of_key,
    hosting_status_for,
    owner_status_key,
    owner_status_version_of_key,
    root_key,
    split_retry_key,
)

SUSPEND = CommandKind.SUSPEND_HOSTING
RESUME = CommandKind.RESUME_HOSTING


@pytest.mark.parametrize(
    ("kind", "key", "want"),
    [
        (SUSPEND, "project-deleted", OfflineReason.PROJECT_DELETED),
        (SUSPEND, "project-deleted.retry-2", OfflineReason.PROJECT_DELETED),
        (SUSPEND, "owner-status:7", OfflineReason.USER_SUSPENDED),
        (RESUME, "owner-status:8.retry-1", OfflineReason.USER_SUSPENDED),
        (SUSPEND, "evt-123", OfflineReason.BILLING),
        (RESUME, "evt-124.retry-3", OfflineReason.BILLING),
    ],
)
def test_the_key_fixes_the_reason(kind: CommandKind, key: str, want: OfflineReason) -> None:
    assert hosting_reason_of_key(kind, key) is want


def test_a_resume_never_names_project_deleted() -> None:
    with pytest.raises(InvalidCommandTarget):
        hosting_reason_of_key(RESUME, "project-deleted")
    with pytest.raises(InvalidCommandTarget):
        hosting_reason_of_key(RESUME, "project-deleted.retry-1")


@pytest.mark.parametrize(
    "kind",
    [CommandKind.DEPLOY, CommandKind.PROMOTE, CommandKind.ROLLBACK, CommandKind.SYNC_DOMAINS],
)
def test_a_non_hosting_kind_has_no_reason(kind: CommandKind) -> None:
    with pytest.raises(InvalidCommandTarget):
        hosting_reason_of_key(kind, "evt-1")


@pytest.mark.parametrize(
    "key", ["owner-status:", "owner-status:abc", "owner-status:0", "owner-status:01"]
)
def test_the_owner_status_prefix_is_reserved(key: str) -> None:
    with pytest.raises(InvalidIdempotencyKey):
        hosting_reason_of_key(SUSPEND, key)


def test_removing_project_deleted_is_refused() -> None:
    with pytest.raises(IllegalTransition):
        apply_hosting_reason(
            frozenset({OfflineReason.PROJECT_DELETED}), RESUME, OfflineReason.PROJECT_DELETED
        )


def test_suspend_adds_and_resume_removes_only_its_reason() -> None:
    both = frozenset({OfflineReason.BILLING, OfflineReason.USER_SUSPENDED})
    assert apply_hosting_reason(frozenset(), SUSPEND, OfflineReason.BILLING) == frozenset(
        {OfflineReason.BILLING}
    )
    assert apply_hosting_reason(both, RESUME, OfflineReason.BILLING) == frozenset(
        {OfflineReason.USER_SUSPENDED}
    )
    assert apply_hosting_reason(both, SUSPEND, OfflineReason.BILLING) == both


def test_apply_refuses_a_non_hosting_kind() -> None:
    with pytest.raises(InvalidCommandTarget):
        apply_hosting_reason(frozenset(), CommandKind.DEPLOY, OfflineReason.BILLING)


def test_offline_exactly_when_a_reason_remains() -> None:
    assert hosting_status_for(frozenset()) is HostingStatus.ONLINE
    for reason in OfflineReason:
        assert hosting_status_for(frozenset({reason})) is HostingStatus.OFFLINE


def test_owner_status_key_round_trip() -> None:
    assert owner_status_key(5) == "owner-status:5"
    assert owner_status_key(1) == OWNER_STATUS_KEY_PREFIX + "1"
    assert owner_status_key(2**53 - 1) == f"owner-status:{2**53 - 1}"
    assert owner_status_version_of_key("owner-status:5") == 5
    assert owner_status_version_of_key("owner-status:5.retry-1") == 5
    assert owner_status_version_of_key("evt-5") is None
    assert owner_status_version_of_key("owner-status:0") is None
    assert owner_status_version_of_key(f"owner-status:{2**53}") is None
    with pytest.raises(InvalidIdempotencyKey):
        owner_status_key(0)
    with pytest.raises(InvalidIdempotencyKey):
        owner_status_key(2**53)


def test_retry_key_helpers() -> None:
    assert DELETION_KEY == "project-deleted"
    assert split_retry_key("k.retry-2") == ("k", 2)
    assert split_retry_key("k") == ("k", 0)
    assert root_key("k.retry-2") == "k"


# --- admin keys (decision 0021; Notion: an admin's suspend or resume stores its key as admin:<key>)


@pytest.mark.parametrize("kind", [SUSPEND, RESUME])
@pytest.mark.parametrize(
    "key", ["admin:abc", "admin:abc.retry-1", "admin:project-deleted", "admin:owner-status:5"]
)
def test_admin_key_maps_to_admin_suspended(kind: CommandKind, key: str) -> None:
    assert hosting_reason_of_key(kind, key) is OfflineReason.ADMIN_SUSPENDED


def test_admin_key_is_prefixed() -> None:
    assert ADMIN_KEY_PREFIX == "admin:"
    assert admin_hosting_key("project-deleted") == "admin:project-deleted"
    assert admin_hosting_key("owner-status:5") == "admin:owner-status:5"
    # The admin key never takes a system key's reason.
    assert hosting_reason_of_key(SUSPEND, admin_hosting_key("project-deleted")) is (
        OfflineReason.ADMIN_SUSPENDED
    )


def test_admin_key_length_limit() -> None:
    """The admin key follows the key rule (1 to 255 bytes), so a client key is at most 249."""
    assert admin_hosting_key("k" * 249) == "admin:" + "k" * 249
    with pytest.raises(InvalidIdempotencyKey):
        admin_hosting_key("k" * 250)
    with pytest.raises(InvalidIdempotencyKey):
        admin_hosting_key("")
    with pytest.raises(InvalidIdempotencyKey):
        admin_hosting_key(" padded")


def test_a_key_like_the_admin_prefix_is_billing() -> None:
    assert hosting_reason_of_key(SUSPEND, "admin-1") is OfflineReason.BILLING
    assert hosting_reason_of_key(SUSPEND, "Admin:1") is OfflineReason.BILLING
    assert hosting_reason_of_key(SUSPEND, "xadmin:1") is OfflineReason.BILLING
