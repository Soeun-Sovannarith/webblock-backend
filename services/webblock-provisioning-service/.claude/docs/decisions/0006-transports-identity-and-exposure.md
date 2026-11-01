# 0006: REST and MCP transports, identity, error mapping and exposure

**Sections 5 and 7 amended by decision [`0024`](0024-auth-and-telemetry-alignment.md) (G4 to G7):** with
`IDENTITY_MODE=jwt` the token check is web-block-commons' (RS256 only, the key set from the issuer's discovery
document, `typ` Bearer, a fixed 30 s skew and fixed key set limits). Provisioning keeps the lifetime cap, the `sub`
rule and the trust roots of section 5 (passed to the library, 0024 G4a; the refusal now reads `tls: the platform has
no certificate roots`). The verifier keys other than `AUTH_ISSUER`, `AUTH_AUDIENCE` and `AUTH_MAX_TOKEN_LIFETIME` are
refused by name.

**Section 3 amended by decision [`0023`](0023-phase-c-rest-shape-and-admin.md) (G5, G6, G10, G14):** the codes become
UPPER_SNAKE, the statuses follow Notion's table (409 only for duplicates and stale changes, 422 for business rules), and
a failed Project or Payment call keeps its upstream reason as the code. The 404 concealment is unchanged.

**Section 7 amended by decision [`0013`](0013-python-port-without-crossplane.md) section 9, A3**: for the Python service
the gateway enforces slow-client limits (header and body read time, write time); the service keeps its body-size limit,
global in-flight cap and per-request timeout.

Status: **Current.** Resolves OD-24. Revision 16 removes `create_release` and `build_release`, removes the client's
expected generation, and adds project route and hosting state; sections 1 to 4 and 6 changed for that. The HTTP and
MCP handlers (`src/provisioning/presentation`) implement it as amended by `0013` and `0023`. Changing a rule means
changing this record first.

Scope: `internal/transport/{ops,http,mcp}`, `internal/adapters/identity`, `internal/platform/{config,telemetry}`,
`cmd/api`, and the amendments to `api/openapi.yaml`.

## 1. One path for both transports

- `internal/transport/ops` is shared by REST and MCP. It holds the list of the seven operations, strict input decoding
  and parsing through the domain, the call into the application, the error classification of section 3, and the response
  bodies. The HTTP and MCP packages only adapt their protocol to it. Neither contains workflow logic, and neither calls
  Kubernetes or the broker.
- Authentication is transport middleware. Authorization is in the application (decision `0004` section 1).
- Input decoding rejects unknown keys, duplicate keys, trailing data, nesting, and numbers that are not integers.
  Identifiers go through `domain.ParseID`, which refuses the nil UUID. `status_url` is a relative path built from parsed
  identifiers, never from the `Host` header.
- A `202` body is written only after the application reports a committed acceptance.

## 2. Contract amendments made before handlers

ARCHITECTURE.md section 6 says to commit the contracts before implementing handlers. These are made in
`api/openapi.yaml` and `api/mcp/tools.json`, with their tests, before any handler exists (Phase 1R task R4):

- Seven operations and seven tools: `deploy_release`, `promote_release`, `rollback_release`, `get_release`,
  `get_command`, `get_deployment`, `get_release_diagnostics`. `create_release`, `build_release` and the
  `ReleaseAccepted` shape are removed. There is no hosting operation or tool.
- The three mutations take no request body. `expected_generation` and `MutationRequest` are removed. A request body must
  be absent, empty, or the empty JSON object; anything else is `400 malformed_request`. The MCP mutation tools take the
  path identifiers and `idempotency_key` only.
- `Release` loses `generation` and `previous_release_id` and gains `source_generation_request_id`,
  `source_repository_id` and `source_commit_sha`. `Command` loses `expected_generation`, gains `fencing_generation` and
  `failure_code`, and its `release_id` becomes optional. `Deployment` gains `route_state` and `hosting_state`, loses
  `tenant_resource`, and keeps an optional summary of the project's command in flight, named `in_flight_command` (ID,
  kind, status). No response carries a command's idempotency key, so a hosting command never shows a Payment event key
  to a caller. No response carries `requested_by` either: it is recorded for operators and audit, and showing one
  project member another member's subject is not something any consumer has asked for. The enums follow decision `0001`
  section 1; the two resource state enums go.
