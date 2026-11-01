-- +goose Up
-- Revision 17 items B, C and E and the candidate step rows (decision 0011 sections 6 and 12):
--
--   B  operator re-drive of an inbox event whose retry budget ran out: the inbox gains
--      attempt_budget, last_redriven_by and last_redriven_at; the new NOLOGIN role
--      provisioning_operator may move a failed row back to pending, and nobody else may;
--   C  only a member of the new NOLOGIN role provisioning_payment_intake (no members yet) may insert
--      a suspend_hosting or resume_hosting command;
--   E  a terminal release keeps its failure_code and failure_summary and a terminal command its
--      failure_code; promoted_at is write-once; source_repository_id, operation_generation and
--      fencing_generation are at most 2^53-1;
--   candidate steps: provisioning_worker may insert release steps and move them; a candidate step is
--      inserted only for a built release, as attempt 1 or after a failed or succeeded attempt, at
--      most 10 in all (domain.MaxCandidateAttempts), and starts only while its release is built.
--      Step names belong to roles (decision 0011 section 12, rulings on task D1's report): only a
--      member of provisioning_worker inserts or updates a candidate step, and a member of
--      provisioning_worker inserts or updates no build step.
--
-- Item D of revision 17 (a hosting event for a project that was never deployed) is a Phase 6 rule
-- and is not here. Guard functions whose triggers already exist are replaced with CREATE OR REPLACE,
-- which keeps their owner, their privileges and their trigger; each REVOKE is repeated so that the
-- file states the hygiene on its own (decision 0004 section 9).
--
-- Upgrade notes. Rows stored before this migration are not checked again by the replaced guards. The
-- three bound checks are validated against existing rows: a row above 2^53-1, which only an
-- administrator could have written (Go refuses it), makes the migration fail and change nothing.
-- attempt_budget of an existing inbox row is set to its attempts, at least 1: see the backfill below.
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

-- Fail before any change, with one message that names every missing role. Both are created by
-- scripts/db/bootstrap-roles.sql; the guards below test membership in them.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_operator', 'provisioning_payment_intake']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- B. Re-drive of an inbox event.
------------------------------------------------------------------------------------------------------

-- attempt_budget: the most processing attempts of the row. The intake writes the processor's
-- configured budget into a new row; only a re-drive raises it; attempts still never decreases.
-- last_redriven_by: the token subject of the operator who last re-drove the row; last_redriven_at:
-- when. Both are set together, by a re-drive only.
ALTER TABLE provisioning_ops.source_published_inbox
    ADD COLUMN attempt_budget   integer,
    ADD COLUMN last_redriven_by varchar(255),
    ADD COLUMN last_redriven_at timestamptz;

-- Backfill. No configured budget is in force anywhere: no configuration key names the processor's
-- budget and no binary runs the processor (decision 0008 section 1), so the migration cannot read
-- one. Each existing row gets the attempts it has already used, at least 1:
--   * a failed row's budget is the one it spent, so a re-drive, which must raise it, gives it
--     attempts beyond those it used;
--   * a pending row gets no attempt beyond those already taken until an operator raises it, which is
--     the fail-closed choice; while the processor's own check still uses its configured maximum
--     (src/core/application, unchanged here), its processing does not change;
--   * accepted and rejected rows are final, and the value only records what they used.
-- The update fires the update guard of migration 00007, which leaves the new column free; nothing
-- else of a row changes, updated_at included.
UPDATE provisioning_ops.source_published_inbox
   SET attempt_budget = GREATEST(attempts, 1);

ALTER TABLE provisioning_ops.source_published_inbox
    ALTER COLUMN attempt_budget SET NOT NULL,
    ADD CONSTRAINT source_published_inbox_attempt_budget_check CHECK (attempt_budget >= 1),
    -- Who and when are recorded together, and the subject is not empty.
    ADD CONSTRAINT source_published_inbox_redriven_together
        CHECK ((last_redriven_by IS NULL) = (last_redriven_at IS NULL)),
    ADD CONSTRAINT source_published_inbox_redriven_by_check CHECK (last_redriven_by <> '');

-- Insert guard: as in migration 00007, and a new row has not been re-driven. attempt_budget is left
-- to its NOT NULL and check constraints: the intake writes the configured budget.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.source_published_inbox_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.status <> 'pending'::provisioning_ops.inbox_status
       OR NEW.attempts <> 0 OR NEW.conflict_count <> 0 OR NEW.last_conflict_at IS NOT NULL
       OR NEW.outcome_code IS NOT NULL OR NEW.release_id IS NOT NULL OR NEW.finished_at IS NOT NULL
       OR NEW.last_redriven_by IS NOT NULL OR NEW.last_redriven_at IS NOT NULL THEN
        RAISE EXCEPTION 'insert into relation "source_published_inbox" violates guard "source_published_inbox_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_inbox',
                  CONSTRAINT = 'source_published_inbox_insert_shape',
                  DETAIL = 'An inbox row is inserted as pending, with no attempt and no conflict, and without an outcome, a release, a finish or a re-drive.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_inbox_insert_shape() FROM PUBLIC;

-- Update guard, for every role including the owner:
--
--   the event (project_id, generation_request_id, repository_id, commit_sha, received_at) never changes;
--   attempts and conflict_count never decrease; release_id, once set, never changes;
--   the re-drive: failed -> pending, only in a session that is a member of provisioning_operator
--     (pg_has_role ... 'MEMBER', so a login attached by membership; a superuser is a member of every
--     role in that sense). It clears outcome_code and finished_at, raises attempt_budget, and sets
--     last_redriven_by and last_redriven_at, the time a new one;
--   every other update: the status moves only from pending to accepted, rejected or failed, a
--     terminal status is final, outcome_code and finished_at once set never change, and
--     attempt_budget, last_redriven_by and last_redriven_at do not change.
--
-- So rejected and accepted stay final, a failed row is reopened only by an operator and only with a
-- larger budget, and the processor cannot raise its own budget or forge a re-drive. It reads no
-- table. A null in a NOT NULL column is left to that column's own constraint.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.source_published_inbox_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean;
BEGIN
    refused := NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.generation_request_id IS DISTINCT FROM OLD.generation_request_id
       OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
       OR NEW.commit_sha IS DISTINCT FROM OLD.commit_sha
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.attempts < OLD.attempts
       OR NEW.conflict_count < OLD.conflict_count
       OR (OLD.release_id IS NOT NULL AND NEW.release_id IS DISTINCT FROM OLD.release_id);
    IF OLD.status = 'failed'::provisioning_ops.inbox_status
       AND NEW.status = 'pending'::provisioning_ops.inbox_status THEN
        refused := refused
           OR NOT pg_catalog.pg_has_role(current_user, 'provisioning_operator', 'MEMBER')
           OR NEW.outcome_code IS NOT NULL
           OR NEW.finished_at IS NOT NULL
           OR NEW.attempt_budget <= OLD.attempt_budget
           OR NEW.last_redriven_by IS NULL
           OR NEW.last_redriven_at IS NULL
           OR NEW.last_redriven_at IS NOT DISTINCT FROM OLD.last_redriven_at;
    ELSE
        refused := refused
           OR (OLD.status <> 'pending'::provisioning_ops.inbox_status AND NEW.status IS DISTINCT FROM OLD.status)
           OR (OLD.outcome_code IS NOT NULL AND NEW.outcome_code IS DISTINCT FROM OLD.outcome_code)
           OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at)
           OR NEW.attempt_budget IS DISTINCT FROM OLD.attempt_budget
           OR NEW.last_redriven_by IS DISTINCT FROM OLD.last_redriven_by
           OR NEW.last_redriven_at IS DISTINCT FROM OLD.last_redriven_at;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "source_published_inbox" violates guard "source_published_inbox_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_inbox',
                  CONSTRAINT = 'source_published_inbox_update_shape',
                  DETAIL = 'The event columns never change; attempts and conflict_count never decrease; the status moves from pending to accepted, rejected or failed only, or from failed to pending in a re-drive by a member of provisioning_operator that clears the outcome and the finish, raises attempt_budget and records who and when; otherwise outcome_code, release_id and finished_at, once set, never change, and attempt_budget and the re-drive columns do not change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_inbox_update_shape() FROM PUBLIC;

