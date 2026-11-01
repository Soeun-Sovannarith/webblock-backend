---
description: Run ruff and the full pytest suite and summarise failures with their likely cause
allowed-tools: Bash(.venv/bin/ruff check *), Bash(.venv/bin/pytest *), Bash(ruff check *), Bash(pytest *)
---

Run the project's quality checks:

1. `.venv/bin/ruff check .`
2. `.venv/bin/pytest -q $ARGUMENTS`

For each failure, give the test or rule, `file:line`, and a one-line likely cause from reading the code. If only the `*_not_found` tests in `tests/test_internal_routes.py` fail, say that Postgres is probably down (`docker compose up -d`) rather than calling it a code failure. Don't fix anything unless asked. End with a one-line verdict.
