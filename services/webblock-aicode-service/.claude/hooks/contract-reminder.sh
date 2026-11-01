#!/usr/bin/env bash
# PostToolUse (Write|Edit): when Claude edits a file that is one side of a cross-file contract, remind it of the
# other sides. Adds context only; never blocks.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
msg=""
case "$f" in
  */app/services/catalog.py|*/app/templates/site/components/blocks/*.tsx)
    msg="Puck component contract: a component type or prop change must also land in app/services/catalog.py, app/templates/site/components/blocks/<Type>.tsx (+ wb- styles in blocks.css), the ai_codegen.puck_component_definitions seed (new idempotent sql/00N_*.sql) and the editor's frontend/lib/puck-config.tsx, which is in the separate frontend repo (tell the user what to change there). Style options also touch theme.ts/theme.css." ;;
  */app/templates/site/components/blocks/theme.ts|*/app/templates/site/components/blocks/theme.css)
    msg="theme.ts/theme.css are recopied into every existing site on its next update (scaffold.REFRESHED_TEMPLATES). Keep data-* attributes in step with ROOT_PROPS/STYLE_PROPS in catalog.py." ;;
  */app/services/pipeline.py|*/app/services/graph_pipeline.py)
    msg="Run-log contract: the frontend parses [STEP] markers and 'SKIPPED:' lines, and step names (PREPARE, AI_CODE_UPDATE, BUILD, GITHUB_PUSH, DEPLOY) must not change. Every finished run must publish ai_codegen.generation_completed.v1. Any graph cycle (compiler_repair) needs a counter in state; run tests/test_graph_pipeline.py." ;;
  */app/services/glm.py|*/app/services/build_runner.py)
    msg="Security boundary for LLM-written code: keep validate_changes on every GLM entry point, the workspace is_relative_to check, and the Docker sandbox flags in BOTH runners (SDK and CLI). Cover the change in tests/test_glm.py." ;;
  */app/schemas/business.py|*/app/services/drizzle_generator.py|*/app/services/business_layout.py|*/app/services/seed_generator.py|*/app/services/onboarding.py)
    msg="Onboarding output is compiled into and run by the site: user values in db/schema.ts strings go through _escape_ts and in comments through _comment_text; seed values only through seed_generator.literal(), never in SQL comments. The products table columns must match lib/products.ts, the template db/schema.ts, seed_generator and sql/006_site_data.sql; every table db/schema.ts declares must exist in site_data. The Puck tree must pass catalog.validate_puck. The onboarding endpoints are public API documented in README.md." ;;
  */app/services/portal_sso.py|*/app/templates/site/lib/portal-auth.ts|*/app/templates/site/app/portal/*)
    msg="Portal SSO contract: ROLES_CLAIM/OWNER_ROLE and the portal_revoked_sessions table must match between portal_sso.py, lib/portal-auth.ts, drizzle_generator.PORTAL_SESSIONS_TABLE and sql/006_site_data.sql; the deploy webhook site_env and the /internal/v1/projects/{id}/portal-owners endpoints are documented in README section 9. Never log the client secret." ;;
  */app/schemas/events.py|*/app/services/event_bus.py)
    msg="Event payloads are a cross-service contract (camelCase, documented in README.md section 4). Update README.md and tests/test_event_bus.py with any change." ;;
  */sql/*.sql)
    msg="Migrations: add the Alembic revision (alembic revision --rev-id 00N -m ...) and keep the SQL idempotent, schema-qualified, enums UPPERCASE, no cross-schema foreign keys, and no columns the ERD lacks without telling the user." ;;
esac
[ -n "$msg" ] || exit 0
jq -n --arg m "$msg" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $m}}'
