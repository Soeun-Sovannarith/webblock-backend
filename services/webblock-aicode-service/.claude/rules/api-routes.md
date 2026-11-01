---
paths:
  - "app/api/**/*.py"
  - "app/main.py"
  - "app/schemas/harness.py"
---

# HTTP routes

Routers are mounted in `app/main.py`:

| Router | Prefix |
|---|---|
| `projects` | `/v1/projects` (and `projects.onboarding_router` at `/v1` for `POST /v1/onboardings`) |
| `runs` | `/v1/generation-runs` |
| `assistant` | `/v1` |
| `internal` | `/internal/v1` |

The editor (`localhost:3000`) calls `/v1`. Other services call `/internal/v1`, which is read-only except the product portal owner endpoints (`/internal/v1/projects/{id}/portal-owners`, see `.claude/rules/portal-sso.md`).

- **REST paths.** Name resources with plural nouns, nest them under their project (`/v1/projects/{id}/pages`), and let the method be the action: `GET` reads, `POST` creates, `PATCH` changes part of a resource, `DELETE` removes it. Never put a verb in the path (`/generate`, `/apply`). A state change is a `PATCH` of the resource's field, as `PATCH /v1/change-sets/{id}` with `{"status": "APPLIED"}` does.
- **`Location` header.** A `201` sets it to the new resource's URL, and a `202` to `/v1/generation-runs/{run_id}` when a run was queued (`projects._locate_run`). Take `response: Response` in the handler to set it.
- **No deprecated aliases.** When a path is renamed or replaced, delete the old route in the same change (route, request model, tests, README) instead of keeping it with `deprecated=True`: the team does not want struck-through operations in `/docs`. Tell the user which callers (the editor frontend, `scripts/`) must move. `tests/test_public_routes.py::test_openapi_has_no_deprecated_operations_and_unique_labels` enforces it, and `test_removed_verb_paths_are_gone` lists the removed paths.
- **Swagger labels.** `/docs` labels an operation from its function name. When the function name reads badly as a label, pass `summary=` in Title Case; the test above fails on duplicate labels.
- **Thin handlers.** Parse and validate input, open `with SessionLocal() as db:` only for simple reads, and delegate the rest to `app/services/*`. Business rules, locking and queuing belong in `harness.py`/`assistant.py`/`store.py`, not in the route.
- **Status codes in use.**
  - `200`: reads, `PATCH`, `DELETE`, and a generation request with nothing left to do (no change, or an `Idempotency-Key` replay of a finished run). `projects._locate_run` picks `200` or `202`; use it for any route that queues a generation.
  - `201`: something was created (project, page, conversation, message), with `Location`.
  - `202`: a generation run is queued or running, with `Location: /v1/generation-runs/{run_id}`.
  - `404`: `HTTPException(status_code=404, detail="Run not found")`, matching the existing wording.
  - `409`: another run is active (`ActiveRunError`), a stale `base_edit_revision`/`STALE_EDIT_REVISION`, a duplicate page path, or a change set already decided.
  - `422`: validation (Pydantic, `validate_tree`), or an `Idempotency-Key` reused with a different body.
  - `502`: a dependency failed (GLM, Keycloak). `503`: a dependency is not configured (check `glm.is_configured()` before doing any work), or the database is unreachable.
  - `500`/`503` for errors nobody caught come from `app/api/errors.py` (JSON `{"detail"}` on `/v1`, Problem Details on `/internal/v1`, never the exception text). Don't catch broad exceptions in a route just to return 500; let them reach it. `errors.register` must run before `CORSMiddleware` is added, or a 500 loses its CORS headers.
- **Codegen mode** is resolved the same way in every generation route: `payload.codegen_mode or (x_codegen_mode if x_codegen_mode in ("hybrid", "llm") else None)`. Reuse that expression. Don't add a third way to set it.
- **`Idempotency-Key`** is honoured by `POST /v1/projects/{id}/generation-requests` (`Header(default=None, max_length=255)`). Any new endpoint that creates generation requests should accept it too.
- **Request models** go in `app/schemas/harness.py` (onboarding: `schemas/business.py`). Use Pydantic v2 `Field` limits on every free-text field (`max_length=4000` for AI instructions), and type mode fields as `Literal["hybrid", "llm"] | None`.
- **`/internal/v1` stays read-only** (the portal-owner endpoints are the one agreed exception) and needs no browser session. Other services depend on its response shapes, so treat them as contracts.
- **The endpoints the Project Service reads** (`/generation-runs/{id}`, `/generation-requests/{id}/runs`, `/artifacts/{id}`) follow its contract (its `service-communication.md` 2.4): camelCase fields in the `{success, data, meta}` envelope (`envelope.ok`), and Problem Details errors (`envelope.problem`, `code` UPPER_SNAKE). Artifact download URLs are HMAC-signed and expire (`artifacts.download_url`/`verify_download`).
- **Every route change updates `README.md`**: section 6 (public `/v1`) or section 3 (`/internal/v1`), with method, path, body and response example.
- **Tests** use a module-level `client = TestClient(app)` and patch `store`/service functions. See `tests/test_onboarding_routes.py` and `tests/test_internal_routes.py`.
