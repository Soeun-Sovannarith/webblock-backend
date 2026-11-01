-- +goose Up
-- The inbox for Project's "source published" event (decision 0008 section 2, OD-42 Project half):
-- one row per event key, typed columns only, no payload column, in the operational schema
-- provisioning_ops. It is not a business table. The intake inserts a row; the processor takes due
-- rows, asks the repository registry, and finishes each row as accepted (with the release it
-- recorded), rejected or failed. Rows are never deleted (retention: OD-15).
--
-- Grants follow in migration 00008, with the role that holds them (provisioning_intake).
--
-- No column has a default: the application supplies every value at insert. Forward-only and
-- immutable, like every migration: fix a mistake with a new one.

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

-- pending until the processor finishes the row; accepted, rejected and failed are terminal.
-- internal/application carries the same labels in the same order; an integration test compares them.
CREATE TYPE provisioning_ops.inbox_status AS ENUM ('pending', 'accepted', 'rejected', 'failed');

-- The composite foreign key below needs a unique key on the three columns it names. It is wider than
-- releases_project_request_key, which already makes (project_id, source_generation_request_id)
-- unique, so it adds no rule of its own: it lets an accepted inbox row name only the release that
-- was recorded for the same project and generation request.
ALTER TABLE provisioning.releases
    ADD CONSTRAINT releases_project_request_release_key UNIQUE (project_id, source_generation_request_id, release_id);

