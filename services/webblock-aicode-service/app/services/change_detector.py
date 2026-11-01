from typing import Any
from app.services.catalog import CATALOG

# Strategy values match the Postgres enum ai_codegen.implementation_strategy. Items only feed the
# run summary; they are not persisted (the ERD has no table for detected changes).

def _blocks(puck: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in puck.get("content", []) if isinstance(b, dict)]

def _key(block: dict[str, Any], index: int) -> str:
    props = block.get("props") or {}
    return str(props.get("id") or f"{block.get('type')}#{index}")

def _item(change_type: str, operation: str, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"change_type": change_type, "operation": operation, "target": target, "payload": payload or {}}

def diff_puck(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Component-level diff of two Puck documents, keyed by props.id (falls back to type#index)."""
    items: list[dict[str, Any]] = []
    old_root = (old.get("root") or {}).get("props") or {}
    new_root = (new.get("root") or {}).get("props") or {}
    if old_root != new_root:
        keys = sorted(k for k in set(old_root) | set(new_root) if old_root.get(k) != new_root.get(k))
        items.append(_item("CONTENT", "UPDATE", "root", {"props": keys}))

    old_map = {_key(b, i): b for i, b in enumerate(_blocks(old))}
    new_map = {_key(b, i): b for i, b in enumerate(_blocks(new))}
    for key, block in old_map.items():
        if key not in new_map:
            items.append(_item("COMPONENT", "REMOVE", key, {"type": block.get("type")}))
    for key, block in new_map.items():
        if key not in old_map:
            items.append(_item("COMPONENT", "ADD", key, {"type": block.get("type")}))
            continue
        before = old_map[key]
        if before.get("type") != block.get("type"):
            items.append(_item("COMPONENT", "REPLACE", key, {"from": before.get("type"), "to": block.get("type")}))
            continue
        a, b = before.get("props") or {}, block.get("props") or {}
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            items.append(_item("CONTENT", "UPDATE", key, {"type": block.get("type"), "props": changed}))

    common_old = [k for k in old_map if k in new_map]
    common_new = [k for k in new_map if k in old_map]
    if common_old != common_new:
        items.append(_item("LAYOUT", "REORDER", "content", {"order": list(new_map)}))
    return items

def diff_pages(old_pages: list[dict[str, Any]], new_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Page-level diff of two codegen inputs, keyed by page path; block targets are prefixed with the path."""
    old_map = {p["path"]: p for p in old_pages}
    new_map = {p["path"]: p for p in new_pages}
    items: list[dict[str, Any]] = []
    for path in sorted(set(old_map) - set(new_map)):
        items.append(_item("LAYOUT", "REMOVE", f"page:{path}"))
    for path in sorted(set(new_map) - set(old_map)):
        items.append(_item("LAYOUT", "ADD", f"page:{path}"))
    for path in sorted(set(old_map) & set(new_map)):
        before, after = old_map[path], new_map[path]
        meta = sorted(k for k in ("name", "seo_title", "seo_description", "sort_order") if before.get(k) != after.get(k))
        if meta:
            items.append(_item("CONTENT", "UPDATE", f"page:{path}", {"fields": meta}))
        if before["puck_tree"] != after["puck_tree"]:
            for it in diff_puck(before["puck_tree"], after["puck_tree"]):
                items.append({**it, "target": f"{path}:{it['target']}"})
    return items

def detect_change(old: dict[str, Any] | None, new: dict[str, Any], *,
                  implemented_types: set[str] | None = None) -> dict[str, Any]:
    """Classify a codegen input (store.capture_input) against the baseline input.

    PUCK_JSON: only puck-data.json (and the generated registry and db/seed.sql) changes.
    SOURCE_CODE: no content change, but code must change (AI instructions, Drizzle schema, missing block source).
    MIXED: both.
    """
    implemented = set(CATALOG) if implemented_types is None else implemented_types
    old_pages = old["pages"] if old else []
    items = diff_pages(old_pages, new["pages"]) if old_pages != new["pages"] else []
    if old is not None and new.get("seed") is not None and new.get("seed") != old.get("seed"):
        items.append(_item("CONTENT", "UPDATE", "db/seed.sql", {"source": "seed"}))
    code_items: list[dict[str, Any]] = []
    instructions = new.get("instructions")
    schema_changed = old is not None and (new.get("drizzle_schema") or "").strip() != (old.get("drizzle_schema") or "").strip()

    missing = sorted({b.get("type") for p in new["pages"] for b in _blocks(p["puck_tree"])} - implemented)
    for t in missing:
        code_items.append(_item("COMPONENT", "ADD", f"components/blocks/{t}.tsx", {"type": t, "source": "template"}))
    if schema_changed:
        code_items.append(_item("FUNCTIONALITY", "UPDATE", "db/schema.ts", {"source": "drizzle_schema"}))
    if instructions and instructions.strip():
        code_items.append(_item("FUNCTIONALITY", "UPDATE", "source", {"source": "glm", "request": instructions.strip()}))

    if not items and not code_items:
        return {"changed": False, "strategy": "PUCK_JSON", "summary": "No Puck JSON changes detected.", "items": []}
    if items and code_items:
        strategy, summary = "MIXED", "Content changes plus source code changes."
    elif code_items:
        strategy, summary = "SOURCE_CODE", "Source code changes only."
    else:
        strategy, summary = "PUCK_JSON", "Content-only change; updating puck-data.json."
    if items:
        counts: dict[str, int] = {}
        for it in items:
            counts[it["operation"]] = counts.get(it["operation"], 0) + 1
        summary += " (" + ", ".join(f"{n} {op.lower()}" for op, n in sorted(counts.items())) + ")"
    return {"changed": True, "strategy": strategy, "summary": summary, "items": items + code_items}