-- provisioning_operator: find a row and re-drive it. SELECT serves the WHERE clause and the read
-- back; the UPDATE columns are those a re-drive writes (next_attempt_at makes the row due again).
-- Nothing else: no insert, no other table, no event column.
GRANT USAGE ON SCHEMA provisioning_ops TO provisioning_operator;
GRANT SELECT ON provisioning_ops.source_published_inbox TO provisioning_operator;
GRANT UPDATE (status, outcome_code, finished_at, attempt_budget, next_attempt_at, last_redriven_by,
              last_redriven_at, updated_at)
    ON provisioning_ops.source_published_inbox TO provisioning_operator;

------------------------------------------------------------------------------------------------------
-- C. Hosting commands only from Payment's intake.
------------------------------------------------------------------------------------------------------

-- commands: as in migration 00002, and a suspend_hosting or resume_hosting command is inserted only
-- in a session that is a member of provisioning_payment_intake (pg_has_role ... 'MEMBER'; a superuser
-- is a member of every role in that sense). The role has no member and no privilege yet: Phase 6
-- attaches Payment's intake to it. So provisioning_api, which inserts deploy, promote and rollback
-- commands, can no longer take a Payment event key or restore hosting without payment.
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
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_payment_intake', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL AND NEW.fencing_generation IS NOT NULL THEN
        SELECT s.operation_generation
          INTO counter
          FROM provisioning.project_release_state AS s
         WHERE s.project_id = NEW.project_id;
        refused := counter IS DISTINCT FROM NEW.fencing_generation;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "commands" violates guard "commands_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_insert_shape',
                  DETAIL = 'A command is inserted as accepted, without a failure code, and with fencing_generation equal to the current operation_generation of its project; a hosting command only by a member of provisioning_payment_intake.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_insert_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- E. Frozen failure fields, write-once promoted_at, the 2^53-1 bounds.
