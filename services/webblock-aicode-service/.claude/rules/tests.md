---
paths:
  - "tests/**/*.py"
---

# Tests

- Plain `pytest` functions named `test_<behavior>`. There is no `conftest.py` and no custom fixtures: use `monkeypatch`, `tmp_path` and `@pytest.mark.parametrize`. Shared sample data is module-level constants (see `HOME`/`INPUT` in `test_scaffold.py`).
- New tests must not need Postgres, RabbitMQ, GLM or GitHub. Patch at the boundary, as the existing tests do:
  - GLM: `monkeypatch.setattr("app.services.glm._chat", fake)`, returning `(raw_json_text, usage_dict)`
  - RabbitMQ: `monkeypatch.setattr("pika.BlockingConnection", ...)`
  - DB: `monkeypatch.setattr(store, "<helper>", lambda ...: ...)`
  - Settings: `monkeypatch.setattr(settings, "<field>", value)`
- Route tests use a module-level `TestClient(app)`, not `with TestClient(app)`, so the lifespan never starts the worker or the consumer. Keep it that way.
- No test may reach a database: `.env` can point at production (`DATABASE_URL`, `USER_NAME`, `DB_PASSWORD`). Patch `store.get_run` and the other store helpers instead. Prove it with an unreachable database: `DATABASE_URL=postgresql+psycopg://x:y@127.0.0.1:1/none USER_NAME= DB_PASSWORD= pytest`.
- Importing LiteLLM (through `glm.py`) calls `load_dotenv()`, so `.env` values end up in `os.environ`. A test that builds its own `Settings(...)` must pass every value it depends on explicitly.
- Run one test with `pytest tests/test_glm.py::test_apply_changes_upserts_and_deletes`. Run `pytest` and `ruff check .` before calling a change done.
