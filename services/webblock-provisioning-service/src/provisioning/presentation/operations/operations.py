"""The operations of the REST and MCP contracts: the
seven of decision 0006 section 2 and the prototype's build_release (decision 0014 section 3), in
contract order, each with its REST method, path template, inputs and declared statuses, then the
two listing reads (decision 0014 section 21 ruling 7) and the four admin operations under
``/v1/admin/projects/{projectId}`` (decision 0023 G9): three mutations that take only the project
and a key, and the admin read, paged like the client list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from provisioning.core.application import Action
from provisioning.presentation.operations.errors import Code

# Input names (decision 0023 G3): the camelCase path parameters of src/contracts/openapi.yaml and
# the properties of the tool input schemas in src/contracts/mcp/tools.json. The idempotency key is
# the Idempotency-Key header on REST and the idempotencyKey argument on MCP.
INPUT_PROJECT_ID: Final = "projectId"
INPUT_RELEASE_ID: Final = "releaseId"
INPUT_COMMAND_ID: Final = "commandId"
INPUT_IDEMPOTENCY_KEY: Final = "idempotencyKey"
# The listings' optional inputs (decision 0023 G4): query parameters on REST, tool arguments on MCP.
INPUT_LIMIT: Final = "limit"
INPUT_CURSOR: Final = "cursor"

_FIELD_NAMES: Final = {INPUT_IDEMPOTENCY_KEY: "Idempotency-Key"}


def field_name(name: str) -> str:
    """The name a REST client sends an input under, for a Problem's ``errors`` (decision 0023 G3,
    G5): the path or query parameter, or the Idempotency-Key header."""
    return _FIELD_NAMES.get(name, name)


@dataclass(frozen=True, slots=True)
class Operation:
    """One operation. ``inputs`` are in parse order: the path identifiers, then idempotencyKey for
    a command mutation, then a listing's optional page inputs; they equal the tool's input schema
    properties. ``optional`` names the inputs that may be absent (the tool schema's properties
    that are not required)."""

    action: Action
    method: str
    path: str
    inputs: tuple[str, ...]
    statuses: frozenset[int]
    optional: frozenset[str] = frozenset()
    not_found: Code = Code.PROJECT_NOT_FOUND

    def required(self) -> tuple[str, ...]:
        """The inputs that must be present, in parse order."""
        return tuple(name for name in self.inputs if name not in self.optional)

    def declares(self, status: int) -> bool:
        """Whether the contract declares ``status`` for the operation; an error whose class has an
        undeclared status is reported as internal."""
        return status in self.statuses

    def answers(self, status: int, code: str) -> bool:
        """Whether the operation may answer ``code`` with ``status``: a declared status, and on a
        read the only 409 is ``CONCURRENT_CHANGE`` (a read takes no command, so a command in
        flight or a running build never refuses it). Anything else is reported as internal."""
        if not self.declares(status):
            return False
        return self.is_mutation() or status != 409 or code == Code.CONCURRENT_CHANGE

    def is_mutation(self) -> bool:
        return self.action.is_mutation()


# The status sets of decision 0006 sections 2 and 3 on Notion's table (decision 0023 G6): 500, 503
# and 504 on every operation; 409 on every operation (on a read only CONCURRENT_CHANGE: a lock
# timeout, lead ruling of the Task 2 review); 413 and 422 on the mutations. Nothing answers 429.
_MUTATION: Final = frozenset({202, 400, 401, 403, 404, 409, 413, 422, 500, 503, 504})
_READ: Final = frozenset({200, 400, 401, 403, 404, 409, 500, 503, 504})
_RELEASE: Final = Code.RELEASE_NOT_FOUND

_RELEASE_INPUTS: Final = (INPUT_PROJECT_ID, INPUT_RELEASE_ID)
_COMMAND_INPUTS: Final = (INPUT_PROJECT_ID, INPUT_RELEASE_ID, INPUT_IDEMPOTENCY_KEY)
_RELEASE_PATH: Final = "/v1/projects/{projectId}/releases/{releaseId}"
_PAGE_INPUTS: Final = frozenset({INPUT_LIMIT, INPUT_CURSOR})
_LIST_INPUTS: Final = (INPUT_PROJECT_ID, INPUT_LIMIT, INPUT_CURSOR)
_ADMIN_PATH: Final = "/v1/admin/projects/{projectId}"
_ADMIN_INPUTS: Final = (INPUT_PROJECT_ID, INPUT_IDEMPOTENCY_KEY)

OPERATIONS: Final[tuple[Operation, ...]] = (
    Operation(
        Action.DEPLOY_RELEASE,
        "POST",
        _RELEASE_PATH + "/deploy",
        _COMMAND_INPUTS,
        _MUTATION,
        not_found=_RELEASE,
    ),
    Operation(
        Action.PROMOTE_RELEASE,
        "POST",
        _RELEASE_PATH + "/promote",
        _COMMAND_INPUTS,
        _MUTATION,
        not_found=_RELEASE,
    ),
    Operation(
        Action.ROLLBACK_RELEASE,
        "POST",
        _RELEASE_PATH + "/rollback",
        _COMMAND_INPUTS,
        _MUTATION,
        not_found=_RELEASE,
    ),
    Operation(Action.GET_RELEASE, "GET", _RELEASE_PATH, _RELEASE_INPUTS, _READ, not_found=_RELEASE),
    Operation(
        Action.GET_COMMAND,
        "GET",
        "/v1/projects/{projectId}/commands/{commandId}",
        (INPUT_PROJECT_ID, INPUT_COMMAND_ID),
        _READ,
        not_found=Code.COMMAND_NOT_FOUND,
    ),
    Operation(
        Action.GET_DEPLOYMENT,
        "GET",
        "/v1/projects/{projectId}/deployment",
        (INPUT_PROJECT_ID,),
        _READ,
        not_found=Code.DEPLOYMENT_NOT_FOUND,
    ),
    Operation(
        Action.GET_RELEASE_DIAGNOSTICS,
        "GET",
        _RELEASE_PATH + "/diagnostics",
        _RELEASE_INPUTS,
        _READ,
        not_found=_RELEASE,
    ),
    Operation(
        Action.BUILD_RELEASE,
        "POST",
        _RELEASE_PATH + "/build",
        _RELEASE_INPUTS,
        _MUTATION,
        not_found=_RELEASE,
    ),
    Operation(
        Action.LIST_RELEASES,
        "GET",
        "/v1/projects/{projectId}/releases",
        _LIST_INPUTS,
        _READ,
        _PAGE_INPUTS,
    ),
    Operation(
        Action.LIST_COMMANDS,
        "GET",
        "/v1/projects/{projectId}/commands",
        _LIST_INPUTS,
        _READ,
        _PAGE_INPUTS,
    ),
    Operation(
        Action.SUSPEND_WEBSITE, "POST", _ADMIN_PATH + "/hosting/suspend", _ADMIN_INPUTS, _MUTATION
    ),
    Operation(
        Action.RESUME_WEBSITE, "POST", _ADMIN_PATH + "/hosting/resume", _ADMIN_INPUTS, _MUTATION
    ),
    Operation(Action.REDEPLOY_RELEASE, "POST", _ADMIN_PATH + "/redeploy", _ADMIN_INPUTS, _MUTATION),
    Operation(
        Action.ADMIN_LIST_COMMANDS,
        "GET",
        _ADMIN_PATH + "/commands",
        _LIST_INPUTS,
        _READ,
        _PAGE_INPUTS,
    ),
)


def lookup(action: Action) -> Operation | None:
    """The operation of an action, or None."""
    for op in OPERATIONS:
        if op.action is action:
            return op
    return None


def status_url(project_id: UUID, command_id: UUID) -> str:
    """The relative path of get_command, built from parsed identifiers and never from the Host
    header (decision 0006 section 1)."""
    return f"/v1/projects/{project_id}/commands/{command_id}"
