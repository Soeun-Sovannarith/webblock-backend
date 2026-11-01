-- +goose Up
-- The sub-domain becomes required (decision 0021 D1 and D8; Notion "5 · Provisioning", section
-- "The next Provisioning migration": project_hosting.subdomain VARCHAR(63) NOT NULL). Migration
-- 00020 added the column as nullable; the operator filled every older row with the command
-- "subdomain set" (docs/runbooks/operator.md). The changes:
--
--   1. A guard: while any hosting row has no sub-domain, stop with SQLSTATE 23502 and a message
--      that names the operator command, before any change.
--   2. project_hosting.subdomain SET NOT NULL.
--   3. Every grant 00020 gave provisioning_operator for the backfill is revoked: UPDATE (subdomain,
--      updated_at) and SELECT (project_id, subdomain) on project_hosting, EXECUTE on
--      offline_reasons_distinct and USAGE on schema provisioning. The backfill grant is temporary
--      (decision 0021; Notion gives the operator only the inbox manual retry), so the operator is
--      back to exactly its rights at 00019, and "subdomain missing" and "subdomain set" are
--      refused with SQLSTATE 42501. The sub-domain is now set once, at insert.
--
-- No DEFAULT, no CASCADE, no row is changed. Goose runs the file in one transaction; any failure
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

-- Fail before any change, with one message that names the missing role (created by
-- scripts/db/bootstrap-roles.sql); the revokes below name it.
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_operator']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

-- 1. Every row must have its sub-domain first. The lock keeps a row without one from appearing
-- between this count and the ALTER below (the ALTER takes the same lock anyway).
LOCK TABLE provisioning.project_hosting IN ACCESS EXCLUSIVE MODE;

-- +goose StatementBegin
DO $$
DECLARE
    found bigint;
BEGIN
    SELECT count(*) INTO found FROM provisioning.project_hosting WHERE subdomain IS NULL;
    IF found > 0 THEN
        RAISE EXCEPTION '% hosting row(s) have no subdomain; run the operator command "subdomain set" for each (docs/runbooks/operator.md)', found
            USING ERRCODE = '23502',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_hosting',
                  COLUMN = 'subdomain',
                  DETAIL = 'Migration 00021 makes project_hosting.subdomain NOT NULL. List the rows with "subdomain missing", set each from the value Project holds, then run the migration again. Nothing was changed.';
    END IF;
END
$$;
-- +goose StatementEnd

-- 2. Required from now on.
ALTER TABLE provisioning.project_hosting ALTER COLUMN subdomain SET NOT NULL;

-- 3. The backfill is over: take back every grant 00020 gave the operator for it.
REVOKE UPDATE (subdomain, updated_at) ON provisioning.project_hosting FROM provisioning_operator;
REVOKE SELECT (project_id, subdomain) ON provisioning.project_hosting FROM provisioning_operator;
REVOKE EXECUTE ON FUNCTION provisioning.offline_reasons_distinct(provisioning.offline_reason[])
    FROM provisioning_operator;
REVOKE USAGE ON SCHEMA provisioning FROM provisioning_operator;
