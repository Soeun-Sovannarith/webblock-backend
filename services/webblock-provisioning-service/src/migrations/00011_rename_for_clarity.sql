-- +goose Up
-- Revision 19 names (decision 0012): renames only. No table, column, constraint, index, trigger, enum
-- value or row is added or removed. Types, nullability, checks, grants and guard behaviour stay as
-- they are after 00010.
--
--   tables       provisioning.project_release_state  -> project_hosting
--                provisioning_ops.command_work       -> command_dispatch
--                provisioning_ops.source_published_inbox -> source_published_events
--   types        route_state -> routing_status, hosting_state -> hosting_status (value serving ->
--                online), provisioning_ops.inbox_status -> source_event_status
--   columns      decision 0012 section 3.1
--   constraints, indexes, triggers and guard functions: decision 0012 section 3.2
--   role         provisioning_payment_intake -> provisioning_hosting_intake (decision 0012 section 4)
--
-- PostgreSQL carries constraints, indexes, foreign keys, column grants, partial index predicates,
-- check expressions, trigger WHEN clauses and stored enum values through a rename, but not
-- PL/pgSQL function bodies. Every guard whose body, TABLE = or CONSTRAINT = names an old table,
-- column, type, value or role is therefore re-created at the end of this file, from its latest body
-- (00002, 00004, 00005, 00006, 00008 or 00010) with only the names changed, and the file ends with a
-- check that fails the whole migration if a function body still contains an old name.
-- releases_terminal_status_final and commands_terminal_status_final contain no old name and are not
-- touched.
--
-- PostgreSQL 18 names the NOT NULL constraints <table>_<column>_not_null and does not rename them
-- with the table or the column, so each one is renamed here too.
--
-- Goose runs the file in one transaction; any failure changes nothing. Forward-only and immutable,
-- like every migration (no Down section, as in 00001 to 00010).

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

-- Fail before any change, with one message that names every missing role. It is created by
-- scripts/db/bootstrap-roles.sql; commands_insert_shape below tests membership in it.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_hosting_intake']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- Types and the enum value.
------------------------------------------------------------------------------------------------------

ALTER TYPE provisioning.route_state RENAME TO routing_status;
ALTER TYPE provisioning.hosting_state RENAME TO hosting_status;
ALTER TYPE provisioning.hosting_status RENAME VALUE 'serving' TO 'online';
ALTER TYPE provisioning_ops.inbox_status RENAME TO source_event_status;

------------------------------------------------------------------------------------------------------
-- Tables.
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.project_release_state RENAME TO project_hosting;
ALTER TABLE provisioning_ops.command_work RENAME TO command_dispatch;
ALTER TABLE provisioning_ops.source_published_inbox RENAME TO source_published_events;

------------------------------------------------------------------------------------------------------
-- Columns.
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.project_hosting RENAME COLUMN active_release_id TO live_release_id;
ALTER TABLE provisioning.project_hosting RENAME COLUMN operation_generation TO last_command_number;
ALTER TABLE provisioning.project_hosting RENAME COLUMN route_state TO routing_status;
ALTER TABLE provisioning.project_hosting RENAME COLUMN hosting_state TO hosting_status;

ALTER TABLE provisioning.releases RENAME COLUMN source_generation_request_id TO generation_request_id;
ALTER TABLE provisioning.releases RENAME COLUMN source_repository_id TO github_repository_id;
ALTER TABLE provisioning.releases RENAME COLUMN source_commit_sha TO git_commit_sha;
ALTER TABLE provisioning.releases RENAME COLUMN image_digest TO container_image_digest;

ALTER TABLE provisioning.commands RENAME COLUMN fencing_generation TO command_number;
ALTER TABLE provisioning.commands RENAME COLUMN request_hash TO request_fingerprint;
ALTER TABLE provisioning.commands RENAME COLUMN requested_by TO requested_by_subject;

