# 0023: Provisioning Phase C, the REST shape release and admin operations

Status: Accepted; built and merged into `python` (merge `2c4a5ea`). The gates still owed are in
`ai/handoffs/after-phase-c.md`.

## 1. Context

Phase A (decision `0021`) moved the database to the Notion design. Phase B (decision `0022`) moved the messaging and
service-to-service edge. Phase C is the last slice of the Provisioning part of "Web Block architecture"
(https://app.notion.com/p/40358175966e8198a088f123a9ab23fe): the REST shape of section 2 (success body, camelCase,
cursor paging, Problem bodies, status table), the gRPC error rule of section 3.4, and the admin operations that "5 ·
Provisioning" plans and Phase A already put in the database (suspend and resume with the reason `admin_suspended`,
redeploy, and the admin-only read of the requester).

Facts this work rests on:

- The Notion pages are read-only and the code follows them. Where Notion is silent, the lead chooses inside what Notion
  defines and records the choice here. Notion names no REST path for the admin operations.
- The database is ready since migrations `00020` and `00021`. The API refuses `redeploy` and `admin:` keys before any
  SQL, and the worker leaves `redeploy` out of its enabled kinds (`0021` D10). Phase C lifts both refusals. No migration
  is needed.
- Decision `0018` (Problem Details) is recorded but never implemented. Its plan, task PD
  (`.superpowers/sdd/python/2026-10-14-problem-details.md`), was never approved. Its decisions 1 to 12 (percent-encoded
  `instance`, the `Accept` rule, the `/mcp` HTTP refusals, the member order) still hold where they do not conflict with
  Notion. `requestId` and `traceId` replace `correlation_id`.
- The audit (`git show de31486:docs/notes/2026-10-24-provisioning-audit.md`) has the audit table C.1.3, which maps every
  code we send today to its new code and status. G6 adopts it.
- An `ADMIN` already rolls back any project without the owner check (`0019` section 7).
- The clients today are the frontend's Provisioning screens, the Bruno guide `.claude/docs/guides/bruno/`, the ten MCP
  tools, the contract tests and the e2e harness.

This record holds the decisions. The finished design and plan are in git history:
`git show 94d57e1:.claude/docs/designs/2026-10-25-provisioning-phase-c-design.md` and
`git show 94d57e1:.claude/docs/plans/2026-10-25-provisioning-phase-c.md`.

## 2. Global constraints

- Branch `phase-c` from `python` (`893016c`). Never push, never merge.
- Notion is the source: "Web Block architecture" section 2 (REST), 3.4 (gRPC errors), 5.3, and "5 · Provisioning" (admin
  rules). Where Notion is silent, the spec's lead decisions apply. Notion is read-only.
- No database migration. `infrastructure/postgres/schema.py` does not change.
- The decision record changes before a rule changes in code: Task 1 writes
  `.claude/docs/decisions/0023-phase-c-rest-shape-and-admin.md`.
- Success body: `{"success": true, "data": <object or list>, "meta": {"requestId", "traceId" (only when sampled),
  "timestamp" (UTC, milliseconds, `Z`), "path" (no query), "status", "page" (null except lists: {"limit",
  "nextCursor"})}}`. Content type `application/json`.
- Problem body members in order: `type`, `title`, `status`, `instance` (REST only), `code`, `requestId`, `traceId` (only
  when sampled), `errors`, `commandId` (only when a command is involved). `errors` always present; entries
  `{field, code, message}`, `code` from `REQUIRED`, `INVALID_FORMAT`, `OUT_OF_RANGE`, `TOO_LONG`; `message` fixed text.
  Never the caller's value except the percent-encoded path in `instance`.
- Names a client sees are camelCase: body fields, query and path parameters, MCP tool arguments. Enum values unchanged.
  `github_repository_id` becomes `repositoryId`.
- Paging: `limit` 1 to 50, default 20; `cursor` opaque; `meta.page.nextCursor` null on the last page.
- Codes UPPER_SNAKE, one enum, statuses per the audit's table C.1.3
  (`git show de31486:docs/notes/2026-10-24-provisioning-audit.md`, lines 126 to 146) plus `REDEPLOY_NOT_ALLOWED` (422)
  and `RESOURCE_NOT_FOUND` (404). 409 only for duplicates and stale changes; 422 business rules; 429 only rate; 503
  unavailable; 504 deadline.
