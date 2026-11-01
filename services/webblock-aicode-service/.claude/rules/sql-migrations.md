---
paths:
  - "sql/**/*.sql"
  - "migrations/**"
  - "alembic.ini"
---

# SQL migrations

The numbered files in `sql/` are the source of truth. **Alembic** (`alembic.ini`, `migrations/`) applies them. Each revision in `migrations/versions/00N_<name>.py` runs exactly one file, `sql/00N_<name>.sql` (`migrations/sql_files.run_sql_file`). `public.alembic_version` records which revisions a database has, so `alembic upgrade head` runs only what is missing, one transaction per revision. The `migrate` compose service does the same for the compose database.

- **A migration is two files**, with the next free number. Write `sql/00N_<snake_case>.sql`, then run `alembic revision --rev-id 00N -m "<snake case words>"`, which creates `migrations/versions/00N_<snake_case>.py` already running that file. `tests/test_migrations.py` checks that there is one revision per file, in order, each running its own file.
- **Still write idempotent SQL.** Alembic runs a file once per database, but production's `001`–`005` were applied by hand before Alembic and then stamped, and the idempotency test re-runs the newest file. Start the file with a `--` comment saying what it changes and why it is idempotent, as `001`–`005` do.
- Don't edit an older file to change existing data: databases that already applied it won't run it again. `005_style_props.sql` shows how to update existing rows from a new file.
- Idioms already in use:
  - Enums: `DO $$ BEGIN CREATE TYPE <schema>.<name> AS ENUM (...); EXCEPTION WHEN duplicate_object THEN NULL; END $$;`
  - `CREATE SCHEMA/TABLE/INDEX IF NOT EXISTS`
  - Seeds: `INSERT ... ON CONFLICT (<unique cols>) DO NOTHING`
  - Data updates: a `WHERE` that matches only rows not yet updated (e.g. `AND NOT editable_props_schema ? 'tone'`), and set `updated_at = now()`
- Not used yet, but the idempotent forms when you need them: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and `ALTER TYPE <schema>.<name> ADD VALUE IF NOT EXISTS 'VALUE';`. Postgres may refuse to use a new enum value in the transaction that added it, so keep that statement in its own file.
- Name every table with its schema (`ai_codegen.harness_runs`). Foreign keys only within one schema; a reference to another service is a plain `uuid` column, noted in the header comment.
- **`catalog` tables** (generated sites' data, `007`, the team's database design "Shared catalog") follow the design: don't add a column or table it lacks without the user's OK, and list every extension (now `business_profiles.maps_url`, `business_profiles.portal_edited_at`, `portal_revoked_sessions`) in the file's header comment and in `tests/test_site_catalog.py` `EXTENSIONS`. A new website-scoped table needs:
  - `website_id uuid NOT NULL DEFAULT catalog.current_website_id() REFERENCES catalog.websites(website_id) ON DELETE CASCADE`, and `UNIQUE (website_id, <id>)` when another table will reference it;
  - references to its parent through a composite foreign key that includes `website_id`, so no row can point into another website;
  - `ENABLE ROW LEVEL SECURITY` with the `website_isolation` policy (`website_id = (SELECT catalog.current_website_id())`, `USING` and `WITH CHECK`) in the `FOREACH t IN ARRAY ARRAY[...]` loop. Without the policy one site reads another's rows. Don't `FORCE` it: the owner `ai_codegen` and its members (the harness) must bypass it to register and seed websites;
  - grants to the group `webblock_site` inside the `IF EXISTS (... pg_roles ...)` block, no wider than the site needs. Never grant it a write on `websites` or `INSERT` on `business_profiles`;
  - a `tr_touch` trigger if it has `updated_at`, and `tr_storage` if it records stored bytes.

  Add the table to the template `db/schema.ts` too. `tests/test_site_catalog.py` reads every `sql/` file from `007` on and checks the tables and columns against the design (`DESIGN`/`EXTENSIONS`), the policies, grants and `db/schema.ts`, so write the new file in the same form.
- Never grant `CREATE` on `public`, and never make the harness resolve names through `public` while site roles exist: a site could plant a function or operator there that RLS-bypassing harness SQL picks up (`008` revokes it, `site_catalog.SESSION_GUARDS` pins `prepare_site`'s `search_path`).
- Enum values are UPPERCASE and must match the strings Python writes (strategies, statuses, validation types, artifact types). The exception is `catalog` (`website_status`, `product_status`), whose values are lowercase as in the team's design; keep that deliberate difference rather than "fixing" either side.
- The ERD is the contract: a column it lacks needs the user's OK. The only agreed extension is `ai_codegen.harness_runs.lease_expires_at`.
- **Migrations only go forward.** `downgrade()` raises; undo a change with a new file.
- **Production** (database `ai_codegen` on `10.109.2.186`): the login `dev_svr` may not create schemas, so set `DB_MIGRATION_ROLE=ai_codegen`, the database owner, which `dev_svr` may switch to through `ai_codegen_migrate`. Alembic then runs `SET ROLE` first, and what it creates is owned by `ai_codegen`. Writing to production needs the user's go-ahead: show `alembic current` and `alembic upgrade head --sql` first. Production is at `006` (applied 30 Oct 2026); `007` and `008` are not applied there yet. Neither `ai_codegen` nor `dev_svr` has `CREATEROLE`, so `007` cannot create `webblock_site` and only raises a `NOTICE`: a DBA must create it `NOLOGIN`, re-run the file's final `GRANT` block, and create the role-admin role for `SITE_ROLE_ADMIN` (README section 9.7 C). `dev_svr` has a connection limit of 10, shared with everyone who logs in as it.
- **Idempotency test** for a new file, on the compose database: `alembic upgrade head`, then `alembic stamp <previous revision>` and `alembic upgrade head` again, which re-runs only the new file.
