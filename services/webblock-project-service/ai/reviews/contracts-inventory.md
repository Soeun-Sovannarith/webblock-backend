# Research: every exchange of the Project service with other services, and what is missing in writing

Stage: 1 Specify support (facts for the contracts docs of `ai/handoffs/next.md`, step 4).
IDs: FR-30 to FR-34, FR-37, PR-18, and every FR or PR an exchange serves (PR-8 to PR-11 and PR-18 to PR-22 have no FR
yet, FRD line 349).

Questions asked:

1. List every exchange Project has with Provisioning, Payment, AI Codegen, Account and Keycloak, Notification (and the
   frontend), with direction, transport, payload, errors, owner, where it is written, state, FR IDs, and whether the
   code builds it.
2. Which gaps exist between what is written and what is needed? Confirm or refute: the publish result for FR-37, the
   repository lookup `GetRepository` (PR-18), `source_published` and `project_deleted` against Provisioning's consumer,
   Payment's suspend signal, anything with Notification.
3. Where do Notion and the repo disagree?

Sources read (all read-only, no branch switched, no cluster):

- Notion "Web Block architecture", last edited 2026-10-28T10:05:03Z (called S2 below).
- Notion "2 · Project", last edited 2026-10-22T10:17:01Z (S1).
- Notion "5 · Provisioning", last edited 2026-10-27T08:42:46Z; Notion "4 · AI", last edited 2026-10-21T15:00:20Z.
- This repo: `.claude/docs/contracts/*`, `.claude/docs/requirements/*` (FRD, PRD, TRD, spec.json, DIRECTION.md),
  `src/main`, `contracts/`, `contracts.lock` (SHA `afff9245444c628b74f88d281939f4bc8b4de3e2`).
- `../web-block-contracts` branches `main` (HEAD `afff924`) and `access-actions-v1` (HEAD `ea4bc7f`); no git remote.
- `../provision-service` branch `cqrs-layout` (HEAD `fc5ff57`), files read in the working tree. Its `contracts.lock`
  is the same SHA `afff924...`, and its copies of the six Project and Provisioning contract files are byte identical to
  `main` (checked with `diff`).

## Short answers

1. The inventory has 34 rows (table below). Built in Project today: 10 (REST access check, gRPC `CheckAccess`,
   REST codegen input, `generation_requested` event, three Payment HTTP calls, catalog database, Keycloak token
   check, frontend REST). Everything else is not built.
2. FR-37: `provisioning.publish_completed.v1` is enough to flip `draft` to `active` (it has `project_id`, `release_id`,
   `message_id`, `occurred_at`), but Project is not a listed consumer anywhere, and "first successful publish" is not
   defined (commit in Git, or release live). No failure event exists, and FR-37.2 needs none.
3. `GetRepository`: `repository.proto` fits Provisioning's client exactly (checked against its code). Four details are
   open: where `clone_url` comes from, no commit confirmation field, the error reason, and the mesh caller.
4. `source_published`, `project_deleted`, `owner_status_changed`: the schemas Provisioning pins are identical to
   `main`. Provisioning reads four fields of `source_published` and ignores `subdomain` (it takes it from
   `GetRepository`). All three are DRAFT "proposed by provisioning, waits for project": Project must accept them.
5. Payment's suspend signal: none exists and none is needed for `projects.status`. Notion says `suspended` is not used
   for unpaid plans. The FRD says "set only by Payment or an admin"; that has no contract behind it.
6. Notification: nothing is written beyond one Notion row each for a consumer queue and `GetProjectOwner`.
7. Notion and the repo disagree in 12 places (section "Notion against the repo"). The big ones: Payment and AI over
   gRPC in Notion but HTTP in the repo; Gitea in Notion but GitHub in `service-communication.md`; the AI hand-over is
   a push call in one Notion row and a pull in the rest of Notion.

## Findings

### Inventory table

Columns: Id, name, direction, transport, payload, errors, owner, written where, state, serves, built in Project.
State words: agreed, draft, proposed by Project and waiting, proposed by other and waiting for Project, missing,
stale. "Contracts main" means `web-block-contracts` branch `main` at `afff924`.

