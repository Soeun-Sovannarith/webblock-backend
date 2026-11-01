# Runbook: bearer-JWT identity

The API verifies every bearer token on `/v1/` and `/mcp` with one middleware. `IDENTITY_MODE` chooses the verifier:

| Profile | Accepted modes |
| --- | --- |
| `prototype` | `dev-tokens` (the static token file, a stand-in) or `jwt` |
| `production` | `jwt` only; `dev-tokens` is refused with "must be jwt in production" |

The prototype stack (`scripts/prototype/run-provisioning.sh`) keeps `dev-tokens`. With `jwt`, the token check is the
shared library `web-block-commons` (decision `0024`); Provisioning adds the checks the library does not make.

## Keys of `IDENTITY_MODE=jwt`

Every key is required and has no default (OD-15).

- `AUTH_ISSUER`: the realm URL. `iss` must equal it exactly, and the key set comes from its discovery document. An
  absolute `https` URL with a host; no credentials, fragment, whitespace or control characters; at most 2048 bytes.
- `AUTH_AUDIENCE`: must be `aud`, or one whole element of it. 1 to 512 bytes, no control characters, no surrounding
  whitespace.
- `AUTH_MAX_TOKEN_LIFETIME`: the longest `exp` minus `iat`, and the longest `exp` minus now (plus the 30 s skew).
  `1s` to `24h`.
- `AUTH_ACTION_SCOPES` (both modes): the one role each action requires, as `action=role` pairs for every action.

The library fixes the rest, so these former keys are refused by name in every profile and mode
(`must be unset: web-block-commons fixes it (decision 0024 G7)`): `AUTH_JWKS_URL`, `AUTH_ALLOWED_ALGS`,
`AUTH_CLOCK_SKEW`, `AUTH_SCOPE_CLAIM`, `IDENTITY_USER_ID_CLAIM`, `AUTH_JWKS_FETCH_TIMEOUT`, `AUTH_JWKS_MAX_BYTES`,
`AUTH_JWKS_REFRESH_INTERVAL`, `AUTH_JWKS_MIN_REFRESH_INTERVAL`, `AUTH_JWKS_MAX_STALE`.

With `dev-tokens`, each `jwt` key above except `AUTH_ACTION_SCOPES` is refused by name when it is set; with `jwt`,
`AUTH_DEV_TOKENS_FILE` is. No key names a CA file, an insecure flag or a proxy: the library follows no redirect and
ignores the proxy variables of the environment, and Provisioning hands it the platform's roots
(`infrastructure/tls.py`, decision `0024` G4a).

The platform's roots are the CA file and directory OpenSSL was built with (`openssl_cafile` and `openssl_capath` of
Python's `ssl.get_default_verify_paths()`, for example `/etc/ssl/cert.pem` and `/etc/ssl/certs`), loaded by name, so
`SSL_CERT_FILE` and `SSL_CERT_DIR` change nothing; `SSLKEYLOGFILE` is ignored too. The image must therefore ship its CA
bundle at OpenSSL's built-in path. When neither the file nor the directory exists, the API refuses to start
(`tls: the platform has no certificate roots`). To see the paths in an image:
`python -c 'import ssl; print(ssl.get_default_verify_paths())'`. An issuer behind a private CA cannot be trusted this
way; that needs a recorded decision before a key is added.

## Production example (Keycloak)

The owner's authentication design (the Project service's spec
`docs/superpowers/specs/2026-10-16-authentication-design.md`, sections 3 and 5.2, in its git history before commit
`2782c64`; its current rules are in `../project-service/.claude/docs/contracts/authentication-contract.md`) fixes the
issuer, the audience and the permissions. Keycloak's realm `webblock` puts two realm roles in a flat list claim `roles`:
`USER` (every user, by default) and `ADMIN` (platform staff), case-sensitive. Provisioning reads the roles as its
scopes. Five actions need `ADMIN`: the operator rollback and the four admin actions (`suspend_website`,
`resume_website`, `redeploy_release`, `admin_list_commands`, decision `0023` G10); the other nine need `USER`. The
other action permissions are configuration only. One role name is in code: `ADMIN` (`ADMIN_ROLE`,
`core/application/shared/action.py`). The five admin-only actions require it whatever scope the configuration maps
them to (rollback: FR-50): a token without `ADMIN` is refused 403 `ACCESS_DENIED` on them before any project is looked
at, and its holder takes them on any project without the project check (decision `0019` section 7, below). Production
refuses an `AUTH_ACTION_SCOPES` that maps one of them to anything but `ADMIN`.

```sh
PROVISIONING_PROFILE=production
IDENTITY_MODE=jwt
AUTH_ISSUER=https://auth.<domain>/realms/webblock
AUTH_AUDIENCE=provisioning
AUTH_ACTION_SCOPES=deploy_release=USER,promote_release=USER,rollback_release=ADMIN,build_release=USER,get_release=USER,get_command=USER,get_deployment=USER,get_release_diagnostics=USER,list_releases=USER,list_commands=USER,suspend_website=ADMIN,resume_website=ADMIN,redeploy_release=ADMIN,admin_list_commands=ADMIN
PROJECT_ACCESS_MODE=project-service
PROJECT_SERVICE_URL=http://<project-service>.<project-namespace>.svc.cluster.local:<port>
```

