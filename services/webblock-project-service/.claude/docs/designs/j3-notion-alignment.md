# J3 Notion alignment: migration V6, gRPC CheckAccess, the 6.1 layout

**Status:** Approved at gate 2 by the owner, with every recommendation below.

## Context

Three things in the code differ from Notion today (TRD "Drift seen"):

1. The project database is behind Notion "2 · Project" (S1) on its "Planned, not yet applied" list: no `subdomain`,
   `github_repository_*` columns, `project.domains` with two enums, an owner index on live projects only. The site
   address is built from the project ID.
2. Notion "Web Block architecture" (S2) §3 says Provisioning and the website portals call
   `webblock.project.v1.AccessService/CheckAccess` over gRPC. Project serves only the REST access check, refuses
   `manage_site`, and has no `PROJECT_DELETED` or `PROJECT_SUSPENDED` code.
3. S2 §6.1 puts request, response and mapping types in `application/`; Project keeps them in
   `presentation/request` and `presentation/response`.

The guard hook also misses `scripts/`, which `.claude/rules/spec-driven.md` counts as code.

## IDs covered

- FR: FR-13, FR-31, FR-32, FR-33, FR-36. FR-10, FR-15 and FR-30 change only as those FRs say (the sub-domain in the
  create and read answers; `manage_site` on the REST check).
- TR: TR-4, TR-5, TR-10, TR-11, TR-13, TR-14, TR-17, TR-24, TR-25, TR-26, TR-27, TR-39 (added in the TRD).
- In scope, not covered: FR-34 (removing the REST check). It waits until the owner confirms Provisioning calls
  FR-31 everywhere.

## a. Migration V6 and the sub-domain (FR-36, FR-13)

### a.1 The migration file

`src/main/resources/db/migration/project/V6__subdomain_and_notion_alignment.sql`, written with the `new-migration`
skill, runs as `project_migrator` (the `beforeEachMigrate.sql` guard) in one transaction. Result: `project.projects`
exactly as S1 "projects", in S1's column order.

1. `LOCK TABLE project.projects, project.pages, project.generation_contracts, project.generation_requests,
   project.domains IN ACCESS EXCLUSIVE MODE`.
2. Pre-check (G2): when two projects share `left(project_id::text, 12)`, stop with SQLSTATE `23505` and a sentence
   naming the count and the cause; nothing changes.
3. Drop `project.domains`, the function `project.domains_guard()`, then the types `project.domain_kind` and
   `project.domain_status`. Their grants go with them.
4. Drop the three foreign keys that point at `project.projects`: `pages_project_fk`,
   `generation_contracts_project_fk`, `generation_requests_project_fk`.
5. Rename the old table to `project.projects_v5`, and its index-backed names `projects_pkey`,
   `projects_github_repository_id_key` and the index `projects_owner_idx` to `*_v5` names, so the final names are
   free.
