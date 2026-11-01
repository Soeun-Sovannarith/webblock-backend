# 0024: Auth and telemetry alignment: web-block-commons for the token check and inner calls

Status: Accepted (the owner agreed the change note `.claude/docs/changes/commons-adoption.md`; constitution version 2,
II.1 and II.2).

## 1. Context

The platform's final auth model (Notion "Web Block architecture" sections 2.4, 3.5 and 8; the Project service's
authentication contract) asks every service to check the token the same way, to refuse a token without `user_id`
with 403, and to send only `x-user-id`, `x-request-id` and `traceparent` on calls to our own services. The shared
library `web-block-commons` does exactly this for Python services. Provisioning had its own verifier
(`infrastructure/identity/`) and its own gRPC metadata (`infrastructure/grpc/call.py`), which differed from the
model in three places: `typ` was not checked (FR-1), a token without `user_id` answered 404 (FR-2), and `CheckAccess`
carried no `x-user-id` (FR-8).

This record covers FR-1, FR-2 and FR-8 (TR-26, TR-27, TR-30, TR-32). The FR-43 log fields and message headers stay
open (section 6).

## 2. Constraints

- Constitution II.1 names `web-block-commons` in the stack; II.2 exempts first-party WebBlock packages from the age
  and open-source rules, but they are pinned to an exact release, installed with their hash, and fetched without a
  credential in `uv.lock`, an image layer or a build argument.
- Constitution III.2: the core imports no framework; the library is an adapter concern.
- Constitution III.7: no token in any outgoing call, log, trace or error.
- Decision `0019`: the mesh is the service identity; rollback and admin actions need `ADMIN` (section 7).
- Decision `0006` section 5: the API's own token check. Its lifetime cap and `sub` rule stay (G5).

## 3. Decisions

- **G1. The library.** Provisioning depends on `webblock-commons[fastapi,grpc]==0.1.0`, the wheel of the GitHub
  Release `v0.1.0`, committed as `vendor/webblock_commons-0.1.0-py3-none-any.whl` (SHA-256
  `e1c494ef409790e3b238460b8f887097647c584a7fbc4e77a3adb47308e4a4f3`). `pyproject.toml` names it as a path source;
  `uv.lock` records the same hash, so `uv sync --locked` refuses any other file. `.gitignore` lets in only
  `vendor/*.whl`. The image build copies `vendor/` and, since it installs with `UV_FROZEN=1`, checks each wheel with
  `sha256sum -c` against that hash before `uv sync` (a unit test keeps the Dockerfile's hash equal to the lock's); it
  needs no token and no access to the library's repository. An upgrade is a new wheel and `uv lock` in one reviewed
  change.
- **G2. Its checks.** `make py-vuln` and `make py-age` check the registry packages of `uv.lock`; the path wheel is
  not one. Its own dependencies (`httpx2`, `pyjwt[crypto]`, `opentelemetry-api`, `fastapi`, `grpcio`,
  `grpcio-status`, `protobuf`) carry the same pins as ours and keep every rule. The wheel itself passed its own
  repository's gates (tests, OSV scan, age check) before release (II.2).
- **G3. Inner calls (FR-8, TR-30).** Every gRPC channel to our services is the library's `internal_channel`
  (`open_channel` in `infrastructure/grpc/call.py`). Its interceptor removes `authorization` and every header only
  the library may set, then adds `x-request-id`, `traceparent` (and `tracestate`) and the current user's
  `x-user-id`. `unary` binds the library's context for each call: the request ID, the trace context of the span
  `grpc <method>` (none when tracing is off), and the user it is given. `CheckAccess` passes the principal's user;
  `GetRepository` and `GetEntitlement` pass none, so they never carry a user of an outer context. The Project
  service's HTTP access check (`PROJECT_ACCESS_MODE=project-service`, `infrastructure/project/access.py`) sends
  through the library's `InternalTransport` and binds the same context, with the principal's user. The context of a
  call is built in one place (`infrastructure/inner_call.py`). Calls to Jenkins, the key set and the Kubernetes API
  use their own clients, which the library never touches: none of these headers reaches an outside host.
- **G4. The token check (FR-1, TR-26).** With `IDENTITY_MODE=jwt` the library's `TokenVerifier` checks the token:
  `RS256` only; the key set from the issuer's discovery document (`<issuer>/.well-known/openid-configuration`,
  whose `issuer` must equal ours and whose `jwks_uri` must be on the issuer's scheme, host and port); `iss` exact;
  `aud` holding `provisioning`; `typ` exactly `Bearer`; `exp` required; `exp` and `nbf` within a 30 s skew. The key
  set: fetched on first use, used for 10 minutes, at most one fetch per 30 s, keys older than 24 hours unused, one
  fetch bounded by 5 s (2 s to connect) and 1 MiB, no proxy variable of the environment. No key set recent enough
  answers 503 `IDENTITY_UNAVAILABLE`, never 401.
