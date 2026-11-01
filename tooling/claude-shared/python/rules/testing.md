---
paths:
  - "**/test_*.py"
  - "**/*_test.py"
  - "**/tests/**"
---

# Testing rules (Python)

## Style
- `pytest` with `pytest-asyncio` for async code. Names: `test_<function>_<condition>_<expected>`. One behavior per test; arrange, act, assert.
- Fixtures for setup; no shared mutable state; no sleeps (use fake clocks or `asyncio` events).
- Test data is obviously fake. Never use real keys, tokens or personal data.

## Boundaries
- Mock at the boundary: HTTP (`respx` or `httpx.MockTransport`), LLM clients, broker, cloud SDKs. Never call the real network, a real LLM, Docker, Kubernetes or cloud APIs.

## API tests
- Use `TestClient` or `httpx.AsyncClient` with dependency overrides. Cover success, validation error (422), 401, 403 (wrong role and wrong owner), downstream failure or timeout, and idempotent retries.

## Special areas
- LLM code: test prompt construction and output parsing with canned responses, including malformed and malicious output.
- Provisioning code: destructive operations must NOT run in dry-run mode and need the explicit confirmation flag.

## Integrity
- Never weaken or delete a failing test to get green. A bug fix includes a regression test that fails without the fix.

Example:
```python
async def test_create_job_without_token_returns_401(client):
    resp = await client.post("/api/v1/jobs", json={"prompt": "hello"})
    assert resp.status_code == 401
```
