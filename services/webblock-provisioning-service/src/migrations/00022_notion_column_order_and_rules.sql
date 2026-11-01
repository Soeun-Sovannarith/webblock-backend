-- +goose Up
-- The database exactly as Notion "5 · Provisioning" (owner decision 0023 G16, 25 October 2026:
-- "follow Notion strictly, especially the database design"). It fixes the seven DIFFERENT findings
-- of the audit of 25 October 2026 (.superpowers/sdd/2026-10-25-provisioning-phase-c/
-- db-notion-audit.md section 7):
--
--   1. Column order. project_hosting, commands and source_published_events are rebuilt in Notion's
--      order. PostgreSQL cannot move a column, so each table is created again under a temporary
--      name, every row is copied, the old table is dropped and the new one takes its name. Every
--      named NOT NULL constraint, check, key, index, foreign key (ON DELETE RESTRICT), trigger, the
--      deferred constraint trigger project_hosting_generation_has_command, and every table and
--      column grant is created again with the same name and definition. The guard functions are not
--      touched: they name the tables in their bodies, which PostgreSQL looks up when they run.
--   2. provisioning.release_step_name in Notion's order: build, candidate, promotion, rollback,
--      access_setup, observation. A new type, the column moved to it, the old type dropped, the new
--      one renamed.
--   3. usage_outbox_insert_shape: completed_at must equal the release's promoted_at.
--   4. usage_outbox_update_shape: a sent row never changes, no column of it.
--   5. release_steps_update_shape: a succeeded or failed step never changes, no column of it (a
--      column still NULL could be set after the end before).
--   6. Check commands_skipped_only_for_hosting: only suspend_hosting and resume_hosting end skipped.
--   7. commands_terminal_status_final: a succeeded, failed or skipped command never changes, no
--      column of it, updated_at included.
--
-- Before any change the file stops, with SQLSTATE 23514 and a sentence that names the cause, when a
-- stored row already breaks 3 or 6. Findings 4, 5 and 7 are about updates, not stored rows, and
-- the code never updates a finished row (every UPDATE names the unfinished status it expects).
--
-- No DEFAULT, no CASCADE, no row changes its values. Goose runs the file in one transaction; any
-- failure changes nothing. Forward-only and immutable: no Down section; fix a mistake with a new
-- migration. Stop the service's processes before running it (it locks the tables) and start them
-- after (docs/runbooks/phase-1-database.md).

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

-- Fail before any change, with one message that names every missing role (created by
-- scripts/db/bootstrap-roles.sql); the grants below name them.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api', 'provisioning_bootstrap', 'provisioning_hosting_intake',
                        'provisioning_intake', 'provisioning_operator', 'provisioning_worker']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

-- No writer may come between the checks below and the rebuild.
LOCK TABLE provisioning.releases, provisioning.project_hosting, provisioning.release_steps,
           provisioning.commands, provisioning_ops.command_dispatch,
           provisioning_ops.source_published_events, provisioning_ops.usage_outbox
    IN ACCESS EXCLUSIVE MODE;

-- Fail before any change when a stored row breaks a new rule. Neither can come from the code: the
-- worker writes the release's promoted_at as completed_at (routing_store), and the domain moves
-- only a hosting command to skipped. An administrator decides how to correct the row, then runs
-- this again.
-- +goose StatementBegin
DO $$
DECLARE
    found bigint;
BEGIN
    SELECT count(*) INTO found
      FROM provisioning_ops.usage_outbox AS u
      LEFT JOIN provisioning.releases AS r
        ON r.project_id = u.project_id AND r.release_id = u.release_id
     WHERE r.promoted_at IS DISTINCT FROM u.completed_at;
    IF found > 0 THEN
        RAISE EXCEPTION '% usage event(s) have a completed_at that is not the promoted_at of their release; nothing was changed', found
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'usage_outbox',
                  CONSTRAINT = 'usage_outbox_insert_shape',
                  DETAIL = 'From migration 00022 a usage event carries the promoted_at of its release as completed_at (Notion "5 · Provisioning"); an older row that differs would break that rule.';
    END IF;
    SELECT count(*) INTO found
      FROM provisioning.commands
     WHERE status = 'skipped'::provisioning.command_status
       AND kind NOT IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind);
    IF found > 0 THEN
        RAISE EXCEPTION '% command(s) are skipped but are not a hosting command; nothing was changed', found
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_skipped_only_for_hosting',
                  DETAIL = 'From migration 00022 only a suspend_hosting or resume_hosting command may end skipped (Notion "5 · Provisioning"); an older row of another kind would break that check.';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- 1a. project_hosting in Notion's order: subdomain third, updated_at last.
