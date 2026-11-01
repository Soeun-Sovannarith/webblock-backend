-- +goose Up
-- Offline reasons, the owner status version and the repository rename (decision 0021; Notion
-- "5 · Provisioning", section "Planned, not yet applied: Migration 00018", and the sections
-- project_hosting, commands, releases, source_published_events and "Rules for every Provisioning
-- table"). The changes:
--
--   1. type provisioning.offline_reason (billing, user_suspended, project_deleted) and the
--      IMMUTABLE function provisioning.offline_reasons_distinct (no reason twice in a set).
--   2. provisioning.project_hosting.offline_reasons offline_reason[] NOT NULL: every reason the site
--      is offline for; the site is offline exactly when the set is not empty. No NULL element, one
--      dimension, no duplicate. project_deleted, once present, is never removed.
--   3. provisioning.project_hosting.owner_status_version bigint NOT NULL: the highest owner status
--      version applied, 0 for none, 0 to 2^53-1, never decreases.
--   4. provisioning.commands.hosting_reason offline_reason NULL: the reason a hosting command adds
--      (suspend) or removes (resume); required exactly for the hosting kinds, fixed by the key
--      (project-deleted is project_deleted, owner-status:<version> is user_suspended, any other key
--      is billing; each may carry .retry-<n>); a resume never names project_deleted; never changes.
--   5. Backfill of the existing rows (decision 0021 D4): an online row gets {}; an offline row with
--      a suspend_hosting whose root key is project-deleted and whose status is succeeded or skipped
--      gets {project_deleted}; every other offline row gets {billing}. owner_status_version is 0.
--      Each hosting command gets the reason of its key.
--   6. Guards: commands_identity_immutable also freezes hosting_reason; project_hosting_insert_shape
--      admits the new columns (version 0, online, no reason, by any role but the hosting intake;
--      or, only for a member of provisioning_hosting_intake, an owner-status row: version 1 or more,
--      online with no reason or offline with user_suspended); the new update guard
--      project_hosting_reasons_guard.
--   7. Rename github_repository_id to repository_id in releases and source_published_events (the
--      Gitea repository ID), with its check and NOT NULL constraints. The guard bodies that name the
--      column (releases_identity_immutable and source_published_events_update_shape, from 00011)
--      are re-created with the new name; the file ends with a check that no function body still
--      names the old column.
--   8. Grants: UPDATE (offline_reasons) on project_hosting to provisioning_worker; INSERT on
--      project_hosting and UPDATE (owner_status_version) to provisioning_hosting_intake; EXECUTE on
--      offline_reasons_distinct to the four roles that write project_hosting (its check calls it).
--   9. provisioning_payment_intake (retired by 00011) must hold no privilege and no ownership in
--      this database or on a shared object (such as CONNECT on a database) and no role membership, else the migration fails and changes nothing
--      (decision 0021 D2). The role itself is cluster-wide; an administrator drops it with
--      scripts/db/drop-retired-roles.sql after this migration (docs/runbooks/phase-1-database.md).
--
-- No DEFAULT, no CASCADE, no DELETE grant. Goose runs the file in one transaction; any failure
-- changes nothing. Forward-only and immutable: no Down section; fix a mistake with a new migration.

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
-- scripts/db/bootstrap-roles.sql). provisioning_payment_intake is not required: its absence is the
-- end state of decision 0021 D2.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api', 'provisioning_intake', 'provisioning_worker', 'provisioning_hosting_intake']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- 1. The type and the distinct function.
------------------------------------------------------------------------------------------------------

CREATE TYPE provisioning.offline_reason AS ENUM ('billing', 'user_suspended', 'project_deleted');

-- True when no reason occurs twice. NULL elements are refused by the check that calls it.
-- +goose StatementBegin
CREATE FUNCTION provisioning.offline_reasons_distinct(reasons provisioning.offline_reason[]) RETURNS boolean
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT count(*) = count(DISTINCT r) FROM unnest(reasons) AS u (r)
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.offline_reasons_distinct(provisioning.offline_reason[]) FROM PUBLIC;
-- A check constraint calls its function with the rights of the role that writes the row, and
-- PostgreSQL evaluates every check of a row on each insert and update. So each role that inserts or
-- updates project_hosting needs EXECUTE: the API and the hosting intake (the counter), the worker
-- (routing and hosting), the release intake and the hosting intake (the insert). No other role.
GRANT EXECUTE ON FUNCTION provisioning.offline_reasons_distinct(provisioning.offline_reason[])
    TO provisioning_api, provisioning_worker, provisioning_intake, provisioning_hosting_intake;

