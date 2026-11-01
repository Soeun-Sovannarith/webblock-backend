-- AI service, code generation (ERD page "AI"). project_id, generation_request_id and generation_contract_id
-- reference the Project service and have no foreign keys.
CREATE SCHEMA IF NOT EXISTS ai_codegen;

DO $$ BEGIN CREATE TYPE ai_codegen.implementation_strategy AS ENUM ('PUCK_JSON','SOURCE_CODE','MIXED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_codegen.run_status AS ENUM ('QUEUED','RUNNING','SUCCEEDED','FAILED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_codegen.validation_type AS ENUM ('INSTALL','BUILD'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_codegen.validation_status AS ENUM ('RUNNING','PASSED','FAILED','SKIPPED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_codegen.artifact_type AS ENUM ('SOURCE_ARCHIVE','CHECK_REPORT','RUN_LOG'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS ai_codegen.puck_component_definitions (
 puck_component_definition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 component_catalog_version varchar(100) NOT NULL,
 component_type varchar(100) NOT NULL,
 component_label varchar(255) NOT NULL,
 editable_props_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
 allowed_operations jsonb NOT NULL DEFAULT '["UPDATE"]'::jsonb,
 implementation_mode ai_codegen.implementation_strategy NOT NULL DEFAULT 'PUCK_JSON',
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(component_catalog_version, component_type)
);

CREATE TABLE IF NOT EXISTS ai_codegen.input_versions (
 input_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL,
 generation_request_id uuid NOT NULL UNIQUE,
 generation_contract_id uuid NOT NULL,
 captured_edit_revision bigint NOT NULL,
 component_catalog_version varchar(100) NOT NULL,
 captured_input jsonb NOT NULL,
 input_hash char(64) NOT NULL,
 baseline_commit_sha varchar(64),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_input_versions_project_created ON ai_codegen.input_versions(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_codegen.harness_runs (
 harness_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL,
 generation_request_id uuid NOT NULL,
 generation_contract_id uuid NOT NULL,
 baseline_version_id uuid REFERENCES ai_codegen.input_versions(input_version_id),
 input_hash char(64) NOT NULL,
 strategy ai_codegen.implementation_strategy NOT NULL,
 status ai_codegen.run_status NOT NULL DEFAULT 'QUEUED',
 attempt integer NOT NULL DEFAULT 1 CHECK(attempt > 0),
 branch_name varchar(255),
 model_name varchar(255),
 input_tokens integer,
 output_tokens integer,
 errors text,
 error_code varchar(100),
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 completed_at timestamptz,
 -- Not in the ERD: the worker's lease, so a run whose worker died is requeued (store.reap_expired_runs).
 lease_expires_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_one_active_run_per_project ON ai_codegen.harness_runs(project_id) WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX IF NOT EXISTS ix_harness_runs_queue ON ai_codegen.harness_runs(created_at) WHERE status = 'QUEUED';
CREATE INDEX IF NOT EXISTS ix_harness_runs_project_created ON ai_codegen.harness_runs(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_harness_runs_generation_request ON ai_codegen.harness_runs(generation_request_id);

CREATE TABLE IF NOT EXISTS ai_codegen.validation_runs (
 validation_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 harness_run_id uuid NOT NULL REFERENCES ai_codegen.harness_runs(harness_run_id) ON DELETE CASCADE,
 validation_type ai_codegen.validation_type NOT NULL,
 attempt integer NOT NULL DEFAULT 1 CHECK(attempt > 0),
 required boolean NOT NULL DEFAULT true,
 status ai_codegen.validation_status NOT NULL DEFAULT 'RUNNING',
 result jsonb NOT NULL DEFAULT '{}'::jsonb,
 error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,
 completed_at timestamptz,
 UNIQUE(harness_run_id, validation_type, attempt)
);

CREATE TABLE IF NOT EXISTS ai_codegen.artifacts (
 artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 harness_run_id uuid NOT NULL REFERENCES ai_codegen.harness_runs(harness_run_id) ON DELETE CASCADE,
 artifact_type ai_codegen.artifact_type NOT NULL,
 storage_ref varchar(500) NOT NULL,
 content_hash char(64) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(harness_run_id, artifact_type)
);

INSERT INTO ai_codegen.puck_component_definitions(component_catalog_version,component_type,component_label,editable_props_schema,allowed_operations,implementation_mode)
VALUES
 ('1.0.0','Hero','Hero','{"title":"string","subtitle":"string"}','["ADD","UPDATE","REMOVE","MOVE"]','PUCK_JSON'),
 ('1.0.0','Text','Text','{"text":"string"}','["ADD","UPDATE","REMOVE","MOVE"]','PUCK_JSON'),
 ('1.0.0','ProductCards','Product Cards','{"title":"string","products":[{"name":"string","price":"string","description":"string"}]}','["ADD","UPDATE","REMOVE","MOVE"]','MIXED'),
 ('1.0.0','About','About','{"title":"string","text":"string"}','["ADD","UPDATE","REMOVE","MOVE"]','PUCK_JSON'),
 ('1.0.0','Location','Location','{"title":"string","address":"string"}','["ADD","UPDATE","REMOVE","MOVE"]','PUCK_JSON'),
 ('1.0.0','Footer','Footer','{"text":"string"}','["ADD","UPDATE","REMOVE","MOVE"]','PUCK_JSON')
ON CONFLICT(component_catalog_version,component_type) DO NOTHING;
