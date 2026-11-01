---
paths:
  - "app/**/*.py"
---

# Service code (app/)

**Persistence**
- SQL lives in `app/services/store.py` (the generated sites' `catalog` schema: `app/services/site_catalog.py`) as `text()` statements with named parameters (`:id`). Pass UUIDs as `str(...)`, and read rows with `.mappings()` into plain dicts.
- Open sessions with `with SessionLocal() as db:` and call `db.commit()` explicitly.
- In the pipeline, never hold a session across a GLM call, a build, a GitHub push or a deploy. Each step opens its own short session, so pollers see progress and the row lock is never long.

**Pipeline**
- The steps are LangGraph nodes in `app/services/graph_pipeline.py` (`build_pipeline_graph` → `PIPELINE_GRAPH`). `pipeline.execute_run` invokes the graph and maps `BuildError`/`GLMError`/`GitHubError`/`DeploymentError` to `StepError`. Change step behaviour in the node, not in `pipeline.py`.
- A node returns a partial `PipelineState` dict. Copy before mutating (`files = dict(state["files"])`), and keep `state["report"]` as the one report dict that `_fail` and the `CHECK_REPORT` artifact see.
- Nodes start with `store.set_step(run_id, "<STEP>")`. This writes the `[STEP]` log marker and extends the lease. Step names are log markers, not a Postgres enum, but the frontend depends on them: `PREPARE`, `AI_CODE_UPDATE`, `BUILD`, `GITHUB_PUSH`, `DEPLOY`. `compiler_repair`, `template_fallback` and `handle_failure` are graph nodes, not steps: they write log lines but no marker.
- A skipped step writes a `SKIPPED:` log line. `STRICT_PROMOTION` must turn a skip into a failure (`pipeline._skipped` does this). The graph nodes do not call it yet, so the setting has no effect until they do.
- The build → `compiler_repair` → build loop is capped by `route_build_outcome` (2 repairs, and only when `glm.is_configured()`), then `template_fallback` rebuilds `fallback_files` once (`fallback_used`). Don't create a graph cycle without a counter or flag in state.
- GLM is optional unless the run applies new `instructions` to a live site (`ai_required` in `ai_code_update_node`). Optional: catch `GLMError`, log `WARNING: [AI_CODE_UPDATE] …`, keep the `PREPARE` code, and set `fallback_files` only when GLM changed files. Required: let it raise, because an accepted run with the same instructions makes a resend a no-op.
- Expected failures raise `StepError(CODE, message, detail_log)`. `execute_run` must never raise: `_fail` records the failure and publishes the completion event.
- Every GLM result goes through `glm.validate_changes` then `glm.apply_changes`, and then the registry is re-rendered. To change which files GLM may write, edit `glm.ALLOWED_PATH`/`RESERVED_PATHS` or `scaffold.HARNESS_OWNED`, with a test in `tests/test_glm.py`.

**Other services**
- Outbound HTTP (`project_client.py`) is sync `httpx` with `settings.downstream_timeout_seconds`. Failures are logged and returned as a value (a default, or a result like `CodegenInputResult("unavailable")`); they never raise into the caller. Project wraps successful `/internal` bodies in `{success, data, meta}`.
- RabbitMQ publishing is best-effort: `publish_event` returns `False` and logs a warning.
- Event models extend `EventEnvelope` (snake_case, the platform's format), and their wire format is documented in `README.md` section 4. Keep both in sync.
- Pipeline and event code tolerate missing `project` rows (`try/except` with fallbacks), because the Project Service may own them. Keep that when you touch `_load` or `handle_generation_requested_event`.

**Config and routes**
- A new setting goes in `Settings` (`app/core/config.py`) and in `.env.example` with a one-line comment.
- Routes stay thin and call into `app/services/`. Services raise `HTTPException` directly, as `harness.py` does.
- Status, strategy and type strings are UPPERCASE and must match the enums in `sql/`.
