# Validation: task 8 ed5fcaf..3ca6db0

Stage 5 Verify. IDs in scope: FR-32, FR-32.1, FR-32.2, FR-32.3, FR-30, TR-11, TR-27; design G20, G21, b.4.
Branch `manage-site`, commits bd8a0c4 and 3ca6db0. Read: plan task 8, design b.4, b.7, G20, G21, FRD rows FR-30,
FR-32, FR-33, TRD TR-11 and TR-27, `git diff ed5fcaf..HEAD`, `web-block-contracts` at 18dd301, Notion "Web Block
architecture" (read-only search: the site portal sends the action `manage_site`).

## Coverage

| ID | Tests | Status | Evidence |
| --- | --- | --- | --- |
| FR-32.1 | `AccessCheckServiceTest.fr32_1_manageSiteIsAllowedForTheOwner`, `AccessCheckControllerTest.fr32_1_manageSiteIsAllowedForTheOwner`, `AccessGrpcServiceTest.fr32_1_manageSiteIsAllowedForTheOwner`, `AccessActionListTest.fr32_1_theEnumEqualsTheSharedList` | done | Owner of a live project gets allowed true, no reason; another user gets `NOT_MANAGER` (service, gRPC). The tests read `manage_site` through `fromWireName(...).orElseThrow()`, so they fail without `MANAGE_SITE` |
| FR-32.2 | `AccessCheckServiceTest.fr32_2_manageSiteOnADeletedProjectIsProjectDeleted` (deleted and suspended), `AccessCheckControllerTest.fr32_2_...`, `AccessGrpcServiceTest.fr32_2_...` | done | `PROJECT_DELETED`; the service test also proves `PROJECT_SUSPENDED` |
| FR-32.3 | `AccessGrpcServiceTest.fr32_3_anUnknownActionIsInvalidArgument` (existing), `AccessCheckControllerTest.fr30_6_fr32_3_anUnknownOrUpperCaseActionIsVALIDATION_FAILED` (renamed), `AccessCheckServiceTest.fr32_3_dropDbIsBadInput` (new) | done | 400 `VALIDATION_FAILED` and `INVALID_ARGUMENT`, field `action`, `VALUE_INVALID`; service never called |
| FR-32 | `AccessCheckServiceTest.fr32_exactlyTheFiveChangesAreWriteActions`, `AccessActionListTest.fr32_theFilePassesItsSchema` | done | The enum holds exactly the 11 FRD names; the five changes are write actions, the six gets and lists are not |
| FR-30 (30.4, 30.5) | `AccessCheckServiceTest.fr30_4_...`, `fr30_5_...` (parameterized over every action) | done | No behaviour change for existing actions: the old `read` flags are the exact inverse of the new `write` flags, and `!isRead()` became `isWrite()` in both guards (`AccessCheckService.java:48`, `:51`) |
| TR-27 | `AccessActionListTest` (8 cases: equality, schema pass, 6 schema refusals) | done, see finding 1 | Enum map (wire name to `isWrite`) must equal the file map; names unique; `manage_site` true asserted. Cannot pass vacuously: a missing file throws, an empty or shorter list breaks equality, the 6 broken lists prove the schema refuses |
| TR-11 | `scripts/contracts-sync.sh --check` | done | `contracts.lock` is 18dd3012405811b4a77fa36dafc7cf044b5fef6d; sync list adds both files; the three files are byte-identical with `git show 18dd301:<path>`; `access.proto` is unchanged since afff924, so the stubs are unchanged |
| G21 | `AccessAction.java` | done | Flag renamed `read` to `write`, values match `actions.v1.json`; `admin_allowed` is not read by Project (FR-33.1 test still passes) |

No Orphan code: every changed line serves FR-32, TR-11 or TR-27. No Drift between code and FRD rows. The file content
matches design b.4 exactly.

## Gates

| Command | Result | Notes |
| --- | --- | --- |
| `./gradlew check --rerun-tasks` | passed | 24 of 24 tasks executed, BUILD SUCCESSFUL in 3m 8s. A plain `./gradlew check` first was UP-TO-DATE for every task, so I forced a rerun |
| `test` (in check) | passed 848, failed 0, errors 0, skipped 0 | counts from `build/test-results/test/*.xml`; `AccessActionListTest` 8 |
| `integrationTest` (in check) | passed 301, failed 0, errors 0, skipped 0 | counts from `build/test-results/integrationTest/*.xml` |
| `spotlessCheck`, `jacocoTestCoverageVerification`, `contractStubsInBootJar` (in check) | passed | |
| `compileJava compileTestJava --rerun-tasks` | passed | 9 Error Prone or deprecation warnings, none in a file this task touched |
| `CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts scripts/contracts-sync.sh --check` | passed | "contracts/ matches commit 18dd3012..." |
| `python3 -I scripts/spec-check.py` | 0 errors | report only; Must FRs with no test: 28 |
| `scripts/osv-scan.sh` | not run | no dependency change (the networknt validator was already pinned at 3.0.8) |
| docs dash and line checks on the changed files | passed | no U+2013 or U+2014 in the diff; no line over 120 in the touched script and Java files |
| `pmdMain pmdTest spotbugsMain cpd` | not run | report-only, not asked for this task |