- `Error` gains an optional `command_id`, set only with `operation_in_progress` to name the command in flight.
- `500` on all seven operations and `413` on the three mutations, both with the existing `Error` schema. `429` on
  `deploy_release` and `promote_release` only: `rollback_release` consults no limit owned by Payment.
- The schema files keep the name `v1`: no version of these drafts was ever released to a consumer.
- `Idempotency-Key` is described as 1 to 255 bytes of UTF-8, because the domain counts bytes.
- The sentence "Handlers are not implemented yet" is removed when handlers land.
- The response shapes stay Provisioning's draft (OD-25); a conformance test pins the handlers to them.

## 3. Error classes

MCP returns the same `code` as REST inside a tool result marked as an error; only a missing or invalid token is an HTTP
`401` on the MCP endpoint.

| Class | Caused by | REST | Codes |
| --- | --- | --- | --- |
| invalid | bad identifier or key; a request body on a mutation; malformed, unknown or duplicate fields; missing or repeated header; wrong content type | 400 | `invalid_id`, `invalid_idempotency_key`, `malformed_request` |
| too large | body over the configured limit | 413 | `request_too_large` |
| unauthenticated | missing, malformed, badly signed, expired, wrong issuer or audience, unknown key | 401 | `unauthenticated`, one message for every cause |
| forbidden | the token lacks the action. Checked before project access, so the answer depends on the token alone and says nothing about any project | 403 | `action_not_permitted` |
| not found | the caller has no access to the project; or the release or command does not exist in it | 404 | `not_found`, identical in both cases |
| conflict | key reuse with a different request; another command in flight; route not stable; hosting offline; release in the wrong status, or a rollback when the route is not uncertain; target older than the live release; generation exhausted | 409 | `idempotency_conflict`, `operation_in_progress`, `route_not_stable`, `hosting_offline`, `illegal_state`, `release_outdated`, `generation_exhausted` |
| limited | a limit owned by Payment | 429, mutations only | `limit_reached` |
| unavailable | a required dependency is down or not configured; identity keys unavailable; timeout; contention; in-flight cap; operation switched off or not available in this phase | 503 | `dependency_unavailable`, `project_access_unavailable`, `identity_unavailable`, `timeout`, `contention`, `overloaded`, `operation_unavailable` |
| internal | anything else, panics, corrupt rows, and domain errors no caller input can cause | 500 | `internal`, fixed message; detail only in logs |

- OD-24: **404 when the caller has no access to the project, 403 when it has access but lacks the action.** Concealment
  hides the status, not timing, and is not claimed to. Because the action is checked first (section 6), a caller that
  lacks the action gets `403` whether or not it has access to the project. That is slightly wider than the ruling's
  wording and reveals nothing about the project; the owner was told. The four `404` cases (no access, missing release,
  release of another project, no state row) return byte-identical bodies and headers.
- A missing, repeated or invalid `Idempotency-Key` header is `invalid_idempotency_key`. Any other header problem, a
  wrong content type, and a request body on a mutation are `malformed_request`.
- Cases the table does not name: a wrong method on a `/v1/` route is `404 not_found`; on `/mcp`, `GET` and `DELETE` are
  `405` with no body and a missing, repeated or disallowed `Origin` is `400 malformed_request`; a body on a read is
  `400 malformed_request`; a cancelled request is `503 timeout`; `domain.ErrInvalidRequester` from the application is
  `401 unauthenticated`; a class an operation does not declare (a `409` on a read, a `429` on rollback) becomes
  `500 internal` rather than an undeclared status. The enabled-operations gate lives in the application only, after the
  action check, so the transports map `ErrOperationDisabled` to `503 operation_unavailable` and hold no gate of their
  own. HTTP header values lose leading and trailing whitespace before any code sees them (RFC 9110 optional whitespace),
  so an `Idempotency-Key` header with surrounding spaces is accepted over REST while the same string is refused as an
  MCP argument; that difference is the protocol's, not a parity defect. The in-flight cap is applied before
  authentication, so a request at the cap gets `503` whether or not its token is valid; per-caller limits are the
  gateway's (OD-55).
- Removed with revision 16: `stale_generation`, `invalid_generation`, `no_rollback_target`, `unfinished_release_exists`.
  `ErrStaleFence` and `ErrLeaseLost` are worker-internal and never reach a caller; if one did, it is `internal`.
