---
description: Apply new migrations to the compose database with Alembic, re-run the newest one (idempotency test) and report the result
allowed-tools: Bash(docker compose up *), Bash(docker compose logs *), Bash(docker compose ps *), Bash(docker compose exec db psql *), Bash(DATABASE_URL=* alembic *), Bash(DATABASE_URL=* .venv/bin/alembic *)
---

Only the compose database (`localhost:5433`), never the configured one, which may be production. Prefix every alembic command with `DATABASE_URL=postgresql+psycopg://webblock:webblock@localhost:5433/webblock USER_NAME= DB_PASSWORD= DB_MIGRATION_ROLE=`.

1. `docker compose up -d db` if Postgres isn't running (`docker compose ps`).
2. `docker compose up migrate`, then read `docker compose logs migrate`: each `Running upgrade` line is one `sql/` file applied.
3. Idempotency test of the newest file: `alembic stamp <the revision before head>`, then `alembic upgrade head`, which runs that file a second time. If it errors, name the statement that isn't idempotent and point to the idempotent form in `.claude/rules/sql-migrations.md`.
4. `alembic current` must print the head revision. List the schemas and table counts: `docker compose exec db psql -U webblock webblock -c '\dt project.*' -c '\dt ai_codegen.*' -c '\dt ai_assistant.*' -c '\dt provisioning.*'`.
