---
name: flyway-migration
description: Use when the database schema must change in a Spring Boot service. Creates a new, backward-compatible Flyway migration and never edits an applied one.
---

# Add a Flyway migration

## Purpose
Change the PostgreSQL schema safely: a new versioned file, compatible with the app version that is still running.

## When to use / NOT to use
- Use: new table, column, index, constraint, backfill.
- Not for: editing an existing migration (never), changing another service's database (never), local seed data.

## Inputs to confirm with the user
- The exact change and the entities/queries that need it.
- Is the table large (index and backfill strategy)?
- Is it breaking for the deployed app? If yes, agree an expand/contract split over releases.

## Procedure
1. List `src/main/resources/db/migration`; take the highest version `n`; the new file is `V<n+1>__<snake_case_description>.sql`.
2. Classify: additive (safe) or breaking. Breaking changes are split: expand now, contract in a later release.
3. Write the SQL (templates below). One logical change per file.
4. Add the header comment: purpose and compensating migration (Flyway Community has no undo).
5. Only then update the entity/repository (`ddl-auto` stays `validate`); see `jpa-entity`.
6. Test: clean `migrate` and Hibernate `validate` on PostgreSQL via Testcontainers.
7. Run `./gradlew test`; report file name, effect, compatibility, rollback.

## Templates
```sql
-- V7__create_project_table.sql
-- Purpose: store projects. Rollback: compensating migration drops the table (no data yet).
CREATE TABLE IF NOT EXISTS project (
    id          uuid PRIMARY KEY,
    owner_id    uuid         NOT NULL,
    name        varchar(100) NOT NULL,
    status      varchar(20)  NOT NULL DEFAULT 'ACTIVE',
    version     bigint       NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_project_owner_id ON project (owner_id);
```
Expand / contract for a new required column:
```sql
-- V8__add_project_slug_expand.sql   (release N: nullable)
ALTER TABLE project ADD COLUMN IF NOT EXISTS slug varchar(120);
-- V9__backfill_project_slug.sql     (release N+1, after code writes both)
UPDATE project SET slug = lower(replace(name, ' ', '-')) WHERE slug IS NULL;
-- V10__project_slug_not_null.sql    (release N+2)
ALTER TABLE project ALTER COLUMN slug SET NOT NULL;
```
Big-table index in its own file:
```sql
-- flyway:executeInTransaction=false
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_project_created_at ON project (created_at);
```

## Checklist
- [ ] Version is the next integer; no existing file touched.
- [ ] Types: `uuid`, `timestamptz`, `numeric(19,4)`, `varchar(n)`.
- [ ] FKs and filter/sort columns indexed; uniqueness enforced in the DB.
- [ ] New columns nullable or defaulted; no environment-specific ids; no secrets.
- [ ] Matches entity types and nullability.

## Common mistakes
- Editing a committed file to "fix" it (checksum failure everywhere).
- Adding `NOT NULL` without default or backfill.
- Dropping or renaming a column the running version still reads.
- Plain `CREATE INDEX` on a large table inside a transaction.
- Updating the entity before the migration exists.

## Definition of done
New file only, migrates cleanly on an empty and an upgraded PostgreSQL, `validate` passes, `./gradlew test` green, rollback approach written.

## Related
Skills: `jpa-entity`, `release-checklist` (root). Agent: `migration-checker`. Rules: `flyway`, `jpa-postgres`.