ALTER TABLE provisioning.release_steps RENAME COLUMN reason_code TO failure_code;
ALTER TABLE provisioning.release_steps RENAME COLUMN details_ref TO log_ref;

ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN lease_token TO claim_number;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN claimed_by TO claimed_by_worker;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN lease_expires_at TO claim_expires_at;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN claim_count TO execution_attempts;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN next_dispatch_at TO next_publish_at;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN last_confirmed_at TO last_broker_ack_at;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN retry_not_before TO retry_after;
ALTER TABLE provisioning_ops.command_dispatch RENAME COLUMN last_error_code TO last_failure_code;

ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN repository_id TO github_repository_id;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN commit_sha TO git_commit_sha;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN conflict_count TO mismatched_repeat_count;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN last_conflict_at TO last_mismatched_repeat_at;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN attempt_budget TO max_attempts;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN last_redriven_by TO last_manual_retry_by;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN last_redriven_at TO last_manual_retry_at;

------------------------------------------------------------------------------------------------------
-- Constraints and indexes (decision 0012 section 3.2). Renaming a primary key or unique constraint
-- renames its index with it.
------------------------------------------------------------------------------------------------------

-- releases
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_source_repository_id_check TO releases_github_repository_id_check;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_source_commit_sha_check TO releases_git_commit_sha_check;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_image_digest_check TO releases_container_image_digest_check;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_digest_after_build TO releases_image_after_build;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_no_digest_before_built TO releases_no_image_before_built;

-- project_hosting
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_pkey TO project_hosting_pkey;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_operation_generation_check TO project_hosting_last_command_number_check;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_active_fk TO project_hosting_live_release_fk;

-- commands (the three partial unique indexes the stores map errors from keep their names)
ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_fencing_key TO commands_command_number_key;
ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_fencing_generation_check TO commands_command_number_check;
ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_request_hash_check TO commands_request_fingerprint_check;
ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_requested_by_by_kind TO commands_requested_by_subject_by_kind;

-- command_dispatch
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_pkey TO command_dispatch_pkey;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_command_fk TO command_dispatch_command_fk;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_publish_attempts_check TO command_dispatch_publish_attempts_check;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_claim_count_check TO command_dispatch_execution_attempts_check;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_lease_token_check TO command_dispatch_claim_number_check;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_claimed_by_check TO command_dispatch_claimed_by_worker_check;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_lease_owner_with_expiry TO command_dispatch_claim_owner_with_expiry;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_correlation_id_check TO command_dispatch_correlation_id_check;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_last_error_code_check TO command_dispatch_last_failure_code_check;
ALTER INDEX provisioning_ops.command_work_due RENAME TO command_dispatch_due;

-- source_published_events
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_pkey TO source_published_events_pkey;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_repository_id_check TO source_published_events_github_repository_id_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_commit_sha_check TO source_published_events_git_commit_sha_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_attempts_check TO source_published_events_attempts_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_attempt_budget_check TO source_published_events_max_attempts_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_conflict_count_check TO source_published_events_mismatched_repeat_count_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_conflict_time_matches_count TO source_published_events_mismatched_repeat_time_matches_count;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_redriven_by_check TO source_published_events_manual_retry_by_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_redriven_together TO source_published_events_manual_retry_together;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_outcome_code_check TO source_published_events_outcome_code_check;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_outcome_matches_status TO source_published_events_outcome_matches_status;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_release_matches_status TO source_published_events_release_matches_status;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_finished_matches_status TO source_published_events_finished_matches_status;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_release_fk TO source_published_events_release_fk;
ALTER INDEX provisioning_ops.source_published_inbox_due RENAME TO source_published_events_due;