- **G4a. The key set's trust roots (TR-26; finding F-5 of H-V).** The fetch trusts only the platform's compiled-in
  roots: `tls.platform_roots()` (OpenSSL's default CA file and directory loaded by name, `SSLKEYLOGFILE` off),
  shared with the Project service's HTTP client. `JwtVerifier` passes it as the library's `ssl_context`. The API
  refuses to start when neither the CA file nor the directory exists. Why: the library's own default is the system
  trust store through `truststore`, which calls OpenSSL's `set_default_verify_paths()`, so `SSL_CERT_FILE` and
  `SSL_CERT_DIR` would widen trust; whoever can set the API's environment and place a CA file could then serve their
  own key set and mint any token. The library's docstring calls `ssl_context` test-only; Provisioning uses it on
  purpose until the library builds these roots itself (asked of web-block-commons 0.1.1; the report is with the
  Project service's issue project-service-14). Moving to that release drops the argument in one reviewed change.
- **G5. What Provisioning still checks.** The library does not read `sub`, `iat` or a token's lifetime. On the
  claims it verified, Provisioning refuses (401): no `sub`, or one that is not a valid requester (it is stored as
  `requested_by_subject`); an `iat` after now plus the skew; `exp` not after `iat` or `nbf`; a lifetime over
  `AUTH_MAX_TOKEN_LIFETIME`, measured from `iat` and from now. These use the library's 30 s skew.
- **G6. The user (FR-2, TR-27).** The user is the `user_id` claim only: a canonical lowercase, non-nil UUID. A token
  that passes G4 and G5 but has no valid `user_id` answers 403 `ACCESS_DENIED` on every route, `/v1/` and `/mcp`,
  before any store or Project call. The roles are the top-level `roles` claim only, a list of text; anything else
  gives no roles (so a role-gated action answers 403). `realm_access` is never read. `ADMIN` is compared whole and
  with case, by `ActionScopePolicy` and the admin gate as before.
- **G7. Settings.** `AUTH_ISSUER` must be an absolute https URL. `AUTH_AUDIENCE`, `AUTH_MAX_TOKEN_LIFETIME` and
  `AUTH_ACTION_SCOPES` stay. The settings the library fixes are refused by name in every profile and mode, so a
  stale environment is caught at start-up: `AUTH_JWKS_URL`, `AUTH_ALLOWED_ALGS`, `AUTH_CLOCK_SKEW`,
  `AUTH_SCOPE_CLAIM`, `IDENTITY_USER_ID_CLAIM`, `AUTH_JWKS_FETCH_TIMEOUT`, `AUTH_JWKS_MAX_BYTES`,
  `AUTH_JWKS_REFRESH_INTERVAL`, `AUTH_JWKS_MIN_REFRESH_INTERVAL`, `AUTH_JWKS_MAX_STALE`.
- **G8. Logs and metrics.** The library logs a refusal's fixed cause (DEBUG) and a failed key set fetch (WARNING)
  on the standard logger `webblock_commons`, with the cause as `auth_cause`. The API host attaches a handler that
  turns those records into the identity's own events (`component=identity`): `token refused` at INFO and
  `jwks fetch failed` at WARNING, each with the cause (the part before any `:`, so no class name). G5 and G6 log the
  same way. `provisioning_identity_refusals_total` and `provisioning_identity_key_fetch_failures_total` count them by
  cause. No token, claim value, key or URL is logged.
- **G9. The prototype's development tokens.** `IDENTITY_MODE=dev-tokens` is a prototype stand-in, refused in
  production; the library does not check it. A development token may still lack a `user_id`: the Kind stack's
  `prototype-admin` and operator tokens have none.

## 4. Consequences

- Code: `infrastructure/identity/verifier.py` wraps the library; `jwks.py` is removed and `token.py` keeps only what
  G5 needs; `log_bridge.py` does G8, attached by the API process (`hosts/api/__main__.py`). `infrastructure/tls.py`
  holds the trust roots of G4a. The Project HTTP client keeps its JSON reader (`infrastructure/project/access.py`).
  `infrastructure/config/identity.py` loses the G7 keys. `presentation/http/auth.py` answers 403 for G6.
  `infrastructure/grpc/call.py`, `infrastructure/project/grpc_access.py`, `infrastructure/project/access.py` and
  `infrastructure/inner_call.py` do G3. The core may not import the library (import-linter).
- Tests: the library's own tests cover its token and key set rules; Provisioning's tests cover its use of them
  (`tests/unit/infrastructure/identity/`), FR-2.2 (`test_fr2_2_*`), FR-8.1 and FR-8.2 (`test_fr8_1_*`,
  `test_fr8_2_*`).
- Deployment: the API must reach the issuer's origin on 443 for the discovery document and the key set (TR-43); an
  in-cluster key set URL on another host is no longer possible. Production sets `AUTH_ISSUER` to the realm URL.
- Cost if wrong: the key set limits of G4 are the library's constants, not settings (TR-37 asks every timeout to be
  a setting). If one must change, the library takes a setting in a new release and Provisioning moves to it. If the
  library ever differs from FR-1 or FR-2, every service that uses it differs the same way: the shared test cases of
  `web-block-commons` are where that is caught.

## 5. What was built and tested

Recorded with the change's gate results in its report.

## 6. What stays open

- FR-43: `request_id` and `span_id` in log lines and `x-request-id` on messages (the library's `logging` and
  `messaging` modules); a later change.
- G9: whether the development tokens follow FR-2.2 too (every token entry then needs a `user_id`, the Kind stack's
  admin and operator included).
