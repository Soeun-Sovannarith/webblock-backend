---
paths:
  - "**/*.py"
---

# Python service rules

## Language and tooling
- Python 3.12+. Type hints on every function signature and return; keep `mypy` clean on new code.
- `ruff format` and `ruff check` decide style. Do not change tool config (`pyproject.toml`) as part of another task.
- Dependencies are pinned in the lock file; add or upgrade one only when the task needs it, and say why and which version.

## Layout
- `app/api` (routers, dependencies) -> `app/services` (logic) -> `app/clients` (HTTP clients); `app/models` (Pydantic and persistence models); `app/core` (settings, security, logging). Routers stay thin.
- No mutable default arguments or module-level global state; inject with `Depends`.

## Configuration
- `pydantic-settings` reading environment variables or config-server values. No literals or credentials in code. Claude never reads or edits env files.

## Async and outbound calls
- Handlers are `async def` with async clients (`httpx.AsyncClient`). Never block the loop with `requests`, `time.sleep` or heavy CPU work.
- Every outbound call has explicit timeouts, bounded retries with backoff (idempotent calls only) and a clear failure result.
- No bare `except:`; catch specific exceptions and log with the correlation id.

## Safety
- Logging via `logging` with structured fields; never log tokens, API keys, prompts holding user secrets or personal data.
- No `eval`, `exec`, `pickle.loads` on untrusted data, `subprocess(..., shell=True)` or `yaml.load` (use `safe_load`).

Example:
```python
async def get_project(client: httpx.AsyncClient, project_id: UUID) -> ProjectDto:
    resp = await client.get(f"/projects/{project_id}", timeout=httpx.Timeout(2.0, connect=1.0))
    resp.raise_for_status()
    return ProjectDto.model_validate(resp.json())
```