------------------------------------------------------------------------------------------------------
-- NOT NULL constraints: <new table>_<new column>_not_null.
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_fencing_generation_not_null TO commands_command_number_not_null;
ALTER TABLE provisioning.commands RENAME CONSTRAINT commands_request_hash_not_null TO commands_request_fingerprint_not_null;

ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_claim_count_not_null TO command_dispatch_execution_attempts_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_command_id_not_null TO command_dispatch_command_id_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_correlation_id_not_null TO command_dispatch_correlation_id_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_created_at_not_null TO command_dispatch_created_at_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_deadline_at_not_null TO command_dispatch_deadline_at_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_lease_token_not_null TO command_dispatch_claim_number_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_next_dispatch_at_not_null TO command_dispatch_next_publish_at_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_publish_attempts_not_null TO command_dispatch_publish_attempts_not_null;
ALTER TABLE provisioning_ops.command_dispatch RENAME CONSTRAINT command_work_updated_at_not_null TO command_dispatch_updated_at_not_null;

ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_attempt_budget_not_null TO source_published_events_max_attempts_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_attempts_not_null TO source_published_events_attempts_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_commit_sha_not_null TO source_published_events_git_commit_sha_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_conflict_count_not_null TO source_published_events_mismatched_repeat_count_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_generation_request_id_not_null TO source_published_events_generation_request_id_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_next_attempt_at_not_null TO source_published_events_next_attempt_at_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_project_id_not_null TO source_published_events_project_id_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_received_at_not_null TO source_published_events_received_at_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_repository_id_not_null TO source_published_events_github_repository_id_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_status_not_null TO source_published_events_status_not_null;
ALTER TABLE provisioning_ops.source_published_events RENAME CONSTRAINT source_published_inbox_updated_at_not_null TO source_published_events_updated_at_not_null;

ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_hosting_state_not_null TO project_hosting_hosting_status_not_null;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_operation_generation_not_null TO project_hosting_last_command_number_not_null;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_project_id_not_null TO project_hosting_project_id_not_null;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_route_state_not_null TO project_hosting_routing_status_not_null;
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_updated_at_not_null TO project_hosting_updated_at_not_null;

ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_source_commit_sha_not_null TO releases_git_commit_sha_not_null;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_source_generation_request_id_not_null TO releases_generation_request_id_not_null;
ALTER TABLE provisioning.releases RENAME CONSTRAINT releases_source_repository_id_not_null TO releases_github_repository_id_not_null;

------------------------------------------------------------------------------------------------------
-- Triggers and guard functions (a guard function keeps the name of its trigger). The bodies follow
-- below; the function is renamed first so that CREATE OR REPLACE finds it.
------------------------------------------------------------------------------------------------------

ALTER FUNCTION provisioning.project_release_state_generation_step() RENAME TO project_hosting_generation_step;
ALTER TRIGGER project_release_state_generation_step ON provisioning.project_hosting RENAME TO project_hosting_generation_step;

ALTER FUNCTION provisioning.project_release_state_generation_has_command() RENAME TO project_hosting_generation_has_command;
ALTER TRIGGER project_release_state_generation_has_command ON provisioning.project_hosting RENAME TO project_hosting_generation_has_command;
-- A constraint trigger also has a pg_constraint row (contype 't') with its own copy of the name, which
-- ALTER TRIGGER does not rename.
ALTER TABLE provisioning.project_hosting RENAME CONSTRAINT project_release_state_generation_has_command TO project_hosting_generation_has_command;

ALTER FUNCTION provisioning.project_release_state_insert_shape() RENAME TO project_hosting_insert_shape;
ALTER TRIGGER project_release_state_insert_shape ON provisioning.project_hosting RENAME TO project_hosting_insert_shape;

ALTER FUNCTION provisioning_ops.command_work_insert_shape() RENAME TO command_dispatch_insert_shape;
ALTER TRIGGER command_work_insert_shape ON provisioning_ops.command_dispatch RENAME TO command_dispatch_insert_shape;

ALTER FUNCTION provisioning_ops.command_work_monotonic() RENAME TO command_dispatch_monotonic;
ALTER TRIGGER command_work_monotonic ON provisioning_ops.command_dispatch RENAME TO command_dispatch_monotonic;

ALTER FUNCTION provisioning_ops.source_published_inbox_insert_shape() RENAME TO source_published_events_insert_shape;
ALTER TRIGGER source_published_inbox_insert_shape ON provisioning_ops.source_published_events RENAME TO source_published_events_insert_shape;

