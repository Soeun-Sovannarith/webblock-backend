-- +goose Up
-- The two new enum values of Notion's next Provisioning migration, and nothing else (decision 0021
-- D1; Notion "5 · Provisioning", section "The next Provisioning migration"):
--
--   1. provisioning.offline_reason gains admin_suspended, last: an admin suspended this one website.
--   2. provisioning.command_kind gains redeploy, before sync_domains, the order Notion lists (decision
--      0021, lead ruling on Task 8): restart the live release's workload with the same image.
--
-- The 00014 precedent: PostgreSQL does not let a transaction use an enum value that the same
-- transaction added, and goose runs each file in one transaction, so everything that names the new
-- values (the checks, the admin keys, the redeploy guards) is in 00020. Until then no command can
-- carry admin_suspended (the 00018 key check gives every key another reason), and the API and the
-- worker refuse redeploy (decision 0021 D10).
--
-- No table, column, constraint, index, trigger, function or privilege changes. Forward-only and
-- immutable: no Down section; fix a mistake with a new migration.

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
-- scripts/db/bootstrap-roles.sql). provisioning_api is the role that will write both values (00020).
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_api']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

ALTER TYPE provisioning.offline_reason ADD VALUE 'admin_suspended';
ALTER TYPE provisioning.command_kind ADD VALUE 'redeploy' BEFORE 'sync_domains';
