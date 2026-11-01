---
name: new-migration
description: Create a new numbered, idempotent Postgres migration in sql/ with its Alembic revision (table, column, index, enum value, seed or data update) and prove it re-runs cleanly. Use for any schema or seed-data change.
argument-hint: "<what the migration changes>"
---

# New SQL migration

The full conventions are in `.claude/rules/sql-migrations.md`; the `sql-check` hook enforces the mechanical ones on save.

1. **Is it in the ERD?** A new column or table that the WebBlock ERD lacks needs the user's OK first. The only agreed extension is `ai_codegen.harness_runs.lease_expires_at`. A reference to another service's table is a plain `uuid` column with no FK.
2. **Pick the number.** Run `ls sql/`, then use the next free `00N`. Name it `sql/00N_<snake_case>.sql`. Never edit a committed migration to change data, because databases that applied it won't run it again.
3. **Write it**, starting with a header comment, using the idempotent forms:
   ```sql
   -- <What this changes and why>. Idempotent: <IF NOT EXISTS / ON CONFLICT / guarded WHERE>.
   CREATE TABLE IF NOT EXISTS ai_codegen.example (...);
   ALTER TABLE ai_codegen.harness_runs ADD COLUMN IF NOT EXISTS example text;
   CREATE INDEX IF NOT EXISTS ix_example ON ai_codegen.example (...);
   ALTER TYPE ai_codegen.run_status ADD VALUE IF NOT EXISTS 'NEW_VALUE';   -- standalone statement
   INSERT INTO ... ON CONFLICT (...) DO NOTHING;
   UPDATE ... SET ..., updated_at = now() WHERE <only rows not yet updated>;
   ```
4. **Python side.** New enum values must be UPPERCASE and match the strings Python writes. Search for the old values with grep and update `store.py`, the schemas and tests. A new column used by the service is read and written only in `app/services/store.py`, with `text()` and bound parameters.
5. **Add the Alembic revision.** `alembic revision --rev-id 00N -m "<the snake_case name as words>"` creates `migrations/versions/00N_<snake_case>.py`, which runs your file. Check that it names exactly `sql/00N_<snake_case>.sql`; `pytest tests/test_migrations.py` fails otherwise.
6. **Apply it, then run it again** (the idempotency test), on the compose database only:
   ```bash
   docker compose up migrate && docker compose logs migrate | tail -n 30
   export DATABASE_URL=postgresql+psycopg://webblock:webblock@localhost:5433/webblock USER_NAME= DB_PASSWORD= DB_MIGRATION_ROLE=
   alembic stamp <previous revision> && alembic upgrade head   # re-runs only the new file
   docker compose exec db psql -U webblock webblock -c '\d+ <schema>.<table>'
   ```
   Production is migrated separately, with the user's go-ahead (`.claude/rules/sql-migrations.md`).
7. Run `pytest` and `ruff check .`. If the change is visible over the API, update `README.md` section 1.5 (database schema) and the API sections.
