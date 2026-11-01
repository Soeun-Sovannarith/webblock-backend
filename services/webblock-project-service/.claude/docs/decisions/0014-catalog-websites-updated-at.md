# 0014 `catalog.websites.updated_at` after registration

## Decision

- The catalog registration job sets `catalog.websites.updated_at` when it inserts the row (FR-35).
- `catalog_platform` may not update `updated_at` (`catalog/V3__catalog_access.sql`), and no trigger sets it. A
  change of the status or the limits leaves it unchanged.
- No FR changes a website's status or limits today. The FR that adds such a change also decides how `updated_at`
  follows it, by a column grant or by a trigger, in a new catalog migration.

## Reason

Notion (page "6 · Shared catalog") lists `updated_at` as "Last change", and its "Who can read and write" gives
`catalog_platform` updates of the status and the limits only. The grants follow Notion exactly (TR-14, TR-15).
Nothing writes the status or the limits after registration, so the column is correct today.

Options considered: add a trigger now, or grant UPDATE on `updated_at` now, or wait for the FR. Chosen: wait,
because either change adds a rule no requirement needs yet.

Cost if wrong: a status or limit change built without the follow up leaves `updated_at` at the registration time,
until one catalog migration adds the grant or the trigger.

## Status

Accepted. Current state built and tested (`CatalogGrantsTest`); the follow up waits for an FR.
