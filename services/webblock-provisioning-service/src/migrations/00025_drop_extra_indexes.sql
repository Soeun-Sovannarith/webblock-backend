-- +goose Up
-- Drop the five indexes Notion "5 · Provisioning" does not show (constitution III.3: the database
-- matches Notion, no extra index; owner decision 0023 G20; DB audit of cqrs-layout, section 7).
-- None of them enforces a rule Notion states:
--
-- - provisioning.commands_domain_event_key: the key class of sync_domains, which Notion marks "In
--   the enum, unused" and "Started by: Never". Notion names two key classes only (client commands,
--   hosting events). No role can insert a sync_domains command since 00017.
-- - provisioning.release_steps_running_deadline: a scan index for the deadline sweeper.
-- - provisioning_ops.command_dispatch_due: a scan index for the dispatcher.
-- - provisioning_ops.source_published_events_due: a scan index for the inbox retries.
-- - provisioning_ops.usage_outbox_due: a scan index for the usage publisher.
--
-- The scans these served become sequential scans; the tables are small until a retention job
-- exists (TR-16). Nothing else changes: no table, column, type, constraint, trigger, guard,
-- function, grant or row. The indexes that hold a rule Notion states stay (one live and one
-- promoting release per project, one active attempt per release and stage, one command in flight,
-- the client and hosting key classes). The migration grants nothing, so it needs no role but the
-- migrator. Goose runs the file in one transaction; any failure changes nothing. Forward-only and
-- immutable: no Down section; fix a mistake with a new migration.

-- Ownership is the privilege boundary, so refuse to run as anyone but the migrator.
-- +goose StatementBegin
DO $$
BEGIN
    IF current_user <> 'provisioning_migrator' THEN
        RAISE EXCEPTION 'migrations must run as role provisioning_migrator (current role: %)', current_user
            USING ERRCODE = '42501', HINT = 'see .claude/docs/runbooks/phase-1-database.md';
    END IF;
END
$$;
-- +goose StatementEnd

DROP INDEX provisioning.commands_domain_event_key;
DROP INDEX provisioning.release_steps_running_deadline;
DROP INDEX provisioning_ops.command_dispatch_due;
DROP INDEX provisioning_ops.source_published_events_due;
DROP INDEX provisioning_ops.usage_outbox_due;
