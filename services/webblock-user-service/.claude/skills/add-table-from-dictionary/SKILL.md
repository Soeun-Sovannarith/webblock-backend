---
name: add-table-from-dictionary
description: Create a Flyway migration, JPA entity, enum and repository for a WebBlock table exactly as defined in docs/specs/data-dictionary.md. Use when implementing a table or column from the data dictionary/ERD.
argument-hint: "[table_name]"
---
# Add table from the data dictionary: $ARGUMENTS

1. Find `$ARGUMENTS` in `docs/specs/data-dictionary.md` (search the physical name, e.g. `payment_bills`, or the dictionary name, e.g. `bills`). Not found → stop and ask.
2. The section heading tells you the owning service. Work only in that service folder (map spec names with the table in CLAUDE.md, e.g. account-service → `user-service`, ai-service → `ai-generator-service`). For Python services (ai-generator, provisioning) use an Alembic revision + SQLAlchemy model instead of steps 3–5 Flyway/JPA, same column rules.
3. Write `V<yyyyMMddHHmm>__create_$ARGUMENTS.sql`:
   - types: uuid, varchar(len), text, bigint, integer, smallint, boolean, numeric(p,s), jsonb, char(64), timestamptz, date, time; `enum` → `text` + `CHECK (col IN (...))` with values from `status-flows.md`; `enum[]` → `text[]` + check.
   - `NOT NULL` for Req = Y; defaults as listed; PK; FKs **only** to tables of the same service; cross-service refs = plain uuid + index.
   - unique constraints the description implies ("unique", "one lifetime", "unique per thread").
4. Entity in `model/entity`: `@Table(name="$ARGUMENTS")`, `UUID` id, `@Enumerated(STRING)` enums (enum types in `constraint`), `@Version` for `edit_revision`/`row_version`, jsonb via `@JdbcTypeCode(SqlTypes.JSON)`.
5. Repository in `repository`.
6. Testcontainers test: migration runs, entity saves and loads, one constraint is enforced.
7. Ask the `db-schema-reviewer` agent to check the result.
