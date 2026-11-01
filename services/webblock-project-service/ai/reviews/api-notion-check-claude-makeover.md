# Research: Project API on main against Notion "Web Block architecture"

Stage: makeover step "Check the API against Notion" (`ai/handoffs/next.md`). IDs: FR-1 to FR-37 as they apply, PR-12 to
PR-22, TR-5.

Questions: (1) every difference in REST paths, gRPC methods, events, envelope, error codes, paging; (2) the folder
layout
against Notion 6.1; (3) which of the 12 conflicts C1 to C12 of `ai/reviews/contracts-inventory.md` still stand.

Code read: this worktree at `7a9ebb7`. `git diff ed5fcaf HEAD -- src contracts` is empty, so `src/` and `contracts/` are
those of main `ed5fcaf`. No cluster, no branch switch. The sibling `../web-block-contracts` was listed read-only (its
checked-out branch is `contracts-drafts-v1`).

Notion sources (fetched fresh, read only):

- "Web Block architecture" (S2), last edited 2026-10-28T10:05:03Z.
- "2 · Project" (S1), last edited 2026-10-22T10:17:01Z.

Both pages have the same edit time as in the earlier inventory, so C1 to C12 are re-checked against unchanged Notion
text.

## Short answers

1. REST: no path or verb in the code contradicts Notion. S2 names only `GET /api/v1/projects` and
   `GET /api/v1/projects/{id}`, and both exist. Envelope, paging (`limit`, `cursor`, `nextCursor`), Problem Details
   fields, the `urn:webblock:problem:` type and the 404-not-403 rule match. The differences are in error code names,
   a few statuses, and a null `traceId`.
2. gRPC: `AccessService/CheckAccess` is the only service built and equals S2 3.2 field for field. Every other method in
   S2 5.1 that Project must serve (`GetRepository`, `GetCodegenInput`, `GetScaffold`, `CheckThreadOwner`,
   `GetProjectOwner`) and every client call (Payment, AI) is not built. Project still calls Payment over HTTP.
3. Events: Project publishes one event of five (`generation_requested.v1`, fields match the pinned schema) and consumes
   none of its two (three with FR-37). Exchange `project.events` and routing key shape match S2 4.2.
4. Layout: Project follows 6.1 "feature first, four layers", which S2 6.1 itself allows for Project. Name differences
   (`controller` for `rest`, `service` for `handler`, `entity`, no `mapper`) are recorded in decision `0010` and are
   enforced by `ArchitectureRulesTest`; changing them needs the owner (Art. III).
5. C1 to C12: 11 still stand; C11 is resolved. Four doc rows can be fixed now (items F1 to F4 below).

Counts per kind (25 differences):

| Kind | Count | Items |
| --- | --- | --- |
| Fix now (name, path or doc, no logic) | 4 | F1, F2, F3, F4 |
| Behaviour (next owner) | 10 | B1 to B10 |
| Notion gap (owner) | 10 | N1 to N10 |
| Structure, recorded, owner needed (not fix now) | 1 | S1 (five sub-points) |

## Findings

### Fix now (docs only; the code and Notion are not touched)

| Id | Notion says | The repo says | Where |
| --- | --- | --- | --- |
| F1 (C5) | S2 5.4: Project to "Gitea", HTTP, "Create the site's repository". S2 9 point 6: "Project commits". S1: organisation `webblock-sites`, repository IDs from Gitea | Docs say GitHub: "alone commits to GitHub", "GitHub credentials"; `next.md` says "GitHub App and local Git" | `.claude/docs/contracts/service-communication.md:93-94`, `:246`; `ai/handoffs/next.md:6`; `.claude/docs/requirements/BRD.md:91` already notes it |
| F2 (C6) | S2 3.1 and 5.1: Provisioning calls gRPC `GetRepository`; PR-18: "no REST version" | Row still lists REST `GET /internal/v1/projects/{id}/repository`, JSON `githubRepositoryId`, error `NO_REPOSITORY` | `service-communication.md:260` (the pinned `repository.proto` uses `REPOSITORY_NOT_FOUND`; PRD PR-18 is right) |
| F3 (C7) | S2 5.2: `project.source_published.v1` goes to "Provisioning, Payment, Notification" | Row names Provisioning only | `service-communication.md:257` (PRD PR-19 is right) |
| F4 (C10) | S2 3.8: `provisioning-api` on `CheckAccess`, `provisioning-build-worker` on `GetRepository` | Row admits `provisioning-api` and `provisioning-worker` | `service-communication.md:260`; `contracts/README.md:93-94` already records the gap with `runbooks/grpc.md:35` |