| Id | Name | Direction | Transport | Payload | Errors | Owner | Written where | State | Serves | Built in Project |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X1 | Access check, REST | Provisioning calls Project | REST `POST /internal/v1/access-checks`, no token | `{userId, projectId, action}` to `{allowed, reason}` in the success envelope; reasons `NOT_FOUND`, `NOT_MANAGER`, `PROJECT_DELETED`, `PROJECT_SUSPENDED` | 400 on bad action or UUID; any non-200 is "unavailable", caller fails closed | Project | `.claude/docs/contracts/provisioning-contract.md` section B; `service-communication.md` section 3; Provisioning `infrastructure/project/access.py`; not in contracts repo | agreed (Provisioning mode `project-service`); to be removed (FR-34) | FR-30, FR-6, FR-34, PR-12 | yes (`AccessCheckController`) |
| X2 | Access check, gRPC `CheckAccess` | Provisioning calls Project (Notion: also the auth library and site portals) | gRPC port 9090 | `CheckAccessRequest{user_id, project_id, action}` to `{allowed, reason}`; no envelope | unknown project is OK with `allowed=false`, reason `NOT_FOUND`; bad input `INVALID_ARGUMENT` with `ErrorInfo`; busy database `UNAVAILABLE` | Project | `proto/webblock/project/v1/access.proto` on contracts main, header ACCEPTED; `.claude/docs/runbooks/grpc.md`; decision 0011; Provisioning `grpc_access.py` | agreed; mesh policy for the owner not applied | FR-31, FR-33, TR-24, TR-25, TR-26, PR-13 | yes (`AccessGrpcService`) |
| X3 | Action list and `manage_site` | Site portal and Provisioning use the list Project accepts | data file read by Project at build time | 11 actions with `name`, `write`, `admin_allowed`; adds `manage_site` | unknown action refused 400 (REST) or `INVALID_ARGUMENT` (gRPC) | Project | `access/actions.v1.json` and schema only on branch `access-actions-v1` (`ea4bc7f`), header DRAFT, not pushed | proposed by Project, waiting for the owner to push | FR-32, TR-27, PR-14 | no: `MANAGE_SITE` missing in `AccessAction.java` (Drift) |
| X4 | Site portal ownership check | Website portal (tenant namespaces `wb-*`) calls Project | Repo: `CheckAccess` over the mesh. Notion open point 3: a small public REST endpoint behind Cloudflare, because sites run on another cluster | `user_id` from the portal token (audience `site`), action `manage_site`; site caches 60 s | as X2 | Project | `service-communication.md` section 3 (row "Site portal"); Notion S2 section 9 point 3; nothing in contracts | missing for the public endpoint; draft for the mesh path | PR-14, FR-32 | no |
| X5 | Repository lookup `GetRepository` | Provisioning build worker calls Project | gRPC `RepositoryService/GetRepository`, deadline 2 s, retry `UNAVAILABLE` at most 2 times | in: `project_id`; out: `repository_id` (int64, 1 to 2^53-1), `clone_url`, `full_name` (logs only), `subdomain` (DNS label) | `NOT_FOUND` with `ErrorInfo.reason` `REPOSITORY_NOT_FOUND` when the project has no repository | Project | `proto/webblock/project/v1/repository.proto` on contracts main, header "DRAFT, proposed by provisioning, waits for project"; Provisioning `infrastructure/project/grpc_repository.py` (prototype only, production refuses it); Notion S2 5.1 and 3.8; PRD PR-18 | proposed by Provisioning, waiting for Project | PR-18 (no FR yet) | no |
| X6 | Repository lookup, REST version | Provisioning calls Project | REST `GET /internal/v1/projects/{id}/repository` answering `{githubRepositoryId, fullName, cloneUrl}` or 404 `NO_REPOSITORY` | as written | as written | Project | `service-communication.md` section 3 only | stale: replaced by X5 (PR-18 says "no REST version") | PR-18 | no (and must not be built) |
| X7 | `project.source_published.v1` | Project publishes; Provisioning consumes (Notion: also Payment, Notification) | RabbitMQ, exchange `project.events`, routing key `source_published.v1`, queue `provisioning.source_published.v1` | envelope plus `generation_request_id`, `repository_id`, `git_commit_sha` (40 or 64 hex), optional `subdomain`; no extra property | consumer: bad body to `.dlq` after 5 tries; same key with another repository or SHA is a conflict | Project | `events/project/source_published.v1.schema.json` on contracts main, header "DRAFT, proposed by provisioning, waits for project"; valid and invalid examples exist; `service-communication.md` section 3; Provisioning `presentation/messaging/v1.py` | proposed by Provisioning, waiting for Project | PR-19, PR-8, PR-9 (no FR yet) | no |
| X8 | `project.project_deleted.v1` | Project publishes; Provisioning and Payment consume | RabbitMQ, key `project_deleted.v1` | envelope only (`project_id`) | none; consumers dedupe (Provisioning command key `project-deleted`, Payment on `project_id`) | Project | `events/project/project_deleted.v1.schema.json`, header DRAFT proposed by provisioning; `payment-contract.md` section 4 | proposed by Provisioning, waiting for Project | PR-20, PR-10 (no FR yet) | no (no delete endpoint) |
| X9 | `project.owner_status_changed.v1` | Project publishes; Provisioning consumes | RabbitMQ, key `owner_status_changed.v1` | envelope plus `status` (`active` or `suspended`), `status_version` (int, at least 1), `subdomain` (required) | consumer ignores a version at or below the stored one | Project | `events/project/owner_status_changed.v1.schema.json`, header DRAFT proposed by provisioning; Notion S2 4.4, 5.2; Notion "5 · Provisioning"; not in any `.claude/docs` file | proposed by Provisioning, waiting for Project | PR-21 (no FR yet) | no |
| X10 | `account.user_status_changed.v1` | Account publishes; Project (and Payment) consume | RabbitMQ, queue `project.user_status_changed.v1` | not defined; Project needs user id, status, status version | not defined | Account | Notion S2 4.4 and 5.2 only; no schema in contracts; no `.claude/docs` file | missing | PR-21 | no |
| X11 | `project.generation_requested.v1` | Project publishes; AI Codegen consumes | RabbitMQ, key `generation_requested.v1` | envelope (correlation id is the request id) plus `generation_request_id`, `captured_input_hash` | Codegen: repeat is not an error; hash mismatch is `input_hash_mismatch` | Project | `service-communication.md` section 2.1; Notion S2 4.4; code `GenerationRequested.java`; no schema in contracts | agreed in the note; schema file missing | FR-27, FR-26, PR-16 | yes |
| X12 | `project.generation_cancelled.v1` | Project publishes; AI Codegen consumes | RabbitMQ, key `generation_cancelled.v1` | envelope plus `generation_request_id` | not defined | Project | `service-communication.md` section 2.5; Notion S2 4.4; no schema | missing (schema); not built | PR-7, PR-9 (no FR yet) | no |
| X13 | `ai_codegen.generation_completed.v1` | AI Codegen publishes; Project consumes | RabbitMQ, queue `project.generation_completed.v1` | envelope plus `generation_request_id`, `generation_run_id`, `outcome` (`succeeded` or `failed`) | Project moves the request to `failed` with a mapped code on `failed` | AI Codegen | `service-communication.md` section 2.4; Notion S2 4.4; no schema | missing (schema); AI side unverified | PR-9, PR-16 (no FR yet) | no (Project has no consumer at all) |
| X14 | Codegen input | AI Codegen calls Project | Repo: REST `GET /internal/v1/generation-requests/{id}/codegen-input`. Notion S2 5.1: gRPC `GetCodegenInput` | `generationRequestId`, `projectId`, `businessTypeCode`, `capturedInput`, `capturedInputHash`, `generationContract` (scaffold and limits arrive in J4) | 404 `GENERATION_REQUEST_NOT_FOUND`; 409 `REQUEST_NOT_GENERATING`; 503 `CATALOG_UNAVAILABLE` | Project | `service-communication.md` section 2.2; decision 0004; PRD note line 74; Notion S2 3.7 lists `project/v1/codegen_input.proto` but the file does not exist; Notion "4 · AI" says HTTP | agreed for REST (AI side unverified); gRPC missing | FR-29, PR-16, PR-22, TR-23 | yes (REST) |
| X15 | Scaffold | AI Codegen calls Project | Repo: REST `GET .../scaffold`, gzip tar, `Content-Digest`. Notion: gRPC `GetScaffold`, streamed | deterministic scaffold for the request | as X14 | Project | `service-communication.md` section 2.2 and "Built today"; Notion S2 5.1 | missing | PR-22 (no FR yet) | no (J4) |
| X16 | Read a run, and its runs | Project calls AI Codegen | Repo: REST `GET /internal/v1/generation-runs/{runId}` and `.../generation-requests/{id}/runs`. Notion: gRPC `GetGenerationRun`, `ListGenerationRuns` | run id, request id, project id, `inputHash`, status, attempt, `errorCode`, artifacts with `contentHash`, `byteSize`, presigned `downloadUrl` (10 min) | not defined | AI Codegen | `service-communication.md` section 2.4; Notion S2 5.1; no proto (`ai_codegen/v1/runs.proto` is in the Notion tree only) | proposed by Project, waiting for AI Codegen; transport disputed | PR-9, PR-16 (no FR yet) | no |
| X17 | Download the generated source | Project reads AI's artifact storage | HTTPS presigned URL, SHA-256 check | `source_archive` (gzip tar overlay), `check_report` | hash mismatch refuses the run | AI Codegen | `service-communication.md` sections 2.3, 2.4, 2.6; Notion S2 5.4 | proposed by Project, waiting for AI Codegen | PR-9 (no FR yet) | no |
| X18 | AI hands over tested code (push) | AI Codegen calls Project | Notion S2 5.1 row: gRPC, "a new method (name to define)" | not defined | not defined | undecided | Notion S2 5.1 and 9 point 6 only; contradicts X13, X16, X17 | missing; conflicts with the pull model | PR-9 | no |
| X19 | `CheckThreadOwner` | AI Codegen (design assistant) calls Project | gRPC (Notion) | not defined; thread owner check for a project and user | not defined | Project | Notion S2 2.4 table and 5.1; no proto, no `.claude/docs` file | missing | PR-22 (no FR yet) | no |
| X20 | `GetProjectOwner` | Notification calls Project | gRPC (Notion) | not defined; project id to owner user id | not defined | Project | Notion S2 5.1; BRD line 28; PRD PR-22; no proto | missing | PR-22 (no FR yet) | no |
| X21 | Notification consumes Project events | Project publishes; Notification consumes | RabbitMQ, queue `notification.source_published.v1` ("more when their events are defined") | the X7 event | as X7 | Project | Notion S2 4.4 and 5.2; PRD PR-19 | draft (same file as X7); other events undefined | PR-19 | no |
| X22 | Admit a website | Project calls Payment | Repo: REST `POST /internal/v1/website-admissions`. Notion S2 5.1: gRPC `AdmitWebsite` | `{clientUserId, projectId}`; `ADMITTED` or `REFUSED` with reason `WEBSITE_LIMIT_REACHED`, `TRIAL_ENDED`, `NO_ACTIVE_PLAN`, `CLIENT_SUSPENDED` | 200 with `REFUSED` is normal; 409 `IDEMPOTENCY_CONFLICT`; timeout 2 s fails closed `PAYMENT_UNAVAILABLE` | Payment | `.claude/docs/contracts/payment-contract.md` section 2.1, status "draft for the owner's review"; `HttpPaymentAdmissions.java`; no admission proto (Notion tree lists `payment/v1/admission.proto`) | proposed by Project, waiting for Payment | FR-10, FR-11, PR-15 | yes (HTTP; stand-in in `prototype`) |
| X23 | Release a website admission | Project calls Payment | Repo: REST `DELETE /internal/v1/website-admissions/{projectId}`. Notion: gRPC `ReleaseWebsiteAdmission` | project id | 204; 404 `NOT_ADMITTED`; 409 `PROJECT_IN_USE` | Payment | `payment-contract.md` section 2.2 | proposed by Project, waiting for Payment | FR-12, PR-15 | yes (HTTP) |
| X24 | Admit a generation attempt | Project calls Payment | Repo: REST `POST /internal/v1/generation-admissions`. Notion: gRPC `AdmitGenerationAttempt` | `{clientUserId, projectId, generationRequestId}`; attempts used and limit; refusal reasons incl. `TRIAL_GENERATION_LIMIT_REACHED`, `PROJECT_NOT_ADMITTED` | stored as `failure_code` in lower case; `admission_unavailable`, `admission_error` | Payment | `payment-contract.md` section 2.3; decision 0004 | proposed by Project, waiting for Payment | FR-26, PR-7, PR-15 | yes (HTTP) |
| X25 | Project entitlement (for the UI) | Project calls Payment (Provisioning also calls it) | Repo: REST `GET /internal/v1/projects/{id}/entitlement`. Notion: gRPC `GetEntitlement`. Proto exists | `hosting` ENTITLED or ENDED, `publish`, `reason`, `plan_code`, publishes used and limit, `period_end` | unknown project is OK with `HOSTING_ENDED`, `NOT_ADMITTED` | Payment | `payment-contract.md` section 2.4; `proto/webblock/payment/v1/entitlement.proto` on contracts main, header "DRAFT, proposed by provisioning ... waits for payment" | draft; nobody on Project's side uses it | no PR names it (BRD-level "Payment decides plan limits", BR-11) | no |
| X26 | Client plan summary (for the UI) | Project calls Payment | REST `GET /internal/v1/clients/{id}/entitlement` | plan, trial end, websites and attempts used and limits, period end | not defined | Payment | `payment-contract.md` section 2.5 only; absent from Notion S2 5.1 and from contracts | proposed by Project, waiting for Payment; not in Notion | none | no |
| X27 | Payment hosting events | Payment publishes; Provisioning and Notification consume. Project is not a party | RabbitMQ, `payment.events` | `hosting_entitlement_ended` (`event_key`, `reason`), `hosting_entitlement_restored` (`event_key`) | consumer rechecks X25 | Payment | `events/payment/*.v1.schema.json` DRAFT proposed by provisioning; `payment-contract.md` section 3 | draft; not Project's exchange | none for Project | not applicable |
| X28 | `provisioning.publish_completed.v1` | Provisioning publishes; Payment and Notification consume (Notion). Project would consume for FR-37 | RabbitMQ, `provisioning.events`, key `publish_completed.v1` | envelope plus `event_key` = `publish-completed-<release_id>`, `release_id` | n/a | Provisioning | `events/provisioning/publish_completed.v1.schema.json`, header OWNED BY provisioning; Notion "5 · Provisioning" `usage_outbox` | agreed between Provisioning and Payment; Project as consumer is missing | FR-37, PR-9 | no |
| X29 | Git hosting (Gitea) | Project calls Gitea | HTTP, Project's bot account | create the site repository in organisation `webblock-sites`; commit the accepted overlay, tagged `WebBlock-Request: <id>`; never force push | not defined | Project, with Gitea as an outside system | Notion S1 and S2 5.4, 9 point 6 ("Project commits"); decision 0006; `service-communication.md` says GitHub | missing (no contract file); doc names the wrong host | PR-8, PR-9 (no FR yet) | no (`repository_id` column exists, no writer) |
| X30 | Shared catalog database | Project writes; Provisioning and sites read and administer | PostgreSQL roles, not a service call | `catalog.websites`, `business_profiles`, reference data; Project migrates the schema | registration retried 16 times over about 44 minutes | Project | `provisioning-contract.md` section A; Notion "6 · Shared catalog"; decisions 0012 to 0015; Provisioning `deploy/kind/proto-catalog.sh` | agreed; Provisioning's Kind script uses it | FR-35, FR-36, TR-21, PR-17 | yes |
| X31 | Keycloak: token check | Project calls Keycloak's key set; Account runs sign-in | HTTPS to the issuer (JWKS); RS256, issuer, audience `project`, `typ` Bearer, `exp`, `nbf`, claim `user_id` | access token claims `iss`, `aud`, `typ`, `user_id`, `roles` (roles ignored by Project) | 401 `UNAUTHENTICATED`; 403 `ACCESS_DENIED` for no `user_id` | Account and Keycloak (realm), Project (check) | `.claude/docs/contracts/authentication-contract.md` ("approved by the owner"); Notion S2 2.4 and section 8; decision 0002 | agreed; Account's and DevOps' acceptance is not in the repo | FR-1, FR-2, TR-30, PR-23 | yes (`JwtDecoderConfig`) |
| X32 | Frontend and mobile REST | Next.js server and mobile app call Project | REST `/api/v1`, camelCase, `{success, data, meta}` envelope, RFC 9457 errors, Bearer token | business types, templates, projects, pages, component catalog, generation contracts and requests | Problem Details with `code` | Project | Notion S2 section 2; decision 0003; FRD; no OpenAPI file in the repo or contracts | agreed in prose; no machine contract | FR-8 to FR-25, TR-22 | yes |
| X33 | Admin pages call Project | Admin pages (inside the Next.js app) call Project | REST admin endpoints, role `ADMIN` | suspend, resume, search by sub-domain (PR-11) | not defined | Project | Notion S2 5.3 and the "Project" row of the audience table | missing | PR-11 (no FR yet) | no |
| X34 | Mesh policy for Project's callers | Owner writes `AuthorizationPolicy` in `web-block-config` | Istio | callers per path or method | n/a | Owner, DevOps | `grpc.md` (one rule: `provisioning-api` on `CheckAccess`); `provisioning-contract.md` B; Notion S2 3.8 | draft; three different caller lists (see conflicts) | TR-31, TR-38 | not applicable |

