---
name: new-migration
description: Use when the Provisioning database needs a schema change, a grant, a guard or a data fix, before
  writing any file under src/migrations.
---

# Write a new goose migration (Provisioning)

Rules first (constitution III.3, `.claude/rules/migrations.md`, CLAUDE.md sections 4 and 6,
`.claude/docs/runbooks/phase-1-database.md`):

- The design is Notion "5 · Provisioning" (https://app.notion.com/p/40058175966e81769596d1ebcadb333d) and
  "Database Design" (https://app.notion.com/p/40058175966e81f790decccc6e9abf4a). They are read-only.
- The database matches Notion 100% (decision 0023 G20).
- Migrations are forward-only and immutable. Never edit an existing file. No Down section. No DEFAULT or CASCADE
  unless Notion asks for it.

## 1. Confirm the change is designed

Fetch the Notion page and find the exact table, column, type, NULL rule, order, enum value, rule or role grant.
If Notion does not show it and no owner-approved decision record does, stop and ask the owner. Do not design
tables yourself.

## 2. Name and number

- `ls src/migrations` and take the next number after the last file (the last one is `00025`), so the next
  file is `00026`. `00024` stays unused: it was reverted and may be recorded as applied in a test or dev database.
- File name: `000NN_<what_it_does>.sql`, lower case with underscores.
- Never reuse a number; decision 0020 and its migrations live only on branches `db-rev21` to `db-rev23`.

## 3. Write the file

Copy the shape of `src/migrations/00023_bootstrap_reads_subdomain.sql`:

1. `-- +goose Up`, then a header comment: what changes, which Notion section or decision asks for it, what does
   not change, and "Forward-only and immutable: no Down section".
2. The guard that refuses any role but `provisioning_migrator` (SQLSTATE 42501).
3. A guard that names every missing role it needs (SQLSTATE 42704), before any change.
4. Guards for existing rows that would break the change: stop with a clear message and change nothing
   (precedent `00021`, `00022`; name the operator command or the fix in the message).
5. The change itself. Follow Notion's physical column order (precedent `00022`, which rebuilt tables to get it).
6. Grants per role, exactly what Notion gives each role, nothing more (`scripts/db/bootstrap-roles.sql` lists the
   roles). A new role is added to `bootstrap-roles.sql` (idempotent) and to the README role list.
7. After a table rebuild or a large data change, plan an `ANALYZE` of the rebuilt tables (00022 carried this into
   its runbook step).

Goose runs the file in one transaction. Use `-- +goose StatementBegin` / `StatementEnd` around `DO` blocks.

## 4. Tests (real PostgreSQL 18)

- New `tests/integration/test_schema_000NN.py`, shaped like `test_schema_00023.py`: migrate a database to the
  version before, seed representative old rows (helpers in `tests/integration/_pg.py` and `_rows.py`), migrate
  up, then assert:
  - the change itself, positive and negative cases (a refused write is refused with the right SQLSTATE);
  - every row is kept;
  - every role's privileges before and after: exactly the intended change;
  - the guards: wrong role refused, missing role refused, bad existing rows refused with nothing changed;
  - no Down section.
- Update `src/provisioning/infrastructure/postgres/schema.py` to the new final state (column order included).
  `tests/integration/test_schema_mirror.py` then proves it equals the migrated catalogue.
- Update stores and their tests if the change touches them.
- Run: `make py-test-integration` (no Kind stack up), then the fast tier of the `provisioning-gates` skill.

## 5. Docs

- `.claude/docs/runbooks/phase-1-database.md`: a short "Migration 000NN" section when the upgrade order, a stop of the
  processes, or an operator step matters.
- The decision record of the current phase (`decision-record` skill), and its section 5 with the test counts.
- The separate change report (owner rule, every DB revision): write
  `../platform-db-rev25-provisioning-000NN-report.md` (next to the repo, outside it) with three
  parts: "Changed" (table: where, before, after, why), "Not changed", "UI impact". Copy the layout of
  `platform-db-rev25-provisioning-00023-report.md` in the same folder. Never edit the Notion design page.

## 6. Then

Run the `db-notion-audit` skill. Zero DIFFERENT findings is the bar before a merge.

## Running it on a real database

Never run `make migrate-up` against a database on your own. That target is the only one that reads `.env`;
the owner runs it (`make migrate-status PG_DATABASE=<name>` first). Write the exact command for the owner.
