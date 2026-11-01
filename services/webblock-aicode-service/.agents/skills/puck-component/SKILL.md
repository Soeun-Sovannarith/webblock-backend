---
name: puck-component
description: Use this skill when creating, modifying, or deleting Puck visual builder components, style props, or catalog definitions.
---

# Puck Component Authoring & Synchronization

This skill provides step-by-step guidance for adding or modifying visual Puck components across the WebBlock ecosystem.

## 4-Step Checklist

When introducing a new component `<ComponentName>`:

### Step 1: Update Backend Catalog Validation
Edit `app/services/catalog.py`:
- Add `<ComponentName>` to `CATALOG`:
  ```python
  "<ComponentName>": {
      "default_props": { ... },
      "props_schema": { ... }
  }
  ```
- If the component supports standard styles, ensure `STYLE_PROPS` are accepted.

### Step 2: Implement Template Block Component
1. Create `app/templates/site/components/blocks/<ComponentName>.tsx`:
   - Keep the component self-contained.
   - Import only from `react`.
   - Apply `data-*` attributes for styling support (`data-tone`, `data-align`, `data-spacing`).
2. Add corresponding styling in `app/templates/site/components/blocks/blocks.css`:
   - Prefix all CSS classes with `wb-` (e.g. `.wb-<component-name>`).

### Step 3: Configure Frontend Editor
Update `frontend/lib/puck-config.tsx`:
- Define fields (`render`, `label`, `fields` with text, select, or array inputs).
- Add default values matching `catalog.py`.

### Step 4: Seed Database Definitions
Add an idempotent migration in `sql/` (or update seed if resetting):
```sql
INSERT INTO ai_codegen.puck_component_definitions (
    name,
    description,
    allowed_operations,
    props_schema
) VALUES (
    '<ComponentName>',
    'Description of the component',
    ARRAY['ADD', 'UPDATE', 'REMOVE', 'MOVE'],
    '{}'::jsonb
) ON CONFLICT (name) DO UPDATE SET
    allowed_operations = EXCLUDED.allowed_operations,
    props_schema = EXCLUDED.props_schema;
```
Apply migration with:
```bash
docker compose up migrate
```

## Validation & Verification

1. Run unit tests to verify catalog consistency:
   ```bash
   pytest tests/test_scaffold.py
   ```
2. Verify template rendering in tests:
   ```bash
   pytest -k "test_render_site"
   ```