CREATE TABLE provisioning_ops.source_published_inbox (
    -- The event's natural key: one release per generation request of a project.
    project_id            uuid                          NOT NULL,
    generation_request_id uuid                          NOT NULL,
    repository_id         bigint                        NOT NULL,
    commit_sha            varchar(64)                   NOT NULL,
    status                provisioning_ops.inbox_status NOT NULL,
    -- Takes so far: the take increments it, nothing lowers it.
    attempts              integer                       NOT NULL,
    -- When the row is next due: the take moves it by the visibility timeout, a retry by its backoff.
    next_attempt_at       timestamptz                   NOT NULL,
    outcome_code          varchar(100),
    release_id            uuid,
    -- Later events with the same key and another repository or commit: counted, never silent.
    conflict_count        integer                       NOT NULL,
    last_conflict_at      timestamptz,
    received_at           timestamptz                   NOT NULL,
    updated_at            timestamptz                   NOT NULL,
    finished_at           timestamptz,
    CONSTRAINT source_published_inbox_pkey PRIMARY KEY (project_id, generation_request_id),
    -- 1 to 2^53-1, the range of domain.ParseRepositoryID.
    CONSTRAINT source_published_inbox_repository_id_check
        CHECK (repository_id BETWEEN 1 AND 9007199254740991),
    -- SHA-1 (40) or SHA-256 (64) in lowercase hexadecimal, as releases_source_commit_sha_check.
    CONSTRAINT source_published_inbox_commit_sha_check
        CHECK (commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    CONSTRAINT source_published_inbox_attempts_check CHECK (attempts >= 0),
    CONSTRAINT source_published_inbox_conflict_count_check CHECK (conflict_count >= 0),
    -- The domain.ValidateCode format. ^ and $ match only at the ends of the whole value.
    CONSTRAINT source_published_inbox_outcome_code_check CHECK (outcome_code ~ '^[a-z][a-z0-9_]{0,99}$'),
    -- An outcome code exactly when the row was rejected or failed.
    CONSTRAINT source_published_inbox_outcome_matches_status
        CHECK ((status IN ('rejected', 'failed')) = (outcome_code IS NOT NULL)),
    -- The release exactly when the row was accepted.
    CONSTRAINT source_published_inbox_release_matches_status
        CHECK ((status = 'accepted') = (release_id IS NOT NULL)),
    -- A finish time exactly when the status is terminal.
    CONSTRAINT source_published_inbox_finished_matches_status
        CHECK ((status <> 'pending') = (finished_at IS NOT NULL)),
    -- A conflict time exactly when a conflict was counted.
    CONSTRAINT source_published_inbox_conflict_time_matches_count
        CHECK ((conflict_count > 0) = (last_conflict_at IS NOT NULL)),
    -- The release recorded for this project and generation request, and no other. MATCH SIMPLE (the
    -- default): a null release_id, which every row but an accepted one has, is not checked.
    CONSTRAINT source_published_inbox_release_fk FOREIGN KEY (project_id, generation_request_id, release_id)
        REFERENCES provisioning.releases (project_id, source_generation_request_id, release_id) ON DELETE RESTRICT
);

-- The processor's take: due rows that are not finished, oldest first. Its size follows the number of
-- unfinished rows, not the size of the table. The take and the finish are never heap-only updates:
-- next_attempt_at is the key and finished_at is in the predicate (columns named in a predicate count
-- as indexed). Do not index attempts or updated_at, which a conflict or a backoff alone writes.
CREATE INDEX source_published_inbox_due ON provisioning_ops.source_published_inbox (next_attempt_at)
    WHERE finished_at IS NULL;

-- Insert guard. A row is inserted fresh: pending, no take and no conflict yet, without an outcome, a
-- release or a finish. So a role that may insert cannot store an event as already decided.
--
-- Hygiene as for the baseline guards (decision 0004 section 9): caller's rights, a fixed search_path,
-- every object named with its schema, owned by the migrator, no EXECUTE for PUBLIC, SQLSTATE 23514
-- with the guard's name as the constraint name. It reads no table. A null in a NOT NULL column is
-- left to that column's own constraint.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.source_published_inbox_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.status <> 'pending'::provisioning_ops.inbox_status
       OR NEW.attempts <> 0 OR NEW.conflict_count <> 0 OR NEW.last_conflict_at IS NOT NULL
       OR NEW.outcome_code IS NOT NULL OR NEW.release_id IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'insert into relation "source_published_inbox" violates guard "source_published_inbox_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_inbox',
                  CONSTRAINT = 'source_published_inbox_insert_shape',
                  DETAIL = 'An inbox row is inserted as pending, with no attempt and no conflict, and without an outcome, a release or a finish.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_inbox_insert_shape() FROM PUBLIC;

CREATE TRIGGER source_published_inbox_insert_shape
    BEFORE INSERT ON provisioning_ops.source_published_inbox
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.source_published_inbox_insert_shape();

-- Update guard, for every role including the owner:
--
--   the event (project_id, generation_request_id, repository_id, commit_sha, received_at) never changes;
--   attempts and conflict_count never decrease;
--   the status moves only from pending to accepted, rejected or failed, and a terminal status is final;
--   outcome_code, release_id and finished_at, once set, never change.
--
-- Without it a faulty or compromised processor could rewrite the event it is deciding, reset attempts
-- to escape the retry budget, reopen a finished event, or point an accepted event at another release.
-- A plain row trigger, not "UPDATE OF": a column-specific trigger does not fire when another BEFORE
-- trigger changes the column. It reads no table.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.source_published_inbox_update_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.generation_request_id IS DISTINCT FROM OLD.generation_request_id
       OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
       OR NEW.commit_sha IS DISTINCT FROM OLD.commit_sha
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.attempts < OLD.attempts
       OR NEW.conflict_count < OLD.conflict_count
       OR (OLD.status <> 'pending'::provisioning_ops.inbox_status AND NEW.status IS DISTINCT FROM OLD.status)
       OR (OLD.outcome_code IS NOT NULL AND NEW.outcome_code IS DISTINCT FROM OLD.outcome_code)
       OR (OLD.release_id IS NOT NULL AND NEW.release_id IS DISTINCT FROM OLD.release_id)
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
        RAISE EXCEPTION 'update of relation "source_published_inbox" violates guard "source_published_inbox_update_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'source_published_inbox',
                  CONSTRAINT = 'source_published_inbox_update_shape',
                  DETAIL = 'The event columns never change; attempts and conflict_count never decrease; the status moves from pending to accepted, rejected or failed only; outcome_code, release_id and finished_at, once set, never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.source_published_inbox_update_shape() FROM PUBLIC;

CREATE TRIGGER source_published_inbox_update_shape
    BEFORE UPDATE ON provisioning_ops.source_published_inbox
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.source_published_inbox_update_shape();
