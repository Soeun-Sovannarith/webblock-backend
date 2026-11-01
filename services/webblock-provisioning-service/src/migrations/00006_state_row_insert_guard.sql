-- +goose Up
-- Insert guard of the project state row (validation finding VR-08, decision 0008 section 5).
-- A project's row is inserted in its initial shape only: operation_generation 0, route_state
-- stable, hosting_state serving and no active release. Every later value is reached by an update
-- that its own guards and grants watch: the counter moves only with a command
-- (project_release_state_generation_step and project_release_state_generation_has_command, which
-- do not fire on insert), and the route, the hosting state and the active release belong to the
-- worker's later phases. Without this guard a role that may insert the row (provisioning_intake,
-- migration 00008) could start a project at any generation, with its route uncertain, offline, or
-- naming an active release.
--
-- Role-independent: it binds every role, the owner included. Forward-only and immutable, like
-- every migration: fix a mistake with a new one.

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

-- Hygiene as for the baseline guards (decision 0004 section 9): caller's rights, a fixed
-- search_path, owned by the migrator, no EXECUTE for PUBLIC, SQLSTATE 23514 with the guard's name
-- as the constraint name. It reads no table. A null in a NOT NULL column is left to that column's
-- own constraint (each comparison with a null is null, which does not refuse), so a forgotten
-- column is still reported as such. A BEFORE trigger runs before the check constraints, so a
-- negative generation is reported by this guard, not by project_release_state_operation_generation_check.
-- +goose StatementBegin
CREATE FUNCTION provisioning.project_release_state_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.operation_generation <> 0
       OR NEW.route_state <> 'stable'::provisioning.route_state
       OR NEW.hosting_state <> 'serving'::provisioning.hosting_state
       OR NEW.active_release_id IS NOT NULL THEN
        RAISE EXCEPTION 'insert into relation "project_release_state" violates guard "project_release_state_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_release_state',
                  CONSTRAINT = 'project_release_state_insert_shape',
                  DETAIL = 'A project state row is inserted with operation_generation 0, route_state stable, hosting_state serving and no active release.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_release_state_insert_shape() FROM PUBLIC;

CREATE TRIGGER project_release_state_insert_shape
    BEFORE INSERT ON provisioning.project_release_state
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_release_state_insert_shape();
