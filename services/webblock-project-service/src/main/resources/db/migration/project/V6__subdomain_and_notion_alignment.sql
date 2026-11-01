-- V6 (FR-36): project.projects becomes exactly Notion "2 · Project", table projects, in its column order, and the
-- page's "Planned, not yet applied" list is applied:
--   * subdomain varchar(63) NOT NULL, unique over all projects (deleted ones included), never updated; existing rows
--     get 'p-' and the first 12 characters of their project ID;
--   * github_repository_id and github_repository_full_name become repository_id and repository_full_name;
--   * project.domains, project.domain_kind and project.domain_status are dropped;
--   * the owner index covers all projects, not only live ones.
-- project.projects is rebuilt so subdomain stands fourth, where Notion lists it; every row, key, foreign key, trigger
-- and grant is kept. "Who may write" gives project_app UPDATE on eight columns of projects; subdomain is not one.
-- "PUBLIC has no access": PUBLIC's default EXECUTE on the guard functions and USAGE on the enum types are revoked.
-- Not changed: pages, generation_contracts, generation_contract_schemas, generation_requests (only their foreign keys
-- are dropped and re-created with the same names), project_messaging, project_ops, the roles.
-- Forward-only and immutable: fix a mistake with a new file.

LOCK TABLE project.projects, project.pages, project.generation_contracts, project.generation_requests,
  project.domains IN ACCESS EXCLUSIVE MODE;

-- Stops before any change when the backfill would give two projects the same sub-domain.
DO $$
DECLARE
  shared bigint;
BEGIN
  SELECT count(*) INTO shared
    FROM (SELECT 1 FROM project.projects GROUP BY left(project_id::text, 12) HAVING count(*) > 1) AS prefixes;
  IF shared > 0 THEN
    RAISE EXCEPTION USING ERRCODE = 'unique_violation',
      MESSAGE = format('V6 stopped and changed nothing: %s sub-domain prefix(es) are shared by two or more projects. '
        'The backfill gives each existing project p- and the first 12 characters of its project ID, which must be '
        'unique. Find them with SELECT left(project_id::text, 12), count(*) FROM project.projects GROUP BY 1 '
        'HAVING count(*) > 1, have the owner decide their sub-domains, then run V6 again.', shared);
  END IF;
END
$$;

DROP TABLE project.domains;
DROP FUNCTION project.domains_guard();
DROP TYPE project.domain_kind;
DROP TYPE project.domain_status;

ALTER TABLE project.pages DROP CONSTRAINT pages_project_fk;
ALTER TABLE project.generation_contracts DROP CONSTRAINT generation_contracts_project_fk;
ALTER TABLE project.generation_requests DROP CONSTRAINT generation_requests_project_fk;

-- The old table's index-backed names are freed for the new table.
ALTER TABLE project.projects RENAME TO projects_v5;
ALTER TABLE project.projects_v5 RENAME CONSTRAINT projects_pkey TO projects_v5_pkey;
ALTER TABLE project.projects_v5 RENAME CONSTRAINT projects_github_repository_id_key
  TO projects_v5_github_repository_id_key;
ALTER INDEX project.projects_owner_idx RENAME TO projects_v5_owner_idx;

CREATE TABLE project.projects (
  project_id                uuid                   NOT NULL,
  owner_user_id             uuid                   NOT NULL,
  name                      varchar(255)           NOT NULL,
  subdomain                 varchar(63)            NOT NULL,
  business_type_id          uuid                   NOT NULL,
  edit_revision             bigint                 NOT NULL DEFAULT 0,
  component_catalog_version varchar(100)           NOT NULL,
  repository_id             bigint,
  repository_full_name      varchar(140),
  status                    project.project_status NOT NULL,
  created_at                timestamptz            NOT NULL,
  updated_at                timestamptz            NOT NULL,
  deleted_at                timestamptz,
  CONSTRAINT projects_pkey PRIMARY KEY (project_id),
  CONSTRAINT projects_subdomain_key UNIQUE (subdomain),
  CONSTRAINT projects_repository_id_key UNIQUE (repository_id),
  CONSTRAINT projects_repository_pair CHECK ((repository_id IS NULL) = (repository_full_name IS NULL)),
  CONSTRAINT projects_repository_id_check CHECK (repository_id > 0),
  CONSTRAINT projects_edit_revision_check CHECK (edit_revision >= 0)
);

