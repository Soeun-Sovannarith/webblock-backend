"""The action policy: each action requires exactly one configured scope, held as a whole value; a
missing mapping never permits."""

from __future__ import annotations

import pytest

from provisioning.core.application import Action, ActionDenied, Principal
from provisioning.infrastructure.identity import ActionScopePolicy

SCOPES = {a: f"prov.{a.value}" for a in Action}


def test_a_principal_with_the_scope_is_permitted() -> None:
    policy = ActionScopePolicy(SCOPES)
    for action in Action:
        policy.permit(Principal(subject="s", scopes=(f"prov.{action.value}",)), action)


@pytest.mark.parametrize(
    "scopes",
    [
        (),
        ("prov.get_release",),
        ("prov.deploy",),
        ("prov.deploy_release ",),
        ("PROV.DEPLOY_RELEASE",),
    ],
)
def test_anything_else_is_denied(scopes: tuple[str, ...]) -> None:
    with pytest.raises(ActionDenied):
        ActionScopePolicy(SCOPES).permit(
            Principal(subject="s", scopes=scopes), Action.DEPLOY_RELEASE
        )


def test_a_scope_is_compared_whole_not_as_a_substring() -> None:
    policy = ActionScopePolicy({**SCOPES, Action.GET_RELEASE: "read"})
    with pytest.raises(ActionDenied):
        policy.permit(Principal(subject="s", scopes=("readwrite",)), Action.GET_RELEASE)


def test_construction_refuses_a_missing_or_empty_scope() -> None:
    missing: dict[Action, str] = {a: s for a, s in SCOPES.items() if a is not Action.BUILD_RELEASE}
    with pytest.raises(ValueError, match="build_release has no scope"):
        ActionScopePolicy(missing)
    with pytest.raises(ValueError, match="get_command has no scope"):
        ActionScopePolicy({**SCOPES, Action.GET_COMMAND: ""})
    with pytest.raises(ValueError, match="no action scopes"):
        ActionScopePolicy({})


def test_the_mapping_is_copied() -> None:
    scopes = dict(SCOPES)
    policy = ActionScopePolicy(scopes)
    scopes[Action.DEPLOY_RELEASE] = "changed"
    policy.permit(Principal(subject="s", scopes=("prov.deploy_release",)), Action.DEPLOY_RELEASE)