- Concealment: no access and unknown project are both `PROJECT_NOT_FOUND`, byte for byte (apart from `requestId`,
  `traceId`, `instance`).
- Admin paths under `/v1/admin/projects/{projectId}`: `POST hosting/suspend`, `POST hosting/resume`, `POST redeploy`,
  `GET commands`. Role `ADMIN` only; no Project `CheckAccess`. Admin hosting keys stored `admin:<Idempotency-Key>`,
  reason `admin_suspended`, `requested_by_subject` = the admin's `sub`.
- Redeploy: routing `stable`, hosting `online`, a live release; `release_id` = the live release; restart annotation
  `websites.webblock.internal/restart-command: "<command number>"` on the pod template; never touches routes,
  `routing_status`, release status or `usage_outbox`.
- Production profile: every mutation still refused (task H2), admin ones included; the admin read allowed.
- `openapi.yaml` and `tools.json` move to `0.2.0`, still `x-status: draft`.
- Unit tests under `tests/unit/` mirror the package and import only public names. No `__init__.py` under `tests/`.
  Dependency suites fail, never skip. One Testcontainers or Kind stack at a time.
- Never log a token, a body or the caller's raw values. Docs: plain, easy English, short, no em dash and no en dash.
- Never stage the owner's untracked files: `db-design-new.md`, `docs/new-platform-db-design.md`, `reports/`,
  `research_notes/`.
- Fast gates after every task: `make py-fmt-check py-lint py-typecheck py-layers py-test`; plus
  `make py-test-integration` when a store changes and `make py-test-messaging` when a consumer changes.
- The real cluster is never touched (CLAUDE.md section 0).

## 3. Decisions G1 to G14

The owner delegates routine choices. Each is recorded here before code changes.

### C-REST

- **G1. One breaking release.** All REST and MCP shape changes land together on this branch. `openapi.yaml` and
  `tools.json` go from `0.1.0` to `0.2.0` (still `x-status: draft`, OD-25 open). The Bruno guide is updated in the same
  change. Decisions 0006 section 3, 0017, 0018 and 0019 are amended in 0023 where they conflict.
- **G2. Success body.** Every 2xx answer is `{"success": true, "data": ..., "meta": {...}}`. `202` keeps its status, and
  `data` is the accepted command. `meta` has `requestId`, `traceId` (only when the request is sampled), `timestamp`
  (UTC, milliseconds), `path` (no query), `status` and `page` (`null` except on lists).
- **G3. camelCase everywhere a client sees a name.** Body fields, query parameters and path parameters (`{projectId}`,
  `{releaseId}`, `{commandId}`). `github_repository_id` becomes `repositoryId` (0021 D3). The values of enums (`live`,
  `candidate_ready`) stay as they are: they are values, not names.
- **G4. Paging.** `?limit=` (1 to 50, default 20) and `?cursor=`. Lists put the items in `data` and
  `{limit, nextCursor}` in `meta.page`; `nextCursor` is `null` on the last page. The cursor stays opaque (today's token,
  renamed).
- **G5. Problem body.** Members in this order: `type`, `title`, `status`, `instance`, `code`, `requestId`, `traceId`
  (only when sampled), `errors`, then `commandId` when a command is involved. `errors` is always present, empty when the
  error is not about a field. A 400 fills it with `{field, code, message}` per bad field (path IDs, `Idempotency-Key`,
  `limit`, `cursor`); `code` is UPPER_SNAKE from one fixed list (`REQUIRED`, `INVALID_FORMAT`, `OUT_OF_RANGE`,
  `TOO_LONG`, as Notion 2.2 shows) and `message` is fixed text, never the value. `correlation_id` is gone: `requestId`
  replaces it. Task PD's decisions 1 to 12 apply, with `requestId` and `traceId` in place of `correlation_id`.
- **G6. Codes and statuses.** The audit's table C.1.3, as written, plus the new codes of G10. 409 only for duplicates
  and stale changes, 422 for business rules, 429 only for rate, 503 for an unavailable dependency, 504 for a deadline.
  The four 404s keep concealment: no access and unknown project are both `PROJECT_NOT_FOUND`, byte for byte;
  `RELEASE_NOT_FOUND`, `COMMAND_NOT_FOUND`, `DEPLOYMENT_NOT_FOUND` only after project access passed.
