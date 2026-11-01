# Final review: project-notion-alignment a5c5b79..546ceeb

Verdict: needs a fix wave (not ready to merge yet). The code is sound and every local gate is green on the head, but
the merge rules are not met: `.claude/docs/requirements/DIRECTION.md` does not exist, two Must items still have Drift
that only the owner can rule on (FR-32 `manage_site`, and Art. III against FR-31.2), the constitution check has
conflicts (Art. II and Art. VIII on the record, Art. III the known question), the cross-service Kind check has not
run, and the handoff is stale. After fix wave items 1 to 5 and the owner rulings R-1 to R-4 below, this becomes
"ready with listed carry-overs".

Stage 5 Verify, final review of the whole branch (49 commits, 215 files). IDs covered: FR-10, FR-13 (FR-13.1 to
FR-13.4), FR-15.1, FR-30, FR-31 (FR-31.1 to FR-31.6), FR-32 (FR-32.1 to FR-32.3), FR-33 (FR-33.1, FR-33.2), FR-34,
FR-35, FR-36 (FR-36.1 to FR-36.8), FR-5; TR-4, TR-5, TR-10, TR-11, TR-13, TR-14, TR-15, TR-16, TR-17, TR-24, TR-25,
TR-26, TR-27, TR-39; design decisions G1 to G26 of `.claude/docs/designs/j3-notion-alignment.md`.

Read: `git diff main...546ceeb` in full for `src/main`, `build.gradle`, `gradle/`, `scripts/`, `contracts/`, the hooks
and settings, and every changed doc; the constitution (Version 2); BRD, PRD, FRD, TRD, `spec.json`; the J3 design
and plan; decisions 0007 and 0010 to 0015; `ai/reviews/j3-tasks-3-4-review.md`,
`ai/reviews/db-notion-audit-project-notion-alignment.md`, `ai/reviews/j3-grpc-security-review.md`; the QA sweep
`ai/reviews/qa-project-notion-alignment.md` (written at 546ceeb while this review ran); `ai/handoffs/next.md`. Notion
"2 · Project" fetched read-only (last edited 2026-10-22T10:17:01.919Z); "6 · Shared catalog" and the platform-db-v6
hub seen through search (2026-10-21T14:16, 2026-10-26T16:21). All three equal the times the DB audit used.

## Evidence

