"""How the verified principal travels from the authentication middleware to an operation (decision
0006 section 4).

The middleware stores it in the ASGI scope's ``state``; the REST handlers read it from the request
and the MCP tool handlers from the HTTP request the SDK attaches to the tool call's context. A tool
handler never reads the raw ``Authorization`` header that the SDK also exposes.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, Final

from provisioning.core.application import Principal

_STATE_KEY: Final = "provisioning_principal"


def with_principal(scope: MutableMapping[str, Any], principal: Principal) -> None:
    """Store the verified principal in the request's scope."""
    state = scope.setdefault("state", {})
    state[_STATE_KEY] = principal


def principal_from(scope: Mapping[str, Any]) -> Principal | None:
    """The principal stored by ``with_principal``, or None when there is none."""
    state = scope.get("state")
    if not isinstance(state, Mapping):
        return None
    principal = state.get(_STATE_KEY)
    return principal if isinstance(principal, Principal) else None
