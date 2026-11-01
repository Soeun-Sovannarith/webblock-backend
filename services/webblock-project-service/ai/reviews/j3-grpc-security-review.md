# Security review: gRPC CheckAccess surface (J3 tasks 5 and 6)

Stage 5. Branch `project-notion-alignment`, commit range `498a5f0..c9499e9` (f09ed69, 642cd4e, c9499e9). The branch
head moved to `517d030` during the review; the four later commits touch only docs, handoff and reviews (no file under
`src`, `build.gradle`, `gradle/`, `scripts/`, `contracts/`). Reviewer model: Claude Opus 5.5 (security-reviewer).
IDs in scope: FR-31, FR-31.1 to FR-31.6, FR-32.3, FR-33, FR-33.1, FR-33.2, TR-4, TR-10, TR-11, TR-24, TR-25, TR-26,
TR-27; decision 0011; design `.claude/docs/designs/j3-notion-alignment.md` section b.

## Method

Skills: `differential-review` (diff, blast radius, tests against the diff), `sharp-edges` (lang-java,
auth-patterns, config-patterns), `project-gates` (commands below). `supply-chain-risk-auditor` was not used; the
dependency checks below were done by hand.

Read: the full diff of the three commits; design b.1 to b.7; FRD FR-30 to FR-34 and acceptance rows; TRD TR-10,
TR-11, TR-24 to TR-27; decisions 0002, 0007, 0011; `authentication-contract.md`; `provisioning-contract.md` section B;
the constitution; the plan's tasks 5 and 6; the docs-keeper's draft `.claude/docs/runbooks/grpc.md` (for the mesh
notes only). Blast radius: `AccessCheckService` has two callers (REST controller, gRPC service); `GrpcServer` picks
up every `BindableService` bean in the context (one today, `AccessGrpcService`).

| Command | Result | Notes |
| --- | --- | --- |
| `./gradlew spotlessCheck compileJava compileTestJava` | passed | Error Prone on main and test |
| `./gradlew test` | passed 788, failed 0, skipped 0 | AccessGrpcServiceTest 26, GrpcServerTest 16, GrpcErrorsTest 50, GrpcRuleTest 3, GrpcPropertiesTest 3, ArchitectureRulesTest and MethodSecurityRuleTest green |
| `./gradlew integrationTest --tests '*CheckAccessIntegrationTest'` | passed 2, failed 0 | FR-31.1 over Netty, FR-31.6 lock: log shows only `CannotAcquireLockException`, no SQL text |
| `./gradlew integrationTest` (whole suite) | not run | no database or broker change in the range; one class run instead |
| `scripts/osv-scan.sh` | exit 0 | 308 packages, "No issues found" |
| `scripts/contracts-sync.sh --check` | exit 0 | `contracts/` matches `afff9245444c628b74f88d281939f4bc8b4de3e2` |
| `python3 -I scripts/spec-check.py` | 0 errors | report only; Must FRs with no test: 29 |
| sha256 of cached jars against `verification-metadata.xml`, sha1 against Maven Central | 11 of 11 match | protoc 4.35.1 and protoc-gen-grpc-java 1.83.1 linux-x86_64 binaries, grpc-api, -core, -stub, -protobuf, -netty-shaded, protobuf-java, guava 33.7.2 (jre jar), proto-google-common-protos, gson |
| Last-Modified of new artifacts on Maven Central and the Gradle plugin portal | all at least 14 days before f09ed69 (2026-10-29) | newest: proto-google-common-protos 2.78.0, 2026-09-30 |
| `javap` on grpc-netty-shaded 1.83.1 and strings in grpc-core 1.83.1 | read | `maxConcurrentCallsPerConnection` defaults to `Integer.MAX_VALUE`, idle and age to unlimited, `maxRstCount` unset; `ServerImpl` answers an unknown method with `"Method not found: " + name` |

Not checked: `./gradlew compileContractsJava` was not rerun to read its warnings (the reviewer hook allows only the
listed tasks; `compileJava` depends on it and passed). The full `check` and coverage rules were not run. No Kind or
mesh test: there is no Istio on Kind and the cluster is off limits, so the mesh gate is reviewed on paper only.
The Linux-only protoc checksums (no macOS or Windows binaries in the metadata) were not treated as a security issue.

## Findings

### 1. medium: no bound on concurrent calls or connections; a flood drains the shared database pool

- Where: `src/main/java/org/kshrd/webblock/project/common/presentation/grpc/GrpcServer.java:64-68`;
  `GrpcErrorInterceptor.java:84-93`.
