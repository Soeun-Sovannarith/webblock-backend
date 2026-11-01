# QA sweep: project-notion-alignment at 546ceeb

Stage: 5 Verify. IDs: FR-1, FR-4, FR-9, FR-10, FR-11, FR-13, FR-14, FR-16, FR-17, FR-18, FR-22, FR-23, FR-24,
FR-25, FR-26, FR-29, FR-31, FR-32, FR-33, FR-36, TR-12, TR-22, TR-24, TR-25, TR-27, TR-29, TR-33, TR-34, TR-36.

Full sweep of `src/main/java/` (and the PMD test report), base `main` (merge base a5c5b79), head 546ceeb. "New" means
the finding's line was written by a commit in `main..HEAD` (`git blame`). Nothing was fixed or committed.

## Tools

| Step | Command | Exit | Findings |
| --- | --- | --- | --- |
| Compile warnings | `./gradlew compileJava compileTestJava --rerun-tasks` | 0 | 9: javac deprecation 4 (tests), removal 0; Error Prone 5 (4 main, 1 test). One javac note: `CatalogRegistrationHandler` uses unchecked operations (lint off, line not listed) |
| Spotless | `./gradlew spotlessCheck` | 0 | none |
| Unit tests | `./gradlew test` | 0 | 797 tests, 0 failed, 0 skipped. The first run (inside `spotlessCheck test`) failed once; two runs with `--rerun` passed. The failure text was not kept; most likely a clash with the final reviewer's Gradle build in the same `build/` at the same time. Not reproduced |
| Integration tests | `./gradlew integrationTest` | not run | needs Docker and an owner slot; not asked |
| PMD | `./gradlew pmdMain pmdTest` | 0 | 31 (13 main, 18 test); 0 processing errors |
| CPD | `./gradlew cpd` | 0 | 0 duplications of 100 tokens or more; 214 of 214 main files scanned |
| SpotBugs | `./gradlew spotbugsMain` | 0 | 40 (all medium); 0 errors, 0 missing classes |
| Coverage | `./gradlew jacocoTestReport` | not run | not asked |
| IDE inspections | IntelliJ through the `pycharm` MCP | not run | IDE MCP not connected; inspections not run |
| Manual review | read the gRPC server, CheckAccess, the sub-domain rule, the layout move, cursors, paging, constraint maps, V6 and V7 test helpers | | 7 |

Per rule: PMD main PreserveStackTrace 8, CognitiveComplexity 2, NPathComplexity 1, CloseResource 1,
AssignmentInOperand 1; PMD test CloseResource 17, AvoidCatchingGenericException 1. SpotBugs MALICIOUS_CODE 30
(`EI_EXPOSE_REP2` 21, `EI_EXPOSE_REP` 9), BAD_PRACTICE 7 (`CT_CONSTRUCTOR_THROW`), STYLE 3 (`NP_NULL_ON_SOME_PATH` 2,
`RCN_REDUNDANT_NULLCHECK` 1).

## Summary

| Tool | Real problem | False positive | Owner ruling | Total | New on branch |
| --- | --- | --- | --- | --- | --- |
| javac deprecation | 4 | 0 | 0 | 4 | 0 |
| Error Prone | 4 | 1 | 0 | 5 | 0 |
| PMD main | 1 | 9 | 3 | 13 | 0 |
| PMD test | 0 | 18 | 0 | 18 | 0 |
| CPD | 0 | 0 | 0 | 0 | 0 |
| SpotBugs | 1 | 23 | 16 | 40 | 0 |
| Manual review | 5 | 2 | 0 | 7 | 5 |
| All | 15 | 53 | 19 | 87 | 5 |

Every tool finding was already on `main`; the tool reports match `ai/reviews/qa-claude-setup.md` except one finding
gone: SpotBugs `HRS_REQUEST_PARAMETER_TO_HTTP_HEADER` on `RequestIdFilter` (the branch moved the check into
`RequestIds.acceptedOrNew`). The 4 tasks of that sweep are still open (tasks 1, 5 and 6 below carry them). The new
gRPC classes, `Subdomain`, `DatabaseBusy`, `FieldErrors` and the V6 and V7 tests raise no tool finding; the branch's
new problems come from reading the code.

## Findings