- `route_not_stable` is a `409` and not a `503`: an uncertain route stays so until an operator acts, so a retry cannot
  help.
- "Not available in this phase" is `503 operation_unavailable`. It is in the documented status set and is literally
  true; `501` is not in the set.
- How an entitlement refusal from Payment is reported (`403` or `429`) depends on the OD-04 contract. Until then the
  application distinguishes "limit reached" (`429`) from "dependency unavailable" (`503`) and nothing else; no
  production code path can produce either in Phase 2.
- Every `Err*` in `internal/domain/errors.go` and `internal/application` must have a row in the classification table; a
  test fails when one is missing. No provider or driver error text reaches a response.

## 4. MCP

- The official MCP Go SDK over Streamable HTTP, mounted at `/mcp`. Stateless mode with JSON responses: nothing in the
  seven tools needs a session, and no session state is held per replica.
- The seven tools are registered from the embedded `api/mcp/tools.json`: name, description and input schema come from
  the file. Construction fails if the file's tools and the handler table differ in either direction. There is no generic
  tool.
- The same authentication middleware wraps `/v1/` and `/mcp`. The tool handlers use the principal that middleware
  verified.
- SDK behaviours this design relies on (how the verified principal reaches a tool handler in stateless mode, the
  body-size limit and its status, schema handling at registration) are pinned by characterization tests before anything
  depends on them. If a test disproves an assumption, the implementer stops and reports.
- The verified principal reaches a tool handler as a context value set by our own middleware; handlers never read the
  raw `Authorization` header that the SDK also exposes. The request timeout is applied inside `ops`, because a deadline
  on the HTTP request does not reach a tool handler. The SDK does not validate tool arguments against the registered
  schema and panics on a bad registration, so both the argument validation and "construction fails" are our code. Its
  body limit treats zero as a large default and a negative value as unlimited, so `MCP_MAX_REQUEST_BODY_BYTES` must be
  greater than zero; its `413` has a plain-text body, which the parity test records as the one response that does not
  carry our `Error` shape unless a wrapper replaces it. A request that carries more than one call is refused, so that
  the in-flight and body limits keep meaning one call.
- Which agent client must work, and its protocol version and authentication discovery, are unknown (OD-28). Only the
  SDK's own client is tested.

## 5. Identity (OD-05 stays blocked-external)

What is built without inventing the Account/Keycloak contract:

- A token-verifier port defined by its consumer and one adapter: signed JWT verification against a JWKS endpoint.
  Issuer, audience, JWKS URL, the claim that carries scopes, the scope required for each of the seven operations, the
  allowed algorithms and the clock skew are **required configuration with no default**. No Keycloak-specific name
  appears in code.
- Rules: asymmetric algorithms only (`none` and HMAC are refused at configuration time and at verification); `exp` and
  `sub` required, checked by the adapter itself because the library's claim validation accepts a token without them;
  `sub` must also fit the requester rule of decision `0001` section 4 (1 to 255 bytes, no control characters), because
  it is stored with every accepted command, and a token whose subject does not is `401` like any other invalid token;
  exact issuer; configured audience present; unknown key ID triggers one rate-limited refresh; a JWKS endpoint that is
  down with no fresh enough cached keys is `503 identity_unavailable`, not `401`.
- The JWKS URL must be HTTPS; the response size and fetch time are bounded.
- Fixed in code, not configurable: `nbf` is honoured; a `kid` is required and a key is selected only by it; the key type
  must fit the algorithm; tokens carrying `jku`, `jwk`, `x5u`, `x5c` or `crit` headers, encrypted tokens, and tokens
  over a size limit are refused before parsing; the fetch follows no redirect and ignores proxy settings from the
  environment; the cached key set is replaced only by a complete, non-empty parse, and an unknown `kid` is remembered
  for the minimum refresh interval so that random key IDs cause at most one fetch per interval. Configuration adds a
  maximum token lifetime and a cap on the clock skew. Start-up fails when any of the seven operations has no non-empty
  required scope: a missing mapping never means permitted. Scope values are compared whole, never as substrings.
- Tests generate keys and run a local issuer inside test files. No type that accepts every token exists outside
  `_test.go`.
- Library: `github.com/go-jose/go-jose/v4`, chosen for having no transitive dependencies and an algorithm allow-list at
  parse time. Hand-written JOSE parsing is rejected.

