-- Brings the project database level with Notion "2 · Project" (platform-db-v6), "Who may write": PUBLIC has no
-- access. PostgreSQL makes a row type for every table and gives PUBLIC USAGE on it by default; V6 took that back on
-- the two enums only. This revokes it on the row type of every table in project, project_messaging and project_ops.
-- Tables, columns, rows, constraints, indexes, triggers and every role's grants do not change.
-- Run by Flyway as project_migrator (beforeEachMigrate.sql refuses any other role).
-- Forward-only and immutable: fix a mistake with a new file.

REVOKE ALL ON TYPE
    project.projects,
    project.pages,
    project.generation_contracts,
    project.generation_contract_schemas,
    project.generation_requests,
    project_messaging.processed_messages,
    project_messaging.outbox_record,
    project_messaging.outbox_partition,
    project_messaging.outbox_instance,
    project_messaging.pending_catalog_registrations,
    project_ops.flyway_schema_history
  FROM PUBLIC;