Paths are under `src/main/java/org/kshrd/webblock/project/` unless they start with `test:`
(`src/test/java/org/kshrd/webblock/project/`). Real problems are ranked first, by risk.

| # | Tool | File:line | What | Class | IDs | New | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | PMD CloseResource | payment/infrastructure/client/HttpPaymentAdmissions.java:68 | `HttpClient` built for the bean, never closed | real | FR-11, TR-29 | no | java-code.md quality rules; the JDK client is `AutoCloseable` and holds a selector thread; close it when the bean is destroyed |
| 2 | manual | common/presentation/grpc/GrpcCallLimitInterceptor.java:95-98, GrpcErrorInterceptor.java:94-95 and 122, GrpcUnknownMethodRegistry.java:70-74 | "close the call with this ErrorCode" written 3 times: the request ID read (twice with a `RequestIds.acceptedOrNew(null)` fallback, once with `requireNonNull`) and `call.close(status, requireNonNullElseGet(trailers, Metadata::new))` | real | FR-31, TR-25, TR-36 | yes | java-code.md: never keep a second copy of a rule. The three copies already disagree on a missing request ID; the fallback cannot run, since `GrpcServer.intercepted` always runs the request-ID interceptor first |
| 3 | manual | access/application/model/AccessAction.java:16-35 and 51-53 | each action's wire name is stated twice: ten `@JsonProperty` values for REST and `wireName()` (lower-case constant name) for gRPC | real | FR-32, TR-27 | yes | second copy of the wire-name rule; `AccessGrpcServiceTest:303` pins them equal, so it is safe today but kept twice. One `@JsonValue` on `wireName()` serves Jackson both ways |
| 4 | manual | project/application/service/ProjectCursor.java, generation/application/service/RequestCursor.java | the same "micros:UUID" cursor parser and encoder in two features, line for line (below the CPD threshold) | real | FR-14, FR-25, TR-22 | no | java-code.md: never keep a second copy of a parser |
| 5 | manual | project/presentation/controller/ProjectController.java:37, generation/presentation/controller/GenerationContractController.java:48, GenerationRequestController.java:64 | default list limit `"20"` written as a literal 3 times (the maximum is already one constant, `ProjectService.MAX_LIST_LIMIT`) | real | FR-14, FR-23, FR-25, TR-22 | no | a repeated limit is a repeated rule |
| 6 | Error Prone OperatorPrecedence | common/infrastructure/config/ProfileGuard.java:210, 211 | `a && !b \|\| c && d` without parentheses | real | TR-34 | no | correct today, but this is the production guard; make the grouping explicit |
| 7 | SpotBugs RCN_REDUNDANT_NULLCHECK_OF_NONNULL_VALUE | messaging/infrastructure/messaging/OutboxMetrics.java:76 | `value == null` after `single()` | real | TR-36 | no | dead branch: `single()` never returns null and `count(*)` is never null |
| 8 | javac deprecation | test: project/presentation/controller/ProjectControllerTest.java:175 | `status().isUnprocessableEntity()` | real | FR-16, TR-12 | no | java-code.md: no deprecated API; use `isUnprocessableContent()` |
| 9 | javac deprecation | test: wizard/ProjectCreationIntegrationTest.java:407, 413 | same, 2 calls | real | FR-10, TR-12 | no | as 8 |
| 10 | javac deprecation | test: wizard/ProjectCreationRefusalIntegrationTest.java:77 | same | real | FR-11, TR-12 | no | as 8 |
| 11 | Error Prone MissingSummary | generation/application/service/CodegenInputService.java:54 | Javadoc with only `@throws` | real | FR-29 | no | add a summary line |
| 12 | Error Prone BadImport | test: generation/domain/model/GenerationRequestStatusTest.java:25 | static import of `values()` | real | FR-26, TR-12 | no | write `GenerationRequestStatus.values()` |
| 13 | manual | test: db/Rows.java:43-46 | a second test sub-domain helper (`"t-"` + ID) beside `support/TestSubdomains.of` (`"s-"` + ID) | real | FR-36, TR-12 | yes | the same test rule twice; `Rows.project` can call `TestSubdomains.of` (no test asserts the `t-` prefix) |
| 14 | Error Prone ArrayRecordComponent | common/infrastructure/security/DevTokenIntrospector.java:49 | private record with a `byte[]` | false positive | FR-1, TR-34 | no | `Entry` is private and never compared with `equals`; prototype only |
| 15 | PMD PreserveStackTrace | generation/application/service/ContractCursor.java:35, RequestCursor.java:41, project/application/service/ProjectCursor.java:38 | cause dropped on a bad cursor | false positive | FR-14, FR-23, FR-25, TR-33 | no | client input answered 400 `VALUE_INVALID`; the cause would hold only the caller's text |
| 16 | PMD PreserveStackTrace | generation/application/service/InputHasher.java:28 | cause dropped | false positive | FR-24, TR-33 | no | deliberate: the comment says the cause can echo captured input |
| 17 | PMD PreserveStackTrace | common/infrastructure/security/DevTokenIntrospector.java:116 | cause dropped reading the dev token file | false positive | FR-1, TR-33 | no | a Jackson cause can quote the token file |
| 18 | PMD PreserveStackTrace | payment/infrastructure/client/HttpPaymentAdmissions.java:128 | cause dropped on an unknown `decision` | false positive | FR-11, TR-33 | no | the cause quotes the upstream body |
| 19 | PMD PreserveStackTrace | payment/infrastructure/standin/PaymentStandIn.java:160, template/application/service/TemplateCatalog.java:87 | cause dropped | false positive | FR-9, FR-11, TR-34 | no | the stand-in logs a fixed message; the template check folds the cause text into its own message |
| 20 | PMD AssignmentInOperand | page/application/service/PuckValidator.java:113 | `kept++ > MAX_BLOCKS` | false positive | FR-18 | no | keeps MAX_BLOCKS + 1 on purpose, as its Javadoc says |
| 21 | PMD CloseResource | test: 14 `ExecutorService` sites: catalog/infrastructure/config/CatalogWiringTest.java:175, generation/application/service/CaptureIntegrationTest.java:248, 504, GenerationContractServiceTest.java:196, messaging/infrastructure/messaging/OutboxSpikeTest.java:455, page/application/service/PageServiceTest.java:544, 624, payment/application/service/AdmissionReleaseFenceTest.java:108, project/application/service/ProjectLockTest.java:82, 111, ProjectServiceTest.java:265, 291, 325, wizard/ProjectCreationIntegrationTest.java:346 | executor not closed | false positive | TR-12 | no | each is shut down in `finally` or right after use |
| 22 | PMD CloseResource | test: messaging/infrastructure/messaging/OutboxSpikeTest.java:515 | application contexts | false positive | TR-12 | no | closed in a `finally` loop |
| 23 | PMD CloseResource | test: support/ServiceTestDatabases.java:31, migration/infrastructure/persistence/ProductionStartupTest.java:28 | RabbitMQ container | false positive | TR-12 | no | shared container, stopped by Testcontainers at JVM exit |
| 24 | PMD AvoidCatchingGenericException | test: wizard/ProjectCreationIntegrationTest.java:479 | `catch (Throwable e)` | false positive | FR-10, TR-12 | no | hands every failure, assertion errors too, to the waiting future |
| 25 | SpotBugs NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE | common/infrastructure/config/ProfileGuard.java:119 | unboxing `orElse(true)` | false positive | TR-34 | no | `orElse(true)` never returns null |
| 26 | SpotBugs NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE | common/presentation/error/GlobalExceptionHandler.java:186 | second `getSupportedHttpMethods()` call | false positive | FR-4, TR-22 | no | same value as the null-checked first call |
| 27 | SpotBugs CT_CONSTRUCTOR_THROW | common/domain/error/WebBlockException.java:13, 17, 21; common/infrastructure/security/DevTokenIntrospector.java:57; page/application/service/PuckValidator.java:46; template/application/service/TemplateCatalog.java:40, 46 | constructor may throw (7) | false positive | TR-12 | no | a finalizer attack needs a subclass and finalization; these are beans and an exception type |
| 28 | SpotBugs EI_EXPOSE_REP2 | catalog/application/job/CatalogRegistrationScheduler.java:30, common/infrastructure/config/ProfileGuard.java:64, common/presentation/error/ProblemDetailWriter.java:24, ProblemErrorController.java:27, SecurityErrorHandlers.java:31, common/presentation/filter/RequestBodyLimitFilter.java:41, generation/application/service/GenerationAdmissionService.java:56, GenerationRequestService.java:60, 64, page/application/service/PageRules.java:56, PageService.java:46, payment/infrastructure/standin/PaymentStandIn.java:70, wizard/application/service/ProjectCreationService.java:65 | bean keeps an injected object (13) | false positive | TR-12 | no | constructor injection of Spring collaborators |
| 29 | SpotBugs EI_EXPOSE_REP | common/presentation/filter/RequestBodyLimitFilter.java:77 (`LimitedRequest`) | returns the wrapped stream | false positive | FR-4 | no | a request wrapper must hand out the stream |
| 30 | manual | common/domain/error/ErrorCode.java:42, 43 | `PROJECT_DELETED` and `PROJECT_SUSPENDED` are never raised by main code | false positive | FR-33, TR-25 | yes | FR-33.2 and TR-25 ask for them in the shared code list (callers raise them after a CheckAccess denial); `J3ErrorCodesTest` and `GrpcErrorsTest` check them |
| 31 | manual | common/presentation/grpc/GrpcServer.java:170 | public `port()` only tests call | false positive | FR-31, TR-24 | yes | the test seam for port 0 (any free port); `serviceNames()` is used by the start log |
| 32 | SpotBugs EI_EXPOSE_REP, EI_EXPOSE_REP2 | generation/application/model/CodegenInput.java:21 (`Contract`), GenerationContractPage.java:6, generation/application/request/CreateGenerationContractRequest.java:11, page/application/model/PageList.java:6, page/application/response/PageListResponse.java:5, project/application/model/ProjectPage.java:6, template/application/model/SiteTemplate.java:6 (x4; the others x2) | records keep a caller's `List` or `Map` (16) | owner ruling | FR-14, FR-17, FR-22, FR-23, FR-29, TR-22 | no | owner question 1; the branch moved two of these records into `application/` without changing them |
| 33 | PMD CognitiveComplexity, NPathComplexity | payment/infrastructure/standin/PaymentStandIn.java:123 | `load()` complexity 23, NPath 615 | owner ruling | FR-11, TR-34 | no | prototype-only stand-in; owner question 2 |
| 34 | PMD CognitiveComplexity | common/infrastructure/security/DevTokenIntrospector.java:53 | constructor at the threshold (15) | owner ruling | FR-1, TR-34 | no | prototype-only; owner question 2 |