`payment-contract.md:53-54` also names `provisioning-worker` but that is Payment's own policy, so it is left alone.

### Behaviour (code differs from Notion; next owner, J3 to J5)

| Id | Notion says | The code does | Where |
| --- | --- | --- | --- |
| B1 | S2 2.1: `meta.traceId` "Only there when tracing picked this request" | `ApiMeta.traceId` is a plain record component, no `@JsonInclude`, so it is written as `null` when untraced (inferred from code; no test run). The error body does omit it (S2 2.2 is met) | `common/presentation/envelope/ApiMeta.java:10`; `common/presentation/error/ProblemDetailFactory.java` (traceId only if present) |
| B2 | S2 2.4: `roles` `ADMIN` "unlocks admin actions"; S2 5.3 "admin endpoints" | No admin endpoint and no roles read. PR-11 (admin search by sub-domain) is J5 | `common/presentation/security/CurrentUser.java` reads `user_id` only |
| B3 (part of C1) | S2 5.1: Project to Payment gRPC `AdmitWebsite`, `ReleaseWebsiteAdmission`, `AdmitGenerationAttempt`, `GetEntitlement` | HTTP: `POST /internal/v1/website-admissions`, `POST /internal/v1/generation-admissions`, `DELETE /internal/v1/website-admissions/{projectId}` | `payment/infrastructure/client/HttpPaymentAdmissions.java:112,154,200`; no payment proto pinned |
| B4 (C2) | S2 5.1: AI to Project gRPC `GetCodegenInput`, `GetScaffold` (streamed) | REST `GET /internal/v1/generation-requests/{id}/codegen-input`; no proto. PRD PR-22: REST stays until AI Codegen's team is ready | `generation/presentation/controller/CodegenInputController.java:13,22` |
| B5 (C3) | S2 5.1: Project to AI gRPC `GetGenerationRun`, `ListGenerationRuns` | Not built; docs plan REST `GET /internal/v1/generation-runs/{runId}` | `service-communication.md:114,203` |
| B6 | S2 5.1: Provisioning to Project `GetRepository`; Notification to Project `GetProjectOwner`; AI to Project `CheckThreadOwner` | None served. One gRPC service only. `repository.proto` and `project_owner.proto` exist in `../web-block-contracts` but are not in `contracts.lock` | `contracts/proto/webblock/project/v1/` has `access.proto` only; `access/presentation/grpc/AccessGrpcService.java` |
| B7 | S2 4.4: Project publishes `generation_requested`, `generation_cancelled`, `source_published`, `project_deleted`, `owner_status_changed` | Only `generation_requested.v1`. No cancel, source-published, delete (no `DELETE /api/v1/projects/{id}` either, PR-10 and PR-20) or owner-status event | `generation/application/model/GenerationRequested.java:26`; the only `IntegrationEvent` implementation |
| B8 | S2 4.4: Project listens on `project.generation_completed.v1` and `project.user_status_changed.v1`; S2 4.3 inbox and 5 retries then `.dlq` | No consumer, no queue, no DLQ declared, no `@RabbitListener`. Only the outbox side exists | `messaging/infrastructure/messaging/MessagingConfig.java:40-52` declares the exchange and the unrouted queue only |
| B9 | S2 4.1: "Message headers also carry `traceparent` and `x-request-id`" | The AMQP message sets content type, persistent, `type` and `message_id` only | `messaging/infrastructure/messaging/IntegrationEvents.java:60-68` |
| B10 | S2 2.4: website portals call `CheckAccess` with action `manage_site` | `manage_site` is known Drift R-1 (FR-32.1 and FR-32.2) | `ai/handoffs/next.md` "Merged and ruled", R-1 |

