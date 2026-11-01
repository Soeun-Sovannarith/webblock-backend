---
name: run-e2e-local
description: Run the service end to end on this machine - start Postgres and the API, create a project, trigger a generation or update, and poll the run to completion. Use to prove a pipeline, template or API change works in the real app, not just in unit tests.
---

# Run a generation end to end locally

The session-context hook already reports whether `.env`, Postgres (:5433) and the API (:8000) are up. Start only what is missing.

1. **Infrastructure**
   ```bash
   docker compose up -d && docker compose up migrate   # Postgres 16 on :5433 + migrations
   ```
   `.env` must exist (`cp .env.example .env`). It is the user's file and you can't read it. The status line shows `BUILD_RUNNER`/`CODEGEN_MODE`.
   - `BUILD_RUNNER=docker` needs `docker info` to succeed.
   - Without Docker, GitHub or deploy config, ask the user to set `BUILD_RUNNER=skip`.
   - `llm` mode and `compiler_repair` need `GLM_API_KEY`.
2. **API + in-process worker**: start it in the background (`run_in_background`) and wait for `/internal/v1/health`:
   ```bash
   .venv/bin/uvicorn app.main:app --port 8000
   curl -s http://localhost:8000/internal/v1/health
   ```
3. **Create a project and generate** (Flow 1):
   ```bash
   curl -s -X POST http://localhost:8000/v1/projects -H 'content-type: application/json' -d '{"name":"E2E demo"}'
   curl -s -X POST http://localhost:8000/v1/projects/<project_id>/generation-requests -H 'content-type: application/json' -d '{
     "puck_json":{"root":{"props":{"title":"Demo","theme":"warm"}},
                  "content":[{"type":"Hero","props":{"id":"hero-1","title":"Hello","subtitle":"E2E"}}]}}'
   ```
   For onboarding instead, send a `BusinessOnboardingRequest` to `POST /v1/onboardings`.
4. **Poll the run** until `status` is `SUCCEEDED` or `FAILED`, every few seconds. The step is the last `[STEP]` marker in `log`:
   ```bash
   curl -s http://localhost:8000/v1/generation-runs/<run_id> | jq '{status, step, error_code, log}'
   ```
5. **Flow 2 (update)**: once the first run has SUCCEEDED, call `POST /v1/projects/<project_id>/generation-requests` again with changed `puck_json` (and optionally `instructions`). Check that the strategy (`PUCK_JSON`/`SOURCE_CODE`/`MIXED`) and the changed files in `GET /v1/generation-runs/<id>/check-report` are what you expect.
6. **Report**: which steps ran, which were `SKIPPED:` and why, and the final status. On failure, switch to the `debug-run` skill. Stop the uvicorn background process when you are done.