Unproven until Account/Keycloak answer: real issuer and audience values, the claim layout (a flat scope claim is assumed
and configurable), scope names, how agent identities are registered and told apart from users, whether token types must
be distinguished (a `typ` or `azp` requirement, so that an ID token or another client's token cannot authenticate),
whether operators are members of the projects they recover (if not, the project access check hides the project from them
and operator rollback cannot work as designed), whether calls to Project need an exchanged token, and who may hold the
scope for `rollback_release`, which revision 16 makes an operator action (OD-45).

> **Amended by task H3 (P-H) for the Python service**. The verifier is `infrastructure/identity/JwtVerifier`, using
> `pyjwt[crypto]` 2.15.1 (a direct dependency at the version `mcp` already locked) in place of go-jose. pyjwt is used
> for the signature alone: `PyJWS` is constructed with the configured algorithms only (so `none` and HMAC are never
> registered) and with `enforce_minimum_key_length`, and is called with the one key the `kid` selected. pyjwt's claim
> validation and `PyJWKClient` (which blocks the event loop) are not used. Everything else in this section is our own
> code, kept as written:
> - the size limit (8192 bytes) and the three-part shape (so encrypted tokens are refused), checked before parsing;
> - the forbidden headers (`jku`, `jwk`, `x5u`, `x5c`, `crit`, plus `zip` and `enc`);
> - the algorithm allow-list;
> - a required `kid` that alone selects the key, a JWK-pinned `alg` honoured, and the key type and curve bound to the
> algorithm;
> - `exp` required, and `nbf`/`iat` honoured within the skew;
> - the lifetime cap measured from `iat` and from now;
> - `sub` required and fitting the requester rule;
> - the exact issuer;
> - the audience as a whole value;
> - scopes compared whole.
>
> JSON with repeated members is refused, so our parser and the library cannot disagree. The key set is fetched with
> httpx2, without redirects or environment proxy settings, with verification always on and bounded in time and bytes. It
> trusts only the platform's compiled-in certificate roots (OpenSSL's default CA file and directory, loaded by name;
> `SSL_CERT_FILE`, `SSL_CERT_DIR` and `SSLKEYLOGFILE` have no effect), and the API refuses to start when neither exists
> (task HF-B, H-V finding F-5). An issuer behind a private CA needs that CA in the image's built-in bundle or a recorded
> decision for an explicit key (OD-05). It is replaced only by a complete parse holding at least one usable key, and an
> unknown `kid` is remembered for `AUTH_JWKS_MIN_REFRESH_INTERVAL`. The key set is fetched on the first request, not at
> start-up, so the API starts with the issuer down and answers `503 identity_unavailable` until keys arrive.
> `IDENTITY_MODE` is `jwt` in production and `dev-tokens` or `jwt` in the prototype. With `jwt` the keys of section 7
> are required, with `dev-tokens` they are refused by name, and the token file is refused with `jwt`. No configuration
> key carries TLS trust or disables verification; only tests pass an `ssl.SSLContext` to the constructor, which refuses
> one that skips verification. Logs carry a fixed cause and, for a transport failure, the error class; the `kid` is no
> longer logged. The development tokens live in `infrastructure/standins` and are built only by `hosts/stand_ins.py`.
> `typ` and `azp` are not required (OD-05).

## 6. What is exposed in Phase 2

 The API starts and fails closed. All seven operations are registered on both transports, so routes, operation IDs and
 tool names stay equal.

| Operation | Production build in Phase 2 |
| --- | --- |
| `get_release`, `get_command`, `get_deployment` | Enabled. In practice `503 project_access_unavailable` until a real Project adapter exists (OD-01) |
| `deploy_release`, `promote_release`, `rollback_release` | Switched off: `503 operation_unavailable`. Enabled with the phase that can execute them (Phase 4 for deploy, Phase 5 for promote and rollback), because an accepted command nothing can run would hold the project's single in-flight slot until it is failed. Rollback also waits for OD-45 |
| `get_release_diagnostics` | Not available in this phase (decision `0004` section 3) |

- The set of enabled operations is a value passed by `cmd/api` in code, not a configuration key, so enabling one is a
  reviewed change. Tests enable the full set to prove acceptance, replay, conflict and parity.
