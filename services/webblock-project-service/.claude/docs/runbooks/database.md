# Database runbook

The Project service owns one PostgreSQL 18 database with three schemas:

| Schema | Content | Application access |
| --- | --- | --- |
| `project` | The business tables | `SELECT`/`INSERT` table-wide, `UPDATE` per column (below) |
| `project_messaging` | The inbox (`processed_messages`), the Namastack outbox tables (`outbox_record`, `outbox_partition`, `outbox_instance`) and `pending_catalog_registrations` | Per table, see below |
| `project_ops` | Flyway's history table only | None |

Flyway migrations live in `src/main/resources/db/migration/project` (`V1` to `V7`). The inbox and the outbox share
one schema (`.claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md`).

1. Once per server, as a role with `CREATEROLE`, create the two `NOLOGIN` group roles:

   ```sh
   psql -f scripts/db/bootstrap-roles.sql
   ```

   The script is safe to run again.

2. Per environment, create the login roles and grant them membership:

   ```sql
   CREATE ROLE project_migrator_login LOGIN PASSWORD '...' IN ROLE project_migrator;
   CREATE ROLE project_app_login LOGIN PASSWORD '...' IN ROLE project_app;
   ```

3. Create the database, owned by the migrator, closed to everyone else:

   ```sql
   CREATE DATABASE webblock_project OWNER project_migrator;
   REVOKE ALL ON DATABASE webblock_project FROM PUBLIC;
   GRANT CONNECT ON DATABASE webblock_project TO project_app;
   ```

4. Configure the service (`application.yml` reads these environment variables):

   - `PROJECT_DB_URL`, which must not carry the parameters `logServerErrorDetail`, `socketTimeout` or `options` (any
     case): pgjdbc lets a URL parameter replace the pool's driver property of the same name, so the service refuses to
     start with such a URL and the migration job exits 2 before connecting. The message names the parameter, not the
     URL.
   - `PROJECT_DB_APP_USER`, `PROJECT_DB_APP_PASSWORD`
   - `PROJECT_DB_MIGRATOR_USER`, `PROJECT_DB_MIGRATOR_PASSWORD`

5. Migrations are forward-only and immutable once merged to main. Never edit an applied migration; fix a mistake with a
   new one.

## Where `SET ROLE` and the default schema are set

In `application.yml`:

```yaml
spring.flyway:
  user: ${PROJECT_DB_MIGRATOR_USER}      # the migrator login
  init-sqls: SET ROLE project_migrator   # every object is owned by project_migrator, not by the login
  default-schema: project_ops            # Flyway's history table lives here
  schemas: project_ops,project,project_messaging  # Flyway creates these schemas if missing
```

- `V1` refuses to run as any role other than `project_migrator`. A `beforeEachMigrate` callback applies the same guard
  before every migration.
- The application connects as the app login. It does not use `SET ROLE` and has no `search_path` setting; entities name
  their schema (`@Table(schema = ...)`).
- The Namastack library is told its schema by `namastack.outbox.jdbc.schema-name` (the outbox schema above). Its own
  table creation is switched off (`schema-initialization.enabled=false`), so the tables come only from Flyway.

## What the application role may do

Business tables (`projects`, `pages`, `generation_requests`): `SELECT` and `INSERT` on the table; `UPDATE` only on
named columns. These columns are frozen, so an `UPDATE` of them fails with `permission denied`:

| Table | Frozen columns |
| --- | --- |
| `projects` | `project_id`, `owner_user_id`, `subdomain`, `business_type_id`, `created_at` |
| `pages` | `page_id`, `project_id`, `created_at` |
| `generation_requests` | `generation_request_id`, `project_id`, `idempotency_key`, `request_hash`, `generation_contract_id`, `captured_edit_revision`, `captured_input`, `captured_input_hash`, `created_at` |

The other tables, and the rules for all:

- **Contracts** (`generation_contracts`, `generation_contract_schemas`) and the inbox `processed_messages`: `SELECT` and
  `INSERT` only.
- **Pending catalog registrations** (`project_messaging.pending_catalog_registrations`): `SELECT`, `INSERT`, `DELETE`
  (decision 0013).
- **Outbox**: `SELECT`, `INSERT`, `UPDATE` on `outbox_record` and `outbox_partition`; `SELECT`, `INSERT`, `UPDATE`,
  `DELETE` on `outbox_instance`.
- No `DELETE` and no `TRUNCATE` on any business table. No DDL. No access to `project_ops`.
- Guard triggers are a second guard, for a superuser or a future grant. `projects_guard` refuses a change of
  `business_type_id` or `subdomain` and a second write of `deleted_at`. `generation_requests_guard` refuses a change
  of the captured input, its hash, the captured edit revision and the request hash, and a second write of
  `source_commit_sha`.

`subdomain` is set once, at creation, and is unique over all projects, deleted ones included, so a sub-domain is
never used again.

## What PUBLIC may do

Nothing. PUBLIC has no `CONNECT` on the database (step 3), no `USAGE` on the three schemas (`V1`, `V2`), no
`EXECUTE` on the guard functions and no `USAGE` on the two enum types (`V6`), and no `USAGE` on the row type of any
table (`V7`).

## Verify after a migration

As the app login (not a superuser):

```sql
-- Schemas and owners: every object is owned by project_migrator
SELECT schemaname, tablename, tableowner FROM pg_tables
 WHERE schemaname IN ('project', 'project_messaging', 'project_ops') ORDER BY 1, 2;

-- Table privileges of the app role
SELECT table_schema, table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type)
  FROM information_schema.table_privileges
 WHERE grantee = 'project_app' GROUP BY 1, 2 ORDER BY 1, 2;

-- A frozen column must not be updatable (expect false)
SELECT has_column_privilege('project_app', 'project.projects', 'owner_user_id', 'UPDATE');

-- The app role must have no access to Flyway's history (expect false)
SELECT has_table_privilege('project_app', 'project_ops.flyway_schema_history', 'SELECT');

-- The sub-domain is frozen too (expect false)
SELECT has_column_privilege('project_app', 'project.projects', 'subdomain', 'UPDATE');
```

The integration tests `MigrationTest` and `GrantsTest` assert the same facts against a throwaway PostgreSQL 18 database.
Run them with `./gradlew integrationTest`.
