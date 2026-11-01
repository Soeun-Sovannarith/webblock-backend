# Project service: Direction check

**Status:** Draft
**Checked:** branch `claude-makeover` at commit `52f55f5` (code as of `61c2e4b`)
**Verdict:** At risk, because two Must items drift (FR-9.2, FR-18) and J3 Must items have not started; the owner
carries that Drift, so it does not block the merge, but it is not resolved

## In short

At risk: task 8 is built and tested, so `manage_site` is accepted and FR-32, FR-30 and TR-27 are Done. Two Must items
still drift as the owner carries them (FR-9.2, FR-18). The owner's new FR-13 rows (FR-13.5 to FR-13.12: `-preview`,
`preview`, `xn--`, 55 characters, an empty reserved list in production) are written but not built, so FR-13 is In
progress. The rest of J3 (PR-8, PR-18), FR-34, FR-28 and FR-37 (J4) have not started.

**Read next:** [BRD.md](BRD.md) to start the chain again; the items below link into each document.

## Top 5 to look at

1. **The new sub-domain rule is not built** (FR-13.5 to FR-13.12, TR-34): `Subdomain.java` allows `-preview`, `xn--`
   and 56 to 63 characters, `application.yml` lacks `preview`, `ProfileGuard.java` accepts an empty reserved list.
   Action: the designer updates TR-34 and the TRD, then one implementer task. S1 still says 3 to 63 characters:
   decide whether Notion "2 · Project" should say 55.
2. **Requests stuck in `captured`** (FR-28, PR-7): no deadline job exists, so a lost fallback write blocks a
   website's generation with 409 until an operator acts (`ai/handoffs/next.md`, step 5.1).
3. **Unknown business type lists a template** (FR-9.2, R-5): the `blank` template applies to every code
   (`SiteTemplate.java:15`), where FR-9.2 says empty. The owner ruled: known Must Drift for the next owner.
4. **Page limit** (FR-18): closed; the limit is the setting `webblock.pages.max-pages` (S1).
5. **FR-37 needs a queue in Notion** (FR-37, PR-9, R-6): FR-37 consumes `provisioning.publish_completed.v1`, but
   Notion S2 §4.4 and §5.2 give Project no queue or broker permission for it. Decide: add them before J4 (Q-1).

## Count

Cross-cutting TRs (serving all FRs) are counted on their own and not rolled into each FR.

| Level | Done | In progress | Not started | Drift | Orphan |
| --- | --- | --- | --- | --- | --- |
| BR | 4 | 5 | 3 | 3 | |
| PR | 10 | 3 | 9 | 2 | |
| FR | 30 | 2 | 3 | 2 | |
| TR | 38 | 1 | 0 | 0 | |
| Code and work | | | | | 0 |

## Trace

One row per FR under the BR of its first PR; "Closest" means a test with no ID that asserts the row.

### BR-1: A client creates a website from a template in one step, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-8 | PR-1 | TR-5, TR-21, TR-22 | `CatalogReferenceTest`, `BusinessTypeControllerTest` (closest) | Done | |
| FR-9 | PR-1 | TR-5, TR-21, TR-22 | `TemplateControllerTest` (closest); `SiteTemplate.java:15` | Drift | FR-9.2: unknown code gets `blank`; owner accepts (R-5) |
| FR-10 | PR-1 | TR-5, TR-18, TR-22 | `ProjectCreationIntegrationTest`, `ProjectCreationServiceTest` (closest) | Done | |
| FR-35 | PR-17 | TR-9, TR-14, TR-15, TR-20, TR-21, TR-37 | `CatalogRegistrationHandlerTest`, tagged tests | Done | |

