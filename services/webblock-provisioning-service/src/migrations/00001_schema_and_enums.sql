-- +goose Up
-- Migrations are immutable and forward-only: never edit an applied file and never
-- drop data. Fix mistakes with a new migration.
--
-- One recorded exception (decision 0007 section 3): files 00001 to 00003 were
-- rewritten once, in Phase 1R, to the revision 16 baseline, because nothing had
-- been deployed and the earlier rows could not be mapped forward. goose tracks
-- version numbers, not file content, so a database migrated before that rewrite
-- keeps the earlier schema without any error: drop and recreate it (see
-- docs/runbooks/phase-1-database.md). The exception ends when Phase 1R is merged.

-- Ownership is the privilege boundary, so refuse to run as anyone but the migrator.
-- +goose StatementBegin
DO $$
BEGIN
    IF current_user <> 'provisioning_migrator' THEN
        RAISE EXCEPTION 'migrations must run as role provisioning_migrator (current role: %); see docs/runbooks/phase-1-database.md', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;
-- +goose StatementEnd

CREATE SCHEMA provisioning;

-- The seven enums, in the order of decision 0001 section 1. internal/domain/enums.go
-- carries the same labels in the same order; an integration test compares them.

CREATE TYPE provisioning.release_status AS ENUM (
    'accepted', 'building', 'built', 'candidate_ready', 'promoting',
    'live', 'superseded', 'failed', 'rolled_back'
);

CREATE TYPE provisioning.release_step_name AS ENUM (
    'build', 'access_setup', 'candidate', 'promotion', 'observation', 'rollback'
);

CREATE TYPE provisioning.release_step_status AS ENUM (
    'pending', 'running', 'succeeded', 'failed'
);

CREATE TYPE provisioning.route_state AS ENUM ('stable', 'changing', 'uncertain');

CREATE TYPE provisioning.hosting_state AS ENUM ('serving', 'offline');

CREATE TYPE provisioning.command_kind AS ENUM (
    'deploy', 'promote', 'rollback', 'suspend_hosting', 'resume_hosting'
);

CREATE TYPE provisioning.command_status AS ENUM (
    'accepted', 'running', 'succeeded', 'failed', 'skipped'
);
