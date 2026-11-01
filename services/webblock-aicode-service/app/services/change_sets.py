import copy
import uuid
from typing import Any
from app.services.catalog import CATALOG, ROOT_PROPS, validate_puck

# Design-assistant change sets: Puck operations on one page (ai_assistant.change_items). Operation names match
# the Postgres enum ai_assistant.change_operation. An item is {operation, component_id, component_type, position,
# props_patch}; position is {"index": n} for ADD and MOVE.
OPERATIONS = ("ADD", "UPDATE", "REMOVE", "MOVE")
ROOT_ID = "root"

class ChangeSetError(ValueError):
    pass

def _index_of(tree: dict[str, Any], component_id: str) -> int:
    for i, block in enumerate(tree.get("content", [])):
        if str((block.get("props") or {}).get("id")) == component_id:
            return i
    raise ChangeSetError(f"No component with id {component_id!r} on this page.")

def _props(value: Any, spec: dict[str, Any], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChangeSetError(f"{where}: props must be an object.")
    unknown = sorted(set(value) - set(spec) - {"id"})
    if unknown:
        raise ChangeSetError(f"{where}: unknown props {', '.join(unknown)}.")
    return {k: v for k, v in value.items() if k != "id"}

def _index(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ChangeSetError(f"{where}: index must be a non-negative integer.")
    return value

def normalize_operations(ops: list[Any], tree: dict[str, Any],
                         allowed: dict[str, set[str]] | None = None) -> list[dict[str, Any]]:
    """Turn the assistant's raw operations into change items, checked against the page and the catalog.
    `allowed` maps component type to its allowed_operations (ai_codegen.puck_component_definitions)."""
    items: list[dict[str, Any]] = []
    for n, op in enumerate(ops, start=1):
        where = f"operation {n}"
        if not isinstance(op, dict) or op.get("op") not in OPERATIONS:
            raise ChangeSetError(f"{where}: op must be one of {', '.join(OPERATIONS)}.")
        kind = op["op"]
        component_id = op.get("component_id")
        if kind == "ADD":
            ctype = op.get("component_type")
            if ctype not in CATALOG:
                raise ChangeSetError(f"{where}: unknown component type {ctype!r}.")
            item = {"component_id": f"{ctype}-{uuid.uuid4().hex[:8]}", "component_type": ctype,
                    "position": {"index": _index(op.get("index", len(tree.get("content", []))), where)},
                    "props_patch": _props(op.get("props", {}), CATALOG[ctype], where)}
        elif kind == "UPDATE" and component_id == ROOT_ID:
            item = {"component_id": ROOT_ID, "component_type": None, "position": None,
                    "props_patch": _props(op.get("props"), ROOT_PROPS, where)}
        else:
            if not isinstance(component_id, str):
                raise ChangeSetError(f"{where}: component_id is required.")
            ctype = tree["content"][_index_of(tree, component_id)].get("type")
            item = {"component_id": component_id, "component_type": ctype, "position": None, "props_patch": None}
            if kind == "UPDATE":
                item["props_patch"] = _props(op.get("props"), CATALOG.get(ctype, {}), where)
            elif kind == "MOVE":
                item["position"] = {"index": _index(op.get("index"), where)}
        ctype = item["component_type"]
        if allowed is not None and ctype is not None and kind not in allowed.get(ctype, set()):
            raise ChangeSetError(f"{where}: {kind} is not allowed for {ctype}.")
        items.append({"operation": kind, **item})
    return items

def apply_items(tree: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply change items in order to a copy of the page tree. Raises ChangeSetError if the result is invalid."""
    out = copy.deepcopy(tree)
    out.setdefault("root", {}).setdefault("props", {})
    content: list[dict[str, Any]] = out.setdefault("content", [])
    for item in items:
        op, cid = item["operation"], item["component_id"]
        if op == "UPDATE" and cid == ROOT_ID:
            out["root"]["props"].update(item["props_patch"] or {})
        elif op == "ADD":
            block = {"type": item["component_type"], "props": {**(item["props_patch"] or {}), "id": cid}}
            content.insert(min(item["position"]["index"], len(content)), block)
        elif op == "UPDATE":
            block = content[_index_of(out, cid)]
            block["props"] = {**(block.get("props") or {}), **(item["props_patch"] or {})}
        elif op == "REMOVE":
            content.pop(_index_of(out, cid))
        elif op == "MOVE":
            block = content.pop(_index_of(out, cid))
            content.insert(min(item["position"]["index"], len(content)), block)
    try:
        validate_puck(out)
    except ValueError as e:
        raise ChangeSetError(f"The change would make the page invalid: {e}") from e
    return out