### BR-2: Two saves of one website never silently overwrite each other, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-17 | PR-4 | TR-5, TR-22 | `PageControllerTest`, `LivePagesTest` (closest) | Done | |
| FR-18 | PR-4 | TR-5, TR-19, TR-22 | `PageLimitSettingTest`, `PagePropertiesTest` | Done | `webblock.pages.max-pages` is the setting (S1) |
| FR-19 | PR-4 | TR-5, TR-19, TR-22 | `PageServiceTest` (closest) | Done | |
| FR-20 | PR-4 | TR-5, TR-19, TR-22 | `PageServiceTest`, `PageControllerTest` (closest) | In progress | FR-20.2 has no test |
| FR-21 | PR-5 | TR-22 | `ComponentCatalogControllerTest` (closest) | Done | |

### BR-3: A generation request freezes the pages, is counted once and reaches AI Codegen, In progress

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-22 | PR-6 | TR-5, TR-21, TR-22 | `GenerationContractServiceTest` (closest) | Done | |
| FR-23 | PR-6 | TR-21, TR-22 | `GenerationContractServiceTest` (closest) | Done | |
| FR-24 | PR-7 | TR-5, TR-19, TR-22 | `CaptureIntegrationTest` (closest) | Done | FR-24.4 shown with a `captured` request |
| FR-25 | PR-7 | TR-22 | `CaptureIntegrationTest` (closest) | Done | |
| FR-26 | PR-7 | TR-9, TR-20, TR-29, TR-35, TR-37 | `GenerationAdmissionServiceTest`, `GenerationAdmissionHttpTest` (closest) | Done | |
| FR-28 | PR-7 | TR-20, TR-37 | none; `grep -ri deadline src/main/java` finds no job | Not started | handoff step 5.1 |
| FR-27 | PR-16 | TR-9, TR-20, TR-28 | `GenerationRequestedMessagingTest` (closest) | Done | |
| FR-29 | PR-16 | TR-21, TR-23 | `CodegenInputIntegrationTest`, `CodegenInputControllerTest` (closest) | Done | |

### BR-4: Accepted code is committed to Gitea and Provisioning can deploy it, Not started

PR-8, PR-9, PR-18 and PR-19 have no FR yet except FR-37.

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-37 | PR-9 | none yet | no code; J4 | Not started | consumes `publish_completed`; S2 §4.4 lacks the queue |

### BR-5: Each website has a fixed visitor-facing name, In progress

PR-11 (Should, J5) and PR-18 (J3) have no FR yet.

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-13 | PR-2 | TR-5, TR-17, TR-18, TR-22, TR-34 | 32 tagged tests (FR-13.1 to FR-13.4); `PlatformPropertiesTest` (FR-13.12) | In progress | FR-13.5 to FR-13.11 not built |

### BR-6: Other services learn reliably who owns a website and what changed, In progress

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-30 | PR-12 | TR-5, TR-23, TR-27, TR-32 | `AccessCheckServiceTest` (closest), FR-30.4 to FR-30.6 tagged | Done | |
| FR-31 | PR-13 | TR-5, TR-10, TR-11, TR-24, TR-25, TR-26, TR-31, TR-32, TR-35 | 10 tagged tests, FR-31.1 to FR-31.6 | Done | `NOT_MANAGER` as TR-32 and Art. III say |
| FR-33 | PR-13 | TR-10, TR-25, TR-26, TR-32 | `AccessGrpcServiceTest`, `J3ErrorCodesTest` tagged | Done | |
| FR-34 | PR-13 | none | none; waits for Provisioning | Not started | |
| FR-32 | PR-14 | TR-11, TR-27 | `AccessAction.java:21`; 16 tagged tests, FR-32.1 to FR-32.3; `AccessActionListTest` | Done | |

### BR-7: A deleted website goes offline and frees its plan slot, Not started

PR-10 and PR-20 (J5) have no FR yet.

