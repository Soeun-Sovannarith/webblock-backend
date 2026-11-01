-- +goose Up
-- Decision 0014 section 21 answer 2 and section 23 item 4 (owner-approved, 9 October 2026). This is
-- the only schema change of wave 6. Nothing else changes: no business table, column or enum value of
-- revision 19 is added, removed or renamed.
--
--   1. provisioning_ops.usage_outbox: the outbox of Payment's "publish completed" usage event, one
--      row per release that went live, inserted by the worker in the transaction that confirms the
--      release live (confirm_live), published with publisher confirms by the worker's usage
--      publisher, and marked sent. It follows the command_dispatch and source_published_events
--      patterns: operational storage, typed columns only (the event's fields, no payload), no
--      default, an insert guard and an update guard, a partial index on the due rows, column grants.
--      Payment's real contract stays open (OD-04, OD-42); the event is a prototype stand-in.
--   2. provisioning_api may read the non-sensitive columns of source_published_events that
--      diagnostics show (the inbox outcome of a release): the key, the status, the outcome code, the
--      release, the attempts, the mismatched repeats and the times received and finished. Never the
--      repository, the commit or the schedule.
--
-- Goose runs the file in one transaction; any failure changes nothing. Forward-only and immutable:
-- no Down section (decision 0014 section 17 item 4); fix a mistake with a new migration.

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
-- scripts/db/bootstrap-roles.sql).
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api', 'provisioning_worker']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- 1. The usage outbox.
------------------------------------------------------------------------------------------------------

-- pending until the broker confirmed the event; sent is final. The application's UsageEventStatus
-- carries the same labels in the same order; the schema mirror's drift test compares them.
CREATE TYPE provisioning_ops.usage_event_status AS ENUM ('pending', 'sent');

CREATE TABLE provisioning_ops.usage_outbox (
    -- The release that went live; one usage event per release.
    release_id        uuid                                NOT NULL,
    project_id        uuid                                NOT NULL,
    -- The event's stable key, which Payment deduplicates on (payment.usage_records.event_key).
    event_key         varchar(255)                        NOT NULL,
    -- When the release was confirmed live (its promoted_at).
    completed_at      timestamptz                         NOT NULL,
    status            provisioning_ops.usage_event_status NOT NULL,
    -- Publish attempts so far: the take increments it, nothing lowers it.
    attempts          integer                             NOT NULL,
    -- When the row is next due: the take moves it by the visibility timeout, a failure by its
    -- backoff.
    next_attempt_at   timestamptz                         NOT NULL,
    last_failure_code varchar(100),
    -- When the broker confirmed the event; set exactly when the row is sent.
    sent_at           timestamptz,
    created_at        timestamptz                         NOT NULL,
    updated_at        timestamptz                         NOT NULL,
    CONSTRAINT usage_outbox_pkey PRIMARY KEY (release_id),
    CONSTRAINT usage_outbox_event_key_key UNIQUE (event_key),
    -- The key is derived from the release, so a row can carry no other release's key.
    CONSTRAINT usage_outbox_event_key_check
        CHECK (event_key = 'publish-completed-' || release_id::text),
    CONSTRAINT usage_outbox_attempts_check CHECK (attempts >= 0),
    -- The domain.ValidateCode format. ^ and $ match only at the ends of the whole value.
    CONSTRAINT usage_outbox_last_failure_code_check
        CHECK (last_failure_code ~ '^[a-z][a-z0-9_]{0,99}$'),
    CONSTRAINT usage_outbox_sent_matches_status CHECK ((status = 'sent') = (sent_at IS NOT NULL)),
    -- The release of this project and no other; rows are never deleted, and neither are releases.
    CONSTRAINT usage_outbox_release_fk FOREIGN KEY (project_id, release_id)
        REFERENCES provisioning.releases (project_id, release_id) ON DELETE RESTRICT
);

-- The publisher's take: due rows that are not sent, oldest first. Its size follows the number of
-- unsent rows, not the size of the table.
CREATE INDEX usage_outbox_due ON provisioning_ops.usage_outbox (next_attempt_at)
    WHERE status = 'pending';