------------------------------------------------------------------------------------------------------

CREATE TABLE provisioning.project_hosting_00022 (
    project_id           uuid                            CONSTRAINT project_hosting_project_id_not_null NOT NULL,
    live_release_id      uuid,
    subdomain            varchar(63)                     CONSTRAINT project_hosting_subdomain_not_null NOT NULL,
    last_command_number  bigint                          CONSTRAINT project_hosting_last_command_number_not_null NOT NULL,
    routing_status       provisioning.routing_status     CONSTRAINT project_hosting_routing_status_not_null NOT NULL,
    hosting_status       provisioning.hosting_status     CONSTRAINT project_hosting_hosting_status_not_null NOT NULL,
    offline_reasons      provisioning.offline_reason[]   CONSTRAINT project_hosting_offline_reasons_not_null NOT NULL,
    owner_status_version bigint                          CONSTRAINT project_hosting_owner_status_version_not_null NOT NULL,
    updated_at           timestamptz                     CONSTRAINT project_hosting_updated_at_not_null NOT NULL
);

-- The new table has no trigger yet, so the copy passes no guard; the checks below test every row.
INSERT INTO provisioning.project_hosting_00022
       (project_id, live_release_id, subdomain, last_command_number, routing_status, hosting_status,
        offline_reasons, owner_status_version, updated_at)
SELECT project_id, live_release_id, subdomain, last_command_number, routing_status, hosting_status,
       offline_reasons, owner_status_version, updated_at
  FROM provisioning.project_hosting;

-- Nothing else depends on the old table (no foreign key points at it).
DROP TABLE provisioning.project_hosting;
ALTER TABLE provisioning.project_hosting_00022 RENAME TO project_hosting;

ALTER TABLE provisioning.project_hosting
    ADD CONSTRAINT project_hosting_pkey PRIMARY KEY (project_id),
    ADD CONSTRAINT project_hosting_live_release_fk FOREIGN KEY (project_id, live_release_id)
        REFERENCES provisioning.releases (project_id, release_id) ON DELETE RESTRICT,
    ADD CONSTRAINT project_hosting_last_command_number_check
        CHECK (last_command_number >= 0 AND last_command_number <= 9007199254740991),
    ADD CONSTRAINT project_hosting_offline_reasons_match_status
        CHECK ((hosting_status = 'offline') = (cardinality(offline_reasons) > 0)),
    ADD CONSTRAINT project_hosting_offline_reasons_shape
        CHECK (CASE
                   WHEN coalesce(array_ndims(offline_reasons), 1) <> 1 THEN false
                   ELSE array_position(offline_reasons, NULL) IS NULL
                        AND provisioning.offline_reasons_distinct(offline_reasons)
               END),
    ADD CONSTRAINT project_hosting_owner_status_version_check
        CHECK (owner_status_version >= 0 AND owner_status_version <= 9007199254740991),
    ADD CONSTRAINT project_hosting_subdomain_check
        CHECK (subdomain ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$');

CREATE TRIGGER project_hosting_generation_step
    BEFORE UPDATE ON provisioning.project_hosting
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_hosting_generation_step();
CREATE TRIGGER project_hosting_insert_shape
    BEFORE INSERT ON provisioning.project_hosting
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_hosting_insert_shape();
CREATE TRIGGER project_hosting_reasons_guard
    BEFORE UPDATE ON provisioning.project_hosting
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_hosting_reasons_guard();
CREATE CONSTRAINT TRIGGER project_hosting_generation_has_command
    AFTER UPDATE ON provisioning.project_hosting
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.last_command_number IS DISTINCT FROM NEW.last_command_number)
    EXECUTE FUNCTION provisioning.project_hosting_generation_has_command();

REVOKE ALL ON provisioning.project_hosting FROM PUBLIC;
GRANT SELECT ON provisioning.project_hosting TO provisioning_api, provisioning_worker,
    provisioning_hosting_intake;
