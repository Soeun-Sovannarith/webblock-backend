-- Project service schema, revision 19 of the platform database design (section 2), as written.
DO $$
BEGIN
  IF current_user <> 'project_migrator' THEN
    RAISE EXCEPTION 'Migrations must run as project_migrator, not %', current_user;
  END IF;
END
$$;

REVOKE ALL ON SCHEMA project FROM PUBLIC;
REVOKE ALL ON SCHEMA project_ops FROM PUBLIC;

CREATE TYPE project.project_status AS ENUM ('draft', 'active', 'suspended');
CREATE TYPE project.generation_request_status AS ENUM ('captured', 'generating', 'publishing', 'published', 'failed');
CREATE TYPE project.domain_kind AS ENUM ('platform_subdomain', 'purchased');
CREATE TYPE project.domain_status AS ENUM (
  'pending_payment', 'registering', 'active', 'expired', 'transferring_out', 'transferred_out', 'released', 'failed');

CREATE TABLE project.projects (
  project_id                  uuid                   NOT NULL,
  owner_user_id               uuid                   NOT NULL,
  name                        varchar(255)           NOT NULL,
  business_type_id            uuid                   NOT NULL,
  edit_revision               bigint                 NOT NULL DEFAULT 0,
  component_catalog_version   varchar(100)           NOT NULL,
  github_repository_id        bigint,
  github_repository_full_name varchar(140),
  status                      project.project_status NOT NULL,
  created_at                  timestamptz            NOT NULL,
  updated_at                  timestamptz            NOT NULL,
  deleted_at                  timestamptz,
  CONSTRAINT projects_pkey PRIMARY KEY (project_id),
  CONSTRAINT projects_github_repository_id_key UNIQUE (github_repository_id),
  CONSTRAINT projects_github_repository_pair CHECK ((github_repository_id IS NULL) = (github_repository_full_name IS NULL)),
  CONSTRAINT projects_github_repository_id_check CHECK (github_repository_id > 0),
  CONSTRAINT projects_edit_revision_check CHECK (edit_revision >= 0)
);
CREATE INDEX projects_owner_idx ON project.projects (owner_user_id) WHERE deleted_at IS NULL;

CREATE TABLE project.pages (
  page_id         uuid         NOT NULL,
  project_id      uuid         NOT NULL,
  name            varchar(255) NOT NULL,
  path            varchar(255) NOT NULL,
  puck_tree       jsonb        NOT NULL,
  seo_title       varchar(255),
  seo_description varchar(500),
  sort_order      integer      NOT NULL,
  created_at      timestamptz  NOT NULL,
  updated_at      timestamptz  NOT NULL,
  deleted_at      timestamptz,
  CONSTRAINT pages_pkey PRIMARY KEY (page_id),
  CONSTRAINT pages_project_fk FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT,
  CONSTRAINT pages_path_check CHECK (path ~ '^/([a-z0-9-]+(/[a-z0-9-]+)*)?$')
);
CREATE UNIQUE INDEX pages_project_path_key ON project.pages (project_id, path) WHERE deleted_at IS NULL;

CREATE TABLE project.generation_contracts (
  generation_contract_id uuid         NOT NULL,
  project_id             uuid         NOT NULL,
  version_number         integer      NOT NULL,
  api_contract_version   varchar(100) NOT NULL,
  created_by_user_id     uuid         NOT NULL,
  created_at             timestamptz  NOT NULL,
  CONSTRAINT generation_contracts_pkey PRIMARY KEY (generation_contract_id),
  CONSTRAINT generation_contracts_project_fk FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT,
  CONSTRAINT generation_contracts_version_key UNIQUE (project_id, version_number),
  CONSTRAINT generation_contracts_project_contract_key UNIQUE (project_id, generation_contract_id),
  CONSTRAINT generation_contracts_version_check CHECK (version_number > 0)
);

CREATE TABLE project.generation_contract_schemas (
  generation_contract_id uuid NOT NULL,
  product_type_schema_id uuid NOT NULL,
  product_type_id        uuid NOT NULL,
  CONSTRAINT generation_contract_schemas_pkey PRIMARY KEY (generation_contract_id, product_type_schema_id),
  CONSTRAINT generation_contract_schemas_contract_fk FOREIGN KEY (generation_contract_id)
    REFERENCES project.generation_contracts (generation_contract_id) ON DELETE RESTRICT,
  CONSTRAINT generation_contract_schemas_type_key UNIQUE (generation_contract_id, product_type_id)
);

