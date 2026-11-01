# WebBlock AI Code Service - Architecture Rules

These rules govern the architecture, service boundaries, data persistence, and AI orchestration of the WebBlock AI Code Service (`ai_codegen`).

## 1. Multi-Schema PostgreSQL Architecture

The database is divided into four distinct schemas matching the WebBlock microservices mesh:
- `project`: Project metadata, pages, generation requests, and contracts.
- `ai_codegen`: Input versions, harness runs, validation runs, artifacts, and puck component definitions.
- `ai_assistant`: Chat conversations, messages, change sets, and change items.
- `provisioning`: Releases, release steps, provisioning commands, and project hosting.

A fifth schema, `site_data` (`sql/006_site_data.sql`), is not a service's: it holds every generated site's tables, shared by all sites and kept apart by row-level security (`app/services/site_data.py`).

### Strict Data Isolation Rules
1. **Raw SQL Only**: Do not introduce an ORM (e.g. SQLAlchemy ORM models). Use raw SQL queries wrapped in `sqlalchemy.text()` and managed through `app/services/store.py`.
2. **Schema Qualification**: All table references in SQL statements MUST be explicitly schema-qualified (e.g. `SELECT * FROM ai_codegen.harness_runs`, never `FROM harness_runs`).
3. **No Cross-Schema Foreign Keys**: Foreign keys exist strictly within their respective schema. A column referencing an entity from another schema must be a plain `UUID` without a database foreign key constraint.
4. **Idempotent Migrations**: Database changes must be added as a numbered idempotent SQL script in `sql/00N_<description>.sql` (e.g. using `IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).

## 2. AI Orchestration & Modern Stack

The microservice utilizes modern, production-grade AI frameworks:
1. **Workflow Orchestration (LangGraph)**:
   - Stateful execution graph (`app/services/graph_pipeline.py`) manages transitions: `prepare` ➔ `ai_code_update` ➔ `build` ➔ `github_push` ➔ `deploy`.
   - Dynamic conditional routing allows autonomous self-healing: when `build` fails, it loops to `compiler_repair` to fix TypeScript/Next.js errors before re-testing.
2. **Structured LLM Outputs**:
   - LLM interactions in `app/services/glm.py` are streamed by default (`GLM_STREAM`; Instructor + LiteLLM when it is off) and strictly validated through Pydantic response models (`LLMCodeResponse`, `LLMFileChange`).
   - Unified interface allows seamless model upgrades (GLM-4, Claude 3.7, GPT-4o).
3. **Containerized Build Sandbox (Docker Python SDK)**:
   - Build validation in `app/services/build_runner.py` uses the official Docker Python SDK (`docker.from_env()`) with drop capabilities (`--cap-drop ALL`), no-new-privileges, CPU/memory limits, and network lockdown (`--network none` during `npm run build`).

## 3. Request Handling vs. Worker Split

- **Thin API Routes**: HTTP endpoints in `app/api/` perform authentication/validation and delegate immediately to services (`app/services/harness.py`).
- **Input Freezing**: Requests lock the target project, snapshot all pages and Drizzle schemas into `project.generation_requests`, copy to `ai_codegen.input_versions`, and enqueue a `QUEUED` run.
- **Worker Execution**: The background worker (`app/worker.py`) polls and claims jobs using `FOR UPDATE SKIP LOCKED` and triggers `graph_pipeline.PIPELINE_GRAPH`.
- **Concurrency Control**: A partial unique index (`ux_one_active_run_per_project`) enforces at most one `QUEUED` or `RUNNING` run per project. Conflicts raise `ActiveRunError`, returning HTTP 409 Conflict.
- **Heartbeats & Leases**: Long-running steps periodically update `lease_expires_at`. Crashed workers are reaped by `reap_expired_runs` up to `run_max_attempts`.

## 4. Microservice Communication

- **Internal APIs**: The `/internal/v1` routes provide headless RPC endpoints for downstream microservices (e.g., Project Service and Provisioning Service). Do not require browser sessions or CSRF cookies.
- **Event Bus**: Asynchronous communication uses RabbitMQ: `project.generation_requested.v1`/`generation_cancelled.v1` are consumed from the Project Service's `project.events` exchange, and `ai_codegen.generation_completed.v1` is published to `ai_codegen.events`, with snake_case bodies in the platform envelope (README section 4).
- **Downstream HTTP Clients**: Inter-service HTTP calls use `httpx.AsyncClient` defined in `app/services/project_client.py` and provisioning clients.
