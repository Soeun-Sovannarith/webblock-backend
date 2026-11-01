import pytest
from app.services.change_sets import ChangeSetError, apply_items, normalize_operations

TREE = {"root": {"props": {"title": "Shop"}}, "content": [
    {"type": "Hero", "props": {"id": "h1", "title": "Hi", "subtitle": "x"}},
    {"type": "Text", "props": {"id": "t1", "text": "a"}},
]}

def test_update_add_move_remove_round_trip():
    items = normalize_operations([
        {"op": "UPDATE", "component_id": "h1", "props": {"title": "Hello"}},
        {"op": "UPDATE", "component_id": "root", "props": {"title": "Clay"}},
        {"op": "ADD", "component_type": "Footer", "index": 99, "props": {"text": "bye"}},
        {"op": "MOVE", "component_id": "t1", "index": 0},
        {"op": "REMOVE", "component_id": "h1"},
    ], TREE)
    assert [i["operation"] for i in items] == ["UPDATE", "UPDATE", "ADD", "MOVE", "REMOVE"]
    assert items[2]["component_id"].startswith("Footer-")
    out = apply_items(TREE, items)
    assert out["root"]["props"]["title"] == "Clay"
    assert [b["type"] for b in out["content"]] == ["Text", "Footer"]
    assert out["content"][1]["props"] == {"text": "bye", "id": items[2]["component_id"]}
    assert TREE["content"][0]["props"]["title"] == "Hi"  # input untouched

@pytest.mark.parametrize("op, message", [
    ({"op": "DELETE", "component_id": "h1"}, "op must be one of"),
    ({"op": "UPDATE", "component_id": "nope", "props": {}}, "No component"),
    ({"op": "UPDATE", "component_id": "h1", "props": {"color": "red"}}, "unknown props"),
    ({"op": "ADD", "component_type": "Script", "props": {}}, "unknown component type"),
    ({"op": "MOVE", "component_id": "h1", "index": -1}, "non-negative"),
])
def test_invalid_operations_are_rejected(op, message):
    with pytest.raises(ChangeSetError, match=message):
        normalize_operations([op], TREE)

def test_allowed_operations_are_enforced():
    with pytest.raises(ChangeSetError, match="not allowed"):
        normalize_operations([{"op": "REMOVE", "component_id": "h1"}], TREE, {"Hero": {"UPDATE"}})

def test_apply_rejects_invalid_result():
    items = [{"operation": "UPDATE", "component_id": "t1", "component_type": "Text", "position": None,
              "props_patch": {"text": {"html": "<b>"}}}]
    with pytest.raises(ChangeSetError, match="invalid"):
        apply_items(TREE, items)
