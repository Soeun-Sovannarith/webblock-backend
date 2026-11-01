---
paths:
  - "**/infrastructure/security/**"
  - "**/*SecurityConfig*.java"
  - "**/*Jwt*.java"
  - "**/presentation/grpc/**"
  - "commons/**"
  - "**/*auth*.py"
  - "**/*security*.py"
---
# Who is calling: tokens and `user_id` (Notion section 2.4)

## Status: two documents disagree. Do not guess.
| Topic | Notion page (27 Oct 2026) | `security.md` / older code |
|---|---|---|
| User identity | top-level claim **`user_id`** (= `account.users.id`) | `jwt.sub` |
| Roles | top-level claim **`roles`**: `USER`, `ADMIN` | Keycloak realm role `admin` |
| Audience | each service refuses a token without **its own** `aud` | not checked |
This is open question #10 in `docs/specs/open-questions.md`. Until the team answers: **do not change existing
`SecurityConfig` behaviour on your own.** For new code, write the check behind ONE small helper (`CurrentUser`) so changing the claim
name later touches one file. Say in your answer which claim you used.

## The flow
1. Every REST call from the frontend server or mobile app sends `Authorization: Bearer <Keycloak access token>`.
2. The Istio gateway checks it first (`RequestAuthentication`; no token = refused).
3. **Then the service decodes and checks it again itself** (never trust the gateway or an identity header): signature with Keycloak's key set,
   **RS256 only**, `iss`, its own `aud`, `typ` = `Bearer`, `exp`/`nbf`. Tokens live 5 minutes.
4. When that service calls ANOTHER service for the user, it sends **`x-user-id`** (not the token). Istio mTLS proves which service is calling.
   The receiver accepts `x-user-id` only from callers on its AuthorizationPolicy allow list, then checks permissions in its own data.
5. Tokens are never logged and never sent outside the cluster.

## Claims we use (and only these)
| Claim | Used for | If wrong |
|---|---|---|
| `iss` | must be exactly `https://auth.<domain>/realms/webblock` | 401 `UNAUTHENTICATED` |
| `aud` | must include this service (table below) | 401 |
| `typ` | must be `Bearer` (stops an ID token being used on an API) | 401 |
| `exp`, `iat` | token lifetime | 401 |
| `user_id` | who the user is | 403 `ACCESS_DENIED` (signed in but not set up) |
| `roles` | `USER` for everyone, `ADMIN` for staff | 403 on admin endpoints |
Ignore everything else (`sub`, `azp`, `sid`, `jti`, `email`, `name`, `preferred_username`, `realm_access`, `scope`, an `account` audience).
Do not use `sub` as the user's ID; a service may store it only in an audit column (Provisioning: `requested_by_subject`).

## Audience per service
| Service | `aud` |
|---|---|
| project | `project` |
| provisioning | `provisioning` |
| payment | `payment` |
| user-service (Account) | `webblock-account` (NOT `account`: Keycloak already puts `account` in every token) |
| AI | `ai-codegen` |
| notification | `notification` |
| website portals | `site` (a portal token must never work on our APIs) |

## Who signs users in
- Web app: the **Account** service runs the sign-in (code + PKCE with Keycloak) and keeps tokens in Redis; the browser only gets a
  session cookie. The Next.js server asks Account for the session's access token. It never talks to Keycloak.
- Mobile app: signs in with Keycloak directly (client `webblock-mobile`, system browser).
- Google sign-in gives the same token. Our services never see a Google token.

## Checks every endpoint must make
- **Owner endpoint:** load the resource and compare its owner with the current user. Not the owner = **404** (same body as missing).
- **Admin endpoint:** `ADMIN` role, else 403. Admin accounts are never created by public registration.
- **Internal/gRPC method:** only the allowed callers; fail closed.
- Never put the check only in the frontend or the gateway.

## Tests
No token -> 401; wrong `aud` -> 401; expired -> 401; `alg: none` / HS256 token -> 401; USER on admin endpoint -> 403;
owner A reading owner B's resource -> 404 with the same body as a missing one.
