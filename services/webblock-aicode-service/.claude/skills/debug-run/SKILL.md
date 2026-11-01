---
name: debug-run
description: Diagnose a failed, stuck or skipped harness run (generation or update) from its run id, project id or request id, using the run log, check report, validation rows and lease state. Use when a run failed, hangs in QUEUED/RUNNING, or a build/push/deploy was skipped.
---

# Debug a harness run

Gather the evidence first, then read the code. Don't change anything until you know which node failed and why.

## 1. Run state
```bash
docker compose exec db psql -U webblock webblock -c "
SELECT harness_run_id, project_id, generation_request_id, status, strategy, attempt, error_code, left(errors, 300) AS errors,
       model_name, started_at, completed_at, lease_expires_at
FROM ai_codegen.harness_runs WHERE harness_run_id = '<run_id>';"   # or WHERE project_id = '<id>' ORDER BY created_at DESC LIMIT 5
```
- **`QUEUED` that never starts.** Check that a worker is running (`RUN_WORKER`, or `python -m app.worker`). Only one QUEUED/RUNNING run per project is allowed (`ux_one_active_run_per_project`).
- **`RUNNING` with a past `lease_expires_at`.** The worker crashed. `reap_expired_runs` requeues the run until `run_max_attempts`.
- **`error_code`** is the `StepError` code: `BUILD_FAILED`, `GLM_FAILED`, `GITHUB_FAILED`, `DEPLOY_FAILED`, `INTERNAL_ERROR`, `CANCELLED`, `*_SKIPPED`.

## 2. Run log and check report
They live under `var/artifacts/<project_id>/<run_id>/` (`ARTIFACT_DIR`), or come from `GET /v1/generation-runs/<run_id>` (`log`, `step`) and `GET /v1/generation-runs/<run_id>/check-report`.
```bash
cat var/artifacts/<project_id>/<run_id>/run.log
```
The last `[STEP]` marker is the step that failed. `SKIPPED:` lines explain a skipped build, push or deploy, usually `BUILD_RUNNER=skip` or missing GitHub/deploy config. `LangGraph autonomous repair loop` lines mean the build failed and `compiler_repair` ran.

## 3. Build output
```bash
docker compose exec db psql -U webblock webblock -c "
SELECT validation_type, attempt, status, left(error, 500) AS error, result
FROM ai_codegen.validation_runs WHERE harness_run_id = '<run_id>' ORDER BY created_at;"
```
`INSTALL` runs with network and `--ignore-scripts`. `BUILD` (`next build`) runs offline. Check that the Docker daemon is up (`docker info`). Next.js 16 / React 19 errors in GLM-written files are the usual cause. The rendered source is in that run's `SOURCE_ARCHIVE` tarball:
```bash
tar -tzf var/artifacts/<project_id>/<run_id>/source.tar.gz
tar -xzOf var/artifacts/<project_id>/<run_id>/source.tar.gz components/blocks/Hero.tsx   # print one file
```

## 4. Reproduce locally
Write a unit test that patches the boundary (`glm._chat`, `store.*`, `build_runner.build`) as `tests/test_graph_pipeline.py` does. Use `BUILD_RUNNER=skip` for an end-to-end run without Docker. Report the root cause to the user before fixing it.
