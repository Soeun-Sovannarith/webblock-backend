---
paths:
  - "**/*.py"
---

# FastAPI API rules

## Contract
- The endpoint exists in `contracts/openapi/<service>.vN.yaml` before it is implemented, and the code matches it (paths, status codes, fields, error format).
- Version in the path (`/api/v1`). A breaking change gets a new version; never change a released endpoint in place.

## Models
- Request and response bodies are Pydantic models with explicit types and constraints (`Field(min_length=..., max_length=...)`, `EmailStr`, enums). Use `response_model`; never return ORM objects or raw dicts.

## Errors
- RFC 7807 `application/problem+json` like the Spring services: `type`, `title`, `status`, `detail`, `instance`, plus `errors[]` for field errors. Register exception handlers; never leak stack traces.

## Auth
- A reusable dependency validates the Keycloak JWT (issuer, audience, signature via JWKS, expiry). Roles and resource ownership are checked in the service layer. Never trust a user id from the body.
- Only documented public endpoints skip auth; `/health` returns nothing sensitive.

## Long-running work
- Code generation and provisioning return `202 Accepted` with a job id and a status endpoint; jobs are idempotent and cancellable.

## Discovery
- Register with Eureka and read config from config-server as described in the service `CLAUDE.md`; never hard-code other service hosts or ports.

Example: `POST /api/v1/jobs` -> `202` `{"jobId": "<uuid>", "status": "PENDING"}`; `GET /api/v1/jobs/{jobId}` -> `200` or `404` problem+json.