GRANT INSERT ON provisioning.project_hosting TO provisioning_intake, provisioning_hosting_intake;
GRANT SELECT (project_id) ON provisioning.project_hosting TO provisioning_intake;
GRANT SELECT (project_id, live_release_id) ON provisioning.project_hosting TO provisioning_bootstrap;
GRANT UPDATE (last_command_number, updated_at) ON provisioning.project_hosting TO provisioning_api;
GRANT UPDATE (last_command_number, updated_at, owner_status_version) ON provisioning.project_hosting
    TO provisioning_hosting_intake;
GRANT UPDATE (live_release_id, routing_status, hosting_status, offline_reasons, updated_at)
    ON provisioning.project_hosting TO provisioning_worker;

------------------------------------------------------------------------------------------------------
-- 1b. commands in Notion's order: hosting_reason fifth, after kind.
------------------------------------------------------------------------------------------------------

CREATE TABLE provisioning.commands_00022 (
    command_id           uuid                         CONSTRAINT commands_command_id_not_null NOT NULL,
    project_id           uuid                         CONSTRAINT commands_project_id_not_null NOT NULL,
    release_id           uuid,
    kind                 provisioning.command_kind    CONSTRAINT commands_kind_not_null NOT NULL,
    hosting_reason       provisioning.offline_reason,
    idempotency_key      varchar(255)                 CONSTRAINT commands_idempotency_key_not_null NOT NULL,
    request_fingerprint  char(64)                     CONSTRAINT commands_request_fingerprint_not_null NOT NULL,
    command_number       bigint                       CONSTRAINT commands_command_number_not_null NOT NULL,
    requested_by_subject varchar(255),
    status               provisioning.command_status  CONSTRAINT commands_status_not_null NOT NULL,
    failure_code         varchar(100),
    created_at           timestamptz                  CONSTRAINT commands_created_at_not_null NOT NULL,
    updated_at           timestamptz                  CONSTRAINT commands_updated_at_not_null NOT NULL
);

INSERT INTO provisioning.commands_00022
       (command_id, project_id, release_id, kind, hosting_reason, idempotency_key,
        request_fingerprint, command_number, requested_by_subject, status, failure_code, created_at,
        updated_at)
SELECT command_id, project_id, release_id, kind, hosting_reason, idempotency_key,
       request_fingerprint, command_number, requested_by_subject, status, failure_code, created_at,
       updated_at
  FROM provisioning.commands;

-- The one foreign key that points at commands goes for the swap and comes back below.
ALTER TABLE provisioning_ops.command_dispatch DROP CONSTRAINT command_dispatch_command_fk;
DROP TABLE provisioning.commands;
ALTER TABLE provisioning.commands_00022 RENAME TO commands;

ALTER TABLE provisioning.commands
    ADD CONSTRAINT commands_pkey PRIMARY KEY (command_id),
    ADD CONSTRAINT commands_command_number_key UNIQUE (project_id, command_number),
    ADD CONSTRAINT commands_release_fk FOREIGN KEY (project_id, release_id)
        REFERENCES provisioning.releases (project_id, release_id) ON DELETE RESTRICT,
    ADD CONSTRAINT commands_command_number_check
        CHECK (command_number >= 1 AND command_number <= 9007199254740991),
    ADD CONSTRAINT commands_failure_code_matches_status
        CHECK ((status IN ('failed', 'skipped')) = (failure_code IS NOT NULL)),
    ADD CONSTRAINT commands_hosting_reason_by_key
        CHECK (hosting_reason IS NULL
               OR (hosting_reason IS NOT DISTINCT FROM (
                       CASE
                           WHEN idempotency_key = '' THEN NULL
                           WHEN idempotency_key ~ '^admin:'
                               THEN 'admin_suspended'::provisioning.offline_reason
                           WHEN idempotency_key ~ '^project-deleted(\.retry-[1-9][0-9]{0,8})?$'
                               THEN 'project_deleted'::provisioning.offline_reason
                           WHEN idempotency_key ~ '^owner-status:' THEN
                               CASE
                                   WHEN idempotency_key ~ '^owner-status:[1-9][0-9]{0,15}(\.retry-[1-9][0-9]{0,8})?$' THEN
                                       CASE
                                           WHEN substring(idempotency_key FROM '^owner-status:([0-9]+)')::bigint <= 9007199254740991
                                               THEN 'user_suspended'::provisioning.offline_reason
                                       END
                               END
                           ELSE 'billing'::provisioning.offline_reason
                       END)
                   AND NOT (kind = 'resume_hosting' AND hosting_reason = 'project_deleted'))),
    ADD CONSTRAINT commands_hosting_reason_by_kind
        CHECK ((hosting_reason IS NULL) = (kind NOT IN ('suspend_hosting', 'resume_hosting'))),
    ADD CONSTRAINT commands_release_by_kind
        CHECK ((release_id IS NULL) = (kind IN ('suspend_hosting', 'resume_hosting', 'sync_domains'))),
    ADD CONSTRAINT commands_request_fingerprint_check
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT commands_requested_by_subject_by_kind
        CHECK (((requested_by_subject IS NOT NULL)
                = (kind IN ('deploy', 'promote', 'rollback', 'redeploy')
                   OR hosting_reason IS NOT DISTINCT FROM 'admin_suspended'))
               AND (requested_by_subject IS NULL OR requested_by_subject <> '')),
    -- 6. Only a hosting command ends skipped (its shutdown is no longer needed).
    ADD CONSTRAINT commands_skipped_only_for_hosting
        CHECK (status <> 'skipped' OR kind IN ('suspend_hosting', 'resume_hosting'));

