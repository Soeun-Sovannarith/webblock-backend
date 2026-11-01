---
name: new-migration
description: Use when the Project or catalog database needs a schema change, a grant, a guard or a data fix,
  before writing any file under src/main/resources/db/migration.
---

# Write a new Flyway migration (Project service)

Rules first (`.claude/CLAUDE.md` section 6, `.claude/docs/runbooks/database.md`,
`.claude/docs/runbooks/migrations.md`, `.claude/docs/runbooks/catalog-database.md`):

- The design is Notion, read-only: "2 · Project" https://app.notion.com/p/40058175966e81d4a77bfb48e736757a for the
  project database, "6 · Shared catalog" https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a for the catalog
  database, conventions in the hub "platform-db-v6" https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad.
- The database matches Notion 100%: no extra table, column, index, grant or constraint.
- Migrations are forward-only and immutable. Never edit, rename or delete an existing file; fix a mistake with a
  new one. No `DEFAULT` or `CASCADE` unless Notion asks for it.
- Production never migrates in the service: a separate job runs
  `infrastructure.persistence.migration.Migrations` (decision `0005`). Your file must work in both.

## 1. Confirm the change is designed

Fetch the Notion page and find the exact table, column, type, NULL rule, physical order, enum value, rule or role
grant. Items under "Planned, not yet applied" are designed and may be built. If Notion does not show it, stop and
ask the owner. Do not design tables yourself, and never edit the Notion page.

The change needs an FR or TR ID (`.claude/rules/spec-driven.md`).

## 2. Name and number

| Database | Folder | Run as | Last file |
| --- | --- | --- | --- |
| project | `src/main/resources/db/migration/project/` | `project_migrator` | `V7__revoke_public_on_table_row_types.sql` |
| catalog | `src/main/resources/db/migration/catalog/` | `catalog_owner` | `V7__revoke_public_on_row_types_and_site_profile_delete.sql` |

- `ls` the folder and take the next number (today `V8` in both). Check other branches too:
  `git log --all --oneline -- src/main/resources/db/migration` must show no other file with that number.
- File name: `V<n>__<what_it_does>.sql`, two underscores, lower case words with underscores.
- `R__catalog_reference_data.sql` is the only repeatable migration (catalog reference rows). Do not add another.
- `project/beforeEachMigrate.sql` is a Flyway callback, not a migration: it refuses any role but
  `project_migrator` before every project migration. Do not change it.

## 3. Write the file

1. A header comment: what changes and which Notion section asks for it, what does not change, and "Forward-only
   and immutable: fix a mistake with a new file." No dates.
2. Catalog files only: the `catalog_owner` guard `DO` block (SQLSTATE `42501`), copied from
   `catalog/V7__revoke_public_on_row_types_and_site_profile_delete.sql`. Project files get the guard from the callback.
3. Guards for existing rows that would break the change: stop with a clear message and change nothing (for
   example before `SET NOT NULL` or a unique index on old data). Name the fix in the message.
4. The change itself, in Notion's physical column order. Schema-qualify every name (`project.`,
   `project_messaging.`, `catalog.`); the app never sets `search_path`.
5. Grants per role, exactly what Notion gives each role, nothing more. `project_app` gets `SELECT`, `INSERT`
   and `UPDATE` only on the columns Notion lists ("Who may write"); never `DELETE` or `TRUNCATE` on a business
   table. Roles come from `scripts/db/bootstrap-roles.sql` and `scripts/db/catalog-bootstrap-roles.sql`; a new
   role goes there (idempotent) and into the runbook.
6. A trigger that freezes a column (write-once, never changes) follows the existing revision 19 triggers in
   `project/V1__project_schema.sql`.

Flyway runs each file in one transaction on PostgreSQL. `CREATE INDEX CONCURRENTLY` cannot run in it: ask first.

## 4. Code that must follow

- The JPA entity in `domain/<feature>/model/` and its repository in `infrastructure/persistence/<feature>/`:
  production validates the schema (`ddl-auto=validate`) and refuses to start on a mismatch.
- A new constraint whose violation the user can cause gets an `ErrorCode` mapping in that feature's
  `ConstraintErrorRegistration` bean (`infrastructure/config/<feature>/*Config.java`, for example
  `infrastructure/config/page/PageConfig.java`).

## 5. Tests first (real PostgreSQL 18, `@Tag("integration")`)

Under `src/test/java/org/kshrd/webblock/project/db/`, with `PostgresFixture` (`freshDatabase()`,
`freshCatalogDatabase(true)`, `asApp()`, `asSuperuser()`) and `Rows`:

- the change itself, positive and negative cases (a refused write fails with the right SQLSTATE or constraint);
- old rows kept: build a Flyway like `PostgresFixture.flyway(...)` with `.target("5")`, seed rows, then migrate;
- every role's privileges after the change, exactly (extend `GrantsTest` or `CatalogGrantsTest`);
- the table list in `MigrationTest` if a table is added or dropped;
- the guards: wrong role refused, bad existing rows refused with nothing changed.

Name the FR ID in each new test. Run `./gradlew integrationTest`, then the fast tier of the `project-gates` skill.

## 6. Docs and reports

- `.claude/docs/runbooks/database.md` (or `catalog-database.md`): update the role and column tables when grants
  or frozen columns change; add an operator step only when the upgrade order matters.
- A decision record (`decision-record` skill) only for a choice Notion does not make (for example how a backfill
  treats old rows).
- The separate DB change report (owner rule, every DB revision), written next to the repo, outside it:
  `../platform-db-rev<hub revision>-project-V<n>-report.md`, with three parts: "Changed" (table: where, before,
  after, why), "Not changed", "UI impact". Copy the layout of `../platform-db-rev25-provisioning-00023-report.md`.
  Take the hub revision from the platform-db-v6 page.

## 7. Then

Run the `db-notion-audit` skill. Zero DIFFERENT findings is the bar before a merge.

## Running it on a real database

Never run a migration against a shared or real database yourself. `SPRING_PROFILES_ACTIVE=prototype` migrates
a local database at start; anything else is a command written for the owner (the job in
`.claude/docs/runbooks/migrations.md`).