------------------------------------------------------------------------------------------------------
-- 2 to 4. The columns, nullable until the backfill.
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.project_hosting ADD COLUMN offline_reasons provisioning.offline_reason[];
ALTER TABLE provisioning.project_hosting ADD COLUMN owner_status_version bigint;
ALTER TABLE provisioning.commands ADD COLUMN hosting_reason provisioning.offline_reason;

------------------------------------------------------------------------------------------------------
-- 5. Backfill (decision 0021 D4). The migrator owns the tables, so the update guards of 00011 run:
-- the counter is not touched, so project_hosting_generation_step passes and the deferred
-- project_hosting_generation_has_command does not fire; commands_identity_immutable (00011 body)
-- does not name hosting_reason and commands_terminal_status_final checks only status and
-- failure_code. updated_at is not changed: the rows' state did not change.
------------------------------------------------------------------------------------------------------

UPDATE provisioning.project_hosting AS h
   SET owner_status_version = 0,
       offline_reasons = CASE
           WHEN h.hosting_status = 'online'::provisioning.hosting_status
               THEN '{}'::provisioning.offline_reason[]
           WHEN EXISTS (
               SELECT 1
                 FROM provisioning.commands AS c
                WHERE c.project_id = h.project_id
                  AND c.kind = 'suspend_hosting'::provisioning.command_kind
                  AND c.idempotency_key ~ '^project-deleted(\.retry-[1-9][0-9]{0,8})?$'
                  AND c.status IN ('succeeded'::provisioning.command_status,
                                   'skipped'::provisioning.command_status))
               THEN '{project_deleted}'::provisioning.offline_reason[]
           ELSE '{billing}'::provisioning.offline_reason[]
       END;

-- The key rule of section 4, as in commands_hosting_reason_by_key below. An empty key, and a key
-- with the reserved prefix owner-status: and no valid version, get no reason, so the checks below
-- refuse the migration on such a row (none can exist: the hosting intake stores only Payment event
-- keys and project-deleted before this migration). The nested CASE casts the version only after
-- the pattern has bounded it to 16 digits, so a longer one can never overflow bigint.
UPDATE provisioning.commands AS c
   SET hosting_reason = CASE
           WHEN c.idempotency_key = '' THEN NULL
           WHEN c.idempotency_key ~ '^project-deleted(\.retry-[1-9][0-9]{0,8})?$'
               THEN 'project_deleted'::provisioning.offline_reason
           WHEN c.idempotency_key ~ '^owner-status:' THEN
               CASE
                   WHEN c.idempotency_key ~ '^owner-status:[1-9][0-9]{0,15}(\.retry-[1-9][0-9]{0,8})?$' THEN
                       CASE
                           WHEN substring(c.idempotency_key FROM '^owner-status:([0-9]+)')::bigint <= 9007199254740991
                               THEN 'user_suspended'::provisioning.offline_reason
                       END
               END
           ELSE 'billing'::provisioning.offline_reason
       END
 WHERE c.kind IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind);

ALTER TABLE provisioning.project_hosting ALTER COLUMN offline_reasons SET NOT NULL;
ALTER TABLE provisioning.project_hosting ALTER COLUMN owner_status_version SET NOT NULL;

------------------------------------------------------------------------------------------------------
-- Checks.
------------------------------------------------------------------------------------------------------

-- The site is offline exactly when it holds a reason.
ALTER TABLE provisioning.project_hosting ADD CONSTRAINT project_hosting_offline_reasons_match_status
    CHECK ((hosting_status = 'offline') = (cardinality(offline_reasons) > 0));

-- One dimension (an empty array has none), no NULL element, no duplicate. The CASE keeps
-- array_position away from a multidimensional array, which it refuses with an error.
ALTER TABLE provisioning.project_hosting ADD CONSTRAINT project_hosting_offline_reasons_shape
    CHECK (CASE
               WHEN coalesce(array_ndims(offline_reasons), 1) <> 1 THEN false
               ELSE array_position(offline_reasons, NULL) IS NULL
                    AND provisioning.offline_reasons_distinct(offline_reasons)
           END);