-- The partial unique indexes: one command in flight per project, and one key class each for
-- client, hosting event and domain event keys.
CREATE UNIQUE INDEX commands_one_in_flight ON provisioning.commands (project_id)
    WHERE status IN ('accepted', 'running');
CREATE UNIQUE INDEX commands_idempotency_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind IN ('deploy', 'promote', 'rollback', 'redeploy');
CREATE UNIQUE INDEX commands_hosting_event_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind IN ('suspend_hosting', 'resume_hosting');
CREATE UNIQUE INDEX commands_domain_event_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind = 'sync_domains';

CREATE TRIGGER commands_identity_immutable
    BEFORE UPDATE ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_identity_immutable();
CREATE TRIGGER commands_insert_shape
    BEFORE INSERT ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_insert_shape();
CREATE TRIGGER commands_terminal_status_final
    BEFORE UPDATE ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_terminal_status_final();

ALTER TABLE provisioning_ops.command_dispatch
    ADD CONSTRAINT command_dispatch_command_fk FOREIGN KEY (command_id)
        REFERENCES provisioning.commands (command_id) ON DELETE RESTRICT;

REVOKE ALL ON provisioning.commands FROM PUBLIC;
GRANT SELECT, INSERT ON provisioning.commands TO provisioning_api, provisioning_hosting_intake;
GRANT SELECT ON provisioning.commands TO provisioning_worker;
GRANT UPDATE (status, failure_code, updated_at) ON provisioning.commands TO provisioning_worker;

------------------------------------------------------------------------------------------------------
-- 1c. source_published_events in Notion's order: max_attempts seventh, after attempts.
------------------------------------------------------------------------------------------------------

CREATE TABLE provisioning_ops.source_published_events_00022 (
    project_id                uuid                                 CONSTRAINT source_published_events_project_id_not_null NOT NULL,
    generation_request_id     uuid                                 CONSTRAINT source_published_events_generation_request_id_not_null NOT NULL,
    repository_id             bigint                               CONSTRAINT source_published_events_repository_id_not_null NOT NULL,
    git_commit_sha            varchar(64)                          CONSTRAINT source_published_events_git_commit_sha_not_null NOT NULL,
    status                    provisioning_ops.source_event_status CONSTRAINT source_published_events_status_not_null NOT NULL,
    attempts                  integer                              CONSTRAINT source_published_events_attempts_not_null NOT NULL,
    max_attempts              integer                              CONSTRAINT source_published_events_max_attempts_not_null NOT NULL,
    next_attempt_at           timestamptz                          CONSTRAINT source_published_events_next_attempt_at_not_null NOT NULL,
    outcome_code              varchar(100),
    release_id                uuid,
    mismatched_repeat_count   integer                              CONSTRAINT source_published_events_mismatched_repeat_count_not_null NOT NULL,
    last_mismatched_repeat_at timestamptz,
    received_at               timestamptz                          CONSTRAINT source_published_events_received_at_not_null NOT NULL,
    updated_at                timestamptz                          CONSTRAINT source_published_events_updated_at_not_null NOT NULL,
    finished_at               timestamptz,
    last_manual_retry_by      varchar(255),
    last_manual_retry_at      timestamptz
);

