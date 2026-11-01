---
name: run-tests
description: Use this skill when running unit tests, integration tests, or checking code formatting and linting for the WebBlock AI Code Service.
---

# Running Tests & Lint Checks

This skill guides running tests and static checks for the FastAPI backend harness.

## Prerequisites

Ensure the local virtual environment is active or invoke python via `.venv/bin/`:
```bash
source .venv/bin/activate
```

## 1. Running Unit Tests

Unit tests are isolated and do not require Docker or an active PostgreSQL database:

```bash
# Run the entire test suite
pytest

# Run tests with verbose output
pytest -v

# Run a specific test module
pytest tests/test_glm.py
pytest tests/test_scaffold.py
pytest tests/test_internal_routes.py

# Run a specific test function
pytest tests/test_glm.py::test_apply_changes_upserts_and_deletes
```

## 2. Running Lint Checks (Ruff)

Check formatting and common bugs using the pinned Ruff rule set (`E4`, `E9`, `F`, `B` with line length 140):

```bash
# Inspect all files
ruff check .

# Automatically apply safe fixes
ruff check --fix .
```

## 3. Verification Steps

- `pytest` should complete with `0 failed, X passed`.
- `ruff check .` should output `All checks passed!`.
