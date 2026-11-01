# Puck Component Modification Rules

When adding, modifying, or removing a visual Puck component, you must keep all four synchronisation points aligned.

## The 4-File Synchronization Rule

A component change requires updating the following 4 files:

1. **Backend Catalog Validation** (`app/services/catalog.py`):
   - Register component name in `CATALOG`.
   - Define allowed props and schema definitions.
   - Register style props if applicable (`STYLE_PROPS`).

2. **Template Block Implementation** (`app/templates/site/components/blocks/<Type>.tsx`):
   - Implement the self-contained React 19 component.
   - Component must only import React (no external heavy libraries).
   - Add styles to `app/templates/site/components/blocks/blocks.css`.
   - All CSS class names must be prefixed with `wb-` (e.g. `wb-hero`, `wb-card`).

3. **Frontend Editor Configuration** (`frontend/lib/puck-config.tsx`):
   - Define field inputs, defaults, and dropzones for Puck's visual canvas.

4. **Database Definitions Seed** (`sql/002_ai_codegen.sql` or new `sql/00N_*.sql`):
   - Insert/update the definition row in `ai_codegen.puck_component_definitions`.
   - Ensure SQL migrations are idempotent.

## Styling System & Theme Propagation

- **Root Properties**: Page-level properties (`theme`, `accent`, `font`) defined in `catalog.py`.
- **Block Properties**: Standard `STYLE_PROPS` (`tone`, `align`, `spacing`) attached to every block.
- **Data Attributes**: `theme.ts` maps props into `data-tone`, `data-align`, `data-spacing` attributes rendered on block containers.
- **CSS Selectors**: `theme.css` targets `[data-tone="..."]` attributes for unified design theming.