- Check order for every call: authentication (`401`), syntactic input validation in the transport (`400`: identifiers,
  key, body, headers), action permission (`403`), operation gate (`503`), project access (`404` or `503`), then the use
  case. The gate comes before project access so that a switched-off operation makes no call to another service. Syntax
  is checked before authorization because the answer depends only on the request's own text and reveals nothing about
  any project; a caller without the scope who sends a malformed identifier gets `400`, not `403`. The application still
  validates what it receives (decision `0004` section 1).

## 7. Operational endpoints, limits, logging

- `GET /healthz` and `GET /readyz` are served on a separate operational listener. The public listener then has one
  invariant that is tested: every route requires a token.
- Required configuration with no defaults (numbers are OD-15): `HTTP_LISTEN_ADDR`, `OPS_LISTEN_ADDR`,
  `HTTP_READ_HEADER_TIMEOUT`, `HTTP_READ_TIMEOUT`, `HTTP_REQUEST_TIMEOUT`, `HTTP_WRITE_TIMEOUT`, `HTTP_IDLE_TIMEOUT`,
  `HTTP_SHUTDOWN_GRACE`, `HTTP_MAX_HEADER_BYTES`, `HTTP_MAX_REQUEST_BODY_BYTES`, `MCP_MAX_REQUEST_BODY_BYTES`,
  `HTTP_MAX_IN_FLIGHT`, `AUTH_ISSUER`, `AUTH_AUDIENCE`, `AUTH_JWKS_URL`, `AUTH_ALLOWED_ALGS`, `AUTH_CLOCK_SKEW`,
  `AUTH_SCOPE_CLAIM`, `AUTH_ACTION_SCOPES`, `AUTH_JWKS_FETCH_TIMEOUT`, `AUTH_JWKS_MAX_BYTES`,
  `AUTH_JWKS_REFRESH_INTERVAL`, `AUTH_JWKS_MIN_REFRESH_INTERVAL`, `AUTH_JWKS_MAX_STALE`, `AUTH_MAX_TOKEN_LIFETIME`,
  `MCP_ALLOWED_ORIGINS` (the value `none` means an empty list), `COMMAND_DEADLINE`, and the database keys listed in
  decision `0005` section 7. `LOG_LEVEL` is optional.
- Loading configuration reports every missing or invalid key by name, never by value, and the process exits non-zero.
  Secret values use a type that redacts itself when printed or logged.
- Phase 2 telemetry is structured `slog` output with a correlation ID and redaction of authorization headers, tokens,
  secrets, passwords, cookies, idempotency-key values and bodies. OpenTelemetry and Sentry are deferred to Phase 6 with
  diagnostics and alerts; TECH_STACK.md lists Sentry as a confirmed direction, so the owner was told.
- The API process has no Kubernetes configuration and no broker credentials. A test asserts its dependency list contains
  no Kubernetes client and no test package.
- The three mutations accept at most the empty object, so their body limit is a small fixed number, not
  `HTTP_MAX_REQUEST_BODY_BYTES`. `/mcp` answers `GET` and `DELETE` with `405` in stateless mode and checks `Origin`
  against a configured allow-list. The operational listener must bind a different address from the public one, serves no
  profiling or metrics route, and `/readyz` names no dependency, version or error. Reads send `Cache-Control: no-store`.
  Logging writes an allow-list of fields, never a response body from another service, and a correlation ID taken from a
  caller is limited to a fixed character set and length.
- Limits per caller (concurrency and rate per subject, a limit on failed authentication) are enforced by the gateway,
  not by this service (OD-55). The service keeps `HTTP_MAX_IN_FLIGHT` and the body limits as a backstop. Until the
  gateway is in front of it, one caller can exhaust the in-flight cap for everyone; that is a deployment precondition,
  not something the service compensates for.
  *(Amended by decision [`0019`](0019-service-identity-is-the-mesh.md) section 5, owner: no gateway sits in front of
  `/v1/*` any more. Per-user limiting is done by Cloudflare (rate rules on `app.<domain>`, per IP) and by the Next.js
  server (per signed-in user); the service keeps its global `HTTP_MAX_IN_FLIGHT` and body limits.)*


