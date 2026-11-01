-- +goose Up
-- Phase 3 roles and the guards that go with them (decision 0008 sections 4 and 5, OD-50):
--
--   provisioning_intake  release intake: the inbox of Project's "source published" event, the
--                        project's initial state row, the accepted release and its first build step;
--   provisioning_build   build attempts: start, result, failure and the deadline sweep.
--
-- Both run in the future cmd/build-worker, never in cmd/api or cmd/worker (one login role per
-- binary, decision 0004 section 8). No binary uses them yet. provisioning_api and
-- provisioning_worker are unchanged. Neither new role may DELETE, TRUNCATE, own an object, create
-- one, or write a table that is not listed for it below.
--
-- Forward-only and immutable, like every migration: fix a mistake with a new one.
--
-- Upgrade note: the index release_steps_one_active_attempt (end of this file) cannot be built on a
-- database whose release_steps already hold two pending or running attempts of one release and step
-- name. Nothing before this migration inserts a step (no role held INSERT on release_steps), so only
-- rows written by an administrator can do that; the migration then fails and changes nothing.

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

-- Fail before any GRANT, with one message that names every missing role.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_intake', 'provisioning_build']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

-- Grants. Nothing for PUBLIC and nothing with the grant option.
--
-- INSERT ... ON CONFLICT (columns) DO NOTHING needs SELECT on the conflict target as well as INSERT,
-- and a row lock (SELECT ... FOR UPDATE) needs UPDATE on at least one column of the table.

-- provisioning_intake.
GRANT USAGE ON SCHEMA provisioning, provisioning_ops TO provisioning_intake;
-- The inbox: the intake inserts and reads back; the processor takes, finishes, backs off and counts
-- conflicts. The event columns (project_id, generation_request_id, repository_id, commit_sha,
-- received_at) are not updatable; the update guard of migration 00007 holds them for the owner too.
GRANT SELECT, INSERT ON provisioning_ops.source_published_inbox TO provisioning_intake;
GRANT UPDATE (status, attempts, next_attempt_at, outcome_code, release_id, conflict_count, last_conflict_at,
              updated_at, finished_at)
    ON provisioning_ops.source_published_inbox TO provisioning_intake;
-- The project's initial state row, inserted ON CONFLICT (project_id) DO NOTHING. SELECT on project_id
-- serves the conflict target and the release insert guard's existence check; the row is not read
-- back otherwise, and the role holds no UPDATE on it, so it never locks it.
GRANT INSERT ON provisioning.project_release_state TO provisioning_intake;
GRANT SELECT (project_id) ON provisioning.project_release_state TO provisioning_intake;
-- The accepted release, inserted ON CONFLICT (project_id, source_generation_request_id) DO NOTHING and
-- read back: its identity, status and time columns only, never image_digest, promoted_at or the
-- failure columns.
GRANT INSERT (release_id, project_id, source_generation_request_id, source_repository_id, source_commit_sha,
              status, created_at, updated_at)
    ON provisioning.releases TO provisioning_intake;
GRANT SELECT ON provisioning.releases TO provisioning_intake;
-- The first build step. The step insert guard below holds its shape.
GRANT INSERT ON provisioning.release_steps TO provisioning_intake;

-- provisioning_build. It reads releases and steps, moves a release through its build, inserts later
-- build attempts and moves steps. The UPDATE privileges also make its row locks (release, then step)
-- legal. It holds nothing on project_release_state, commands or provisioning_ops.
GRANT USAGE ON SCHEMA provisioning TO provisioning_build;
GRANT SELECT ON provisioning.releases, provisioning.release_steps TO provisioning_build;
GRANT UPDATE (status, image_digest, failure_code, failure_summary, updated_at) ON provisioning.releases TO provisioning_build;
GRANT INSERT ON provisioning.release_steps TO provisioning_build;
GRANT UPDATE (status, started_at, finished_at, deadline_at, reason_code, details_ref)
    ON provisioning.release_steps TO provisioning_build;

-- Guards. Hygiene as for the baseline guards (decision 0004 section 9): caller's rights, a fixed
-- search_path, every object named with its schema, owned by the migrator, no EXECUTE for PUBLIC,
-- SQLSTATE 23514 with the guard's name as the constraint name, messages that carry no row values.
-- Plain row triggers, not "UPDATE OF". A null in a NOT NULL column is left to that column's own
-- constraint (a comparison with a null is null, which does not refuse), and a missing parent row to
-- its foreign key. A BEFORE trigger runs before the check constraints, so where a guard and a check
-- constraint both object, the guard is reported.