The roles are the top-level `roles` claim only, a list of text (the library, TR-27); `realm_access` is never read.
Because the `ADMIN` role skips the project owner check for the admin actions, two rules hold on the Keycloak side:

- The `roles` claim must carry realm roles only. No client-role or client-scope mapper may write into `roles`.
- No service account (a client-credentials client) may hold `ADMIN`. Only platform staff hold it, and an `ADMIN` token
  is a high-value credential: keep its lifetime short (`AUTH_MAX_TOKEN_LIFETIME`).

`AUTH_MAX_TOKEN_LIFETIME` is still to be chosen; the access token lives 5 minutes. A unit test reads this block and
loads every line of it through the real configuration loader (`test_the_runbooks_production_example_loads` in
`tests/unit/infrastructure/config/test_api.py`). It replaces each `<...>` placeholder with a test value, takes only
`AUTH_MAX_TOKEN_LIFETIME` and the sections this example does not cover (listeners, database, limits) from its
fixtures, and compares the loaded issuer, audience, roles, mode and URL. A missing or wrong line here fails it; a new
placeholder needs a test value in `RUNBOOK_PLACEHOLDERS`.

Production may use `PROJECT_ACCESS_MODE=project-service` because the call carries no token (design decision A3). Inside
the Istio mesh the API is its mTLS identity, and the Project service's sidecar admits Provisioning's API ServiceAccount
on `POST /internal/v1/access-checks`, and also the `wb-*` site namespaces (spec section 4.5: the sites' portals ask the
same check, and any site may ask about any project, as section 8 accepts). `PROJECT_SERVICE_URL` is a plain in-cluster
`http://` URL; the sidecar adds mTLS. Production accepts `http://` only when the host ends in `.svc` or
`.svc.cluster.local` (any case, a trailing dot allowed); any other host needs `https://`. For any scheme it refuses a
host with any non-ASCII character (the HTTP client maps the full stops U+3002, U+FF0E and U+FF61 to `.` before it
connects; write an internationalised name in its `xn--` form), loopback and unspecified addresses (`127.0.0.0/8`, `::1`,
`0.0.0.0`, `::`, and their IPv4-mapped forms); `localhost` and names under it, `ip6-localhost`, `ip6-loopback` and
`localhost.localdomain`; a host made only of decimal, octal or hex numbers that is not a canonical IPv4 literal
(`127.1`, `2130706433`, `0x7f000001`); an IPv6 literal with a zone ID; and the RFC 2606 placeholders `invalid` and
`test`, as names or as suffixes. This is the rule of the amended spec section 5.4, which the Project service's
`ProfileGuard` applies too. The refusal never repeats the URL. `PROJECT_SERVICE_TOKEN_FILE` no longer exists and is
refused by name when set. Without the mode, production denies every project access (the closed adapter).

The role is checked before the project. The Project service answers for owners only, so an admin who does not own a
project would be denied by it. Decision `0019` section 7 (OD-45) is the operator path: for `rollback_release`, a token
whose `roles` claim holds `ADMIN` skips the Project service's check, so an admin rolls back any project, owner or not
(`OperationGate.check` in `core/application/operations/gate.py`). The rules:

- Only `ADMIN`, exactly and case-sensitively, counts. `admin`, `Admin`, `USER` or any other role never skips the check.
  Such a token is refused 403 `ACCESS_DENIED` on a rollback, before the project is asked, whatever scope
  `AUTH_ACTION_SCOPES` maps `rollback_release` to (FR-50).
- The four admin actions under `/v1/admin/projects/{projectId}` (decision `0023` G9, G10) never ask the Project service
  either: an `ADMIN` takes them on any project.
- Every other action of an admin (the client reads, deploy, promote, `build_release`) still needs the Project service's
  answer.
- So the `202`'s status URL does not work for an admin who does not own the project: `get_command` and `get_deployment`
  answer 404 (security review finding M-4). The admin follows the command through the admin read
  `GET /v1/admin/projects/{projectId}/commands` (`admin_list_commands`), which shows each command with its
  `requestedBySubject`, or through the logs (the audit event, `actor_kind=operator`).
- The accepted command's `requested_by_subject` is the admin's `sub`, and the mutation's audit event names the same
  subject. No database change.
- Production still refuses every mutation, the rollback and the three admin mutations included (503
  `OPERATION_UNAVAILABLE`), until a later step enables them; it serves the admin read (decision `0023` G13). The admin's
  suspend and resume (`/hosting/suspend`, `/hosting/resume`) add or remove only the offline reason `admin_suspended`;
  every other suspend or resume comes from Payment's and Project's events.

## What a token must be

The library checks (decision `0024` G4; `webblock_commons.core.token` and `verifier`); pyjwt checks the signature alone:

- one `Authorization` header with the `Bearer` scheme; at most 8192 bytes; three base64url parts; no `jku`, `jwk`,
  `x5u`, `x5c`, `crit`, `zip` or `enc` header; JSON with no repeated member;