Count: 34 rows. Built in Project: X1, X2, X11, X14, X22, X23, X24, X30, X31, X32 (10). X27 and X34 are not code
exchanges of Project. The other 22 are not built.

### Findings on the known gaps

#### G1. The publish result for FR-37 (FRD Q-4, PR-9)

Is `events/provisioning/publish_completed.v1.schema.json` enough? For the status change, yes. Facts:

- Fields: envelope, `event_key` (`publish-completed-<release_id>`, 54 characters), `release_id`. No
  `generation_request_id`. Source: the schema file; Notion "5 · Provisioning" (`usage_outbox`).
- It is sent when a release is confirmed live (after build, deploy and promote), at least once, one per release, not
  one per project. So Project sees it for every release and must flip only `draft`.
- A failed first publish produces no event in any contract: Provisioning's release goes to `failed`, nothing is
  published ("A failed first publish leaves the offline page", Notion "5 · Provisioning"). FR-37.2 only requires that
  the status stays `draft`, so no failure signal is required. FRD line 353 says "Success or failure"; the failure half
  is "do nothing".
- Project is not a consumer in the written design. Notion S2 4.4 gives Project two queues only
  (`project.generation_completed.v1`, `project.user_status_changed.v1`); 5.2 gives `publish_completed` to Payment and
  Notification. Using it needs: a queue `project.publish_completed.v1`, a binding on `provisioning.events`, read
  permission for Project's broker user on that exchange (S2 4.4, permission table), and a change to S2 tables, which
  only the owner edits.