- Breaks: TR-24 (resource limits of the one gRPC server; design G15 bounds size but not count) and FR-31 ("Database
  busy: `UNAVAILABLE`, reason `DEPENDENCY_UNAVAILABLE`").
- What: one virtual thread per call with no cap, `maxConcurrentCallsPerConnection` left at `Integer.MAX_VALUE`, no
  `maxConnectionIdle`, `maxConnectionAge`, `permitKeepAliveTime` or `maxRstFramesPerWindow`. Each call takes a
  connection from the Hikari pool that REST also uses (default 10, 30 s `connectionTimeout`). The handler does not
  look at `Context.current().isCancelled()`, so work keeps going after Provisioning's 2 s deadline.
- Abuse: any workload the policy lets in (on Kind every pod; in the mesh a buggy or compromised `provisioning-api`,
  or a retry storm) sends a few thousand CheckAccess calls. The calls queue on the pool, owners' REST requests wait
  up to 30 s and fail, and each pool timeout surfaces as `CannotCreateTransactionException`, which the interceptor
  maps to `INTERNAL_ERROR` and logs at ERROR with a stack trace (a log flood), not `DEPENDENCY_UNAVAILABLE`.
- Fix: (a) a small interceptor in `common/presentation/grpc` with a `Semaphore` (for example 32 permits;
  `tryAcquire` or a short wait) that answers `UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE` through `GrpcErrors` when full,
  and releases on complete and cancel; (b) in `NettyServerBuilder`: `maxConcurrentCallsPerConnection(100)`,
  `maxConnectionIdle(5, MINUTES)`, `maxConnectionAge(30, MINUTES)` with `maxConnectionAgeGrace(10, SECONDS)`,
  `permitKeepAliveTime(1, MINUTES)`, `maxRstFramesPerWindow(...)`; (c) before the lookup in `AccessGrpcService`,
  stop when `Context.current().isCancelled()`; (d) map `CannotCreateTransactionException` whose cause is
  `SQLTransientConnectionException` to `DEPENDENCY_UNAVAILABLE` (REST has the same gap in
  `GlobalExceptionHandler`; fix both or file the REST one separately). Add a `GrpcServerTest` case: N+1 parallel
  calls with N permits, the extra one answers `UNAVAILABLE`. The values need a TR-24 line.

### 2. low: refusals made by grpc-java itself skip both interceptors; one echoes the caller's method path

- Where: `GrpcServer.java:65-69` (no `fallbackHandlerRegistry`); grpc-core 1.83.1 `ServerImpl`.
- Breaks: TR-25 (each error carries one `ErrorInfo` and the `x-request-id`) and the rule that an error never echoes
  caller input.
- What: an unknown method answers `UNIMPLEMENTED` with the description `Method not found: <path the caller sent>`
  (up to the 16 KiB metadata limit). Oversized messages (`RESOURCE_EXHAUSTED`) and bytes that do not parse as the
  proto (`INTERNAL`, "Invalid protobuf byte sequence", for example invalid UTF-8 in a proto3 string) are closed by
  the transport, so they carry no `ErrorInfo` and no request ID. Their texts hold only sizes, no content.
- Abuse: small. A caller gets its own path back; logs and callers cannot correlate these refusals by request ID.
  Provisioning fails closed on all of them.
- Fix: register a `fallbackHandlerRegistry` whose handler closes the call through `GrpcServer.intercepted(...)`
  style handling with a fixed description and `ErrorInfo` reason `ROUTE_NOT_FOUND` (status `UNIMPLEMENTED`, which
  needs a line in decision 0011 since `ROUTE_NOT_FOUND` maps to `NOT_FOUND` today). Record in decision 0011 that
  size and parse refusals come from the transport without `ErrorInfo`. Test: an unknown method's description does
  not contain the path sent.

### 3. low: every `BindableService` bean in the context is exposed on the tokenless port

- Where: `GrpcServer.java:46-47` and `:69`.
- Breaks: TR-24 ("no reflection or health service") as a property that holds only while no library adds a bean;
  `bindableServicesSitInPresentationGrpc` checks only this repository's classes, and
  `tr24_noReflectionOrHealthService` builds the server from a hand-made list, not from the application context.
- Abuse: a later dependency (grpc-services health, reflection, channelz, admin, or a gRPC Spring starter) that
  declares a `BindableService` bean is served on 9090 with no review. A path-scoped mesh rule (below) still blocks
  it in the cluster, but on Kind it is open.
- Fix: refuse to start when a `BindableService` bean's class is outside `org.kshrd.webblock.project..presentation.grpc`
  (or register by an explicit list), and add one assertion in `CheckAccessIntegrationTest` that the running
  server's `getServices()` names only `webblock.project.v1.AccessService`.

### 4. low: an unknown project and a stranger's project give different reasons, which Art. III words otherwise

- Where: `access/application/service/AccessCheckService.java:41-46`; tests `AccessGrpcServiceTest` FR-31.2 and
  FR-31.3, `CheckAccessIntegrationTest.java:98-99`.
- Breaks: Art. III ("a caller who may not see a project ... the internal access check answers `allowed: false`,
  reason `NOT_FOUND`"), and decision 0007's stated reason ("so it does not reveal whether a project exists").
- What the spec intends: FRD FR-30.2 and FR-31.2, the decision line above the FR-30 table, design G17, and
  `provisioning-contract.md` section B all say a stranger gets `NOT_MANAGER` and an unknown ID gets `NOT_FOUND`;
  FR-33 makes the caller map both to 404 `PROJECT_NOT_FOUND`. So the code matches the FRD; the constitution
  sentence and 0007's reason disagree with the FRD. This predates the range (FR-30 since J1); the range copies it to
  gRPC.
- Abuse: a mesh caller (or any pod on Kind) learns "this project ID exists and is someone else's". This is no more
  than what REST already tells any signed-in user through 409 `PROJECT_ID_CONFLICT` on a create `PUT` (0007), and a
  stranger never learns deleted or suspended state (the owner check runs first, proven by
  `AccessCheckServiceTest.aNonOwnerOfADeletedOrSuspendedProjectLearnsNothingAboutItsState`). Timing: both answers
  do the same single primary-key read, and the reason says it outright anyway.
- Fix: an owner ruling, no code change by the implementer. Either the owner amends Art. III to say "reason
  `NOT_FOUND` or `NOT_MANAGER`, which the caller answers as 404", and 0007's reason is reworded; or the FRD changes to
  answer `NOT_FOUND` for both (Provisioning's contract would change too).

### 5. low: the guava override forces 33.7.2 on every configuration, even a later higher request

- Where: `build.gradle:147-155`.
- Breaks: Art. II (pins are deliberate and recorded) as a future risk, not today.
- What: `configurations.configureEach` with `useVersion` rewrites any requested guava to 33.7.2 on all
  configurations, including PMD, SpotBugs and the protobuf tool locators. A future dependency that needs guava 34
  is silently downgraded. Today the lockfile is correct: 33.7.2-jre on the processor paths, 33.7.2-android on the
  rest, and Gradle module metadata resolves the JVM variant `guava-33.7.2-jre.jar` (verification-metadata lines
  604 to 610), which OSV and Maven Central both accept.
- Fix: apply the rule only when the requested version is lower than the pin (compare with
  `org.gradle.util.internal.VersionNumber` or a `constraints { implementation('com.google.guava:guava') {
  version { require '33.7.2-android' } } }`), so a higher request wins and the lockfile shows the change.

## Checked and clean

- Identity from metadata: in main, only `GrpcRequestIdInterceptor.java:30` reads a header (`x-request-id`);
  `AccessGrpcService.java:30-34` uses only the proto fields. `fr33_1_anAdminGetsNoExtraPower` sends `x-user-id`,
  `x-user-roles` and `authorization` and still gets `NOT_MANAGER`; `fr33_1_onlyTheUserIdFieldIsTheSubject` proves a
  contrary `x-user-id` changes nothing. No `SecurityContext` or `CurrentUser` is on the gRPC path. (TR-26, FR-33.1)
- Trust model: matches decision 0011 and Art. III: no token on 9090, the mesh policy is the gate, the subject is the
  request field, as the accepted `access.proto` defines. Tokens are not read, logged or forwarded.
- Input validation: empty strings read as missing (`AccessGrpcMapper.java:70-72`); IDs must be canonical 8-4-4-4-12
  (`AccessCheckRequest.java:22`, anchored `@Pattern`, no backtracking risk); action is an exact, case-sensitive map
  lookup (`AccessAction.java:56-58`); `"DEPLOY_RELEASE"`, trailing space, `drop_db` and `"0"` refused. Message at
  most 64 KiB, metadata 16 KiB, both tested. A refused request never reaches the database (`verifyNoInteractions`).
- Error bodies: description is the code's fixed sentence, `BadRequest` holds the field name and code only
  (`GrpcErrors.java:77-96`); the lock-timeout test asserts no `lock` or table name in the answer; unknown failures
  answer `INTERNAL_ERROR` with no message (`tr25_anUnknownFailureIsInternal`).
- `x-request-id`: same `RequestIds.acceptedOrNew` rule as REST (1 to 128 of `[A-Za-z0-9._-]`), so a kept ID is safe
  to log and echo; sent once in headers or trailers; in the MDC around every listener step and removed after.
- Interceptor order: `ServerInterceptors.intercept(service, error, requestId)` runs `requestId` first, so error
  answers go through `TaggedCall` and read the ID from the attached `Context` (`tr25_anUnsafeRequestIdIsReplaced`).
- Not-found answers: an unknown project is an OK answer, never gRPC `NOT_FOUND` (FR-31.3, both test classes).
- Shutdown: `SmartLifecycle` at the default phase stops before the web server and data sources; `shutdown`, wait the
  grace, `shutdownNow`, 2 s more, then the executor; a taken port stops start-up (fails closed). Tested.
- Method security: `noMethodSecurityAnywhere` still in `ArchitectureRulesTest.java:300` and green; the two G22 rules
  have planted-violation tests (`GrpcRuleTest`, fixtures in `architecture/fixture/`).
- Profiles and stand-ins: no stand-in added; the server runs in every profile with the same code; test properties set
  `webblock.grpc.port=0` only in test support. No secret in code, tests, docs or commits of the range.
- Database: read path only (`ProjectService.ownership`, `@Transactional(readOnly = true)`); no grant or `DELETE`
  change.
- Outgoing calls: none added; the server only listens.
- Error Prone scope: `options.errorprone.excludedPaths = '.*/build/generated/.*'` is set on `compileContractsJava`
  only (`build.gradle:141`); main and test keep every check, and a hand-written file under `src/contracts/java`
  would still be checked. No `-Xep:...:OFF` added.
- Supply chain: all new artifacts are Apache-2.0 or BSD-3-Clause, pinned in `libs.versions.toml` and
  `gradle.lockfile`, recorded in `dependency-versions.md`, covered by sha256 in `verification-metadata.xml`, and the
  checksums match Maven Central. No grpc-services, reflection or Spring gRPC artifact on the runtime class path.
  Runtime additions: grpc-api, -context, -core, -netty-shaded, -protobuf, -protobuf-lite, -stub, -util,
  protobuf-java, proto-google-common-protos, guava, failureaccess, gson, perfmark-api, jsr305, j2objc, android
  annotations, animal-sniffer. OSV clean.
- Contracts sync script: `set -euo pipefail`; the SHA must be 40 lowercase hex; files are a fixed list (no path
  from input); content comes from `git show <sha>:<path>` into a `mktemp -d` folder removed by `trap`; `--check` uses
  `diff -r`, which also reports extra files; `rm -rf` targets only `contracts` under the repository root. Hardening
  idea, not a finding: also require `git merge-base --is-ancestor "$SHA" origin/main` so a commit from an unreviewed
  branch cannot be pinned.

## What the owner must apply in `web-block-config` (agents never apply these)

1. Deployment and Service: port 9090 named `grpc` (or `appProtocol: grpc`), `targetPort: grpc`, Service type
   `ClusterIP` only, no NodePort, no Ingress or gateway route to 9090. Without the `grpc` name Istio treats the port as
   TCP, and an ALLOW rule with `paths` then never matches (calls are refused, which fails closed but breaks FR-31).
2. Keep STRICT `PeerAuthentication` with no `portLevelMtls` override for 9090.
3. `AuthorizationPolicy` of Project: a separate rule for gRPC, never added to the existing access-check rule, because
   that rule also admits the tenant namespaces `wb-*` (decision 0002). Pin the port and method too:

   ```yaml
   - from:
       - source:
           principals: ["cluster.local/ns/webblock/sa/provisioning-api"]
     to:
       - operation:
           ports: ["9090"]
           methods: ["POST"]
           paths: ["/webblock.project.v1.AccessService/CheckAccess"]
   ```

4. Check every other ALLOW rule of Project's policy: each needs `to.operation.ports` (8080 for `/api/*` and
   `/internal/*`, 8081 for Prometheus). A rule with a source and no port or path lets that source reach 9090.
5. Kind: until Istio runs there, 9090 is open to every pod (accepted in design b.6). If the Kind CNI enforces
   NetworkPolicy, a policy that admits 9090 only from Provisioning's namespace closes the gap meanwhile.

The draft runbook `.claude/docs/runbooks/grpc.md` (docs-keeper, not in this range) shows the rule of point 3 without
`ports` and `methods` and does not mention points 4 and 5; worth aligning before the owner applies it.

Verdict: needs fixes. Finding 1 should be fixed (or explicitly accepted by the owner with a TR-24 note) before the
merge. Findings 2, 3 and 5 are small hardening items. Finding 4 needs an owner ruling between Art. III and FR-31.2.
No critical or high finding: the trust model, the metadata handling, input checks and error bodies are sound.