ALTER TABLE provisioning.project_hosting ADD CONSTRAINT project_hosting_owner_status_version_check
    CHECK (owner_status_version BETWEEN 0 AND 9007199254740991);

ALTER TABLE provisioning.commands ADD CONSTRAINT commands_hosting_reason_by_kind
    CHECK ((hosting_reason IS NULL) = (kind NOT IN ('suspend_hosting', 'resume_hosting')));

-- The key fixes the reason (the domain's hosting_reason_of_key): an empty key has none (the domain
-- refuses it); project-deleted is project_deleted; owner-status:<v> with v from 1 to 2^53-1 (no
-- leading zero) is user_suspended, and any other key with that reserved prefix has no reason; any
-- other key is billing. Each may end in .retry-<n>, n from 1 to 999999999. A resume never names
-- project_deleted. IS NOT DISTINCT FROM makes a key without a reason fail rather than pass on
-- NULL; the nested CASE casts the version only once the pattern has bounded it.
ALTER TABLE provisioning.commands ADD CONSTRAINT commands_hosting_reason_by_key
    CHECK (hosting_reason IS NULL
           OR (hosting_reason IS NOT DISTINCT FROM (
                   CASE
                       WHEN idempotency_key = '' THEN NULL
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
               AND NOT (kind = 'resume_hosting' AND hosting_reason = 'project_deleted')));

------------------------------------------------------------------------------------------------------
-- 6. Guards.
------------------------------------------------------------------------------------------------------

-- commands: as in migration 00011, with hosting_reason.
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
       OR NEW.hosting_reason IS DISTINCT FROM OLD.hosting_reason
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
                  DETAIL = 'command_id, project_id, release_id, kind, hosting_reason, idempotency_key, request_fingerprint, command_number, requested_by_subject and created_at never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_identity_immutable() FROM PUBLIC;

-- project_hosting: as in migration 00011, with the new columns. Every row starts at counter 0,
-- stable and without a live release. A row of the release intake starts at owner status version
-- 0, online and without a reason. A member of provisioning_hosting_intake inserts owner-status rows
-- only: version 1 or more, online without a reason, or offline with user_suspended alone.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.project_hosting_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    allowed boolean;
BEGIN
    allowed := NEW.last_command_number = 0
           AND NEW.routing_status = 'stable'::provisioning.routing_status
           AND NEW.live_release_id IS NULL
           AND (
               (NEW.owner_status_version = 0
                AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_hosting_intake', 'MEMBER')
                AND NEW.hosting_status = 'online'::provisioning.hosting_status
                AND cardinality(NEW.offline_reasons) = 0)
               OR (NEW.owner_status_version >= 1
                   AND pg_catalog.pg_has_role(current_user, 'provisioning_hosting_intake', 'MEMBER')
                   AND ((NEW.hosting_status = 'online'::provisioning.hosting_status
                         AND cardinality(NEW.offline_reasons) = 0)
                        OR (NEW.hosting_status = 'offline'::provisioning.hosting_status
                            AND NEW.offline_reasons = '{user_suspended}'::provisioning.offline_reason[])))
           );
    IF allowed IS NOT TRUE THEN
        RAISE EXCEPTION 'insert into relation "project_hosting" violates guard "project_hosting_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_insert_shape',
                  DETAIL = 'A project hosting row is inserted with last_command_number 0, routing_status stable and no live release, and either, by any role but a member of provisioning_hosting_intake, with owner_status_version 0, hosting_status online and no offline reason, or, only by a member of provisioning_hosting_intake, with owner_status_version 1 or more and either online without a reason or offline with user_suspended alone.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_insert_shape() FROM PUBLIC;

-- project_hosting: owner_status_version never decreases, and project_deleted, once present, is
-- never removed. For every role, the owner included.
-- +goose StatementBegin
CREATE FUNCTION provisioning.project_hosting_reasons_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.owner_status_version < OLD.owner_status_version
       OR ('project_deleted'::provisioning.offline_reason = ANY (OLD.offline_reasons)
           AND ('project_deleted'::provisioning.offline_reason = ANY (NEW.offline_reasons)) IS NOT TRUE) THEN
        RAISE EXCEPTION 'update of relation "project_hosting" violates guard "project_hosting_reasons_guard"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_reasons_guard',
                  DETAIL = 'owner_status_version never decreases, and the offline reason project_deleted, once present, is never removed.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_reasons_guard() FROM PUBLIC;

CREATE TRIGGER project_hosting_reasons_guard
    BEFORE UPDATE ON provisioning.project_hosting
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_hosting_reasons_guard();

------------------------------------------------------------------------------------------------------
-- 7. The rename. PostgreSQL carries checks, NOT NULL, indexes and column grants through a column
-- rename, but not PL/pgSQL bodies or constraint names.
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.releases RENAME COLUMN github_repository_id TO repository_id;
ALTER TABLE provisioning_ops.source_published_events RENAME COLUMN github_repository_id TO repository_id;

ALTER TABLE provisioning.releases
    RENAME CONSTRAINT releases_github_repository_id_check TO releases_repository_id_check;
ALTER TABLE provisioning.releases
    RENAME CONSTRAINT releases_github_repository_id_not_null TO releases_repository_id_not_null;
ALTER TABLE provisioning_ops.source_published_events
    RENAME CONSTRAINT source_published_events_github_repository_id_check TO source_published_events_repository_id_check;
ALTER TABLE provisioning_ops.source_published_events
    RENAME CONSTRAINT source_published_events_github_repository_id_not_null TO source_published_events_repository_id_not_null;

-- releases: as in migration 00011, with repository_id.
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
       OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
       OR NEW.git_commit_sha IS DISTINCT FROM OLD.git_commit_sha
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.container_image_digest IS NOT NULL AND NEW.container_image_digest IS DISTINCT FROM OLD.container_image_digest)
       OR (OLD.promoted_at IS NOT NULL AND NEW.promoted_at IS DISTINCT FROM OLD.promoted_at) THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_identity_immutable',
                  DETAIL = 'release_id, project_id, generation_request_id, repository_id, git_commit_sha and created_at never change; container_image_digest and promoted_at change only from null to a value.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_identity_immutable() FROM PUBLIC;

