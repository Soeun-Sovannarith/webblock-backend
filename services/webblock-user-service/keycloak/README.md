# Local Keycloak realm (dev on a laptop)

`webblock-realm.local.json` = the same realm setup as the dev Keycloak (`auth.roa-dev.digital`), for a local
Keycloak on http://localhost:8080. Use it while the dev Admin API is not reachable from a laptop.

It has: realm roles `USER` (default) and `ADMIN`; client `webblock-web` (Next.js, code flow + PKCE, mappers
`user_id`, `roles`, 6 audiences); client `webblock-auth-admin` (user-service robot: manage-users, view-users);
client `webblock-password-check` (user-service checks the current password on Change password); password policy;
lastName not required. No secrets and no users are in this file.

Import (Admin console http://localhost:8080):
1. If a realm `webblock` already exists: select it → Realm settings → Action (top right) → Delete.
2. Realm dropdown → Create realm → Resource file → Browse → this file → Create.
3. Clients → webblock-web → Credentials → copy the secret → Next.js `.env.local` KEYCLOAK_CLIENT_SECRET.
4. Clients → webblock-auth-admin → Credentials → copy → user-service `.env` KEYCLOAK_ADMIN_CLIENT_SECRET.
5. Clients → webblock-password-check → Credentials → copy → user-service `.env` KEYCLOAK_PASSWORD_CHECK_CLIENT_SECRET.
