---
name: project-gates
description: Use when about to say Project service work is done, before a commit a reviewer will see, after a
  dependency change, or when asked to run the checks, gates or tests of this repo.
---

# Project gates

Run from the repo root (this repository or its worktree). Report every command with its real result. A check you
did not run is "not run", never "passed". There is no CI, so every result is a local run. Gradle comes from the
wrapper (`./gradlew`, Gradle 9.8.0); JDK 25 must be on PATH (`java -version`).

Where the gates are defined: `build.gradle` (tasks, tags, JaCoCo rules, Spotless), `gradle/libs.versions.toml`
(pins), `src/test/java/org/kshrd/webblock/project/architecture/` (ArchUnit rules). Testing rules:
`.claude/rules/testing.md`.

## What "done" needs

- **Format:** `spotlessCheck` passes (palantir-java-format, unused imports removed, trailing whitespace, final
  newline). Fix with `./gradlew spotlessApply`, never by hand-formatting against it.
- **Compile:** `compileJava` and `compileTestJava` pass with Error Prone (an Error Prone error fails the build).
  Read the warnings in the output (Error Prone `[Name]`, javac `[deprecation]` and `[removal]`); a new warning in
  code you touched is a finding. The `contracts` source set (stubs `protoc` generates from `contracts/proto`)
  compiles without Error Prone; main and test keep every check.
- **Contract stubs** (`contractStubsInBootJar`, part of `check`): the boot jar holds the stubs generated from
  `contracts/proto` (`webblock/project/v1/AccessServiceGrpc.class`).
- **Architecture:** `ArchitectureRulesTest` (layers, type folders, no method security, envelope only from the
  advice, stand-ins only in `prototype`), `EnvelopeRuleTest`, `MethodSecurityRuleTest`, `StandInRuleTest`,
  `GrpcRuleTest` and `LayoutRuleTest` pass. They run inside `./gradlew test`.
- **Tests:** `test` (untagged: unit, web slices, ArchUnit) and `integrationTest` (`@Tag("integration")` and
  `@Tag("messaging")`: PostgreSQL 18 and RabbitMQ through Testcontainers) pass. New tests carry their FR ID.
- **Coverage** (`jacocoTestCoverageVerification`, part of `check`): 70% of lines overall, 80% in
  `org.kshrd.webblock.project.common*`, 80% in each `application.*.handler.*Service` and
  `domain.*.model.*Entity` class. It reads both suites' recordings.
- **Supply chain** (after any dependency change, and before a phase merge): `scripts/osv-scan.sh` exits 0.
- **Spec:** every acceptance row the change touches has a passing test; no Drift left unreported.

Review rules: no TODO stubs or fake success (unsupported means an explicit error code); every error mapped to an
`ErrorCode`; every resource closed; no unbounded lists, request bodies or retries; tests check behaviour and the
refusal cases (401, 404 for "not the owner", 400 field errors, 409, 422), not mocks that repeat the code;
migrations immutable, app and migrator privileges separate.

## Fast tier (always, in this order)

```sh
./gradlew spotlessCheck
./gradlew compileJava compileTestJava
./gradlew test
```

One class while you work: `./gradlew test --tests '*ProjectServiceTest'` (also works with `integrationTest`).

## Slow tier (when the change touches it, and always before a merge)

| Change touches | Run | Needs |
| --- | --- | --- |
| Entities, repositories, SQL, migrations, transactions | `./gradlew integrationTest` | Docker; PostgreSQL 18 container |
| Outbox, inbox, listeners, jobs, events | `./gradlew integrationTest` (the `messaging` tag is in it) | Docker; RabbitMQ container |
| Everything, before a merge or a "done" claim | `./gradlew check` (`spotlessCheck`, `test`, `integrationTest`, coverage) | Docker |
| Dependencies (`libs.versions.toml`, `build.gradle`, BOM overrides) | the dependency steps below | network |
| The cross-service exit on Kind | `.claude/docs/runbooks/j1-kind-exit.md`, `scripts/kind/j1-exit-check.sh` | a Kind slot from the owner |

Machine slot rules (shared machine; Provisioning runs Kind stacks here too):

- Check `free -m` and `docker ps` first.
- One Testcontainers suite at a time; never run one while a Kind stack runs unless the owner says so.
- Never change a real cluster or open `~/.kube` or a kubeconfig (`.claude/CLAUDE.md` section 0).

## Dependency changes

A new or changed dependency needs the owner's approval first (open-source licence, release at least 14 days old,
exact pin). Then:

```sh
./gradlew --write-verification-metadata sha256 resolveAndLockAll --write-locks
./gradlew --write-verification-metadata sha256 check
scripts/osv-scan.sh
```

Lock and verify in the same run: on its own, `resolveAndLockAll --write-locks` fails because verification refuses
the new artifacts before they have checksums.

Commit `gradle/libs.versions.toml`, `gradle.lockfile`, `settings-gradle.lockfile` (if changed),
`gradle/verification-metadata.xml` and the entry in `.claude/docs/dependency-versions.md` with the change.

## Coverage report (when asked, or before a phase merge)

`./gradlew jacocoTestReport` runs both suites and writes the HTML and XML reports under `build/reports/jacoco/`.
Never lower a rule, add an exclusion or skip a failing path to pass it.

## Spec check (report only, not a gate)

`python3 -I scripts/spec-check.py` reads `.claude/docs/requirements/spec.json`. It reports duplicate IDs, parents
that are missing or removed, any FR-n, FR-n.m or TR-n cited in `src/test`, the branch's commit messages
(`git log main..HEAD`), the designs and the plans that does not exist or is removed, and the current Must FRs with
no test. It does not block a "done" claim; put its "Errors" and "Must FRs with no test" lines in the report, and
fix an error (or report it as Drift) before review. `--self-test` proves the script itself.

## Report-only QA tools (not gates)

`./gradlew pmdMain pmdTest spotbugsMain cpd` runs PMD, SpotBugs and CPD (decision `0009`). They never fail the
build and are not in `check`. The `qa-sweep` skill runs and triages them; a "done" claim does not wait for them.
A new finding in code you touched is worth a look before review. A tool becomes a gate only by a new decision record.

## Rules that make a result count

- Testcontainers suites fail, never skip, when Docker is missing. A skip is a failure.
- Do not add `@Disabled`, `@SuppressWarnings` without a reason, an Error Prone `-Xep:...:OFF`, a looser ArchUnit
  rule or a coverage exclusion to make a gate pass. An exception needs the owner and a decision record.
- If a gate fails for a reason outside your change, say so and show the output; do not fix unrelated code
  without asking.
- Counts come from the test result files:
  `awk -F'tests="' '/<testsuite /{split($2,a,"\""); s+=a[1]} END{print s+0}' build/test-results/test/*.xml`
  (use `integrationTest/` for the slow suite, `failures="` and `skipped="` the same way).

## Report

A table: command, result (passed N, failed N, not run), notes. Name every check that was not run and why.

| Command | Result | Notes |
| --- | --- | --- |
| `./gradlew spotlessCheck` | passed | |
| `./gradlew test` | passed 652 | |
| `./gradlew integrationTest` | not run | no database or broker change |
| `scripts/osv-scan.sh` | not run | no dependency change |
| `python3 -I scripts/spec-check.py` | 0 errors | report only; Must FRs with no test: 36 |
