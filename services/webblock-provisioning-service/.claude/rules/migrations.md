---
paths:
  - "src/migrations/**"
  - "scripts/db/**"
---

# Database migrations (owner rules)

- Use the `new-migration` skill for any new file here.
- The design is Notion "5 · Provisioning" and "Database Design" (read-only). The database matches Notion 100%
  (decision 0023 G20). A change that Notion does not show needs the owner first. A difference is fixed by a new
  migration, or taken to the owner before anything else merges. Objects Notion does not name (indexes, checks,
  guard triggers) only enforce a rule Notion states. Compare the schema with Notion; never assume the code matches.
- Migrations are forward-only and immutable: never edit an existing file, never write a Down section. Fix a
  mistake with a new numbered file.
- Every migration runs only as `provisioning_migrator` and stops before any change when a role it needs is
  missing (copy the two guard blocks of `00023`). Roles come from `scripts/db/bootstrap-roles.sql`.
- Every schema change ships with a test in `tests/integration/`, a matching `schema.py`, and a separate change
  report (changed, not changed, UI impact). Then run the `db-notion-audit` skill.
- Runbook: `.claude/docs/runbooks/phase-1-database.md`.
