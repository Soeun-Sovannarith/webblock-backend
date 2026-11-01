---
name: add-endpoint
description: Add or change a FastAPI endpoint in this service (public /v1 or read-only /internal/v1) end to end - request model, thin route, service logic, store SQL, README contract and route test. Use when asked for a new API, route, or response field.
argument-hint: "<METHOD /path and what it does>"
---

# Add an endpoint

Conventions are in `.claude/rules/api-routes.md`.

1. **Choose the router** by prefix (`app/main.py`):
   - `projects.py` → `/v1/projects`
   - `generation.py`/`updates.py`/`runs.py` → `/v1/harness`
   - `assistant.py` → `/v1`
   - `internal.py` → `/internal/v1` (read-only, for other services)

   Add a new router file only for a new resource area, and mount it in `main.py`.
2. **Request model** goes in `app/schemas/harness.py`: Pydantic v2 with `Field` limits, and `Literal[...]` for fixed choices. Responses are plain dicts from the service/store layer, as elsewhere.
3. **Service logic** goes in `app/services/<area>.py`. It raises `HTTPException` for 404/409 like `harness.py`, and calls `store.*` for SQL. Generation-triggering endpoints call `harness.enqueue_generation(...)` and return its result with `status_code=202`.
4. **SQL** goes in `app/services/store.py` only: `text()` with bound `:params`, schema-qualified tables, rows via `.mappings()`. Interpolate only code-owned identifiers (column lists, `FOR UPDATE`), never request values.
5. **Route** (keep the handler short):
   ```python
   @router.get("/{project_id}/thing")
   def get_thing(project_id: UUID):
       with SessionLocal() as db:
           return thing_service.get(db, project_id)
   ```
6. **Test**: `tests/test_<area>_routes.py` with a module-level `client = TestClient(app)`. Patch the service or `store` functions with `monkeypatch`, with no DB. Cover the happy path, 404 and validation (422).
7. **README**: section 6 for `/v1`, section 3 for `/internal/v1`. Give method, path, headers (`Idempotency-Key`, `X-Codegen-Mode` if relevant), request body, response example and error codes.
8. Run `pytest && ruff check .`. If the editor frontend will call the endpoint, tell the user what to add there.
