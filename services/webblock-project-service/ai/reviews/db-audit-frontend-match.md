# Project databases against Notion: independent audit

Branch `frontend-match`, commit b29c87d (worktree `.claude/worktrees/subdomain-at-first-deploy`), auditor: db-notion-auditor.
Cites: TRD Data section TR IDs (`.claude/rules/spec-driven.md`) and Art. III of the constitution.

Notion pages fetched fresh (last edited):
- 2 · Project: 2026-10-31T11:59:18Z
- 6 · Shared catalog: 2026-10-21T14:16:06Z
- Database Design and the platform-db-v6 hub: not fetched. The request named only the two pages and the changed files
  touch nothing the hub's global rules cover beyond what "2 · Project" restates. This is a gap in method, not a finding.

## 0. Method and sources

Static reading only. No Gradle or Docker run; no live database. Read: all `project/V1..V8` and `beforeEachMigrate.sql`,
catalog `R__catalog_reference_data.sql` and its diff against `main`, `ProjectEntity.java`, `GrantsTest`, `MigrationTest`,
`CatalogReferenceDataTest`, decision `0018`. Only V8 and the catalog repeatable changed on this branch (V1 to V7 and
catalog V1 to V7 are unchanged and were audited as the base state).

Physical column order: the skill compares "position", and V6 rebuilt `projects` to put `subdomain` where Notion lists
it, so this audit treats physical order as part of the match (one DIFFERENT below). Whether the owner accepts order as
a difference is the owner's call; the finding says what each choice costs.

## 1. Columns: name, type, nullability, order

`project.projects`, target (Notion) against actual (V6 then V8):

| Notion position | Column | Notion type | Actual | Result |
| --- | --- | --- | --- | --- |
| 1 | project_id | UUID PK | uuid NOT NULL, projects_pkey (V6:269,282) | MATCH |
| 2 | owner_user_id | UUID NOT NULL | uuid NOT NULL (V6:270) | MATCH |
| 3 | name | VARCHAR(255) | varchar(255) NOT NULL (V6:271) | MATCH |
| 4 | subdomain | VARCHAR(63) UNIQUE NULL | varchar(63), V8:386 drops NOT NULL, projects_subdomain_key kept (V6:283) | MATCH |
| 5 | business_type_id | UUID | uuid NOT NULL (V6:273) | MATCH |
| 6 | template_code | VARCHAR(100) NULL | varchar(100) NULL, V8:411, actual position 14 | DIFFERENT (position only) |
| 7 | edit_revision | BIGINT | bigint NOT NULL DEFAULT 0, check >= 0 (V6:274,287) | MATCH |
| 8 | component_catalog_version | VARCHAR(100) | varchar(100) NOT NULL (V6:275) | MATCH |
| 9 | repository_id | BIGINT UNIQUE NULL | bigint, unique, check > 0 (V6:276,284,286) | MATCH |
| 10 | repository_full_name | VARCHAR(140) NULL | varchar(140) (V6:277) | MATCH |
| 11 | status | project.project_status | NOT NULL (V6:278) | MATCH |
| 12 | created_at | TIMESTAMPTZ | NOT NULL (V6:279) | MATCH |
| 13 | updated_at | TIMESTAMPTZ | NOT NULL (V6:280) | MATCH |
| 14 | deleted_at | TIMESTAMPTZ NULL | timestamptz NULL (V6:281) | MATCH |
| 15 | suspended_at | TIMESTAMPTZ NULL | timestamptz NULL, V8:412 | MATCH (type, NULL rule, order) |
| 16 | reactivated_at | TIMESTAMPTZ NULL | timestamptz NULL, V8:413 | MATCH (type, NULL rule, order) |

Actual physical order after V8: project_id, owner_user_id, name, subdomain, business_type_id, edit_revision,
component_catalog_version, repository_id, repository_full_name, status, created_at, updated_at, deleted_at,
template_code, suspended_at, reactivated_at. Only `template_code` is out of place: Notion lists it fifth after
`business_type_id`; ALTER TABLE ADD COLUMN puts it after `deleted_at`. `suspended_at` and `reactivated_at` are last in
both, so they match. The JPA entity (`ProjectEntity.java:22-71`) maps every column by name with matching lengths and
nullability, so `ddl-auto=validate` is unaffected by order. TR: Data section, projects table.

Other tables (`pages`, `generation_contracts`, `generation_contract_schemas`, `generation_requests`, messaging,
`project_ops`): unchanged on this branch, MATCH as before. Catalog tables: no DDL change, MATCH.

## 2. Enums: values and order

- `project.project_status`: draft, active, suspended (V1:13). MATCH.
- `project.generation_request_status`: captured, generating, publishing, published, failed (V1:14). MATCH.
- `domain_kind` and `domain_status`: dropped in V6, absent from Notion. MATCH.
- Catalog `website_status` and `product_status`: not touched. MATCH.

## 3. Rules Notion states, and what enforces them

`project.projects`:
- subdomain unique over all projects, deleted included, many NULLs allowed: `projects_subdomain_key` UNIQUE (V6:283);
  PostgreSQL treats NULLs as distinct. MATCH.
- subdomain "exactly one change, from NULL to a value": trigger `projects_guard` as replaced in V8:390-407. The check
  `NEW IS DISTINCT FROM OLD AND NOT (OLD IS NULL AND NEW IS NOT NULL)` refuses value to other value and value to NULL
  with constraint name `projects_subdomain_immutable`. CREATE OR REPLACE keeps the trigger and the REVOKE from PUBLIC
  of V6:353. MATCH.
