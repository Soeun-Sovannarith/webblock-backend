# Codegen Pipeline & LangGraph Rules

These rules govern the autonomous code generation pipeline, build runner, and LLM code generation interactions.

## 1. LangGraph State Machine Execution Flow

The generation pipeline executes through a compiled LangGraph StateGraph (`app/services/graph_pipeline.py`):
`START` ➔ `prepare` ➔ `ai_code_update` ➔ `build` ➔ (conditional edge) ➔ `github_push` ➔ `deploy` ➔ `END`

1. **Step Name Contract**:
   - Step names in database state and logging must strictly match uppercase Postgres enums: `PREPARE`, `AI_CODE_UPDATE`, `BUILD`, `GITHUB_PUSH`, `DEPLOY`.
   - The frontend parses `[STEP]` markers and `SKIPPED:` lines from the `RUN_LOG` artifact to show real-time progress.
   - Failures raise `StepError(code)`, saved in `harness_runs.error_code` and `generation_requests.failure_code`.

2. **Self-Healing Compiler Repair Loop**:
   - When the `build` node encounters TypeScript or Next.js compiler errors:
     - If `repair_attempts < 2` and LLM is configured, conditional edge routes to `compiler_repair`.
     - The `compiler_repair` node prompts GLM with compiler error diagnostics to repair broken components or syntax.
     - The repaired files are written back and the graph loops to `build` to re-validate.
     - If retries are exceeded, routes to `handle_failure`.

3. **Codegen Strategies**:
   - `PUCK_JSON`: Content-only edits modify `puck-data.json`. No LLM codegen needed.
   - `SOURCE_CODE`: Component or schema changes trigger GLM code generation.
   - `MIXED`: Both Puck JSON and source updates apply.
   - Enums must always be UPPERCASE.

## 2. LLM Code Generation Guardrails

1. **Path Whitelist**:
   - GLM may only create or edit files matching: `(app|components|lib|db)/**/*.(ts|tsx|css)`.
2. **Harness-Owned Reserved Paths**:
   - GLM MUST NEVER touch harness-owned files listed in `scaffold.HARNESS_OWNED`:
     - `app/[[...slug]]/page.tsx` (catch-all Puck router)
     - `components/registry.tsx` (auto-generated component registry)
     - `components/blocks/theme.ts` & `theme.css` (re-synced from template on every run)
     - `app/page.tsx` (reserved; clashes with `app/[[...slug]]/page.tsx`)
     - `package.json`, `tsconfig.json`
     - `db/schema.ts` (describes the shared `site_data` tables; GLM may not add tables) and `db/seed.sql`
     - `scaffold.REMOVED_TEMPLATES` (`drizzle.config.ts`, `db/seed.mjs`: the old per-site database setup, deleted from existing sites on their next update)
     - the fullstack files: `lib/db.ts`, `lib/products.ts`, `lib/business.ts`, `lib/portal-auth.ts`, everything under `app/portal/`, `portal/` and `app/media/` (`scaffold.HARNESS_OWNED_DIRS`), and any `.ts`/`.tsx` sibling of a harness-owned module (`glm.HARNESS_MODULES`)
3. **Structured Response Format**:
   - GLM returns JSON strictly validated against `LLMCodeResponse`:
     `{"summary": string, "files": [{"path": string, "action": "upsert" | "delete", "content": string}]}`.
   - POSIX path normalization, null-byte rejection, and path bounds are enforced before file write.

## 3. Artifact Storage

- Artifacts must be stored under `ARTIFACT_DIR/<project_id>/<harness_run_id>/` and tracked in `ai_codegen.artifacts`.
- Key artifact types:
  - `SOURCE_ARCHIVE`: Deterministic `.tar.gz` archive of the complete generated source tree.
  - `CHECK_REPORT`: JSON summary of changed files, AI modifications, and build status.
  - `RUN_LOG`: Real-time line-delimited execution log streamed to the client.
