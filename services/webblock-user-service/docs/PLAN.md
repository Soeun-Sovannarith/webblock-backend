# user-service — build plan and progress

Claude: read this file and `CLAUDE.md` at the start of every task. Do ONE task at a time, on its own branch,
show the plan first, then build, run `./gradlew.bat test`, and tell the developer how to check it by hand.
Update the checkboxes here when a task is done (ask before committing).

## Done
- [x] Keycloak realm `webblock` on dev Keycloak (roles, clients, policies) — token test passed for test.user
- [x] Repo scaffold copied from the team template (`services/user-service`)
- [x] Flyway migration `V202610301030__create_account_tables.sql` (columns = data dictionary)
      → must run once against the dev `account` DB and be checked in pgAdmin (3 tables incl. flyway_schema_history)

## Next (in this order — target: first 3 days)
1. **Account entity + repository** (`domain/account`, `infrastructure/persistence`)
   - `AccountUser` maps `account_users` exactly; `AccountStatus` enum `ACTIVE`, `SUSPENDED` (`EnumType.STRING`)
   - Check: app starts with `ddl-auto: validate` (Hibernate agrees with the table); repository test with Testcontainers.
2. **Keycloak admin adapter** (`infrastructure/client/keycloak`)
   - Uses client `webblock-auth-admin` (client credentials). Config: `webblock.keycloak.*` via env vars
     (`KEYCLOAK_ADMIN_CLIENT_ID`, `KEYCLOAK_ADMIN_CLIENT_SECRET`, server URL, realm).
   - Methods: findByEmail, createUser(email, fullName, password, emailVerified=true), setPassword, hasPassword,
     setEnabled, logoutAllSessions, set/clear attribute `picture`.
3. **Me / profile**: `GET /api/user/v1/me` (creates the `account_users` row on first call if missing:
   user_id, identity_issuer = token `iss`, identity_subject = token `sub`), `PUT /api/user/v1/me` (display_name only;
   email cannot change). Updates `last_login_at`. SUSPENDED → 403.
4. **OTP service** (Redis): 6 digits, store hash only, TTL 10 min, max 5 tries, resend after 60 s, delete after use,
   purpose REGISTER / RESET_PASSWORD. Sends the email (sender: open question). Never log the code.
5. **Register** (public): `POST /register/start` {fullName, email} · `/register/verify-otp` {email, code} → verificationToken
   (single use, ~10 min) · `/register/resend-otp` · `/register/complete` {verificationToken, password, confirmPassword}.
   Email exists with password → 409 "You already have an account". Exists via Google only → add password to that user.
6. **Forgot / reset password** (public): `/password/forgot` (same reply whether the email exists or not) ·
   `/password/verify-otp` · `/password/reset` → set password + log out all sessions.
7. Connect the Next.js Register and Forgot pages; Sign in through Keycloak's default login page (styled later).

## Later
- Keycloak login page styled like Figma (Keycloakify) · Google login + account linking (first broker login auto-link,
  picture import sync mode Import) · profile photo upload/change/remove (RustFS → Keycloak `picture`) ·
  admin: list users, suspend/unsuspend (+ `account_user_status_changes`, disable in Keycloak) ·
  one Keycloak client per website for `/portal` SSO (called by provisioning-service).

## Blocked on the team
- Open question #10 (token claims: `sub` + realm roles vs `user_id` + `roles` + `aud`) — needed before sign-in work.
- Who sends the OTP email (user-service SMTP or notification-service).
- Redis and RustFS connection details (DevOps).