-- releases: a release is inserted as accepted, without a digest, a failure or promoted_at, with
-- created_at equal to updated_at and inside the inserting transaction's time window (from the start
-- of the transaction to the moment of the insert), for a project whose state row exists. The
-- acceptance transaction reads the database clock once and inserts the state row before the release
-- (decision 0008 sections 3 and 6). Role-independent: it binds every role, the owner included, so no
-- role can insert a live, promoting or failed release, nor a release dated in the far future that the
-- older-release rule would then favour. The existence check needs SELECT (project_id) on
-- project_release_state, which every role that may insert a release has.
-- +goose StatementBegin
CREATE FUNCTION provisioning.releases_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean := false;
BEGIN
    IF NEW.status <> 'accepted'::provisioning.release_status
       OR NEW.image_digest IS NOT NULL OR NEW.failure_code IS NOT NULL OR NEW.failure_summary IS NOT NULL
       OR NEW.promoted_at IS NOT NULL
       OR NEW.updated_at <> NEW.created_at
       OR NEW.created_at < pg_catalog.transaction_timestamp()
       OR NEW.created_at > pg_catalog.clock_timestamp() THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL THEN
        refused := NOT EXISTS (
            SELECT 1
              FROM provisioning.project_release_state AS s
             WHERE s.project_id = NEW.project_id
        );
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "releases" violates guard "releases_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_insert_shape',
                  DETAIL = 'A release is inserted as accepted, without image_digest, failure_code, failure_summary or promoted_at, with created_at equal to updated_at inside the inserting transaction''s time window, for a project whose state row exists.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_insert_shape() FROM PUBLIC;

CREATE TRIGGER releases_insert_shape
    BEFORE INSERT ON provisioning.releases
    FOR EACH ROW EXECUTE FUNCTION provisioning.releases_insert_shape();

-- releases: what a member of provisioning_build may do. Environments attach login roles by
-- membership, so current_user is the login role and never provisioning_build itself: the guard
-- therefore tests membership (pg_has_role ... 'MEMBER'), never the role name. A superuser is a member
-- of every role in that sense and is exempt, because it can disable any trigger anyway.
--
-- For such a session the only status changes are accepted -> building, building -> built with a
-- digest, and building -> failed with a failure code. An update that leaves the status as it is
-- touches only a release that is accepted or building and leaves its digest and failure columns as
-- they are (for example updated_at after a failed attempt below the maximum). So the build process
-- cannot promote, fail a release the worker is moving, skip building, go back, or write a digest or a
-- failure on a release outside its build. provisioning_worker and the owner are not members and are
-- not restricted by this guard. pg_roles is readable by every role.
-- +goose StatementBegin
CREATE FUNCTION provisioning.releases_build_transition() RETURNS trigger
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
                AND NEW.status = 'built'::provisioning.release_status AND NEW.image_digest IS NOT NULL)
            OR (OLD.status = 'building'::provisioning.release_status
                AND NEW.status = 'failed'::provisioning.release_status AND NEW.failure_code IS NOT NULL)
            OR (OLD.status IN ('accepted'::provisioning.release_status, 'building'::provisioning.release_status)
                AND NEW.status = OLD.status
                AND NEW.image_digest IS NOT DISTINCT FROM OLD.image_digest
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

CREATE TRIGGER releases_build_transition
    BEFORE UPDATE ON provisioning.releases
    FOR EACH ROW EXECUTE FUNCTION provisioning.releases_build_transition();

-- release_steps: a step is inserted as pending, without times, a reason or a reference; in Phase 3
-- only build steps, on a release that is accepted or building; as attempt 1, or as the attempt after
-- one that failed. Role-independent. The reads need SELECT on releases and, for a later attempt, on
-- release_steps: provisioning_build holds both; provisioning_intake inserts only the first attempt
-- and holds SELECT on releases only, so a later attempt from it is refused for lack of privilege.
-- +goose StatementBegin
CREATE FUNCTION provisioning.release_steps_insert_shape() RETURNS trigger
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
       OR NEW.step_name <> 'build'::provisioning.release_step_name THEN
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
                  DETAIL = 'A step is inserted as a pending build step without times, reason or reference, on an accepted or building release, as attempt 1 or as the attempt after a failed one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_insert_shape() FROM PUBLIC;

CREATE TRIGGER release_steps_insert_shape
    BEFORE INSERT ON provisioning.release_steps
    FOR EACH ROW EXECUTE FUNCTION provisioning.release_steps_insert_shape();

-- release_steps: on every update, for every role including the owner:
--
--   the identity (release_step_id, release_id, step_name, attempt) never changes;
--   the status moves pending -> running -> succeeded | failed, or pending -> failed, and a
--   succeeded or failed step is final;
--   started_at, deadline_at, finished_at, reason_code and details_ref, once set, never change.
--
-- Without it a faulty or compromised build process could reopen a failed step for the sweeper,
-- move a running step's deadline into the past or the far future, or rewrite why a step failed.
-- +goose StatementBegin
CREATE FUNCTION provisioning.release_steps_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.release_step_id IS DISTINCT FROM OLD.release_step_id
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
       OR (OLD.details_ref IS NOT NULL AND NEW.details_ref IS DISTINCT FROM OLD.details_ref) THEN
        RAISE EXCEPTION 'update of relation "release_steps" violates guard "release_steps_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'release_steps',
                  CONSTRAINT = 'release_steps_update_shape',
                  DETAIL = 'release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed, and a finished step is final; started_at, deadline_at, finished_at, reason_code and details_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;

CREATE TRIGGER release_steps_update_shape
    BEFORE UPDATE ON provisioning.release_steps
    FOR EACH ROW EXECUTE FUNCTION provisioning.release_steps_update_shape();

-- One active attempt per release and step name: a second pending or running attempt fails with
-- SQLSTATE 23505 on the statement that makes it, the step analogue of commands_one_in_flight.
CREATE UNIQUE INDEX release_steps_one_active_attempt ON provisioning.release_steps (release_id, step_name)
    WHERE status IN ('pending', 'running');
