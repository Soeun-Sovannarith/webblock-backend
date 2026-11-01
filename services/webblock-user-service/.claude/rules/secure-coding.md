---
paths:
  - "**/*.java"
  - "**/*.py"
  - "**/*.yml"
  - "**/*.sql"
  - "contracts/**"
---
# Secure coding (extra to `security.md`; read both)

`security.md` has the short list. This file explains how to apply it in the code you write here.

## 1. Authorization is the main risk (OWASP A01)
- Every owner endpoint loads the resource and compares its owner with the current user in the SERVICE layer/handler.
  A missing check is the most likely serious bug in this system. Not the owner -> **404**, same body as a missing resource.
- Never take `ownerUserId`/`userId` from a request body or path for permission decisions. Take it from the verified token
  (or from `x-user-id` on a trusted internal call).
- Admin endpoints: `ADMIN` role. Admin actions are audited: who, what, to whom, when (business rule 25).
- Internal (gRPC/Feign) endpoints must not be reachable through the public paths. Keep them off `/api/<service>/v1/**` or protect them.
- IDOR check for lists: filter by owner IN THE QUERY, not after loading everything.

## 2. Input
- Validate at the controller with Bean Validation (`@Valid`, `@Size`, `@Pattern`) AND again in the domain for rules the
  annotations cannot express (subdomain `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`, max 63 chars, reserved words).
- Whitelist, not blacklist. Reject unknown enum strings; do not `valueOf` a raw user string without a try/catch.
- Limit sizes: request body, list `limit` (max 100), string lengths, number of Puck blocks/pages (plan limits).
- Pagination `limit`/`cursor` are untrusted too. A cursor is opaque and signed or validated, not a raw SQL value.

## 3. Database
- Parameterized queries only (JPA/Spring Data/SQLAlchemy bound params). Never build SQL with string concatenation, even for `ORDER BY`
  (map a sort key through a whitelist).
- Python raw SQL (`sqlalchemy.text()`) must use `:name` bind parameters.
- Least privilege: each service has its own DB role for its own database. Each generated site has its own role
  (`websites.db_role_name`) with row-level security on the shared catalog.
- Migrations never contain data from production, passwords or personal data.

## 4. Secrets and config
- Config comes from env vars / Kubernetes Secrets. `application.yml` holds placeholders only: `${WEBILL_API_KEY}`.
- `application-k8s.yml` and `config-repo/*.yml` must contain no real value either. The `secret-scan` hook blocks writes that look like secrets.
- Never print config objects that hold secrets (`@ConfigurationProperties` `toString`). Mark secret fields so they are masked.

## 5. Outbound calls (SSRF, data leak)
- Hosts we call are fixed in config (Gitea, Jenkins, Webill, Knock, Keycloak, LLM). Never call a URL taken from a request,
  an event or a database row without a host allow-list.
- Always set connect + read timeouts. Never follow redirects to another host for authenticated calls.
- Do not forward the user's token to another service (send `x-user-id`); never send our service headers to outside hosts.

## 6. Files, archives, generated code
- Never build a file path from user or AI output without normalizing it and checking it stays inside the target folder
  (`..`, absolute paths, drive letters, symlinks). Applies to the scaffold, Git commits, archive extraction (zip-slip/tar-slip).
- AI/GLM output is untrusted: allow-list paths and extensions, size limits, and let the build/validation step be the final check.
  Do not `eval`/`exec` it on the host (`BUILD_RUNNER=local` is for trusted dev only).
- Verify downloads with the SHA-256 in the contract. Presigned links are short-lived and never logged.

## 7. Deserialization and events
- Event consumers accept only the fields they need and ignore unknown ones; they validate types and ranges.
- Never trust an event just because it is well formed: check your own database before acting (`rabbitmq-events.md`).
- Do not use Java native serialization or Python `pickle` for anything that crosses a boundary.

## 8. Web basics
- CORS: only the known frontend origins. The gateway/Istio own CORS; services do not add `*`.
- No sensitive data in URLs (tokens, OTPs, e-mails). Use headers/body.
- Rate-limit login, OTP, password reset and KHQR creation (Istio/gateway + service-side counters for OTP attempts).
- Errors: stable code, short sentence, no internals (`rest-response-format.md`).

## 9. Dependencies and build
- Do not add a dependency casually. Prefer what is already in the service; pin versions; check it is maintained.
  Never put versions on Boot/Cloud artifacts (BOM manages them). `springdocVersion` is pinned in each `build.gradle`.
- Run the build and tests after adding one. Mention new dependencies in your answer.
- Lockfiles are changed by the package manager only (`protect-files` hook).

## 10. Payments and accounts
- WeBill callbacks/polling are verified and idempotent; a bill is marked PAID once. Amounts come from our database, not the request.
- OTP: 6 digits, short expiry, limited attempts, stored hashed, never logged. Admin accounts are never created by registration.

## When you find a vulnerability
Say so plainly with the file and line, the exact input that triggers it, and the smallest fix. Do not hide it in a refactor.
The `security-auditor` agent and `/security-scan` command do a systematic pass.