ALTER FUNCTION provisioning_ops.source_published_inbox_update_shape() RENAME TO source_published_events_update_shape;
ALTER TRIGGER source_published_inbox_update_shape ON provisioning_ops.source_published_events RENAME TO source_published_events_update_shape;

------------------------------------------------------------------------------------------------------
-- Guard function bodies, re-created with the new names. The behaviour is the one of the latest
-- version of each body; CREATE OR REPLACE keeps the owner, the privileges (no EXECUTE for PUBLIC)
-- and the trigger, and each REVOKE is repeated so that the file states the hygiene on its own
-- (decision 0004 section 9).
------------------------------------------------------------------------------------------------------

-- releases: as in migration 00010.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.releases_identity_immutable() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.generation_request_id IS DISTINCT FROM OLD.generation_request_id
       OR NEW.github_repository_id IS DISTINCT FROM OLD.github_repository_id
       OR NEW.git_commit_sha IS DISTINCT FROM OLD.git_commit_sha
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.container_image_digest IS NOT NULL AND NEW.container_image_digest IS DISTINCT FROM OLD.container_image_digest)
       OR (OLD.promoted_at IS NOT NULL AND NEW.promoted_at IS DISTINCT FROM OLD.promoted_at) THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_identity_immutable',
                  DETAIL = 'release_id, project_id, generation_request_id, github_repository_id, git_commit_sha and created_at never change; container_image_digest and promoted_at change only from null to a value.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_identity_immutable() FROM PUBLIC;

-- releases: as in migration 00008.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.releases_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean := false;
BEGIN
    IF NEW.status <> 'accepted'::provisioning.release_status
       OR NEW.container_image_digest IS NOT NULL OR NEW.failure_code IS NOT NULL OR NEW.failure_summary IS NOT NULL
       OR NEW.promoted_at IS NOT NULL
       OR NEW.updated_at <> NEW.created_at
       OR NEW.created_at < pg_catalog.transaction_timestamp()
       OR NEW.created_at > pg_catalog.clock_timestamp() THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL THEN
        refused := NOT EXISTS (
            SELECT 1
              FROM provisioning.project_hosting AS s
             WHERE s.project_id = NEW.project_id
        );
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "releases" violates guard "releases_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_insert_shape',
                  DETAIL = 'A release is inserted as accepted, without container_image_digest, failure_code, failure_summary or promoted_at, with created_at equal to updated_at inside the inserting transaction''s time window, for a project whose state row exists.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_insert_shape() FROM PUBLIC;

-- releases: as in migration 00008.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.releases_build_transition() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    allowed boolean;
BEGIN
    IF NOT pg_catalog.pg_has_role(current_user, 'provisioning_build', 'MEMBER')
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS r WHERE r.rolname = current_user AND r.rolsuper)
       OR NEW.status IS NULL THEN
        RETURN NEW;
    END IF;
    allowed := (OLD.status = 'accepted'::provisioning.release_status
                AND NEW.status = 'building'::provisioning.release_status)
            OR (OLD.status = 'building'::provisioning.release_status
                AND NEW.status = 'built'::provisioning.release_status AND NEW.container_image_digest IS NOT NULL)
            OR (OLD.status = 'building'::provisioning.release_status
                AND NEW.status = 'failed'::provisioning.release_status AND NEW.failure_code IS NOT NULL)
            OR (OLD.status IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status)
                AND NEW.status = OLD.status
                AND NEW.container_image_digest IS NOT DISTINCT FROM OLD.container_image_digest
                AND NEW.failure_code IS NOT DISTINCT FROM OLD.failure_code
                AND NEW.failure_summary IS NOT DISTINCT FROM OLD.failure_summary);
    IF allowed IS NOT TRUE THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_build_transition"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_build_transition',
                  DETAIL = 'A member of provisioning_build may move a release only from accepted to building, from building to built with a digest, or from building to failed with a failure code.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_build_transition() FROM PUBLIC;

