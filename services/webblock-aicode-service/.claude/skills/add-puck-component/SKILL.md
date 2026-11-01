---
name: add-puck-component
description: Add a new Puck block type or change an existing one's props across all four places that must agree (catalog, site template, SQL seed, editor config). Use when asked to add, rename or change a block/component or its fields.
---

# Add or change a Puck component

A block type exists in four places, and they must agree. Do steps 1, 2 and 4 in this repo. Step 3 is in the separate frontend repo, so tell the user exactly what to change there.

## 1. Catalog: `app/services/catalog.py`
Add an entry to `CATALOG`. The value types are `"string"`, `one_of("a", "b")`, `COLOR`, or a one-element list describing an array of objects. Always spread `**STYLE_PROPS`:
```python
"Gallery": {"title": "string", "images": [{"src": "string", "alt": "string"}], **STYLE_PROPS},
```
Don't bump `CATALOG_VERSION` for an additive change.

## 2. Template: `app/templates/site/components/blocks/<Type>.tsx`
The file name must equal the catalog key. Copy the shape of `Text.tsx`:
```tsx
export type GalleryProps = { title?: string; images?: { src?: string; alt?: string }[] };

export default function Gallery({ title = "", images = [] }: GalleryProps) {
  return (
    <section className="wb-section wb-gallery">
      <div className="wb-container">...</div>
    </section>
  );
}
```
- Make every prop optional and give each a default, because Puck may omit any prop. Import only from `react`.
- Leave `tone`/`align`/`spacing` alone: the registry wrapper applies them as `data-*` attributes.
- Put styles in `blocks.css`, with every class prefixed `wb-`.
- Existing sites receive a new block only when a page uses it (`scaffold.ensure_blocks`). A change to an existing block reaches existing sites only through a GLM update. Tell the user about that.

## 3. Editor (frontend repo): `frontend/lib/puck-config.tsx`
Add the component's `fields` and `defaultProps`, matching the catalog keys. The editor copies the block template for its preview, so no other frontend change is needed.

## 4. SQL seed: new `sql/00N_<snake_case>.sql`
Don't edit `002_ai_codegen.sql`: its seed is `ON CONFLICT DO NOTHING`, so an edit there reaches only fresh databases. The real columns are:
```sql
-- Adds the Gallery component definition (catalog 1.0.0). Idempotent: ON CONFLICT DO NOTHING.
INSERT INTO ai_codegen.puck_component_definitions
  (component_catalog_version, component_type, component_label, editable_props_schema, allowed_operations, implementation_mode)
VALUES
  ('1.0.0', 'Gallery', 'Gallery',
   '{"title":"string","images":[{"src":"string","alt":"string"}],"tone":"one of: default, surface, accent, contrast","align":"one of: default, left, center","spacing":"one of: default, compact, spacious"}',
   '["ADD","UPDATE","REMOVE","MOVE"]', 'PUCK_JSON')
ON CONFLICT (component_catalog_version, component_type) DO NOTHING;
```
To change props on an existing row, follow `005_style_props.sql`: an `UPDATE ... SET editable_props_schema = editable_props_schema || '{...}'::jsonb, updated_at = now()` whose `WHERE` matches only rows that haven't been updated yet.

## Verify
```bash
pytest tests/test_scaffold.py          # asserts implemented block types == CATALOG
pytest && ruff check .
docker compose up migrate   # plus the Alembic revision and idempotency test from the new-migration skill
```
If the business onboarding layout should use the block, update `app/services/business_layout.py` as well.