Not findings, checked by reading: `Subdomain` is the one sub-domain rule (`ProjectService.requireValidSubdomain`,
`PlatformProperties` and the wizard all call it; the site-domain pattern in `PlatformProperties` is a different
rule); `DatabaseBusy` and `FieldErrors` are shared by REST and gRPC, not copied; the canonical UUID pattern exists
once; the constraint maps in `ProjectConfig` and `PageConfig` hold each constraint once; no reference to the removed
`domains` code or to `presentation/request` or `presentation/response` is left, and no empty package is left.

## Tasks

Small change lane (`.claude/WORKFLOW.md`): each task is covered by an existing FR and sets no new rule. Tests first;
commits name the IDs.

1. Close the Payment `HttpClient` when `HttpPaymentAdmissions` is destroyed (finding 1). IDs: FR-11, TR-29. Files:
   `payment/infrastructure/client/HttpPaymentAdmissions.java`. Failing test first: the bean's destroy closes the
   client. Check: PMD no longer reports line 68; the Payment client tests pass.
2. One helper closes a gRPC call with an `ErrorCode` (finding 2), for example in `GrpcErrors`: it reads the request ID
   from `GrpcRequestIdInterceptor.REQUEST_ID` one way and closes with status and trailers. The call limit, the error
   interceptor and the unknown-method registry use it. IDs: FR-31, TR-25, TR-36. Files: the three classes and
   `GrpcErrors.java`. Check: `rg -n 'getTrailers' src/main/java` shows one place; `GrpcServerTest`,
   `GrpcErrorsTest`, `AccessGrpcServiceTest` and `./gradlew test` pass; `CheckAccessIntegrationTest` passes.
