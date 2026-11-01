# Authentication contract (Keycloak)

Status: **approved by the owner**. From the platform (Project, Provisioning, DevOps) to the
authentication team. It says what the Keycloak realm must provide so that the platform can sign users in and the
services can trust the tokens. How the cluster uses it (gateway, proxies, policies) is the DevOps team's part and is
not in this document.

Words used here: **must** is required; a value in `<angle brackets>` is filled in by the team named next to it.
`<domain>` is the platform's domain, given by DevOps.

## 1. What you build, in one list

1. One realm, `webblock`.
2. Two realm roles: `USER` and `ADMIN`.
3. A `user_id` attribute on every user, carried in every access token.
4. Three clients: `webblock-web` (web sign-in, used by the Account service), `webblock-mobile` (mobile app),
   `site-portal` (website portals). The old client `webblock-ui` is retired: delete it if it exists.
5. The token settings in section 6.
6. The hand-over in section 10: the `webblock-web` secret goes to the Account team.

Section 7 (hostnames) is for your information: DevOps runs the Keycloak server and sets it up.

There are **no service-account clients**: services do not get tokens from Keycloak to call each other.
`webblock-web` has a secret, but only to sign users in; its service account is off.

How the platform uses the tokens, in short:

- The Istio gateway checks every token first, and refuses a request without a token on user-facing routes. Then the
  **first service** (the one the gateway sends the request to) checks the token itself again.
- Calls between services carry **no token**. They carry the `user_id`, and the callee trusts it only from callers
  that its Istio `AuthorizationPolicy` allows (mTLS identity). Background work (events, workers, builds) works the
  same way.
- Tokens are never logged and never sent to outside services (Knock, the LLM, Gitea, Jenkins).

## 2. Roles