INSERT INTO provisioning_ops.source_published_events_00022
       (project_id, generation_request_id, repository_id, git_commit_sha, status, attempts,
        max_attempts, next_attempt_at, outcome_code, release_id, mismatched_repeat_count,
        last_mismatched_repeat_at, received_at, updated_at, finished_at, last_manual_retry_by,
        last_manual_retry_at)
SELECT project_id, generation_request_id, repository_id, git_commit_sha, status, attempts,
       max_attempts, next_attempt_at, outcome_code, release_id, mismatched_repeat_count,
       last_mismatched_repeat_at, received_at, updated_at, finished_at, last_manual_retry_by,
       last_manual_retry_at
  FROM provisioning_ops.source_published_events;

-- Nothing else depends on the old table (no foreign key points at it).
DROP TABLE provisioning_ops.source_published_events;
ALTER TABLE provisioning_ops.source_published_events_00022 RENAME TO source_published_events;

ALTER TABLE provisioning_ops.source_published_events
    ADD CONSTRAINT source_published_events_pkey PRIMARY KEY (project_id, generation_request_id),
    ADD CONSTRAINT source_published_events_release_fk
        FOREIGN KEY (project_id, generation_request_id, release_id)
        REFERENCES provisioning.releases (project_id, generation_request_id, release_id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT source_published_events_repository_id_check
        CHECK (repository_id >= 1 AND repository_id <= 9007199254740991),
    ADD CONSTRAINT source_published_events_git_commit_sha_check
        CHECK (git_commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    ADD CONSTRAINT source_published_events_attempts_check CHECK (attempts >= 0),
    ADD CONSTRAINT source_published_events_max_attempts_check CHECK (max_attempts >= 1),
    ADD CONSTRAINT source_published_events_outcome_code_check
        CHECK (outcome_code ~ '^[a-z][a-z0-9_]{0,99}$'),
    ADD CONSTRAINT source_published_events_outcome_matches_status
        CHECK ((status IN ('rejected', 'failed')) = (outcome_code IS NOT NULL)),
    ADD CONSTRAINT source_published_events_release_matches_status
        CHECK ((status = 'accepted') = (release_id IS NOT NULL)),
    ADD CONSTRAINT source_published_events_finished_matches_status
        CHECK ((status <> 'pending') = (finished_at IS NOT NULL)),
    ADD CONSTRAINT source_published_events_mismatched_repeat_count_check
        CHECK (mismatched_repeat_count >= 0),
    ADD CONSTRAINT source_published_events_mismatched_repeat_time_matches_count
        CHECK ((mismatched_repeat_count > 0) = (last_mismatched_repeat_at IS NOT NULL)),
    ADD CONSTRAINT source_published_events_manual_retry_by_check
        CHECK (last_manual_retry_by <> ''),
    ADD CONSTRAINT source_published_events_manual_retry_together
        CHECK ((last_manual_retry_by IS NULL) = (last_manual_retry_at IS NULL));

-- The intake's retry scan.
CREATE INDEX source_published_events_due ON provisioning_ops.source_published_events (next_attempt_at)
    WHERE finished_at IS NULL;

CREATE TRIGGER source_published_events_insert_shape
    BEFORE INSERT ON provisioning_ops.source_published_events
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.source_published_events_insert_shape();
CREATE TRIGGER source_published_events_update_shape
    BEFORE UPDATE ON provisioning_ops.source_published_events
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.source_published_events_update_shape();

REVOKE ALL ON provisioning_ops.source_published_events FROM PUBLIC;
GRANT SELECT, INSERT ON provisioning_ops.source_published_events TO provisioning_intake;
GRANT UPDATE (status, attempts, next_attempt_at, outcome_code, release_id, mismatched_repeat_count,
              last_mismatched_repeat_at, updated_at, finished_at)
    ON provisioning_ops.source_published_events TO provisioning_intake;
GRANT SELECT ON provisioning_ops.source_published_events TO provisioning_operator;
GRANT UPDATE (status, outcome_code, finished_at, max_attempts, next_attempt_at, last_manual_retry_by,
              last_manual_retry_at, updated_at)
    ON provisioning_ops.source_published_events TO provisioning_operator;
-- The API's diagnostic read (00016): never the repository, the commit or the retry columns.
GRANT SELECT (project_id, generation_request_id, status, attempts, outcome_code, release_id,
              mismatched_repeat_count, received_at, finished_at)
    ON provisioning_ops.source_published_events TO provisioning_api;

------------------------------------------------------------------------------------------------------
-- 2. release_step_name in Notion's order. The guards name the labels in their bodies, which
-- PostgreSQL looks up when they run, so they keep working; the two indexes on step_name are
-- rebuilt by the ALTER with the same names and definitions.
------------------------------------------------------------------------------------------------------

CREATE TYPE provisioning.release_step_name_00022 AS ENUM (
    'build', 'candidate', 'promotion', 'rollback', 'access_setup', 'observation'
);
ALTER TABLE provisioning.release_steps
    ALTER COLUMN step_name TYPE provisioning.release_step_name_00022
    USING step_name::text::provisioning.release_step_name_00022;
DROP TYPE provisioning.release_step_name;
ALTER TYPE provisioning.release_step_name_00022 RENAME TO release_step_name;

------------------------------------------------------------------------------------------------------
-- 3 and 4. The guards of usage_outbox.
------------------------------------------------------------------------------------------------------

-- As in 00016, with completed_at equal to the release's promoted_at.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.usage_outbox_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    release_status provisioning.release_status;
    promoted timestamptz;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'pending'::provisioning_ops.usage_event_status
       OR NEW.attempts <> 0 OR NEW.last_failure_code IS NOT NULL OR NEW.sent_at IS NOT NULL THEN
        refused := true;
    ELSIF NEW.release_id IS NOT NULL AND NEW.project_id IS NOT NULL THEN
        SELECT r.status, r.promoted_at
          INTO release_status, promoted
          FROM provisioning.releases AS r
         WHERE r.project_id = NEW.project_id AND r.release_id = NEW.release_id;
        refused := FOUND AND (release_status <> 'live'::provisioning.release_status
                              OR promoted IS DISTINCT FROM NEW.completed_at);
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "usage_outbox" violates guard "usage_outbox_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'usage_outbox',
                  CONSTRAINT = 'usage_outbox_insert_shape',
                  DETAIL = 'A usage event is inserted as pending, with no attempt, failure or send time, for a release that is live, with the promoted_at of that release as completed_at.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.usage_outbox_insert_shape() FROM PUBLIC;

-- As in 00016, with a sent row final in every column.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning_ops.usage_outbox_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status = 'sent'::provisioning_ops.usage_event_status
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.event_key IS DISTINCT FROM OLD.event_key
       OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.attempts < OLD.attempts
       OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS DISTINCT FROM OLD.sent_at) THEN
        RAISE EXCEPTION 'update of relation "usage_outbox" violates guard "usage_outbox_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'usage_outbox',
                  CONSTRAINT = 'usage_outbox_update_shape',
                  DETAIL = 'The event columns never change; attempts never decrease; the status moves from pending to sent only; a sent event never changes again.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.usage_outbox_update_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- 5. A finished step never changes. As in 00015, with a succeeded or failed row final in every
-- column.
------------------------------------------------------------------------------------------------------

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
    refused := OLD.status IN ('succeeded'::provisioning.release_step_status,
                              'failed'::provisioning.release_step_status)
       OR (OLD.step_name IN ('candidate'::provisioning.release_step_name,
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
                  DETAIL = 'A candidate, promotion or rollback step is updated only by a member of provisioning_worker, a build step not by one; release_step_id, release_id, step_name and attempt never change; a step moves pending, running, then succeeded or failed, or pending to failed; a succeeded or failed step never changes again, no column of it; a candidate step moves to running only while its release is built, a build step only while its release is accepted or building; started_at, deadline_at, finished_at, failure_code and log_ref, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.release_steps_update_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- 7. A finished command never changes, updated_at included. As in 00010, for every column.
------------------------------------------------------------------------------------------------------

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.commands_terminal_status_final() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status IN ('succeeded'::provisioning.command_status,
                      'failed'::provisioning.command_status,
                      'skipped'::provisioning.command_status) THEN
        RAISE EXCEPTION 'update of relation "commands" violates guard "commands_terminal_status_final"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_terminal_status_final',
                  DETAIL = 'A succeeded, failed or skipped command never changes again, no column of it, updated_at included.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_terminal_status_final() FROM PUBLIC;