### BR-8: Only the owner sees or changes a website, In progress

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-3 | PR-3 | TR-22, TR-32 | `ProjectControllerTest`, `PageServiceTest`, `ProjectLockTest` (closest) | Done | |
| FR-14 | PR-3 | TR-22 | `ProjectServiceTest` (closest) | Done | |
| FR-15 | PR-3 | TR-22 | `ProjectControllerTest` fr15_1 | Done | |
| FR-16 | PR-3 | TR-5, TR-18, TR-22 | `ProjectServiceTest`, `ProjectControllerTest` (closest) | Done | |

### BR-9: A deleted or suspended website stays readable but cannot be changed, In progress

Served by PR-3 (Done), PR-12 (Done, FR-30) and PR-13 (In progress, FR-34), above. BR-8 is In progress for the same
reason.

### BR-10: Nothing is purged, Not started

PR-10 (J5) has no FR yet.

### BR-11: Payment alone decides plan limits, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-11 | PR-15 | TR-29, TR-34, TR-35 | `ProjectCreationServiceTest`, `HttpPaymentAdmissionsTest` (closest) | Done | |
| FR-12 | PR-15 | TR-9, TR-20, TR-29, TR-37 | `AdmissionReleaseHandlerTest`, `ProjectCreationServiceTest` (closest) | Done | |

Drift comes from PR-1 (FR-9).

### BR-12: The databases match the Notion design exactly, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-36 | PR-24 | TR-14 to TR-18, TR-39 | 14 tagged tests; DB audit 0 DIFFERENT | Done | no schema change since `498a5f0` |

### BR-13: Sign-in is checked at the gateway, then by Project, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-1 | PR-23 | TR-22, TR-30, TR-34 | `JwtSecurityWebTest` (closest) | Done | CMN §4.1 503 not built (no FR) |
| FR-2 | PR-23 | TR-22, TR-30 | `JwtSecurityWebTest`, `CurrentUserTest` (closest) | Done | |
| FR-4 | PR-23 | TR-5, TR-22, TR-33 | `ProjectControllerTest`, `GlobalExceptionHandlerWebTest` (closest) | Done | |
| FR-5 | PR-23 | TR-19, TR-22, TR-35 | `RetryAfterWebTest` fr5; `ProjectDatabaseTimeoutsTest` (closest) | Done | FR-5.2 shown with a rename |
| FR-6 | PR-23 | TR-23, TR-31 | `JwtSecurityWebTest` (closest) | Done | |
| FR-7 | PR-23 | TR-29, TR-33 | `HttpPaymentAdmissionsTest` (closest) | Done | |

### BR-14 and BR-15: constraints, Done

No PR serves them; TR-34 (stand-ins only in `prototype`) and TR-7 (dependency rule) are Done.

### No PR

None: every FR has a PR.

### Cross-cutting

TR-1, TR-2, TR-3, TR-4, TR-6, TR-7, TR-8, TR-12 and TR-13 are Done (ArchUnit, `check`, hook tests); TR-2 rests on
ArchUnit `commonDependsOnNoFeature` and the review rule that another feature is used through its `application`
classes. TR-36 is Done; TR-38 is In progress (below).

### TRs not Done

| TR | FR | Status | Evidence |
| --- | --- | --- | --- |
| TR-38 | All FR | In progress | ports 8080, 9090 (`application.yml:92`), 8081 (`:72`); no image definition here; manifests in `web-block-config` |

## FRs with no test

31 of 37 FRs have every acceptance row tested, 26 rows by a tagged test. The table lists the 15 rows with neither a
tagged nor a closest test.

| FR | Acceptance rows without a test | Closest test, no ID yet |
| --- | --- | --- |
| FR-9 | FR-9.2 | none; the code answers `blank` (Drift) |
| FR-13 | FR-13.5 to FR-13.9, FR-13.11 | none (not built); FR-13.10 has `ProfileGuardTest.productionWithoutStandInsStarts` |
| FR-20 | FR-20.2 | none; `ProjectServiceTest.advanceEditRevisionMovesOnlyFromTheExpectedRevision` is the shared rule |
| FR-28 | FR-28.1, FR-28.2 | none (no code) |
| FR-34 | FR-34.1 | none (not due) |
| FR-37 | FR-37.1 to FR-37.4 | none (J4) |

