-- +goose Up
-- Decision 0014 section 16 (owner-approved, 9 October 2026), items 1, 2, 4 and 5. Nothing else
-- changes: no table, column or other enum value is added, removed or renamed. The enum value
-- 'sync_domains' itself is added by 00014.
--
--   1. sync_domains command: it names no release and has no requester (like the hosting kinds), has
--      its own idempotency key class (commands_domain_event_key), and is inserted only by a member
--      of provisioning_intake, which receives the command-acceptance privileges that
--      provisioning_api and (00013) provisioning_hosting_intake hold.
--   2. the guard gap: commands_insert_shape refuses deploy, promote and rollback unless the session
--      is a member of provisioning_api (task B3 found that any role could insert them).
--   4. release_steps_insert_shape and release_steps_update_shape admit promotion and rollback steps,
--      written only by a member of provisioning_worker.
--   5. a read-only role provisioning_bootstrap, with SELECT on exactly the columns the bootstrap
--      component reads: releases (project_id, status) and project_hosting (project_id,
--      live_release_id).
--
-- Goose runs the file in one transaction; any failure changes nothing. Forward-only: no Down section
-- (decision 0014 section 17 item 4).

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

-- Fail before any change, with one message that names every missing role. They are created by
-- scripts/db/bootstrap-roles.sql; the guards below test membership in some of them.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api', 'provisioning_intake', 'provisioning_hosting_intake',
                        'provisioning_worker', 'provisioning_bootstrap']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- Item 1: the sync_domains command (checks and index).
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.commands DROP CONSTRAINT commands_release_by_kind;
ALTER TABLE provisioning.commands ADD CONSTRAINT commands_release_by_kind
    CHECK ((release_id IS NULL) = (kind IN ('suspend_hosting', 'resume_hosting', 'sync_domains')));

ALTER TABLE provisioning.commands DROP CONSTRAINT commands_requested_by_subject_by_kind;
ALTER TABLE provisioning.commands ADD CONSTRAINT commands_requested_by_subject_by_kind
    CHECK (((requested_by_subject IS NULL) = (kind IN ('suspend_hosting', 'resume_hosting', 'sync_domains')))
           AND (requested_by_subject IS NULL OR requested_by_subject <> ''));

-- The Project event key, unique per project in its own class, so a client's key and a hosting or
-- domain event key never meet (the argument of 00002 for commands_hosting_event_key).
CREATE UNIQUE INDEX commands_domain_event_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind = 'sync_domains';

------------------------------------------------------------------------------------------------------
-- Items 1 and 2: who inserts which kind. As in 00011, with the two new rules.
------------------------------------------------------------------------------------------------------

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
    ELSIF NEW.kind = 'sync_domains'::provisioning.command_kind
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_intake', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.kind IN ('deploy'::provisioning.command_kind, 'promote'::provisioning.command_kind,
                       'rollback'::provisioning.command_kind)
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_api', 'MEMBER') THEN
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
                  DETAIL = 'A command is inserted as accepted, without a failure code, and with command_number equal to the current last_command_number of its project; a hosting command only by a member of provisioning_hosting_intake, a sync_domains command only by a member of provisioning_intake, a deploy, promote or rollback only by a member of provisioning_api.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_insert_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- Item 1: provisioning_intake accepts commands. The list is the one 00013 gave
-- provisioning_hosting_intake, which is the acceptance list of provisioning_api.
------------------------------------------------------------------------------------------------------

GRANT USAGE ON SCHEMA provisioning, provisioning_ops TO provisioning_intake;
GRANT SELECT ON
    provisioning.releases,
    provisioning.project_hosting,
    provisioning.release_steps,
    provisioning.commands
TO provisioning_intake;
GRANT INSERT ON provisioning.commands TO provisioning_intake;
GRANT UPDATE (last_command_number, updated_at) ON provisioning.project_hosting TO provisioning_intake;
GRANT INSERT ON provisioning_ops.command_dispatch TO provisioning_intake;

------------------------------------------------------------------------------------------------------
-- Item 4: promotion and rollback steps. As in 00011 (release_steps_insert_shape and
-- release_steps_update_shape), with promotion and rollback treated like candidate: only a member
-- of provisioning_worker inserts or updates them, numbered at most 10, as attempt 1 or after a
-- failed or succeeded one. They are history and diagnostics, so no release status is required
-- (the command row and the release status stay the authority); access_setup and observation steps
-- are still refused.
------------------------------------------------------------------------------------------------------

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
    worker_step     boolean;
    refused boolean := false;
BEGIN
    worker_step := NEW.step_name IN ('candidate'::provisioning.release_step_name,
                                     'promotion'::provisioning.release_step_name,
                                     'rollback'::provisioning.release_step_name);
    IF NEW.status <> 'pending'::provisioning.release_step_status
       OR NEW.started_at IS NOT NULL OR NEW.finished_at IS NOT NULL OR NEW.deadline_at IS NOT NULL
       OR NEW.failure_code IS NOT NULL OR NEW.log_ref IS NOT NULL
       OR NEW.step_name NOT IN ('build'::provisioning.release_step_name, 'candidate'::provisioning.release_step_name,
                                'promotion'::provisioning.release_step_name, 'rollback'::provisioning.release_step_name)
       OR (worker_step AND NOT worker)
       OR (NEW.step_name = 'build'::provisioning.release_step_name AND worker AND NOT administrator)
       OR NEW.attempt > 10 THEN
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
                  DETAIL = 'A step is inserted as pending without times, failure code or log reference: a build step, not by a member of provisioning_worker, numbered at most 10 on an accepted or building release, as attempt 1 or after a failed one; a candidate step, only by a member of provisioning_worker, numbered at most 10 on a built release, as attempt 1 or after a failed or succeeded one; or a promotion or rollback step, only by a member of provisioning_worker, numbered at most 10, as attempt 1 or after a failed or succeeded one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_insert_shape() FROM PUBLIC;

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
    refused := (OLD.step_name IN ('candidate'::provisioning.release_step_name,
                                  'promotion'::provisioning.release_step_name,
                                  'rollback'::provisioning.release_step_name) AND NOT worker)
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
        ELSIF OLD.step_name = 'build'::provisioning.release_step_name THEN
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
                  DETAIL = 'A candidate, promotion or rollback step is updated only by a member of provisioning_worker, a build step not by one; release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed, and a finished step is final; a candidate step moves to running only while its release is built, a build step only while its release is accepted or building; started_at, deadline_at, finished_at, failure_code and log_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- Item 5: the read-only bootstrap role. The bootstrap component lists the projects with a release on
-- its way to a deploy (releases.project_id, releases.status) and asks whether a project has a live
-- release (project_hosting.project_id, project_hosting.live_release_id). It writes nothing.
------------------------------------------------------------------------------------------------------

GRANT USAGE ON SCHEMA provisioning TO provisioning_bootstrap;
GRANT SELECT (project_id, status) ON provisioning.releases TO provisioning_bootstrap;
GRANT SELECT (project_id, live_release_id) ON provisioning.project_hosting TO provisioning_bootstrap;
