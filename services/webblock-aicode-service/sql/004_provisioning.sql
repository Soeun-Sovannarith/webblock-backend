-- Provisioning service (ERD page "Provisioning"). project_id and generation_request_id reference the
-- Project service and have no foreign keys.
CREATE SCHEMA IF NOT EXISTS provisioning;

DO $$ BEGIN CREATE TYPE provisioning.release_status AS ENUM ('PENDING','RUNNING','SUCCEEDED','FAILED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.release_step_name AS ENUM ('DEPLOY','ACTIVATE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.step_status AS ENUM ('RUNNING','SUCCEEDED','FAILED','SKIPPED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.command_kind AS ENUM ('PUBLISH','TAKE_OFFLINE','BRING_ONLINE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.hosting_reason AS ENUM ('OWNER_REQUEST','USER_SUSPENDED','ENTITLEMENT_ENDED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.command_status AS ENUM ('PENDING','SUCCEEDED','FAILED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.routing_status AS ENUM ('UNROUTED','ROUTED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.hosting_status AS ENUM ('OFFLINE','ONLINE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE provisioning.offline_reason AS ENUM ('NO_RELEASE','USER_SUSPENDED','ENTITLEMENT_ENDED','PROJECT_DELETED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS provisioning.releases (
 release_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL,
 generation_request_id uuid NOT NULL UNIQUE,
 repository_id bigint,
 git_commit_sha varchar(64),
 container_image_digest varchar(71),
 status provisioning.release_status NOT NULL DEFAULT 'PENDING',
 failure_code varchar(100),
 failure_summary text,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 promoted_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_releases_project_created ON provisioning.releases(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS provisioning.release_steps (
 release_step_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 release_id uuid NOT NULL REFERENCES provisioning.releases(release_id) ON DELETE CASCADE,
 step_name provisioning.release_step_name NOT NULL,
 attempt integer NOT NULL DEFAULT 1 CHECK(attempt > 0),
 status provisioning.step_status NOT NULL DEFAULT 'RUNNING',
 started_at timestamptz,
 finished_at timestamptz,
 deadline_at timestamptz,
 failure_code varchar(100),
 log_ref varchar(500),
 UNIQUE(release_id, step_name, attempt)
);

CREATE TABLE IF NOT EXISTS provisioning.project_hosting (
 project_id uuid PRIMARY KEY,
 live_release_id uuid REFERENCES provisioning.releases(release_id),
 last_command_number bigint NOT NULL DEFAULT 0,
 routing_status provisioning.routing_status NOT NULL DEFAULT 'UNROUTED',
 hosting_status provisioning.hosting_status NOT NULL DEFAULT 'OFFLINE',
 offline_reasons provisioning.offline_reason[] NOT NULL DEFAULT '{NO_RELEASE}',
 owner_status_version bigint NOT NULL DEFAULT 0,
 subdomain varchar(63),
 updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS provisioning.commands (
 command_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL,
 release_id uuid REFERENCES provisioning.releases(release_id),
 kind provisioning.command_kind NOT NULL,
 hosting_reason provisioning.hosting_reason,
 idempotency_key varchar(255) NOT NULL,
 request_fingerprint char(64) NOT NULL,
 command_number bigint NOT NULL,
 requested_by_subject varchar(255) NOT NULL,
 status provisioning.command_status NOT NULL DEFAULT 'PENDING',
 failure_code varchar(100),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(project_id, command_number),
 UNIQUE(project_id, idempotency_key)
);
