# Plan: J3 Notion alignment (V6, gRPC CheckAccess, the 6.1 layout)

**Status:** Approved by the owner at gate 3.

Design: `.claude/docs/designs/j3-notion-alignment.md` (approved at gate 2). Decisions: `0010` to `0015`: `0010`
(Java layout of Notion 6.1), `0011` (gRPC CheckAccess and pinned contracts), `0012` (checks on internal tables),
`0013` (grants on internal tables), `0014` (catalog `websites.updated_at`), `0015` (catalog database PUBLIC
CONNECT). Branch `project-notion-alignment`. Requirements: FRD FR-13, FR-31 to FR-33, FR-36 (FR-10, FR-15, FR-30
as those FRs say); TRD TR-4, TR-5, TR-10, TR-11, TR-13, TR-14, TR-17, TR-24 to TR-27, TR-39.

## Global Constraints

- Art. I to Art. VIII of `.claude/docs/CONSTITUTION.md` (Version 2) bind every task.
- Gate sets, as the design names them: **fast** `./gradlew spotlessCheck test`; **full** `./gradlew check`; **QA**
  `./gradlew pmdMain pmdTest spotbugsMain cpd` with no new untriaged finding against the reports before the task;
  **OSV** `scripts/osv-scan.sh`; **spec** `python3 -I scripts/spec-check.py` with 0 errors; **contracts**
  `scripts/contracts-sync.sh --check`; **docs** the dash and line checks of `.claude/rules/docs-style.md`.
- Every code task writes its failing tests first, sees them fail, then writes the code, and ends with a
  `project-validator` review; at most 2 fix rounds.
- Owner rules from gate 2: V6 rolls out together with Provisioning's sub-domain routing and both are proved on Kind
  first (O-4); only `provisioning-api` may call CheckAccess for now (O-5); the owner runs the read-only collision
  count before the rollout (O-6); the mesh manifests live in `web-block-config` and the owner applies them (Q-5); no
  response-time targets until J5 (Q-4).
- Lead rule: the shared action list carries a boolean `write` per action next to `admin_allowed` (design G21).
- One implementer at a time in the shared tree. Commit named files only; never push or merge.

## Constitution check

