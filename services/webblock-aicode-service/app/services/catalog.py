import re
from typing import Any

# Supported Puck components and their editable props. Must stay in sync with the
# frontend Puck config (frontend/lib/puck-config.tsx) and the block templates in
# app/templates/site/components/blocks/. A list value describes an array of objects, "one of: a, b" a fixed choice
# and COLOR a hex color. Style props (ROOT_PROPS theme/accent/font, STYLE_PROPS on every block) are applied by
# components/blocks/theme.ts and theme.css, so the look of a site is Puck data, like its content.
CATALOG_VERSION = "1.0.0"
ONE_OF = "one of: "
COLOR = "hex color like #a4532b, or empty for the theme's accent"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

def one_of(*values: str) -> str:
    return ONE_OF + ", ".join(values)

STYLE_PROPS: dict[str, Any] = {
    "tone": one_of("default", "surface", "accent", "contrast"),
    "align": one_of("default", "left", "center"),
    "spacing": one_of("default", "compact", "spacious"),
}
CATALOG: dict[str, dict[str, Any]] = {
    "Hero": {"title": "string", "subtitle": "string", **STYLE_PROPS},
    "Text": {"text": "string", **STYLE_PROPS},
    "ProductCards": {"title": "string", "products": [{"name": "string", "price": "string", "description": "string"}],
                     **STYLE_PROPS},
    "About": {"title": "string", "text": "string", **STYLE_PROPS},
    "Location": {"title": "string", "address": "string", **STYLE_PROPS},
    "Footer": {"text": "string", **STYLE_PROPS},
}
ROOT_PROPS: dict[str, Any] = {
    "title": "string",
    "theme": one_of("warm", "light", "dark"),
    "accent": COLOR,
    "font": one_of("sans", "serif", "mono"),
}
MAX_BLOCKS = 200
MAX_PAGES = 50
PAGE_PATH = re.compile(r"^/(?:[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*)?$")
EMPTY_TREE: dict[str, Any] = {"root": {"props": {}}, "content": []}

def _check_props(props: dict[str, Any], spec: dict[str, Any], where: str) -> None:
    for key, kind in spec.items():
        if key not in props or props[key] is None:
            continue
        value = props[key]
        if kind == "string":
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise ValueError(f"{where}.{key} must be a string")
        elif kind == COLOR:
            if not isinstance(value, str) or (value and not HEX_COLOR.match(value)):
                raise ValueError(f"{where}.{key} must be a hex color like #a4532b")
        elif isinstance(kind, str) and kind.startswith(ONE_OF):
            options = kind[len(ONE_OF):].split(", ")
            if value not in options:
                raise ValueError(f"{where}.{key} must be one of {', '.join(options)}")
        elif isinstance(kind, list):
            if not isinstance(value, list):
                raise ValueError(f"{where}.{key} must be an array")
            for i, item in enumerate(value):
                if not isinstance(item, dict):
                    raise ValueError(f"{where}.{key}[{i}] must be an object")
                _check_props(item, kind[0], f"{where}.{key}[{i}]")

def validate_puck(puck: dict[str, Any]) -> list[dict[str, Any]]:
    content = puck.get("content")
    if not isinstance(content, list):
        raise ValueError("Expected Puck JSON with a 'content' array.")
    if len(content) > MAX_BLOCKS:
        raise ValueError(f"Too many components (max {MAX_BLOCKS}).")
    root = puck.get("root", {})
    if not isinstance(root, dict) or not isinstance(root.get("props", {}), dict):
        raise ValueError("root.props must be an object")
    _check_props(root.get("props", {}), ROOT_PROPS, "root.props")
    for i, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") not in CATALOG:
            raise ValueError(f"Unsupported component at content[{i}]")
        props = block.get("props", {})
        if not isinstance(props, dict):
            raise ValueError(f"content[{i}].props must be an object")
        _check_props(props, CATALOG[block["type"]], f"content[{i}].props")
    return content

# Served by the generated site itself (the product portal, and the platform's health and readiness checks), so a
# Puck page there would never be shown.
RESERVED_PAGE_PATHS = ("/portal", "/healthz", "/readyz")

def validate_page_path(path: str) -> str:
    if not PAGE_PATH.match(path) or len(path) > 255:
        raise ValueError("path must look like / or /about or /shop/bowls (lowercase letters, digits and dashes)")
    if any(path == p or path.startswith(p + "/") for p in RESERVED_PAGE_PATHS):
        raise ValueError(f"path {path} is reserved: the site serves it itself")
    return path

def block_types(puck: dict[str, Any]) -> set[str]:
    return {b["type"] for b in puck.get("content", []) if isinstance(b, dict) and "type" in b}

def input_block_types(captured_input: dict[str, Any]) -> set[str]:
    """Block types used on any page of a codegen input (see store.capture_input for its shape)."""
    return set().union(*(block_types(p["puck_tree"]) for p in captured_input["pages"])) if captured_input["pages"] else set()
