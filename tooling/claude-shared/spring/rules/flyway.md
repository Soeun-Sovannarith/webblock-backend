---
paths:
  - "**/db/migration/**"
  - "**/*.sql"
---

# Flyway migration rules

## Files
- Location `src/main/resources/db/migration`; name `V<n>__<snake_case_description>.sql` (two underscores). Versions strictly increase; check the highest existing version first; never reuse a gap.
- **Never edit, rename, reorder or delete a committed migration** (checksum failure in every environment). Fix mistakes with a new migration.
- One logical change per file, idempotent where practical (`IF NOT EXISTS`).
- Header comment: purpose and compensating (rollback) migration; Flyway Community has no undo.

## Expand / contract
Every migration works with the previous app version:
1. add nullable column or new table -> 2. deploy code writing both -> 3. backfill -> 4. add constraint -> 5. drop the old column in a later release.
Never drop or rename something the running version uses in the same release.

## Safety
- `NOT NULL` needs a default or a prior backfill.
- Big tables: `CREATE INDEX CONCURRENTLY` in its own file with `-- flyway:executeInTransaction=false`.
- No `DROP DATABASE/SCHEMA`, no `TRUNCATE` of business tables, no environment-specific ids, no secrets.
- Local seed data lives outside versioned migrations unless it is reference data.

## Verification
- Test a clean `migrate` plus Hibernate `validate` against PostgreSQL (Testcontainers), never H2.

## Example
```sql
-- Purpose: add nullable display_name (expand). Rollback: V8 drops it if unused.
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name varchar(120);
```
