# Security (always on)

- Never read, print or edit `.env*`, `*.pem`, `*.key`, `secrets/**`, Keycloak realm exports with secrets, or WeBill credentials.
- Config comes from env vars / Kubernetes secrets. Put placeholders in `application.yml` (`${WEBILL_API_KEY}`), never real values.
- Every owner endpoint checks that the resource belongs to the caller (`owner_user_id == jwt.sub` mapped user). Return 404, not 403, for other owners' resources.
- Admin endpoints require the Keycloak `admin` realm role. Admin accounts are never created by the public register flow.
- Log no tokens, passwords, OTPs, KHQR data, bank account numbers (`sender_account_no`) or full emails.
- `failure_code` / `failure_summary` / `errors` columns hold sanitized text only (no stack traces, SQL, file paths, secrets).
- Generated sites: each gets its own DB role (`websites.db_role_name`) with access only to its own data.
- Validate all input at the controller (Bean Validation) and on the client (zod). Trust neither alone.
