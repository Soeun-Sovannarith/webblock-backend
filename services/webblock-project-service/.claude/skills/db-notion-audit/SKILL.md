---
name: db-notion-audit
description: Use before any merge that touches the Project or catalog database schema, after a new migration or
  entity change, or when asked whether the database matches the Notion design.
context: fork
agent: db-notion-auditor
---

# Audit: the Project databases against the Notion design

You are an independent, read-only auditor. You start with no conversation history: everything you need is here.
Repo: this repository's root (or the worktree named in the request).

Owner rule: the databases match Notion 100%, no difference: every table, column, type, NULL rule, enum value,
index, constraint, role and grant, and nothing more. A merge that touches a schema needs an audit with zero
DIFFERENT findings.

## Hard rules

- Notion database design pages are read-only. Only fetch them. Never edit, comment on or move a Notion page.
- Change nothing in the repository except the one report file. No migration, no code, no commit, no push.
- If a Notion page cannot be fetched, stop and report that. Never audit from memory or an old local copy.
- Never print a secret value; never read `.env` files or dev token files.

## 1. Fetch the design (Notion, read-only)

| Page | URL |
| --- | --- |
| 2 · Project (tables, statuses, rules, roles, "Planned, not yet applied") | https://app.notion.com/p/40058175966e81d4a77bfb48e736757a |
| 6 · Shared catalog (the catalog database this repo migrates) | https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a |
| Database Design (every field and value) | https://app.notion.com/p/40058175966e81f790decccc6e9abf4a |
| platform-db-v6 hub (conventions, revision table) | https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad |

Write down each page's "last edited" time. An item on "Planned, not yet applied" is reported as **PLANNED**, not
DIFFERENT, unless a migration on this branch already claims to apply it (then it is compared like any other).

## 2. Read the final database state (repo)

- Tables, columns (order of `CREATE TABLE` and later `ALTER TABLE`), types, NULL rules, enums, checks, foreign
  keys, unique rules, indexes, functions, triggers and grants: every file in
  `src/main/resources/db/migration/project/` and `src/main/resources/db/migration/catalog/`, in version order
  (a later file overrides an earlier one), plus `project/beforeEachMigrate.sql` and the catalog's
  `R__catalog_reference_data.sql`.
- Roles: `scripts/db/bootstrap-roles.sql`, `scripts/db/catalog-bootstrap-roles.sql`.
- What the code expects: the JPA entities
  (`src/main/java/org/kshrd/webblock/project/domain/*/model/*Entity.java`); production validates them against the
  schema (`ddl-auto=validate` in `src/main/resources/application.yml`).
- What the tests prove: `src/test/java/org/kshrd/webblock/project/db/` (`MigrationTest` table list, `GrantsTest`,
  `CatalogGrantsTest`, constraint and trigger tests).
- Accepted differences: `.claude/docs/decisions/` (only an Accepted record counts) and
  `.claude/docs/runbooks/database.md`.

Optional, only when the request asks for a live check and Docker is free: `./gradlew integrationTest --tests
'org.kshrd.webblock.project.db.*'` and quote the counts. Otherwise say plainly that this is a static reading.

## 3. Compare, table by table

For every Notion table of Project (`project.*`, the `project_messaging` background storage Notion lists, and
`project_ops` as "migration history only") and every catalog table this repo migrates:

1. Table name and schema.
2. Every column: name, type, NULL or NOT NULL (a Notion type without "NULL" means NOT NULL), position.
3. Every enum: values and their order.
4. Every rule Notion states (checks, foreign keys, unique rules, write-once fields, "rows are never purged"). Say
   what enforces it: a constraint, a trigger or function, a grant, or "app only".
5. Roles and grants per role, exactly (table and column privileges): Notion's "Who may write" list against the
   `GRANT` statements; `PUBLIC` has none.
6. Notion's global rules from the hub.
7. Anything in the database that Notion does not have.

Status words: **MATCH**; **DIFFERENT** (state the change needed); **PLANNED** (on Notion's planned list, not yet
applied); **ACCEPTED-BY-DECISION** (cite the record). Every row cites evidence as `file:line` or the Notion
section.

## 4. Report

Write the report to the path given in the request; if none, `ai/reviews/db-notion-audit-<branch>.md`
(`git branch --show-current`).

```
# Project databases against Notion: independent audit
Branch, commit, auditor. Notion pages with their last-edited times.
## 0. Method and sources (what was read, what was not run)
## 1. Columns: name, type, nullability, order (per-table summary, then target vs actual where different)
## 2. Enums: values and order
## 3. Rules Notion states, and what enforces them (one subsection per table)
## 4. Roles and grants (final state)
## 5. Notion's global rules
## 6. In the database but not in Notion
## 7. PLANNED items and their state
## 8. DIFFERENT findings, most important first (with the change each needs)
```

Plain English, no em or en dashes, lines at most 120 characters, no dates beyond the Notion "last edited" times.
End with one line: "Result: N DIFFERENT, P PLANNED, M ACCEPTED-BY-DECISION, K MATCH".

## 5. Hand back

Return the report path, the result line and the DIFFERENT findings. Gaps Notion does not cover are not yours to
decide: list them for the lead, who records them (`decision-record` skill) or asks the owner.
