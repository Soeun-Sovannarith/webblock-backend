---
name: add-api-endpoint
description: Add a REST endpoint to a WebBlock Spring service with DTO records, validation, ownership/role checks, ProblemDetail errors, OpenAPI and tests. Use when adding or changing an API route.
argument-hint: "[METHOD /api/<service>/v1/path]"
---
# Add endpoint: $ARGUMENTS

1. Find the feature + workflow step it serves (feature-detail.md, workflow.md). Note every decision branch it must answer.
2. Pick owner route (`/api/<svc>/v1/...`) or admin route (`/api/<svc>/v1/admin/...`).
3. Request/response `record` DTOs with Bean Validation. camelCase JSON.
4. Controller → one service method (interface in `service`, logic in `service/impl`). Owner route: load resource and check `ownerUserId == current user` (404 if not). Admin route: `@PreAuthorize("hasRole('admin')")`.
5. Retryable write → require `Idempotency-Key` header; store and return the first result on repeat.
6. Errors: throw exceptions from `exception` mapped to `ProblemDetail` + stable `code` (e.g. `SUBDOMAIN_TAKEN`, `PLAN_REQUIRED`, `RETRY_LIMIT_REACHED`, `OTP_INVALID`).
7. Long job → `202 Accepted` + job id.
8. Update the service OpenAPI file.
9. Tests: `@WebMvcTest` for 200/400/401/404 (other owner) and each decision branch; use-case unit test for the rule.
