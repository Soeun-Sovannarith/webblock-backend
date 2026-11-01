# 0015 The catalog database keeps PUBLIC CONNECT

## Decision

- The catalog database keeps PostgreSQL's default CONNECT for PUBLIC
  (`.claude/docs/runbooks/catalog-database.md`, step 3).
- PUBLIC holds no TEMP on it (`catalog/V4`), no USAGE on the schema `catalog` and no EXECUTE on its functions
  (TR-15).
- The default schema `public` stays. PUBLIC can see it (USAGE, no CREATE in PostgreSQL 18), and it holds nothing:
  no migration creates anything there.

## Reason

Each generated site logs in as its own `site_<id>` role. Provisioning creates these roles at run time, one per
website, outside this repository. They connect to the catalog database through PUBLIC's CONNECT, so no grant is
needed per site. Inside the database a role reaches nothing without the grants of `catalog_site`, and row-level
security limits each site to its own rows (FR-35, TR-14). Notion (page "6 · Shared catalog") does not state who may
connect.

Options considered: revoke CONNECT from PUBLIC and grant it per site login when Provisioning creates the role, or
keep PUBLIC CONNECT. Kept, because a per-site grant adds a step to Provisioning's role setup for no data gain.

Cost if wrong: any role on the server can open a session in the catalog database and see that it exists. Closing it
is one REVOKE plus a CONNECT grant in Provisioning's site role setup.

## Status

Accepted. Built (runbook step 3, `catalog/V4`).
