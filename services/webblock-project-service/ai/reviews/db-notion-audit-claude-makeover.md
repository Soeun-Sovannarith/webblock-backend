# Project databases against Notion: independent audit

Stage 5 Verify. IDs covered: FR-35, FR-36, TR-14, TR-15, TR-16 (TRD Data section, `TRD.md:56-58`); rows also touch
TR-17 to TR-21; constitution `Art. III`.

- Branch `claude-makeover`, commit `7a9ebb7` (from `main` at `ed5fcaf`). Working tree clean at audit time; this
  report is the only file written.
- Auditor: `db-notion-audit` skill, read-only subagent.
- Notion pages (fetched fresh, read-only), last edited:

| Page | Last edited |
| --- | --- |
| 2 · Project | 2026-10-22T10:17:01.919Z |
| 6 · Shared catalog | 2026-10-21T14:16:06.723Z |
| Database Design | 2026-10-26T16:20:45.379Z |
| platform-db-v6 hub | 2026-10-26T16:21:34.007Z |

All four times equal the ones the last audit (`db-notion-audit-project-notion-alignment.md`, commit `498a5f0`)
recorded: Notion has not changed since then.

Per page:

| Page | DIFFERENT | PLANNED | ACCEPTED-BY-DECISION | MATCH |
| --- | --- | --- | --- | --- |
| 2 · Project | 0 | 0 (all four planned items are claimed by V6 and compared) | 5 | 66 |
| 6 · Shared catalog | 0 | 3 | 3 | 58 |