| Article | Result | Note |
| --- | --- | --- |
| I Spec-driven flow | pass | Every task names FR or TR IDs; task 2 adds `scripts/` to the commit ID check (TR-13) |
| II Tech stack | pass | Task 5 adds grpc-java, protobuf and `protoc` with the pinned contracts, as Art. II allows; the owner approves the exact versions in task 5 |
| III Architecture limits | pass | Task 3 puts request, response and validation types in `application/` with `TYPE_FOLDERS` in the same commit (0010); task 6 adds two ArchUnit rules; task 4 makes the database equal S1 and runs the DB audit; nothing is invented for another service (task 7 is Project's own file in the shared repo) |
| IV Coding conventions | pass | Tasks 4, 6 and 8 follow `.claude/rules/java-code.md`: constructor injection, package-private wiring, `WebBlockException` for refusals |
| V Testing and quality | pass | Tests first with IDs in every code task; no gate lowered; stubs in their own source set, no coverage exclusion (task 5) |
| VI Safety guardrails | pass | No cluster command; mesh changes are notes for the owner (task 9); the hook edit in task 2 asks the owner; no secret |
| VII Docs | pass | Tasks 1 and 9 write current facts, plain English, docs checks |
| VIII Agents | pass | One implementer at a time; validator, security and DB reviews by other agents |

## Tasks in run order

### Task 1: Decision records 0010 and 0011
IDs: TR-4, TR-5, TR-10, TR-11, TR-24, TR-25, TR-26, TR-27
Parallel: no
Needs: none
Files: Create `.claude/docs/decisions/0010-java-layout-notion-6-1.md`,
`.claude/docs/decisions/0011-grpc-check-access-and-pinned-contracts.md`; Modify `.claude/docs/decisions/README.md`,
`.claude/CLAUDE.md` (record range)
Failing tests first: none (docs)
Steps: 1. Write both records as Accepted. 2. Index rows. 3. TRD Decision column cites them.
Gates: docs, spec
Commit: `Decisions: 0010 Java layout, 0011 gRPC server and contracts (TR-5, TR-10)`
Done in `ff86a9d`; the `write` flag follows in `4a02267`.

### Task 2: The commit ID check covers `scripts/`
IDs: TR-13 (FR-1 to FR-36 through TR-13)
Parallel: yes (hook files only; no Java, no build file)
Needs: none; the owner confirms the hook edit (the guard asks)
Files: Modify `.claude/hooks/guard.py` (`CODE_PATHS`); Test `.claude/hooks/test_hooks.py`
Failing tests first: `test_hooks.py` rows "a commit of `scripts/x.sh` with no ID asks" and "a commit of `scripts/x.sh`
with `(TR-13)` passes" (design G26)
Steps: 1. Add the two rows; see the first fail. 2. Add `"scripts/"` to `CODE_PATHS`; nothing else changes.
Gates: `python3 -I .claude/hooks/test_hooks.py`, `python3 -I .claude/hooks/test_guard.py`, spec
Commit: `Hooks: commit ID check covers scripts/ (TR-13)`

### Task 3: Request, response and validation types in `application/`
IDs: TR-4, TR-5, FR-4 (FR-4.1 to FR-4.3 keep passing)
Parallel: no (touches most features; must run before task 4)
Needs: none
Files: Modify `src/test/java/org/kshrd/webblock/project/architecture/ArchitectureRulesTest.java` (`TYPE_FOLDERS`:
presentation `controller`; application adds `request`, `response`, `validation`); move the 21 classes of design c.2
from `<feature>/presentation/{request,response,validation}` to `<feature>/application/{request,response,validation}`
and `common/presentation/validation` to `common/application/validation`; Modify every importer (controllers, tests);
Test moves: `wizard/presentation/request/BusinessProfileRequestTest` to `wizard/application/request/`;
`common/presentation/validation/ConstraintMessageTest`, `IdempotencyKeyValidatorTest`, `PagePathValidatorTest` to
`common/application/validation/`
Failing tests first: `ArchitectureRulesTest` `layerClassesSitInATypeFolder` with the new `TYPE_FOLDERS`, run before
the moves, fails on each old folder
Steps: 1. Change `TYPE_FOLDERS`; see it fail. 2. Move with `git mv`, package and imports only, no behaviour change
(G25). 3. No `@NamedInterface` on the new packages; no `mapper` folder (G24). 4. `ModuleStructureTest` passes unchanged.
Gates: full, QA, spec
Commit: `Layout: request, response and validation types in application (TR-5, FR-4)`

### Task 4: Migration V6 and the sub-domain at creation
IDs: FR-36, FR-36.1 to FR-36.8, FR-13, FR-13.1 to FR-13.4, FR-10, FR-15.1, TR-14, TR-17
Parallel: no
Needs: db audit
Files: Create `src/main/resources/db/migration/project/V6__subdomain_and_notion_alignment.sql` (with the
`new-migration` skill), `project/domain/model/Subdomain.java`; Modify `project/domain/entity/ProjectEntity`,
`project/infrastructure/persistence/ProjectRepository`, `project/application/config/PlatformProperties`,
`project/application/model/NewProject`, `ProjectRecord`, `ProjectView`, `project/application/service/ProjectService`,
`project/infrastructure/config/ProjectConfig`, `common/domain/error/ErrorCode`,
`wizard/application/request/CreateProjectRequest`, `wizard/application/model/CreateProjectCommand`,
`wizard/application/service/ProjectCreationService`, `src/main/resources/application.yml`; Delete
`project/domain/entity/DomainEntity`, `project/domain/model/DomainKind`, `DomainStatus`,
`project/infrastructure/persistence/DomainRepository`, `db/DomainsTest`, `db/DomainRulesTest` (main paths under
`src/main/java/org/kshrd/webblock/project/`, tests under `src/test/java/org/kshrd/webblock/project/`)
Failing tests first: `db/SubdomainMigrationTest` (new; V5 with rows by `target("5")`, then V6) "FR-36.1: old rows get
p- and 12 characters", "FR-36.2: subdomain update refused as project_migrator", "FR-36.3: a deleted project's
sub-domain refused for a new row", "FR-36.8: owner index has no deleted_at condition", "G2: shared 12-character prefix
stops V6, nothing changed"; `db/MigrationTest` "FR-36.4: repository columns, no github_repository_*", "FR-36.5: no
domains table or enums", "G1: projects columns in S1 order"; `db/GrantsTest` "FR-36.6: project_app may not update
subdomain or business_type_id" and exact privileges; `ProjectServiceTest` and `ProjectCreationServiceTest`
"FR-13.1" to "FR-13.4" and the race (G8); `ProjectCreationControllerTest` "FR-13.3: auth, -shop, ab answer
VALUE_INVALID" and a missing `subdomain` answering `FIELD_REQUIRED`; `ProjectCreationIntegrationTest` "FR-13.1" and
"FR-13.2: Payment not asked"; `ProjectControllerTest` "FR-15.1: subdomain in the read"; `project/domain/model/
SubdomainTest` (label shape, reserved set); `PlatformPropertiesTest` (each reserved entry passes the label shape);
`J1ErrorCodesTest`, `ProjectConfigTest` (`DOMAIN_TERMINAL` gone, `projects_subdomain_key` mapped to
`HOSTNAME_TAKEN`); `db/ProjectConstraintsTest`, `db/Rows`, `db/ProjectsAndPagesTest` take the new column names
Steps: 1. Tests first; see them fail. 2. V6 in the 12 steps of design a.1 (lock, pre-check with SQLSTATE `23505`,
drops, rebuild in S1 order, copy, guard trigger with `projects_subdomain_immutable`, owner index, foreign keys,
grants). 3. Code of design a.2 in one commit, since V6 makes `subdomain` required at insert. 4. Creation order of
G8. 5. The `db-notion-audit` skill reports 0 DIFFERENT (FR-36.7), report in `ai/reviews/`. 6. The DB change report
outside the repo: `/home/roa/Documents/web-block/platform-db-rev25-project-V6-report.md` (Changed, Not changed, UI
impact).
Gates: full, QA, spec; then the DB audit and the change report
Commit: `Projects: migration V6 and the sub-domain at creation (FR-36, FR-13)`

### Task 5: gRPC dependencies and the pinned contracts
IDs: TR-10, TR-11, FR-31
Parallel: no (build files; task 6 needs it)
Needs: security (dependencies); the owner approves the exact versions first
Files: Create `contracts.lock` (`afff9245444c628b74f88d281939f4bc8b4de3e2`), `scripts/contracts-sync.sh` (the shape of
`web-block-commons/scripts/contracts-sync.sh`), `contracts/proto/webblock/project/v1/access.proto` (written only by the
script); Modify `build.gradle` (protobuf plugin, `contracts` source set on the main class path, boot jar), `gradle/
libs.versions.toml`, `gradle.lockfile`, `gradle/verification-metadata.xml`, `.claude/docs/dependency-versions.md`
Failing tests first: `scripts/contracts-sync.sh --check` fails before the sync; a build check that the boot jar holds
`webblock/project/v1/AccessServiceGrpc.class` fails before the source set exists
Steps: 1. Pick one grpc-java version for `grpc-api`, `grpc-stub`, `grpc-protobuf`, `grpc-netty-shaded`,
`grpc-inprocess` (test), plus `protobuf-java`, `proto-google-common-protos`, the `com.google.protobuf` plugin,
`protoc`, `protoc-gen-grpc-java`: open source, at least 14 days old, the ones `web-block-commons` pins; a BOM-managed
version stays the BOM's unless OSV reports an advisory. Ask the owner to approve the list. 2. Script, lock, sync.
3. Source set; if the stubs need `javax.annotation.Generated`, use the plugin option that omits it, else stop and ask.
JaCoCo, Spotless, PMD and CPD stay on `main` (G12). 4. `./gradlew resolveAndLockAll --write-locks`, then
`./gradlew --write-verification-metadata sha256 check`. 5. An Error Prone error in generated code: stop and ask.
Gates: full, OSV, contracts, boot jar content (`unzip -l build/libs/*.jar`), spec; security review
Commit: `Build: gRPC dependencies and the pinned contracts (TR-10, TR-11, FR-31)`

### Task 6: gRPC CheckAccess on port 9090
IDs: FR-31, FR-31.1 to FR-31.6, FR-33, FR-33.1, FR-33.2, FR-32.3, TR-4, TR-24, TR-25, TR-26
Parallel: no
Needs: security (a new port with no token, error answers)
Files: Create `common/application/config/GrpcProperties`, `common/presentation/grpc/GrpcServer`,
`GrpcRequestIdInterceptor`, `GrpcErrorInterceptor`, `GrpcErrors`, `access/presentation/grpc/AccessGrpcService`,
`AccessGrpcMapper`; Modify `access/application/model/ProvisioningAction` renamed `AccessAction` (no `MANAGE_SITE` yet)
and its users, `common/domain/error/ErrorCode` (`PROJECT_DELETED`, `PROJECT_SUSPENDED`, category `CONFLICT`),
`src/main/resources/application.yml` (`webblock.grpc.port`, `webblock.grpc.shutdown-grace`), `ArchitectureRulesTest`
(presentation `grpc` folder and the two G22 rules)
Failing tests first: `access/presentation/grpc/AccessGrpcServiceTest` (in-process) "FR-31.1" to "FR-31.5", "FR-33.1:
admin D gets NOT_MANAGER", "FR-32.3: drop_db answers INVALID_ARGUMENT"; `access/presentation/grpc/
CheckAccessIntegrationTest` (Netty on a free port, PostgreSQL) "FR-31.6: table lock answers UNAVAILABLE,
DEPENDENCY_UNAVAILABLE"; `common/presentation/grpc/GrpcErrorsTest` (every `ErrorCode` has its status, G16);
`common/presentation/grpc/GrpcServerTest` (starts on the port, serves every service bean, stops within the grace, a
65 KiB request answers `RESOURCE_EXHAUSTED`, G13, G15); `common/domain/error/J3ErrorCodesTest` "FR-33.2: both codes
exist with status 409"; `ArchitectureRulesTest` G22 rules
Steps: 1. Tests first; see them fail. 2. Server and interceptors of design b.2, status map of b.2 as a `switch` over
the enum. 3. Mapper (empty strings read as missing, snake_case field names) and service of b.3, same
`AccessCheckService` (G17); only the `user_id` field is the subject (G18). 4. `PROJECT_DELETED` and
`PROJECT_SUSPENDED` come back as `reason` in an OK answer (G19).
Gates: full, QA, spec; security review
Commit: `Access: gRPC CheckAccess on port 9090 (FR-31, FR-33)`

### Task 7: The shared action list in `web-block-contracts`
IDs: FR-32, FR-32.1 to FR-32.3, TR-27
Parallel: yes (another repository; shares no file with tasks 4 to 6)
Needs: none; the owner pushes `web-block-contracts`
Files (in `web-block-contracts`): Create `access/actions.v1.json`, `access/actions.v1.schema.json` (design b.4: each
action has `name`, `write`, `admin_allowed`; `write` true for the five change actions, false for the get and list
actions); Modify `README.md` (owner row `access/*`: project)
Failing tests first: the schema check of the example file against the schema, run before the files are final
Steps: 1. Write both files as design b.4 shows, header DRAFT. 2. Validate the example. 3. Commit; the owner pushes and
gives the new commit SHA.
Gates: schema check of the example
Commit: `Access: CheckAccess action list v1, DRAFT (FR-32)`

### Task 8: `manage_site` and the shared action list
IDs: FR-32, FR-32.1, FR-32.2, FR-32.3, FR-30, TR-11, TR-27
Parallel: no (waits for task 6 and the owner's push of task 7)
Needs: none
Files: Modify `access/application/model/AccessAction` (`MANAGE_SITE`, `manage_site`, a write action),
`contracts.lock` (task 7's commit), `scripts/contracts-sync.sh` (sync list adds both files); Create
`access/application/model/AccessActionListTest`; Test `AccessCheckServiceTest`, `AccessCheckControllerTest`,
`AccessGrpcServiceTest`
Failing tests first: `AccessActionListTest` "FR-32.1: the enum equals actions.v1.json, names and write flags" and
"the file passes its schema" (G20); `AccessCheckServiceTest`, `AccessCheckControllerTest` and `AccessGrpcServiceTest`
"FR-32.1: manage_site allowed for the owner", "FR-32.2: manage_site on a deleted project is PROJECT_DELETED",
"FR-32.3: drop_db is bad input"
Steps: 1. Move the lock, sync. 2. Tests first; see them fail. 3. Add `MANAGE_SITE` with its `write` flag.
Gates: full, contracts, spec
Commit: `Access: manage_site and the shared action list (FR-32)`

### Task 9: Docs of the J3 alignment
IDs: FR-36, FR-31, TR-5, TR-39
Parallel: no (last)
Needs: none
Files: Modify `.claude/CLAUDE.md` (structure, current facts, port 9090, sync command), `.claude/rules/java-code.md`
(folders, gRPC), `.claude/rules/migrations.md` and the `new-migration` skill (the planned list is applied; last file
V6), the `spring-endpoint` skill, `.claude/docs/runbooks/database.md` (columns, grants),
`.claude/docs/runbooks/migrations.md` (stop the old version before V6, TR-39; the owner's collision count, O-6),
`.claude/TECH_STACK.md`, `.claude/docs/contracts/provisioning-contract.md` §B (gRPC served, same meaning),
`ai/handoffs/next.md`; mesh notes for the owner (design b.6) in the handoff
Failing tests first: none (docs)
Steps: 1. docs-keeper writes current facts only. 2. Mesh notes as commands for the owner, nothing applied.
Gates: docs, spec
Commit: `Docs: J3 alignment facts (FR-36, FR-31, TR-5)`

After task 9: the Kind check in an owner-given slot (V6 with Provisioning's sub-domain routing, and the REST check),
QA sweep, direction check, final review, gate 4.

## Coverage

| ID | Tasks |
| --- | --- |
| FR-4, FR-4.1 to FR-4.3 (unchanged, kept passing) | 3 |
| FR-10 (create answers carry `subdomain`) | 4 |
| FR-13, FR-13.1, FR-13.2, FR-13.3, FR-13.4 | 4 |
| FR-15.1 (read carries `subdomain`) | 4 |
| FR-30 (`manage_site` on the REST check) | 8 |
| FR-31, FR-31.1 to FR-31.5 | 6 |
| FR-31.6 | 6 |
| FR-32, FR-32.1, FR-32.2 | 7, 8 |
| FR-32.3 | 6, 8 |
| FR-33, FR-33.1, FR-33.2 | 6 |
| FR-34 | not covered: waits until the owner confirms Provisioning calls FR-31 everywhere (design "IDs covered") |
| FR-36, FR-36.1 to FR-36.6, FR-36.8 | 4 |
| FR-36.7 | 4 (DB audit) |
| TR-4 | 3, 6 |
| TR-5 | 3 |
| TR-10 | 5 |
| TR-11 | 5, 8 |
| TR-13 | 2 |
| TR-14 | 4 |
| TR-17 | 4 |
| TR-24 | 6 |
| TR-25 | 6 |
| TR-26 | 6 |
| TR-27 | 7, 8 |
| TR-39 | 9 (runbook), 4 (V6 makes the old version unable to run) |

## Pre-flight

| Pair | Produces and consumes | Finding |
| --- | --- | --- |
| 3 then 4 | 3 moves `CreateProjectRequest` to `wizard/application/request`; 4 adds `subdomain` to it | Run in order; 4 edits the new path |
| 3 then 6 | 3 sets `TYPE_FOLDERS`; 6 adds presentation `grpc` and the G22 rules to the same test | Run in order |
| 4 and 6 | Both change `ErrorCode` and `application.yml` (4: `DOMAIN_TERMINAL` out, reserved sub-domains; 6: two codes, gRPC port) | Never at once; 4 first |
| 5 then 6 | 5 makes the generated `AccessServiceImplBase`; 6 extends it | 6 cannot compile before 5 |
| 6 then 8 | 6 renames `ProvisioningAction` to `AccessAction`; 8 adds `MANAGE_SITE` | Run in order |
| 7 then 8 | 7's commit SHA (owner push); 8 moves `contracts.lock` to it | 8 waits for the push |
| 5 and 8 | Both write `contracts.lock` and `scripts/contracts-sync.sh` | 5 first; 8 only moves the SHA and the list |
| 2 and 5 | 2 makes `scripts/` count as code; 5 creates `scripts/contracts-sync.sh` | 2 first, so 5's commit is checked; 5 cites TR-10 and TR-11 anyway |
| 9 and all | 9 describes what 3 to 8 built | Last |

## Open questions

None for this plan. Carried from the design: FR-34 waits for the owner (Provisioning on FR-31 everywhere); the
commit ID check for `contracts/` and `contracts.lock` is a later small change (O-8).