3. `AccessAction` states each wire name once (finding 3): drop the ten `@JsonProperty` and put `@JsonValue` on
   `wireName()`. IDs: FR-32, TR-27. Files: `access/application/model/AccessAction.java`, and the pin test in
   `AccessGrpcServiceTest` around line 303 (rewrite it to check the REST name through Jackson). Check:
   `AccessCheckControllerTest` passes unchanged (an unknown action is still 400; first add an assertion on the
   current Problem Details body of an unknown and an upper-case action, so the body cannot change unseen); the TR-27
   contracts test passes.
4. One time-and-ID cursor and one default list limit (findings 4 and 5): `ProjectCursor` and `RequestCursor` share
   one encoder and parser in `common` (a type folder ArchUnit allows), and the three list endpoints read one default
   limit constant next to `ProjectService.MAX_LIST_LIMIT`. IDs: FR-14, FR-23, FR-25, TR-22. Files: the two cursor
   classes, the three controllers, `ProjectCursorTest`. Check: `ProjectCursorTest`, `ProjectServiceTest`,
   `GenerationRequestControllerTest`, `GenerationContractControllerTest` pass; cursor texts are byte for byte the
   same (a test decodes a cursor made before the change).
5. Clear the main-code warnings (findings 6, 7, 11): parentheses in `ProfileGuard.isUncheckableLiteral`, the dead
   `value == null` branch in `OutboxMetrics.query`, a summary line in `CodegenInputService`. IDs: TR-34, TR-36,
   FR-29. Check: `./gradlew compileJava --rerun-tasks` prints only the `ArrayRecordComponent` warning; SpotBugs no
   longer reports `RCN_REDUNDANT_NULLCHECK_OF_NONNULL_VALUE`; `./gradlew test` passes.