-- source_published_events: as in migration 00011, with repository_id.
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
       OR NEW.repository_id IS DISTINCT FROM OLD.repository_id
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
-- 8. Grants.
------------------------------------------------------------------------------------------------------

GRANT UPDATE (offline_reasons) ON provisioning.project_hosting TO provisioning_worker;
GRANT INSERT ON provisioning.project_hosting TO provisioning_hosting_intake;
GRANT UPDATE (owner_status_version) ON provisioning.project_hosting TO provisioning_hosting_intake;

------------------------------------------------------------------------------------------------------
-- 9. The retired role holds nothing here (decision 0021 D2). pg_shdepend lists every privilege,
-- ownership and policy that names a role on an object of this database, columns included, and
-- (dbid 0) on a shared object such as a database (CONNECT) or a tablespace; role membership is
-- cluster-wide and is checked in both directions.
------------------------------------------------------------------------------------------------------
-- +goose StatementBegin
DO $$
DECLARE
    retired oid;
BEGIN
    SELECT r.oid INTO retired FROM pg_catalog.pg_roles AS r WHERE r.rolname = 'provisioning_payment_intake';
    IF retired IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_shdepend AS d
         WHERE d.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
           AND d.refobjid = retired
           AND (d.dbid = 0
                OR d.dbid = (SELECT db.oid FROM pg_catalog.pg_database AS db WHERE db.datname = pg_catalog.current_database()))
    ) THEN
        RAISE EXCEPTION 'migration 00018: role provisioning_payment_intake still holds a privilege or owns an object in this database; revoke it before migrating (decision 0021 D2)'
            USING ERRCODE = '55000';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM pg_catalog.pg_auth_members AS m
         WHERE m.member = retired OR m.roleid = retired
    ) THEN
        RAISE EXCEPTION 'migration 00018: role provisioning_payment_intake still has a role membership; remove it before migrating (decision 0021 D2)'
            USING ERRCODE = '55000';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- Final check: no function body in either schema still names github_repository_id. A failure here
-- rolls back the file.
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
       AND p.prosrc ~ '\mgithub_repository_id\M';
    IF stale IS NOT NULL THEN
        RAISE EXCEPTION 'migration 00018: function(s) still name github_repository_id: %', stale
            USING ERRCODE = '55000';
    END IF;
END
$$;
-- +goose StatementEnd