- business_type_id never changes, deleted_at set once, for every role: guard V8:393-400, unchanged. MATCH.
- template_code "never changes": enforced by privilege only (no UPDATE grant), plus `updatable = false` in the entity
  (`ProjectEntity.java:38`). Notion states no database trigger for it (same as owner_user_id). MATCH.
- subdomain label shape (DNS label, 3 to 63, reserved names): Notion states it as a rule, not as a constraint; enforced
  by app only (`Subdomain.java`, decision 0017). No CHECK exists and none is named in Notion. MATCH.
- repository pair and repository_id > 0: V6:285-286. MATCH.
- Rows never purged; no row deleted while referenced: no DELETE grant; FKs ON DELETE RESTRICT (V6:344-349). MATCH.
- suspended_at / reactivated_at semantics (NULL while not suspended): app only; Notion states no constraint. MATCH.
- Index: owner index over all projects (V6:318). Notion's list says planned; V6 applied it. MATCH.

Catalog reference data (`R__catalog_reference_data.sql`):
- business type codes of form `*_shop`: unchanged codes; only `name` values changed (Fashion, Food & Beverage,
  Health & Beauty). Notion gives no names, name is "Display name" VARCHAR(100); all within 100. MATCH.
- new product type `shoes` (code unique per business type, under clothing_shop id 3f555430...): row at R__:38.
  MATCH.
- "Every active product type has an active schema": `shoes` has a version-1 schema row, is_active true (R__:54 and
  R__:78-82), JSON Schema object schema, `additionalProperties` false like its siblings. MATCH.
- Schema immutability: the file only adds a new schema row with a new ID; no existing schema's product_type_id,
  version or field_schema changed. The upsert is safe against the immutability trigger. MATCH.
- Product type belongs to the schema's business type: shoes schema id 9b1f7c42... points at shoes product type
  e4d2a1b7.... MATCH.

## 4. Roles and grants (final state)

`project_app` on `project.projects`: SELECT, INSERT (V6:357) and UPDATE on
name, status, edit_revision, component_catalog_version, repository_id, repository_full_name, updated_at, deleted_at
(V6:358); plus subdomain (V8:409); plus suspended_at, reactivated_at (V8:415). Final set of 11 columns.

Notion "Who may write" for projects: name, status, edit_revision, component_catalog_version, repository_id,
repository_full_name, updated_at, deleted_at, suspended_at, reactivated_at, and subdomain (only from NULL to a value).
Same 11 columns. MATCH. `template_code` is correctly absent. No DELETE. `GrantsTest.java:237-238` asserts exactly this
list. The "only from NULL to a value" qualifier is enforced by the guard trigger, not the grant. MATCH.

`pages` and `generation_requests` update lists, `project_migrator`, PUBLIC (functions and row types revoked in V6/V7):
unchanged, MATCH. New columns inherit the table-level SELECT and INSERT. V7's revoke on row types covers the table's
row type, which includes the new columns. MATCH.

Catalog roles and grants: no GRANT changed; `shoes` rows are plain reference data read through existing grants. MATCH.

## 5. Notion's global rules

- PUBLIC has no access: V6/V7 revokes remain; V8 creates no new function or type. MATCH.
- Forward-only, applied migrations never edited: V1 to V7 unchanged on this branch (V8 is new). MATCH.
- Reference data upsert on fixed IDs, retire never delete: no delete in the diff. MATCH.

## 6. In the database but not in Notion

Nothing extra. No new index, constraint, grant, trigger or table. The V8 guard function is the same trigger Notion
describes ("a guard trigger allows exactly one change, from NULL to a value").

## 7. PLANNED items and their state

"2 · Project", "Planned, not yet applied":
1. `github_*` columns renamed to `repository_*`: applied by V6. Now equal to the main text. Compared as MATCH.
2. `domains` table and enums dropped: applied by V6. MATCH.
3. Owner index over all projects: applied by V6. MATCH.
4. subdomain nullable, guard NULL to value, UPDATE grant: claimed and applied by V8, so compared above. MATCH.
5. `template_code`, `suspended_at`, `reactivated_at` added nullable, grant on the last two: claimed by V8, compared
   above. MATCH apart from the position of `template_code`.

Note for the lead: items 1 to 3 on Notion's list describe V6, which is applied. They are stale wording on Notion, not
a database difference. Items 4 and 5 stay on the list even though V8 applies them; Notion is read-only for agents,
so the owner clears them when V8 is merged. "6 · Shared catalog" lists no planned item touching reference data.

## 8. DIFFERENT findings, most important first

1. `project.projects.template_code` physical position (column 14, Notion position 6, after `business_type_id`). TR:
   Data section, projects table; Art. III ("every column ... 100%"). Type, length and NULL rule match; only the order
   differs. Because the column is added by ALTER TABLE, the only fix is a forward migration `V9` that rebuilds the
   table in Notion's order, as V6 did (lock, rename, create, copy, drop, restore constraint names, re-add the three
   foreign keys, trigger, grants, revokes, and update `MigrationTest` and `SubdomainMigrationTest`). Cost: a table
   rebuild of `projects` in production. Alternative: the owner accepts physical order as not part of the match and a
   decision record says so (Art. III names tables, columns, types, NULL rules and so on, and does not name order).
   Nothing in code depends on the order. Owner decision needed; if accepted, this finding becomes
   ACCEPTED-BY-DECISION and the count drops to 0.

Gaps for the lead (not findings): the Database Design page and the hub were not fetched; Notion's catalog page
lists no reference-data names, so the new display names and the `shoes` product type cannot be compared with Notion,
only with the rules it states.

Result: 1 DIFFERENT, 0 PLANNED, 0 ACCEPTED-BY-DECISION, 62 MATCH (static reading; no integration run)
