from app.services.change_detector import detect_change, diff_puck

def _input(*pages, instructions=None, schema=None):
    """A codegen input (store.capture_input) from (path, puck_tree) pairs."""
    return {"pages": [{"page_id": f"p{i}", "name": path, "path": path, "seo_title": None, "seo_description": None,
                       "sort_order": i, "puck_tree": tree} for i, (path, tree) in enumerate(pages)],
            "drizzle_schema": schema, "instructions": instructions}

def test_equal_input_has_no_changes():
    x = _input(("/", {"root": {}, "content": [{"type": "Text", "props": {"text": "hello"}}]}))
    assert detect_change(x, x)["changed"] is False

def test_property_change_is_json_change():
    a = _input(("/", {"content": [{"type": "Text", "props": {"text": "a"}}]}))
    b = _input(("/", {"content": [{"type": "Text", "props": {"text": "b"}}]}))
    assert detect_change(a, b)["strategy"] == "PUCK_JSON"

def test_structure_change_with_implemented_blocks_is_json_change():
    a = _input(("/", {"content": [{"type": "Text", "props": {"id": "t1", "text": "a"}}]}))
    b = _input(("/", {"content": [{"type": "Hero", "props": {"id": "h1", "title": "Hi"}},
                                  {"type": "Text", "props": {"id": "t1", "text": "a"}}]}))
    result = detect_change(a, b)
    assert result["strategy"] == "PUCK_JSON"
    assert [(i["change_type"], i["operation"], i["target"]) for i in result["items"]] == [("COMPONENT", "ADD", "/:h1")]

def test_page_added_and_removed():
    tree = {"content": []}
    result = detect_change(_input(("/", tree), ("/old", tree)), _input(("/", tree), ("/new", tree)))
    assert result["strategy"] == "PUCK_JSON"
    assert {(i["operation"], i["target"]) for i in result["items"]} == {("REMOVE", "page:/old"), ("ADD", "page:/new")}

def test_missing_block_source_needs_code():
    a = _input(("/", {"content": [{"type": "Text", "props": {"id": "t1", "text": "a"}}]}))
    b = _input(("/", {"content": [{"type": "Text", "props": {"id": "t1", "text": "a"}}]}),
               ("/contact", {"content": [{"type": "Footer", "props": {"id": "f1"}}]}))
    result = detect_change(a, b, implemented_types={"Text"})
    assert result["strategy"] == "MIXED"
    assert any(i["target"] == "components/blocks/Footer.tsx" for i in result["items"])

def test_instructions_alone_are_source_code():
    tree = {"content": [{"type": "Text", "props": {"text": "a"}}]}
    result = detect_change(_input(("/", tree)), _input(("/", tree), instructions="Make the hero background dark blue"))
    assert result["changed"] is True
    assert result["strategy"] == "SOURCE_CODE"
    assert result["items"][0]["change_type"] == "FUNCTIONALITY"

def test_schema_change_is_source_code():
    tree = {"content": []}
    result = detect_change(_input(("/", tree)), _input(("/", tree), schema="export const x = 1;"))
    assert result["strategy"] == "SOURCE_CODE"
    assert result["items"][0]["target"] == "db/schema.ts"

def test_diff_detects_update_remove_reorder_and_root():
    a = {"root": {"props": {"title": "A"}}, "content": [
        {"type": "Hero", "props": {"id": "h", "title": "x"}},
        {"type": "Text", "props": {"id": "t", "text": "y"}},
        {"type": "Footer", "props": {"id": "f", "text": "z"}},
    ]}
    b = {"root": {"props": {"title": "B"}}, "content": [
        {"type": "Text", "props": {"id": "t", "text": "changed"}},
        {"type": "Hero", "props": {"id": "h", "title": "x"}},
    ]}
    ops = {(i["operation"], i["target"]) for i in diff_puck(a, b)}
    assert ops == {("UPDATE", "root"), ("REMOVE", "f"), ("UPDATE", "t"), ("REORDER", "content")}