## Constitution

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | Commits cite TR-11, FR-32 and FR-32; every new test names its ID; the code follows FRD FR-32 and design b.4 |
| II Tech stack | pass | No new dependency; contracts pinned by full SHA, written only by the sync script |
| III Architecture limits | pass | `AccessAction` stays in `access/application/model`; no ArchUnit or Modulith change; no database change; `NOT_FOUND` and `NOT_MANAGER` unchanged; the file is Project's own (b.4), its `admin_allowed` values stay Provisioning's to confirm while DRAFT |
| IV Coding conventions | pass | No new class outside its folder, no injection change, no deprecated API in touched code |
| V Testing and quality | pass | Gates rerun, counts above, coverage rule passed; finding 1 is a gap in how the test is wired, not a lowered gate |
| VI Safety guardrails | pass | No cluster command, no secret, no push |
| VII Docs | pass for this diff | Only Java and script comments changed, plain English, no dashes. Several spec docs now state stale facts; the lead's follow-ups below fix them before merge |
| VIII Agents | pass | I did not write this work; I wrote only this report |

## Findings

1. important, `build.gradle` (test task, no input for `contracts/access`), TR-27, G20. `AccessActionListTest` reads
   `contracts/access/actions.v1.json` and its schema from disk at run time. The Gradle `test` task declares no input
   for those files, so a later change that only moves `contracts.lock` and syncs a new list leaves `test`
   UP-TO-DATE, and `./gradlew check` reports success without running the comparison G20 exists for. Fix: declare
   the folder as a test input, for example in the `test` task block
   `inputs.dir('contracts/access').withPropertyName('accessActionList').withPathSensitivity(PathSensitivity.RELATIVE)`,
   cite TR-27 and FR-32. Related to Art. V (a result must come from a real run), not a conflict.
2. minor, `AccessCheckServiceTest.java` `fr32_3_dropDbIsBadInput`. The test checks `AccessCheckRequest` Bean
   Validation, not `AccessCheckService`. The plan asked for it in this class, and the controller and gRPC tests prove
   the same row end to end, so no change is required; a later move to a request test would place it with its subject.
3. minor, `.claude/docs/requirements/spec.json:909`. It still names the old display name of the renamed controller
   test ("FR-30.6: an unknown or upper-case action is 400 ..."); it now reads "FR-30.6, FR-32.3: an unknown (drop_db)
   or upper-case action ...". Part of the lead's spec follow-ups.

Spec follow-ups for the lead (not edited here), so the docs state current facts (Art. VII):

- `spec.json`: FR-32 test names (add the FR-32.1, FR-32.2 and `AccessActionListTest` tests; fix line 909); TR-27 tests.
- FRD lines 295 to 297 "Proven today by": add `AccessActionListTest` (TR-27, FR-32) and the FR-32.1 and FR-32.2 tests
  in `AccessCheckServiceTest`, `AccessCheckControllerTest`, `AccessGrpcServiceTest`.
- FRD line 418: the `manage_site` Drift note is closed.
- TRD lines 126 and 127: the S2 §2.4 and TR-27 Drift notes are closed.
- DIRECTION lines 11, 20, 172, 195 (and 238 if task 8 is no longer pending): `manage_site` is accepted, TR-27 done.
- Also stale, outside the brief's list: PRD line 77 (PR-14 Drift), BRD lines 89 and 90, decision `0011` lines 81 to
  83 ("Still to build: task 8").

## Also needed

- Security review: recommended. The change widens the access check by one accepted action (`manage_site`) and moves
  the contracts lock; no token, `SecurityConfig`, mesh policy, secret or dependency changed.
- DB audit: not needed, no migration or schema change.

Verdict: needs fixes (1): finding 1, the test input for `contracts/access`. Findings 2 and 3 and the spec follow-ups
do not block the task.

## Re-review 1: 3ca6db0..03f669f

Stage 5 Verify, fix round 1 of 2. IDs: TR-27, FR-32 (finding 1).

Change read: `build.gradle:182` and `:183`, the `test` task only, one comment and
`inputs.dir('contracts/access').withPropertyName('accessActionList').withPathSensitivity(PathSensitivity.RELATIVE)`.
Commit message cites TR-27 and FR-32. Nothing else changed.

Finding 1: closed. Proof without editing any file: `./gradlew test --rerun -Dorg.gradle.caching.debug=true` prints
`Appending input file fingerprints for 'accessActionList' to build cache key` with a content hash per file
(`actions.v1.json` and `actions.v1.schema.json`, relative paths). A change of any byte in either file changes the
key of `test`, so `test` can no longer be UP-TO-DATE after a new list is synced. `contracts/access/actions.v1.json`
has the same sha256 as `git show 18dd301:access/actions.v1.json` before and after the run.

New problems from the fix: none. `PathSensitivity` is a default Gradle import; the two lines are 116 and 117
characters, no dashes; `integrationTest` runs no untagged test, so it needs no such input. A missing
`contracts/access` folder now fails the build, which fails closed.

| Command | Result | Notes |
| --- | --- | --- |
| `./gradlew test --rerun -Dorg.gradle.caching.debug=true` | passed 848, failed 0, errors 0, skipped 0 | counts from `build/test-results/test/*.xml` |
| `./gradlew check` on 03f669f | passed | `test` UP-TO-DATE from the run above; `integrationTest` UP-TO-DATE: the fix changed none of its inputs, so it is my earlier `check --rerun-tasks` run (301 passed, 0 failed, 0 skipped); coverage verification executed and passed |
| `CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts scripts/contracts-sync.sh --check` | passed | matches 18dd3012405811b4a77fa36dafc7cf044b5fef6d, run after the test run |
| `python3 -I scripts/spec-check.py` | 0 errors | report only; Must FRs with no test: 28 |

Constitution: articles I to VIII unchanged from the first review, all pass.

Still open, not blocking, owned by the lead: minor 3 (`spec.json:909`) and the spec follow-ups listed above.
Minor 2 needs no change.

Verdict: approved.
