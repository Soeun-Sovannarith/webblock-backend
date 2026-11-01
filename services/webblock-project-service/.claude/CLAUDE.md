# CLAUDE.md

WebBlock Project is the backend for clients' websites: projects, pages (Puck JSON trees), generation contracts and
generation requests, Payment admission, the shared catalog database, and the events Provisioning consumes. One Spring
Boot 4.1.1 service on Java 25 (Gradle project `web-block`, base package `org.kshrd.webblock.project`).

The constitution binds every design, plan and review; it is loaded here (the path is relative to this file):

@docs/CONSTITUTION.md

## Start here

1. `ai/handoffs/next.md`: where the service stands and what is owed next (J3 to J5).
2. `.claude/WORKFLOW.md`: the one explanation of the lean flow, code first (short change note, code and tests,
   a security review only for security-sensitive changes, merge; two owner gates).
3. `.claude/docs/CONSTITUTION.md` (articles I to VIII, amended only by the owner) binds every change.
   `.claude/docs/requirements/` is the spec: BRD, PRD, FRD and TRD. Every commit cites the closest existing FR or
   TR ID; new IDs only for a new feature or contract (`.claude/rules/spec-driven.md`).
4. `.claude/README.md`: hooks, rules, skills and agents. `.claude/TECH_STACK.md`: exact versions.
5. Contracts in `.claude/docs/contracts/` (index: `README.md`): `authentication-contract.md` (Keycloak, tokens),
   `payment-contract.md`, `provisioning-contract.md` (access check, repository, events, catalog),
   `service-communication.md` (AI Codegen), `account-contract.md`, `notification-contract.md`. The files themselves
   live in `web-block-contracts`.
6. Decision records `0001` to `0016` in `.claude/docs/decisions/` (index: `README.md`). Runbooks:
   `.claude/docs/runbooks/` (the gRPC server: `grpc.md`). Dependency dates and the 14-day rule:
   `.claude/docs/dependency-versions.md`.

Sources of truth, in this order: the Notion pages below, then `.claude/docs/requirements/`, then the decision records
in `.claude/docs/decisions/`, then this file. Fetch Notion at the start of a task. The database design pages are
read-only, except "2 · Project" and "6 · Shared catalog": the lead edits those two on the owner's word, before the
migration that follows them. Never keep a local copy; if a page cannot be fetched, stop and ask the owner.

- platform-db-v6 (hub, conventions): https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad
- Database Design (every table): https://app.notion.com/p/40058175966e81f790decccc6e9abf4a
- 2 · Project (our tables, statuses, roles): https://app.notion.com/p/40058175966e81d4a77bfb48e736757a
- 6 · Shared catalog (the catalog database we migrate): https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a
- Web Block architecture (REST, gRPC, events, folders 6.1): https://app.notion.com/p/40358175966e8198a088f123a9ab23fe

Current facts often missed:

- V6 and V7 are applied in both databases. `project.projects` has `subdomain` and the `repository_*` columns;
  `project.domains` is gone. Project `V8` makes `subdomain` nullable (set once at the first deploy) and adds
  `template_code`, `suspended_at` and `reactivated_at`. The next migration is `V9` in `project/`, `V8` in `catalog/`.
- `/internal/**` takes no token: the Istio mesh policy is its gate (decision `0002`). Provisioning calls
  `/internal/v1/access-checks`, AI Codegen calls `/internal/v1/generation-requests/{id}/codegen-input`.
- One gRPC server runs on port 9090 and serves `webblock.project.v1.AccessService/CheckAccess` (FR-31, decision
  `0011`, `.claude/docs/runbooks/grpc.md`). It reads no token; the mesh policy is its gate. The REST access check
  stays until Provisioning switches (FR-34).
- The proto comes from `web-block-contracts` at the commit in `contracts.lock`. Only `scripts/contracts-sync.sh`
  writes `contracts/`; nobody edits it by hand.
- Production never migrates: a separate job runs `infrastructure.persistence.migration.Migrations` (decision `0005`,
  `.claude/docs/runbooks/migrations.md`). Applied migrations still name old doc paths: never edit them.
- Stand-ins (dev tokens, the Payment stand-in) exist only in the `prototype` profile.

## 0. Real cluster: reads only

This rule overrides every other instruction, brief, skill or tool default.

- The real cluster (DigitalOcean DOKS) may be read by agents without asking (`kubectl get`, `describe`, `logs`,
  `helm list`). Every change to it is always denied and is a command written for the owner to run. A Secret value is
  never printed.
