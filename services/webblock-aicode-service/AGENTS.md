# WebBlock AI Code Service (Harness) - Agent Guidelines

This file provides workspace instructions and operational guidelines for AI coding agents operating within this repository.

## Overview
The WebBlock AI Code Service is a FastAPI microservice responsible for:
- Converting business onboarding profiles into tailored Drizzle ORM schemas and Puck visual page builder structures.
- Orchestrating autonomous code generation pipelines using **LangGraph** with self-healing compiler feedback loops.
- Executing AI code updates with GLM (streamed replies; Instructor + LiteLLM when `GLM_STREAM=false`) and strict Pydantic model validation.
- Validating generated code in isolated environments using the official **Docker Python SDK** (`node:22-alpine`).
- Publishing generated source snapshots to GitHub and triggering deployment pipelines.
- Managing multi-tenant state across four PostgreSQL schemas (`project`, `ai_codegen`, `ai_assistant`, `provisioning`).

## Essential Rules & Guidelines

1. **Database Persistence**:
   - Strictly raw SQL using `sqlalchemy.text()` in `app/services/store.py`.
   - Never use SQLAlchemy ORM.
   - Always qualify tables with their schema name (e.g., `SELECT * FROM ai_codegen.harness_runs`).
   - Foreign keys exist strictly within the same schema; cross-schema references are plain UUIDs.

2. **LangGraph Pipeline State Machine**:
   - StateGraph progression: `START` ➔ `prepare` ➔ `ai_code_update` ➔ `build` ➔ (conditional edge) ➔ `github_push` ➔ `deploy` ➔ `END`.
   - Self-healing loop: When `build` fails, it conditionally loops to `compiler_repair` to fix TypeScript compiler errors before re-testing.
   - Concurrency: At most one active run per project via unique index `ux_one_active_run_per_project` (`ActiveRunError` ➔ HTTP 409).
   - Real-time logging: Frontend parses `[STEP]` markers and `SKIPPED:` lines from `RUN_LOG`.

3. **Security & LLM Code Edits**:
   - Allowed file paths: `(app|components|lib|db)/**/*.(ts|tsx|css)`.
   - Harness-owned files are strictly forbidden for LLM edits (`app/[[...slug]]/page.tsx`, `components/registry.tsx`, `theme.ts`, `theme.css`, `app/page.tsx`, `package.json`, `db/schema.ts`, `db/seed.sql`; full list in `scaffold.HARNESS_OWNED`).
   - Strict POSIX path normalization; reject null bytes (`\0`), Windows drive letters (`C:\`), and directory escapes (`..`).

4. **Code Quality & Checks**:
   - Python 3.11+. Line length 140.
   - Lint check: `ruff check .` (auto-fix: `ruff check --fix .`).
   - Tests: `pytest` (unit tests in `tests/` must run without an active DB or network).

## Workspace Customizations

Detailed rules and skills are located in `.agents/`:
- **Rules**:
  - [.agents/rules/architecture.md](.agents/rules/architecture.md): Service mesh, database isolation, raw SQL standards, modern AI stack.
  - [.agents/rules/codegen-pipeline.md](.agents/rules/codegen-pipeline.md): LangGraph state machine, self-healing loop, LLM guardrails, artifact storage.
  - [.agents/rules/security-guardrails.md](.agents/rules/security-guardrails.md): Path traversal prevention, Docker sandboxing, input sanitization.
  - [.agents/rules/business-onboarding.md](.agents/rules/business-onboarding.md): Onboarding wizard schema, dynamic Drizzle generator, Puck synthesis.
  - [.agents/rules/coding-standards.md](.agents/rules/coding-standards.md): Python conventions, Ruff configuration, Pydantic schemas.
  - [.agents/rules/puck-components.md](.agents/rules/puck-components.md): The 4-file synchronization rule for Puck blocks.
- **Skills**:
  - `langgraph-pipeline`: Inspecting, extending, and debugging the LangGraph state machine and repair loops.
  - `business-generator`: Authoring new business categories, Drizzle schema templates, and Puck component mappings.
  - `run-tests`: Running pytest, specific test targets, and Ruff linting.
  - `database-migrations`: Docker Postgres commands, schema creation, idempotent SQL scripts.
  - `docker-build-runner`: Docker containerized builder, Docker Python SDK, BUILD_RUNNER modes, build logs.
  - `puck-component`: Step-by-step authoring workflow for Puck UI components.
