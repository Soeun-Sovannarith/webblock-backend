-- +goose Up
-- The admin reason, the redeploy guards and the nullable sub-domain (decision 0021 D1; Notion
-- "5 · Provisioning", section "The next Provisioning migration", and the sections commands and
-- project_hosting). Migration 00019 added the enum values this file uses. The changes:
--
--   1. commands_requested_by_subject_by_kind: a requester (not empty) exactly for deploy, promote,
--      rollback and redeploy, and for a hosting command with reason admin_suspended (the admin);
--      none for the system hosting reasons and sync_domains.
--   2. commands_hosting_reason_by_key: a key whose root starts with admin: is admin_suspended,
--      checked before the other patterns, so admin:project-deleted is admin_suspended and can never
--      take the system key project-deleted.
--   3. commands_idempotency_key: the client key class now holds redeploy too (dropped and created in
--      this transaction).
--   4. commands_insert_shape: deploy, promote, rollback and redeploy only from members of
--      provisioning_api; a hosting command with admin_suspended only from members of
--      provisioning_api, with any other reason only from members of provisioning_hosting_intake;
--      sync_domains as before; a redeploy only when it names the project's live release while
--      hosting is online and routing is stable.
--   5. project_hosting.subdomain varchar(63) NULL, a lower-case DNS label (check). NULL only on a
--      row from before this migration; the operator fills it (Task 10) and 00021 sets NOT NULL.
--   6. project_hosting_insert_shape: every new row names a sub-domain.
--      project_hosting_reasons_guard: the sub-domain changes only from NULL to a value, and only for
--      a member of provisioning_operator (the backfill), for every role, the owner included.
--   7. Grants for the backfill to provisioning_operator: USAGE on schema provisioning, SELECT
--      (project_id, subdomain) and UPDATE (subdomain, updated_at) on project_hosting, and EXECUTE on
--      offline_reasons_distinct (a check of project_hosting calls it with the writer's rights).
--      provisioning_api already holds INSERT on commands and command_dispatch and UPDATE
--      (last_command_number, updated_at) on project_hosting, which redeploy and the admin hosting
--      commands need.
--
-- Nothing for usage_outbox: its insert stays tied to a release becoming live, which a redeploy
-- never does. The API and the worker still refuse redeploy and admin hosting commands until
-- Phase C (decision 0021 D10).
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
-- scripts/db/bootstrap-roles.sql); the guards below test membership in some of them.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api', 'provisioning_hosting_intake', 'provisioning_intake',
                        'provisioning_operator']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

-- Fail before any change when an existing command would break the new rules, with a message that
-- names the cause instead of a bare check violation. Neither can come from the code: a Payment
-- event key that starts with admin: holds billing before this file and admin_suspended after it,
-- and the API and the worker refuse redeploy (decision 0021 D10), so such a row was never checked
-- against the live release. An administrator decides how to correct the row, then runs this again.
-- +goose StatementBegin
DO $$
DECLARE
    found bigint;
BEGIN
    SELECT count(*) INTO found
      FROM provisioning.commands
     WHERE kind IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind)
       AND idempotency_key ~ '^admin:';
    IF found > 0 THEN
        RAISE EXCEPTION '% hosting command(s) have a key that starts with admin: from before migration 00020; nothing was changed', found
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_hosting_reason_by_key',
                  DETAIL = 'From migration 00020 a key that starts with admin: is an admin hosting command with reason admin_suspended and a requester; an older row with such a key holds another reason and would break that check.';
    END IF;
    SELECT count(*) INTO found
      FROM provisioning.commands
     WHERE kind = 'redeploy'::provisioning.command_kind;
    IF found > 0 THEN
        RAISE EXCEPTION '% redeploy command(s) exist from before migration 00020; nothing was changed', found
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_insert_shape',
                  DETAIL = 'A redeploy is inserted only from migration 00020, which checks that it names the live release while hosting is online and routing is stable and shares the client key class; an older one was never checked.';
    END IF;
END
$$;
-- +goose StatementEnd

------------------------------------------------------------------------------------------------------
-- 1 and 2. The checks of commands.
------------------------------------------------------------------------------------------------------

-- IS NOT DISTINCT FROM keeps a NULL reason (every kind but the hosting kinds) from turning the
-- whole check into NULL, which a check would pass.
ALTER TABLE provisioning.commands DROP CONSTRAINT commands_requested_by_subject_by_kind;
ALTER TABLE provisioning.commands ADD CONSTRAINT commands_requested_by_subject_by_kind
    CHECK (((requested_by_subject IS NOT NULL)
            = (kind IN ('deploy', 'promote', 'rollback', 'redeploy')
               OR hosting_reason IS NOT DISTINCT FROM 'admin_suspended'))
           AND (requested_by_subject IS NULL OR requested_by_subject <> ''));

-- As in 00018, with admin: first: the domain's hosting_reason_of_key looks at the root key, and a
-- key that starts with admin: keeps that start after .retry-<n> is removed.
ALTER TABLE provisioning.commands DROP CONSTRAINT commands_hosting_reason_by_key;
ALTER TABLE provisioning.commands ADD CONSTRAINT commands_hosting_reason_by_key
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
               AND NOT (kind = 'resume_hosting' AND hosting_reason = 'project_deleted')));

------------------------------------------------------------------------------------------------------
-- 3. The client key class holds redeploy (the argument of 00002: a client key and an event key
-- never meet).
------------------------------------------------------------------------------------------------------

DROP INDEX provisioning.commands_idempotency_key;
CREATE UNIQUE INDEX commands_idempotency_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind IN ('deploy', 'promote', 'rollback', 'redeploy');

