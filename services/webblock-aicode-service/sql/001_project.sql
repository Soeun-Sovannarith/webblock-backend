-- Project service (ERD page "Project"). Columns referencing other services (account, catalog, ai_codegen)
-- are plain UUIDs without foreign keys.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS project;

DO $$ BEGIN CREATE TYPE project.project_status AS ENUM ('ACTIVE','SUSPENDED','DELETED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE project.generation_request_status AS ENUM ('QUEUED','RUNNING','SUCCEEDED','FAILED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS project.projects (
 project_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 owner_user_id uuid,
 name varchar(255) NOT NULL,
 subdomain varchar(63) UNIQUE,
 business_type_id uuid,
 edit_revision bigint NOT NULL DEFAULT 0 CHECK(edit_revision >= 0),
 component_catalog_version varchar(100) NOT NULL DEFAULT '1.0.0',
 repository_id bigint,
 repository_full_name varchar(140),
 status project.project_status NOT NULL DEFAULT 'ACTIVE',
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS project.pages (
 page_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL REFERENCES project.projects(project_id) ON DELETE CASCADE,
 name varchar(255) NOT NULL,
 path varchar(255) NOT NULL CHECK(path LIKE '/%'),
 puck_tree jsonb NOT NULL DEFAULT '{"root":{"props":{}},"content":[]}'::jsonb,
 seo_title varchar(255),
 seo_description varchar(500),
 sort_order integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 deleted_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pages_live_path ON project.pages(project_id, path) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS project.generation_contracts (
 generation_contract_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL REFERENCES project.projects(project_id) ON DELETE CASCADE,
 version_number integer NOT NULL CHECK(version_number > 0),
 api_contract_version varchar(100) NOT NULL,
 created_by_user_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id, version_number)
);

CREATE TABLE IF NOT EXISTS project.generation_contract_schemas (
 product_type_schema_id uuid NOT NULL,
 product_type_id uuid NOT NULL,
 generation_contract_id uuid NOT NULL REFERENCES project.generation_contracts(generation_contract_id) ON DELETE CASCADE,
 PRIMARY KEY(generation_contract_id, product_type_schema_id)
);

CREATE TABLE IF NOT EXISTS project.generation_requests (
 generation_request_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL REFERENCES project.projects(project_id) ON DELETE CASCADE,
 generation_contract_id uuid NOT NULL REFERENCES project.generation_contracts(generation_contract_id),
 idempotency_key varchar(255) NOT NULL,
 request_hash char(64) NOT NULL,
 captured_edit_revision bigint NOT NULL,
 captured_input jsonb NOT NULL,
 captured_input_hash char(64) NOT NULL,
 accepted_generation_run_id uuid,
 source_commit_sha varchar(64),
 status project.generation_request_status NOT NULL DEFAULT 'QUEUED',
 failure_code varchar(100),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_generation_requests_project_created ON project.generation_requests(project_id, created_at DESC);