-- commands: as in migration 00002.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.commands_identity_immutable() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.command_id IS DISTINCT FROM OLD.command_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_fingerprint IS DISTINCT FROM OLD.request_fingerprint
       OR NEW.command_number IS DISTINCT FROM OLD.command_number
       OR NEW.requested_by_subject IS DISTINCT FROM OLD.requested_by_subject
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'update of relation "commands" violates guard "commands_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_identity_immutable',
                  DETAIL = 'command_id, project_id, release_id, kind, idempotency_key, request_fingerprint, command_number, requested_by_subject and created_at never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_identity_immutable() FROM PUBLIC;

-- commands: as in migration 00010, with the hosting-intake role of decision 0012 section 4. A
-- suspend_hosting or resume_hosting command is inserted only in a session that is a member of
-- provisioning_hosting_intake (pg_has_role ... 'MEMBER'; a superuser is a member of every role in
-- that sense). Membership of provisioning_payment_intake grants nothing any more.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.commands_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    counter bigint;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'accepted'::provisioning.command_status OR NEW.failure_code IS NOT NULL THEN
        refused := true;
    ELSIF NEW.kind IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind)
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_hosting_intake', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL AND NEW.command_number IS NOT NULL THEN
        SELECT s.last_command_number
          INTO counter
          FROM provisioning.project_hosting AS s
         WHERE s.project_id = NEW.project_id;
        refused := counter IS DISTINCT FROM NEW.command_number;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "commands" violates guard "commands_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_insert_shape',
                  DETAIL = 'A command is inserted as accepted, without a failure code, and with command_number equal to the current last_command_number of its project; a hosting command only by a member of provisioning_hosting_intake.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_insert_shape() FROM PUBLIC;

-- project_hosting: as in migration 00002.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.project_hosting_generation_step() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean := false;
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id THEN
        refused := true;
    ELSIF NEW.last_command_number < OLD.last_command_number THEN
        refused := true;
    ELSIF NEW.last_command_number > OLD.last_command_number THEN
        refused := NEW.last_command_number - OLD.last_command_number > 1;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "project_hosting" violates guard "project_hosting_generation_step"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_generation_step',
                  DETAIL = 'project_id never changes; last_command_number stays the same or increases by exactly one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_generation_step() FROM PUBLIC;

-- project_hosting: as in migration 00002 (the constraint trigger deferred to commit).
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.project_hosting_generation_has_command() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM provisioning.commands AS c
         WHERE c.project_id = NEW.project_id
           AND c.command_number = NEW.last_command_number
    ) THEN
        RAISE EXCEPTION 'update of relation "project_hosting" violates guard "project_hosting_generation_has_command"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_generation_has_command',
                  DETAIL = 'last_command_number changed, and no command of the project carries the new value as its command_number.';
    END IF;
    RETURN NULL;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_generation_has_command() FROM PUBLIC;

-- project_hosting: as in migration 00006, with the value online.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.project_hosting_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.last_command_number <> 0
       OR NEW.routing_status <> 'stable'::provisioning.routing_status
       OR NEW.hosting_status <> 'online'::provisioning.hosting_status
       OR NEW.live_release_id IS NOT NULL THEN
        RAISE EXCEPTION 'insert into relation "project_hosting" violates guard "project_hosting_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_insert_shape',
                  DETAIL = 'A project hosting row is inserted with last_command_number 0, routing_status stable, hosting_status online and no live release.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_insert_shape() FROM PUBLIC;

-- release_steps: as in migration 00010.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.release_steps_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    release_status  provisioning.release_status;
    previous_status provisioning.release_step_status;
    worker          boolean := pg_catalog.pg_has_role(current_user, 'provisioning_worker', 'MEMBER');
    administrator   boolean := EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS r WHERE r.rolname = current_user AND r.rolsuper);
    refused boolean := false;