6. Create `project.projects` in S1 order: `project_id`, `owner_user_id`, `name`, `subdomain varchar(63) NOT NULL`,
   `business_type_id`, `edit_revision` (keeps V1's `DEFAULT 0`), `component_catalog_version`, `repository_id bigint`,
   `repository_full_name varchar(140)`, `status`, `created_at`, `updated_at`, `deleted_at`, with V1's types and NULL
   rules. Constraints: `projects_pkey`, `projects_subdomain_key UNIQUE (subdomain)`, `projects_repository_id_key
   UNIQUE (repository_id)`, `projects_repository_pair`, `projects_repository_id_check (repository_id > 0)`,
   `projects_edit_revision_check`.
7. Copy every row; `subdomain` = `'p-' || left(project_id::text, 12)`; the repository columns take the
   `github_repository_*` values.
8. Drop `project.projects_v5` (its trigger goes with it).
9. `CREATE INDEX projects_owner_idx ON project.projects (owner_user_id)`: no condition, all projects.
10. Replace `project.projects_guard()`: the two V1 checks, plus `subdomain IS DISTINCT FROM OLD.subdomain` raises
    `check_violation`, constraint `projects_subdomain_immutable`. Create the trigger `projects_guard BEFORE UPDATE`.
11. Re-create the three foreign keys with the same names and `ON DELETE RESTRICT`.
12. `GRANT SELECT, INSERT ON project.projects TO project_app`; `GRANT UPDATE (name, status, edit_revision,
    component_catalog_version, repository_id, repository_full_name, updated_at, deleted_at)`. Nothing else.

### a.2 Code that must follow (`ddl-auto: validate`)

| Class | Change |
| --- | --- |
| `project/domain/entity/ProjectEntity` | Maps `subdomain` (`nullable = false`, `updatable = false`, length 63), `repository_id`, `repository_full_name`; `draft(...)` takes the sub-domain; getter `subdomain()` |
| `project/domain/entity/DomainEntity`, `project/domain/model/DomainKind`, `DomainStatus`, `project/infrastructure/persistence/DomainRepository` | Deleted |
| `project/domain/model/Subdomain` (new) | The one sub-domain rule: one DNS label `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$` and not in a given reserved set |
| `project/infrastructure/persistence/ProjectRepository` | `existsBySubdomain(String)`, over all rows |
| `project/application/config/PlatformProperties` | Adds `reservedSubdomains` (each entry must pass the label shape); javadoc says `<subdomain>.<siteDomain>` |
| `project/application/model/NewProject`, `ProjectRecord`, `ProjectView` | Carry `subdomain`; `platformHostname` stays |
| `project/application/service/ProjectService` | No `DomainRepository`; `requireValidSubdomain(String)`; `subdomainTaken(String)`; hostname = sub-domain + `.` + site domain, one query per read |
| `project/infrastructure/config/ProjectConfig` | Maps `projects_pkey` to `PROJECT_ID_CONFLICT` and `projects_subdomain_key` to `HOSTNAME_TAKEN`; the two `domains_*` entries go |
| `common/domain/error/ErrorCode` | `DOMAIN_TERMINAL` deleted; `HOSTNAME_TAKEN` message "This sub-domain is already taken." |
| `wizard/.../CreateProjectRequest`, `wizard/application/model/CreateProjectCommand` | `@NotNull String subdomain` |
| `wizard/application/service/ProjectCreationService` | Order of G10; `repeat` also compares the sub-domain |
| `src/main/resources/application.yml` | `webblock.platform.reserved-subdomains: www, app, api, admin, git, mail, status, docs, auth`; the site-domain comment names `<subdomain>.<site-domain>` |

### a.3 Decisions

| G | Decision | Test | Options and cost if wrong |
| --- | --- | --- | --- |
| G1 | V6 rebuilds `project.projects` so `subdomain` stands fourth, as S1 lists it, keeping every row, name, key, foreign key, trigger and grant | `MigrationTest`: column order equals S1; rows of a V5 database kept | Append the column instead (one DIFFERENT audit finding, needs a record; TRD Q-3). Rebuild costs a longer file and a short lock of four small tables |
| G2 | The pre-check stops V6 before any change when the backfill would give two projects the same sub-domain | Seed two IDs with the same 12-character prefix at target 5; V6 fails, nothing changed | UUIDv7 IDs made within about 16 ms share the prefix; without the check the unique key fails with a less clear error |
| G3 | Names: `projects_subdomain_key`, `projects_repository_id_key`, `projects_repository_pair`, `projects_repository_id_check`, `projects_owner_idx` (no condition), `projects_subdomain_immutable` | Catalog queries in `MigrationTest`, `ProjectConstraintsTest`; FR-36.8 | S1 states rules, not names; keeping `github_*` names would mislead readers. Cost: renaming again needs another migration |
| G4 | One guard function `project.projects_guard()` refuses changes of `business_type_id`, a set `deleted_at` and `subdomain` | FR-36.2 as `project_migrator` | A second trigger would also work; one function keeps the frozen columns in one place |
| G5 | No CHECK on the label shape in the database: S1's planned list names none; the service checks shape and reserved names | FR-13.3 | A hand-written insert could store a bad label. Low: only the service inserts |
| G6 | `project_app` keeps SELECT and INSERT and gets UPDATE on exactly S1's eight columns; nothing on `subdomain`, `owner_user_id`, `business_type_id`; no DELETE | FR-36.6, `GrantsTest` compares every privilege | Any extra grant fails the audit (Art. III) |
| G7 | The sub-domain rule lives once, in `Subdomain` with the configured reserved set; `ProjectService.requireValidSubdomain` answers 400 `VALIDATION_FAILED`, field `subdomain`, code `VALUE_INVALID`; a missing value is `FIELD_REQUIRED` from `@NotNull` | FR-13.3; a missing field | A `@Pattern` on the request would copy the rule (two places to change) |
| G8 | Creation order: sub-domain rule, then a known ID (repeat; another sub-domain is `PROJECT_ID_CONFLICT`), type and template, catalog website, `subdomainTaken` (409 `HOSTNAME_TAKEN`, Payment not asked), release fence, Payment, insert. A race loses on `projects_subdomain_key`: the existing permanent-failure path schedules the admission release and answers `HOSTNAME_TAKEN` | FR-13.1, FR-13.2, FR-13.4; a race test with two creates | Checking after Payment would admit a website that cannot be created |
| G9 | `ProjectView` gains `subdomain` and stays in `project/application/model`, the module's one representation of a website, used by the project and wizard controllers | FR-13.1, FR-15.1 | A wizard copy obeys "never shared" literally but duplicates ten fields (O-3) |
| G10 | Existing sites change address from `<projectId>.<site domain>` to `p-<12 characters>.<site domain>` at V6 | FR-36.1 | Their old address stops working once Provisioning routes by sub-domain (O-4) |

### a.4 Tests (written first, each names its ID)

- `db/MigrationTest`: table list without `domains`; `project.projects` columns, types, NULL rules and order equal S1
  (FR-36.4, FR-36.5, G1).
- `db/SubdomainMigrationTest` (new): a V5 database with rows (Flyway `target("5")`), then V6: backfill values and
  NOT NULL (FR-36.1); update refused as the migrator (FR-36.2); a deleted project's sub-domain refused for a new row
  (FR-36.3); the owner index definition (FR-36.8); the pre-check (G2).
- `db/GrantsTest`: exact privileges per role after V6 (FR-36.6). `db/ProjectConstraintsTest`, `db/Rows`,
  `db/ProjectsAndPagesTest`: new column names and `subdomain`.
- `db/DomainsTest` and `db/DomainRulesTest` are deleted with the table they test; FR-36.5 proves it is gone.
- `ProjectServiceTest`, `ProjectCreationServiceTest`, `ProjectCreationControllerTest`,
  `ProjectCreationIntegrationTest`: FR-13.1 to FR-13.4 and the race; `PlatformPropertiesTest`: reserved entries.
- `J1ErrorCodesTest`, `ProjectConfigTest`: `DOMAIN_TERMINAL` gone, `projects_subdomain_key` mapped.

### a.5 Audit and change report

- After the step, the `db-notion-audit` skill runs and must report 0 DIFFERENT (FR-36.7); report in `ai/reviews/`.
- The DB change report, outside the repo: `/home/roa/Documents/web-block/platform-db-rev25-project-V6-report.md`
  (hub revision 25), in the layout of `platform-db-rev25-provisioning-00023-report.md`: "Changed", "Not changed",
  "UI impact". UI impact: the wizard sends `subdomain` (required), shows `platformHostname` from it, and old sites
  show `p-...` addresses; no domain screens.

## b. gRPC CheckAccess (FR-31 to FR-33)

### b.1 Build

- `contracts.lock` holds `afff9245444c628b74f88d281939f4bc8b4de3e2` (`web-block-contracts` main);
  `scripts/contracts-sync.sh` has the same shape as `web-block-commons/scripts/contracts-sync.sh` and copies
  `proto/webblock/project/v1/access.proto` into `contracts/`; `--check` fails on any difference. `contracts/` is
  never edited by hand.
- The protobuf Gradle plugin generates Java and gRPC stubs from `contracts/proto` in a source set `contracts`. Main
  compiles and runs against it; the boot jar ships it. The accepted proto has no `java_package`, so the stubs are in
  `webblock.project.v1`, outside the base package. If the generated code needs `javax.annotation.Generated`, use the
  plugin option that omits it; if none exists, ask the owner before adding a library.
- Dependencies (TR-10): `grpc-api`, `grpc-stub`, `grpc-protobuf`, `grpc-netty-shaded`, `grpc-inprocess` (tests),
  `protobuf-java`, `proto-google-common-protos`, the `com.google.protobuf` plugin, `protoc`,
  `protoc-gen-grpc-java`. One grpc-java version for all its artifacts. Versions are picked when the step runs, under
  the 14-day rule, and are the ones `web-block-commons` pins (whichever repo pins first sets them). A version the
  Spring Boot 4.1.1 BOM manages stays the BOM's unless OSV reports an advisory.

### b.2 Server and errors (`common`)

| Class (new) | What it does |
| --- | --- |
| `common/application/config/GrpcProperties` | `webblock.grpc.port` (default 9090) and `webblock.grpc.shutdown-grace` (default 10s) |
| `common/presentation/grpc/GrpcServer` | Lifecycle bean: one Netty server on the port, every `BindableService` bean, the two interceptors, a virtual-thread executor, inbound message at most 64 KiB, metadata at most 16 KiB, no reflection or health service; graceful stop within the grace |
| `common/presentation/grpc/GrpcRequestIdInterceptor` | Reads `x-request-id` with the rule of `RequestIdFilter`, else makes one; puts it in the logging context and the call |
| `common/presentation/grpc/GrpcErrorInterceptor` | Turns an exception into a closed call through `GrpcErrors`; an unknown exception is logged without request content and answers `INTERNAL`, `INTERNAL_ERROR` |
| `common/presentation/grpc/GrpcErrors` | `ErrorCode` to status, `ErrorInfo` and `BadRequest` |

Status per category (S2 §2.3): `VALIDATION` `INVALID_ARGUMENT`; `UNAUTHENTICATED` `UNAUTHENTICATED`; `FORBIDDEN`
`PERMISSION_DENIED`; `NOT_FOUND` `NOT_FOUND`; `CONFLICT` `ABORTED`, except `ALREADY_EXISTS` for
`PROJECT_ID_CONFLICT`, `PAGE_PATH_TAKEN`, `HOSTNAME_TAKEN` and `FAILED_PRECONDITION` for `PROJECT_DELETED`,
`PROJECT_SUSPENDED` (CMN `PLAN.md` Q2); `BUSINESS_RULE` `FAILED_PRECONDITION`; `PAYLOAD_TOO_LARGE`
`RESOURCE_EXHAUSTED`; `DEPENDENCY` `UNAVAILABLE`; `INTERNAL` and the HTTP-only categories (405, 406, 415)
`INTERNAL`. `PessimisticLockingFailureException` and `QueryTimeoutException`, the classes
`GlobalExceptionHandler` answers 503, give `DEPENDENCY_UNAVAILABLE`.

### b.3 CheckAccess (`access`)

| Class | Change |
| --- | --- |
| `access/presentation/grpc/AccessGrpcService` (new) | Extends the generated `AccessServiceImplBase`; calls the mapper and `AccessCheckService.check` |
| `access/presentation/grpc/AccessGrpcMapper` (new) | Proto request to `AccessCheckRequest` (empty strings read as missing), checked with the Bean Validation rules REST uses; field names in snake_case (`user_id`, `project_id`, `action`); answer to `CheckAccessResponse` (`reason` "" when allowed) |
| `access/application/model/ProvisioningAction` | Renamed `AccessAction`; adds `MANAGE_SITE` (`manage_site`, a change) |
| `common/domain/error/ErrorCode` | Adds `PROJECT_DELETED` ("This project is deleted.") and `PROJECT_SUSPENDED` ("This project is suspended."), category `CONFLICT` |

### b.4 The shared action list (FR-32)

Project owns two new files in `web-block-contracts`, next to its protos; the owner pushes that repository. The
README's owner table gets the row `access/*`: project. Then Project moves `contracts.lock` to that commit and adds
both files to its sync list.

`access/actions.v1.json`:

```json
{
  "actions": [
    { "name": "deploy_release", "write": true, "admin_allowed": false },
    { "name": "promote_release", "write": true, "admin_allowed": false },
    { "name": "rollback_release", "write": true, "admin_allowed": true },
    { "name": "build_release", "write": true, "admin_allowed": false },
    { "name": "manage_site", "write": true, "admin_allowed": false },
    { "name": "get_release", "write": false, "admin_allowed": false },
    { "name": "get_command", "write": false, "admin_allowed": false },
    { "name": "get_deployment", "write": false, "admin_allowed": false },
    { "name": "get_release_diagnostics", "write": false, "admin_allowed": false },
    { "name": "list_releases", "write": false, "admin_allowed": false },
    { "name": "list_commands", "write": false, "admin_allowed": false }
  ]
}
```

The `admin_allowed` values are Provisioning's: `rollback_release` is the one CMN §7.1 records; Provisioning confirms
or changes them while the file is DRAFT.

`access/actions.v1.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/access/actions.v1.schema.json",
  "title": "Access: CheckAccess actions v1",
  "description": "<the sentence below, on one line>",
  "type": "object",
  "additionalProperties": false,
  "required": ["actions"],
  "properties": {
    "actions": {
      "type": "array",
      "minItems": 1,
      "uniqueItems": true,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["name", "write", "admin_allowed"],
        "properties": {
          "name": { "type": "string", "pattern": "^[a-z][a-z_]{0,62}[a-z]$" },
          "write": { "type": "boolean" },
          "admin_allowed": { "type": "boolean" }
        }
      }
    }
  }
}
```

The schema's `description`, one line in the file: "DRAFT, proposed by project, waits for provisioning. Owner:
project. Every action AccessService/CheckAccess accepts. write true: the action changes state, and Project refuses
it on a deleted or suspended project (PROJECT_DELETED, PROJECT_SUSPENDED). write false: a get or list action,
allowed there. admin_allowed: a caller may let an admin through without asking Project. Names are unique."
When Provisioning confirms the `admin_allowed` flags, the README rule turns the header into `ACCEPTED`.

### b.5 Decisions

| G | Decision | Test | Options and cost if wrong |
| --- | --- | --- | --- |
| G11 | Contracts are pinned by full SHA and copied only by the sync script, as `web-block-commons` does | `scripts/contracts-sync.sh --check` passes | Copying by hand drifts silently from the shared repo |
| G12 | Stubs compile in their own source set; JaCoCo, Spotless, PMD and CPD keep covering `main` only; no exclusion is added | Boot jar holds `webblock/project/v1/AccessServiceGrpc.class`; coverage rules unchanged | Generating into `main` would need a coverage exclusion (Art. V forbids one to pass) |
| G13 | Plain grpc-java with a lifecycle bean, not a gRPC Spring starter | `GrpcServerTest`: starts on the port, serves every service bean, stops gracefully | A starter adds a dependency and its own defaults to review; this is about 100 lines |
| G14 | The server lives in `common/presentation/grpc` (S2 §6.1: presentation holds gRPC servers), so infrastructure never uses presentation | ArchUnit layer rules | In infrastructure it would break `infrastructureDoesNotUsePresentation` |
| G15 | Inbound message at most 64 KiB, metadata at most 16 KiB (the REST header limit) | A 65 KiB request answers `RESOURCE_EXHAUSTED` | gRPC's 4 MiB default lets any mesh caller send large bodies |
| G16 | Every `ErrorCode` has one status (b.2); the mapping is a `switch` over the enum, so a new code without a status fails to compile | `GrpcErrorsTest` over all codes; FR-31.5, FR-31.6 | A missed code would answer `UNKNOWN` |
| G17 | CheckAccess uses the REST request record, its validation and `AccessCheckService`; never `NOT_FOUND` | FR-31.1 to FR-31.5; the same cases as `AccessCheckControllerTest` | Two rule sets would drift between REST and gRPC |
| G18 | The subject is only the `user_id` field; `x-user-id` metadata and roles are not read; admins get no extra answer | FR-33.1 | The caller check of CMN §4.2 comes with the library (FRD note) |
| G19 | `PROJECT_DELETED` and `PROJECT_SUSPENDED` are codes for callers; CheckAccess returns them as `reason` in an OK answer; Project's REST keeps 422 `PROJECT_READ_ONLY` | FR-33.2, FR-31.4 | none |
| G20 | `AccessAction` is the one list for REST and gRPC; `AccessActionListTest` proves it equals the pinned `actions.v1.json` (names and `write` flags) and that the file passes its schema | FR-32.1 to FR-32.3 | Without the test the enum and the shared file drift |
| G21 | Each action carries a boolean `write`: true for every action that changes state, false for the get and list actions. `web-block-commons` maps `PROJECT_DELETED` and `PROJECT_SUSPENDED` to 409 only for write actions; reads get 404. `write` replaces a `kind` field, which would hold the same fact twice | The schema and G20's test | Leaving it out hides Project's rule from callers; two fields for one fact could disagree |
| G22 | ArchUnit gains two rules: every `BindableService` implementation sits in a `presentation.grpc` package, and only `presentation.grpc` classes use `io.grpc` or the generated `webblock..` types | `ArchitectureRulesTest` | Without them gRPC types could leak into services and the domain |

FR-31.6 needs a table lock: a plain read does not wait on a row lock, so the test holds `LOCK TABLE project.projects
IN ACCESS EXCLUSIVE MODE` in a second connection and expects `UNAVAILABLE`, reason `DEPENDENCY_UNAVAILABLE`.

### b.6 Mesh notes for `web-block-config` (the owner applies them)

- Deployment: container port 9090 named `grpc`. Service `project`: port 9090 named `grpc` (S2 §3.1).
- `AuthorizationPolicy` of Project: one rule, from principal `cluster.local/ns/webblock/sa/provisioning-api`, to
  path `/webblock.project.v1.AccessService/CheckAccess` (S2 §3.8). The portal caller waits for O-5.
- The REST rule for `POST /internal/v1/access-checks` stays until FR-34. STRICT mTLS stays.
- On Kind there is no Istio yet, so port 9090 is open there, as `/internal/**` is today.

### b.7 Tests

`access/presentation/grpc/AccessGrpcServiceTest` (in-process server; FR-31.1 to FR-31.5, FR-32.1 to FR-32.3,
FR-33.1); `access/presentation/grpc/CheckAccessIntegrationTest` (Netty on a free port, PostgreSQL; FR-31.6);
`common/presentation/grpc/GrpcErrorsTest` and `GrpcServerTest` (G15, G16); `common/domain/error/J3ErrorCodesTest`
(FR-33.2); `access/application/model/AccessActionListTest` (G20); `AccessCheckServiceTest` and
`AccessCheckControllerTest` for `manage_site` over REST (FR-32.1, FR-32.2).

## c. The 6.1 layout move (TR-4, TR-5)

### c.1 Type folders

| Layer | Before | After |
| --- | --- | --- |
| presentation | `controller`, `request`, `response`, `validation` | `controller`, `grpc` |
| application | `service`, `model`, `port`, `job`, `config` | `service`, `model`, `port`, `job`, `config`, `request`, `response`, `validation` |
| domain | `entity`, `model` | unchanged |
| infrastructure | `config`, `persistence`, `client`, `messaging`, `standin` | unchanged |
| common only | presentation `envelope`, `error`, `filter`, `security`; domain `error`, `support`; infrastructure `security` | unchanged |

`TYPE_FOLDERS` changes in the same commit as the moves. Nothing is loosened: `request`, `response` and `validation`
leave presentation and join application; `grpc` holds only gRPC servers and their mappers (G22). The new packages get
no `@NamedInterface`, so Modulith refuses any other feature using them.

### c.2 Class map (package change only, no behaviour change)

| Old path (under `src/main/java/org/kshrd/webblock/project/`) | New path |
| --- | --- |
| `access/presentation/request/AccessCheckRequest` | `access/application/request/AccessCheckRequest` |
| `catalog/presentation/response/BusinessTypeResponse` | `catalog/application/response/BusinessTypeResponse` |
| `generation/presentation/request/CaptureGenerationRequest` | `generation/application/request/CaptureGenerationRequest` |
| `generation/presentation/request/CreateGenerationContractRequest` | `generation/application/request/CreateGenerationContractRequest` |
| `generation/presentation/response/CaptureResponse` | `generation/application/response/CaptureResponse` |
| `page/presentation/request/CreatePageRequest` | `page/application/request/CreatePageRequest` |
| `page/presentation/request/UpdatePageRequest` | `page/application/request/UpdatePageRequest` |
| `page/presentation/response/EditRevisionResponse` | `page/application/response/EditRevisionResponse` |
| `page/presentation/response/PageListResponse` | `page/application/response/PageListResponse` |
| `page/presentation/response/PageResponse` | `page/application/response/PageResponse` |
| `page/presentation/response/PageWriteResponse` | `page/application/response/PageWriteResponse` |
| `project/presentation/request/RenameProjectRequest` | `project/application/request/RenameProjectRequest` |
| `template/presentation/response/TemplateResponse` | `template/application/response/TemplateResponse` |
| `wizard/presentation/request/BusinessProfileRequest` | `wizard/application/request/BusinessProfileRequest` |
| `wizard/presentation/request/CreateProjectRequest` | `wizard/application/request/CreateProjectRequest` |
| `wizard/presentation/validation/TimeZoneId` | `wizard/application/validation/TimeZoneId` |
| `wizard/presentation/validation/TimeZoneIdValidator` | `wizard/application/validation/TimeZoneIdValidator` |
| `common/presentation/validation/IdempotencyKey` | `common/application/validation/IdempotencyKey` |
| `common/presentation/validation/IdempotencyKeyValidator` | `common/application/validation/IdempotencyKeyValidator` |
| `common/presentation/validation/PagePath` | `common/application/validation/PagePath` |
| `common/presentation/validation/PagePathValidator` | `common/application/validation/PagePathValidator` |

Tests move with them: `wizard/presentation/request/BusinessProfileRequestTest` to `wizard/application/request/`;
`common/presentation/validation/ConstraintMessageTest`, `IdempotencyKeyValidatorTest`, `PagePathValidatorTest` to
`common/application/validation/`.

### c.3 Decisions

| G | Decision | Test | Options and cost if wrong |
| --- | --- | --- | --- |
| G23 | The validation annotations move too: a request in application may not use presentation (`applicationDoesNotUsePresentation`), and the `common` ones are used by requests | `ArchitectureRulesTest` | Leaving them breaks the layer rule |
| G24 | No `mapper` folder and no mapper class for REST: mapping stays in static factories and `toCommand` (`.claude/rules/java-code.md`); a `mapper` folder comes with its first class. Only gRPC has a mapper, in `presentation/grpc` as S2 §6.1 shows | `ArchitectureRulesTest` | Empty mapper classes would only copy code |
| G25 | Command and model records stay; services keep their signatures. S2 §6.1 says Project keeps its feature-first layout | The whole test suite passes unchanged apart from imports | Passing request records into services would be a larger change with no FR |

## Guard: `scripts/` counts as code (TR-13)

| G | Decision | Test | Options and cost if wrong |
| --- | --- | --- | --- |
| G26 | `CODE_PATHS` in `.claude/hooks/guard.py` adds `scripts/`; nothing else changes | `test_guard.py`: a commit of `scripts/x.sh` with no ID asks; with `TR-13` it passes | The owner confirms the hook edit (the hook asks) |

## d. Order of work, gates, risks

### d.1 Steps

Gate sets: **fast** `./gradlew spotlessCheck test`; **full** `./gradlew check`; **QA** `./gradlew pmdMain pmdTest
spotbugsMain cpd` with no new untriaged finding against the reports before the step; **OSV** `scripts/osv-scan.sh`;
**spec** `python3 -I scripts/spec-check.py`, 0 errors; **contracts** `scripts/contracts-sync.sh --check`. Each code
step writes its failing tests first and ends with a validator review.

| # | Commit | Content | Gates and approvals |
| --- | --- | --- | --- |
| 1 | `Decisions: 0010 Java layout, 0011 gRPC server and contracts (TR-5, TR-10)` | Records and index (TRD Q-1) | docs checks, spec |
| 2 | `Hooks: commit ID check covers scripts/ (TR-13)` | `guard.py`, `test_guard.py` | `python3 -I .claude/hooks/test_guard.py`, spec; owner confirms the hook edit |
| 3 | `Layout: request, response and validation types in application (TR-5, FR-4)` | c.1, c.2, `ArchitectureRulesTest` | full, QA, spec |
| 4 | `Projects: migration V6 and the sub-domain at creation (FR-36, FR-13)` | a.1 to a.4 in one commit, since V6 makes `subdomain` required at insert | full, QA, spec; then `db-notion-audit` 0 DIFFERENT and the DB change report |
| 5 | `Build: gRPC dependencies and the pinned contracts (TR-10, TR-11, FR-31)` | b.1, `contracts.lock`, `contracts/`, `scripts/contracts-sync.sh`, lock and verification files, `dependency-versions.md` | the owner approves the versions first; `resolveAndLockAll --write-locks`, `--write-verification-metadata sha256 check`, OSV, contracts, boot jar content, spec; security review |
| 6 | `Access: gRPC CheckAccess on port 9090 (FR-31, FR-33)` | b.2, b.3 without `MANAGE_SITE`, G22 rules | full, QA, spec; security review |
| 7 | In `web-block-contracts`: `Access: CheckAccess action list v1, DRAFT (FR-32)` | b.4 files and README row | schema check of the example; the owner pushes |
| 8 | `Access: manage_site and the shared action list (FR-32)` | `AccessAction`, lock move, sync list, `AccessActionListTest` | full, contracts, spec |
| 9 | `Docs: J3 alignment facts (FR-36, FR-31, TR-5)` | Docs listed below, by docs-keeper | docs checks, spec |

Then the workflow's closing steps: Kind check in an owner-given slot (the migration and the REST check), QA sweep,
direction check, final review, gate 4.

### d.2 Docs and other services

- This repo (step 9): `.claude/CLAUDE.md` (structure, current facts, port 9090, sync command),
  `.claude/rules/java-code.md` (folders, gRPC), `.claude/rules/migrations.md` and the `new-migration` skill (the
  planned list is applied; last file V6), the `spring-endpoint` skill, `.claude/docs/runbooks/database.md` (columns,
  grants), `.claude/docs/runbooks/migrations.md` (stop the old version before V6), `.claude/TECH_STACK.md`,
  `.claude/docs/contracts/provisioning-contract.md` §B (gRPC served, same meaning), `ai/handoffs/next.md`.
- Owner: the mesh notes of b.6, in `web-block-config` (TRD Q-5).
- `web-block-contracts`: b.4. `web-block-commons`: the same grpc-java version; its prerequisites P-1 and P-2 are met
  by steps 6 and 7. Provisioning: confirms the `admin_allowed` flags; its gRPC client is its own work.
- Frontend: the wizard sends `subdomain`.

### d.3 Risks

- The previous service version cannot read V6 (renamed columns, no `domains`): it stops before the job runs (TR-39).
- The pre-check stops V6 if two existing IDs share a prefix (O-6).
- Site addresses change for existing projects (G10, O-4).
- Generated code could trip an Error Prone error; then stop and ask, never switch Error Prone off.
- The grpc-java versions are not pinned anywhere yet; the first repo to pin sets them for both.
- Step 8 waits on the owner's push of step 7.
- Coverage: `AccessCheckService` and `ProjectService` keep 80%; new `common` classes count toward its 80%.

## Constitution check

| Article | Result | Note |
| --- | --- | --- |
| I Spec-driven flow | pass | Every step cites FR or TR IDs; G26 closes the `scripts/` gap |
| II Tech stack | pass | gRPC (grpc-java, protobuf, protoc) with the pinned contracts, as Art. II (Version 2) allows; decision `0011` |
| III Architecture limits | pass | Request, response and validation types in `application/` as Art. III states, with `TYPE_FOLDERS` in the same commit and two added rules (decision `0010`); V6 equals S1; nothing invented for another service |
| IV Coding conventions | pass | Constructor injection, package-private wiring, `WebBlockException` for refusals |
| V Testing and quality | pass | Tests first with IDs; gates unchanged; no coverage exclusion (G12) |
| VI Safety guardrails | pass | No cluster command; mesh changes are notes; the hook edit asks the owner; no secret |
| VII Docs | pass | Current facts, plain English |
| VIII Agents | pass | One implementer at a time; reviews by others; gates 2 to 4 with the owner |

## Owner answers at gate 2

| ID | Question | Answer |
| --- | --- | --- |
| O-1 | Amend Art. II for gRPC? | Amended (constitution Version 2) |
| O-2 | Rebuild `project.projects` for column order? | Rebuild |
| O-3 | `ProjectView` is used by the project and wizard controllers; Art. III says responses are never shared | Keep it shared: it is the module's one website representation in `project/application/model` (G9) |
| O-4 | When does Provisioning start routing by sub-domain, relative to V6? | In the same owner-run rollout as V6, proved together on Kind first |
| O-5 | Who besides `provisioning-api` may call CheckAccess? | Only `provisioning-api` (S2 §3.8); the portal is added when its identity is decided (S2 §9 point 3) |
| O-6 | If existing IDs collide on the backfill prefix, what values? | Before the rollout the owner runs the read-only count `SELECT left(project_id::text, 12), count(*) FROM project.projects GROUP BY 1 HAVING count(*) > 1`; any row it returns stops the rollout for an owner ruling |
| O-7 | Art. III still says request types sit in presentation | Amended: request, response and validation types sit in `<feature>/application/` |
| O-8 | Should `contracts/` and `contracts.lock` count as code for the commit ID check? | Yes, in a later small change of `spec-driven.md` and the guard, not in this design |