Since `498a5f0`, `git diff --stat 498a5f0 HEAD` shows no change to any migration, `scripts/db/`, any entity, or any
schema test but the helper `Rows.java` (sub-domain helper only). The changed files that touch the database docs are
decision 0015 (now names the catalog's `public` schema, old gap G7), and the runbooks `database.md` and
`catalog-database.md` (now name V7, `subdomain` and `pending_catalog_registrations`, old gap G6).

## 0. Method and sources

Read in full at `7a9ebb7` in the worktree:

- Project migrations `src/main/resources/db/migration/project/`: `beforeEachMigrate.sql`, `V1` to `V3`, `V5` to
  `V7` in full; `V4` (outbox) for its tables, indexes and grants.
- Catalog migrations `src/main/resources/db/migration/catalog/`: `V1` to `V7` in full; `R__catalog_reference_data.sql`
  rules header and first statements.
- Roles: `scripts/db/bootstrap-roles.sql`, `scripts/db/catalog-bootstrap-roles.sql`.
- Entities: column mappings of `ProjectEntity`, `PageEntity`, `GenerationContractEntity`,
  `GenerationContractSchemaEntity`, `GenerationRequestEntity`; `ddl-auto: validate` at
  `src/main/resources/application.yml:27`; Flyway schemas `application.yml:35-36`.
- Tests in `src/test/java/org/kshrd/webblock/project/db/`: anchors in `MigrationTest`, `GrantsTest`,
  `CatalogGrantsTest`; the file list. Every test file but `Rows.java` is byte-identical to `498a5f0`, so its line
  citations below are the same as in the last audit.
- Decisions 0001, 0012 to 0015 (status lines: all Accepted) and the decisions index; runbooks `database.md`,
  `catalog-database.md`; TRD Data section and "Drift seen".
- Notion: the four pages above, the Database Design page's Conventions, Project and Shared catalog sections.

Not run: this is a static reading. No Gradle task, no Testcontainers run. The privilege and catalog rules cited with a
test were proven by a live run at `498a5f0` (44 tests, 0 failures); neither those tests nor the migrations they test
have changed since, but they were not run again here.

## 1. Columns: name, type, nullability, order

Every column was compared for name, type, length, NULL rule, default and position against "2 · Project",
"6 · Shared catalog" and the Database Design page (the two agree). A Notion type without NULL is NOT NULL.

| Table | Columns | Evidence | Status |
| --- | --- | --- | --- |
| `project.projects` | 13 in Notion order: `subdomain` VARCHAR(63) NOT NULL 4th, `repository_id` BIGINT NULL and `repository_full_name` VARCHAR(140) NULL 8th and 9th, `edit_revision` default 0 | `V6:51-64`; `MigrationTest:56-121` | MATCH |
| `project.pages` | 11 | `V1:40-51` | MATCH |
| `project.generation_contracts` | 6 | `V1:58-64` | MATCH |
| `project.generation_contract_schemas` | 3, PK (contract, schema) | `V1:72-76` | MATCH |
| `project.generation_requests` | 14 | `V1:82-96` | MATCH |
| `project_messaging.processed_messages` | `message_id`, `message_type`, `outcome`, `processed_at` | `V2:6-10` | MATCH |
| `project_messaging.pending_catalog_registrations` | `project_id`, `business_profile`, `created_at` | `V5:4-7` | MATCH |
| `project_messaging.outbox_record`, `_partition`, `_instance` | Notion names the tables only; columns are Namastack's | `V4:7-44`; decision 0001 | ACCEPTED-BY-DECISION |
| `project_ops` | Flyway history only | `application.yml:35-36`; `MigrationTest:199-215` | MATCH |
| JPA entities | Same names, lengths, NULL rules; frozen columns `updatable = false` (`subdomain` at `ProjectEntity.java:33`) | entity files | MATCH |
| `catalog.business_types` | 5 | `catalog/V1:21-29` | MATCH |
| `catalog.product_types` | 6 | `catalog/V1:31-42` | MATCH |
| `catalog.product_type_schemas` | 6 | `catalog/V1:44-57` | MATCH |
| `catalog.websites` | 9 | `catalog/V1:59-76` | MATCH |
| `catalog.business_profiles` | 12, `opening_hours` default `[]` | `catalog/V1:78-100` | MATCH |
| `catalog.categories` | 7 | `catalog/V1:102-115` | MATCH |
| `catalog.products` | 13, `price` NUMERIC(12,2) NULL, `category_id` NULL | `catalog/V1:117-143` | MATCH |
| `catalog.product_images` | 8 | `catalog/V1:149-165` | MATCH |

No target versus actual difference. The project table list is fixed by `MigrationTest:19-42`.

## 2. Enums: values and order

| Enum | Values | Evidence | Status |
| --- | --- | --- | --- |
| `project.project_status` | `draft`, `active`, `suspended` | `V1:13` | MATCH |
| `project.generation_request_status` | `captured`, `generating`, `publishing`, `published`, `failed` | `V1:14` | MATCH |
| `catalog.website_status` | `active`, `suspended`, `archived` | `catalog/V1:17` | MATCH |
| `catalog.product_status` | `draft`, `published`, `archived` | `catalog/V1:19` | MATCH |

`project.domain_kind` and `project.domain_status` are dropped (`V6:37-38`; `MigrationTest:140-162`). `failure_code`
is text, not an enum, as Notion says.

## 3. Rules Notion states, and what enforces them

### 3.1 `project.projects`

| Rule | Enforced by | Status |
| --- | --- | --- |
| Owner never changes | grant (not in the UPDATE list, `V6:141-143`); `GrantsTest:297-309` | MATCH |
| Business type never changes, for every role | trigger `V6:109-112`; grant; `GrantsTest:215-243` | MATCH |
| `deleted_at` set once, for every role | trigger `V6:113-116` | MATCH |
| `repository_id` and `repository_full_name` both empty or both set | check `V6:68` | MATCH |
| `repository_id` unique, above 0 | `V6:67`, `V6:69` | MATCH |
| `subdomain` unique over all projects, deleted ones included | UNIQUE constraint, not partial, `V6:66`; `SubdomainMigrationTest:166-175` | MATCH |
| `subdomain`: a guard trigger refuses any UPDATE | trigger `V6:117-120`; `SubdomainMigrationTest:142-164` | MATCH |
| `subdomain` is one DNS label, 3 to 63, reserved names refused (list is configuration) | app only: `Subdomain.java:14` | MATCH |
| `edit_revision` starts at 0 | default and check `V6:57`, `V6:70` | MATCH |
| Rows never purged; no delete while referenced | no DELETE grant; FKs `ON DELETE RESTRICT` `V6:127-132` | MATCH |

### 3.2 `project.pages`

| Rule | Enforced by | Status |
| --- | --- | --- |
| Belongs to one project | FK `V6:127-128` | MATCH |
| `path` is `/` or lower-case words, digits and dashes split by `/` | check `V1:54` | MATCH |
| `path` unique per project among live pages | partial unique index `V1:56` | MATCH |
| At most `max_pages` live pages; a save sends `edit_revision` | app only (TR-19) | MATCH |

### 3.3 `project.generation_contracts`

| Rule | Enforced by | Status |
| --- | --- | --- |
| `version_number` above 0, unique per project | `V1:67`, `V1:69` | MATCH |
| Insert-only | grant `V3:16` (SELECT, INSERT); `GrantsTest:77-88` | MATCH |

### 3.4 `project.generation_contract_schemas`

| Rule | Enforced by | Status |
| --- | --- | --- |
| PK (contract, schema); at most one schema per product type | `V1:76`, `V1:79` | MATCH |
| Insert-only | grant `V3:16`; `GrantsTest:91-161` | MATCH |
| Schema belongs to the type, type to the business type | app only (Catalog check) | MATCH |

### 3.5 `project.generation_requests`

| Rule | Enforced by | Status |
| --- | --- | --- |
| `idempotency_key` unique per project | `V1:101` | MATCH |
| `request_hash`, `captured_input_hash` are SHA-256 hex | checks `V1:102-103` | MATCH |
| Contract of the same project | composite FK `V1:99-100` on key `V1:68` | MATCH |
| One in flight per project | partial unique index `V1:107-108` (TR-19) | MATCH |
| Hashes, contract and capture fields never change | trigger `V1:166-172`; grant for the contract; `GrantsTest:163-212` | MATCH |
| `source_commit_sha` set once, set exactly when `published`, SHA-1 or SHA-256 hex | trigger `V1:173-176`; checks `V1:104-105` | MATCH |
| Never deleted | no DELETE grant; FKs RESTRICT | MATCH |
| Run accepted only if hashes equal; `failure_code` shape | app only | MATCH |

### 3.6 Background storage

| Rule | Enforced by | Status |
| --- | --- | --- |
| `message_type` is `<service>.<event>.v<n>` | check `V2:12` | MATCH |
| Outbox records are kept | no DELETE on `outbox_record` `V4:60` | MATCH |
| `project_ops` holds migration history only | `MigrationTest:199-215`; no app USAGE `V3:5`, `GrantsTest:58-75` | MATCH |
| Extra checks `processed_messages_outcome_check`, `pending_catalog_registrations_profile_object` | `V2:13`, `V5:9`; decision 0012 | ACCEPTED-BY-DECISION |

### 3.7 Shared catalog

| Table | Rule | Enforced by | Status |
| --- | --- | --- | --- |
| business_types | `code` unique, of the form `*_shop` | `catalog/V1:28`; reference data rules `R__:12`; `CatalogReferenceDataTest:52` | MATCH |
| business_types | Retire, never delete | no DELETE grant; FKs without cascade | MATCH |
| product_types | `code` unique per business type | `catalog/V1:41` | MATCH |
| product_types | Every active type has an active schema | `R__:13`; `CatalogReferenceDataTest:62` | MATCH |
| product_type_schemas | `version_number` above 0, unique per type | `catalog/V1:54-56` | MATCH |
| product_type_schemas | Type, version and schema never edited | trigger `catalog/V2:50-67` | MATCH |
| websites | Business type and role name never change | trigger `catalog/V2:31-47` | MATCH |
| websites | `db_role_name` unique | `catalog/V1:72` | MATCH |
| websites | Limit 0 or more, image limit above 0, used 0 or more | checks `catalog/V1:73-75` | MATCH |
| websites | Used bytes kept by the database, never by sites | `track_media_bytes` `catalog/V2:102-162`; no site grant | MATCH |
| websites | Never deleted | no DELETE grant; FKs RESTRICT | MATCH |
| websites | `updated_at` "Last change": set at registration, not moved by a status or limit change | `catalog/V3:53`; `CatalogGrantsTest:78-89`; decision 0014 | ACCEPTED-BY-DECISION |
| business_profiles | One per website (PK/FK) | `catalog/V1:91-93` | MATCH |
| business_profiles | Never deleted while the website exists | no DELETE for any role: `catalog/V7:32`; FK RESTRICT `catalog/V1:92-93`; `CatalogRlsTest:96-107` | MATCH |
| business_profiles | Logo key under `<website_id>/` | check `catalog/V1:94-95` | MATCH |
| business_profiles | Logo size above 0, NULL exactly when no logo | checks `catalog/V1:96-99` | MATCH |
| business_profiles | `opening_hours` default `[]` | `catalog/V1:88` | MATCH |
| business_profiles | Logo counts against storage | trigger `catalog/V2:160-162` | MATCH |
| categories | `slug` unique per website | `catalog/V1:114` | MATCH |
| categories | Deleting leaves products without a category | `ON DELETE SET NULL (category_id)` `catalog/V1:138-139` | MATCH |
| products | Price 0 or more or NULL | `catalog/V1:140` | MATCH |
| products | `row_version` above 0 | `catalog/V1:141` | MATCH |
| products | `attributes` is a JSON object | `catalog/V1:142` | MATCH |
| products | Schema's type belongs to the website's business type | trigger `catalog/V2:72-95` | MATCH |
| products | Category from its own website | composite FK `catalog/V1:138-139` | MATCH |
| products | Deleting deletes its images | `ON DELETE CASCADE` `catalog/V1:161-162` | MATCH |
| products | Exact attribute filters are fast (GIN, the one index Notion names) | `catalog/V1:147`; `catalog/V6:17` drops the other; `CatalogMigrationTest:107-117` | MATCH |
| products | Visitors see only `published` | app only | MATCH |
| product_images | Key under `<website_id>/` | `catalog/V1:163` | MATCH |
| product_images | `byte_size` above 0 | `catalog/V1:164` | MATCH |
| product_images | Product on the same website | composite FK `catalog/V1:161-162` | MATCH |
| product_images | Over limit refused with `catalog_storage_limit`, one upload at a time | `catalog/V2:137-146` | MATCH |
| tenant tables | Rows filtered to `current_website_id()`: login role, active site only | `catalog/V2:19-28`; `catalog/V3:15-47`; `CatalogRlsTest:71-130` | MATCH |
| business_profiles | Project's role has its own rule | policy `catalog/V3:29-32`; `CatalogGrantsTest:139-156` | MATCH |

## 4. Roles and grants (final state)

| Role or grantee | Final state | Evidence | Status |
| --- | --- | --- | --- |
| `project_migrator` | Owns every object; every migration refuses other roles | `V1:2-8`, `beforeEachMigrate.sql:2-8`; bootstrap `:5-7`; `MigrationTest:165`, `:218` | MATCH |
| `project_app` on `projects` | SELECT, INSERT; UPDATE on the 8 listed columns, not `subdomain` | `V6:140-143` (the V3 grants went with the dropped old table); `GrantsTest:215-243` | MATCH |
| `project_app` on `pages` | SELECT, INSERT; UPDATE on the 8 listed columns | `V3:6`, `V3:10-11`; `GrantsTest:163-212` | MATCH |
| `project_app` on `generation_requests` | SELECT, INSERT; UPDATE on the 5 listed columns | `V3:6`, `V3:12-13`; `GrantsTest:163-212` | MATCH |
| `project_app` on both contract tables | SELECT, INSERT | `V3:16`; `GrantsTest:91-161` | MATCH |
| `project_app` in general | No DELETE on business rows, no DDL, no `project_ops` | `V3:5`, `V6:35`; `GrantsTest:28-75` | MATCH |
| `project_app` on outbox tables | What the library executes | `V4:60-61`; decision 0001 | ACCEPTED-BY-DECISION |
| `project_app` on the inbox and pending registrations | SELECT, INSERT on `processed_messages`; SELECT, INSERT, DELETE on `pending_catalog_registrations` | `V3:17`, `V5:12`; decision 0013 | ACCEPTED-BY-DECISION |
| `PUBLIC` on project schemas and tables | None | `V1:10-11`, `V2:3`; `MigrationTest:178-195` | MATCH |
| `PUBLIC` on project database | None; CONNECT only for `project_app` | `database.md:33` (runbook step 3) | MATCH |
| `PUBLIC` on project functions | None | `V6:136`; `GrantsTest:250-295` | MATCH |
| `PUBLIC` on project enum types | None | `V6:137`; `GrantsTest:250-295` | MATCH |
| `PUBLIC` on the row types of all 11 tables in `project`, `project_messaging`, `project_ops` | None | `V7:8-20`; `GrantsTest:251-294` | MATCH |
| `catalog_owner` | Owns every object; migrations refuse other roles | `catalog/V1:6-13`; bootstrap `:6-8`; `CatalogMigrationTest:34`, `:54` | MATCH |
| `catalog_platform` | INSERT, SELECT websites; UPDATE status and 2 limits; INSERT profiles; SELECT 3 reference tables | `catalog/V3:51-54`, `V4:15-16`, `V5:17`; `CatalogGrantsTest:46-89` | MATCH |
| `catalog_site` | SELECT, INSERT, UPDATE profiles; SELECT, INSERT, UPDATE, DELETE categories, products, images; SELECT 3 reference tables; EXECUTE `current_website_id`; nothing on `websites`; no BYPASSRLS | `catalog/V3:58-64`, `catalog/V7:32`; bootstrap `:12-13`; `CatalogGrantsTest:46-98` | MATCH |
| Catalog schema | USAGE for the two runtime roles only | `catalog/V3:51`, `:58`; `CatalogGrantsTest:131-137` | MATCH |
| `PUBLIC` on catalog functions and TEMP | None | `catalog/V2:164-168`, `V4:19-23`; `CatalogGrantsTest:91-98`, `CatalogMigrationTest:164-173` | MATCH |
| `PUBLIC` on catalog enum types | None | `catalog/V6:19`; `CatalogGrantsTest:105-129` | MATCH |
| `PUBLIC` on the row types of the 8 catalog tables and the catalog Flyway history | None | `catalog/V7:20-30`; `CatalogGrantsTest:106-128` | MATCH |
| `PUBLIC` CONNECT on the catalog database | Kept for the per-site logins; no TEMP, no schema USAGE, no EXECUTE | `catalog-database.md:24-26`; decision 0015 | ACCEPTED-BY-DECISION |

`site_<uuid>` logins are created by Provisioning, outside this repository.

## 5. Notion's global rules (hub and Database Design conventions)

| Rule | Page | Evidence | Status |
| --- | --- | --- | --- |
| One database per service; no SQL FK between services | Project | no FK to `account`, `catalog` or `project` across databases | MATCH |
| Status and kind types are native lowercase enums | Project | section 2 | MATCH |
| Single-column PK is singular table name plus `_id` | Project | all PKs in section 1 | MATCH |
| Service references are not FKs | Project | `owner_user_id`, `business_type_id`, `created_by_user_id`, `product_type_*` | MATCH |
| No purge of projects or generations | Project | sections 3.1, 3.5 | MATCH |
| No hostname table; `subdomain` unique, never reused | Project | `V6:35`, `V6:66` | MATCH |
| 2 to 4 pages checked by Project; SEO columns on pages | Project | app; `V1:46-47` | MATCH |
| Project has 5 business tables; PostgreSQL 18 | Project | `MigrationTest:19-42`; `PostgresFixture.java:147` | MATCH |
| English only, USD only, one price, no variants, no template table | Catalog | no such columns or tables | MATCH |
| No visitor-written tables | Catalog | none | MATCH |
| GIN on `attributes`; byte counts and limits in catalog | Catalog | `catalog/V1:147`, `catalog/V1:64-66` | MATCH |
| No human role bypasses catalog RLS | Catalog | FORCE RLS `catalog/V3:15-22`; `CatalogGrantsTest:161-172`; roles without BYPASSRLS | MATCH |
| Catalog has 8 business tables | Catalog | `catalog/V1`; `CatalogGrantsTest:108-122` | MATCH |

## 6. In the database but not in Notion

| Item | Page | Evidence | Status |
| --- | --- | --- | --- |
| Nine outbox indexes and library column types | Project | `V4:46-55`; decision 0001 | ACCEPTED-BY-DECISION |
| Helper unique key `generation_contracts_project_contract_key` behind a composite FK | Project | `V1:68` | MATCH (enforces a stated rule) |
| Guard triggers and functions | Project | `V1:163-181`, `V6:106-125` | MATCH (each enforces a stated rule) |
| Index `catalog.products_listing` | Catalog | dropped `catalog/V6:17`; `CatalogMigrationTest:107-117` | MATCH (gone) |
| Helper unique keys `categories_website_id_category_id_key`, `products_website_id_product_id_key` | Catalog | `catalog/V1:113`, `:136` | MATCH (enforce stated rules) |
| Storage, immutability and business type triggers and functions | Catalog | `catalog/V2` | MATCH (each enforces a stated rule) |
| Default schema `public` in the catalog database, empty, PUBLIC USAGE only (old G7) | Catalog | decision 0015 `:9-10`; `catalog-database.md:24-26` | ACCEPTED-BY-DECISION |

Gaps Notion does not cover (not counted; for the lead):

- G8 (new, static reading, low weight). The project database also keeps PostgreSQL's default `public` schema, on
  which PUBLIC holds USAGE (no CREATE in PostgreSQL 18). Unlike the catalog, PUBLIC has no CONNECT here
  (`database.md:33`), so only `project_app` and `project_migrator` can reach it, and no migration creates anything
  there. `database.md:96-100` says "PUBLIC may do nothing" and lists only the three project schemas; decision 0015
  covers only the catalog. No test looks at it. Options: one sentence in `database.md` (and, if the owner wants it
  recorded, a decision like 0015), or a `REVOKE ALL ON SCHEMA public FROM PUBLIC` in the runbook's database step.
- G9 (Notion text, not database state). "2 · Project" still calls `subdomain` "(planned)" and its "Planned, not yet
  applied" list still describes the four V6 items as future; the Database Design page marks the hostname
  "(planned)". V6 is applied (`V6:1-13`, `.claude/CLAUDE.md:37`). The pages are read-only for agents: the owner may
  update them. Nothing here needs a schema change.
- Old G6 is closed: `database.md:67-100` and `.claude/CLAUDE.md:37` now name V6, V7 and `subdomain`, and no longer
  list `domains`. The TRD "Drift seen" (`TRD.md:124-129`) lists no database item.

## 7. PLANNED items and their state

"2 · Project" planned items: V6 claims all four (`V6:1-13`, TR-17), so they were compared like any other.

| Item | Evidence | Status |
| --- | --- | --- |
| `github_repository_*` become `repository_id`, `repository_full_name` | `V6:59-60`, `V6:73-79`; `MigrationTest:126-138` | MATCH |
| `project.domains`, `domain_kind`, `domain_status` dropped | `V6:35-38`; `MigrationTest:140-162` | MATCH |
| Owner index over all projects | `V6:101`; `SubdomainMigrationTest:177-188` | MATCH |
| `subdomain`: backfill `p-` plus first 12 characters, NOT NULL, unique over all rows, guard trigger | `V6:19-33`, `V6:55`, `V6:66`, `V6:76`, `V6:117-120`; `SubdomainMigrationTest:84-` | MATCH |

"6 · Shared catalog" planned items, none claimed by a migration:

| Item | State | Status |
| --- | --- | --- |
| Project deletion archives its website | `archived` value exists (`catalog/V1:17`); no writer | PLANNED |
| Product limit enforced | not stored in the catalog schema; nothing enforces it | PLANNED |
| Storage limits from the plan | configuration defaults today (`application.yml`, `storage-limit-bytes`, `max-image-bytes`) | PLANNED |

## 8. DIFFERENT findings, most important first

None. Notion is unchanged since the last audit (same four last-edited times), and no migration, role script or
entity changed after `498a5f0`; every rule and grant above was read again from the files at `7a9ebb7`. No applied
migration was edited (TR-16).

Result: 0 DIFFERENT, 3 PLANNED, 8 ACCEPTED-BY-DECISION, 124 MATCH
