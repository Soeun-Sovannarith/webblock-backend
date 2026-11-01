---
name: fastapi-endpoint
description: Use when creating or changing a FastAPI endpoint in a Python service. Contract first, Pydantic schemas, service layer, auth, error format and tests.
---

# Create or change a FastAPI endpoint

Paths assume a session started in `services/<python-service>`; `../../contracts/` is the repo-root `contracts/`.

## Purpose
Deliver one endpoint that matches its OpenAPI contract, keeps the router thin, authorizes by role and ownership, returns RFC 7807 errors, and is covered by tests.

## When to use / when NOT to use
- Use for a new endpoint, or a change to the request, response, status codes or auth of an existing one.
- Do NOT use for pure schema work (skill `pydantic-schema`), a call to another service (skill `microservice-integration`), or LLM client code (skill `llm-client` in aicode-service).

## Inputs to confirm with the user
Operation (method, path, version), who may call it (roles) and the ownership rule, request and response fields, error cases, sync or long-running (`202` + job id), whether the contract operation already exists.

## Procedure
1. **Contract first.** Find or add the operation in `../../contracts/openapi/<service>.vN.yaml` with the skill `contract-change`. Do not implement what is not in the contract.
2. **Schemas.** Add models in `app/models/` (skill `pydantic-schema`) with constraints matching the contract.
3. **Service.** Logic in `app/services/` with injected dependencies (clients, settings). It raises typed domain exceptions and does the ownership check.
4. **Router.** In `app/api/`: a thin `async def`, `Depends` for auth and services, call the service, return `response_model` with the correct status (201, 202, 204).
5. **Auth.** Reuse the JWT dependency (issuer, audience, signature, expiry). Take the user id from the token, never from the body.
6. **Errors.** Map domain exceptions to `application/problem+json` in the registered exception handlers.
7. **Tests.** Dependency overrides; mock outbound HTTP with `respx`. Cover success, 422, 401, 403 (role and owner), 404, downstream timeout.
8. **Verify.** Run `ruff check .`, `ruff format --check .` and `pytest -q` (see `CLAUDE.md`) and report the real output.

## Templates
```python
@router.post("/api/v1/jobs", status_code=202, response_model=JobResponse)
async def create_job(
    body: JobCreate,
    user: CurrentUser = Depends(require_role("USER")),
    service: JobService = Depends(get_job_service),
) -> JobResponse:
    return await service.start(owner_id=user.id, request=body)
```
```python
async def test_create_job_forbidden_for_other_role(client, token_for):
    r = await client.post("/api/v1/jobs", json={"prompt": "hi"}, headers=token_for("GUEST"))
    assert r.status_code == 403
    assert r.headers["content-type"].startswith("application/problem+json")
```

## Checklist
- [ ] Operation in the contract; code matches paths, fields, codes
- [ ] Router has no business logic; response_model set
- [ ] Role AND ownership enforced; no user id from body
- [ ] Problem+json for every error; no stack traces
- [ ] No blocking calls; outbound calls have timeouts
- [ ] Tests for success, 422, 401, 403, failure; ruff and pytest green

## Common mistakes
Returning dicts or ORM objects; checking role but not ownership; `requests` or `time.sleep` in a handler; holding a long job open instead of `202`; changing a released endpoint in place; real network in tests; logging tokens or user content.

## Definition of done
Contract merged, endpoint and tests added, lint and tests green with real output reported, consumers of the contract listed, nothing committed unless asked.

## Related
Skills `contract-change`, `pydantic-schema`, `microservice-integration`; rules `fastapi-api.md`, `python-style.md`, `testing.md`; agent `test-writer-py`.