------------------------------------------------------------------------------------------------------
-- 4. Who inserts which kind. As in 00015, with the admin reason and the redeploy rule.
------------------------------------------------------------------------------------------------------

-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.commands_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    counter bigint;
    live uuid;
    hosting provisioning.hosting_status;
    routing provisioning.routing_status;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'accepted'::provisioning.command_status OR NEW.failure_code IS NOT NULL THEN
        refused := true;
    ELSIF NEW.kind IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind)
          AND NEW.hosting_reason IS NOT DISTINCT FROM 'admin_suspended'::provisioning.offline_reason
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_api', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.kind IN ('suspend_hosting'::provisioning.command_kind, 'resume_hosting'::provisioning.command_kind)
          AND NEW.hosting_reason IS DISTINCT FROM 'admin_suspended'::provisioning.offline_reason
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_hosting_intake', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.kind = 'sync_domains'::provisioning.command_kind
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_intake', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.kind IN ('deploy'::provisioning.command_kind, 'promote'::provisioning.command_kind,
                       'rollback'::provisioning.command_kind, 'redeploy'::provisioning.command_kind)
          AND NOT pg_catalog.pg_has_role(current_user, 'provisioning_api', 'MEMBER') THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL AND NEW.command_number IS NOT NULL THEN
        SELECT s.last_command_number, s.live_release_id, s.hosting_status, s.routing_status
          INTO counter, live, hosting, routing
          FROM provisioning.project_hosting AS s
         WHERE s.project_id = NEW.project_id;
        refused := counter IS DISTINCT FROM NEW.command_number;
        IF NEW.kind = 'redeploy'::provisioning.command_kind THEN
            refused := refused
                OR live IS NULL
                OR NEW.release_id IS DISTINCT FROM live
                OR hosting IS DISTINCT FROM 'online'::provisioning.hosting_status
                OR routing IS DISTINCT FROM 'stable'::provisioning.routing_status;
        END IF;
    ELSIF NEW.kind = 'redeploy'::provisioning.command_kind THEN
        refused := true;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "commands" violates guard "commands_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_insert_shape',
                  DETAIL = 'A command is inserted as accepted, without a failure code, and with command_number equal to the current last_command_number of its project; a deploy, promote, rollback or redeploy, and a hosting command with reason admin_suspended, only by a member of provisioning_api; a hosting command with any other reason only by a member of provisioning_hosting_intake; a sync_domains command only by a member of provisioning_intake; a redeploy only when it names the live release of its project while hosting is online and routing is stable.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_insert_shape() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- 5. The sub-domain, nullable until the backfill (decision 0021 D1).
------------------------------------------------------------------------------------------------------

ALTER TABLE provisioning.project_hosting ADD COLUMN subdomain varchar(63);

-- A lower-case DNS label: 1 to 63 letters, digits and hyphens, no hyphen at either end (the
-- domain's validate_subdomain). NULL passes until 00021.
ALTER TABLE provisioning.project_hosting ADD CONSTRAINT project_hosting_subdomain_check
    CHECK (subdomain ~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$');

------------------------------------------------------------------------------------------------------
-- 6. Guards of project_hosting.
------------------------------------------------------------------------------------------------------

-- As in 00018, with a sub-domain on every new row.
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
           AND NEW.subdomain IS NOT NULL
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
                  DETAIL = 'A project hosting row is inserted with last_command_number 0, routing_status stable, no live release and a subdomain, and either, by any role but a member of provisioning_hosting_intake, with owner_status_version 0, hosting_status online and no offline reason, or, only by a member of provisioning_hosting_intake, with owner_status_version 1 or more and either online without a reason or offline with user_suspended alone.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_insert_shape() FROM PUBLIC;

-- As in 00018, with the sub-domain: set once, from NULL to a value, by the operator's backfill.
-- +goose StatementBegin
CREATE OR REPLACE FUNCTION provisioning.project_hosting_reasons_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.owner_status_version < OLD.owner_status_version
       OR ('project_deleted'::provisioning.offline_reason = ANY (OLD.offline_reasons)
           AND ('project_deleted'::provisioning.offline_reason = ANY (NEW.offline_reasons)) IS NOT TRUE)
       OR (NEW.subdomain IS DISTINCT FROM OLD.subdomain
           AND (OLD.subdomain IS NOT NULL
                OR NEW.subdomain IS NULL
                OR NOT pg_catalog.pg_has_role(current_user, 'provisioning_operator', 'MEMBER'))) THEN
        RAISE EXCEPTION 'update of relation "project_hosting" violates guard "project_hosting_reasons_guard"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  CONSTRAINT = 'project_hosting_reasons_guard',
                  DETAIL = 'owner_status_version never decreases, the offline reason project_deleted, once present, is never removed, and subdomain changes only from null to a value, by a member of provisioning_operator.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_hosting_reasons_guard() FROM PUBLIC;

------------------------------------------------------------------------------------------------------
-- 7. Grants for the sub-domain backfill (Task 10 adds the operator command).
------------------------------------------------------------------------------------------------------

GRANT USAGE ON SCHEMA provisioning TO provisioning_operator;
GRANT SELECT (project_id, subdomain) ON provisioning.project_hosting TO provisioning_operator;
GRANT UPDATE (subdomain, updated_at) ON provisioning.project_hosting TO provisioning_operator;
GRANT EXECUTE ON FUNCTION provisioning.offline_reasons_distinct(provisioning.offline_reason[])
    TO provisioning_operator;