- **G7. MCP follows REST.** A tool's result is the same object REST returns in `data`; a tool error is the same Problem.
  Tool argument names are camelCase. MCP has no envelope (the JSON-RPC result already carries the call). A list tool
  returns `{"items": <the REST data list>, "page": <the REST meta.page>}`, because an MCP result is an object.
- **G14. gRPC errors keep their reason (Notion 3.4).** When a call to Project or Payment fails and the answer to our
  caller is an error, the HTTP status follows the gRPC code by Notion's table 2.3 (`UNAVAILABLE` 503,
  `DEADLINE_EXCEEDED` 504, and so on) and the REST `code` is the upstream `ErrorInfo.reason` when one is present and it
  is UPPER_SNAKE; otherwise our own code (`PROJECT_ACCESS_UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE`, `DEPENDENCY_TIMEOUT`).
  The 404 concealment of G6 still wins: an access answer never reveals whether a project exists. Today's fail closed
  rule does not change: no answer still means no.
- **G14 precision (lead, Task 2 review):** a project access failure never passes an upstream reason on: it is always 503
  `PROJECT_ACCESS_UNAVAILABLE`, a passed deadline included, so an access outage looks the same for every project. For
  any other failed call to Project or Payment, a reason is passed on only when it is UPPER_SNAKE and does not end in
  `NOT_FOUND`. A reason that is one of our codes takes that code's status from our table. After a passed deadline the
  answer is 504, with the reason as the code, else `DEPENDENCY_TIMEOUT`. Any other passed reason is 503 with the reason
  as the code. When the operation does not declare the resulting status, the answer falls back to our own code and
  status (503 `DEPENDENCY_UNAVAILABLE`, or 504 `DEPENDENCY_TIMEOUT` after a deadline), never to 500.
- **G8. Phase B parked items.** (a) `StoreUnreachable` answers 503 `DEPENDENCY_UNAVAILABLE`. (b) replaced by G15: every
  failure counts. (c) The trace exporter scrubs the span status description to the exception type. (d) The settings
  refuse any `*_STORE_HOLD_MAX` that is not below `RABBITMQ_CONSUMER_TIMEOUT`.

### C-ADMIN

- **G9. Paths.** Notion says admin pages call admin endpoints; it names none. Under `/v1/admin/projects/{projectId}`:
  - `POST .../hosting/suspend`: an admin suspend, reason `admin_suspended`.
  - `POST .../hosting/resume`: an admin resume, removes only `admin_suspended`.
  - `POST .../redeploy`: a redeploy of the current live release.
  - `GET .../commands`: the project's commands with `requestedBySubject`, paged like the client list.
  Each takes `Idempotency-Key` and no body (or `{}`), as today's mutations. Each answers 202 with the accepted command,
  or 200 for the read. The client paths stay as they are; `rollback` keeps its client path and its ADMIN rule.
- **G10. Who may call.** Only a caller with role `ADMIN`. No Project `CheckAccess` call (0019 section 7: an admin acts
  on any site). A `USER` gets 403 `ACCESS_DENIED`. Four new actions with scope `ADMIN`: `suspend_website`,
  `resume_website`, `redeploy_release`, `admin_list_commands`. New codes: `REDEPLOY_NOT_ALLOWED` (422: no live release,
  or hosting offline) and `RESOURCE_NOT_FOUND` (404: an unknown path). Reused: `ROUTE_NOT_STABLE` (422) and
  `COMMAND_IN_PROGRESS` (409).
- **G11. Admin hosting commands.** The API stores the key as `admin:<Idempotency-Key>`, kind `suspend_hosting` or
  `resume_hosting`, reason `admin_suspended`, `requested_by_subject` the admin's `sub`. The worker runs them with the
  existing hosting executor: no entitlement re-check (that is only for `billing`), the reason rules of 0021 (D5, D6, D7)
  unchanged. The API store's refusal of `admin:` keys (0021 D10) goes; the hosting intake's refusal of a Payment key
  starting with `admin:` stays.
