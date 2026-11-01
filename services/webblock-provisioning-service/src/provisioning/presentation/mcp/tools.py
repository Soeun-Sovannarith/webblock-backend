"""The tool file against the handler table (decision 0006
section 4). Name, description and input schema come from src/contracts/mcp/tools.json; construction
fails, never panics, when the file and the operations differ in either direction, a tool appears
twice, or a schema is not an object of exactly the operation's inputs (all strings but the
listings' integer limit; required exactly the inputs that are not optional), with no
additional properties. Unknown keys are refused, so a field the SDK would treat differently (an
output schema, annotations) cannot be added to the file without a change here."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from provisioning.core.application import Action, parse_action
from provisioning.core.domain import DomainError
from provisioning.presentation.operations import (
    INPUT_LIMIT,
    OPERATIONS,
    MalformedRequest,
    Operation,
    lookup,
    parse_json_strict,
)

_FILE_KEYS: Final = frozenset({"version", "status", "description", "tools"})
_TOOL_KEYS: Final = frozenset({"name", "description", "inputSchema"})
_SCHEMA_KEYS: Final = frozenset({"type", "properties", "required", "additionalProperties"})
# Every input is a string but the listings' limit (decision 0023 G4).
_INPUT_TYPES: Final = {INPUT_LIMIT: "integer"}


class ToolFileError(Exception):
    """The tool file does not match the handler table; the endpoint is not built."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One checked tool of the file and its operation."""

    name: str
    description: str
    input_schema: dict[str, Any]
    operation: Operation


def parse_tools(raw: bytes) -> tuple[str, tuple[ToolSpec, ...]]:
    """The file's version and its tools in file order (``ToolFileError`` otherwise)."""
    try:
        doc = parse_json_strict(raw)
    except MalformedRequest:
        raise ToolFileError("mcp: tool file: invalid JSON") from None
    if not isinstance(doc, dict):
        raise ToolFileError("mcp: tool file: not a JSON object")
    _exact_keys(doc, _FILE_KEYS, "tool file")
    version, tools = doc["version"], doc["tools"]
    if not isinstance(version, str) or not version or not isinstance(tools, list):
        raise ToolFileError("mcp: tool file: version must be text and tools a list")
    seen: set[str] = set()
    specs: list[ToolSpec] = []
    for entry in tools:
        if not isinstance(entry, dict):
            raise ToolFileError("mcp: tool file: a tool is not an object")
        _exact_keys(entry, _TOOL_KEYS, "a tool")
        name, description, schema = entry["name"], entry["description"], entry["inputSchema"]
        if not isinstance(name, str):
            raise ToolFileError("mcp: tool file: a tool name is not text")
        if name in seen:
            raise ToolFileError(f'mcp: tool "{name}" is listed twice')
        seen.add(name)
        op = _operation(name)
        if not isinstance(description, str) or not description:
            raise ToolFileError(f'mcp: tool "{name}" has no description')
        _check_schema(name, schema, op)
        specs.append(
            ToolSpec(name=name, description=description, input_schema=schema, operation=op)
        )
    for op in OPERATIONS:
        if op.action.value not in seen:
            raise ToolFileError(f'mcp: handler "{op.action.value}" has no tool in the file')
    return version, tuple(specs)


def _operation(name: str) -> Operation:
    try:
        action: Action = parse_action(name)
    except DomainError:
        raise ToolFileError(f'mcp: tool "{name}" has no handler') from None
    op = lookup(action)
    if op is None:
        raise ToolFileError(f'mcp: tool "{name}" has no handler')
    return op


def _check_schema(name: str, schema: object, op: Operation) -> None:
    where = f'mcp: tool "{name}"'
    if not isinstance(schema, dict):
        raise ToolFileError(f"{where}: input schema is not an object")
    if set(schema) - _SCHEMA_KEYS:
        raise ToolFileError(f"{where}: input schema has an unknown key")
    if schema.get("type") != "object":
        raise ToolFileError(f'{where}: input schema type is not "object"')
    if schema.get("additionalProperties") is not False:
        raise ToolFileError(f"{where}: input schema must set additionalProperties to false")
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise ToolFileError(f"{where}: input schema needs properties and required")
    for prop, spec in properties.items():
        want = _INPUT_TYPES.get(prop, "string")
        if not isinstance(spec, dict) or spec.get("type") != want:
            article = "an integer" if want == "integer" else "a string"
            raise ToolFileError(f'{where}: input "{prop}" is not {article}')
    inputs = sorted(op.inputs)
    must = sorted(op.required())
    if sorted(properties) != inputs or sorted(str(r) for r in required) != must:
        raise ToolFileError(
            f"{where}: input schema properties {sorted(properties)} and required "
            f"{sorted(str(r) for r in required)} differ from the operation's inputs {inputs} "
            f"and required inputs {must}"
        )


def _exact_keys(obj: dict[str, Any], keys: frozenset[str], where: str) -> None:
    if set(obj) - keys:
        raise ToolFileError(f"mcp: {where} has an unknown key")
    if keys - set(obj):
        raise ToolFileError(f"mcp: {where} lacks " + ", ".join(sorted(keys - set(obj))))
