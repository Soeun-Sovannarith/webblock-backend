---
paths:
  - "src/main/resources/db/migration/**"
  - "scripts/db/**"
---

# Database migrations (owner rules)

- Use the `new-migration` skill for any new file here.
- Flyway SQL, two folders: `db/migration/project/` for the Project database (`webblock_project`, schemas `project`,
  `project_messaging`, `project_ops`) and `db/migration/catalog/` for the shared catalog database. Names are
  `V<n>__<what>.sql`; the catalog's reference data is the repeatable `R__catalog_reference_data.sql`.
- The design is Notion "2 · Project" (project database) and "6 · Shared catalog" (catalog), with the conventions of
  platform-db-v6 and "Database Design". These pages are read-only. The databases match them 100%: every table,
  column, type, NULL rule, enum value, index, constraint, role and grant, and nothing more. A change that Notion
  does not show needs the owner first. Objects Notion does not name (guard triggers, checks) only enforce a rule
  Notion states. Compare with Notion; never assume the code matches.
- The last files are `V8` in `project/` and `V7` in `catalog/`; the next are `V9` and `V8`.
  V6 applied Notion's "Planned, not yet applied" list on "2 · Project" (`subdomain`, the `repository_*` columns,
  no `project.domains`); project V8 applied the nullable `subdomain` and the three new `projects` columns.
- Never edit an applied migration and never write an undo: Flyway checksums them. Applied files still name old doc
  paths on purpose; leave them. Fix a mistake with a new numbered file.
- Production migrates only in the separate job (decision `0005`); the service validates the schema at start.
- Project migrations run only as `project_migrator`; `beforeEachMigrate.sql` refuses any other role. Catalog
  migrations run only as `catalog_owner` and start with the same guard. Roles come from
  `scripts/db/bootstrap-roles.sql` and `scripts/db/catalog-bootstrap-roles.sql`.
- The runtime role `project_app` never deletes business rows and updates only the columns Notion lists.
- Every schema change ships with: a test in `src/test/java/org/kshrd/webblock/project/db/`, the matching JPA entity
  change (Hibernate validates the schema at start), and a separate change report (changed, not changed, UI impact).
  Then run the `db-notion-audit` skill.
- Runbooks: `.claude/docs/runbooks/database.md`, `.claude/docs/runbooks/migrations.md`,
  `.claude/docs/runbooks/catalog-database.md`.
