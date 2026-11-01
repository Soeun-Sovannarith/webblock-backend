-- Style props on every catalog component (app/services/catalog.py STYLE_PROPS), applied by the generated site's
-- components/blocks/theme.css. Additive and optional, so catalog 1.0.0 keeps its version. Idempotent: jsonb || merge.
UPDATE ai_codegen.puck_component_definitions
SET editable_props_schema = editable_props_schema || '{"tone":"one of: default, surface, accent, contrast","align":"one of: default, left, center","spacing":"one of: default, compact, spacious"}'::jsonb,
    updated_at = now()
WHERE component_catalog_version = '1.0.0'
  AND NOT editable_props_schema ? 'tone';
