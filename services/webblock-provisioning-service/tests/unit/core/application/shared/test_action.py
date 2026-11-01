"""The operations of the REST and MCP contracts (decision 0006 section 2) and the prototype's eighth
operation, build_release (decision 0014 section 3, "Build").

The contract files read here are src/contracts/openapi.yaml and src/contracts/mcp/tools.json; they
list the seven contract operations, build_release (prototype task E1), the two listings and, since
Phase C task 7, the four admin operations (decision 0023 G9).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from provisioning.core.application import Action, parse_action
from provisioning.core.domain import CommandKind, UnknownEnumValue

_REPO_ROOT = Path(__file__).resolve().parents[5]
_OPENAPI = _REPO_ROOT / "src" / "contracts" / "openapi.yaml"
_TOOLS = _REPO_ROOT / "src" / "contracts" / "mcp" / "tools.json"

# The fourteen operations of the contract in contract order: the seven of decision 0006, the
# prototype's build endpoint (decision 0014 section 3), the two listing reads (decision 0014
# section 21 ruling 7), then the four admin operations (decision 0023 G9).
_CONTRACT_ACTIONS = (
    Action.DEPLOY_RELEASE,
    Action.PROMOTE_RELEASE,
    Action.ROLLBACK_RELEASE,
    Action.GET_RELEASE,
    Action.GET_COMMAND,
    Action.GET_DEPLOYMENT,
    Action.GET_RELEASE_DIAGNOSTICS,
    Action.BUILD_RELEASE,
    Action.LIST_RELEASES,
    Action.LIST_COMMANDS,
    Action.SUSPEND_WEBSITE,
    Action.RESUME_WEBSITE,
    Action.REDEPLOY_RELEASE,
    Action.ADMIN_LIST_COMMANDS,
)

# A method line of a path item followed by its operation ID, at the contract's indentation.
_METHOD_AND_OPERATION = re.compile(
    r"(?m)^ {4}(get|put|post|delete|patch|head|options|trace):[ \t]*\n"
    r" {6}operationId:[ \t]*([a-z_]+)[ \t]*$"
)


def _contract_operation_ids() -> list[str]:
    # The order is read from the text, because a parsed YAML mapping is not the contract's order.
    text = _OPENAPI.read_text(encoding="utf-8")
    return re.findall(r"(?m)^\s+operationId:\s*([a-z_]+)\s*$", text)


def test_actions_equal_contract_operation_ids_in_order() -> None:
    want = _contract_operation_ids()
    assert len(want) == 14, f"the contract declares {len(want)} operation IDs, want 14: {want}"
    actions = tuple(Action)
    # Fourteen actions: the seven contract operations in contract order, build_release, the two
    # listings, which are reads, then the four admin operations (decision 0023 G9).
    assert [a.value for a in actions] == want
    assert actions == _CONTRACT_ACTIONS
    assert actions[7] is Action.BUILD_RELEASE
    assert not Action.LIST_RELEASES.is_mutation()
    assert not Action.LIST_COMMANDS.is_mutation()
    assert not Action.ADMIN_LIST_COMMANDS.is_mutation()


def test_actions_equal_contract_tool_names_in_order() -> None:
    doc: dict[str, Any] = json.loads(_TOOLS.read_text(encoding="utf-8"))
    want = [tool["name"] for tool in doc["tools"]]
    assert [a.value for a in Action] == want


def test_is_mutation_matches_contract_method() -> None:
    text = _OPENAPI.read_text(encoding="utf-8")
    methods = {op: method for method, op in _METHOD_AND_OPERATION.findall(text)}
    assert len(methods) == len(_CONTRACT_ACTIONS)
    mutations = 0
    for action in _CONTRACT_ACTIONS:
        assert action.value in methods, f"action {action} is not an operation of the contract"
        assert action.is_mutation() == (methods[action.value] == "post"), action
        if action.is_mutation():
            mutations += 1
    # The three command mutations, build_release, which starts a build and so changes state, and
    # the three admin mutations.
    assert mutations == 7


def test_parse_action() -> None:
    for action in Action:
        assert parse_action(action.value) is action


@pytest.mark.parametrize(
    "text",
    [
        "",
        "create_release",
        "suspend_hosting",
        "Deploy_Release",
        " deploy_release",
        "deploy_release ",
    ],
    ids=["empty", "create_release", "suspend_hosting", "capitalised", "leading space", "trailing"],
)
def test_parse_action_refuses(text: str) -> None:
    with pytest.raises(UnknownEnumValue) as exc_info:
        parse_action(text)
    if text.strip():
        assert text.strip() not in str(exc_info.value)


def test_command_kind_of_action() -> None:
    want = {
        Action.DEPLOY_RELEASE: CommandKind.DEPLOY,
        Action.PROMOTE_RELEASE: CommandKind.PROMOTE,
        Action.ROLLBACK_RELEASE: CommandKind.ROLLBACK,
        Action.SUSPEND_WEBSITE: CommandKind.SUSPEND_HOSTING,
        Action.RESUME_WEBSITE: CommandKind.RESUME_HOSTING,
        Action.REDEPLOY_RELEASE: CommandKind.REDEPLOY,
    }
    for action in Action:
        kind = action.command_kind()
        assert kind == want.get(action), action
        if action is not Action.BUILD_RELEASE:
            # A command kind exactly for the command mutations, client and admin.
            assert (kind is not None) == action.is_mutation(), action
        if kind is not None and kind.is_hosting():
            # Only an admin's suspend or resume creates a hosting command (0023 G11).
            assert action.is_admin_only(), action
    assert not Action.ADMIN_LIST_COMMANDS.is_mutation()
    # build_release is a mutation that accepts no command.
    assert Action.BUILD_RELEASE.is_mutation()
    assert Action.BUILD_RELEASE.command_kind() is None


def test_fr50_rollback_is_admin_only_in_code() -> None:
    """FR-50 (TR-48): only an ADMIN rolls back. The rule is in code, whatever scope
    AUTH_ACTION_SCOPES maps the action to: rollback_release and the four actions of 0023 G10 are
    the admin-only actions, and an ADMIN takes them on any project."""
    assert Action.ROLLBACK_RELEASE.is_admin_only()
    assert [a for a in Action if a.is_admin_only()] == [
        Action.ROLLBACK_RELEASE,
        Action.SUSPEND_WEBSITE,
        Action.RESUME_WEBSITE,
        Action.REDEPLOY_RELEASE,
        Action.ADMIN_LIST_COMMANDS,
    ]


def test_the_four_admin_path_actions_follow_the_contract_actions() -> None:
    """Decision 0023 G9: the four admin operations come last; rollback keeps its client path."""
    assert [a.value for a in tuple(Action)[10:]] == [
        "suspend_website",
        "resume_website",
        "redeploy_release",
        "admin_list_commands",
    ]
    assert all(a.is_admin_only() for a in tuple(Action)[10:])
    assert [a for a in tuple(Action)[:10] if a.is_admin_only()] == [Action.ROLLBACK_RELEASE]