- **G12. Redeploy executor.** The worker re-applies the live release's Deployment `rel-<releaseId>` by server-side
  apply, with the command's fence annotation and one pod template annotation
  `websites.webblock.internal/restart-command: "<command number>"`. A new value starts a rolling restart with the same
  image digest. The worker waits until the rollout is complete and every pod is ready, within the command's deadline,
  with the same readiness rules as a candidate. It never touches the routes, `routing_status`, the release status or
  `usage_outbox`. Outcome: `succeeded`; or `failed` with `redeploy_unready` (rollout not ready by the deadline; the
  rolling update keeps the old pods serving), or `redeploy_target_changed` (the live release is no longer the command's
  release when it runs). The admission policies must allow the new template annotation and nothing else new; the
  manifests agent changes `deploy/prototype/policy/` only if a policy refuses it.
- **G13. Profiles.** The prototype enables the new kinds (`redeploy` joins `PROTOTYPE_KINDS`; the admin routes are
  mounted). The production profile keeps refusing every mutation while OD-01, OD-04 and OD-05 are open (task H2), admin
  ones included; the admin read is allowed in production like the other reads.


### Owner decisions: follow Notion strictly, the database design first

- **G15. Retries count outages (owner).** Notion 4.3: "a failing message goes back to the queue up to 5 times, then to
  the `.dlq`". Every failure of a message counts toward the 5, a database outage (`StoreUnreachable`, a statement
  timeout) included. This replaces Phase B's final-review ruling. Only deliberate waits that are not failures stay
  uncounted: lock contention (`Contention`), the inbox per-project cap and the hosting retry budget (its own
  `.retry-<n>` commands). After an outage an operator re-drives the `.dlq` queues (Notion 4.3: "someone looks at the
  `.dlq` and moves messages back after the fix"). G8b is replaced by this rule.
- **G16. Physical column order is Notion's (owner).** A new migration rebuilds every table whose physical column order
  differs from Notion's field list, so the layout matches Notion exactly. Rows, keys, indexes, checks, functions,
  triggers and grants are kept. It ships with its change report (changed, not changed, UI impact) and an upgrade test
  with representative rows. The independent audit
  (`.superpowers/sdd/2026-10-25-provisioning-phase-c/db-notion-audit.md`) lists the tables and any other difference from
  Notion; every DIFFERENT finding is fixed in this phase or recorded with the owner's decision.
- **G17. An older or equal owner-status version changes nothing (Notion).** Notion: "an older event changes nothing, so
  the newest wins", and `owner_status_version` never decreases. An event whose `status_version` is at or below the
  stored version is acked as `STALE` and writes nothing, whatever its status. This replaces the Phase B ruling that
  dead-lettered a repeated version with another status.
- **G18. Hostname from the sub-domain (Notion).** Notion: "the route is `<subdomain>.<site domain>`". Routing, the
  preview route, the admission policy that checks route shapes and the e2e harness switch from
  `<projectId>.<site domain>` to `<subdomain>.<site domain>`. The preview host follows the same rule with its existing
  prefix. Moved into Phase C from Phase D.
- **G18a. Bootstrap reads the sub-domain.** Migration `00023` grants
  `SELECT (subdomain) ON provisioning.project_hosting TO provisioning_bootstrap`, so the bootstrap writes the sub-domain
  into the tenant parameters and the route-shape admission policy keeps refusing a host that is not the project's own
  (the hostname-takeover guard). Notion lists `provisioning_bootstrap` as "reads only"; this is a read. It ships with
  the migration rules, an upgrade test and its change report.
- **G18b. Preview host `<subdomain>-preview.<site domain>` (FR-20, FR-52, FR-55; owner ruling PREVIEW1).**
  Replaces G18's "existing prefix" for the preview host only. The preview host is one label under the site domain, so
  the one wildcard certificate `*.<site domain>` covers it: the free Cloudflare certificate covers `*.roaa15.xyz`, one
  level, and previews are public with the project's token (G18c). A DNS label has at most 63 characters, so a hosted
  sub-domain has at most 55; and project `shop`'s preview host `shop-preview.<d>` is the site host of a sub-domain
  `shop-preview`. So the bootstrap gives no tenant to a sub-domain ending in `-preview` or of 56 to 63 characters
  (FR-55), and without the namespace label the route-shape policy admits no route for it; `preview` alone is allowed.
  The admission policy `webblock-bootstrap-scope` refuses such a new sub-domain label too, so a bootstrap bug cannot
  write one; a label left unchanged is not checked. The database keeps Notion's check, and Project caps sub-domains at
  55 characters (asked through project-service-14). The route-shape policy refuses the older hosts
  `<subdomain>.preview.<site domain>` and `preview-<subdomain>.<site domain>` (Task 12 review I1 and I2). Notion does
  not define the preview host, so Notion does not change. It covers, all in Provisioning: `preview_hostname` and the
  domain's `preview_label`, the renderer's and the probe's host checks, the route-shape and bootstrap-scope admission
  policies, the Gateway's one wildcard certificate, the golden and example manifests, the `preview-only` sample site
  and the runbooks. The route names (`preview-<projectId>`) do not change. The k3d run must cover the new host.
- **G18c. One preview token per project (Task 12 security
  review S1).** The preview route and the probe no longer carry the worker's secret `PREVIEW_PROBE_TOKEN`. They carry
  the project's token `base64url(HMAC-SHA256(PREVIEW_PROBE_TOKEN, projectId))` without padding. The probe sends the
  header only when the promotion names the project's sub-domain and the host is exactly its preview host
  `preview_hostname(subdomain, site domain)`; any other host, a site host or another project's `-preview` host, gets
  no token. The preview rule removes the header (`RequestHeaderModifier`, `remove: [X-Webblock-Preview]`) before the
  request reaches the site; the route-shape policy admits that one filter on `preview-<projectId>` only, setting or
  adding nothing. Reason: a site that logged the header learned the platform-wide token and could open any other
  project's preview. Now a site can learn only its own preview's token. Every preview answer carries
  `Cache-Control: private, no-store`, set in the rule's one `ResponseHeaderModifier` beside `X-Webblock-Release`; the
  route-shape policy requires it on every preview rule and admits no other response header there, so a shared cache
  (Cloudflare) never serves the candidate to a visitor without the token (FR-52.1;
  [0016](0016-production-environment.md) section 10 item 4). The k3d run must cover it.
- **G20. The database matches Notion 100%.** No difference of any kind between the migrated database and Notion "5 ·
  Provisioning" (tables, columns, types, NULL rules, physical order, enum values and order, constraints, roles, grants).
  Objects Notion does not name (indexes, checks, guard triggers) exist only to enforce a rule Notion states. A final
  independent audit after the last migration of Phase C must report zero differences before the merge.
- **G20a. An index that enforces a rule Notion states counts as matching Notion (owner; Task 12
  security review S2).** Migration `00024` adds the unique index `project_hosting_subdomain_key` on
  `provisioning.project_hosting (subdomain)`: Notion "2 · Project" makes a sub-domain unique over all projects, and the
  route-shape admission policy now trusts the sub-domain as the project's own host. The owner adds the index to the
  Notion page. An owner-status event that would insert a taken sub-domain is refused as `SubdomainTaken` and
  dead-lettered at once, never retried. Change report:
  `/home/roa/Documents/web-block/platform-db-rev25-provisioning-00024-report.md`.
  Withdrawn: the owner's rule, the database matches Notion 100%; replaced by a code check.
  Migration `00024` and its index are removed; the check is FR-54 and TR-53 (`.claude/docs/requirements/`): a lock on
  the sub-domain, the hosting intake's check, and the bootstrap's refusal of a tenant whose sub-domain two rows hold.
- **G19. Folder layout (Notion 6.2).** `workers/` and `devtools/` move into the four layers with no behaviour change:
  consumers to `presentation/messaging`, publishers (dispatcher, usage publisher) to `infrastructure/rabbitmq`, loops
  and executors to `core/application` or `infrastructure` by what they import (import-linter decides), `devtools/` under
  `hosts/`. Moved into Phase C.
  Amended by folder layout rule FL6: the dispatcher and the usage publisher go to `core/application` (`commands/`,
  `usage/`), not `infrastructure/rabbitmq`, because they import no broker code; Task 13 did the move.

## 3a. Checked against Notion

| Notion | Spec |
| --- | --- |
| 2.1 success body, `meta` members, `traceId` only when traced, `page` null except lists | G2, G4 |
| 2.1 camelCase field names | G3 |
| 2.2 Problem members and order, `errors[]` with UPPER_SNAKE codes, never echo the input | G5 |
| 2.2 codes UPPER_SNAKE, thing first, one enum per service | G6, G10 |
| 2.3 status table | G6 (413 `REQUEST_TOO_LARGE` kept: Notion has no row for it) |
| 2.4 `ADMIN` unlocks suspend, resume, redeploy, roll back any site | G10 |
| 3.4 gRPC reason kept as the REST code | G14 |
| 3.5 `x-request-id` from the first service | Phase B (0022 E8) |
| 5.3 Provisioning `/v1`; admin pages call admin endpoints for suspend, resume, redeploy | G9 |
| 5 · Provisioning: admin reason `admin_suspended`, key `admin:<key>`, subject required, redeploy rules, admin-only read of the subject | G9, G11, G12 |

Where Notion is silent, the spec chooses inside what Notion defines: the admin paths (G9), the extension member
`commandId` (G5), the restart annotation (G12) and `RESOURCE_NOT_FOUND` (G10).

## 4. Consequences

- One breaking release. Every client of the REST and MCP edge changes at once: the frontend's Provisioning screens must
  switch at the merge, together with the Bruno guide (updated in the same change) and any agent that calls the MCP
  tools. `openapi.yaml` and `tools.json` go to `0.2.0`, still `x-status: draft` (OD-25).
- The old paging names `page_size` and `page_token` are gone (G4). As today (0006 section 3), an unknown query parameter
  is refused: 400 `VALIDATION_FAILED` with `errors[{field: "query", code: "INVALID_FORMAT"}]`, so an old client fails
  clearly instead of silently reading the first page. The parameter name is not echoed.
- `correlation_id` is gone from error bodies and from the contracts. `requestId` replaces it, and `traceId` appears when
  the request is sampled. The `X-Request-Id` header of decision `0022` E8 is unchanged.
- Every success answer has the envelope. A client that read a bare object now reads `data`. The `202` status of an
  accepted command is unchanged.
- Error codes change from lower_snake to UPPER_SNAKE, and some statuses move to Notion's table (409 only for duplicates
  and stale changes, 422 for business rules). Decision `0006` section 3 is amended for this. `request_too_large` stays
  413 as `REQUEST_TOO_LARGE`, because Notion has no row for it.
