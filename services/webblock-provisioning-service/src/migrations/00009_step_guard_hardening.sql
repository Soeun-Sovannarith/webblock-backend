-- +goose Up
-- Hardening of the two release_steps guards of migration 00008 (validation finding V3-03, decision
-- 0008 section 13). Both limit what a compromised build login can do; neither changes a legal path.
--
--   release_steps_insert_shape: a step's attempt number is at most 10, the number in
--       domain.MaxBuildAttempts (the upper bound of BUILD_MAX_ATTEMPTS, decision 0008 section 4).
--       provisioning_build may fail its own attempts, so without the bound it could insert attempts
--       without end and pass the 100-step read limit of decision 0004 section 6. A test compares the
--       two numbers (tests/integration/step_guard_hardening_test.go).
--   release_steps_update_shape: a step moves pending -> running only while its release is accepted or
--       building, so a step of a release that failed, or that left its build, is never started.
--
-- Every other rule of both functions is as in 00008. CREATE OR REPLACE keeps each function's owner,
-- its privileges (no EXECUTE for PUBLIC) and its trigger; the REVOKE is repeated so that the file
-- states the hygiene on its own. Rows stored before this migration are not checked again: an attempt
-- beyond 10 written at version 8 stays, and gets no successor.
--
-- Forward-only and immutable, like every migration: fix a mistake with a new one.

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

-- release_steps: a step is inserted as pending, without times, a reason or a reference; in Phase 3
-- only build steps, numbered 1 to 10, on a release that is accepted or building; as attempt 1, or as
-- the attempt after one that failed. Role-independent. The reads need SELECT on releases and, for a
-- later attempt, on release_steps: provisioning_build holds both; provisioning_intake inserts only
-- the first attempt and holds SELECT on releases only, so a later attempt from it is refused for lack
-- of privilege. The bound is a literal on purpose: a guard reads no configuration, and 10 is the
-- largest BUILD_MAX_ATTEMPTS that the configuration accepts.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.release_steps_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    release_status  provisioning.release_status;
    previous_status provisioning.release_step_status;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'pending'::provisioning.release_step_status
       OR NEW.started_at IS NOT NULL OR NEW.finished_at IS NOT NULL OR NEW.deadline_at IS NOT NULL
       OR NEW.reason_code IS NOT NULL OR NEW.details_ref IS NOT NULL
       OR NEW.step_name <> 'build'::provisioning.release_step_name
       OR NEW.attempt > 10 THEN
        refused := true;
    ELSIF NEW.release_id IS NOT NULL THEN
        SELECT r.status
          INTO release_status
          FROM provisioning.releases AS r
         WHERE r.release_id = NEW.release_id;
        IF FOUND THEN
            IF release_status NOT IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status) THEN
                refused := true;
            ELSIF NEW.attempt > 1 THEN
                SELECT s.status
                  INTO previous_status
                  FROM provisioning.release_steps AS s
                 WHERE s.release_id = NEW.release_id
                   AND s.step_name = NEW.step_name
                   AND s.attempt = NEW.attempt - 1;
                refused := previous_status IS DISTINCT FROM 'failed'::provisioning.release_step_status;
            END IF;
        END IF;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "release_steps" violates guard "release_steps_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'release_steps',
                  CONSTRAINT = 'release_steps_insert_shape',
                  DETAIL = 'A step is inserted as a pending build step without times, reason or reference, numbered at most 10, on an accepted or building release, as attempt 1 or as the attempt after a failed one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_insert_shape() FROM PUBLIC;

-- release_steps: on every update, for every role including the owner:
--
--   the identity (release_step_id, release_id, step_name, attempt) never changes;
--   the status moves pending -> running -> succeeded | failed, or pending -> failed, and a
--   succeeded or failed step is final;
--   pending -> running only while the step's release is accepted or building;
--   started_at, deadline_at, finished_at, reason_code and details_ref, once set, never change.
--
-- The release is read only for a start, and without a lock: the build paths lock the release before
-- the step (decision 0008 sections 4 and 7), so for them the status read is the locked one, and a
-- lock taken here would reverse that order. The read needs SELECT on releases, which every role that
-- may update a step holds (provisioning_build and the owner; a test pins it). The release is named by
-- OLD.release_id, which the identity rule above keeps equal to NEW.release_id.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.release_steps_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean;
BEGIN
    refused := NEW.release_step_id IS DISTINCT FROM OLD.release_step_id
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
       OR (OLD.reason_code IS NOT NULL AND NEW.reason_code IS DISTINCT FROM OLD.reason_code)
       OR (OLD.details_ref IS NOT NULL AND NEW.details_ref IS DISTINCT FROM OLD.details_ref);
    IF refused IS NOT TRUE
       AND OLD.status = 'pending'::provisioning.release_step_status
       AND NEW.status = 'running'::provisioning.release_step_status THEN
        refused := NOT EXISTS (
            SELECT 1
              FROM provisioning.releases AS r
             WHERE r.release_id = OLD.release_id
               AND r.status IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status)
        );
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "release_steps" violates guard "release_steps_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'release_steps',
                  CONSTRAINT = 'release_steps_update_shape',
                  DETAIL = 'release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed, and a finished step is final; it moves to running only while its release is accepted or building; started_at, deadline_at, finished_at, reason_code and details_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;
