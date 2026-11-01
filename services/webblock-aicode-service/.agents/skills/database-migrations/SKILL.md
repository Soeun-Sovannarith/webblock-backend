---
name: database-migrations
description: Use this skill when managing PostgreSQL schemas, creating new idempotent migrations, running database containers, or inspecting the database.
---

# Database & Schema Migrations

This skill provides procedures for running PostgreSQL and managing database migrations in the WebBlock harness.

## Architecture Context

PostgreSQL 16 runs via Docker Compose on port **5433** to avoid port collisions with host PostgreSQL services.
The database has five schemas:
- `project`
- `ai_codegen`
- `ai_assistant`
- `provisioning`
- `site_data` (`006`): every generated site's tables, with row-level security per tenant; see `.claude/rules/sql-migrations.md` for what a new table there needs

All table references in queries must be schema-qualified.

## 1. Running the Database

Start the database container and apply all idempotent migration files:

```bash
# Start Postgres and run migrations
docker compose up -d

# Check status of Postgres service
docker compose ps
```

## 2. Applying Migrations

Alembic (`alembic.ini`, `migrations/`) applies the `sql/*.sql` files a database hasn't run yet, in order, and records them in `public.alembic_version`. Each revision in `migrations/versions/` runs one `sql/` file.

```bash
# The compose database: the migrate service runs `alembic upgrade head`
docker compose up migrate

# The database in your settings (production: set DB_MIGRATION_ROLE=ai_codegen, the database owner)
alembic current               # where it is
alembic upgrade head --sql    # the SQL that would run
alembic upgrade head          # apply it
```

## 3. Creating a New Migration

1. Add a new file in `sql/` following the numbered naming convention:
   `sql/00N_<feature_name>.sql`
2. Ensure the SQL is completely idempotent:
   ```sql
   -- Create tables with IF NOT EXISTS
   CREATE TABLE IF NOT EXISTS project.example (
       id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       created_at TIMESTAMPTZ NOT NULL DEFAULT now()
   );

   -- Alter columns with IF NOT EXISTS
   ALTER TABLE ai_codegen.harness_runs ADD COLUMN IF NOT EXISTS custom_field text;

   -- Seed or update rows with ON CONFLICT
   INSERT INTO ai_codegen.puck_component_definitions (name, allowed_operations)
   VALUES ('CustomCard', ARRAY['ADD', 'UPDATE', 'REMOVE', 'MOVE'])
   ON CONFLICT (name) DO UPDATE SET allowed_operations = EXCLUDED.allowed_operations;
   ```
3. Create its Alembic revision: `alembic revision --rev-id 00N -m "<feature name in words>"` (it runs `sql/00N_<feature_name>.sql`; `pytest tests/test_migrations.py` checks the pairing).
4. Run `docker compose up migrate` to apply it.

## 4. Connecting via PSQL

Inspect schemas, run queries, or debug tables directly:

```bash
docker compose exec db psql -U webblock webblock
```

Useful psql commands:
- `\dn` : List all schemas (`project`, `ai_codegen`, `ai_assistant`, `provisioning`)
- `\dt project.*` : List tables in `project` schema
- `\dt ai_codegen.*` : List tables in `ai_codegen` schema
