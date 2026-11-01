# Handoff: auth and telemetry alignment with the final auth model

A plan from a read-only review (no code changed). Another agent executes it.

## Why

The owner fixed the final auth model and the telemetry plan. Provisioning was built before that,
so a few rules differ. Sources, all read-only for this work:

- Auth contract (approved, amendments):
  `/home/roa/Documents/web-block/project-service/.claude/docs/contracts/authentication-contract.md`
- Shared library design (draft, not yet approved):
  `/home/roa/Documents/web-block/2026-10-27-web-block-commons-design.md`
- Notion "Web Block architecture" (sections 2.3, 2.4, 3.5, 3.8, 4.1, 7.3, 8):
  https://app.notion.com/p/40358175966e8198a088f123a9ab23fe
- Notion "5 · Provisioning" (`commands.kind` rollback row, `requested_by_subject`):
  https://app.notion.com/p/40058175966e81769596d1ebcadb333d

In short: the gateway checks the token, then Provisioning checks it again (issuer, audience `provisioning`, RS256,
exp/nbf, `typ` Bearer). `user_id` is the identity (missing: 403). Roles come from the top-level `roles` claim only.
Not the owner: 404. Ownership is Project `CheckAccess` (gRPC, 2 s, retry only UNAVAILABLE twice, fail closed).
Inner calls carry `x-user-id`, `x-request-id`, `traceparent`, never the token. `web-block-commons` will later give
token check, inner-call headers, RabbitMQ headers and JSON log fields; until then only small stop-gaps.

## Rules for the agent that executes this

