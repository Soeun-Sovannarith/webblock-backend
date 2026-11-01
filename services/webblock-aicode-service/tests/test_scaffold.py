import json
import pytest
from app.services.catalog import CATALOG, validate_page_path, validate_puck
import re
from app.services.scaffold import (FULLSTACK_PATHS, HARNESS_OWNED, HARNESS_OWNED_DIRS, PORTAL_PATHS, PUCK_DATA_PATH,
                                   REFRESHED_TEMPLATES, REGISTRY_PATH, RUNTIME_PATHS, SEED_PATH,
                                   apply_puck_update, block_path, catalog_schema, implemented_types, refresh_templates, render_site)

HOME = {"root": {"props": {"title": "Khmer Clay Shop"}}, "content": [
    {"type": "Hero", "props": {"id": "h1", "title": "Handmade pottery", "subtitle": "Made with care"}},
    {"type": "ProductCards", "props": {"id": "p1", "title": "Shop", "products": [{"name": "Bowl", "price": "$12"}]}},
]}

def _page(path, tree, sort_order=0):
    return {"page_id": path, "name": path, "path": path, "seo_title": None, "seo_description": None,
            "sort_order": sort_order, "puck_tree": tree}

INPUT = {"pages": [_page("/about", {"root": {}, "content": []}, 1), _page("/", HOME)], "drizzle_schema": None, "instructions": None}

def test_render_site_is_complete_next_project():
    files = render_site(INPUT)
    for path in ["package.json", "tsconfig.json", "app/layout.tsx", "app/[[...slug]]/page.tsx", "db/schema.ts", REGISTRY_PATH]:
        assert path in files
    assert "app/page.tsx" not in files
    site = json.loads(files[PUCK_DATA_PATH])
    assert [p["path"] for p in site["pages"]] == ["/", "/about"]
    assert site["pages"][0]["data"] == HOME
    assert implemented_types(files) == set(CATALOG)
    assert "\\n" not in files[REGISTRY_PATH]

def test_db_schema_is_always_the_shared_catalog():
    """No site can add tables to the shared catalog, so a drizzle_schema in the input never reaches the site."""
    files = render_site({**INPUT, "drizzle_schema": "export const x = 1;"})
    assert files["db/schema.ts"] == catalog_schema() and "export const x" not in files["db/schema.ts"]
    assert 'pgSchema("catalog")' in catalog_schema()

def test_apply_puck_update_restores_missing_block():
    files = render_site(INPUT)
    del files[block_path("Footer")]
    footer_page = _page("/", {"root": {}, "content": [{"type": "Footer", "props": {"text": "bye"}}]})
    added = apply_puck_update(files, {**INPUT, "pages": [footer_page]})
    assert added == ["Footer"]
    assert 'import Footer from "./blocks/Footer";' in files[REGISTRY_PATH]

@pytest.mark.parametrize("bad", [
    {"content": "nope"},
    {"content": [{"type": "Script", "props": {}}]},
    {"content": [{"type": "Text", "props": {"text": {"html": "<b>"}}}]},
    {"content": [{"type": "ProductCards", "props": {"products": "x"}}]},
])
def test_validate_rejects_bad_documents(bad):
    with pytest.raises(ValueError):
        validate_puck(bad)

@pytest.mark.parametrize("path", ["/", "/about", "/shop/bowls", "/a-b/c1"])
def test_valid_page_paths(path):
    assert validate_page_path(path) == path

@pytest.mark.parametrize("path", ["", "about", "/About", "/a/", "/a//b", "/../x", "/a b", "/-a"])
def test_invalid_page_paths(path):
    with pytest.raises(ValueError):
        validate_page_path(path)

def test_style_props_are_validated():
    styled = {"root": {"props": {"title": "x", "theme": "dark", "accent": "#ff8800", "font": "serif"}},
              "content": [{"type": "Hero", "props": {"id": "h1", "tone": "accent", "align": "left", "spacing": "compact"}}]}
    validate_puck(styled)
    validate_puck({"root": {"props": {"accent": ""}}, "content": []})  # empty accent = the theme's own

@pytest.mark.parametrize("bad", [
    {"root": {"props": {"theme": "neon"}}, "content": []},
    {"root": {"props": {"accent": "red"}}, "content": []},
    {"root": {"props": {"accent": "#fff; background:url(x)"}}, "content": []},
    {"content": [{"type": "Text", "props": {"tone": "purple"}}]},
])
def test_style_props_reject_unknown_values(bad):
    with pytest.raises(ValueError):
        validate_puck(bad)

def test_render_site_wraps_pages_in_theme():
    files = render_site(INPUT)
    assert "renderPage(withSiteData(page" in files["app/[[...slug]]/page.tsx"]
    assert "blockAttributes(block.props)" in files[REGISTRY_PATH]
    assert {"components/blocks/theme.ts", "components/blocks/theme.css"} <= set(files)
    assert "theme" not in implemented_types(files)

def test_apply_puck_update_refreshes_harness_templates():
    files = render_site(INPUT)
    files["app/[[...slug]]/page.tsx"] = "// generated before theme support"
    del files["components/blocks/theme.css"]
    apply_puck_update(files, INPUT)
    assert "renderPage" in files["app/[[...slug]]/page.tsx"]
    assert ".wb-site" in files["components/blocks/theme.css"]

def _product_slug_source(source):
    start = source.index("function productSlug(")
    return source[start:source.index("\n}\n", start)]

def test_product_cards_link_to_detail_pages_with_the_catalog_slug():
    files = render_site(INPUT)
    catalog, cards = files["lib/products.ts"], files[block_path("ProductCards")]
    assert 'slug[0] === "products"' in files["app/[[...slug]]/page.tsx"]
    assert "/products/${productSlug(" in cards
    assert _product_slug_source(catalog) == _product_slug_source(cards)

