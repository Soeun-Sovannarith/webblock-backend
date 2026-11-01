-- +goose Up
-- Update guard of the work row (validation finding V2-08). provisioning_worker may update the
-- dispatch and lease columns of provisioning_ops.command_work (migration 00004). On every
-- update, for every role including the owner:
--
--   lease_token, claim_count and publish_attempts never decrease, and
--   finished_at, once set, never changes.
--
-- Without it a faulty or compromised worker could lower lease_token so that an old lease
-- token is valid again, reset claim_count to escape the attempt budget, or clear finished_at
-- to put a finished command back into the dispatcher's scan. Every legal path only increases
-- the counters (take, claim) or keeps them (records, heartbeat, release, finish), and sets
-- finished_at once, in the transaction that gives the command its terminal status.
--
-- Forward-only and immutable, like every migration: fix a mistake with a new one.

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
-- search_path, owned by the migrator, no EXECUTE for PUBLIC, SQLSTATE 23514 with the guard's
-- name as the constraint name. It reads no table. A null in a NOT NULL column is left to that
-- column's own constraint. A plain row trigger, not "UPDATE OF": a column-specific trigger
-- does not fire when another BEFORE trigger changes the column.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.command_work_monotonic() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.lease_token < OLD.lease_token
       OR NEW.claim_count < OLD.claim_count
       OR NEW.publish_attempts < OLD.publish_attempts
       OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS DISTINCT FROM OLD.finished_at) THEN
        RAISE EXCEPTION 'update of relation "command_work" violates guard "command_work_monotonic"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'command_work',
                  CONSTRAINT = 'command_work_monotonic',
                  DETAIL = 'lease_token, claim_count and publish_attempts never decrease; finished_at, once set, never changes.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.command_work_monotonic() FROM PUBLIC;

CREATE TRIGGER command_work_monotonic
    BEFORE UPDATE ON provisioning_ops.command_work
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.command_work_monotonic();
