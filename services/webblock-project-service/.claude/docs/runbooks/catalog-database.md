# Catalog database runbook

The Project service owns the shared catalog database. Its migrations are `src/main/resources/db/migration/catalog`
(V1 to V7, plus the repeatable reference data `R__catalog_reference_data.sql`). Flyway's history is
`catalog_flyway.flyway_schema_history`. Provisioning's Kind stack applies the same migrations with its own Flyway run
(`.claude/docs/contracts/provisioning-contract.md`).

| Role | Kind | Used by |
| --- | --- | --- |
| `catalog_owner` | NOLOGIN; owns the database, schema `catalog`, tables, functions | migrations only |
| `catalog_platform` | NOLOGIN | the Project service at runtime (website rows, first business profile, reference reads, product-type and schema reads for generation contracts and `codegen-input` (`V5`)) |
| `catalog_site` | NOLOGIN group | every `site_<project id as 32 hex>` login, created by Provisioning's bootstrap |
| `catalog_migrator_login` | LOGIN; `GRANT catalog_owner ... WITH INHERIT FALSE, SET TRUE` | Flyway (`SET ROLE catalog_owner`) |
| `catalog_platform_login` | LOGIN; `GRANT catalog_platform ... WITH INHERIT TRUE, SET FALSE` | the service's catalog DataSource |

1. Once per server, as a role with `CREATEROLE`: `psql -f scripts/db/catalog-bootstrap-roles.sql`.
2. Per environment, the logins (passwords from the secret store, never on a command line):
   ```sql
   CREATE ROLE catalog_migrator_login LOGIN PASSWORD '...';
   GRANT catalog_owner TO catalog_migrator_login WITH INHERIT FALSE, SET TRUE;
   CREATE ROLE catalog_platform_login LOGIN PASSWORD '...';
   GRANT catalog_platform TO catalog_platform_login WITH INHERIT TRUE, SET FALSE;
   ```
3. `CREATE DATABASE catalog OWNER catalog_owner;` (sites connect through PUBLIC's CONNECT, decision 0015; TEMP is
   revoked by V4). The default schema `public` stays: PUBLIC can see it (USAGE, no CREATE in PostgreSQL 18), and it
   holds nothing, since no migration creates anything there.
4. Prototype: migrations run at service start-up when `CATALOG_MIGRATIONS_ENABLED=true` (default), as
   `CATALOG_DB_MIGRATOR_USER`.
   Set it to `false` where another process applies them (Provisioning's Kind stack,
   `.claude/docs/contracts/provisioning-contract.md`).
   Production: always the separate migration job (`.claude/docs/runbooks/migrations.md`); the service holds no migrator
   login.
5. Runtime: `CATALOG_DB_URL`, `CATALOG_DB_PLATFORM_USER`, `CATALOG_DB_PLATFORM_PASSWORD`. Readiness includes
   `catalogDb`.
   `CATALOG_DB_URL` must not carry the parameters `logServerErrorDetail`, `socketTimeout` or `options` (any case):
   pgjdbc lets a URL parameter replace the pool's driver property of the same name, so the service refuses to start
   with such a URL in every profile, and the migration job exits 2 before connecting. The message names the parameter,
   not the URL.

Verify: `SELECT version, success FROM catalog_flyway.flyway_schema_history ORDER BY installed_rank;` shows 1 to 7
and the repeatable row, all `t`.

PUBLIC holds nothing inside the catalog: no USAGE on the schema `catalog` (only `catalog_platform` and
`catalog_site` get it, V3), no EXECUTE on its functions (V2), no TEMP (V4), no USAGE on the two enum types (V6) or
on the row type of any catalog table or of the Flyway history (V7).
`catalog_site` may read, insert and update `catalog.business_profiles`, never delete (V7).

## Catalog registration of a new website

A new project's business profile waits in `project_messaging.pending_catalog_registrations` (project database), never in
an outbox row, which is kept after completion. The outbox job `catalog:<project id>`, whose payload is the project ID
only, writes `catalog.websites` and `catalog.business_profiles` as `catalog_platform`, then deletes the pending row. The
website's `created_at` is the pending row's `created_at`, which marks the row as the registration's own: a repeat after
a crash finds the same row and completes. Every catalog statement is bounded (statement timeout 5 s, socket timeout 10
s). A failure retries 16 times, from 1 s doubling to 5 min, about 44 minutes in all. The record then turns `FAILED`
(`webblock_outbox_failed_records`), and the pending row stays for a re-drive (`.claude/docs/runbooks/outbox.md`).

A website conflict turns the record `FAILED` at once, without retries. Its `failure_reason` names the project ID:
- "The catalog already holds website <id>, which this project's registration did not write": a `catalog.websites` row
  with the project's ID exists, but its business type, role name or `created_at` differ, for example a demo shop that
  Provisioning's `catalog_platform register` tool wrote. The job never adopts it.
- "The catalog role of website <id> belongs to another website": another website holds the role name `site_<id hex>`.

Nothing was written. Decide with the owner of the existing website: remove it if it is a leftover, then re-drive the
record; otherwise the project cannot get a catalog website under that ID.

## Catalog archive of a deleted website

Deleting a project (a draft website only) schedules the outbox job `catalog-archive:<project id>`; its payload is the
project ID only. As `catalog_platform` it sets `catalog.websites.status` to `archived` (only `status`: `updated_at` is
outside the column grant). Rows, profile and history stay. A repeat changes nothing.
- No pending registration: the website is the registration's own and is archived. No website at all completes the job
  with a warning.
- A registration is pending: only the row that registration writes (business type, role name, `created_at`) is
  archived. No website yet retries 16 times, from 1 s doubling to 5 min, until the registration has written it. A
  website under the ID that the registration did not write is never touched: the record turns `FAILED` at once with
  "The catalog holds website <id>, which this project's registration did not write", for an operator.
- A registration that can never finish keeps its pending row, so the archive record ends `FAILED` after its retries.

Re-drive order for a deleted project (`.claude/docs/runbooks/outbox.md`): re-drive the registration `catalog:<id>`
first, then the archive `catalog-archive:<id>`. A registration re-driven after the archive record ended `FAILED` writes
an `active` website that nothing archives until the archive record is re-driven too.

Never:
- Never delete a `pending_catalog_registrations` row by hand. The job takes a missing pending row to mean "already
  done" without looking at the catalog (`catalog_platform` may read `catalog.websites`, V4, but the pending row holds
  the
  profile the job would write). It completes and the website is never registered.
- Never rename or remove a field of `BusinessProfile` (`catalog.application.model`) while rows are pending. The job
  reads each stored profile with the strict JSON mapper, so a row written with the old field fails every retry. Wait
  until `SELECT count(*) FROM project_messaging.pending_catalog_registrations` is 0, or ship a migration that rewrites
  the rows.