-- Insert guard. A row is inserted fresh, for a release that is live in the same transaction: pending,
-- no attempt, no failure, not sent. So a worker cannot store an event as already sent, or one for a
-- release that never went live. A null in a NOT NULL column is left to that column's own constraint
-- and a missing release to the foreign key.
--
-- Hygiene as for the other guards (decision 0004 section 9): caller's rights, a fixed search_path,
-- every object named with its schema, owned by the migrator, no EXECUTE for PUBLIC, SQLSTATE 23514
-- with the guard's name as the constraint name. The read of releases needs SELECT on it, which the
-- worker holds.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.usage_outbox_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    release_status provisioning.release_status;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'pending'::provisioning_ops.usage_event_status
       OR NEW.attempts <> 0 OR NEW.last_failure_code IS NOT NULL OR NEW.sent_at IS NOT NULL THEN
        refused := true;
    ELSIF NEW.release_id IS NOT NULL AND NEW.project_id IS NOT NULL THEN
        SELECT r.status
          INTO release_status
          FROM provisioning.releases AS r
         WHERE r.project_id = NEW.project_id AND r.release_id = NEW.release_id;
        refused := FOUND AND release_status <> 'live'::provisioning.release_status;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "usage_outbox" violates guard "usage_outbox_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'usage_outbox',
                  CONSTRAINT = 'usage_outbox_insert_shape',
                  DETAIL = 'A usage event is inserted as pending, with no attempt, failure or send time, for a release that is live.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.usage_outbox_insert_shape() FROM PUBLIC;

CREATE TRIGGER usage_outbox_insert_shape
    BEFORE INSERT ON provisioning_ops.usage_outbox
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.usage_outbox_insert_shape();

-- Update guard, for every role including the owner:
--
--   the event (release_id, project_id, event_key, completed_at, created_at) never changes;
--   attempts never decrease;
--   the status moves only from pending to sent, and sent is final;
--   sent_at, once set, never changes.
--
-- Without it a faulty publisher could rewrite the event it is sending, reset attempts, or reopen a
-- sent event. A plain row trigger, not "UPDATE OF". It reads no table.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.usage_outbox_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.event_key IS DISTINCT FROM OLD.event_key
       OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.attempts < OLD.attempts
       OR (OLD.status = 'sent'::provisioning_ops.usage_event_status AND NEW.status IS DISTINCT FROM OLD.status)
       OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS DISTINCT FROM OLD.sent_at) THEN
        RAISE EXCEPTION 'update of relation "usage_outbox" violates guard "usage_outbox_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'usage_outbox',
                  CONSTRAINT = 'usage_outbox_update_shape',
                  DETAIL = 'The event columns never change; attempts never decrease; the status moves from pending to sent only; sent_at, once set, never changes.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.usage_outbox_update_shape() FROM PUBLIC;

CREATE TRIGGER usage_outbox_update_shape
    BEFORE UPDATE ON provisioning_ops.usage_outbox
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.usage_outbox_update_shape();

-- Grants. Nothing for PUBLIC; nobody may DELETE or TRUNCATE. provisioning_worker inserts the row in
-- the confirm_live transaction, takes due rows, and records each publish: SELECT serves the take and
-- the conditional writes, and the UPDATE columns are exactly those a take, a failure or a send
-- writes (the UPDATE privilege also makes the take's row locks legal). It already holds USAGE on
-- provisioning_ops (00004).
GRANT SELECT, INSERT ON provisioning_ops.usage_outbox TO provisioning_worker;
GRANT UPDATE (status, attempts, next_attempt_at, last_failure_code, sent_at, updated_at)
    ON provisioning_ops.usage_outbox TO provisioning_worker;

------------------------------------------------------------------------------------------------------
-- 2. Diagnostics read the inbox outcome (decision 0014 section 23 item 4).
------------------------------------------------------------------------------------------------------

-- provisioning_api already holds USAGE on provisioning_ops (00004). The key columns serve the WHERE
-- clause; the rest is what diagnostics show. Not granted: github_repository_id, git_commit_sha,
-- next_attempt_at, updated_at, last_mismatched_repeat_at, max_attempts and the manual retry columns.
GRANT SELECT (project_id, generation_request_id, status, outcome_code, release_id, attempts,
              mismatched_repeat_count, received_at, finished_at)
    ON provisioning_ops.source_published_events TO provisioning_api;
