---
paths:
  - "app/services/glm.py"
  - "app/services/build_runner.py"
  - "app/services/scaffold.py"
  - "app/services/graph_pipeline.py"
---

# Guardrails on LLM-written code

GLM output is untrusted input. It gets written to disk, built and deployed. These checks are the security boundary, so don't weaken one without the user's OK, and cover any change with a test in `tests/test_glm.py`.

**Paths (`glm.validate_changes`)**
- Normalize with `posixpath.normpath(path.replace("\\", "/"))`. Reject empty paths, a leading `/`, any `..` segment, null bytes and drive letters.
- The result must match `ALLOWED_PATH` (`(app|components|lib|db)/**/*.(ts|tsx|css)`). It must not be in `scaffold.HARNESS_OWNED` or `glm.RESERVED_PATHS`.
- Keep `db/schema.ts` harness-owned (`scaffold.SCHEMA_PATH`): it describes the shared `catalog` tables, which the site's role can't change, so a table GLM added would build and then fail at runtime. The code prompts in `glm.py` tell GLM to query only the tables in `db/schema.ts` without the `catalog.` prefix, never name `website_id` (the database fills it in and row-level security limits every query to the site), and never create or alter tables; keep that wording in all three. `scaffold.REMOVED_TEMPLATES` stay harness-owned so GLM can't recreate the deleted per-site database setup.
- The path rules live in `_allowed_path`; `editable(path)` answers the same question, and the repair prompt uses it to list the files the model may change (`_repair_scope`), including which files named in the build error are harness-owned. Change the rules there, not in a copy.
- Every GLM entry point (`request_code_update`, `request_code_from_puck`, `request_code_update_from_puck`, `request_compiler_repair`) goes through `_code_request`, so it returns `changes` that have already passed `validate_changes`. A new code entry point must use it too, before `apply_changes` sees them.
- **Garbled files** (`_garbled`): a `.ts`/`.tsx` file of 300+ characters on fewer than three lines (the first `//` comment would swallow the rest, `export default` included), or with a line break turned into a stray `n`, raises `GLMGarbledError`. `_code_request` then asks once more, adding up the tokens; a second garbled reply raises. Qwen3-Coder-30B did this in 4 of 16 code requests, and those builds failed. Keep the thresholds loose enough that no template file or normal output trips them (tests in `tests/test_glm.py`).

**LLM transport (`glm._chat`)**
- With `GLM_STREAM` (default) it makes one streaming call (`_chat_stream`, server-sent events; `reasoning_content` is dropped). The GLM server sits behind Cloudflare, which answers 524 when an origin sends nothing for ~100 s, so a non-streaming code generation can fail there. Keep long calls streaming.
- With `GLM_STREAM=false` it first tries Instructor + LiteLLM with `response_model`, then a plain `httpx` JSON-mode call.
- `GLM_THINKING=false` (default) adds `_thinking_options()` to every call; thinking was most of a code generation's time. `GLMTransientError` (connection error, 429, 5xx) is retried once; timeouts and 4xx are not.
- `_context` leaves out `CONTEXT_SKIP`/`CONTEXT_SKIP_DIRS` (portal, sign-in, media and the `lib/storage.ts`/`lib/media.ts` helpers, business profile, seed, theme.css, runtime files, docs): files GLM may not change and blocks don't import. `db/schema.ts` and `lib/db.ts` stay in the context, because GLM must see which tables exist and how to connect. Remove a path from `CONTEXT_SKIP` when its template is deleted. Never send `lib/portal-auth.ts`; add a new harness-owned file there unless GLM needs to read it.
- Every path returns `(raw_json_text, usage)`, and tests patch `glm._chat` at that boundary (`test_glm.py` fakes `httpx.stream` for the transport itself). Callers validate the reply themselves (`parse_response`, `LLMSeedResponse`), so streaming skips no check.
- `LLMCodeResponse` is the code-change shape and `_chat`'s default `response_model`. A prompt with a different output shape must pass its own (`request_seed_products` passes `LLMSeedResponse`). `propose_change_set` still uses the default, so with `GLM_STREAM=false` its Instructor attempt fails validation and it falls back to plain HTTP.
- `request_seed_products` returns rows, never SQL. `seed_generator` escapes them into `db/seed.sql`, which is harness-owned.

**Workspace and build sandbox (`build_runner`)**
- `write_workspace` resolves every target and refuses one outside the workspace (`is_relative_to`). Keep that check ahead of any write.
- Both runners (Docker SDK and `docker run` CLI) keep `cap_drop ALL`, `no-new-privileges`, memory/CPU limits (`BUILD_MEMORY`/`BUILD_CPUS`), `pids_limit 512` and the host UID/GID. Only `INSTALL` (with `--ignore-scripts`) gets network. `BUILD` runs with `--network none`. Change both runners together.
- `BUILD_RUNNER=local` runs GLM-written code on the host. Never make it the default.
