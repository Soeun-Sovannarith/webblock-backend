-- +goose Up
-- Grants only (decision 0014 section 9, owner-approved): provisioning_hosting_intake accepts the
-- suspend_hosting and resume_hosting commands that Payment's events ask for, so it holds exactly
-- the command-acceptance privileges of provisioning_api. No table, column, enum value, constraint,
-- trigger or guard changes; commands_insert_shape still admits a hosting command only from a member
-- of provisioning_hosting_intake, and refuses one from provisioning_api.
--
--   USAGE on provisioning, provisioning_ops       reach the tables
--   SELECT on the four business tables            the acceptance facts
--   INSERT on commands                            the accepted command
--   UPDATE (last_command_number, updated_at)      advance the counter; makes the row lock legal
--   INSERT on provisioning_ops.command_dispatch   the dispatch row, in the same transaction
GRANT USAGE ON SCHEMA provisioning, provisioning_ops TO provisioning_hosting_intake;
GRANT SELECT ON
    provisioning.releases,
    provisioning.project_hosting,
    provisioning.release_steps,
    provisioning.commands
TO provisioning_hosting_intake;
GRANT INSERT ON provisioning.commands TO provisioning_hosting_intake;
GRANT UPDATE (last_command_number, updated_at) ON provisioning.project_hosting TO provisioning_hosting_intake;
GRANT INSERT ON provisioning_ops.command_dispatch TO provisioning_hosting_intake;

-- +goose Down
REVOKE INSERT ON provisioning_ops.command_dispatch FROM provisioning_hosting_intake;
REVOKE UPDATE (last_command_number, updated_at) ON provisioning.project_hosting FROM provisioning_hosting_intake;
REVOKE INSERT ON provisioning.commands FROM provisioning_hosting_intake;
REVOKE SELECT ON
    provisioning.releases,
    provisioning.project_hosting,
    provisioning.release_steps,
    provisioning.commands
FROM provisioning_hosting_intake;
REVOKE USAGE ON SCHEMA provisioning, provisioning_ops FROM provisioning_hosting_intake;