------------------------------------------------------------------------------------------------------

-- releases: as in migration 00002, and promoted_at changes only from null to a value. Phase 5 grants
-- the column to the worker; the rule is in place before that.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.releases_identity_immutable() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.source_generation_request_id IS DISTINCT FROM OLD.source_generation_request_id
       OR NEW.source_repository_id IS DISTINCT FROM OLD.source_repository_id
       OR NEW.source_commit_sha IS DISTINCT FROM OLD.source_commit_sha
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.image_digest IS NOT NULL AND NEW.image_digest IS DISTINCT FROM OLD.image_digest)
       OR (OLD.promoted_at IS NOT NULL AND NEW.promoted_at IS DISTINCT FROM OLD.promoted_at) THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_identity_immutable',
                  DETAIL = 'release_id, project_id, source_generation_request_id, source_repository_id, source_commit_sha and created_at never change; image_digest and promoted_at change only from null to a value.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_identity_immutable() FROM PUBLIC;

-- releases: superseded, failed and rolled_back are final, and so are failure_code and
-- failure_summary of such a release.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.releases_terminal_status_final() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status IN ('superseded'::provisioning.release_status,
                      'failed'::provisioning.release_status,
                      'rolled_back'::provisioning.release_status)
       AND (NEW.status IS DISTINCT FROM OLD.status
            OR NEW.failure_code IS DISTINCT FROM OLD.failure_code
            OR NEW.failure_summary IS DISTINCT FROM OLD.failure_summary) THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_terminal_status_final"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_terminal_status_final',
                  DETAIL = 'The status, failure_code and failure_summary of a superseded, failed or rolled_back release never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_terminal_status_final() FROM PUBLIC;

-- commands: succeeded, failed and skipped are final, and so is the failure_code of such a command.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.commands_terminal_status_final() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status IN ('succeeded'::provisioning.command_status,
                      'failed'::provisioning.command_status,
                      'skipped'::provisioning.command_status)
       AND (NEW.status IS DISTINCT FROM OLD.status
            OR NEW.failure_code IS DISTINCT FROM OLD.failure_code) THEN
        RAISE EXCEPTION 'update of relation "commands" violates guard "commands_terminal_status_final"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_terminal_status_final',
                  DETAIL = 'The status and failure_code of a succeeded, failed or skipped command never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_terminal_status_final() FROM PUBLIC;

-- The upper bound 2^53-1 (domain.MaxRepositoryID, domain.MaxGeneration), so a value always survives a
-- JSON number, as the inbox's repository_id already does. Each check keeps its name and gains the
-- bound; the lower bound is unchanged.
ALTER TABLE provisioning.releases DROP CONSTRAINT releases_source_repository_id_check;
ALTER TABLE provisioning.releases
    ADD CONSTRAINT releases_source_repository_id_check
        CHECK (source_repository_id BETWEEN 1 AND 9007199254740991);

ALTER TABLE provisioning.project_release_state DROP CONSTRAINT project_release_state_operation_generation_check;
ALTER TABLE provisioning.project_release_state
    ADD CONSTRAINT project_release_state_operation_generation_check
        CHECK (operation_generation BETWEEN 0 AND 9007199254740991);