### Notion gaps (Notion is silent, inconsistent or older than the contract; owner decides)

| Id | Notion says | The code does | Where |
| --- | --- | --- | --- |
| N1 | S2 2.2 example: duplicate sub-domain is 400 `VALIDATION_FAILED` with `errors[].code` `TAKEN`; S2 2.3 lists `HOSTNAME_TAKEN` as the 409 example. Field codes shown: `TOO_LONG`, `REQUIRED` | 409 `HOSTNAME_TAKEN`; field codes are `FIELD_REQUIRED`, `FIELD_LENGTH_INVALID`, `FIELD_OUT_OF_RANGE`, `VALUE_INVALID`, `UUID_INVALID` and others. Renaming would break wire codes pinned by the FRD ("Stable, never renamed") | `common/domain/error/ErrorCode.java:39,69-79` |
| N2 | S2 2.3: business rule is 422 `FAILED_PRECONDITION` | `PROJECT_DELETED` and `PROJECT_SUSPENDED` are 409 (FRD says 409) but gRPC `FAILED_PRECONDITION`; `GENERATION_IN_PROGRESS`, `IDEMPOTENCY_KEY_REUSED`, `REQUEST_NOT_GENERATING` are 409/`ABORTED` though Notion reserves `ABORTED` for "changed by someone else". Today REST never returns the two project codes | `ErrorCode.java:42-43,52-55`; `common/presentation/grpc/GrpcErrors.java:53-67`; FRD lines 292 and 397 |
| N3 | S2 2.3 lists 400, 401, 403, 404, 409, 422, 429, 500, 503, 504 only | Code adds 405, 406, 413, 415 and codes `CONCURRENT_UPDATE`, `ROUTE_NOT_FOUND`, `MALFORMED_REQUEST`, `PAYLOAD_TOO_LARGE`; gRPC maps 413 to `RESOURCE_EXHAUSTED` (Notion reserves it for 429) and 405/406/415 to `INTERNAL`. Notion's `RATE_LIMITED` (429) and `DEPENDENCY_TIMEOUT` (504) are absent from the enum | `ErrorCode.java:11-24`; `ErrorCategory.java`; `GrpcErrors.java:68-73` |
| N4 | S2 3.4: `BadRequest` example `{ "field": "project_id", "description": "REQUIRED" }` (code in `description`) | `reason` carries the code and `description` carries the sentence | `GrpcErrors.java:114-117` |
| N5 | S2 gives no REST path list for Project beyond the two examples | Code serves 19 routes (table below) | the controllers named below |
| N6 (C9) | S2 9 point 3: proposal of "a small public REST endpoint for sites" on a separate cluster | `manage_site` over the mesh on `CheckAccess`; `runbooks/grpc.md` admits `provisioning-api` only | `service-communication.md` site portal row |
| N7 (C4) | S2 5.1 has both "AI calls a new method to hand over the tested code (name to define)" and "Project reads the result after a completion event"; S2 9 point 6 says AI calls Project | Pull only in the docs | `service-communication.md:114` |
| N8 | S2 4.2: one topic exchange per service, queues owned by consumers | Project also declares alternate exchange `project.events.unrouted` (fanout) and a quorum queue of the same name for unroutable events. Not in Notion | `MessagingConfig.java:29-30,40-52` |
| N9 (C8) | S2 4.4 and 5.2: Project listens to two events; no `publish_completed` | FR-37 needs `provisioning.publish_completed.v1` (active on first live release). Pinned schema `events/provisioning/publish_completed.v1.schema.json` exists in the contracts repo | FRD line 350 and 421 |
| N10 (C12) | S2 3.7 tree: `project/v1/access.proto`, `codegen_input.proto`, `payment/v1/admission.proto`, `account/v1/users.proto`, `ai_codegen/v1/runs.proto` | Pinned `contracts/` has `access.proto` only. The contracts repo has `repository.proto`, `entitlement.proto`, `project_owner.proto` and ten event schemas, and none of the four other names | `contracts/`, `contracts.lock`; `../web-block-contracts` |