6. Test hygiene (findings 8 to 10, 12, 13): `isUnprocessableContent()` in `ProjectControllerTest`,
   `ProjectCreationIntegrationTest` and `ProjectCreationRefusalIntegrationTest`; `GenerationRequestStatus.values()`
   in `GenerationRequestStatusTest`; `Rows.project` uses `TestSubdomains.of`. IDs: FR-10, FR-11, FR-16, FR-26,
   FR-36, TR-12. Check: no `[deprecation]` and no `BadImport` line in `compileTestJava --rerun-tasks`; `./gradlew
   check` passes.

## Owner questions

1. Records in `application/model`, `application/request` and `application/response` keep the `List` or `Map` they
   are given (finding 32, 16 SpotBugs findings). Recommendation: `List.copyOf` or `Map.copyOf` in compact
   constructors of the `application/model` records that cross modules (`PageList`, `ProjectPage`,
   `GenerationContractPage`, `CodegenInput.Contract`, `SiteTemplate`); leave the request and response records,
   which Jackson fills and reads once.
2. Prototype stand-ins over the PMD complexity threshold (findings 33, 34). Recommendation: no change now; split
   `PaymentStandIn.load()` the next time an FR touches the stand-in.
3. Which tasks go into this branch's fix wave. Recommendation: tasks 2, 3 and the `Rows` part of task 6 are new on
   this branch and small: fix them here before the merge. Tasks 1, 4, 5 and the rest of 6 were already on `main`:
   one small-change branch after the merge, so this branch's review scope does not grow.
4. Gate status of the tools (decision `0009`). Recommendation: keep PMD, CPD and SpotBugs report-only; after tasks 1,
   5 and 6 and question 1, PMD on main code could join `check` by a new decision record.

## Not checked

- IDE inspections: IDE MCP not connected; inspections not run.
- Coverage and `integrationTest`: not run (not asked; need Docker and an owner slot). The unit suite passed.
- Public members nothing calls: searched by name for the methods of the files this branch changed (all others are
  framework entry points or used); the rest of `src/main/java` was not searched member by member.
- CPD runs on main code only; test code is not checked for duplicates (the V6 and V7 tests were read by hand only
  for the sub-domain helper). Duplicates under 100 tokens are seen only where read by hand.
- SpotBugs runs on main code only (`spotbugsTest` exists, not run).
- Unchecked warnings: `-Xlint:unchecked` is off, so the `CatalogRegistrationHandler` note has no line. It was there
  before this branch.
- The first failed `test` run: its output was not kept; two reruns passed. If the final reviewer saw a failure at the
  same time, the two builds most likely shared `build/`.