- Meaning is open. Notion S1 says `active` = "Finished its first publish ... Does not mean live". The Provisioning
  page says request `published` = "the commit exists", release `live` = visitors are routed. FR-37 text says "first
  successful publish" and the handoff says it "needs the publish result from Provisioning", which reads as "live".
  If the owner means "commit published" (request `published`), Project already knows it and no contract is needed.
- Payment's own contract for this message is still open on the Provisioning page ("Open": "Payment's final contract
  for publish_completed"). Project must not rely on a field Payment may still change; the file is OWNED BY
  provisioning.

Options and a recommendation are in the next section.

#### G2. `GetRepository` and `repository.proto` (PR-18)

Does `repository.proto` fit? Yes for what Provisioning builds. Provisioning's `GrpcRepositories` (both ports,
`RepositoryRegistry` and `RepositoryLocator`) uses exactly: `repository_id` (range 1 to 2^53-1), `subdomain` (1 to 63
DNS label), `clone_url` (must be non-empty), and `NOT_FOUND` with reason `REPOSITORY_NOT_FOUND` for "no repository".
Any other failure is "unknown, retry". `full_name` is not read. Details that need a decision:

1. **`clone_url` has no source in Project.** Project stores `repository_id` and `repository_full_name`
   (`webblock-sites/<repo>`, S1) and no URL. The URL must be built from a configured Gitea base plus the full name.
   Which host: the in-cluster Gitea address, or the Cloudflare Access address Jenkins uses (S2 5.4 says Jenkins clones
   "through Cloudflare Access")? Question for the owner. Provisioning logs never print it.
