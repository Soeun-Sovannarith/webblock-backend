-- +goose Up
-- Decision 0014 section 16 item 1 (owner-approved), first half: the new command_kind value only.
-- PostgreSQL does not let a transaction use an enum value that the same transaction added, and
-- goose runs each file in one transaction, so everything that names 'sync_domains' (the checks, the
-- index, the guard) is in 00015.
--
-- Forward-only, like every migration after 00011 (decision 0014 section 17 item 4): no Down section.

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

ALTER TYPE provisioning.command_kind ADD VALUE 'sync_domains';