BEGIN
    IF NEW.status <> 'pending'::provisioning.release_step_status
       OR NEW.started_at IS NOT NULL OR NEW.finished_at IS NOT NULL OR NEW.deadline_at IS NOT NULL
       OR NEW.failure_code IS NOT NULL OR NEW.log_ref IS NOT NULL
       OR NEW.step_name NOT IN ('build'::provisioning.release_step_name, 'candidate'::provisioning.release_step_name)
       OR (NEW.step_name = 'candidate'::provisioning.release_step_name AND NOT worker)
       OR (NEW.step_name = 'build'::provisioning.release_step_name AND worker AND NOT administrator)
       OR (NEW.step_name = 'build'::provisioning.release_step_name AND NEW.attempt > 10)
       OR (NEW.step_name = 'candidate'::provisioning.release_step_name AND NEW.attempt > 10) THEN
        refused := true;
    ELSIF NEW.release_id IS NOT NULL THEN
        SELECT r.status
          INTO release_status
          FROM provisioning.releases AS r
         WHERE r.release_id = NEW.release_id;
        IF FOUND THEN
            IF NEW.step_name = 'build'::provisioning.release_step_name
               AND release_status NOT IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status) THEN
                refused := true;
            ELSIF NEW.step_name = 'candidate'::provisioning.release_step_name
                  AND release_status <> 'built'::provisioning.release_status THEN
                refused := true;
            ELSIF NEW.attempt > 1 THEN
                SELECT s.status
                  INTO previous_status
                  FROM provisioning.release_steps AS s
                 WHERE s.release_id = NEW.release_id
                   AND s.step_name = NEW.step_name
                   AND s.attempt = NEW.attempt - 1;
                IF NEW.step_name = 'build'::provisioning.release_step_name THEN
                    refused := previous_status IS DISTINCT FROM 'failed'::provisioning.release_step_status;
                ELSE
                    refused := previous_status IS NULL
                        OR previous_status NOT IN ('failed'::provisioning.release_step_status,
                                                   'succeeded'::provisioning.release_step_status);
                END IF;
            END IF;
        END IF;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "release_steps" violates guard "release_steps_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'release_steps',
                  CONSTRAINT = 'release_steps_insert_shape',
                  DETAIL = 'A step is inserted as pending without times, failure code or log reference: a build step, not by a member of provisioning_worker, numbered at most 10 on an accepted or building release, as attempt 1 or after a failed one; or a candidate step, only by a member of provisioning_worker, numbered at most 10 on a built release, as attempt 1 or after a failed or succeeded one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_insert_shape() FROM PUBLIC;

-- release_steps: as in migration 00010.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.release_steps_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    worker        boolean := pg_catalog.pg_has_role(current_user, 'provisioning_worker', 'MEMBER');
    administrator boolean := EXISTS (SELECT 1 FROM pg_catalog.pg_roles AS r WHERE r.rolname = current_user AND r.rolsuper);
    refused boolean;