2. **No commit confirmation.** Notion "5 · Provisioning" says the intake "asks Project to confirm the repository and
   commit". The proto carries only the repository. Provisioning checks `repository_id` equals the event's and never
   checks the SHA against Project. Either the Notion wording is loose, or a field `git_commit_sha` per request is
   missing. Question for Provisioning and the owner.
3. **Error reason.** `REPOSITORY_NOT_FOUND` is not in Project's `ErrorCode` list. S2 3.4 says the gRPC `reason` is the
   same code as REST. Unknown project, deleted project and "no repository yet" should all give this one reason (a
   caller of this method has no user, so the "not the owner is 404" rule does not apply). Needs the owner's rule for
   deleted projects.
4. **Mesh caller.** Notion 3.8 names `provisioning-build-worker` for this method. `grpc.md` admits only
   `provisioning-api` and only for `CheckAccess`. `service-communication.md` names `provisioning-api` and
   `provisioning-worker` for the old REST path. Provisioning's code confirms the build worker (its
   `build_worker/wiring.py` consumes `source_published`).
5. **Production gate on the other side.** Provisioning keeps `GetRepository` refused in production "while the proto is
   a draft" (decision 0022 E7). Project accepting the file (header to ACCEPTED, then both sides move the pinned SHA)
   unblocks it.
