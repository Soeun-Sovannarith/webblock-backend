import json
from pathlib import Path
from typing import Any
from app.services.catalog import CATALOG, input_block_types

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "site"
BLOCK_DIR = "components/blocks"
PUCK_DATA_PATH = "puck-data.json"
REGISTRY_PATH = "components/registry.tsx"
SCHEMA_PATH = "db/schema.ts"
SEED_PATH = "db/seed.sql"
PAGE_ROUTE_PATH = "app/[[...slug]]/page.tsx"
THEME_PATHS = (f"{BLOCK_DIR}/theme.ts", f"{BLOCK_DIR}/theme.css")

def _template_files(*prefixes: str) -> tuple[str, ...]:
    """Every template file under these directories (posix paths, sorted)."""
    return tuple(sorted(p.relative_to(TEMPLATE_DIR).as_posix() for prefix in prefixes
                        for p in (TEMPLATE_DIR / prefix).rglob("*") if p.is_file()))

# The owner portal (/portal): its routes and single sign-on routes (app/portal), its screens (portal/, the former
# standalone Portal_UI wired to the site's data) and the product photos it uploads (app/media).
PORTAL_DIRS = ("app/portal/", "portal/", "app/media/")
PORTAL_PATHS = _template_files(*PORTAL_DIRS)
# The database-backed site: the Postgres client (the shared catalog, app/services/site_catalog.py), the catalog
# and business profile queries, single sign-on and the owner portal. package.json carries their npm dependencies.
FULLSTACK_PATHS = ("lib/db.ts", "lib/storage.ts", "lib/media.ts", "lib/products.ts", "lib/business.ts", "lib/portal-auth.ts", *PORTAL_PATHS,
                   SCHEMA_PATH, "package.json")
# What the platform needs to build and run the site (Provisioning's contract for generated sites): the image built
# from the repository root, the standalone server on port 8080, /healthz and /readyz, and the npm lock that the
# Dockerfile's `npm ci` installs exactly.
RUNTIME_PATHS = ("Dockerfile", ".dockerignore", "next.config.mjs", "package-lock.json",
                 "app/healthz/route.ts", "app/readyz/route.ts")
# Copied from the templates on every update too, so sites generated before a template change pick up the
# current page route, theme, fullstack and runtime files (GLM cannot edit them, so nothing project-specific is lost).
REFRESHED_TEMPLATES = (PAGE_ROUTE_PATH, *THEME_PATHS, *FULLSTACK_PATHS, *RUNTIME_PATHS)
# Template files that existing sites still have but the templates no longer do: the per-site database setup
# (drizzle-kit push and the seed runner), replaced by the shared catalog that the harness seeds.
REMOVED_TEMPLATES = ("drizzle.config.ts", "db/seed.mjs")

# Directories the harness owns entirely; GLM may not add, edit or delete files in them (a page.tsx next to a
# route.ts would also break the build).
HARNESS_OWNED_DIRS = (*PORTAL_DIRS, "app/healthz/", "app/readyz/")

# Files the harness owns and rewrites on every run; GLM may not edit them. db/schema.ts describes the shared catalog
# tables, which no site can change, so GLM may not add tables to it either.
HARNESS_OWNED = {PUCK_DATA_PATH, REGISTRY_PATH, SCHEMA_PATH, SEED_PATH, *REFRESHED_TEMPLATES, *REMOVED_TEMPLATES,
                 "tsconfig.json", ".gitignore"}

def block_path(component_type: str) -> str:
    return f"{BLOCK_DIR}/{component_type}.tsx"

def _template(path: str) -> str:
    return (TEMPLATE_DIR / path).read_text(encoding="utf-8")

def implemented_types(files: dict[str, str] | list[str]) -> set[str]:
    prefix = BLOCK_DIR + "/"
    return {p[len(prefix):-len(".tsx")] for p in files
            if p.startswith(prefix) and p.endswith(".tsx") and "/" not in p[len(prefix):]}

def render_puck_data(captured_input: dict[str, Any]) -> str:
    """puck-data.json: every page of the site, in sort order (see lib/puck.ts SiteData)."""
    pages = sorted(captured_input["pages"], key=lambda p: (p["sort_order"], p["path"]))
    site = {"pages": [{"path": p["path"], "name": p["name"], "seo_title": p.get("seo_title"),
                       "seo_description": p.get("seo_description"), "data": p["puck_tree"]} for p in pages]}
    return json.dumps(site, indent=2, ensure_ascii=False) + "\n"