| Role | Who has it | What it allows |
| --- | --- | --- |
| `USER` | **Every** user, automatically (realm default role) | Use the platform: create and edit their own websites, deploy them, open their portals |
| `ADMIN` | Platform staff, assigned by hand | Everything a user can do, plus operator actions (for example rolling back a website's release) |

- An admin also holds `USER` (it is the default role for everyone).
- Website ownership is **not** a role. Which websites a user may manage is decided by the Project service (the owner
  of a website is the user who created it). Do not create roles or groups per website.
- A user who is not the owner of a project, website or portal gets **404**, not 403, so they learn nothing about it.
- No other roles are needed. Keycloak's built-in roles (`offline_access`, `uma_authorization`) may stay.
- Role names are **case-sensitive** and must be exactly `USER` and `ADMIN`: the services compare them letter by
  letter.

## 3. The `user_id` attribute

Every user has the attribute `user_id`: the user's ID in the platform's `account.users` table, a UUID written in lower
case with hyphens (for example `4f6c1e9a-2b7d-4c3e-9a51-0d8e7f6a5b42`).

Rules:

- **Set before the first token is issued**, by every way a user can be created: registration, first login with an
  external provider (for example Google), and creation by an admin.
- **Never changes** and is **unique**: one Keycloak user, one `account.users` row.
- **Only admins (and the Account service through the Admin API) can write it.** The user must not be able to see or
  edit it in the account console: in *Realm settings → User profile*, attribute `user_id`, *Who can edit* = Admin
  only, *Who can view* = Admin only. A user who could edit it could become another user.
- A token without `user_id` is refused by the platform (403): the user can sign in but can do nothing.

## 4. Clients

| Setting | `webblock-web` | `webblock-mobile` | `site-portal` |
| --- | --- | --- | --- |
| Used by | The **Account service**, for web sign-in (see below) | The mobile app | The portal sign-in proxy (DevOps) |
| Client type | **Confidential** (secret held only by Account) | Public | **Confidential** (has a secret) |
| Flow | Authorization code + PKCE (`S256` required) | Same, in the **system browser** (AppAuth) | Authorization code |
| Disabled flows | Implicit, direct access grants (password), service account, device | Same | Same |
| Redirect URIs (exact) | Account's callback | `<mobile app link>` | `https://auth.<SITE_DOMAIN>/oauth2/callback` |
| Post-logout redirect URIs | `https://app.<domain>/` | `<mobile app link>` | `https://auth.<SITE_DOMAIN>/` |
| Web origins | **none** (Account exchanges the code on its server) | none | none |
| Audience (`aud`) in its access token | The six platform audiences | The six platform audiences | `site` **only** |
| Client scopes | `webblock-identity` (section 5) | `webblock-identity` | `webblock-identity` |

`<SITE_DOMAIN>` (`sites.<domain>`), `<domain>` and the URLs are given by DevOps (section 10). Every redirect URI is
exact (no wildcard). Account's callback is `https://app.<domain>/account/auth/callback`: the gateway routes this path
on `app.<domain>` to Account, and the Account team confirms the path. `<mobile app link>` is the mobile app's
redirect: preferably a claimed HTTPS app link (Android App Links, iOS Universal Links), as RFC 8252 advises.

The six platform audiences: `project`, `provisioning`, `payment`, `webblock-account`, `ai-codegen`, `notification`.

Why the audiences differ: a token is only accepted by the services named in its `aud`. A portal token (`site`) must
never work on the platform's own APIs. The web app (admin pages included) and the mobile app call the platform's
services, and any of the six can be the first service a request reaches, so their tokens carry all six.

| Audience | Service |
| --- | --- |
| `project` | Project |
| `provisioning` | Provisioning |
| `payment` | Payment |
| `webblock-account` | Account. Not `account`: Keycloak already puts `account` in every token for its own account console, so that name would let every token in |
| `ai-codegen` | AI (the design assistant in the editor) |
| `notification` | Notification |
| `site` | The website portal proxy |

Why `webblock-web` is confidential (owner's decision): it is used by the Account service, which runs
on a server and can keep a secret. A stolen code or refresh token is useless without the secret. The secret lives only
in Account's Kubernetes Secret `account-keycloak-client`, created by hand (never in Git).

How web sign-in works (for your information; the Account team builds it, and every step here is required):

1. **Start.** The browser goes to Account on `app.<domain>`. Account makes a random `state`, a random `nonce` and a
   PKCE verifier. The verifier stays on Account's server. `state` and `nonce` are bound to a short-lived pre-login
   cookie (a few minutes) and are used once. Account sends the browser to Keycloak with the `S256` code challenge.
2. **Sign-in.** The user signs in at Keycloak (with a password or with Google: Keycloak brokers Google).
3. **Callback.** Keycloak sends the browser to `https://app.<domain>/account/auth/callback`. Account checks `state`
   against the pre-login cookie and deletes that cookie, exchanges the code with its secret and the PKCE verifier,
   and checks the `nonce` in the ID token.
4. **Session.** Account makes a **new** random session ID at the callback (never one from before sign-in, so no
   session fixation). It keeps the tokens in Redis under that ID, with a TTL of 30 minutes idle and 10 hours max
   (as Keycloak's SSO session, section 6). Redis requires a password and TLS.
5. **Cookie.** The browser gets only a session cookie named with the `__Host-` prefix: Secure, HttpOnly, `Path=/`,
   no `Domain` (so it is sent to `app.<domain>` only), SameSite=Lax. No token reaches the browser.
6. **CSRF.** User websites stay on our own sub-domain `*.sites.<domain>`, which counts as the same site as
   `app.<domain>`, so SameSite=Lax alone does not stop them. Every state-changing request, and logout, is refused
   unless its `Origin` (or `Sec-Fetch-Site`) shows it came from `https://app.<domain>`. This check is permanent.
7. **Refresh.** Account refreshes the tokens about 60 s before the access token expires, one refresh at a time per
   session (a Redis lock), so it never sends the same refresh token twice (rotation stays on, section 6).
8. **Calls.** The Next.js server never talks to Keycloak. It asks Account for the session's current access token
   and calls the services with `Authorization: Bearer <token>`. Account hands out a token with at least 60 s left.
   That Account endpoint is internal only: never routed through the gateway, and an Istio `AuthorizationPolicy`
   allows only the Next.js server's mTLS identity to call it.
9. **Logout.** Account deletes the Redis session, revokes the refresh token at Keycloak (server side), clears the
   cookie, and sends the browser to Keycloak's logout with `id_token_hint` and the post-logout URI. When a Keycloak
   session ends elsewhere (an admin, another device), Keycloak calls Account's backchannel logout URL and Account
   deletes the matching sessions.

Why `webblock-mobile` stays public (owner's decision): a phone app cannot keep a secret. It signs in
directly with Keycloak, in the system browser (never a WebView) with PKCE (the native-app standard, RFC 8252). It
does not go through Account.

## 5. Claims in the access token

Create one client scope, **`webblock-identity`**, assigned as a **default** scope to the three clients, with two
mappers:

| Mapper | Type | Settings |
| --- | --- | --- |
| `user_id` | User Attribute | user attribute `user_id` → token claim `user_id`, JSON type String, added to the **access token** and userinfo |
| `roles` | User Realm Role | token claim `roles`, **multivalued**, JSON type String, added to the access token |

Plus one **Audience** mapper per audience on each client, as in section 4 (access token only).

A decoded access token from `webblock-web`:

```json
{
  "iss": "https://auth.<domain>/realms/webblock",
  "aud": ["project", "provisioning", "payment", "webblock-account", "ai-codegen", "notification"],
  "azp": "webblock-web",
  "typ": "Bearer",
  "sub": "8a1d5e2c-7b3f-4e8a-9c21-3f4b5a6d7e8f",
  "user_id": "4f6c1e9a-2b7d-4c3e-9a51-0d8e7f6a5b42",
  "roles": ["default-roles-webblock", "USER", "offline_access", "uma_authorization"],
  "exp": 1792195500,
  "iat": 1792195200,
  "jti": "…",
  "sid": "…"
}
```

| Claim | What it is | Who uses it |
| --- | --- | --- |
| `iss` | The realm that issued the token | Gateway and first service: must be `https://auth.<domain>/realms/webblock` |
| `aud` | The services this token is meant for | Gateway: one of the six. First service: its own audience (section 4) |
| `azp` | The client the user signed in with | Logs only |
| `typ` | `Bearer` for access tokens, `ID` for ID tokens | The first service accepts only `Bearer` (ID tokens refused) |
| `sub` | Keycloak's own ID for the user | Logs only. The platform does **not** use it as the user's identity |
| `user_id` | The user's platform ID (`account.users.id`) | **The** identity; inner calls carry it |
| `roles` | The user's realm roles | A service with admin actions (Provisioning today): `ADMIN` allows them |
| `exp`, `iat` | Expiry and issue time (seconds since 1970) | Gateway and first service: refuse an expired token |
| `jti`, `sid` | Token ID and session ID | Logs only |

Who checks the token (for your information; DevOps and the service teams build it):

- **Gateway.** `RequestAuthentication` checks the signature, `iss`, `aud` and `exp`. On its own it lets a request
  with no token through, so an `AuthorizationPolicy` requiring `requestPrincipals: ["*"]` covers every user-facing
  route.
- **First service.** The service the gateway sends the request to checks the token again itself (signature, `iss`,
  its own `aud`, `exp`, `typ`, `user_id`).
- **Inner calls** (gRPC) carry **no token**. They carry the `user_id` in `x-user-id` (and `x-request-id`,
  `traceparent`). The callee trusts the `user_id` only from callers its Istio `AuthorizationPolicy` allows (mTLS
  identity), then checks the user's permissions in its own data. These are internal paths, never user-facing routes.
- **Background work** (events, workers, builds) works the same way: mTLS identity, and the `user_id` when there is a
  user. So long work never depends on a 5-minute token.
- **Tokens** are never logged and never sent to outside services (Knock, the LLM, Gitea, Jenkins).
- The shared library `web-block-commons` (a Java starter and a Python package, delivered through the GitLab Package
  Registry; design in review) will do this the same way in every service.

Other claims Keycloak adds by default (`email`, `name`, `preferred_username`, `realm_access`, `scope`, an extra
`account` audience, …) are allowed and ignored. Do not rename or remove the claims in the table.

## 6. Realm and token settings

| Setting | Value | Why |
| --- | --- | --- |
| Signing algorithm | **RS256** (realm default), RSA key of at least 2048 bits | The services accept RS256 only |
| Key rotation | Allowed: add the new key first, keep the old one active until tokens signed with it have expired (≥ 10 min) | Services pick the key by `kid` from the key set |
| Access token lifespan | **5 minutes** | Short: a stolen token is useless quickly; logout reaches the portals within 5 minutes |
| SSO session idle / max | **30 minutes / 10 hours** | How long a user stays signed in |
| Refresh tokens | Rotation on: *Revoke refresh token* = on, *Max reuse* = 0 | A stolen one works once (see below) |
| Access token type (`typ`) | `Bearer` (default; do not change) | See `typ` above |
| Required actions, password policy, brute-force protection, external providers (Google) | Your choice | Not part of this contract, as long as section 3 holds for every user |

Why *Max reuse* = 0 works for the web: Account refreshes one session at a time (a Redis lock, section 4), so it never
sends the same refresh token twice. Without the lock, two refreshes at once would look like a stolen token to
Keycloak, and the user would be signed out.

## 7. Hostnames (for your information)

**Nothing to build here.** DevOps runs the Keycloak Deployment (the server, its hostnames and its proxy settings); you
configure the `webblock` realm only. This section tells you where things are.

| Host | What is reachable there | Who |
| --- | --- | --- |
| `https://auth.<domain>` | Only `/realms/webblock/…`, `/resources/…` and `/js/…`: the `webblock` sign-in pages, its OpenID Connect endpoints (section 8), its account console. Everything else answers 404, **the master realm** (`/realms/master/…`) and `/admin` included | Everyone: browsers, phones, the platform's servers |
| `https://kc-admin.<domain>` | The admin console, with the master realm's sign-in | The team only, behind Cloudflare Access: sign in with GitHub or Google, or with a one-time PIN sent by e-mail |

So a realm other than `webblock`, and the master realm in particular, is never reachable from the internet: use
`kc-admin.<domain>` for all admin work.

DevOps runs the server with these settings (hostname options v2; the names are checked for the installed Keycloak
version):

```text
KC_HOSTNAME=https://auth.<domain>
KC_HOSTNAME_ADMIN=https://kc-admin.<domain>
KC_PROXY_HEADERS=xforwarded
```

The issuer is therefore always `https://auth.<domain>/realms/webblock`, whichever host a request came through. The
one-time PIN stays as a way into `kc-admin.<domain>` even if Keycloak itself later becomes a sign-in method for
Cloudflare Access, so that a Keycloak outage never locks the team out of Keycloak's own admin console.

## 8. Endpoints

All are Keycloak's standard OpenID Connect endpoints under `https://auth.<domain>/realms/webblock`:

| Endpoint | Path (after the realm URL) | Used by |
| --- | --- | --- |
| Discovery | `/.well-known/openid-configuration` | Every component, to find the others |
| Key set (JWKS) | `/protocol/openid-connect/certs` | Every service and the gateway, to check token signatures |
| Authorization | `/protocol/openid-connect/auth` | Browser (sent by Account), mobile app, portal proxy: start sign-in |
| Token | `/protocol/openid-connect/token` | Account, mobile app, portal proxy: exchange the code, refresh |
| Revocation | `/protocol/openid-connect/revoke` | Account: revoke the refresh token at logout |
| Logout | `/protocol/openid-connect/logout` | Account (sends the browser, `id_token_hint`), mobile app: sign out |
| Userinfo | `/protocol/openid-connect/userinfo` | Optional (Account may read the user's name) |

All must be reachable over **HTTPS** with a certificate from a public certificate authority, from the users'
browsers and phones and from inside the cluster. The key set must answer quickly (services fetch and cache it).

No custom endpoint is needed from the authentication team. Keycloak calls Account's backchannel logout URL (section 9).

## 9. Realm configuration (import file)

A partial realm export with everything above. Import it in *Realm settings → Action → Partial import* (or create the
realm from it), then check each item against sections 2 to 6 in the admin console: export formats change between
Keycloak versions. Replace every `<…>`.

Keycloak 25 and later put `sub` in the token through a client scope named `basic`, so the file lists `basic` in every
client's default scopes. On an older version, remove `basic` (there `sub` is always present). Check the installed
version with DevOps.

```json
{
  "realm": "webblock",
  "enabled": true,
  "sslRequired": "external",
  "defaultSignatureAlgorithm": "RS256",
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 36000,
  "revokeRefreshToken": true,
  "refreshTokenMaxReuse": 0,
  "roles": {
    "realm": [
      { "name": "USER", "description": "Every user of the platform" },
      { "name": "ADMIN", "description": "Platform staff: operator actions" },
      { "name": "default-roles-webblock", "composite": true,
        "composites": { "realm": ["USER", "offline_access", "uma_authorization"] } }
    ]
  },
  "defaultRole": { "name": "default-roles-webblock" },
  "clientScopes": [
    {
      "name": "webblock-identity",
      "protocol": "openid-connect",
      "attributes": { "include.in.token.scope": "false" },
      "protocolMappers": [
        { "name": "user_id", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
          "config": { "user.attribute": "user_id", "claim.name": "user_id", "jsonType.label": "String",
                      "access.token.claim": "true", "id.token.claim": "false", "userinfo.token.claim": "true" } },
        { "name": "roles", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-realm-role-mapper",
          "config": { "claim.name": "roles", "multivalued": "true", "jsonType.label": "String",
                      "access.token.claim": "true", "id.token.claim": "false", "userinfo.token.claim": "false" } }
      ]
    }
  ],
  "clients": [
    {
      "clientId": "webblock-web",
      "publicClient": false,
      "clientAuthenticatorType": "client-secret",
      "secret": "<webblock-web secret: regenerate after import, see below>",
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "redirectUris": ["https://app.<domain>/account/auth/callback"],
      "webOrigins": [],
      "frontchannelLogout": false,
      "attributes": { "pkce.code.challenge.method": "S256",
                      "post.logout.redirect.uris": "https://app.<domain>/",
                      "backchannel.logout.url": "<Account backchannel logout URL>",
                      "backchannel.logout.session.required": "true" },
      "defaultClientScopes": ["webblock-identity", "basic", "profile", "email"],
      "protocolMappers": [
        { "name": "aud-project", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "project",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-provisioning", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "provisioning",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-payment", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "payment",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-webblock-account", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "webblock-account",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-ai-codegen", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "ai-codegen",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-notification", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "notification",
                      "access.token.claim": "true", "id.token.claim": "false" } }
      ]
    },
    {
      "clientId": "webblock-mobile",
      "publicClient": true,
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "redirectUris": ["<mobile app link>"],
      "attributes": { "pkce.code.challenge.method": "S256",
                      "post.logout.redirect.uris": "<mobile app link>" },
      "defaultClientScopes": ["webblock-identity", "basic", "profile", "email"],
      "protocolMappers": [
        { "name": "aud-project", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "project",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-provisioning", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "provisioning",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-payment", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "payment",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-webblock-account", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "webblock-account",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-ai-codegen", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "ai-codegen",
                      "access.token.claim": "true", "id.token.claim": "false" } },
        { "name": "aud-notification", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "notification",
                      "access.token.claim": "true", "id.token.claim": "false" } }
      ]
    },
    {
      "clientId": "site-portal",
      "publicClient": false,
      "clientAuthenticatorType": "client-secret",
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "redirectUris": ["https://auth.<SITE_DOMAIN>/oauth2/callback"],
      "attributes": { "post.logout.redirect.uris": "https://auth.<SITE_DOMAIN>/" },
      "defaultClientScopes": ["webblock-identity", "basic", "profile", "email"],
      "protocolMappers": [
        { "name": "aud-site", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
          "config": { "included.custom.audience": "site", "access.token.claim": "true", "id.token.claim": "false" } }
      ]
    }
  ]
}
```

The `user_id` attribute's permissions (section 3) are set in *Realm settings → User profile*, not in this file.

The `webblock-web` secret in the file is only a placeholder. After the import, open *Clients → webblock-web →
Credentials*, press *Regenerate*, and send the new secret as in section 10. Never put a real secret in this file.

## 10. Hand-over to DevOps and the Account team

When the realm is ready, send these to the DevOps team and the Account team. Send each secret through the team's
secret store or another private channel, **never** in chat, e-mail or a repository:

| Item | To | Example |
| --- | --- | --- |
| Issuer (confirm) | DevOps, Account | `https://auth.<domain>/realms/webblock` |
| Key set URL (confirm) | DevOps, Account | `https://auth.<domain>/realms/webblock/protocol/openid-connect/certs` |
| `site-portal` client secret | DevOps | (private channel) |
| `webblock-web` client secret | **Account team only** | (private channel), for the Secret `account-keycloak-client` |
| Two test users | DevOps, Account | one with `USER` only, one with `ADMIN`, both with a `user_id` |

DevOps sends you, before you start: `<domain>` (so `auth.<domain>`, `kc-admin.<domain>`, `app.<domain>` and
`api.<domain>`), `<SITE_DOMAIN>`, `<mobile app link>`, the installed Keycloak version, and your access to
`kc-admin.<domain>` through Cloudflare Access. The Account team confirms its callback path (`/account/auth/callback`)
and gives you `<Account backchannel logout URL>`.

## 11. How we check it together

Account keeps the web tokens on its server, so read them in the admin console instead of the browser: *Clients →
(client) → Client scopes → Evaluate*, pick the user, *Generated access token*.

1. For the test user and `webblock-web`: `iss`, `aud` (`project`, `provisioning`, `payment`, `webblock-account`,
   `ai-codegen`, `notification`), `typ: Bearer`, `user_id`, `roles`
   contains `USER`, `exp` 5 minutes after `iat`.
2. Same for the admin: `roles` also contains `ADMIN`.
3. `site-portal`: `aud` is `site` and nothing else.
4. `webblock-mobile`: `aud` has the same six audiences as `webblock-web`.
5. As the test user, open the account console: `user_id` is neither visible nor editable.
6. Try the password grant (`grant_type=password`) on each client: refused.
7. Use a refresh token twice: the second use is refused.
8. Sign in to the web app, then open a website portal: no second login screen.
9. `https://auth.<domain>/admin/` and `https://auth.<domain>/realms/master/.well-known/openid-configuration` answer
   404; `https://auth.<domain>/realms/webblock/.well-known/openid-configuration` answers 200;
   `https://kc-admin.<domain>` asks for the Cloudflare Access sign-in, then shows the admin console.
10. `webblock-web` is confidential (*Client authentication* on) and has no web origin. Its only redirect URI is
    Account's callback, `https://app.<domain>/account/auth/callback`. A code exchange without the secret is refused.
11. There is no `webblock-ui` client.
12. If Google sign-in is set up, sign in to the web app with Google: it works (Keycloak brokers it), with a `user_id`.
13. Sign out of the web app, then use the old refresh token: refused.
14. End the user's session in the admin console: Account's session for that user ends too (backchannel logout).
15. Start a sign-in without `code_challenge` on `webblock-web` and on `webblock-mobile`: refused.
16. The access token has `sub` (on Keycloak 25 and later this needs the `basic` scope).
