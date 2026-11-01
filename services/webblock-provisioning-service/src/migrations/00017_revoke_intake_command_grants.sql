-- +goose Up
-- Grants only (decision 0014 section 23, amendment of 18 October 2026, owner; PLATFORM_DB_DESIGN.md
-- revision 20, sections 5 and 8). 00015 gave provisioning_intake the command-acceptance privileges
-- of provisioning_api so that it could accept sync_domains commands. Custom domains were withdrawn
-- (0014 section 23), the sync_domains intake and executor are gone, and nothing uses those
-- privileges. This migration revokes every 00015 extra:
--
--   SELECT on releases, project_hosting, release_steps and commands
--   INSERT on commands
--   INSERT on provisioning_ops.command_dispatch
--   UPDATE (last_command_number, updated_at) on project_hosting
--
-- and keeps the 00008 reads that the intake's release and step guards need: SELECT on releases
-- (the release insert, read back, and release_steps_insert_shape's read of the release status) and
-- SELECT (project_id) on project_hosting (the hosting row's ON CONFLICT target and
-- releases_insert_shape's existence check). A table-level REVOKE also removes that table's column
-- privileges, so both are granted again below. USAGE on both schemas (00008) is not touched. After
-- this migration provisioning_intake holds exactly what it held at 00014.
--
-- Nothing else changes: no table, column, enum value, constraint, trigger or guard. The enum value
-- sync_domains, its index commands_domain_event_key and the rule of commands_insert_shape that only
-- a member of provisioning_intake inserts one stay, unused; without INSERT on commands the intake
-- inserts no command at all.
--
-- Goose runs the file in one transaction; any failure changes nothing. Forward-only: no Down section
-- (decision 0014 section 17 item 4); fix a mistake with a new migration.

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
-- scripts/db/bootstrap-roles.sql).
-- +goose StatementBegin
DO $$
DECLARE
    missing text;
BEGIN
    SELECT string_agg(wanted.rolname, ', ' ORDER BY wanted.rolname)
      INTO missing
      FROM unnest(ARRAY['provisioning_intake']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

-- The 00015 extras, in the order 00015 granted them.
REVOKE SELECT ON
    provisioning.releases,
    provisioning.project_hosting,
    provisioning.release_steps,
    provisioning.commands
FROM provisioning_intake;
REVOKE INSERT ON provisioning.commands FROM provisioning_intake;
REVOKE UPDATE (last_command_number, updated_at) ON provisioning.project_hosting FROM provisioning_intake;
REVOKE INSERT ON provisioning_ops.command_dispatch FROM provisioning_intake;

-- The 00008 reads, granted again exactly as 00008 granted them (project_release_state is
-- project_hosting since 00011).
GRANT SELECT ON provisioning.releases TO provisioning_intake;
GRANT SELECT (project_id) ON provisioning.project_hosting TO provisioning_intake;