6. **Timing.** The lookup is meaningful only after Project has written `repository_id` and `repository_full_name` (both
   or neither, S1) and before it publishes `source_published`. There is no writer yet (PR-8).

#### G3. `source_published`, `project_deleted`, `owner_status_changed` against Provisioning's consumer

- Schemas: identical to contracts main (`diff` clean for the three, plus `publish_completed`, `access.proto` and
  `repository.proto`). All three Project events carry the header "DRAFT, proposed by provisioning, waits for project".
  The README says the owner may change or accept; on acceptance the header becomes ACCEPTED.
- `source_published` as Provisioning reads it (`presentation/messaging/v1.py`, `decode_source_published`): validates
  the whole body against the schema (`additionalProperties` false), then keeps four fields: `project_id`,
  `generation_request_id`, `repository_id`, `git_commit_sha`. An optional `subdomain` is validated and thrown away; the
  sub-domain used for routing comes from `GetRepository` (decision 0021 D8). The AMQP `message_id` must equal the body's
  `message_id`, and the `type` property must equal `message_type`, or the event is rejected (`ENVELOPE_MISMATCH`).
- Inbox key is `(project_id, generation_request_id)`. A second event with the same key and a different repository or
  SHA is counted and refused, never accepted (Notion "5 · Provisioning"). So Project must publish one source per
  request, set once (S1: `source_commit_sha` is set once), and a re-publish after a crash must carry the same
  repository and SHA.
- Notion S1 says Provisioning "gets the sub-domain from `project.source_published.v1` and its repository lookup". The
  code uses only the lookup. Project should still send `subdomain` (optional in the schema, harmless).
- Notion S2 5.2 sends `source_published` also to Payment and Notification. `payment-contract.md` section 4 lists only
  `publish_completed` and `project_deleted` for Payment, and `service-communication.md` section 3 lists only
  Provisioning. No document says what Payment does with it.
- `project_deleted`: Provisioning's `ProjectDeletionConsumer` reads `project_id` and sends a `suspend_hosting` command
  with the fixed key `project-deleted` (so repeats are harmless). A project with no hosting row is acknowledged and
  logged (Notion "5 · Provisioning"). Payment dedupes on `project_id` (`payment-contract.md` section 4, A2). Project
  must emit it in the transaction that sets `deleted_at` (PR-20). There is no delete endpoint and no FR yet.
- `owner_status_changed`: requires `subdomain` (unlike `source_published`) and `status_version` of at least 1.
  Provisioning
  stores the highest version per project and drops an older or equal one; the command key is `owner-status:<version>`.
  The version is Account's counter ("The sender adds 1 ... on every status", Notion "Database Design", seen only as a
  search highlight, page not fetched). So Project can only relay the version from the Account event X10, which has
  no schema yet. Fan-out is one event per project of the owner, deleted ones included (Notion "5 · Provisioning").
- Project's message plumbing already matches the envelope: `IntegrationEvents` derives the routing key
  `<event>.v<N>` from `project.<event>.v<N>`, sets AMQP `type` and `message_id`, persistent. Not checked: that Project's
  Jackson output of `occurred_at` and `message_id` satisfies the schema patterns (the only built event,
  `generation_requested`, has no schema to test against).

#### G4. Payment's suspend signal

Confirmed: none exists and none is needed for Project.

- Payment publishes only `hosting_entitlement_ended` and `_restored`, to Provisioning and Notification (S2 5.2,
  `payment-contract.md` section 3). Project is not a receiver. Project receives no entitlement event and no
  `owner_status_changed` (Project sends that).
