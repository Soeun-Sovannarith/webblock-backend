---
paths:
  - "**/db/migration/**"
  - "**/*.sql"
  - "**/model/entity/**/*.java"
  - "**/repository/**/*.java"
  - "**/alembic/**"
  - "**/models/**/*.py"
---
# Database

- Check every table/column against `docs/specs/data-dictionary.md` (names, types, lengths, required, defaults). If the change is not in the dictionary, stop and ask.
- Table names use the service prefix from the ERD: `account_`, `project_`, `catalog_`, `ai_codegen_` / `ai_assistant_`, `provisioning_`, `payment_`.
- One database per service. FKs only **inside** a service. Cross-service IDs (`owner_user_id`, `client_user_id`, `project_id` in payment…) are plain `uuid` with an index, no FK.
- Flyway: `V<yyyyMMddHHmm>__<snake_description>.sql`. Never edit a migration that was already applied — add a new one.
- Every table: `created_at timestamptz not null default now()`; mutable tables also `updated_at`.
- Enums: PostgreSQL `text` + `CHECK (col IN (...))` using the values from `status-flows.md` (easier to evolve than `CREATE TYPE`).
- Soft delete uses `deleted_at`; once set it never changes.
- Unique constraints the spec requires: `projects.subdomain`, `websites.db_role_name`, `idempotency_key`/`event_key` columns, `bills.bill_no`, `transactions.transaction_id`, `(conversation_id, message_number)`, `(change_set_id, item_order)`.
- Money is `numeric`, never float. Hashes are `char(64)` SHA-256 hex.
- Optimistic locking: `edit_revision`, `row_version` → JPA `@Version` or explicit `WHERE revision = ?`.