- The admin paths under `/v1/admin/projects/{projectId}` are new. They need the role `ADMIN`, call no Project
  `CheckAccess`, and are refused in the production profile like every mutation. The admin read stays allowed.
- `redeploy` joins the worker's enabled kinds in the prototype profile. The API store stops refusing `admin:` keys; the
  hosting intake still refuses a Payment key that starts with `admin:`.
- The admission policies may need to allow one new pod template annotation. They change only if a policy refuses it.
- There is no database migration and `infrastructure/postgres/schema.py` does not change.
- The Phase B parked items (G8) change the behaviour of the consumers on database errors and the settings checks, as
  listed there.

## 5. What was built and tested

| Task | What was built | Evidence |
| --- | --- | --- |
| 1 | This record (G1 to G14) | this commit |

The remaining rows and the gate tables are filled when the owed gates run (`ai/handoffs/after-phase-c.md`).

## 6. What stays open

- The hostname cut-over (`<projectId>.<site domain>` to the stored sub-domain), the folder tidy and the cluster deploy:
  not in Phase C.
- Payment's `publish_completed` event, which Provisioning owns the schema of and no consumer reads yet.
- The draft protos and event schemas of decision `0022` E2 (OD-01 repository part, OD-04): the Project and Payment teams
  review them. The repository and entitlement modes stay refused in the production profile.
- OD-25: the response shapes are still a draft. Phase C fixes them at `0.2.0` for review, but the UI and agent consumers
  have not signed off.
- The `/api/v1` prefix is not adopted; the paths stay under `/v1`.
- Production mutations stay refused while OD-01, OD-04 and OD-05 are open, admin ones included (task H2).
