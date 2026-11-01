"""The verified principal's scopes (decision 0006 section 5) and the set of enabled operations
(decision 0006 section 6)."""

from __future__ import annotations

from typing import cast
from uuid import UUID

import pytest

from provisioning.core.application import Action, EnabledOperations, Principal
from provisioning.core.domain import UnknownEnumValue


@pytest.mark.parametrize("scope", ["provisioning:deploy", "provisioning:read"])
def test_has_scope_holds_granted_scopes(scope: str) -> None:
    p = Principal(subject="user-1", scopes=("provisioning:deploy", "provisioning:read"))
    assert p.has_scope(scope)


# Scope values are compared whole, never as substrings or prefixes (decision 0006 section 5).
@pytest.mark.parametrize(
    "scope",
    [
        "provisioning",
        "provisioning:",
        "provisioning:dep",
        "provisioning:deploy ",
        " provisioning:deploy",
        "PROVISIONING:DEPLOY",
        "provisioning:deploy provisioning:read",
        "deploy",
        "provisioning:promote",
    ],
)
def test_has_scope_compares_whole_values(scope: str) -> None:
    p = Principal(subject="user-1", scopes=("provisioning:deploy", "provisioning:read"))
    assert not p.has_scope(scope)


# The empty principal is permitted nothing (decision 0004 section 1): it holds no scope at all.
@pytest.mark.parametrize("scope", ["", "provisioning:read", "*"])
def test_zero_principal_has_no_scope(scope: str) -> None:
    zero = Principal()
    assert zero.is_empty()
    assert not zero.has_scope(scope)


def test_principal_with_a_subject_or_a_scope_is_not_empty() -> None:
    assert not Principal(subject="user-1").is_empty()
    assert not Principal(scopes=("provisioning:read",)).is_empty()


# A missing scope mapping must never mean "permitted": the empty scope is held by nobody, even by a
# principal whose scope list contains an empty string.
def test_empty_scope_is_never_held() -> None:
    p = Principal(subject="user-1", scopes=("", "provisioning:read"))
    assert not p.has_scope("")


def test_enabled_operations_zero_value_enables_nothing() -> None:
    none = EnabledOperations()
    for action in Action:
        assert not none.enabled(action), action
    assert none.actions() == ()


def test_enabled_operations_holds_exactly_what_was_passed() -> None:
    enabled = EnabledOperations(
        [Action.GET_DEPLOYMENT, Action.GET_RELEASE, Action.GET_COMMAND, Action.GET_RELEASE]
    )
    want = {Action.GET_RELEASE, Action.GET_COMMAND, Action.GET_DEPLOYMENT}
    for action in Action:
        assert enabled.enabled(action) == (action in want), action
    # Listed in contract order, whatever order they were passed in, and without the duplicate.
    assert enabled.actions() == (Action.GET_RELEASE, Action.GET_COMMAND, Action.GET_DEPLOYMENT)


def test_enabled_operations_full_set() -> None:
    # All eight actions (the seven contract operations and build_release) in declaration order.
    enabled = EnabledOperations(Action)
    assert enabled.actions() == tuple(Action)


# Enabling an operation is a reviewed change in code, so a value that is not an Action is a mistake
# to report, not something to drop silently.
@pytest.mark.parametrize(
    "bad",
    ["", "create_release", "suspend_hosting"],
    ids=["empty", "create_release", "suspend_hosting"],
)
def test_enabled_operations_refuses_unknown_actions(bad: str) -> None:
    with pytest.raises(UnknownEnumValue):
        EnabledOperations([Action.GET_RELEASE, cast(Action, bad)])


# An unknown action is never enabled, whatever the set holds.
def test_enabled_operations_unknown_action_is_not_enabled() -> None:
    enabled = EnabledOperations(Action)
    assert not enabled.enabled(cast(Action, "create_release"))
    assert not enabled.enabled(cast(Action, ""))


# The set is fixed at construction: the caller's list does not reach it.
def test_enabled_operations_cannot_be_changed_after_construction() -> None:
    given = [Action.GET_RELEASE]
    enabled = EnabledOperations(given)
    given[0] = Action.ROLLBACK_RELEASE
    given.append(Action.DEPLOY_RELEASE)
    assert not enabled.enabled(Action.ROLLBACK_RELEASE)
    assert not enabled.enabled(Action.DEPLOY_RELEASE)
    assert enabled.enabled(Action.GET_RELEASE)
    assert enabled.actions() == (Action.GET_RELEASE,)


# Task PA (decision 0017 section 2): the principal carries the user's optional platform ID. It is
# not part of the emptiness rule: a user ID alone is no subject and no scope.
def test_the_platform_user_id_is_optional_and_absent_by_default() -> None:
    assert Principal(subject="user-1").user_id is None
    user = UUID("0199e7a0-0000-7000-8000-0000000000aa")
    p = Principal(subject="user-1", scopes=("provisioning:read",), user_id=user)
    assert p.user_id == user
    assert Principal(user_id=user).is_empty()
