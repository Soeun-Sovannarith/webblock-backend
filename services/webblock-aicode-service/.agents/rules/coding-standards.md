# Python & Project Coding Standards

These rules specify formatting, linting, type validation, and testing standards for Python code in this repository.

## 1. Python Environment & Dependencies

- **Runtime**: Python 3.11 or newer.
- **Dependencies**: Managed in `pyproject.toml`.
  - Core: FastAPI, Pydantic v2, SQLAlchemy 2.0 (Core only, no ORM), Psycopg 3, HTTPX, PyJWT, Pika.
  - Dev: Pytest, Ruff.
- **Virtual Environment**: Located at `.venv`.

## 2. Formatting & Linting (Ruff)

- **Line Length**: 140 characters (`line-length = 140` in `pyproject.toml`).
- **Rule Set**: Pinned to `["E4", "E9", "F", "B"]` (Syntax errors, standard flake8 errors, Pyflakes, bugbear).
- **Enforcement**:
  - Run `ruff check .` before committing or finalizing code.
  - Automatically fix format issues when possible with `ruff check --fix .`.
  - Keep docstrings and meaningful comments intact.

## 3. Schema & Validation Standards

- Use **Pydantic v2** (`pydantic.BaseModel`, `Field`) for request/response payloads in `app/schemas/`.
- Use `pydantic_settings.BaseSettings` with `.env` reading for configuration (`app/core/config.py`).
- Type annotations: Use modern Python type hints (e.g. `list[str]`, `dict[str, Any]`, `X | None`).

## 4. Testing Standards

- Tests are located in `tests/` and run with `pytest`.
- **Unit Isolation**: Unit tests in `tests/` must NOT depend on an active database or network calls. Mock external HTTP clients (`httpx`), RabbitMQ (`pika`), and GLM LLM calls.
- **Naming**: Test files follow `test_<feature>.py`, functions follow `test_<behavior>()`.
- Always verify all unit tests pass before finishing any code change.
