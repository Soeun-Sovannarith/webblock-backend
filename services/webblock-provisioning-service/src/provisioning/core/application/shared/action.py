"""The operations of the REST and MCP contracts (decision 0006 section 2), plus the
prototype's build endpoint (decision 0014 section 3, "Build") and the two listing reads (decision
0014 section 21 ruling 7), and the four admin operations (decision 0023 G9, G10)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from provisioning.core.application.shared.errors import OperationDisabled
from provisioning.core.application.shared.profile import OpenContract
from provisioning.core.domain import CommandKind, UnknownEnumValue


class Action(StrEnum):
    """One operation; its value is the operation ID, which is also the MCP tool name. Declared in
    contract order; ``BUILD_RELEASE`` is the prototype's eighth operation and accepts no command;
    ``LIST_RELEASES`` and ``LIST_COMMANDS`` are reads (no new mutation, section 21 ruling 7). The
    last four are the admin-only actions of decision 0023 G10: three mutations and the admin
    read."""

    DEPLOY_RELEASE = "deploy_release"
    PROMOTE_RELEASE = "promote_release"
    ROLLBACK_RELEASE = "rollback_release"
    GET_RELEASE = "get_release"
    GET_COMMAND = "get_command"
    GET_DEPLOYMENT = "get_deployment"
    GET_RELEASE_DIAGNOSTICS = "get_release_diagnostics"
    BUILD_RELEASE = "build_release"
    LIST_RELEASES = "list_releases"
    LIST_COMMANDS = "list_commands"
    SUSPEND_WEBSITE = "suspend_website"
    RESUME_WEBSITE = "resume_website"
    REDEPLOY_RELEASE = "redeploy_release"
    ADMIN_LIST_COMMANDS = "admin_list_commands"

    def is_mutation(self) -> bool:
        """Whether the action changes state: the three client command mutations, build_release
        and the three admin mutations (suspend_website, resume_website, redeploy_release)."""
        return self in _MUTATIONS

    def command_kind(self) -> CommandKind | None:
        """The kind of command the action accepts, or None for a read and for build_release. The
        only actions that create a hosting command are an admin's suspend_website and
        resume_website (reason ``admin_suspended``, decision 0023 G11); every other hosting
        command comes from an event."""
        return _COMMAND_KIND_OF.get(self)

    def is_admin_only(self) -> bool:
        """Whether only a principal holding ``ADMIN_ROLE`` may take this action: rollback_release
        (FR-50) and the four actions of 0023 G10 (suspend_website, resume_website,
        redeploy_release, admin_list_commands). The gate refuses anyone else with
        ``ActionDenied``, whatever scope the policy maps the action to; an ``ADMIN`` takes it on
        any project, and the Project service is never asked (decision 0019 section 7)."""
        return self in _ADMIN_ONLY


ADMIN_ROLE: Final = "ADMIN"
"""The platform staff's realm role in the roles claim (decision 0019 section 3, A5), compared whole
and case-sensitively; the only role that takes the actions ``Action.is_admin_only`` names, and on
those it skips the project access check."""


def parse_action(text: str) -> Action:
    """Match ``text`` exactly; anything else is ``UnknownEnumValue`` without the text."""
    for action in Action:
        if action.value == text:
            return action
    raise UnknownEnumValue("action") from None


class EnabledOperations:
    """The set of actions a build of the API exposes (decision 0006 section 6), passed in code.
    The empty set enables nothing. Immutable after construction.

    ``refused`` names, for an action that is switched off because a contract it needs is still
    open, those contracts (task H2: a production build refuses every mutation, OD-01 and OD-04).
    Such an action is refused with ``OpenContract``; any other switched-off action with
    ``OperationDisabled``."""

    __slots__ = ("_enabled", "_refused")

    def __init__(
        self,
        enabled: Iterable[Action] = (),
        refused: Mapping[Action, tuple[str, ...]] | None = None,
    ) -> None:
        """Hold exactly ``enabled``; a value that is not an ``Action`` is ``UnknownEnumValue``.
        An action both enabled and refused, or refused without a contract, is ``ValueError``."""
        self._enabled = frozenset(parse_action(action) for action in enabled)
        reasons = {parse_action(a): tuple(c) for a, c in (refused or {}).items()}
        for action, contracts in reasons.items():
            if action in self._enabled:
                raise ValueError(f"enabled operations: {action} is both enabled and refused")
            if not contracts:
                raise ValueError(f"enabled operations: {action} is refused without a contract")
        self._refused = MappingProxyType(reasons)

    def enabled(self, action: Action) -> bool:
        return action in self._enabled

    def actions(self) -> tuple[Action, ...]:
        """The enabled actions in contract order."""
        return tuple(action for action in Action if action in self._enabled)

    def refusal(self, action: Action) -> OperationDisabled:
        """The error a switched-off ``action`` is refused with: ``OpenContract`` naming its open
        contracts when it has them, else ``OperationDisabled``."""
        contracts = self._refused.get(action)
        if contracts is not None:
            return OpenContract(action.value, contracts)
        return OperationDisabled(str(action))


# The command each mutation accepts. The only hosting commands an action creates are an admin's
# (decision 0023 G11); the others come from events (decision 0004 section 3, decision 0021).
_COMMAND_KIND_OF: dict[Action, CommandKind] = {
    Action.DEPLOY_RELEASE: CommandKind.DEPLOY,
    Action.PROMOTE_RELEASE: CommandKind.PROMOTE,
    Action.ROLLBACK_RELEASE: CommandKind.ROLLBACK,
    Action.SUSPEND_WEBSITE: CommandKind.SUSPEND_HOSTING,
    Action.RESUME_WEBSITE: CommandKind.RESUME_HOSTING,
    Action.REDEPLOY_RELEASE: CommandKind.REDEPLOY,
}
_MUTATIONS = frozenset({*_COMMAND_KIND_OF, Action.BUILD_RELEASE})
# The actions only an ADMIN takes (FR-50, 0023 G10), on any project and with no Project call.
_ADMIN_ONLY = frozenset(
    {
        Action.ROLLBACK_RELEASE,
        Action.SUSPEND_WEBSITE,
        Action.RESUME_WEBSITE,
        Action.REDEPLOY_RELEASE,
        Action.ADMIN_LIST_COMMANDS,
    }
)