BEGIN
    refused := (OLD.step_name = 'candidate'::provisioning.release_step_name AND NOT worker)
       OR (OLD.step_name = 'build'::provisioning.release_step_name AND worker AND NOT administrator)
       OR NEW.release_step_id IS DISTINCT FROM OLD.release_step_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.step_name IS DISTINCT FROM OLD.step_name
       OR NEW.attempt IS DISTINCT FROM OLD.attempt
       OR (NEW.status IS DISTINCT FROM OLD.status AND NOT (
              (OLD.status = 'pending'::provisioning.release_step_status
               AND NEW.status IN ('running'::provisioning.release_step_status, 'failed'::provisioning.release_step_status))
           OR (OLD.status = 'running'::provisioning.release_step_status
               AND NEW.status IN ('succeeded'::provisioning.release_step_status, 'failed'::provisioning.release_step_status))))
       OR (OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at)
       OR (OLD.deadline_at IS NOT NULL AND NEW.deadline_at IS DISTINCT FROM OLD.deadline_at)
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
       OR (OLD.failure_code IS NOT NULL AND NEW.failure_code IS DISTINCT FROM OLD.failure_code)
       OR (OLD.log_ref IS NOT NULL AND NEW.log_ref IS DISTINCT FROM OLD.log_ref);
    IF refused IS NOT TRUE
       AND OLD.status = 'pending'::provisioning.release_step_status
       AND NEW.status = 'running'::provisioning.release_step_status THEN
        IF OLD.step_name = 'candidate'::provisioning.release_step_name THEN
            refused := NOT EXISTS (
                SELECT 1
                  FROM provisioning.releases AS r
                 WHERE r.release_id = OLD.release_id
                   AND r.status = 'built'::provisioning.release_status
            );
        ELSE
            refused := NOT EXISTS (
                SELECT 1
                  FROM provisioning.releases AS r
                 WHERE r.release_id = OLD.release_id
                   AND r.status IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status)
            );
        END IF;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "release_steps" violates guard "release_steps_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'release_steps',
                  CONSTRAINT = 'release_steps_update_shape',
                  DETAIL = 'A candidate step is updated only by a member of provisioning_worker, a build step not by one; release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed, and a finished step is final; a candidate step moves to running only while its release is built, any other step only while its release is accepted or building; started_at, deadline_at, finished_at, failure_code and log_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;

-- command_dispatch: as in migration 00004.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.command_dispatch_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    command_status provisioning.command_status;
    refused boolean := false;
BEGIN
    IF NEW.publish_attempts <> 0 OR NEW.execution_attempts <> 0 OR NEW.claim_number <> 0
       OR NEW.claimed_by_worker IS NOT NULL OR NEW.claim_expires_at IS NOT NULL
       OR NEW.last_broker_ack_at IS NOT NULL OR NEW.retry_after IS NOT NULL
       OR NEW.last_failure_code IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
        refused := true;
    ELSIF NEW.command_id IS NOT NULL THEN
        SELECT c.status
          INTO command_status
          FROM provisioning.commands AS c
         WHERE c.command_id = NEW.command_id;
        refused := FOUND AND command_status <> 'accepted'::provisioning.command_status;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "command_dispatch" violates guard "command_dispatch_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'command_dispatch',
                  CONSTRAINT = 'command_dispatch_insert_shape',
                  DETAIL = 'A dispatch row is inserted with claim_number and counters at zero, without a claim, broker acknowledgement, retry time, failure code or finish, for an accepted command.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.command_dispatch_insert_shape() FROM PUBLIC;

-- command_dispatch: as in migration 00005.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.command_dispatch_monotonic() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.claim_number < OLD.claim_number
       OR NEW.execution_attempts < OLD.execution_attempts
       OR NEW.publish_attempts < OLD.publish_attempts
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
        RAISE EXCEPTION 'update of relation "command_dispatch" violates guard "command_dispatch_monotonic"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'command_dispatch',
                  CONSTRAINT = 'command_dispatch_monotonic',
                  DETAIL = 'claim_number, execution_attempts and publish_attempts never decrease; finished_at, once set, never changes.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.command_dispatch_monotonic() FROM PUBLIC;

-- source_published_events: as in migration 00010.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.source_published_events_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.status <> 'pending'::provisioning_ops.source_event_status
       OR NEW.attempts <> 0 OR NEW.mismatched_repeat_count <> 0 OR NEW.last_mismatched_repeat_at IS NOT NULL
       OR NEW.outcome_code IS NOT NULL OR NEW.release_id IS NOT NULL OR NEW.finished_at IS NOT NULL
       OR NEW.last_manual_retry_by IS NOT NULL OR NEW.last_manual_retry_at IS NOT NULL THEN
        RAISE EXCEPTION 'insert into relation "source_published_events" violates guard "source_published_events_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_events',
                  CONSTRAINT = 'source_published_events_insert_shape',
                  DETAIL = 'An event row is inserted as pending, with no attempt and no mismatched repeat, and without an outcome, a release, a finish or a manual retry.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_events_insert_shape() FROM PUBLIC;