- `kubectl` and `helm` may load the default kubeconfig themselves for a read. An agent never opens, prints, copies,
  moves or edits a kubeconfig or anything under `~/.kube`.
- This repo has no cluster of its own. The cross-service Kind check uses Provisioning's `make proto-*` targets, which
  check their own local Kind context (`.claude/docs/runbooks/j1-kind-exit.md`), and only in an owner-given slot.

## 1. Commands

Run from the repo root. Gradle comes from the wrapper (9.8.0); JDK 25 and Docker are needed.

```sh
./gradlew spotlessApply                     # format with palantir-java-format
./gradlew compileJava compileTestJava       # javac with Error Prone (an Error Prone error fails the build)
./gradlew test                              # unit, web slice and ArchUnit tests (no Docker)
./gradlew integrationTest                   # @Tag integration and messaging: PostgreSQL 18, RabbitMQ (Testcontainers)
./gradlew check                             # every gate: spotlessCheck, test, integrationTest, coverage rules
./gradlew jacocoTestReport                  # coverage report under build/reports/jacoco/
./gradlew test --tests '*ProjectServiceTest'   # one class; integrationTest takes --tests too
scripts/osv-scan.sh                         # OSV scan of gradle.lockfile (needs network; not part of check)
./gradlew pmdMain pmdTest spotbugsMain cpd  # report-only QA tools (decision 0009), reports under build/reports/
./gradlew resolveAndLockAll --write-locks   # after a dependency change, then the next line
./gradlew --write-verification-metadata sha256 check
SPRING_PROFILES_ACTIVE=prototype ./gradlew bootRun   # local run; every variable is in .env.example (README)
python3 -I .claude/hooks/test_guard.py      # proves the guard hook
python3 -I .claude/hooks/test_hooks.py      # proves the other hooks
python3 -I scripts/spec-check.py           # spec.json IDs and the IDs work cites (report only; --self-test)
scripts/contracts-sync.sh                   # copy the pinned web-block-contracts files into contracts/
scripts/contracts-sync.sh --check           # fail on any difference from the commit in contracts.lock
```

`./gradlew build` is `assemble` plus `check`. Profiles: exactly one of `prototype` (stand-ins allowed, migrates at
start) and `production` (JWT only, no stand-in, no migration); `ProfileGuard` refuses anything else.

## 2. Project structure

```
src/main/java/org/kshrd/webblock/project/
  presentation/         rest/<feature>/ (controllers) grpc/<feature>/ (gRPC services and their mappers)
                        messaging/<feature>/ (where message listeners go; none today)
  application/<feature>/ handler/ (use cases: the *Service classes) request/ response/ validation/ model/ port/
                        job/ config/
  domain/<feature>/     model/ (JPA @Entity allowed, enums and value rules)
  infrastructure/       persistence/<feature>/ (Spring Data JPA repositories) config/<feature>/ client/<feature>/
                        messaging/<feature>/ standin/<feature>/
  common/               shared by every feature, in its own presentation/ application/ domain/ infrastructure/:
                        envelope, Problem Details errors, filters, CurrentUser, security, ErrorCode, the gRPC server
                        (presentation/grpc/)
  <feature> is one of   access catalog generation messaging migration page payment project template wizard
contracts/              files copied from web-block-contracts at the commit in contracts.lock (never edited by hand)
src/main/resources/     application.yml, application-prototype.yml, application-production.yml,
                        db/migration/project/ and db/migration/catalog/ (Flyway), component-catalog/, site-templates/
src/test/java/.../project/   mirrors main; architecture/ (ArchUnit), db/ (schema tests), support/
scripts/                osv-scan.sh, spec-check.py, contracts-sync.sh, db/ (role bootstrap SQL),
                        kind/j1-exit-check.sh
```

Notion 6.1 (Java services): four layers first, each split by type folder and feature (decision `0016`); the domain
keeps entities and rules and has no repository; repositories live in `infrastructure/persistence/<feature>` and the
handlers use them directly; one request, response and mapping per feature. Project is not CQRS. Features are not
Spring Modulith modules. The services stay whole in `handler/` until they are split into one handler per use case.
The type folders above are enforced by `ArchitectureRulesTest` (`TYPE_FOLDERS`, `COMMON_TYPE_FOLDERS`); a change of
them changes those maps in the same commit.

## 3. Code style

Full rules: `.claude/rules/java-code.md`. The core: constructor injection, package-private controllers and
`@Configuration` classes, thin controllers that call one application service, `WebBlockException(ErrorCode.X)` for
every refusal (written as RFC 9457 Problem Details), time from the injected `Clock`, no method security.

