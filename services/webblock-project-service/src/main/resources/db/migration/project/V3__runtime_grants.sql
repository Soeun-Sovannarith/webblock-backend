-- project_app: data access only. No DELETE anywhere (deletion is a soft delete), no DDL, no Flyway history (no USAGE on
-- project_ops).
-- UPDATE is granted per column: identity, ownership, idempotency and captured-input columns are frozen at the privilege
-- level, in addition to the triggers that guard them.
GRANT USAGE ON SCHEMA project, project_messaging TO project_app;
GRANT SELECT, INSERT ON project.projects, project.pages, project.generation_requests, project.domains TO project_app;
GRANT UPDATE (name, status, edit_revision, component_catalog_version, github_repository_id,
              github_repository_full_name, updated_at, deleted_at)
  ON project.projects TO project_app;
GRANT UPDATE (name, path, puck_tree, seo_title, seo_description, sort_order, updated_at, deleted_at)
  ON project.pages TO project_app;
GRANT UPDATE (status, accepted_generation_run_id, source_commit_sha, failure_code, updated_at)
  ON project.generation_requests TO project_app;
GRANT UPDATE (status, is_primary, registrar_ref, registered_at, expires_at, failure_code, updated_at)
  ON project.domains TO project_app;
GRANT SELECT, INSERT ON project.generation_contracts, project.generation_contract_schemas TO project_app;
GRANT SELECT, INSERT ON project_messaging.processed_messages TO project_app;
