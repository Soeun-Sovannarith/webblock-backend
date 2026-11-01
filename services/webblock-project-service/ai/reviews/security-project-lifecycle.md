# Security review: project lifecycle (delete, suspend, reactivate, catalog archive)

Stage 5. Branch `project-lifecycle`, commit range `29b6fde..0d1a0fc` (one commit, `0d1a0fc`), reviewed read-only in
`.claude/worktrees/project-lifecycle`. Reviewer model: Claude Opus 5.5 (`claude-opus-5-5`). In scope: FR-38, FR-39,
FR-40 (PR-10, PR-20), and the changed rules of FR-15, FR-16, FR-18, FR-30, FR-31, FR-32 (access check on a deleted
project). Articles at stake: Art. III (auth model, not-the-owner 404, decision `0007`), Art. VI.

## Method

Skills: `differential-review` (diff triage, blast radius of `ProjectService.find/getOwned/lockOwned/ownership`,
adversarial scenarios), `sharp-edges` (unfiltered `find` as the easy path; retry policy; conditional updates).
`project-gates` for running tests. No dependency change, so `supply-chain-risk-auditor` and the OSV scan do not apply.

Read in full: every `src/main` file of the diff; `ProjectCreationService`, `GenerationAdmissionService`,
`CodegenInputService`, `GenerationRequestService`, `GenerationContractService`, `PageService`,
`CatalogRegistrationHandler`, `IntegrationEvents`, `MessagingConfig`, `SecurityConfig`, `CatalogDataSourceConfig`,
catalog grants `V3`/`V4`, decision `0007`, runbook `catalog-database.md`, the FRD and contract doc diffs.

Commands (all in the worktree):

| Command | Result | Notes |
| --- | --- | --- |
| `git diff --stat 29b6fde..0d1a0fc` | 31 files | no `build.gradle`, `gradle/`, lockfile or `src/main/resources` change |
| `./gradlew test --tests` AccessCheckService, AccessGrpcService, ProjectController, ArchitectureRules, MethodSecurityRule, StandInRule (plus four integration-tagged names that `test` skips) | passed 142, failed 0, skipped 0 | counts from `build/test-results/test/*.xml` |
| `./gradlew integrationTest --tests` ProjectLifecycleIntegrationTest, ProjectDeletedMessagingTest, CatalogArchiveHandlerTest, ProjectServiceTest, ProjectLockTest, PageServiceTest, GenerationContractServiceTest | passed 114, failed 0, skipped 0 | Testcontainers; counts from `build/test-results/integrationTest/*.xml` |
| `python3 -I scripts/spec-check.py` | Errors: 0 | report only; Must FRs with no test: 27 (FR-38 to FR-40 not among them) |
| grep of the diff for secret-like values | clean | only `PostgresFixture` constant names in tests |

Not run: the full `./gradlew check` (spotless, the whole `test` and `integrationTest` suites, coverage of the new
`ProjectLifecycleService`), the OSV scan (no dependency change), the Kind exit (no owner slot). Not checked: the
`project_deleted.v1` JSON schema in `web-block-contracts` (not pinned into `contracts/`, no local copy); the field set
was checked against FR-38.6 and the test that asserts the exact key set. Payment's and Provisioning's consumers.

## Findings

### M1 (medium): a deleted or suspended website's generation keeps going; codegen-input serves a deleted project

- `src/main/java/org/kshrd/webblock/project/application/generation/handler/GenerationAdmissionService.java:80-81`
  checks `readOnly()` on an unlocked read, then calls Payment, then `generate()` (`:110-118`) moves the request
  `captured -> generating` and publishes `generation_requested.v1` with no second look at the project.
- `src/main/java/org/kshrd/webblock/project/application/generation/handler/CodegenInputService.java:63` reads the
  project with the unfiltered `find`, so a `generating` request of a deleted project still gets its input.
- Breaks FR-38 ("works while a generation request is in flight (its admission later fails it)") and the FR-38 rule
  that a deleted website is unknown from then on; for suspend, FR-39.4 / decision `0007` (read-only refuses changes).
- Abuse or failure scenario, now reachable by any owner because this change adds the delete and suspend endpoints:
  1. The owner captures a request. The admission job reads the live project and waits on `admitGeneration`.
  2. The owner sends `DELETE /api/v1/projects/{id}`: 204, `deleted_at` set, `project_deleted.v1` stored.
  3. Payment answers ADMITTED; `generate()` moves the request to `generating` and stores `generation_requested.v1`
     after `project_deleted.v1` (same outbox key, so consumers see "requested" after "deleted").
  4. AI Codegen calls `/internal/v1/generation-requests/{rid}/codegen-input`: 200 with the deleted site's captured
     pages (customer content), which AI Codegen sends on to the LLM. Payment has counted the attempt.
  The same happens with no race at all for a request that is already `generating` when the owner deletes: nothing in
  this service ever fails a `generating` request (the sweep covers `captured` only), so codegen-input answers 200 for
  the deleted project indefinitely. No cross-tenant access; the harm is customer content processed and sent to an
  outside host after the owner deleted the site, and a generation that can outlive the delete.