ALTER TABLE provisioning.commands DROP CONSTRAINT commands_fencing_generation_check;
ALTER TABLE provisioning.commands
    ADD CONSTRAINT commands_fencing_generation_check
        CHECK (fencing_generation BETWEEN 1 AND 9007199254740991);

------------------------------------------------------------------------------------------------------
-- Candidate steps.
------------------------------------------------------------------------------------------------------

-- provisioning_worker records each candidate attempt as a step row (decision 0011 section 6): it
-- inserts the pending attempt (the insert guard below holds its shape), starts it, and finishes it
-- succeeded or failed with a reason code. Table-level INSERT as for the intake and build roles; the
-- UPDATE columns are those a start and a finish write (no details_ref: a candidate attempt has no
-- build log). The UPDATE privilege also makes its row lock on a step legal. It already reads
-- releases and release_steps, which both step guards read with the caller's rights.
GRANT INSERT ON provisioning.release_steps TO provisioning_worker;
GRANT UPDATE (status, started_at, finished_at, deadline_at, reason_code)
    ON provisioning.release_steps TO provisioning_worker;

-- release_steps: a step is inserted as pending, without times, a reason or a reference, and only as
--   a build step, numbered 1 to 10 (domain.MaxBuildAttempts), on a release that is accepted or
--     building, as attempt 1 or as the attempt after one that failed (as in migration 00009); or
--   a candidate step, numbered 1 to 10 (domain.MaxCandidateAttempts), on a release that is built, as
--     attempt 1 or as the attempt after one that failed or succeeded (decision 0011 section 12: a
--     candidate that a newer deploy returned to built can be deployed again).
-- Every other step name is refused: access_setup is recorded inside the candidate step (decision 0011
-- section 6).
--
-- Step names belong to roles (decision 0011 section 12, rulings on task D1's report), tested by
-- membership (pg_has_role ... 'MEMBER'), as releases_build_transition does: a candidate step is
-- inserted only in a session that is a member of provisioning_worker; a build step is not inserted in
-- a session that is a member of provisioning_worker. A superuser is a member of every role and is
-- exempt. So provisioning_build, provisioning_intake and the owner keep their build steps as before
-- and cannot spend a release's candidate attempts, and the worker cannot write build steps. So a release holds at most 10 build and 10 candidate steps through the guarded paths,
-- 20 in all, within the 100-step read limit (application.MaxReleaseSteps). Role-independent. The
-- bounds are literals on purpose: a guard reads no configuration; a test compares each with its Go
-- constant. The reads need SELECT on releases and, for a later attempt, on release_steps:
-- provisioning_build and provisioning_worker hold both; provisioning_intake holds SELECT on releases
-- only, so a later attempt from it is refused for lack of privilege.
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
       OR NEW.reason_code IS NOT NULL OR NEW.details_ref IS NOT NULL
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
                  DETAIL = 'A step is inserted as pending without times, reason or reference: a build step, not by a member of provisioning_worker, numbered at most 10 on an accepted or building release, as attempt 1 or after a failed one; or a candidate step, only by a member of provisioning_worker, numbered at most 10 on a built release, as attempt 1 or after a failed or succeeded one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_insert_shape() FROM PUBLIC;

-- release_steps: on every update, for every role including the owner, as in migration 00009, except
-- that a candidate step moves pending -> running only while its release is built. Step names belong
-- to roles, as in the insert guard: a candidate step is updated only in a session that is a member of
-- provisioning_worker, a build step not in a session that is a member of provisioning_worker; a
-- superuser is exempt. Every other step,
-- build steps included, still starts only while its release is accepted or building. The release is
-- read without a lock, as in 00009 (the build paths lock the release before the step; the worker's
-- paths lock in the order of decision 0004 section 7, release before step). The read needs SELECT on
-- releases, which every role that may update a step holds (the owner, provisioning_build and
-- provisioning_worker; a test pins it).
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
       OR (OLD.reason_code IS NOT NULL AND NEW.reason_code IS DISTINCT FROM OLD.reason_code)
       OR (OLD.details_ref IS NOT NULL AND NEW.details_ref IS DISTINCT FROM OLD.details_ref);
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
                  DETAIL = 'A candidate step is updated only by a member of provisioning_worker, a build step not by one; release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed, and a finished step is final; a candidate step moves to running only while its release is built, any other step only while its release is accepted or building; started_at, deadline_at, finished_at, reason_code and details_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;