def catalog_schema() -> str:
    """db/schema.ts: the shared catalog tables (sql/007_catalog.sql), the same for every site. Onboarding returns it as
    drizzle_schema; it is copied from the templates, never generated, because no site can add tables."""
    return _template(SCHEMA_PATH)

def refresh_generated(files: dict[str, str]) -> None:
    """Re-derive the generated files after a GLM change: the registry."""
    files[REGISTRY_PATH] = render_registry(implemented_types(files))

def render_seed_sql(captured_input: dict[str, Any]) -> str:
    """db/seed.sql, rendered from data on every run so no stored SQL reaches the site: the onboarding seed when the
    input has one (seed_generator.build_seed), else the Puck ProductCards products."""
    from app.services import seed_generator
    seed = captured_input.get("seed")
    if isinstance(seed, dict):
        return seed_generator.render_seed_sql(seed)
    return seed_generator.render_seed_sql({"products": seed_generator.products_from_input(captured_input)})

def render_registry(types: set[str]) -> str:
    names = sorted(types)
    imports = "\n".join(f'import {n} from "./blocks/{n}";' for n in names)
    return (
        "// Generated by the WebBlock harness on every run. Do not edit.\n"
        'import type { ComponentType } from "react";\n'
        'import type { PuckBlock, PuckData } from "@/lib/puck";\n'
        'import { blockAttributes, siteAttributes } from "./blocks/theme";\n'
        f"{imports}\n\n"
        "// eslint-disable-next-line @typescript-eslint/no-explicit-any\n"
        f"export const blocks: Record<string, ComponentType<any>> = {{ {', '.join(names)} }};\n\n"
        "export function renderBlocks(content: PuckBlock[]) {\n"
        "  return content.map((block, i) => {\n"
        "    const Block = blocks[block.type];\n"
        "    return Block ? (\n"
        "      <div key={block.props?.id ?? i} {...blockAttributes(block.props)}>\n"
        "        <Block {...block.props} />\n"
        "      </div>\n"
        "    ) : null;\n"
        "  });\n"
        "}\n\n"
        "export function renderPage(data: PuckData) {\n"
        "  return <div {...siteAttributes(data.root.props)}>{renderBlocks(data.content)}</div>;\n"
        "}\n"
    )

def ensure_blocks(files: dict[str, str], types: set[str]) -> list[str]:
    """Add template sources for any catalog block types missing from files. Returns the added types."""
    added = sorted(t for t in types - implemented_types(files) if t in CATALOG)
    for t in added:
        files[block_path(t)] = _template(block_path(t))
    return added

def render_site(captured_input: dict[str, Any]) -> dict[str, str]:
    """Flow 1: a complete Next.js + Drizzle project. Every catalog block is included so later
    content-only edits (including adding new block types) never need source changes."""
    files = {
        p.relative_to(TEMPLATE_DIR).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(TEMPLATE_DIR.rglob("*")) if p.is_file()
    }
    files[PUCK_DATA_PATH] = render_puck_data(captured_input)
    files[SEED_PATH] = render_seed_sql(captured_input)
    files[REGISTRY_PATH] = render_registry(implemented_types(files))
    return files

def refresh_templates(files: dict[str, str]) -> list[str]:
    """Copy REFRESHED_TEMPLATES into an existing site, and delete what the templates no longer have from the
    harness-owned directories (GLM can't write there, so anything extra is a template file since removed, such as the
    single-page portal's app/portal/actions.ts) and REMOVED_TEMPLATES. Returns the deleted paths."""
    for path in REFRESHED_TEMPLATES:
        files[path] = _template(path)
    current = set(REFRESHED_TEMPLATES)
    stale = sorted(p for p in files if (p.startswith(HARNESS_OWNED_DIRS) and p not in current) or p in REMOVED_TEMPLATES)
    for path in stale:
        del files[path]
    return stale

def apply_puck_update(files: dict[str, str], captured_input: dict[str, Any]) -> list[str]:
    """Flow 2 JSON edit: rewrite puck-data.json and db/seed.sql, add any missing block sources, refresh the
    harness-owned templates and the registry."""
    refresh_templates(files)
    added = ensure_blocks(files, input_block_types(captured_input))
    files[PUCK_DATA_PATH] = render_puck_data(captured_input)
    files[SEED_PATH] = render_seed_sql(captured_input)
    refresh_generated(files)
    return added