### Structure against Notion 6.1 (recorded; changing it needs the owner)

S1 (one item, five points). S2 6.1 says: "Project already uses these four layers, grouped by feature first ... It keeps
that layout; new services use the one above." So grouping by feature is not a difference. The inner names are:

| Point | Notion 6.1 | Code |
| --- | --- | --- |
| a | `presentation/rest/<feature>/ProjectController` | `<feature>/presentation/controller/ProjectController` |
| b | `application/<feature>/handler` ("CreateProjectHandler") | `<feature>/application/service` (`ProjectService`). The word "Handler" in code means outbox job handlers in `infrastructure/messaging` (`GenerationAdmissionHandler`, `AdmissionReleaseHandler`, `CatalogRegistrationHandler`), a different meaning |
| c | `application/<feature>/mapper` | no folder; static factories (`ProjectView.of`, `toCommand`) and `access/presentation/grpc/AccessGrpcMapper`. TR-5 and decision `0010` line 14 say so |
| d | `domain/<feature>/model` holds the entity; also `constant` and `event` | `domain/entity` and `domain/model`; the event record sits in `generation/application/model/GenerationRequested.java`; no `constant` folder |
| e | `presentation/messaging/...Listener`; `infrastructure/security`; `infrastructure/client/PaymentGrpcClient` | no listener yet; security is in `common/infrastructure/security`; the client is `payment/infrastructure/client/HttpPaymentAdmissions.java` |

Enforced by `TYPE_FOLDERS` in
`src/test/java/org/kshrd/webblock/project/architecture/ArchitectureRulesTest.java:209-213`.
The gRPC names match 6.1 exactly (`AccessGrpcService`, `AccessGrpcMapper`). `ErrorCode` is in `common/domain/error`, as
6.1 shows for `common/domain`. Adding a note to decision `0010` that lists points a to e as "Project's recorded
variants of 6.1" would be a docs-only step, but the owner must confirm it.

### REST routes in the code (for N5)

| Method and path | File:line | In Notion S2 |
| --- | --- | --- |
| `GET /api/v1/projects` | `project/presentation/controller/ProjectController.java:35` | yes (2.1 list example) |
| `GET /api/v1/projects/{projectId}` | `ProjectController.java:43` | yes (section 1 example) |
| `PATCH /api/v1/projects/{projectId}` | `ProjectController.java:48` | no |
| `PUT /api/v1/projects/{projectId}` | `wizard/presentation/controller/ProjectCreationController.java:34` | no |
| `GET, POST /api/v1/projects/{projectId}/pages`, `PUT, DELETE .../pages/{pageId}` | `page/presentation/controller/PageController.java:41,48,55,63` | no |
| `GET, POST /api/v1/projects/{projectId}/generation-contracts` | `generation/presentation/controller/GenerationContractController.java:38,45` | no |
| `GET, POST /api/v1/projects/{projectId}/generation-requests`, `GET .../{generationRequestId}` | `GenerationRequestController.java:45,61,70` | no |
| `GET /api/v1/component-catalog/{version}` | `page/presentation/controller/ComponentCatalogController.java:21` | no |
| `GET /api/v1/templates` | `template/presentation/controller/TemplateController.java:22` | no |
| `GET /api/v1/business-types` | `catalog/presentation/controller/BusinessTypeController.java:19` | no |
| `POST /internal/v1/access-checks` | `access/presentation/controller/AccessCheckController.java:14,23` | open point 5 (REST goes, gRPC first) |
| `GET /internal/v1/generation-requests/{id}/codegen-input` | `CodegenInputController.java:13,22` | open point 5 (to move to gRPC) |

### What matches Notion (no difference)

- Success envelope `success`, `data`, `meta` with `requestId`, `timestamp`, `path`, `status`, `page`
  (`ApiResponse.java`,
  `ApiMeta.java`); `meta.page` is `{limit, nextCursor}` and null otherwise; lists take `limit` and `cursor`
  (`ProjectController.java:37-38`).
