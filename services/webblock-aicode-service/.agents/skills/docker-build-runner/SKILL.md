---
name: docker-build-runner
description: Use this skill when testing or troubleshooting the Docker-based site builder, validating Next.js 16 / React 19 generation, or configuring BUILD_RUNNER.
---

# Docker Build Runner Management

This skill covers the configuration, validation, and troubleshooting of the site build runner inside the WebBlock harness.

## Context

The generation pipeline includes a `BUILD` step that tests whether generated Next.js + React 19 + Drizzle code successfully compiles.

## 1. Build Runner Modes (`BUILD_RUNNER`)

Configured in `.env`:
- `BUILD_RUNNER=docker` (Default / Production):
  - Builds inside an isolated `node:22-alpine` container.
  - Network access is disabled during the build stage for security.
  - Requires the local Docker daemon to be running.
- `BUILD_RUNNER=skip`:
  - Skips `npm install` and `next build` validation checks.
  - Promotes the version without running Docker.
  - Recommended for fast offline testing or development when Docker is unavailable.
- `BUILD_RUNNER=local`:
  - Executes `npm install` and `npm run build` directly on the host machine.
  - Requires Node.js 22+ installed on the host.

## 2. Docker Health & Image Preparation

When running in `docker` mode, ensure the build image is available:

```bash
# Verify Docker is running
docker info

# Pull build base image if not present
docker pull node:22-alpine
```

## 3. Debugging Build Failures

When a build fails during pipeline execution:
1. Inspect the `validation_runs` rows in PostgreSQL:
   ```sql
   SELECT phase, status, stdout, stderr, exit_code
   FROM ai_codegen.validation_runs
   WHERE harness_run_id = '<run_id>'
   ORDER BY started_at;
   ```
2. Inspect the `RUN_LOG` artifact file located under:
   `var/artifacts/<project_id>/<run_id>/run.log`
3. Check for typical Next.js 16 / React 19 issues:
   - Server Component vs Client Component directive (`"use client"`)
   - Unresolved template block imports in `components/registry.tsx`