INSERT INTO project.projects (
  project_id, owner_user_id, name, subdomain, business_type_id, edit_revision, component_catalog_version,
  repository_id, repository_full_name, status, created_at, updated_at, deleted_at)
SELECT project_id, owner_user_id, name, 'p-' || left(project_id::text, 12), business_type_id, edit_revision,
       component_catalog_version, github_repository_id, github_repository_full_name, status, created_at, updated_at,
       deleted_at
  FROM project.projects_v5;

DROP TABLE project.projects_v5;

-- PostgreSQL names NOT NULL constraints uniquely per schema, so while projects_v5 held projects_<column>_not_null the
-- new table's got a numbered name. They take the plain name back, as a table created once would have.
DO $$
DECLARE
  c record;
BEGIN
  FOR c IN
    SELECT con.conname, 'projects_' || a.attname || '_not_null' AS plain
      FROM pg_constraint con
      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1]
     WHERE con.conrelid = 'project.projects'::regclass AND con.contype = 'n'
       AND con.conname <> 'projects_' || a.attname || '_not_null'
  LOOP
    EXECUTE format('ALTER TABLE project.projects RENAME CONSTRAINT %I TO %I', c.conname, c.plain);
  END LOOP;
END
$$;

CREATE INDEX projects_owner_idx ON project.projects (owner_user_id);

-- The business type and the sub-domain never change and deleted_at is set once, for every role. Each check raises
-- check_violation naming its constraint, worded like PostgreSQL's own message, so the driver and Hibernate report the
-- constraint name the error mapper uses.
CREATE OR REPLACE FUNCTION project.projects_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.business_type_id IS DISTINCT FROM OLD.business_type_id THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'projects_business_type_immutable',
      MESSAGE = 'new row for relation "projects" violates check constraint "projects_business_type_immutable"';
  END IF;
  IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'projects_deleted_at_write_once',
      MESSAGE = 'new row for relation "projects" violates check constraint "projects_deleted_at_write_once"';
  END IF;
  IF NEW.subdomain IS DISTINCT FROM OLD.subdomain THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'projects_subdomain_immutable',
      MESSAGE = 'new row for relation "projects" violates check constraint "projects_subdomain_immutable"';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER projects_guard BEFORE UPDATE ON project.projects
  FOR EACH ROW EXECUTE FUNCTION project.projects_guard();

ALTER TABLE project.pages ADD CONSTRAINT pages_project_fk
  FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT;
ALTER TABLE project.generation_contracts ADD CONSTRAINT generation_contracts_project_fk
  FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT;
ALTER TABLE project.generation_requests ADD CONSTRAINT generation_requests_project_fk
  FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT;

-- Notion: PUBLIC has no access. PostgreSQL gives PUBLIC EXECUTE on functions and USAGE on types by default; both
-- are taken back for the project schema's guards and enums.
REVOKE ALL ON FUNCTION project.projects_guard(), project.generation_requests_guard() FROM PUBLIC;
REVOKE ALL ON TYPE project.project_status, project.generation_request_status FROM PUBLIC;

-- Notion "Who may write": read and insert, and UPDATE on these eight columns only; never DELETE.
GRANT SELECT, INSERT ON project.projects TO project_app;
GRANT UPDATE (name, status, edit_revision, component_catalog_version, repository_id, repository_full_name,
              updated_at, deleted_at)
  ON project.projects TO project_app;
