-- +goose Up
-- One grant (owner decision 0023 G18a, 25 October 2026): provisioning_bootstrap may read
-- project_hosting.subdomain. The bootstrap writes the sub-domain into the tenant namespace as a
-- label, the same trusted way it writes the project ID, so the route-shape admission policy can
-- refuse a host that is not <subdomain>.<site domain> of the namespace's own project (the
-- hostname-takeover guard, 0023 G18). Notion lists provisioning_bootstrap as "reads only"; this is
-- a read.
--
-- After this migration provisioning_bootstrap holds exactly: USAGE on schema provisioning, SELECT
-- (project_id, status) on releases and SELECT (project_id, live_release_id, subdomain) on
-- project_hosting. It still writes nothing. Every other role keeps exactly what it had.
--
-- Nothing else changes: no table, column, type, constraint, trigger, guard or row. Goose runs the
-- file in one transaction; any failure changes nothing. Forward-only and immutable: no Down
-- section; fix a mistake with a new migration.

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
      FROM unnest(ARRAY['provisioning_bootstrap']) AS wanted (rolname)
     WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = wanted.rolname);
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'missing role(s): %; run scripts/db/bootstrap-roles.sql as an administrator first', missing
            USING ERRCODE = '42704';
    END IF;
END
$$;
-- +goose StatementEnd

GRANT SELECT (subdomain) ON provisioning.project_hosting TO provisioning_bootstrap;