def test_render_site_seeds_the_puck_products():
    seed = render_site(INPUT)[SEED_PATH]
    assert "INSERT INTO catalog.products" in seed and "('Bowl', NULL, 12.00::numeric(12,2), 'general', 0)" in seed
    assert "('General', 'general', 0)" in seed  # its category
    assert "WHERE NOT EXISTS (SELECT 1 FROM catalog.products p WHERE p.website_id = current_setting('webblock.website_id')::uuid)" in seed
    assert "business_profiles" not in seed

def test_onboarding_seed_is_rendered_from_data_on_every_run():
    files = render_site({**INPUT, "seed": {"business": None, "products": [{"name": "Teapot"}]}})
    assert "('Teapot', NULL, NULL::numeric(12,2), 'general', 0)" in files[SEED_PATH]
    # Stored seeds are data only: a smuggled SQL string is never written to the site.
    apply_puck_update(files, {**INPUT, "seed": {"products": [{"name": "Cup"}], "sql": "DROP TABLE products;"}})
    assert "('Cup', NULL, NULL::numeric(12,2), 'general', 0)" in files[SEED_PATH] and "DROP" not in files[SEED_PATH]

def test_apply_puck_update_refreshes_fullstack_files():
    files = render_site(INPUT)
    for path in FULLSTACK_PATHS:
        files.pop(path)
    apply_puck_update(files, INPUT)
    assert set(FULLSTACK_PATHS) <= set(files)
    assert '"postgres"' in files["package.json"]
    assert set(FULLSTACK_PATHS) <= HARNESS_OWNED

def test_runtime_files_meet_provisionings_site_contract():
    """Provisioning builds the repository's Dockerfile and runs the standalone server on port 8080 as UID 65532,
    probing /healthz and /readyz; existing sites receive these files on their next update."""
    files = render_site(INPUT)
    assert set(RUNTIME_PATHS) <= set(files) and set(RUNTIME_PATHS) <= HARNESS_OWNED
    dockerfile = files["Dockerfile"]
    for line in ("PORT=8080", "HOSTNAME=0.0.0.0", "USER 65532:65532", "npm ci --ignore-scripts",
                 "ln -s /tmp/next-cache /app/.next/cache", 'CMD ["node", "server.js"]'):
        assert line in dockerfile
    assert 'output: "standalone"' in files["next.config.mjs"]
    lock = json.loads(files["package-lock.json"])
    assert lock["packages"][""]["dependencies"] == json.loads(files["package.json"])["dependencies"]
    assert "PGHOST" in files["lib/db.ts"] and "process.env.DATABASE_URL" not in files["app/readyz/route.ts"]
    for path in RUNTIME_PATHS:
        files.pop(path)
    apply_puck_update(files, INPUT)
    assert set(RUNTIME_PATHS) <= set(files)

def test_portal_path_is_reserved():
    for path in ("/healthz", "/readyz"):
        with pytest.raises(ValueError):
            validate_page_path(path)
    for path in ("/portal", "/portal/products"):
        with pytest.raises(ValueError):
            validate_page_path(path)
    assert validate_page_path("/portals") == "/portals"


def test_owner_portal_ships_in_every_site_and_is_harness_owned():
    files = render_site(INPUT)
    for path in ("app/portal/layout.tsx", "app/portal/page.tsx", "app/portal/(owner)/products/page.tsx",
                 "app/portal/(owner)/info/page.tsx", "app/portal/(owner)/monitoring/page.tsx",
                 "portal/portal.tailwind.css", "portal/features/products/api.ts", "app/media/products/[id]/route.ts",
                 "lib/business.ts"):
        assert path in files and path in PORTAL_PATHS + ("lib/business.ts",)
    assert set(PORTAL_PATHS) <= set(REFRESHED_TEMPLATES) <= HARNESS_OWNED
    assert all(p.startswith(HARNESS_OWNED_DIRS) for p in PORTAL_PATHS)
    # The standalone Portal_UI's mock data, demo delays and Google Fonts stay out of the sites.
    portal = "\n".join(files[p] for p in PORTAL_PATHS)
    assert "@/mocks" not in portal and "lib/api/delay" not in portal and "next/font" not in portal


def test_refresh_templates_removes_files_the_portal_no_longer_has():
    """Sites from before the portal moved to portal/ still carry the single-page portal's files; GLM can't write to
    the harness-owned directories, so anything there that the templates lack goes."""
    files = render_site(INPUT)
    files.update({"app/portal/actions.ts": "old", "app/portal/portal.css": "old", "lib/extra.ts": "glm"})
    assert refresh_templates(files) == ["app/portal/actions.ts", "app/portal/portal.css"]
    assert "lib/extra.ts" in files and set(REFRESHED_TEMPLATES) <= set(files)


_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*["'](@/[^"']+|\.{1,2}/[^"']+)["']""")

def test_every_site_import_resolves():
    """No JS tests run here, so at least every @/ and relative import of a generated site must name a file in it."""
    import posixpath
    files = render_site(INPUT)
    missing = []
    for source, text in files.items():
        if not source.endswith((".ts", ".tsx", ".css")):
            continue
        for spec in _IMPORT.findall(text):
            base = spec[2:] if spec.startswith("@/") else posixpath.normpath(posixpath.join(posixpath.dirname(source), spec))
            if not any(c in files for c in (base, *(base + ext for ext in (".ts", ".tsx", ".css", ".json")),
                                            base + "/index.ts", base + "/index.tsx")):
                missing.append(f"{source}: {spec}")
    assert missing == []