- `alg` exactly `RS256`; a `kid` that names a key of the issuer's key set;
- `iss` exactly `AUTH_ISSUER`; `aud` holds `provisioning` (`AUTH_AUDIENCE`) whole; `typ` exactly `Bearer`;
- `exp` present; `exp` and `nbf` within a 30 s skew.

Provisioning then checks (G5, `infrastructure/identity/token.py`), with the same skew:

- `iat` not in the future; `exp` after `iat` and `nbf`; the lifetime under `AUTH_MAX_TOKEN_LIFETIME`;
- `sub` present and a valid requester (1 to 255 bytes, no control characters), because it is stored with every accepted
  command.

Last, the user (G6, FR-2): `user_id` must be a canonical lowercase, non-nil UUID. A token that passes every check above
but has no valid `user_id` is refused 403 `ACCESS_DENIED` on every route, `/mcp` included, before any store or Project
call. Roles are compared whole, never as substrings.

## Answers

Every answer below is an RFC 9457 Problem (decision `0018`): `application/problem+json`, or `application/json` for a
client whose `Accept` allows only that, which includes MCP clients at `/mcp`; its `code` is the one shown.

| Situation | HTTP and `/mcp` |
| --- | --- |
| Any refused token | 401 `UNAUTHENTICATED`, one title for every cause |
| Valid token without a valid `user_id` (FR-2.2) | 403 `ACCESS_DENIED`, before any store or Project call |
| Valid token without the action's role | 403 `ACCESS_DENIED` (1) |
| Valid token without `ADMIN` on an admin action (`/v1/admin/...`) | 403 `ACCESS_DENIED` (2) |
| No key set young enough (3) | 503 `IDENTITY_UNAVAILABLE`, never 401 |

1. The REST status; an MCP tool result is marked as an error with the same Problem, without `instance`.
2. Decision `0023` G10: whatever the token's roles, before any store or Project call; the same body for every project.
3. The discovery document or the key set is down, refusing, redirecting, oversized or invalid, and nothing was cached
   within 24 hours.

## The key set

The library's rules (decision `0024` G4):

- Nothing is fetched at start-up. The first request fetches, so the API starts with the issuer down and answers 503
  until keys arrive.
- The key set URL is the `jwks_uri` of `<AUTH_ISSUER>/.well-known/openid-configuration`, whose `issuer` must equal
  `AUTH_ISSUER` and whose `jwks_uri` must be on the issuer's scheme, host and port.
- A set younger than 10 minutes is used as is. An older one, or an unknown `kid`, causes a fetch, at most one per
  30 s. Concurrent requests wait for the one fetch.
- One fetch takes at most 5 s (2 s to connect) and 1 MiB. A fetched set replaces the old one whole; a failed fetch
  keeps the old set, usable for 24 hours.
- Only RSA keys of at least 2048 bits for `RS256` signatures are used. A set with private key material or a repeated
  `kid` is refused whole.

## Logs

The library logs on the standard logger `webblock_commons`; the API sends those records through its own logger
(`infrastructure/identity/log_bridge.py`, G8). The identity's lines have `component=identity`:

- `token refused` with `auth_cause` (for example `expired`, `issuer_mismatch`, `typ_mismatch`, `unknown_kid`,
  `alg_not_allowed`, `sub_missing`, `lifetime_exceeded`);
- `no user id` with `auth_cause=user_id_missing` (403);
- `jwks fetch failed` with `auth_cause` (`fetch_status`, `fetch_too_large`, `fetch_encoding`, `discovery_issuer`,
  `discovery_jwks_uri`, `jwks_invalid`, `jwks_timeout`, `jwks_transport`);
- `identity keys unavailable` when a request got 503.

`provisioning_identity_refusals_total` and `provisioning_identity_key_fetch_failures_total` count them by cause. No
token, claim value, key or URL is ever logged.

## Checking a deployment

- Start the API with `PROVISIONING_PROFILE=production`, `IDENTITY_MODE=jwt` and the keys above. It starts even if the
  issuer is unreachable.
- A request with a token from the real issuer that lacks a mutation's role gets 403. With the role, a mutation gets
  503 `OPERATION_UNAVAILABLE`, because production enables no mutation yet. A read gets 404 without
  `PROJECT_ACCESS_MODE`. With `project-service` the Project service decides it: 200 for the owner, 404 otherwise, 503
  `PROJECT_ACCESS_UNAVAILABLE` when it cannot be reached. A token without `user_id` gets 403 everywhere.
- If every request gets 503 `IDENTITY_UNAVAILABLE`, look for `jwks fetch failed` and its `auth_cause`.
- In the mesh the API reaches Keycloak through its public URL `https://auth.<domain>` (amended spec D8), and the mesh
  allows only registered external hosts (`outboundTrafficPolicy: REGISTRY_ONLY`, D9). The API's namespace therefore
  needs the `ServiceEntry` for `auth.<domain>` (spec section 4.1); without it no key set can be fetched, and every
  request gets 503 `IDENTITY_UNAVAILABLE`. The discovery document and the key set are both on that host.
