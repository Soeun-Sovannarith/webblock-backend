---
paths:
  - "app/services/portal_sso.py"
  - "app/services/deployment.py"
  - "app/api/routes/internal.py"
  - "app/templates/site/lib/portal-auth.ts"
  - "app/templates/site/app/portal/**"
  - "tests/test_portal_sso.py"
---

# Product portal single sign-on

The full explanation for people is README section 9. In short: every generated site is its own confidential Keycloak client, `webblock-site-<projectId>`, with an `owner` client role. Its ID tokens carry that role as the `webblock_roles` claim. The site admits only owners.

- **The harness side (`portal_sso.py`) must stay idempotent.** `ensure_site_client` runs before and after every deploy. It merges redirect URIs instead of replacing them, adds the mapper and role only when they're missing, and re-grants the project owner. Exact redirect URIs only (`<origin>/portal/callback`), never wildcards.
- **Keycloak failures never fail a run.** `graph_pipeline._ensure_portal_sso` logs `SKIPPED:` (not configured) or `WARNING:` (Keycloak error) and deploys without the SSO keys in `site_env` (the site-catalog keys are independent). The site catalog is the opposite: a `site_catalog.SiteCatalogError` fails `DEPLOY` (`DEPLOY_FAILED`). The internal endpoints map `SiteClientMissing` to 404, other `KeycloakError`s to 502, and missing configuration to 503.
- **Keep the claim and role names in sync** in `portal_sso.py` (`ROLES_CLAIM`, `OWNER_ROLE`) and `lib/portal-auth.ts`. Keep the `portal_revoked_sessions` table in sync in the template `db/schema.ts`, `lib/portal-auth.ts` and `sql/007_catalog.sql` (where the table actually lives, in `catalog`, keyed `(website_id, sid)`; an extension to the team's design).
- **Secrets:** the site's client secret travels only in the deploy webhook body (`site_env`), as do `PGPASSWORD` and `STORAGE_SECRET_ACCESS_KEY`. Never log any of them, put them in run logs or check reports, or store them in Postgres (the site role's password is derived from `SITE_DB_SECRET` and Postgres receives only its SCRAM verifier, `site_catalog.scram_verifier`). The site derives its cookie keys from the client secret, so rotating it signs everyone out.
- **The site side (`lib/portal-auth.ts`) is the security boundary for the portal.** Keep every check:
  - signature (RS256/ES256 only, key type must match `alg`, unknown `kid` refetches the keys at most every 30 s);
  - `iss`, `aud`, `azp`, `exp`/`iat` with 60 s skew;
  - `nonce`, `state` (constant-time compare), PKCE S256;
  - the `owner` role;
  - an ended `sid` refused at sign-in, which also prevents a login loop;
  - failing closed when the revocation check can't reach the database.

  Use Node built-ins only; don't add an auth library without the user's OK, since every site would install it.
- **Server actions and `lib/products.ts` writes each check the session** (`requireOwner`, `ownerSql`). Keep both.
- **`/internal/v1/projects/{id}/portal-owners` are the only write endpoints under `/internal/v1`.** Don't add more without asking.
- **Tests:** `tests/test_portal_sso.py` patches `portal_sso.httpx.request` with an in-memory fake Keycloak. No Keycloak, DB or network is needed. There are no JS tests in this repo, so verify site changes with `next build` plus a real sign-in, as the `update-site-template` skill describes.