- Suggested fix: in `generate()`, make the move conditional on the project in the same transaction (lock the project
  row, or a conditional UPDATE that joins `projects` with `deleted_at is null and status <> 'suspended'`), else fail
  it `project_unavailable`. In `CodegenInputService`, refuse a request whose project is deleted with an existing code
  (`GENERATION_REQUEST_NOT_FOUND` or `REQUEST_NOT_GENERATING`). Have the owner decide what delete does to a
  `generating` request (for example fail it `project_unavailable` in the delete transaction) and state it in FR-38.
  Tests: delete during admission; codegen-input after delete.

### M2 (medium): decision 0007 still says the owner reads a deleted project; the access-check answer changed

- `.claude/docs/decisions/0007-project-access-rules.md:7` ("A deleted or suspended project allows the owner's reads
  (including the release history)") is contradicted by
  `src/main/java/org/kshrd/webblock/project/application/access/handler/AccessCheckService.java:41-50` (deleted is
  `NOT_FOUND`, `PROJECT_DELETED` is no longer answered) and by `ProjectService.getOwned/lockOwned` (deleted is 404).
- Breaks `.claude/CLAUDE.md` section 6 "the decision record changes before a rule in code" (Art. I), and Art. III,
  which names decision `0007` as its enforcement. The access-check answer is part of the Provisioning contract
  (FR-30.4, FR-31.4, FR-32.2 changed), which is an "ask first" item.
- Abuse: none directly; the new rule fails closed. The risk is drift: the next change that follows `0007` restores
  owner reads of a deleted project, and Provisioning's `get_release` / `list_releases` for a deleted project now
  answer `NOT_FOUND` where the accepted decision promises `allowed: true`.
- Suggested fix: the owner amends `0007` in this branch (deleted is unknown to every call and to the access check;
  `PROJECT_DELETED` kept in the reason list but not answered) and confirms the access-check change with Provisioning.

### L1 (low): the owner's repeat create of a deleted project answers 200 with the deleted website

- `src/main/java/org/kshrd/webblock/project/application/wizard/handler/ProjectCreationService.java:88-90` and
  `:187-201`: `find` returns the deleted row and `repeat()` returns `CreationOutcome(existing, false)`.
- Breaks FR-38 ("from then on every per-project call of the caller ... treat the website as unknown").
- Scenario: the owner deletes P, then the wizard retries `PUT /api/v1/projects/P` with the original body: 200 with
  the full `ProjectView` of P (status, `readOnly` true, hostname), while `GET /api/v1/projects/P` is 404. Only the
  owner sees it, so nothing leaks to a stranger (a stranger gets the 409 that decision `0007` allows), but the client
  is told its website exists.
- Suggested fix: in `repeat()`, answer `PROJECT_ID_CONFLICT` (409) when `existing.deleted()`; the ID stays used for
  good, which is exactly the recorded exception. Add a test.

### L2 (low): the archive job can archive a catalog website its registration did not write

- `src/main/java/org/kshrd/webblock/project/infrastructure/messaging/catalog/CatalogArchiveHandler.java:36-37`
  archives any `catalog.websites` row with `website_id = <project id>`.
- Breaks FR-40 ("sets the website's status to archived": the project's own website) and the registration rule that
  "the job never adopts" a foreign row (`catalog-database.md`).
- Confirmed, narrow: create refuses an ID the catalog already holds (`ProjectCreationService.java:97`), so a foreign
  row with the project's ID can only appear between that check and the registration job (seconds, or up to about 44
  minutes while the catalog is down), for example a demo shop written by Provisioning's `catalog_platform register`
  tool. The registration then ends FAILED with "website conflict" and keeps its pending row. If the owner deletes the
  project, the archive job finds the pending row, runs the UPDATE, and archives the demo shop. No user controls the
  foreign insert, hence low.
- Suggested fix: while a pending registration exists, archive only the registration's own row (the `OWN_WEBSITE`
  predicate of `CatalogRegistrationHandler`: business type, role name and the pending row's `created_at`); a foreign
  row fails the job without retry for an operator, like `WebsiteConflict`.

### L3 (low): operators have no runbook step for the archive job

- `CatalogArchiveHandler.java:26` sends a FAILED archive to `.claude/docs/runbooks/catalog-database.md`, which does
  not mention the archive job.
- Breaks FR-40 (operator outcome of FR-40.3) and Art. VII (a runbook step people rely on).
- Scenario: a registration that ended FAILED is re-driven by an operator after the owner deleted the project. The
  archive job has already ended FAILED (its 16 retries ran while the registration was pending), so the re-drive
  writes an `active` catalog website for a deleted project and nothing archives it unless the operator also re-drives
  `catalog-archive:<id>`.
- Suggested fix: a short runbook section: the job key, what FAILED means, and "re-drive the archive after any
  registration re-drive of a deleted project".

### L4 (low, owner decision): delete plus never-reused sub-domains lets one account hold unlimited sub-domains

- `src/main/java/org/kshrd/webblock/project/application/project/handler/ProjectLifecycleService.java:58-62` with
  `ProjectService.subdomainTaken` (deleted projects included, Notion "2 · Project").
- Touches FR-38 and FR-13 together with PAY section 4 (Payment stops counting a deleted project toward the website
  limit).
- Scenario, once Payment consumes `project_deleted.v1`: an account with a one-website limit loops
  `PUT` (sub-domain `brand-n`) then `DELETE`; each cycle frees the slot and keeps `brand-n` taken for ever. Bounded
  only by request rate and Payment admission.
- Suggested fix: the owner decides (rate limit creates per owner, cap deleted projects per owner, or a release rule
  for sub-domains of deleted projects, which needs a Notion change). Not a code fix for this branch.

## Checked and clean

- Ownership, REST: every `/api/v1/projects/{id}/**` path reaches the project only through `getOwned`, `lockOwned`,
  `advanceEditRevision` or `rename`, all filtered on owner and `deleted_at is null`; a stranger, an unknown and a
  deleted project give the same 404 `PROJECT_NOT_FOUND` (`ProjectService.java:111-129`, `whyNotChanged` goes through
  `getOwned`). FR-38.2, FR-38.3, FR-39.5 tests pass.
- Repository predicates: `markDeleted` (`owner = :owner and deletedAt is null`), `changeStatus` (owner, not deleted,
  `status in :from`), `rename` and `advanceEditRevision` (owner, not deleted, not suspended): each re-checks every
  condition in the UPDATE itself.
- Races: delete is one conditional UPDATE; suspend and reactivate take `lockOwned` then a conditional UPDATE; capture
  and contract freeze take `lockOwned`; page writes use the conditional revision UPDATE. Under READ COMMITTED a
  waiting UPDATE or `FOR UPDATE` re-reads the committed row, so delete vs suspend, rename, page save, capture and
  freeze serialise on the project row with one consistent lock and no lost update. Only the admission path is open
  (M1). `ProjectLockTest` passes.
- Internal access check (REST and gRPC share `AccessCheckService`): deleted is `allowed: false, NOT_FOUND` for every
  action and caller; suspended keeps reads, refuses writes with `PROJECT_SUSPENDED`. FR-38.5 tests pass for both.
- Decision `0007` 409 exception: unchanged; a stranger's `PUT` on a deleted ID is 409 `PROJECT_ID_CONFLICT`, which
  reveals only that the ID is used (see L1 for the owner's case).
- Event: `ProjectDeleted` carries exactly `schema_version`, `message_id` (UUIDv7), `message_type`, `occurred_at`,
  `correlation_id` (project ID), `project_id`; no owner ID, no name, no hostname. `ProjectDeletedMessagingTest`
  asserts the exact key set and that a refused delete publishes nothing.
- Atomicity: the event is published and the archive scheduled only after `markDeleted` returned 1, in the same
  `@Transactional` method; `IntegrationEvents.outboxKey` refuses a publish outside a transaction and the scheduler is
  `Propagation.MANDATORY`, so `deleted_at`, the event and the job commit or roll back together.
- Archive SQL: bound parameters only, the project ID from the job payload (which holds nothing else), only `status`
  written, inside the `catalog_platform` grant (`V3__catalog_access.sql:53` `UPDATE (status, ...)`, `V4` `SELECT`);
  catalog statements bounded (statement timeout 5 s, socket timeout 10 s, `CatalogDataSourceConfig`).
- Archive retry and termination: pending read before the update, so a registration finishing in between is archived;
  16 retries with the registration's backoff, then FAILED; no website and no pending row completes with a warning;
  replay is idempotent (`status <> 'archived'`). `CatalogArchiveHandlerTest` passes.
- Suspended is read-only everywhere a user writes: rename and page create, update, delete (conditional UPDATE excludes
  `suspended`), contract freeze and capture (`readOnly()` after `lockOwned`), access-check write actions
  (`PROJECT_SUSPENDED`). FR-39.4 test passes. Admission refuses it too, apart from the M1 race.
- Tokens and filter chains: `SecurityConfig` is unchanged; the new routes sit under `/api/**`, which requires a token
  with the text `user_id` claim; the owner comes from `CurrentUser`, never from the request. No method security
  (`MethodSecurityRuleTest` passes). No token is logged, forwarded or put in an error by the new code.
- Errors: refusals are `WebBlockException(ErrorCode.PROJECT_NOT_FOUND)`, written as RFC 9457 by the existing advice;
  no upstream reason or customer content in them. Log lines carry the project ID only.
- Profiles and secrets: no stand-in, profile or configuration change; no secret value in code, tests or docs of the
  diff (`StandInRuleTest` passes).
- Database: no migration or grant change; the delete is a soft delete (`deleted_at`), no `DELETE` of business rows.
- Supply chain: no dependency, version, lockfile or verification-metadata change.

Verdict: needs fixes. No High finding, so nothing blocks the merge under Art. VIII. M1 and M2 belong in the one fix
round; if the owner merges first, they become backlog items under FR-38 (M1) and decision `0007` (M2). L1 to L3 are
small and fit the same round; L4 is an owner decision.
