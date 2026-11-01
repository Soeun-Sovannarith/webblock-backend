-- +goose Up
-- Grants for the two service roles, exactly as decision 0004 section 8 lists them.
-- Nothing is granted to PUBLIC, and nothing is granted table-wide beyond SELECT and
-- the API's INSERT on commands. No role may DELETE, TRUNCATE or run DDL. No role may
-- insert a release, a state row or a step, or write image_digest, promoted_at,
-- active_release_id, route_state or hosting_state: each arrives in a new migration
-- with the phase that uses it.

-- Fail before any GRANT, with one message that names every missing role.
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

GRANT USAGE ON SCHEMA provisioning TO provisioning_api, provisioning_worker;

-- Both roles read all four tables.
GRANT SELECT ON
    provisioning.releases,
    provisioning.project_release_state,
    provisioning.release_steps,
    provisioning.commands
TO provisioning_api, provisioning_worker;

-- provisioning_api: accept a command. It advances the project's counter and inserts
-- the command. The UPDATE privilege on the two columns also makes its row lock on
-- the project row legal.
GRANT INSERT ON provisioning.commands TO provisioning_api;
GRANT UPDATE (operation_generation, updated_at) ON provisioning.project_release_state TO provisioning_api;

-- provisioning_worker: move a command and a release through their statuses.
-- UPDATE (updated_at) on the project row exists so that the fence lock
-- (SELECT ... FOR UPDATE) is legal: a row lock needs UPDATE on at least one column.
GRANT UPDATE (status, failure_code, updated_at) ON provisioning.commands TO provisioning_worker;
GRANT UPDATE (updated_at) ON provisioning.project_release_state TO provisioning_worker;
GRANT UPDATE (status, failure_code, failure_summary, updated_at) ON provisioning.releases TO provisioning_worker;