- Read `.claude/CLAUDE.md` first. Decision record before code (CLAUDE.md section 6: "the decision record changes
  before a rule in code"). Guidance rules live only in `.claude/CLAUDE.md` (`AGENTS.md` was merged into it).
- One implementer at a time in the shared tree (Phase C ruling). Never push. Never merge without the owner's OK.
- DOKS is read-only. Changes to the cluster (web-block-config) are written as commands or files for the owner.
- Notion pages are read-only. Where Notion is silent, write a labelled lead decision in the repo.
- Do not touch `reports/`, `research_notes/` (another agent works there).
- Docs: plain easy English, no em or en dashes, lines at most 120 characters.

## Where it fits

| Tasks | When |
| --- | --- |
| 1 to 6 | Before the owed Kind run of `ai/handoffs/after-phase-c.md` (its gates and Kind cover them) |
| 7 (docs) | With the owed client docs (`after-phase-c.md` item 2) |
| 8 | Now, as part of task 1's decision record |
| 9, 10 | Before the first production deploy |
| 11 | Wait: Project's gRPC `CheckAccess` |
| 12 | Next phase: private images with a pull Secret (owner; spec FR-53, TR-52) |
| 13 to 15 | After `web-block-commons` exists. Do not build them now |

Every path below is in today's layout (task 1 writes decision `0024`). Line numbers move with the code: re-find a
line by its name.

## Tasks

### 1. Decision record 0024 (now)

- What: write `.claude/docs/decisions/0024-auth-and-telemetry-alignment.md` with the rules of tasks 2 to 12 and the lead
  decisions L1 to L4 below. Mark OD-05 as answered again by the contract (typ Bearer, user_id required, RS256).
  Say that it keeps `0019` section 3 (`rollback_release=ADMIN`, L3) and supersedes `0016` on Docker Hub for sites.
- Files: `.claude/docs/decisions/0024-auth-and-telemetry-alignment.md` (new),
  `.claude/docs/decisions/open-decisions.md:16` (OD-05).
- Rule: CLAUDE.md section 6, "the decision record changes before a rule in code".
- Test: line length and dash check only (`grep -nP '\x{2013}|\x{2014}'` finds nothing; no line over 120).

Lead decisions to record:

- L1. `typ` must be exactly `Bearer` (case-sensitive), in both identity modes that read JWTs.
- L2. The clock skew cap drops from 2 minutes to 60 seconds for every profile (commons design 4.1 item 2).
- L3. Rollback is `ADMIN` only (Owner, answer O1). This overrides Notion's "Project owner or
  `ADMIN`" (5 · Provisioning, `commands.kind`); the owner updates that Notion page to match (read-only for us). So
  the production example keeps `rollback_release=ADMIN` and `0019` section 3 stays. An `ADMIN` skips the owner check.
  Spec: FR-50 in `.claude/docs/requirements/FRD.md`.
- L4. The build worker's service account stays `provisioning-build-worker` (task 8).

### 2. Refuse a token whose `typ` is not `Bearer` (now)

- What: in `_check_claims`, after the audience check, refuse when `typ` is missing or not exactly `Bearer`, with the
  fixed cause `typ_mismatch` (401, the same one message as every refusal). Fix the docstring that says `typ` is not
  required, and the `RESERVED_CLAIMS` comment ("once OD-05 is answered").
- Files: `src/provisioning/infrastructure/identity/verifier.py:18`, `:176` (insert after);
  `src/provisioning/infrastructure/config/identity.py:86-88`; `.claude/docs/runbooks/identity.md:76-83`;
  test helper `tests/unit/infrastructure/identity/issuer.py` (default claims must carry `typ: Bearer`).
- Rule: contract section 5 (`typ`: the first service accepts only `Bearer`); Notion 2.4 (`typ` wrong: 401).
- Test: `tests/unit/infrastructure/identity/test_jwt.py`: `typ` `ID`, `bearer`, missing, a number: each 401 with
  cause `typ_mismatch` in the log and no token text; `Bearer` passes. `make py-test`.

### 3. No `user_id` is 403 ACCESS_DENIED, not 404 (now)

- What today: a token without a valid `user_id` is accepted; `GrpcProjectAccess` then denies without a call and the
  client gets 404 `PROJECT_NOT_FOUND` (`grpc_access.py:56-64`); admin routes even work without it.
- Change: with `IDENTITY_MODE=jwt`, `Authenticate` answers 403 `ACCESS_DENIED` (raise `ActionDenied`) when the
  verified principal has no `user_id`, on every route (`/v1/` and `/mcp`), before any store or Project call. 401
  still comes first for a bad token. `dev-tokens` (prototype) keeps today's behaviour. Wire the flag in the API host.
  Production must refuse `IDENTITY_USER_ID_CLAIM` other than `user_id`, like `roles` at `identity.py:185`.
  Keep `grpc_access.py:56` as a second lock.
- Files: `src/provisioning/presentation/http/auth.py:102-106`; `src/provisioning/hosts/api/wiring.py:130` (where the
  verifier is built); `src/provisioning/infrastructure/config/identity.py:111-116, 183-187`;
  `.claude/docs/runbooks/identity.md:30`.
- Rule: contract section 3 ("A token without `user_id` is refused by the platform (403)"); Notion 2.4; commons 4.1.
- Test: `tests/unit/presentation/http/test_auth.py`: jwt principal without `user_id` on a client route, an admin
  route and `/mcp`: 403 `ACCESS_DENIED`, verifier called once, Project access never called; with `user_id`: passes;
  dev-tokens without `user_id`: unchanged. `tests/unit/infrastructure/config/test_identity.py`: production refuses
  `IDENTITY_USER_ID_CLAIM=sub`. `make py-test`.

### 4. Production pins RS256; skew at most 60 s (now)

- What: production refuses `AUTH_ALLOWED_ALGS` other than exactly `RS256` (same style as the `roles` check).
  Lower `MAX_CLOCK_SKEW` to 60 seconds (L2).
- Files: `src/provisioning/infrastructure/config/identity.py:83`, `:183-187`, `:251-270`;
  `.claude/docs/runbooks/identity.md:21-22` and its production example (`:41-52`, add `AUTH_ALLOWED_ALGS=RS256`).
- Rule: contract section 6 ("The services accept RS256 only"); Notion 2.4 and 8; commons 4.1 item 2.
- Test: `test_identity.py`: production with `RS256,ES256` or `PS256` refused by name; prototype still accepts them;
  skew `61s` refused, `60s` accepted. `test_api.py::test_the_runbooks_production_example_loads` still passes.

### 5. Rollback stays ADMIN only (now)

- What: no config change (L3, owner). The production example keeps `rollback_release=ADMIN`. Make
  the runbook text say why (owner answer O1, overrides Notion until the owner updates it).
- Files: `.claude/docs/runbooks/identity.md:39`, `:49`, `:63-69`; `.claude/docs/decisions/open-decisions.md:16`.
- Rule: owner answer O1; `0019` section 3; FRD FR-50.
- Test: `test_api.py::test_the_runbooks_production_example_loads`; add a gate test in
  `tests/unit/core/application/` (owner with USER only: 403 `ACCESS_DENIED`, nothing stored; ADMIN: CheckAccess not
  asked, accepted).

### 6. `x-user-id` on the CheckAccess call (now, small stop-gap)

- What: `unary()` takes an optional `user_id` and adds the metadata `x-user-id` when it is set. Only
  `GrpcProjectAccess` passes it (the user is known there). `GetRepository` and `GetEntitlement` have no user and send
  none. `user_id` stays in the request body too. Never log it. The commons interceptor replaces this later.
- Files: `src/provisioning/infrastructure/grpc/call.py:72-93`;
  `src/provisioning/infrastructure/project/grpc_access.py:69-75`.
- Rule: Notion 3.5 ("x-user-id: the signed-in user's user_id, when there is a user. Never the token").
- Test: `tests/unit/infrastructure/grpc/test_call.py` (metadata has `x-user-id` only when given);
  `tests/unit/infrastructure/project/test_grpc_access.py` (equals the body's `user_id`); repository and entitlement
  tests assert no `x-user-id`. `make py-test`.

### 7. Docs: retired names and new platform facts (with the owed client docs, with their FR IDs)

- What: replace stale facts, keep decision records `0006` to `0023` and `ai/` as history.
  - GitHub becomes Gitea (source and repository ID). Project commits the source to Gitea after AI Codegen hands it
    over, then publishes `source_published`. Provisioning still never commits.
  - Website images go to the Gitea container registry (Jenkins pushes); platform service images go to Docker Hub.
    Site images are private: one read-only Gitea account, a pull Secret per site.
  - Project access in production: say both modes exist and gRPC is the target (task 11).
  - Identity runbook: error codes are UPPER_SNAKE since Phase C (`identity.md:91-92`, `:115`); typ, user_id, RS256.
- Files: `.claude/CLAUDE.md` (search it; line numbers change); `README.md:36`;
  `.claude/docs/architecture/provisioning-overview.md:29`; `.claude/docs/runbooks/prototype-stack.md:167`; comments
  `src/provisioning/core/domain/values/identifiers.py:28`,
  `src/provisioning/core/application/ports/dependencies.py:69`;
  `src/contracts/openapi.yaml:1028` (description text only, no wire change).
- Rule: decisions file "Platform"; Notion 5.4 and open point 6; Notion 5 · Provisioning (`repository_id` is Gitea's).
- Test: `make contracts-check py-test` (openapi text), line-length and dash check on every touched file.

### 8. Service account names for Project's AuthorizationPolicy (now; record in task 1)

- What: Notion 3.8's example names `provisioning-build`; the chart creates `provisioning-build-worker`
  (`deploy/helm/provisioning/templates/serviceaccounts.yaml:14`). Lead decision L4: keep the chart name (renaming
  touches RBAC, policies and tests). In `0024` list who calls what, for web-block-config:
  `provisioning-api` calls `CheckAccess` and `GetEntitlement`; `provisioning-worker` calls `GetEntitlement`;
  `provisioning-build-worker` calls `GetRepository`. Tell the owner the Notion example needs this name.
- Rule: Notion 3.8 ("list exactly the ones that make the call").
- Test: none (doc).

### 9. Chart egress for identity, gRPC and traces (before the first production deploy)

- What: every chart pod has default-deny egress. The API may reach only DNS and its database
  (`networkpolicies.yaml:35-37`), so it cannot fetch the JWKS or call Project or Payment. Add value-driven egress:
  API to the JWKS host on 443 and to Project and Payment on 9090; worker to Payment on 9090; build worker to Project
  on 9090; every process to Tempo on 4318 (task 10). Check in web-block-config how the `webblock` namespace gets its
  Istio data plane and add what the sidecar needs. Empty values add no rule, as today.
- Files: `deploy/helm/provisioning/templates/networkpolicies.yaml`, `deploy/helm/provisioning/values.yaml:80-86`,
  `deploy/helm/provisioning/values.schema.json`.
- Rule: contract section 8 (JWKS reachable from inside the cluster); Notion 3.8 and 5.1.
- Test: `make chart-check` with new fixture values (rule present when set, absent when empty).

### 10. Turn tracing on in production with Tempo (config only)

- What: with `OTEL_EXPORTER_OTLP_ENDPOINT=none` (chart default) Provisioning drops the incoming `traceparent` and
  sends none (`tracing.py:266-300`), so the Istio trace breaks at Provisioning. Production sets
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo.monitoring.svc.cluster.local:4318` for all four processes. Fix the chart
  comment that names `otel-collector.observability`. Production values live in web-block-config: write them for the
  owner, do not apply. The sampler `ParentBased(ratio 0.1)` already matches Istio's 10 percent.
- Files: `deploy/helm/provisioning/values.yaml:35-37`; owner side: web-block-config (Tempo port in
  `istio/istiod-values.yaml:20-24`, sampling in `mesh-telemetry/values.yaml:16`).
- Rule: Notion 7.3 ("Provisioning already sends its own spans and keeps doing so"); commons 4.6.
- Test: `make chart-check`; `tests/unit/infrastructure/telemetry/test_tracing.py` unchanged and green.

### 11. Production uses gRPC CheckAccess (waits for Project)

- What: Project has only the HTTP check today (`project-service/.../AccessCheckController.java`), no gRPC
  `AccessService`. When Project ships it: the production example uses `PROJECT_ACCESS_MODE=project-grpc` with
  `PROJECT_GRPC_TARGET=project.webblock.svc:9090`; production refuses `project-service`; the HTTP client becomes
  prototype-only or is removed. Both modes fail closed today, so nothing is unsafe meanwhile.
- Files: `.claude/docs/runbooks/identity.md:50`, `:61`; `src/provisioning/infrastructure/config/api.py:402-446`;
  `src/provisioning/infrastructure/project/access.py`.
- Rule: decisions file ("Ownership ... is answered by Project `AccessService/CheckAccess` (gRPC)"); Notion open point 5.
- Test: `test_api.py` (production refuses `project-service`); `test_grpc_access.py` unchanged.

### 12. Website images in the Gitea container registry (owner answered: private, pull Secret per site)

- What: the code builds `<registry>/webblock/sites/<projectId>` (`start_build.py:127-129`, `deploy.py:218-222`) and
  the admission policy prefix is a ConfigMap value. Verify that the Gitea version in web-block-config accepts the
  nested name (owner `webblock`, image `sites/<id>`); if not, record the new layout in `0024` and change both places
  and `deploy/prototype/policy/site-params.yaml.tmpl`. Pull model (Owner, replaces answer O3): images
  are private. One read-only Gitea account pulls; its token is a Kubernetes Secret, never logged. The bootstrap
  writes an image pull Secret into each `wb-<id>` and site pods use it. Say so in `README.md:36`. Spec: FR-53 and
  TR-52 (TR-50 removed); cluster reachability notes under "Open infrastructure notes" in
  `.claude/docs/requirements/TRD.md`.
- Rule: decisions file "Registries"; Notion 5.4 and 7.2.
- Test: unit tests of `start_build` and `deploy`; `make proto-test-kind` for the image policy.

### 13. Pass `traceparent` even when no exporter is set (after commons)

- Do not build a stop-gap; task 10 covers production. For the commons builder: Provisioning keeps its own tracer
  provider and never sets the global one (`tracing.py:33-35`). The library must read the current OpenTelemetry
  context, must not install a second provider, and must keep Provisioning's span scrubbing (`tracing.py:11-19`).

### 14. RabbitMQ `x-request-id` header; request id in worker logs (after commons)

- Today: publish adds `traceparent` only when tracing is on and sets the AMQP `correlation_id` property, no
  `x-request-id` header (`infrastructure/rabbitmq/client.py:433-446`). Consumers open the span
  (`consume_span` in `infrastructure/rabbitmq/client.py:162`) but bind no request id, so worker log lines have none.
- Note for the commons builder: commands and usage events are published later by a background loop (outbox), so
  there is no request context at publish time. Use the stored `command_dispatch.correlation_id` (it is the request's
  `X-Request-Id`) as `x-request-id`. There is no column for `traceparent` (open question 2).
- Rule: Notion 4.1 ("Message headers also carry `traceparent` and `x-request-id`"); commons 4.6 item 2.

### 15. Log fields `request_id` and `span_id` (after commons)

- Today: lines carry `correlation_id` (the request id) and `trace_id` only when tracing is on; no `span_id`
  (`infrastructure/telemetry/logger.py:40-41`, `:259-265`). Do not rename now: the commons fixes the shape once, and
  each rename breaks Loki queries and `.claude/docs/runbooks/observability.md`.
- Note for the commons builder: keep Provisioning's allow-list and scrub (`logger.py:84-128`, `:156`). The Python
  package should offer a structlog processor or a context getter, not a second logger. Keep the request id form
  `[A-Za-z0-9._:-]{1,128}` (`presentation/http/request_id.py:22`, the envelope's `correlation_id`), and keep 503
  `IDENTITY_UNAVAILABLE` (not 401) when no key set can be fetched (`identity/verifier.py:133-137`).

## Checked, no change needed

- Token forwarding: none. Project (gRPC and HTTP), Payment, Jenkins (its own API token), Kubernetes and the catalog
  get no user token; no code adds an `Authorization` header.
- Inbound `x-user-id`: Provisioning has no internal endpoint (no gRPC server, no `/internal` route) and reads no
  `x-user-id`, so no allow list is needed. A future internal method uses the commons "allowed callers only".
- Ownership: gRPC `CheckAccess`, 2 s deadline, retry only UNAVAILABLE twice (`grpc/call.py:28-29`, `:101`), fail
  closed (`grpc_access.py:76-98`). Not the owner: 404 `PROJECT_NOT_FOUND`
  (`core/application/operations/gate.py:74`).
- Admin: not `ADMIN` on an admin action: 403 `ACCESS_DENIED` (`core/application/operations/gate.py:51-59`).
- Roles: production forces `AUTH_SCOPE_CLAIM=roles` (`config/identity.py:183-187`); `realm_access` is never read.
- Issuer exact, audience a whole element, exp and nbf with skew: `verifier.py:173-197`.
- `sub` stays required: Notion stores it in `requested_by_subject` and the architecture page allows that use.
- Tokens in logs, traces, Sentry, errors: refusals log a fixed cause only; the logger is an allow-list with scrub;
  span export drops header attributes; Sentry drops request data. 401 answers echo nothing.
- Outgoing gRPC already sends `x-request-id` and, when tracing is on, `traceparent` (`grpc/call.py:93`). Calls to
  Jenkins carry neither, which is right (outside host).
- Sampling is parent based (`tracing.py:140`).
- `webblock-ui`, CouchDB, MongoDB: not mentioned anywhere. `webblock-system`: only in history (decision records,
  `ai/reviews/`, an older audit in git history); code and chart use `webblock`.
- `source_published`: consumed from queue `provisioning.source_published.v1`; Provisioning never writes to Git.

## Open questions for the owner

None open.

## Answered by the owner

1. Website images in the Gitea container registry: private, with a pull Secret per site namespace written by the
   bootstrap from one read-only Gitea account (owner; replaces answer O3). Task 12, next phase.
2. Rollback: `ADMIN` only (answer O1). Task 5 and L3 keep `rollback_release=ADMIN`.

## Answered by the lead

- Outbox and `traceparent`: the link by request id (`command_dispatch.correlation_id`) is enough. No new column, so
  the Notion "5 · Provisioning" design stays as it is. Record this in decision 0024.
- Key set unreachable: web-block-commons keeps 503 `IDENTITY_UNAVAILABLE` (design section 4.1), same as today.