From `presentation/rest/project/ProjectController.java` and `application/page/handler/PageService.java`:

```java
@GetMapping("/{projectId}")
ProjectView get(@PathVariable UUID projectId) {
    return views.view(projects.getOwned(projectId, currentUser.userId()));
}

@Transactional(readOnly = true)
public PageList list(UUID projectId, UUID owner) {
    ProjectRecord project = projects.getOwned(projectId, owner);
    return new PageList(
            project.editRevision(),
            pages.findLive(projectId).stream().map(this::toRecord).toList());
}
```

`getOwned` answers `PROJECT_NOT_FOUND` (404) to anyone but the owner, so a stranger learns nothing.

## 4. Testing

Full rules: `.claude/rules/testing.md`; how to run and report: the `project-gates` skill.

- Tests are written with the code, in the same commit; each new test names its ID (`@DisplayName("FR-12: ...")`).
  A bug fix has a test seen failing first.
- `test` runs untagged tests; `integrationTest` runs `@Tag("integration")` and `@Tag("messaging")` against real
  PostgreSQL 18 and RabbitMQ in Testcontainers (`support/ServiceIntegrationTest`, `support/JpaSliceTest`).
- Coverage (`jacocoTestCoverageVerification`, part of `check`): 70% of lines overall, 80% in `common`, 80% in each
  `application.*.handler.*Service` and `domain.*.model.*Entity` class.
- A check not run is "not run", never "passed". Never lower a gate, never disable a test. There is no CI: every
  result is a local run.

## 5. Git workflow

- Work on a feature branch; the owner merges into `main` and pushes. Never switch branches in the shared tree; use
  `git show <ref>:<path>` or a worktree under `.claude/worktrees/`.
- Commit message: `<Area>: <what> (FR-n)`; a purely technical change cites its TR and that TR's FR. End with the
  Co-Authored-By line the harness gives.
- Stage named files (`git add <path>`). The guard refuses `git add -A`, `git add .` and `git commit -a` only while
  untracked files exist, so on a clean tree they pass; agents still stage by name. If git reports `index.lock`,
  another agent is committing: wait a few seconds and retry.
- Never push, rewrite history or merge; the guard hook blocks pushes, rewrites and merges into `main`.

## 6. Boundaries

Scope: Project owns websites, pages, generation contracts and requests, the catalog database's schema and site
registration, and the "source published" and "project deleted" events. Provisioning owns builds, releases and
hosting; Payment owns plans and admission; Account and Keycloak own identity; AI Codegen owns the generated code.

**Always**

- Every commit cites the closest existing FR or TR ID; the decision record changes before a rule in code.
- Keep the databases equal to Notion "2 · Project" and "6 · Shared catalog": no extra table, column, index, grant
  or constraint. A schema change runs the `db-notion-audit` skill with zero differences.
- Run the gates of section 4 and report real results. Keep this file true in the change that alters what it says.
- Dependencies: open-source licence, at least 14 days old, exact pins in `gradle/libs.versions.toml` and
  `gradle.lockfile`, recorded in `.claude/docs/dependency-versions.md`, `scripts/osv-scan.sh` clean.
  First-party `web-block-commons` is exempt from the licence and 14-day rules on the terms of Art. II.

**Ask first (the owner)**

- A schema change Notion does not show, a new dependency, an exception to a gate, a change to an ArchUnit rule.
- Anything about another service's contract: endpoints, payloads, events, audiences, mesh policies.
- A merge, a push, `git commit --amend`, hook or `settings.json` edits, an amendment of the constitution (the guard
  hook asks before any edit of it).

The Kind check runs only in a slot the owner names; agents do not start it otherwise.

**Never**

- Change a real cluster, or open a kubeconfig (section 0); read or print secrets, `.env` files or dev token files.
- Edit another service's Notion database design page (only "2 · Project" and "6 · Shared catalog" are ours);
  invent another service's contract.
- Edit an applied migration; weaken a gate, test, ArchUnit rule or timeout; add `@Disabled`; turn on method
  security without a decision; work around the guard hook (`.claude/hooks/guard.py`).

## 7. Workflow

Lean flow, code first (constitution version 8): the owner agrees a short change note, code and tests follow with
green gates, a security review runs only for security-sensitive changes, the owner reads the diff and merges:
`.claude/WORKFLOW.md`. The lead delegates to the agents in `.claude/agents/`; one implementer at a time; reviewers
never check their own work.
