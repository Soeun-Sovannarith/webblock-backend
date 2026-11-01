-- +goose Up
-- Grants only (decision 0014 section 8, owner-approved): the release worker promotes, suspends and
-- resumes, so it may write the columns those writes touch. No table, column, enum value, constraint,
-- trigger or guard changes; the guards of 00011 keep watching every one of these columns.
--
--   releases.promoted_at            set once, when a release goes live (write-once: identity guard)
--   project_hosting.live_release_id the release that is live (the rollback target)
--   project_hosting.routing_status  stable, changing, uncertain
--   project_hosting.hosting_status  online, offline (suspend and resume)
GRANT UPDATE (promoted_at) ON provisioning.releases TO provisioning_worker;
GRANT UPDATE (live_release_id, routing_status, hosting_status)
    ON provisioning.project_hosting TO provisioning_worker;

-- +goose Down
REVOKE UPDATE (promoted_at) ON provisioning.releases FROM provisioning_worker;
REVOKE UPDATE (live_release_id, routing_status, hosting_status)
    ON provisioning.project_hosting FROM provisioning_worker;
