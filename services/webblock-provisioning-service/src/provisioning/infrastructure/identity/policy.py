"""The action policy: implements the application's
ActionPolicy. Each action requires exactly one configured scope, held by the principal as a whole
value."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from provisioning.core.application import Action, ActionDenied, Principal


class ActionScopePolicy:
    """Construction fails when any action has no scope or an empty one: a missing
    mapping never means "permitted" (decision 0006 section 5). The mapping is copied."""

    __slots__ = ("_scopes",)

    def __init__(self, scopes: Mapping[Action, str]) -> None:
        if not scopes:
            raise ValueError("identity: no action scopes")
        for action in Action:
            if not scopes.get(action):
                raise ValueError(f"identity: action {action} has no scope")
        self._scopes = MappingProxyType({a: scopes[a] for a in Action})

    def permit(self, principal: Principal, action: Action) -> None:
        """Return when the principal holds the action's scope; ``ActionDenied`` otherwise."""
        scope = self._scopes.get(action, "")
        if not scope or not principal.has_scope(scope):
            raise ActionDenied(str(action))