-- source_published_events: as in migration 00010.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.source_published_events_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean;
BEGIN
    refused := NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.generation_request_id IS DISTINCT FROM OLD.generation_request_id
       OR NEW.github_repository_id IS DISTINCT FROM OLD.github_repository_id
       OR NEW.git_commit_sha IS DISTINCT FROM OLD.git_commit_sha
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.attempts < OLD.attempts
       OR NEW.mismatched_repeat_count < OLD.mismatched_repeat_count
       OR (OLD.release_id IS NOT NULL AND NEW.release_id IS DISTINCT FROM OLD.release_id);
    IF OLD.status = 'failed'::provisioning_ops.source_event_status
       AND NEW.status = 'pending'::provisioning_ops.source_event_status THEN
        refused := refused
           OR NOT pg_catalog.pg_has_role(current_user, 'provisioning_operator', 'MEMBER')
           OR NEW.outcome_code IS NOT NULL
           OR NEW.finished_at IS NOT NULL
           OR NEW.max_attempts <= OLD.max_attempts
           OR NEW.last_manual_retry_by IS NULL
           OR NEW.last_manual_retry_at IS NULL
           OR NEW.last_manual_retry_at IS NOT DISTINCT FROM OLD.last_manual_retry_at;
    ELSE
        refused := refused
           OR (OLD.status <> 'pending'::provisioning_ops.source_event_status AND NEW.status IS DISTINCT FROM OLD.status)
           OR (OLD.outcome_code IS NOT NULL AND NEW.outcome_code IS DISTINCT FROM OLD.outcome_code)
           OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
           OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
           OR NEW.last_manual_retry_by IS DISTINCT FROM OLD.last_manual_retry_by
           OR NEW.last_manual_retry_at IS DISTINCT FROM OLD.last_manual_retry_at;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "source_published_events" violates guard "source_published_events_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_events',
                  CONSTRAINT = 'source_published_events_update_shape',
                  DETAIL = 'The event columns never change; attempts and mismatched_repeat_count never decrease; the status moves from pending to accepted, rejected or failed only, or from failed to pending in a manual retry by a member of provisioning_operator that clears the outcome and the finish, raises max_attempts and records who and when; otherwise outcome_code, release_id and finished_at, once set, never change, and max_attempts and the manual retry columns do not change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_events_update_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- Final check: no guard function in either schema may still contain an old name of decision 0012
-- section 3 (word-boundary match, so a new name that contains an old one as a part, such as
-- github_repository_id or requested_by_subject, does not count). It also reports the old names of the
-- trigger functions, which the renames above must have replaced. A failure here rolls back the file.
------------------------------------------------------------------------------------------------------
-- +goose StatementBegin
DO $$
DECLARE
    stale text;
BEGIN
    SELECT string_agg(DISTINCT n.nspname || '.' || p.proname, ', ' ORDER BY n.nspname || '.' || p.proname)
      INTO stale
      FROM pg_catalog.pg_proc AS p
      JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname IN ('provisioning', 'provisioning_ops')
       AND (p.prosrc ~ ('\m(project_release_state|active_release_id|operation_generation|route_state|hosting_state|serving'
                        '|source_generation_request_id|source_repository_id|source_commit_sha|image_digest'
                        '|fencing_generation|request_hash|requested_by|reason_code|details_ref'
                        '|command_work|lease_token|claimed_by|lease_expires_at|claim_count|next_dispatch_at'
                        '|last_confirmed_at|retry_not_before|last_error_code'
                        '|source_published_inbox|inbox_status|repository_id|commit_sha|conflict_count'
                        '|last_conflict_at|attempt_budget|last_redriven_by|last_redriven_at'
                        '|provisioning_payment_intake)\M')
            OR p.proname ~ '(project_release_state|command_work|source_published_inbox)');
    IF stale IS NOT NULL THEN
        RAISE EXCEPTION 'migration 00011: function(s) still carry an old name: %', stale
            USING ERRCODE = '55000';
    END IF;
END
$$;
-- +goose StatementEnd