**Amended by task H4 (P-H)** (`0016` section 5 item 4). This replaces "serves no profiling or metrics route" and the
deferral of Sentry above.
- Each long-lived process has an operational listener: the API's `OPS_LISTEN_ADDR`, on a different address from its
  public listener, and a required `OPS_LISTEN_ADDR` with no default in the worker, the build worker and the bootstrap
  process. The operator CLI has none. It serves `GET/HEAD /healthz`, `GET/HEAD /readyz` (API only) and `GET /metrics`
  (the Prometheus text exposition of the process's own registry). The public listener never serves `/metrics`, and no
  listener serves a profiling route. `/readyz` still names no dependency, version or error; a `/metrics` that cannot be
  produced within its bound answers 503 with a fixed word. The listener's connection cap, keep-alive and shutdown grace
  are constants for now (16, 5 s, 2 s); OD-15 prefers keys.
- Metric labels are low-cardinality: every label value comes from a fixed set, anything else is counted as `other`, and
  no label carries a project, release, command, step or subject ID. A test scans the registry against an allow-list. The
  dispatch lag, unfinished-command and inbox backlog samples are read on scrape, in one short read-only transaction on
  the process's own login with existing grants. The metric names are listed in `.claude/docs/runbooks/observability.md`.
  Follow-up: the failure-code label allow-list is a static list; it should be derived from
  `core/domain/failure_codes.py` (task H5a) so build-system codes are not counted as `other`.
- Error reports go to Sentry only when `SENTRY_DSN` names a DSN. The key is required in every long-lived process with no
  default, `none` turns reports off, and with a DSN `SENTRY_ENVIRONMENT` and `SENTRY_TRACES_SAMPLE_RATE` are required.
  The DSN is a secret. The SDK runs with no default or auto-enabled integration, no personal data, local variables,
  source context, request bodies, breadcrumbs, sessions, client reports, logs or metrics of its own, no trace
  propagation and no proxy from the environment. The service's own `before_send` rebuilds each event from an allow-list:
  only events with an exception; each exception keeps its type, module, mechanism and frames, never its message; tags
  only `process`, `profile` and `correlation_id`; everything else is dropped. The project ID is not a tag until
  agent-visible data can be project-scoped (P-C). Transactions are dropped until tracing is decided (OD-65). Expected
  5xx codes (`timeout`, `overloaded`, `dependency_unavailable`, `identity_unavailable`) are not reported. Redaction
  tests plant secrets and customer content in every place an event can carry them.
- The loggers of `kubernetes_asyncio` and `urllib3` are held at WARNING even at `LOG_LEVEL=debug`, because
  `kubernetes_asyncio` writes whole API responses at DEBUG, and a Secret's apply response carries its data.

**Amended by task SW** (built into the API, prototype only). Interactive API documentation for team testing, in the
prototype profile only. This narrows "every route requires a token" above and `openapi_url`/`docs_url` being off.
- With `PROVISIONING_PROFILE=prototype` the public app serves `GET /openapi.yaml` (the hand-written contract
  `src/contracts/openapi.yaml`, byte for byte, `application/yaml`) and `GET /docs` (a Swagger UI page that loads that
  contract). These two routes, and only for `GET`/`HEAD`, are the one exception to "every route requires a token". Every
  API call made from the page still needs the bearer token (the page's Authorize button). The prototype's public
  listener binds `127.0.0.1`.
- With `PROVISIONING_PROFILE=production` neither route exists: both answer like any unknown route (the token is required
  first, then `404 not_found`), and FastAPI's own `openapi_url`, `docs_url` and `redoc_url` stay `None` in both
  profiles. Tests prove both profiles.
- The page loads Swagger UI from an exact pinned version with Subresource Integrity hashes, no other script, and no
  inline credentials. No CORS header is added: the page and the API share one origin.
- The contract file stays the single source: the existing contract conformance tests already keep it equal to the
  handlers, so the page cannot drift from the served API.

## 8. Layout notes

`api/embed.go` and `api/events/embed.go` make the contract files available to the binaries, because Go cannot embed
files from a parent directory. `internal/transport/ops` is an addition to the folder sketch in ARCHITECTURE.md
section 3. So are `cmd/api/apiwiring` and `cmd/worker/workerwiring`: each `main.go` only reads the configuration and
calls the wiring package, which holds the constructors so that the end-to-end tests build the same processes the
binaries run.

## Not decided here

Identity contract (OD-05), Project contract (OD-01), production numbers (OD-15), consumer review of response shapes
(OD-25), the agent client (OD-28), retry ownership between mesh and application for HTTP (OD-40), behaviour behind the
gateway.
