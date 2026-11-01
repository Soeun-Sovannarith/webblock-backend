# 0013 Grants of `project_app` on the internal tables of `project_messaging`

## Decision

- `project_app` holds SELECT and INSERT on `project_messaging.processed_messages`, and nothing more
  (`V3__runtime_grants.sql`).
- `project_app` holds SELECT, INSERT and DELETE on `project_messaging.pending_catalog_registrations`, and no UPDATE
  (`V5__pending_catalog_registrations.sql`).
- `GrantsTest` checks this exact set. The outbox tables keep the grants of decision 0001.

## Reason

The Notion rule "Who may write" (page "2 · Project") covers business rows only: no DELETE and UPDATE on listed
columns. These two tables are internal storage of background jobs, not business rows. The inbox records a handled
message once and never changes it (decision 0001, TR-20). The pending business profile is written at creation,
read by the catalog registration job, and deleted once the catalog holds it, so no personal data stays behind
(FR-35, `.claude/docs/runbooks/catalog-database.md`). Notion says the profile is "held meanwhile" there, which the
DELETE fits.

Options considered: no DELETE on the pending table, keeping each profile forever after registration; or the grant
set above. Chosen: the grant set above, the smallest set the jobs need.

Cost if wrong: one new migration changes a grant, and `GrantsTest` changes with it (TR-15).

## Status

Accepted. Built (`V3`, `V5`) and tested (`GrantsTest`).
