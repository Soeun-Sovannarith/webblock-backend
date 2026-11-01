"""The tool file against the handler table (decision 0006 section 4): construction fails, never
panics, when the file and the operations differ in either direction or a schema does not fit the
operation's inputs."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from tests.unit.presentation.apps import TOOLS

from provisioning.core.application import Action
from provisioning.presentation.mcp import ToolFileError, parse_tools


def _doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(TOOLS)
    return doc


def _raw(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc).encode()


def test_the_contract_file_lists_every_operation_in_order() -> None:
    version, tools = parse_tools(TOOLS)
    assert version == "0.2.0"
    assert [t.name for t in tools] == [a.value for a in Action]
    for tool in tools:
        assert tool.description
        assert tool.input_schema["additionalProperties"] is False


def _without(name: str) -> bytes:
    doc = _doc()
    doc["tools"] = [t for t in doc["tools"] if t["name"] != name]
    return _raw(doc)


def _changed(name: str, change: Any) -> bytes:
    doc = _doc()
    for tool in doc["tools"]:
        if tool["name"] == name:
            change(tool)
    return _raw(doc)


def _set(key: str, value: Any) -> Any:
    def change(tool: dict[str, Any]) -> None:
        tool[key] = value

    return change


def _schema(change: Any) -> Any:
    def apply(tool: dict[str, Any]) -> None:
        change(tool["inputSchema"])

    return apply


def _extra_tool() -> bytes:
    doc = _doc()
    extra = copy.deepcopy(doc["tools"][0])
    extra["name"] = "create_release"
    doc["tools"].append(extra)
    return _raw(doc)


def _twice() -> bytes:
    doc = _doc()
    doc["tools"].append(copy.deepcopy(doc["tools"][0]))
    return _raw(doc)


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        (b"", "tool file"),
        (b"{", "tool file"),
        (b'{"version":"1","status":"x","description":"d","tools":[]} trailing', "tool file"),
        (_raw({**_doc(), "unknown": 1}), "unknown key"),
        (_without("build_release"), 'handler "build_release" has no tool'),
        (_without("get_release"), 'handler "get_release" has no tool'),
        (_extra_tool(), 'tool "create_release" has no handler'),
        (_twice(), "listed twice"),
        (_changed("deploy_release", _set("description", "")), "has no description"),
        (_changed("deploy_release", _set("outputSchema", {})), "unknown key"),
        (
            _changed("deploy_release", _schema(lambda s: s.update(additionalProperties=True))),
            "additionalProperties",
        ),
        (
            _changed("deploy_release", _schema(lambda s: s.pop("additionalProperties"))),
            "additionalProperties",
        ),
        (_changed("deploy_release", _schema(lambda s: s.update(type="array"))), "object"),
        (
            _changed("get_release", _schema(lambda s: s["required"].append("idempotencyKey"))),
            "differ from the operation's inputs",
        ),
        (
            _changed("build_release", _schema(lambda s: s["properties"].pop("releaseId"))),
            "differ from the operation's inputs",
        ),
        (
            _changed(
                "get_release",
                _schema(lambda s: s["properties"]["releaseId"].update(type="integer")),
            ),
            "is not a string",
        ),
        (
            _changed(
                "list_commands",
                _schema(lambda s: s["properties"]["limit"].update(type="string")),
            ),
            "is not an integer",
        ),
        (
            _changed("list_releases", _schema(lambda s: s["required"].append("limit"))),
            "differ from the operation's inputs",
        ),
        (
            _changed("list_releases", _schema(lambda s: s["properties"].pop("cursor"))),
            "differ from the operation's inputs",
        ),
        (_without("list_commands"), 'handler "list_commands" has no tool'),
        (
            _changed(
                "list_releases",
                _schema(lambda s: s["properties"].update(page_size=s["properties"].pop("limit"))),
            ),
            'input "page_size" is not a string',
        ),
    ],
)
def test_construction_fails_on_any_difference(raw: bytes, fragment: str) -> None:
    with pytest.raises(ToolFileError) as exc_info:
        parse_tools(raw)
    assert fragment in str(exc_info.value)


def test_tool_arguments_are_camel_case() -> None:
    """Decision 0023 G3: every argument name a client sends is camelCase."""
    for tool in _doc()["tools"]:
        for name in tool["inputSchema"]["properties"]:
            assert "_" not in name, (tool["name"], name)