| Check | Commit | Result | Notes |
| --- | --- | --- | --- |
| `./gradlew spotlessCheck` | 546ceeb | passed | |
| `./gradlew compileJava --rerun compileTestJava --rerun` | 546ceeb | passed | 9 warnings (4 Error Prone main, 1 Error Prone test, 4 deprecation test); `git blame` puts every one on `main`; none new |
| `./gradlew check test --rerun integrationTest --rerun` | 546ceeb | passed | BUILD SUCCESSFUL; `contractStubsInBootJar` ran |
| `test` | 546ceeb | passed 797 | 0 failures, 0 errors, 0 skipped (result files) |
| `integrationTest` | 546ceeb | passed 301 | 0 failures, 0 errors, 0 skipped; PostgreSQL 18 and RabbitMQ in Testcontainers |
| `jacocoTestCoverageVerification` | 546ceeb | passed | no rule or exclusion changed on the branch |
| Earlier runs of mine | 546ceeb | 2 failed, discarded | the QA reviewer's Gradle run shared `build/` at the same time (`Unable to find a @SpringBootConfiguration`, a missing `CurrentUser` bean); the clean rerun above passed |
| `scripts/osv-scan.sh` | 546ceeb | exit 0 | 308 packages, no issues |
| `scripts/contracts-sync.sh --check` | 546ceeb | exit 0 | `contracts/` matches `afff9245444c628b74f88d281939f4bc8b4de3e2` |
| `python3 -I scripts/spec-check.py` | 546ceeb | 0 errors | report only; Must FRs with no test: 29 (FR-1 to FR-12, FR-14, FR-16 to FR-30, FR-34); `--self-test` 15 of 15 |
| `python3 -I .claude/hooks/test_guard.py` | 546ceeb | passed 281 | |
| `python3 -I .claude/hooks/test_hooks.py` | 546ceeb | passed 215 | |
| Docs checks (dashes, 120 characters) on changed files | 546ceeb | passed | one en dash in `OutboxSpikeTest.java:67`, from `2782c64` on `main` |
| Secret scan of the diff | 546ceeb | clean | no value; only `${...}` placeholders; `DevTokenIntrospector` changes are comments |
| DB audit | 498a5f0 | current, 0 DIFFERENT | 3 PLANNED, 7 ACCEPTED-BY-DECISION, 124 MATCH. After 498a5f0 no migration, entity, repository or role script changed (`git diff 498a5f0 546ceeb`); the Notion pages are unchanged |
| Security review (gRPC) | c9499e9 | closed by fix round | 59f4bdf closes findings 1 to 3, 61007fa closes 5, 67c68e8 the mesh notes; finding 4 goes to the owner. No re-review report exists; this review checked the fix round (section Findings) |
| QA sweep | 546ceeb | current | 87 findings, 5 new on the branch, all from manual review; tasks 2, 3, 6 apply here |
| Kind | none | not run | the change crosses services (V6 with Provisioning's routing by sub-domain, design O-4) |
| `DIRECTION.md` | none | missing | `.claude/docs/requirements/DIRECTION.md` does not exist on the head |

## Spec coverage

| ID | Where built | Test | Status |
| --- | --- | --- | --- |
| FR-13.1 | `ProjectCreationService.create`, `ProjectService.create`, `ProjectView.subdomain` | `ProjectCreationIntegrationTest` fr13_1, `ProjectServiceTest`, `ProjectCreationServiceTest`, `ProjectCreationControllerTest` | done |
| FR-13.2 | `ProjectCreationService.java:105-112` (before Payment); `projects_subdomain_key` decides a race | `ProjectCreationIntegrationTest` (deleted holder, race), `ProjectCreationServiceTest`, `ProjectConfigTest`, `J1ErrorCodesTest` | done |
| FR-13.3 | `Subdomain`, `ProjectService.requireValidSubdomain`, `PlatformProperties.reservedSubdomains` | `SubdomainTest`, `ProjectCreationIntegrationTest` fr13_3, `PlatformPropertiesTest` | done |
| FR-13.4 | `ProjectCreationService.repeat` compares the sub-domain | `ProjectCreationIntegrationTest`, `ProjectCreationServiceTest` | done |
| FR-10 (sub-domain in the answer) | `ProjectView` | as FR-13.1 | done |
| FR-15.1 | `ProjectView` | `ProjectControllerTest` fr15_1 | done |
| FR-30 | REST check, `AccessCheckService` | `AccessCheckServiceTest`, `AccessCheckControllerTest` (no ID, written before the FRD) | done |
| FR-31.1 | `AccessGrpcService`, `AccessGrpcMapper`, `GrpcServer` | `AccessGrpcServiceTest`, `CheckAccessIntegrationTest` | done |
| FR-31.2 | `AccessCheckService.decide` answers `NOT_MANAGER` | `AccessGrpcServiceTest` | done against the FRD; conflicts with the Art. III sentence (R-2) |
| FR-31.3 | OK answer, reason `NOT_FOUND` | `AccessGrpcServiceTest` | done |
| FR-31.4 | `AccessCheckService.decide` with `AccessAction.isRead()` | `AccessGrpcServiceTest` (deleted, suspended) | done |
| FR-31.5 | `AccessGrpcMapper.toRequest`, `GrpcErrors` | `AccessGrpcServiceTest` (abc, empty fields, non-canonical UUID) | done |
| FR-31.6 | `DatabaseBusy`, `GrpcErrorInterceptor.answer` | `CheckAccessIntegrationTest` fr31_6 | done |
| FR-32.1, FR-32.2 | `AccessAction` has no `MANAGE_SITE` | none | Drift (Must; FRD and TRD "Drift seen"; task 8 waits for the owner's push of task 7) |
| FR-32.3 | `AccessAction.fromWireName` | `AccessGrpcServiceTest` (drop_db) | done |
| FR-33.1 | subject is only `user_id` | `AccessGrpcServiceTest` (admin, contrary `x-user-id`) | done |
| FR-33.2 | `ErrorCode.PROJECT_DELETED`, `PROJECT_SUSPENDED`; `GrpcErrors` `FAILED_PRECONDITION` | `J3ErrorCodesTest`, `GrpcErrorsTest` | done |
| FR-34 | not in scope (waits for the owner's confirmation) | none | missing by plan |
| FR-36.1 to FR-36.6, FR-36.8 | project `V6`, `V7` | `SubdomainMigrationTest`, `MigrationTest`, `GrantsTest`, `ProjectConstraintsTest`, `ProjectsAndPagesTest`, `J1ErrorCodesTest` | done |
| FR-36.7 | audit | `ai/reviews/db-notion-audit-project-notion-alignment.md` (0 DIFFERENT at 498a5f0, still current) | done |
| FR-35 (catalog V6, V7) | catalog `V6`, `V7` | `CatalogMigrationTest`, `CatalogGrantsTest`, `CatalogRlsTest` | done |
| FR-5 (REST pool timeout) | `GlobalExceptionHandler.handleUnexpected` with `DatabaseBusy` | `RetryAfterWebTest` (cites TR-24, FR-31; finding 9) | done |
| TR-4 | `TYPE_FOLDERS`, two gRPC ArchUnit rules | `ArchitectureRulesTest`, `GrpcRuleTest` | done |
| TR-5 | 23 moves into `application/{request,response,validation}` | `ArchitectureRulesTest`, `ModuleStructureTest` | done |
| TR-10 | `contracts` source set, protobuf plugin, grpc-java 1.83.1 | `contractStubsInBootJar` in `check` | done |
| TR-11 | `contracts.lock`, `scripts/contracts-sync.sh` | `--check` exit 0 | done |
| TR-13 | `guard.py` `CODE_PATHS` has `scripts/` | `test_hooks.py`, `test_guard.py` | done |
| TR-14, TR-15 | V6, V7 in both databases | audit 0 DIFFERENT; `GrantsTest`, `CatalogGrantsTest` | done |
| TR-16 | V1 to V5 untouched in both folders | diff shows only added files | done |
| TR-17 | project `V6` | the FR-36 tests, G1, G2 | done |
| TR-24 | `GrpcServer`, `GrpcCallLimitInterceptor`, `GrpcProperties` | `GrpcServerTest`, `GrpcPropertiesTest`, `CheckAccessIntegrationTest` | done; finding 4 (a stalled call holds a place) |
| TR-25 | `GrpcErrors`, `GrpcErrorInterceptor`, `GrpcRequestIdInterceptor`, `GrpcUnknownMethodRegistry` | `GrpcErrorsTest`, `GrpcServerTest` | done |
| TR-26 | `AccessGrpcMapper` uses `AccessCheckRequest` and its validation | `AccessGrpcServiceTest` | done |
| TR-27 | no `actions.v1.json` pinned, no `AccessActionListTest` | none | missing (task 8) |
| TR-39 | `.claude/docs/runbooks/migrations.md` "Rolling out V6" | none (runbook) | done; finding 8 (no backup step) |
| G1 to G4, G6 | V6 rebuild, pre-check, names, one guard, grants | `MigrationTest` g1, `SubdomainMigrationTest` g2 and FR-36 rows, `GrantsTest` | done |
| G5 | no CHECK on the label in the database | audit MATCH | done |
| G7, G8 | `Subdomain`, `requireValidSubdomain`, creation order | `ProjectCreationServiceTest` fr13_1 (order), race test | done |
| G9, G10 | `ProjectView` shared (design O-3); `p-` backfill | FR-13.1, FR-15.1, FR-36.1 | done |
| G11, G12 | sync script; own source set, no exclusion | contracts `--check`; `contractStubsInBootJar` | done |
| G13 to G16 | lifecycle bean in `common/presentation/grpc`; 64 KiB and 16 KiB; status `switch` with no default | `GrpcServerTest`, `GrpcErrorsTest` | done |
| G17 to G19 | one request record and service; subject is `user_id`; deleted and suspended as reasons | `AccessGrpcServiceTest`, `J3ErrorCodesTest` | done |
| G20, G21 | `AccessAction` equals the pinned list with a `write` flag | none | missing (task 8); the enum keeps a `read` flag today |
| G22, G23 | two gRPC ArchUnit rules; validation moved with requests | `GrpcRuleTest`, `ArchitectureRulesTest` | done |
| G24, G25 | no REST mapper folder; services keep their signatures | `ArchitectureRulesTest`, whole suite | done |
| G26 | `scripts/` in `CODE_PATHS` | `test_guard.py` | done |

Orphan: none. Every added class serves an ID above; the REST pool-timeout change answers security finding 1 under
FR-5. Drift: FR-32.1 and FR-32.2 (listed in the FRD and TRD), and FR-31.2 against Art. III (R-2).

## Constitution

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | Every commit touching code since `88dd1b5` cites an ID; the 11 commits without an ID are the spec and process setup (only `88dd1b5` touches `scripts/`, before TR-13 made `scripts/` code); spec-check 0 errors; the Must Drift is reported, not hidden |
| II Tech stack | conflict | The gRPC stack is allowed by Version 2 and pinned, locked, verified, recorded and OSV clean. But Art. II says the guard "asks before lock rewrites" (`CONSTITUTION.md:41`), and `f4adb20` removed that ask (`guard.py` now returns OK, `settings.json` lost the three `ask` lines). The plan says "the owner approves the exact versions" in task 5 (`j3-notion-alignment-plan.md:32`, `:122`); no record of that approval is in the repo. R-3 |
| III Architecture limits | conflict | Layout, ArchUnit and Modulith, repositories, boundaries and both databases pass (audit 0 DIFFERENT, current). Conflict: Art. III says a caller who may not see a project gets reason `NOT_FOUND` from the access check; `AccessCheckService.java:45-46` answers `NOT_MANAGER`, as FRD FR-30.2 and FR-31.2 require. Known open item, R-2 |
| IV Coding conventions | pass | Constructor injection, package-private gRPC classes, `WebBlockException` with `ErrorCode`, a `switch` with no default in `GrpcErrors`, no new compiler warning |
| V Testing and quality | pass | 797 and 301 passed, 0 skipped; coverage rules unchanged and green; no `@Disabled`, no new exclusion; the one new `@SuppressWarnings("unchecked")` in a test has its reason |
| VI Safety guardrails | pass | No secret value; no cluster command; mesh and V6 rollout steps are written for the owner; Notion read only; the hook and settings edits were the owner's (`f4adb20` message) and the guard asks before constitution edits |
| VII Docs | pass | Plain English, no dash, lines within 120 in the changed docs. Stale facts remain (findings 5 to 7) and go in the fix wave |
| VIII Agents | conflict | `j3-notion-alignment-plan.md:3`: "Approved at gate 3 by the lead, with the owner in auto mode". Art. VIII says the owner approves the plan. R-4 |

## Findings

Critical: none.

Important

1. `.claude/docs/requirements/DIRECTION.md`: missing. `.claude/rules/spec-driven.md:20` and `.claude/WORKFLOW.md:76`
   require the direction check on the final code before a merge. Fix: the spec-writer runs
   `checking-requirements-direction` on the head and writes it, with FR-32.1, FR-32.2 and FR-31.2 as Must Drift
   waiting for the owner.
2. `.claude/docs/CONSTITUTION.md:41` (Art. II) names a guard ask before lock rewrites that `f4adb20` removed
   (`.claude/hooks/guard.py`, dependency rule returns OK; `.claude/settings.json` lost the `--write-locks`,
   `--update-locks`, `--write-verification-metadata` asks). The commit says the gate approves pins, but the design
   left the versions to task 5 and no owner approval of grpc 1.83.1, protobuf 4.35.1, proto-google-common-protos
   2.78.0, plugin 0.10.0 and guava 33.7.2-android is recorded. Fix: owner ruling R-3 (amend the enforcement line or
   restore the ask, and confirm the versions; the lead records the answer in the handoff).
3. `.claude/docs/plans/j3-notion-alignment-plan.md:3`: gate 3 approved by the lead. Fix: owner ruling R-4 (ratify
   the plan at gate 4, or say what changes).
4. `src/main/java/org/kshrd/webblock/project/common/presentation/grpc/GrpcCallLimitInterceptor.java:44-57`: a call
   takes its place at call start and gives it back only at half-close, cancel or complete. A client that opens 8
   calls and never sends the message or half-close, with no deadline, holds every place until Netty's connection age
   ends them (30 minutes plus 10 s grace, `GrpcServer.java:43-44`); meanwhile every CheckAccess answers
   `DEPENDENCY_UNAVAILABLE` and Provisioning fails closed. Only a caller the mesh admits can do it (on Kind, any
   pod). TR-24, FR-31. Fix: take the place in `onHalfClose`, right before the handler runs (refuse there when none
   is free), and give it back after; test: 8 calls that never half-close do not stop a ninth from being answered.
   Add one sentence to decision 0011 and TR-24.
5. `ai/handoffs/next.md:21, 30-31, 39-45`: stale. It says the last code commit is `c9499e9`, task 5 has no review,
   task 6's security review is "running", and gate results are "not recorded" for the head. The fix round `59f4bdf`,
   `61007fa`, `67c68e8`, `2844158` and the gate results at 546ceeb (this report) are missing. Art. VII. Fix: the
   docs-keeper updates it with `phase-handoff`.

Minor

6. `.claude/docs/requirements/FRD.md:414-416` "Questions for the owner: None open" (and the same in BRD, PRD and
   `TRD.md:131-133`), while two owner questions are open: Art. III against FR-31.2, and the sub-domain labels
   (`xn--`, empty reserved list in production). Fix: list both, or point to the handoff's "Open for the owner".
7. `.claude/docs/decisions/0007-project-access-rules.md:10-12`: the create repeat is "the same owner, name, business
   type and template"; since FR-13.4 it also compares the sub-domain (`ProjectCreationService.repeat`). Fix: add the
   sub-domain.
8. `.claude/docs/runbooks/migrations.md` "Rolling out V6": V6 drops `project.domains`, rebuilds `project.projects`
   and has no way back ("fix forward"), but the steps take no backup first. Fix: add a step before the job: take a
   database backup (or confirm a recent CNPG backup) and record its name.
9. `src/test/java/org/kshrd/webblock/project/common/presentation/error/RetryAfterWebTest.java:56`: the REST
   pool-timeout test cites "TR-24, FR-31"; the REST behaviour is FR-5 (TR-35). Fix: cite FR-5 in the display name.
10. QA sweep tasks new on this branch (`ai/reviews/qa-project-notion-alignment.md`, tasks 2, 3 and the `Rows` part
    of 6): three copies of "close a gRPC call with an `ErrorCode`" (`GrpcCallLimitInterceptor.java:95-98`,
    `GrpcErrorInterceptor.java:94-96` with `:122`, `GrpcUnknownMethodRegistry.java:69-74`); `AccessAction` states each
    wire name twice (`@JsonProperty` and `wireName()`); `Rows.project` does not use `TestSubdomains.of`.
11. Design G21 calls the flag `write`; `AccessAction` keeps a `read` flag (`AccessAction.java:40-48`). It is
    equivalent today. Fix: in task 8, name it as the shared file does, so `AccessActionListTest` compares like with
    like.

Checked and clean: the fix round of the security review (call cap with `tryAcquire`, refusal logged once per full
period; Netty limits; cancelled or expired calls skip the lookup; `DatabaseBusy` walks at most 8 causes; unknown
methods answer `UNIMPLEMENTED`, `ROUTE_NOT_FOUND`, without the path; the service package check runs in the
constructor; `serviceNames()` is asserted in `CheckAccessIntegrationTest`); the guava rule only raises up to the pin;
`ErrorCode` statuses agree between REST (`PROJECT_DELETED` and `PROJECT_SUSPENDED` 409) and gRPC
(`FAILED_PRECONDITION`); `application.yml` `webblock.grpc.*` matches `GrpcProperties` defaults, the runbook and
decision 0011; `max-calls-in-flight` 8 stays below Hikari's default pool of 10 (asserted by `GrpcPropertiesTest`);
`ProfileGuard` needs no gRPC rule (the port reads no secret); `.claude/CLAUDE.md` and `.claude/TECH_STACK.md` match
`build.gradle` and `gradle/libs.versions.toml`; the provisioning contract section B states the gRPC transport; V1 to
V5 unchanged in both folders.

## Fix wave

1. Spec-writer: write `.claude/docs/requirements/DIRECTION.md` with `checking-requirements-direction` at the head
   (finding 1). IDs: FR-13, FR-31 to FR-34, FR-36.
2. Implementer: the call limit takes its place at half-close (finding 4), with the failing test first, and one
   sentence each in decision 0011 and TR-24. IDs: TR-24, FR-31. Gates: `./gradlew check`, `GrpcServerTest`.
3. Implementer: QA tasks 2, 3 and the `Rows` part of 6 (finding 10), and the FR-5 display name (finding 9). IDs:
   FR-31, TR-25, FR-32, TR-27, FR-36, FR-5. Gates: `./gradlew check`.
4. Docs-keeper: the handoff (finding 5), decision 0007's repeat (finding 7), the backup step in the V6 rollout
   (finding 8); the spec-writer lists the open owner questions in the requirement files (finding 6). IDs: FR-36,
   FR-31, TR-39, FR-13.
5. Final reviewer: a scoped re-review of the fix-wave commits, then the verdict.

Owner rulings that the fix wave cannot replace:

- R-1: push task 7 (`web-block-contracts` branch `access-actions-v1`, `ea4bc7f`) so task 8 (FR-32.1, FR-32.2, TR-27,
  G20, G21) can run; or merge this branch with FR-32 `manage_site` carried as known Drift.
- R-2: Art. III against FR-31.2 (security review finding 4): amend Art. III to allow `NOT_MANAGER` that callers map
  to 404, or change FR-30.2, FR-31.2 and Provisioning's contract to answer `NOT_FOUND` for both.
- R-3: Art. II's enforcement line and the gRPC versions (finding 2).
- R-4: ratify the plan (finding 3).
- Before the rollout, not before the merge: a Kind slot for the V6 and REST checks (design O-4), the mesh steps of
  `.claude/docs/runbooks/grpc.md`, the V6 rollout steps, the sub-domain label question.

## Deferred minors

No ledger file exists for this branch; the minors of the earlier reviews and the QA sweep are triaged here.

| Ledger line | Decision | Reason |
| --- | --- | --- |
| Tasks 3-4 review 2: site domain cap for a 253 hostname | drop (fixed) | `c3502e3`, `PlatformPropertiesTest` (189) |
| Tasks 3-4 review 3: GrantsTest and controller test names | drop (fixed) | `157c28d` |
| Tasks 3-4 review 4: stale docs after V6 and the layout move | drop (fixed) | `8b9e49e`, `e43c265`; checked: no `DOMAIN_TERMINAL`, `presentation/request` or "no test yet" left |
| Tasks 3-4 review 5: `xn--` labels, reserved list, empty list in production | defer | owner question in the handoff ("Sub-domain labels"); a small FR-13 change after this branch |
| Security review 1, 2, 3, 5 | drop (fixed) | `59f4bdf`, `61007fa`; tested; finding 4 of this report is a residual of 1 |
| Security review 4: Art. III against FR-31.2 | defer | owner ruling R-2 |
| Security review: `merge-base --is-ancestor` check in `contracts-sync.sh` | defer | with the later commit ID change for `contracts/` (design O-8) |
| Security review mesh notes (ports, methods, other rules, Kind) | drop (fixed) | `67c68e8` runbook |
| QA tasks 2, 3, `Rows` part of 6 | fix now | new on this branch and small (fix wave 3) |
| QA tasks 1, 4, 5, rest of 6 | defer | already on `main`; one small-change branch after the merge, as the QA sweep recommends |
| QA owner questions 1, 2, 4 | defer | owner questions in the QA report; no effect on this branch's FRs |
| En dash in `OutboxSpikeTest.java:67` | defer | from `main`; with QA task 6 after the merge |
| 11 commits without an ID | drop | spec and process setup before the IDs existed; history is never rewritten |
| Finding 11 (`read` against `write`) | defer | task 8 |

## Not proven

- Kind: V6 with Provisioning's routing by sub-domain, the create with `subdomain`, the REST check, and CheckAccess from
  Provisioning's client have not run together. Port 9090 is open to every pod on Kind until Istio runs there.
- Istio: the `grpc` port name, STRICT mTLS without a port override, the port-pinned `AuthorizationPolicy` for
  `provisioning-api`, and the port pins on every other ALLOW rule are notes for the owner; nothing proves them.
  HTTP/2 through the Envoy sidecar, and how Envoy and Netty idle, age and keepalive limits interact, are untested.
- DOKS: the migration job, V6 on real data (the collision count O-6, the lock time of the rebuild on the real row
  count), the stop-then-migrate rollout, and Hibernate validation of the new version.
- TLS: the wildcard certificate for `<subdomain>.<site domain>` and the address change of existing sites to
  `p-<12 characters>`.
- Backups: no restore point is part of the V6 steps (finding 8); a restore has not been tried.
- Scale: 8 calls in flight per pod and a pool of 10 are reasoned, not load tested; several replicas multiply the
  database connections.
- Other repositories: task 7 in `web-block-contracts` is not pushed; `web-block-commons` pinning the same grpc-java
  version, Provisioning's gRPC client and its `admin_allowed` confirmation are outside this repo.