CREATE TABLE project.generation_requests (
  generation_request_id      uuid                              NOT NULL,
  project_id                 uuid                              NOT NULL,
  idempotency_key            varchar(255)                      NOT NULL,
  request_hash               char(64)                          NOT NULL,
  generation_contract_id     uuid                              NOT NULL,
  captured_edit_revision     bigint                            NOT NULL,
  captured_input             jsonb                             NOT NULL,
  captured_input_hash        char(64)                          NOT NULL,
  accepted_generation_run_id uuid,
  source_commit_sha          varchar(64),
  status                     project.generation_request_status NOT NULL,
  failure_code               varchar(100),
  created_at                 timestamptz                       NOT NULL,
  updated_at                 timestamptz                       NOT NULL,
  CONSTRAINT generation_requests_pkey PRIMARY KEY (generation_request_id),
  CONSTRAINT generation_requests_project_fk FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT,
  CONSTRAINT generation_requests_contract_fk FOREIGN KEY (project_id, generation_contract_id)
    REFERENCES project.generation_contracts (project_id, generation_contract_id) ON DELETE RESTRICT,
  CONSTRAINT generation_requests_idempotency_key UNIQUE (project_id, idempotency_key),
  CONSTRAINT generation_requests_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT generation_requests_captured_input_hash_check CHECK (captured_input_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT generation_requests_source_commit_sha_check CHECK (source_commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
  CONSTRAINT generation_requests_published_has_commit CHECK ((status = 'published') = (source_commit_sha IS NOT NULL))
);
CREATE UNIQUE INDEX generation_requests_one_in_flight ON project.generation_requests (project_id)
  WHERE status IN ('captured', 'generating', 'publishing');

CREATE TABLE project.domains (
  domain_id     uuid                  NOT NULL,
  project_id    uuid                  NOT NULL,
  hostname      varchar(253)          NOT NULL,
  kind          project.domain_kind   NOT NULL,
  status        project.domain_status NOT NULL,
  is_primary    boolean               NOT NULL,
  registrar_ref varchar(255),
  registered_at timestamptz,
  expires_at    timestamptz,
  failure_code  varchar(100),
  created_at    timestamptz           NOT NULL,
  updated_at    timestamptz           NOT NULL,
  CONSTRAINT domains_pkey PRIMARY KEY (domain_id),
  CONSTRAINT domains_project_fk FOREIGN KEY (project_id) REFERENCES project.projects (project_id) ON DELETE RESTRICT,
  CONSTRAINT domains_hostname_check CHECK (
    char_length(hostname) <= 253
    AND hostname ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,63}|xn--[a-z0-9-]{1,59})$'),
  CONSTRAINT domains_platform_subdomain_shape CHECK (
    kind <> 'platform_subdomain'
    OR (status = 'active' AND registrar_ref IS NULL AND registered_at IS NULL AND expires_at IS NULL AND failure_code IS NULL)),
  CONSTRAINT domains_primary_is_serving CHECK (NOT is_primary OR status IN ('active', 'transferring_out')),
  CONSTRAINT domains_registered_dates CHECK (
    kind <> 'purchased'
    OR status NOT IN ('active', 'expired', 'transferring_out', 'transferred_out')
    OR (registered_at IS NOT NULL AND expires_at IS NOT NULL AND expires_at > registered_at)),
  CONSTRAINT domains_failure_code_matches_status CHECK ((status = 'failed') = (failure_code IS NOT NULL))
);
CREATE UNIQUE INDEX domains_hostname_held_key ON project.domains (hostname)
  WHERE status IN ('pending_payment', 'registering', 'active', 'expired', 'transferring_out');
CREATE UNIQUE INDEX domains_one_platform_subdomain ON project.domains (project_id) WHERE kind = 'platform_subdomain';
CREATE UNIQUE INDEX domains_one_primary ON project.domains (project_id) WHERE is_primary;
CREATE UNIQUE INDEX domains_registrar_ref_key ON project.domains (registrar_ref) WHERE registrar_ref IS NOT NULL;

-- Guards. Each raises check_violation naming its constraint, worded like PostgreSQL's own message, so the
-- driver and Hibernate report the constraint name the error mapper uses.
CREATE FUNCTION project.projects_guard() RETURNS trigger
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
  RETURN NEW;
END
$$;
CREATE TRIGGER projects_guard BEFORE UPDATE ON project.projects
  FOR EACH ROW EXECUTE FUNCTION project.projects_guard();

CREATE FUNCTION project.generation_requests_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.captured_input IS DISTINCT FROM OLD.captured_input
     OR NEW.captured_input_hash IS DISTINCT FROM OLD.captured_input_hash
     OR NEW.captured_edit_revision IS DISTINCT FROM OLD.captured_edit_revision
     OR NEW.request_hash IS DISTINCT FROM OLD.request_hash THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'generation_requests_capture_immutable',
      MESSAGE = 'new row for relation "generation_requests" violates check constraint "generation_requests_capture_immutable"';
  END IF;
  IF OLD.source_commit_sha IS NOT NULL AND NEW.source_commit_sha IS DISTINCT FROM OLD.source_commit_sha THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'generation_requests_commit_write_once',
      MESSAGE = 'new row for relation "generation_requests" violates check constraint "generation_requests_commit_write_once"';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER generation_requests_guard BEFORE UPDATE ON project.generation_requests
  FOR EACH ROW EXECUTE FUNCTION project.generation_requests_guard();

CREATE FUNCTION project.domains_guard() RETURNS trigger
  LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  IF NEW.project_id IS DISTINCT FROM OLD.project_id
     OR NEW.hostname IS DISTINCT FROM OLD.hostname
     OR NEW.kind IS DISTINCT FROM OLD.kind THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'domains_identity_immutable',
      MESSAGE = 'new row for relation "domains" violates check constraint "domains_identity_immutable"';
  END IF;
  IF OLD.status IN ('released', 'failed', 'transferred_out') AND NEW.status IS DISTINCT FROM OLD.status THEN
    RAISE EXCEPTION USING ERRCODE = 'check_violation', CONSTRAINT = 'domains_terminal_status',
      MESSAGE = 'new row for relation "domains" violates check constraint "domains_terminal_status"';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER domains_guard BEFORE UPDATE ON project.domains
  FOR EACH ROW EXECUTE FUNCTION project.domains_guard();
