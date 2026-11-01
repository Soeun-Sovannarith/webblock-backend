---
paths:
  - "services/*-service/**/api/**"
  - "**/*Controller*.java"
  - "**/openapi*.yaml"
  - "apps/web/**/lib/api/**"
  - "services/api-gateway/**"
---
# API contracts

> The Notion page (27 Oct 2026) defines a newer response shape (envelope, cursor paging, RFC 9457). New endpoints follow
> `rest-response-format.md`; existing ones keep the shape below until the team decides (`docs/specs/open-questions.md` #11).

- Base path per service: `/api/<service>/v1/...` where `<service>` = folder name without `-service`
  (e.g. `/api/project/v1/projects/{projectId}`, `/api/user/v1/...`). Same path locally (Spring Cloud
  Gateway) and on Kubernetes (Istio).
- Status codes: 200/201 (+ `Location`)/202/204; 400 validation, 401 unauthenticated, 404 not found
  (also for other owners' resources), 403 wrong role, 409 conflict, 422 business rule. 500 only for bugs.
- Owner routes and admin routes are separate: `/api/<service>/v1/admin/**` needs the `admin` role.
- JSON is camelCase. Dates ISO-8601 UTC. Money as string decimal + `currencyCode`.
- List endpoints: `?page=&size=&sort=` and return `{ items, page, size, totalItems }` (UI tables always show "Showing X of Y").
- Errors: `ProblemDetail` with `code` (stable, e.g. `SUBDOMAIN_TAKEN`, `PLAN_REQUIRED`, `RETRY_LIMIT_REACHED`).
- Long jobs (generate, deploy) return `202 Accepted` + the request/command ID; the client polls or gets a notification.
- Keep an OpenAPI file per service up to date; the web app's API client is generated/typed from it.
