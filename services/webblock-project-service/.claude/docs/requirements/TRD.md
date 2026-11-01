# Project service: Technical Requirements (TRD)

**Status:** Approved at gate 2 by the owner

## In short

Project is one Spring Boot service in four layers, each split by feature. Its data lives in its own
PostgreSQL database and in the shared catalog database, both equal to the Notion design. Clients call it over REST
with a Keycloak token; other services call `/internal` REST or gRPC inside the Istio mesh with no token, and it
sends events through a transactional outbox to RabbitMQ. A separate job migrates the databases before each rollout.

**Read next:** [DIRECTION.md](DIRECTION.md), whether the work matches these requirements. Up: [FRD.md](FRD.md).

## Modules and layers

The code is grouped by layer first, then by type folder and feature, as Notion "Web Block architecture" 6.1 shows
for Java (decision `0016`).

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-1 | One deployable: the REST API, the gRPC server, the listeners, the outbox relay and the durable jobs run in one process | All FR | 0008 | 0008, S2 §6.1 |
| TR-2 | Each feature (`access`, `catalog`, `generation`, `messaging`, `migration`, `page`, `payment`, `project`, `template`, `wizard`) is a folder inside each layer, not a Spring Modulith module; another feature is used through its `application` classes; `common` uses no feature | All FR | 0016 | Art. III, `ArchitectureRulesTest.java`, `.claude/rules/java-code.md` |
| TR-3 | Every class sits in `presentation`, `application`, `domain` or `infrastructure` (or common's four layers); presentation never uses infrastructure or a feature's domain, application and infrastructure never use presentation, the domain uses no other layer and only allowed libraries | All FR | none | S2 §6, `ArchitectureRulesTest.java` |
| TR-4 | Inside a layer every class sits in one type folder and one feature folder (`TYPE_FOLDERS`): `presentation/{rest,grpc,messaging}/<feature>`; `application/<feature>/{handler,model,port,job,config,request,response,validation}`; `domain/<feature>/model`; `infrastructure/{config,persistence,client,messaging,standin}/<feature>`; common keeps `common/<layer>/<type>` (`COMMON_TYPE_FOLDERS`). Classes generated from protos live outside the base package and only `presentation/grpc` uses them | All FR | 0016, 0011 | S2 §6.1, `.claude/docs/designs/j3-notion-alignment.md` |
| TR-5 | The Java code follows the layer-first tree of Notion 6.1 (decision `0016`). Request, response and validation types live per feature in `application/<feature>/{request,response,validation}` and no other feature uses them (a review rule); use cases are the `*Service` classes in `application/<feature>/handler` until the split into one handler per use case; mapping is a static factory (`ProjectView.of`) or `toCommand` | FR-4, FR-8 to FR-10, FR-13, FR-16 to FR-20, FR-22, FR-24, FR-30, FR-31 | 0016 | S2 §6.1, Art. III |
| TR-6 | Spring Data repositories live in `infrastructure/persistence/<feature>` and that feature's application services use them directly; the domain holds entities and rules and no repository; Project is not CQRS | All FR | none | S2 §6.1, Art. III |

## Stack

The stack is fixed by the constitution; exact versions come only from `gradle/libs.versions.toml` and the lockfile.

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-7 | Java 25, Spring Boot 4.1.1, Spring Modulith 2.1.1, Gradle 9.8.0 wrapper. Every dependency is open source, at least 14 days old, pinned exactly, locked, in `gradle/verification-metadata.xml`, recorded in `.claude/docs/dependency-versions.md`, and `scripts/osv-scan.sh` is clean | All FR | none | Art. II, `.claude/TECH_STACK.md` |
| TR-8 | Spring Data JPA on Hibernate with `ddl-auto: validate` and `open-in-view` off; PostgreSQL 18; Flyway for both databases | All FR | none | `application.yml`, `.claude/TECH_STACK.md` |
| TR-9 | Spring AMQP with the Namastack outbox through Modulith event externalization (mode `outbox`); no `@ApplicationModuleListener` | FR-12, FR-26, FR-27, FR-35 | 0001 | 0001 |
| TR-10 | gRPC with grpc-java (`grpc-api`, `grpc-stub`, `grpc-protobuf`, `grpc-netty-shaded`; `grpc-inprocess` for tests), `protobuf-java` and `proto-google-common-protos`; the protobuf Gradle plugin with `protoc` and `protoc-gen-grpc-java` generates the stubs in their own `contracts` source set. One grpc-java version, the same `web-block-commons` pins | FR-31, FR-33 | 0011 | S2 §3, CMN `PLAN.md` Q5 |
| TR-11 | The service copies what it uses from `web-block-contracts` at the full commit SHA in `contracts.lock` into `contracts/`; only `scripts/contracts-sync.sh` writes that folder and its `--check` fails on any difference | FR-31, FR-32 | 0011 | CTR `README.md` |
| TR-12 | `./gradlew check` runs `spotlessCheck`, Error Prone, `test` (ArchUnit included), `integrationTest` and the coverage rules (70% of lines, 80% in `common`, 80% per `application.*.handler.*Service` and `domain.*.model.*Entity`); PMD, CPD and SpotBugs are report-only | All FR | 0009 | Art. V, `build.gradle` |
| TR-13 | The guard hook asks before a commit that changes `src/main/`, `src/test/`, `build.gradle`, `gradle/` or `scripts/` with no FR or TR ID in its message | All FR | none | `.claude/rules/spec-driven.md`, `.claude/hooks/guard.py` |

## Data

Project owns two databases; the tables, columns and grants are designed only in Notion, so this file points there.

- `project.projects`: one website and its owner (S1 projects).
- `project.pages`: the latest saved design of one page (S1 pages).
- `project.generation_contracts` and `project.generation_contract_schemas`: frozen generation rules (S1).
- `project.generation_requests`: one generate-and-publish attempt (S1).
- `project_messaging`: `processed_messages`, the Namastack outbox tables and `pending_catalog_registrations` (S1
  Background storage). `project_ops`: Flyway history only.
- Shared catalog database, schema `catalog`: business types, product types and schemas, websites, business profiles
  and site content (S3).

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-14 | Both databases equal S1 and S3: every table, column with its type, NULL rule and position, enum value, index, constraint, role and grant, and nothing more; the `db-notion-audit` skill finds zero differences before a merge | FR-35, FR-36 | none | Art. III, S1, S3 |
| TR-15 | `project_app` reads and inserts business rows and updates only the columns S1 "Who may write" lists, never deletes; `project_migrator` owns every object; `catalog_platform` registers websites; `catalog_owner` migrates the catalog; `PUBLIC` has nothing | FR-35, FR-36 | 0005 | S1 Who may write, S3, `.claude/docs/runbooks/database.md` |
| TR-16 | Flyway migrations are forward-only and never edited; project files run only as `project_migrator`, catalog files only as `catalog_owner`; production migrates only in the separate job | FR-36 | 0005 | 0005, `.claude/rules/migrations.md` |
| TR-17 | Migration V6 rebuilds `project.projects` in S1 column order with `subdomain` (backfilled, NOT NULL, unique over all rows, frozen by the guard trigger), renames the repository columns, drops `project.domains` with its two enums, makes the owner index cover all projects and grants exactly S1's list | FR-13, FR-36 | none | S1 Planned, `.claude/docs/designs/j3-notion-alignment.md` |
| TR-18 | Frozen columns are guarded by `BEFORE UPDATE` triggers that raise `check_violation` with a constraint name; each feature maps the constraint names users can hit to an `ErrorCode` in its `ConstraintErrorRegistration` bean | FR-10, FR-13, FR-16, FR-36 | none | `V1__project_schema.sql`, `.claude/rules/java-code.md` |
| TR-19 | Saves move `edit_revision` with one conditional UPDATE under the project row lock; a partial unique index keeps one generation request in flight; every pooled connection sets `statement_timeout` 10 s and `lock_timeout` 5 s, and a timeout answers `DEPENDENCY_UNAVAILABLE` | FR-5, FR-18 to FR-20, FR-24 | 0004 | S1 pages, `application.yml` |
| TR-20 | A change and its event or durable job are stored in one transaction as a Namastack record and sent after commit; handled messages are recorded once in `processed_messages`; records are kept | FR-12, FR-26 to FR-28, FR-35 | 0001 | 0001, `.claude/docs/runbooks/outbox.md` |
| TR-21 | The catalog is reached through its own pool as `catalog_platform`; the business profile waits in `pending_catalog_registrations` until the registration job ends | FR-8, FR-9, FR-22, FR-23, FR-29, FR-35 | 0001 | S1, S3, `.claude/docs/runbooks/catalog-database.md` |

## Interfaces

Clients use REST; other services use internal REST and gRPC; events leave on RabbitMQ.

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-22 | REST `/api/v1`: camelCase bodies, typed ID names, the success envelope `{ success, data, meta }`, RFC 9457 Problem Details with `code`, `errors`, `requestId`, `traceId`; lists page with a cursor | FR-1 to FR-5, FR-8 to FR-25 | 0003 | 0003, S2 §2 |
| TR-23 | Internal REST `/internal/v1`: `POST access-checks` (until FR-34) and `GET generation-requests/{id}/codegen-input` | FR-6, FR-29, FR-30 | 0002 | 0002, PROV §B, COMM §2.2 |
| TR-24 | One gRPC server on port 9090 (Service port `grpc`) in the same process registers every gRPC service bean and refuses to start if one sits outside a `presentation.grpc` package of this service; virtual-thread executor; inbound message at most 64 KiB, metadata at most 16 KiB; no reflection or health service. At most `webblock.grpc.max-calls-in-flight` calls run at once (default 8, below the project pool of 10 that REST shares); a call over it answers `UNAVAILABLE`, `DEPENDENCY_UNAVAILABLE`, at once. A call takes its place only when its handler is about to run (at half-close); a call that has started but not half-closed holds none. Per connection Netty allows 100 calls, closes it idle 5 min or older than 30 min (10 s grace), accepts a ping at most once a minute and only with calls open, and closes it after 200 stream resets in 10 s. A call cancelled or past its deadline does no lookup. A busy database (lock wait, statement timeout, no pooled connection in time) answers `DEPENDENCY_UNAVAILABLE` in gRPC and REST | FR-31 | 0011 | 0011, S2 §3.1, `.claude/docs/runbooks/grpc.md` |
| TR-25 | gRPC errors: the status of S2 §2.3 for the code's category, with `PROJECT_DELETED` and `PROJECT_SUSPENDED` as `FAILED_PRECONDITION`; one `ErrorInfo` (`reason` = code, `domain` `project.webblock`, `metadata.request_id`) and a `BadRequest` for field problems; `x-request-id` is read or created. A method the server does not serve answers `UNIMPLEMENTED`, reason `ROUTE_NOT_FOUND`, with `ErrorInfo` and the request ID, and never echoes the path; refusals the transport makes first (size, unparsable bytes, connection limits) stay grpc-java's own and hold sizes only | FR-31, FR-33 | 0011 | 0011, S2 §3.4, §3.5, CMN `PLAN.md` Q2 |
| TR-26 | `webblock.project.v1.AccessService/CheckAccess` maps the proto request to the access feature's request record, checks it with the same rules as REST and asks the same `AccessCheckService`; the subject is the `user_id` field only | FR-31, FR-33 | 0007 | S2 §3.2, CTR `access.proto` |
| TR-27 | The accepted actions are one enum, `AccessAction`; a test proves it equals `access/actions.v1.json` from the pinned contracts (names and `write` flags) | FR-30, FR-32 | 0011 | CMN `PLAN.md` Q1, `.claude/docs/designs/j3-notion-alignment.md` |
| TR-28 | Events go to the topic exchange `project.events` (alternate exchange `project.events.unrouted`) with snake_case bodies and the envelope; routing key is the event name without the service prefix | FR-27 | 0001, 0003 | COMM §2.1, S2 §4 |
| TR-29 | Outgoing HTTP only from `payment`, `generation` or `publication` infrastructure with bounded timeouts; calls carry the user ID, never a token; the Payment stand-in exists only in `prototype` | FR-7, FR-11, FR-12, FR-26 | 0002 | PAY §2, `ArchitectureRulesTest.java` |
| TR-40 | In the `prototype` profile only, springdoc-openapi serves an OpenAPI 3 description of the user REST API `/api/v1/**` (JSON at `/v3/api-docs`) and Swagger UI at `/swagger-ui/index.html`; both are off in `application.yml` and on in `application-prototype.yml`, and `ProfileGuard` refuses to start `production` with either on. `/internal/**` is never in the description. The doc paths take no token in `prototype`; `/api/**` keeps its token check (TR-30). A Bearer security scheme lets a caller paste a dev token or a Keycloak token. The doc JSON is not wrapped in the success envelope. The description reflects the code; S2 stays the authority for the API contract | FR-1 to FR-5, FR-8 to FR-25 | none | `application-prototype.yml`, `ProfileGuard`, Art. II, S2 §2 |

## Security

The gateway checks the user's token, Project checks it again, and inner calls rely on the mesh identity.

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-30 | `/api/**` checks the Keycloak token again in `common/infrastructure/security/`: RS256 from the key set, issuer, audience `project`, `typ` `Bearer`, `exp`, `nbf`; `user_id` must be a canonical UUID | FR-1, FR-2 | 0002 | AUTH §5, S2 §2.4 |
| TR-31 | `/internal/**` and the gRPC port read no token; STRICT mTLS and the Istio `AuthorizationPolicy` (caller principal and path) are the only gate | FR-6, FR-31 | 0002 | 0002, S2 §3.8 |
| TR-32 | Not the owner answers 404 `PROJECT_NOT_FOUND`, checked in the services; the access check answers `allowed: false` with `NOT_FOUND` for an unknown project and `NOT_MANAGER` for a caller who is not the owner, and every caller maps both to 404 `PROJECT_NOT_FOUND`; method security is banned (ArchUnit) | FR-3, FR-30, FR-31, FR-33 | 0007 | 0007, Art. III |
| TR-33 | Tokens, request bodies, personal data and `Failing row contains` details are never logged or put in an error; pgjdbc runs with `logServerErrorDetail=false` | FR-4, FR-7 | 0002 | 0002, `application.yml` |
| TR-34 | Exactly one profile, `prototype` or `production` (`ProfileGuard`); stand-ins only in `prototype`; production holds no migrator password, refuses test domains and an empty reserved sub-domain list; the one sub-domain rule (3 to 55 characters, no `-preview` ending, no `xn--` start, not reserved) is `Subdomain` | FR-1, FR-11, FR-13 | 0005 | 0005, PROV §C |

## Non-functional needs

Every wait is bounded, background work retries, and the service exposes health and metrics.

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-35 | Every database statement and lock wait and every outgoing call is bounded (PAY timeouts, 10 s statements, 5 s locks); gRPC callers set a 2 s deadline and fail closed | FR-5, FR-11, FR-26, FR-31 | none | S2 §3.6, `application.yml` |
| TR-36 | Actuator `health`, `info` and `prometheus` on the management port 8081; outbox metrics; a request ID on every request, response, log line and gRPC error; traces come from Istio | All FR | none | S2 §7.3, `application.yml` |
| TR-37 | Durable jobs retry on their schedules (FR-12, FR-26, FR-35) and the outbox delivers at least once; a missing answer never counts as yes | FR-12, FR-26, FR-28, FR-35 | 0001, 0004 | 0001, 0004 |

## Deployment

This repository holds no chart; it ships one image and a migration job entry point.

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-38 | One container image with ports 8080 (HTTP), 9090 (gRPC) and 8081 (management); the Deployment, Service and mesh policies are defined in `web-block-config` and applied by the owner | All FR | none | S2 §3.1, §7 |
| TR-39 | The migration job runs before each rollout; V6 is not readable by the previous version, so that version stops before the job runs | FR-36 | 0005 | `.claude/docs/runbooks/migrations.md` |

## Constitution check

| Article | Result | Note |
| --- | --- | --- |
| I Spec-driven flow | pass | Every TR links to FR IDs; TR-13 extends the commit ID check to `scripts/` |
| II Tech stack | pass | TR-10 and TR-11 use gRPC (grpc-java, protobuf, protoc) and the pinned contracts, as Art. II allows; TR-40 adds springdoc-openapi 3.1.1 (owner-approved dependency, Apache-2.0), on only in the `prototype` profile |
| III Architecture limits | pass | TR-4 and TR-5 put request, response and validation types in `application/` as Art. III states, with `TYPE_FOLDERS` in the same commit; TR-14 and TR-17 keep the databases equal to S1 |
| IV Coding conventions | pass | No rule of `.claude/rules/java-code.md` changes except the type folders |
| V Testing and quality | pass | Gates unchanged; generated stubs sit in their own source set, no coverage exclusion is added |
| VI Safety guardrails | pass | No cluster command; mesh changes are notes for the owner; hook edit asks the owner |
| VII Docs | pass | Current facts, plain English |
| VIII Agents | pass | The owner approved this design at gate 2 |

## Drift seen

- CMN §4.1: no 503 `IDENTITY_UNAVAILABLE` when the key set is unreachable (see FRD Drift seen).

## Questions for the owner

Open, with proposals, in FRD "Questions for the owner": FRD Q-2 the sub-domain label rule (FR-13), FRD Q-3 who
commits to Gitea. Closed there: FRD Q-1 (Art. III allows `NOT_MANAGER`, TR-32) and FRD Q-4 (FR-37 sets `active`;
nothing sets `suspended`). The gate 2 answers, numbered in this file:

| ID | Question | Answer |
| --- | --- | --- |
| Q-1 | Which rules need a decision record? | `0010` (Java layout, the 6.1 move and the gRPC edge) and `0011` (gRPC server, errors, contracts pin, action list); the other rows rest on the constitution |
| Q-2 | Amend Art. II for gRPC? | Amended: Art. II allows gRPC between services with grpc-java, protobuf, `protoc` and the pinned contracts |
| Q-3 | Rebuild `project.projects` for S1 column order, or append `subdomain`? | Rebuild |
| Q-4 | Response-time or throughput targets? | None until J5 hardening; every wait stays bounded |
| Q-5 | Where is the deployment defined, and who adds the gRPC port and policy? | `web-block-config`; the owner applies it from the design's mesh notes |

## Glossary

| Word | Meaning here |
| --- | --- |
| Feature | A folder inside each layer such as `project` or `access`; not a Spring Modulith module |
| Type folder | The package inside a layer that says what a class is (`rest`, `handler`, `request`) |
| Outbox | Events and jobs stored in the same transaction as the change, sent later |
| Contracts pin | The commit of `web-block-contracts` this service copies its protos and lists from |
| Mesh policy | The Istio `AuthorizationPolicy` naming who may call which path |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "2 · Project" (read-only) |
| S2 | Notion "Web Block architecture" |
| S3 | Notion "6 · Shared catalog" (read-only) |
| AUTH, PAY, PROV, COMM | `.claude/docs/contracts/` |
| CMN | `web-block-commons/DESIGN.md` and `PLAN.md` |
| CTR | `web-block-contracts` (`README.md`, `proto/webblock/project/v1/access.proto`) |
| 0001 to 0015 | `.claude/docs/decisions/` |
| Art. N | `.claude/docs/CONSTITUTION.md` |
