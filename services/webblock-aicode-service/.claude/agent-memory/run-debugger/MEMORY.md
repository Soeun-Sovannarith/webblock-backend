# run-debugger memory

Known failure signatures, as `signature → cause → fix`. Add one when you confirm a new recurring one.

- Run stays `QUEUED` → no worker (`RUN_WORKER=false` with no `python -m app.worker`), or another run for the project is active (`ux_one_active_run_per_project`) → start a worker, or wait for or cancel the active run.
- `RUNNING` with `lease_expires_at` in the past → the worker died mid-step → `reap_expired_runs` requeues it up to `run_max_attempts` (default 3), then fails it.
- `GLM_FAILED` "GLM is not configured" → `GLM_BASE_URL`/`GLM_API_KEY` unset while `CODEGEN_MODE=llm` or `instructions` were given → set the key, or use hybrid without instructions.
- `GLM_FAILED` mentioning Cloudflare Access → self-hosted endpoint behind Access → set `GLM_CF_ACCESS_CLIENT_ID`/`SECRET`.
- `BUILD_FAILED` with no `INSTALL` validation row → Docker daemon down, or `node:22-alpine` can't be pulled → `docker info`, `docker pull node:22-alpine`.
- `LangGraph autonomous repair loop (attempt 2/2)` followed by `BUILD_FAILED` → GLM couldn't fix the compile error in 2 tries → read the `BUILD` validation `error` and the offending file in `source.tar.gz`.
- `SKIPPED: [GITHUB_PUSH]` / `SKIPPED: [DEPLOY]` but the run SUCCEEDED → no GitHub token/repo or `DEPLOYMENT_PROVIDER=none`. Expected locally, and promoted because `STRICT_PROMOTION` is currently not enforced (see contract-reviewer memory).