- Notion S1: `projects.status = suspended` is "reserved ... No writer yet; no hosting effect" and "is not used for
  unpaid plans (hosting reason `billing`) or blocked users (`user_suspended`)".
- FRD line 372 says `suspended` is "set only by Payment or an admin (not built yet)". The "by Payment" part has no
  signal behind it and disagrees with Notion. FR-37 text repeats "Only Payment or an admin sets `suspended`".
- What Payment does tell Project today: the admission refusal reason `CLIENT_SUSPENDED` (X22, X24), when Account blocks
  a user. That is a synchronous answer, not a signal.

#### G5. Notification

- Consumer side (S2 4.4): `notification.source_published.v1` (more "when their events are defined"). It is the same file
  as X7 and needs nothing extra from Project besides publishing it.
- Call side (S2 5.1): Notification calls Project `GetProjectOwner` over gRPC. No proto, no field list, no error list, no
  FR (PR-22 says "project owner"), no `.claude/docs` page. The mesh would need an `AuthorizationPolicy` rule for
  Notification's service account.
- Nothing else is written. Unknown: whether Notification also needs "generation failed" or "project deleted" events
  (S2 5.2 gives it neither). Question for the owner.
- Project never calls Notification (S2 5.5: "Notification deciding anything" is forbidden; it only reports).

#### G6. AI Codegen (largest gap)

No file for any AI exchange exists in `web-block-contracts` (no event schema for X11, X12, X13; no proto for X14 to X16,
X19). The only written form is `service-communication.md` section 2, written by the Project side for a team whose side
was not seen (no AI repo was found under `/home/roa/Documents/web-block`). Three transport disagreements are in the
conflicts list.

#### G7. Payment

`payment-contract.md` is "draft for the owner's review", written by the Project side "for the Payment team, which does
not exist yet". Open point 5 in it (mesh identity, no bearer) awaits Payment. Notion S2 4.4 says Payment is built by the
Webill team, who "follow sections 2 to 4". No admission proto exists; `entitlement.proto` was written by Provisioning.

#### G8. Contracts repository process

