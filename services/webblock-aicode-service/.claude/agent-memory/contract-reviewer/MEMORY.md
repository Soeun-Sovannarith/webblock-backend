# contract-reviewer memory

Recurring pitfalls and known open issues in this repo. One line each, with paths. Keep this current: delete entries once they are fixed.

## Known open issues (as of 2026-10-28, verified in code)
- `STRICT_PROMOTION` is not enforced. Only `pipeline._skipped` and `pipeline._deploy` read `settings.strict_promotion`, and neither is called since the LangGraph switch (`graph_pipeline.py` writes `SKIPPED:` lines directly).
- The `SKIPPED:` line format changed from `SKIPPED: <msg>` (old `pipeline._skipped`) to `SKIPPED: [STEP] <msg>` (`graph_pipeline.py` build/push/deploy nodes). The frontend parser compatibility is unverified.
- Dead code in `app/services/pipeline.py`: `_prepare_update`, `_build`, `_deploy`, `_load` have no callers.
- `glm._chat`/`_chat_with_instructor` take `response_model` (default `LLMCodeResponse`) since 2026-10-29, but `propose_change_set` still passes none, so its Instructor attempt always fails and it falls back to plain HTTP (documented in `rules/codegen-guardrails.md`).
- Event path `harness.handle_generation_requested_event` calls `store.capture_input` without baseline carry-over, so `drizzle_schema` and `seed_sql` of an onboarded project drop to None (template schema.ts, Puck-derived seed.sql). Only `enqueue_generation` carries them over.
- `harness.handle_generation_requested_event` returns `{"status": "CONFLICT"}` on `ActiveRunError` (event path). Only the HTTP path maps that to a 409.

## Recurring pitfalls
- New block type: `tests/test_scaffold.py` asserts `implemented_types(files) == set(CATALOG)`, so the catalog key and the file name must match exactly.
- `db/schema.ts` is NOT in `scaffold.REFRESHED_TEMPLATES`; `graph_pipeline.prepare_node` rewrites it only when `drizzle_schema` changes. Template code reading new DB columns (`lib/products.ts`, `db/seed.sql`) reaches existing sites whose schema.ts still lacks them.
- New captured-input field: check `enqueue_generation` carry-over, the event path in `harness.py`, `change_detector.detect_change`, `request_hash` in `harness.py` (adding a key changes every hash -> Idempotency-Key 422 across deploys).
- Lists of harness-owned / refreshed files are duplicated in `.claude/CLAUDE.md`, `skills/update-site-template`, `.agents/rules/codegen-pipeline.md`, `.agents/rules/security-guardrails.md`; grep them when `scaffold.py` sets change.
- The seed in `sql/002_ai_codegen.sql` is `ON CONFLICT DO NOTHING`, so editing it never reaches existing DBs. Changes need a new numbered file (pattern: `005_style_props.sql`).
