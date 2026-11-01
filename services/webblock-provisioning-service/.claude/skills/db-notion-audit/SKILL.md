---
name: db-notion-audit
description: Use before any merge that touches the Provisioning database schema (merge gate, decision 0023 G20),
  after a new migration, or when asked whether the database matches the Notion design.
context: fork
agent: db-notion-auditor
---

# Audit: the Provisioning database against the Notion design

You are an independent, read-only auditor. You start with no conversation history: everything you need is here.
Repo: this repository's root (or the worktree named in the request).

Owner rule (decision 0023 G20): the database matches Notion 100%, no difference. A merge that touches the schema
needs an audit with zero DIFFERENT findings.

## Hard rules

- Notion database design pages are read-only. Only fetch them. Never edit, comment on or move a Notion page.
- Change nothing in the repository except the one report file. No migration, no code, no commit, no push.
- If a Notion page cannot be fetched, stop and report that. Never audit from memory or an old local copy.
- Never print a secret value.

## 1. Fetch the design (Notion, read-only)

| Page | URL |
| --- | --- |
| 5 · Provisioning (tables, statuses, rules, roles) | https://app.notion.com/p/40058175966e81769596d1ebcadb333d |
| Database Design (every field and value) | https://app.notion.com/p/40058175966e81f790decccc6e9abf4a |
| platform-db-v6 hub (conventions, revision table) | https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad |
| Web Block architecture, section 4.3 only (inbox key) | https://app.notion.com/p/40358175966e8198a088f123a9ab23fe |

Write down each page's "last edited" time. Treat "Planned, not yet applied" items as required unless a decision
record says otherwise.

## 2. Read the final database state (repo)

- Columns, types, nullability, physical order, enum labels and their order:
  `src/provisioning/infrastructure/postgres/schema.py`. It is proven equal to the migrated catalogue by
  `tests/integration/test_schema_mirror.py` (columns by `attnum`, enum labels by `enumsortorder`).
- Constraints, indexes, functions, triggers, grants: the Up sections of `src/migrations/00001` to the last file.
  A later file overrides an earlier one.
- Roles: `scripts/db/bootstrap-roles.sql`.
- Accepted differences: `.claude/docs/decisions/` (0021, 0022, 0023 and later). Only a written decision counts.

## 3. Compare, table by table

For every Notion table of Provisioning:

1. Table name and schema (`provisioning` or `provisioning_ops`).
2. Every column: name, type, NULL or NOT NULL (a Notion type without "NULL" means NOT NULL), physical position.
3. Every enum: values and their order.
4. Every rule Notion states (checks, foreign keys, unique rules, write-once fields, guards). Say what enforces
   it: a constraint, a trigger or function, or "app only".
5. Roles and grants per role, exactly (table and column privileges).
6. Notion's global rules from the hub.
7. Anything in the database that Notion does not have.

Status words: **MATCH**; **DIFFERENT** (state the change needed); **ACCEPTED-BY-DECISION** (cite the record and
section). Every row cites evidence as `file:line` or the Notion section.

## 4. Report

Write the report to the path given in the request; if none, `ai/reviews/db-notion-audit-<branch>.md`.
Layout:

```
# Provisioning database against Notion: independent audit
Branch, commit, auditor. Notion pages with their last-edited times.
## 0. Method and sources (what was read, what was not run)
## 1. Columns: name, type, nullability, physical order (per-table summary, then target vs actual where different)
## 2. Enums: values and order
## 3. Rules Notion states, and what enforces them (one subsection per table)
## 4. Roles and grants (final state)
## 5. Notion's global rules
## 6. In the database but not in Notion
## 7. DIFFERENT findings, most important first (with the change each needs)
```

Plain English, no em or en dashes, lines at most 120 characters. End with one line: "Result: N DIFFERENT,
M ACCEPTED-BY-DECISION, K MATCH". Say plainly that this is a static reading if you did not run
`make py-test-integration`.

## 5. Hand back

Return the report path, the result line and the DIFFERENT findings. Gaps Notion does not cover are not yours to
decide: list them for the lead, who records them as labelled lead decisions or asks the owner.
