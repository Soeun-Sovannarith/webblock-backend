# 0012 Checks on the internal tables of `project_messaging`

## Decision

- `project_messaging.processed_messages` keeps the check `processed_messages_outcome_check`: `outcome` is lower case
  letters and underscores only (`V2__project_messaging.sql`).
- `project_messaging.pending_catalog_registrations` keeps the check `pending_catalog_registrations_profile_object`:
  `business_profile` is a JSON object (`V5__pending_catalog_registrations.sql`).
- The two checks are part of the target state that TR-14 compares, beside what the Notion page "2 · Project" states.

## Reason

Both tables are internal storage of the service: the inbox of handled messages (decision 0001, TR-20) and the
pending business profile of the catalog registration job (FR-35). Notion lists them under "Background storage" by
column name only and states no rule for them, so the rule is this service's choice. A check refuses a bad row when it
is written, before a job or a re-drive reads it.

Options considered: drop both checks to follow a strict reading of "nothing more than Notion" (TR-14), or keep them.
Kept, because they hold only rows the service writes itself and refuse nothing a correct write sends.

Cost if wrong: one new migration drops or changes a check; no data is lost.

## Status

Accepted. Built (`V2`, `V5`) and tested (`ProcessedMessagesTest`, `PendingCatalogRegistrationsTest`).