- `access-actions-v1` (`ea4bc7f`) is unpushed (no remote in this clone). Task 8 waits for it.
- Accepting the three Project events and `repository.proto` means Project (owner of the files) edits their headers on
  `main`; then Project and Provisioning each move `contracts.lock` and re-sync in one reviewed change (README, "How a
  service pins it"). In this scope Project writes only docs, so the header change is a hand-over item.

### Notion against the repo

| Id | Subject | Notion says | The repo says |
| --- | --- | --- | --- |
| C1 | Project to Payment | S2 5.1 gRPC: `AdmitWebsite`, `ReleaseWebsiteAdmission`, `AdmitGenerationAttempt`, `GetEntitlement` | HTTP `/internal/v1/...` in `payment-contract.md` and `HttpPaymentAdmissions.java`; only `GetEntitlement` has a proto, written by Provisioning |
| C2 | AI reads Project's input | S2 5.1 gRPC `GetCodegenInput`, `GetScaffold`; S2 3.7 lists `codegen_input.proto`. "4 · AI" (21 Oct) says "over Project HTTP" | REST `codegen-input` built; PRD says REST stays until AI Codegen's team is ready; no proto file |
| C3 | Project reads AI runs | S2 5.1 gRPC `GetGenerationRun`, `ListGenerationRuns` | REST `GET /internal/v1/generation-runs/{runId}` in section 2.4 of the note |
| C4 | How accepted code reaches Project | S2 5.1 has two models: "AI to Project: a new method to hand over the tested code" (push) and "Project to AI: read the result after a completion event" plus artifact storage in 5.4 (pull) | Pull only (note section 2); S2 9 point 6 says "AI tests and builds, then calls Project, which commits" |
| C5 | Git host | Gitea, repository IDs from Gitea, organisation `webblock-sites` | `service-communication.md` says GitHub, `githubRepositoryId`, "GitHub credentials" (BRD line 91 lists this) |
| C6 | Repository lookup | gRPC `GetRepository` (S2 5.1, 3.8; PR-18 "no REST version") | `service-communication.md` section 3 still lists REST `GET /internal/v1/projects/{id}/repository` with `NO_REPOSITORY` |
| C7 | Receivers of `source_published` | Provisioning, Payment, Notification (S2 5.2) | `service-communication.md` section 3: Provisioning; `payment-contract.md` section 4: Payment consumes other events only |
| C8 | Project's consumers | S2 4.4: only `generation_completed` and `user_status_changed` | FR-37 needs `publish_completed`; no document adds it |
| C9 | Site portal check | S2 9 point 3: a public REST endpoint for sites on a separate EC2 cluster (proposal) | `service-communication.md`: `CheckAccess` over the mesh from `wb-*` namespaces; `grpc.md` admits only `provisioning-api` |
| C10 | Who may call the lookup and the check | S2 3.8: `provisioning-api` on `CheckAccess`, `provisioning-build-worker` on `GetRepository` | `grpc.md`: `provisioning-api` only; `service-communication.md`: `provisioning-api` and `provisioning-worker` for REST |
| C11 | Project `suspended` | S1: no writer, "not used for unpaid plans" | FRD line 372 and FR-37: "set only by Payment or an admin" |
| C12 | Contract tree | S2 3.7: `project/v1/codegen_input.proto`, `payment/v1/admission.proto`, `account/v1/users.proto`, `ai_codegen/v1/runs.proto`, `events/project/source_published.v1.schema.json` | contracts main has `access.proto`, `repository.proto`, `entitlement.proto` and ten event schemas; none of the four named protos |

Also seen, small: S2 3.4 shows a gRPC `NOT_FOUND` example with reason `PROJECT_NOT_FOUND`, while `repository.proto` uses
`REPOSITORY_NOT_FOUND`. S2 section 8 (`web-block-commons`) is "design in review", and Project has its own
`JwtDecoderConfig`; if the library is adopted, X31 changes inside Project only.

## Options and recommendation

The brief asked for options only on the gaps; short ones follow. Each is a suggestion for the owner or the lead.

FR-37 publish result:

- A. Consume `provisioning.publish_completed.v1` (flip `draft` to `active` where status is `draft`; inbox on
  `message_id` and `event_key`). Needs the Notion 4.4 and 5.2 rows, a queue and a broker permission. No change on the
  Provisioning side. Matches "first successful publish" if it means live.
- B. Define the first publish as the request reaching `published` (the commit exists). No new contract; Project writes
  `active` in the same transaction as the event. It changes the meaning of "publish" for FR-37 and for "Does not mean
  live" in S1 stays true.
- C. Project asks Provisioning for release status (REST or gRPC). Not in Notion 5.1 or 5.5, adds a call and a
  Provisioning method. Not recommended.
- Recommendation: A, after the owner rules what "publish" means. If the owner says "commit", take B.

`GetRepository`: accept `repository.proto` as written, and ask for three additions or answers before ACCEPTED: the
source of `clone_url` (host), whether commit confirmation is wanted (a `git_commit_sha` per generation request would be
a new field or a new method), and one rule for deleted projects. Add `REPOSITORY_NOT_FOUND` to the contract page for
Project's error codes.

Events: accept the three Project schemas as they are (they match Provisioning's consumer). Add schemas for
`generation_requested` (the only one Project already emits), `generation_cancelled`, `generation_completed` and
`account.user_status_changed` as DRAFT, proposed by Project, so the other teams can answer.

Transport (C1 to C4): the lead needs one owner ruling per pair, since Notion (newer, 28 Oct) says gRPC but no proto
exists and the code is HTTP. Suggested default: keep HTTP for Payment and AI until their teams ship protos, write the
contract page for both forms, and mark the gRPC form "target".

Notification: write one page now with `GetProjectOwner` proposed as `{project_id}` to `{owner_user_id, deleted}`, status
"proposed by Project, waits for Notification", and ask which Project events Notification needs besides
`source_published`.

## Not checked, and how sure I am

| Answer | Confidence | Why |
| --- | --- | --- |
| Table rows for Provisioning (X1, X2, X5 to X9, X28) | high | read both sides, schemas and protos diffed byte for byte |
| Table rows for Payment (X22 to X26) | medium | Project side read in code and contract; Payment's side does not exist locally; Notion "3 · Payment" (edited 2026-10-21) not fetched |
| Table rows for AI Codegen (X11 to X18) | medium | Project side and Notion "4 · AI" read; no AI repo found; whether AI implements the REST endpoints is unknown |
| Table rows for Account (X10, X31) | medium | Notion S2 only; no Account repo; Notion "Database Design" page (status_version rules) seen only as a search highlight |
| Notification (X20, X21) | medium | only Notion S2 rows exist |
| FR-37 conclusion (G1) | high for "schema is enough for success", medium for the consumer plumbing | permissions and queue names come from S2 4.4 text, not from a broker |
| Project's event JSON matches the schemas (patterns, `occurred_at` format) | low | not run; only `generation_requested` exists and has no schema |
| `web-block-commons` effect | low | S2 section 8 and `../web-block/2026-10-27-web-block-commons-design.md` not read |
| Mesh policies in `web-block-config` | not checked | repo not in scope; real cluster never touched |
| Notion pages edited after the times above | not checked | pages read once; times are as the tool reported |

Questions for the owner (nothing here could be decided from files):

1. What does "first successful publish" mean for FR-37: commit published, or release live?
2. Which host goes into `clone_url` (in-cluster Gitea, or the Cloudflare Access address)?
3. Does Provisioning need Project to confirm the commit SHA, or only the repository?
4. Gitea or GitHub for `service-communication.md` (Notion and the DB say Gitea)?
5. Push or pull for the generated code (C4)?
6. Does Notification need more events than `source_published`?
7. Is `suspended` to be set by Payment (FRD) or by nobody (Notion)?
8. Public REST endpoint for the site portal check, or the mesh (C9)?
9. Push `access-actions-v1` and give the SHA (task 7), so X3 can move.