- Problem Details: `type` is `urn:webblock:problem:` plus the lower-case code with dashes, `title`, `status`,
  `instance`, `code`, `requestId`, `traceId` (only if traced), `errors` (`ProblemDetailFactory.java`); no echo of caller
  input; `application/problem+json`.
- 404 for a non-owner (`PROJECT_NOT_FOUND`), access check answers `allowed: false` with a reason, never NOT_FOUND
  (`AccessGrpcService.java` class comment).
- gRPC: package `webblock.project.v1`, `AccessService`, `CheckAccess`, port 9090 (`application.yml:92`), `ErrorInfo`
  with `reason` = REST code, domain `project.webblock`, `request_id` in metadata, `x-request-id` read
  (`GrpcErrors.java:25,100-113`). `contracts/proto/webblock/project/v1/access.proto` equals S2 3.2 except two comment
  words.
- Events: exchange `project.events`, topic, routing key `<event>.v<N>`, snake_case body, envelope fields and
  `generation_requested.v1` equal the pinned schema; outbox in the same transaction (`MessagingConfig.java:29`,
  `IntegrationEvents.java:50-58`).
- Token audience `project` (`application.yml:65`).

## Re-check of C1 to C12 against today's Notion

| Id | State | Evidence |
| --- | --- | --- |
| C1 Payment over gRPC | stands (B3) | S2 5.1 unchanged; `HttpPaymentAdmissions.java:112,154,200` |
| C2 `GetCodegenInput` over gRPC | stands (B4) | `CodegenInputController.java:22`; no proto |
| C3 `GetGenerationRun` over gRPC | stands (B5) | docs say REST (`service-communication.md:114,203`); nothing built |
| C4 push or pull of the code | stands (N7) | S2 5.1 still shows both models |
| C5 GitHub or Gitea | stands in docs (F1) | `service-communication.md:93-94,246`; code and database say `repository_id` |
| C6 repository lookup | stands in docs (F2) | `service-communication.md:260` |
| C7 receivers of `source_published` | stands in docs (F3) | `service-communication.md:257` |
| C8 `publish_completed` consumer | stands (N9) | S2 4.4 unchanged; FRD 421 |
| C9 site portal check | stands (N6) | S2 9 point 3 still a proposal |
| C10 who may call | stands (F4) | S2 3.8 against `runbooks/grpc.md:35` |
| C11 project `suspended` writer | resolved | No FRD line says "set only by Payment or an admin" now; `next.md` R-6: FR-37 matches Notion, nothing sets `suspended`; S1 says no writer (medium sure, grep only) |
| C12 contract tree | stands (N10) | `contracts/` holds one proto |

Also seen in the inventory ("small"): S2 3.4 example uses `PROJECT_NOT_FOUND` as a gRPC reason while `repository.proto`
uses `REPOSITORY_NOT_FOUND`. Not a code difference; folded into N10 as a Notion example.

## Options and recommendation

Not asked for a ruling. For the lead: do F1 to F4 now (one docs commit citing PR-18, PR-19, PR-22, TR-5), and give the
owner N1 to N10 and S1 as one list. B1 is the only behaviour item that is a one-line change (annotate the envelope), but
it is service logic, so it stays with the next owner under the scope rule.

## Not checked, and how sure I am

- Whether the `traceId: null` of B1 is really written: from code reading only, no test or run. Confidence: medium.
- gRPC deadlines and retries (S2 3.6) and Istio rules (S2 3.8): Project has no gRPC client, and the mesh policy files
  are
  not in this repo. Confidence on "nothing to compare": high.
- AMQP broker users, permissions and queue names (S2 4.2 and 4.4): owned by the `rabbitmq` chart, not read.
- Notion "5 · Provisioning" and "4 · AI" were not fetched again (the inventory read them on 2026-10-28 and 2026-10-21).
- The route count (19) counts each verb once; the routes table groups them.
- Confidence: REST/envelope/error matches high; gRPC and events matches high; layout high; C11 medium; kind labels are
  my
  judgement of the stated scope (structure, names, docs only).