92 rows are covered only by FR-level tags or closest tests.

## Drift

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| FR-9.2 | an unknown type code gives an empty list | gives `blank` (`"*"` applies to every code) | `SiteTemplate.java:15`, `site-templates/templates.json:7`; owner carries it (R-5) |
| FR-18 | S1: `max_pages` is a setting, now 4 | `webblock.pages.max-pages`, message without a number | none; closed |

Source disagreements with no FR behind them, listed in the BRD, PRD, FRD and TRD: CMN §4.1 `IDENTITY_UNAVAILABLE`,
S2 §5.1 gRPC `GetCodegenInput` (PR-22, Later), and S2 §4.4 with no Project queue for FR-37's event. The contract
pages name Gitea and gRPC `GetRepository` with no REST version (PROV §E, PR-18), as S1 and S2 do. FR-37 now matches
S1: nothing sets `suspended`. S1 projects allows 3 to 63 characters and names no `-preview` or `xn--` refusal;
FR-13 follows the owner's decision (FRD Q-2, Q-5) and S1 is read-only (Q-2 below).

## Orphans

| Work, code or test file | Where | Suggested home |
| --- | --- | --- |
| none | every commit in `main..HEAD` touching code cites an ID; every feature package serves an FR | |

## How I checked

- Commit `52f55f5`; code unchanged since `61c2e4b` (`a3be2b8` adds the task 8 review, `52f55f5` the FR-13 spec
  change). `AccessAction.java:21` holds `MANAGE_SITE` as a write action; `AccessActionListTest` compares the enum
  with `contracts/access/actions.v1.json`. Read BRD, PRD, FRD, TRD, `spec.json`, `ai/handoffs/next.md`,
  `ai/reviews/manage-site-task-8-review.md`, `Subdomain.java`, `PlatformProperties.java`, `ProfileGuard.java`,
  `application.yml`. Notion "2 · Project" read-only.
- `python3 -I scripts/spec-check.py`: 15 BR, 24 PR, 37 FR (133 acceptance rows), 39 TR; Errors 0; Must FRs with no
  listed test: 28. Markdown and `spec.json` hold the same IDs.
- Searches: `grep -rnE 'FR-[0-9]+|fr[0-9]+_' src/test/java`, the `@DisplayName` values of the four access test
  classes, `git log main..HEAD` (every code commit cites an ID).
- `./gradlew test --rerun` at `a3be2b8`, run by me: 848 tests, 0 failures, 0 errors, 0 skipped.
- `./gradlew integrationTest`: not run by me; the task 8 review reports 301 passed, 0 failed, 0 skipped at `e630c62`,
  and its re-review found it up to date at `61c2e4b`.
- Not run: the Kind check (needs an owner slot), `./gradlew check` as one task, `scripts/osv-scan.sh`.

## Questions for the owner

| ID | Question | Why it matters | Suggested answer |
| --- | --- | --- | --- |
| Q-1 | Add Project's queue and broker permission for `provisioning.publish_completed.v1` to Notion S2 §4.4 and §5.2? | FR-37 cannot be built without them (J4) | Suggestion: queue `project.publish_completed.v1` on `provisioning.events`, read only |
| Q-2 | Update Notion "2 · Project" `subdomain` to 3 to 55 characters, no `-preview` ending, no `xn--`, `preview` reserved? | Notion ranks first; until then FR-13 and S1 disagree | Suggestion: yes, the owner edits S1; the column stays `VARCHAR(63)` |

## Glossary

| Word | Meaning here |
| --- | --- |
| Closest test | A test with no FR ID that asserts what an acceptance row says |
| R-5, R-6 | Owner rulings named in the final reviews |
| Cross-cutting TR | A TR that serves every FR, counted on its own |
