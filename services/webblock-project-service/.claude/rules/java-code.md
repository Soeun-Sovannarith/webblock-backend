---
paths:
  - "src/main/java/**"
---

# Java code rules (owner rules)

Java 25, Spring Boot 4.1.1, Spring Modulith 2.1.1 (event externalization only). CLAUDE.md section 3 gives the short
form; this file is the full one. The rules marked (ArchUnit) are tests in
`src/test/java/org/kshrd/webblock/project/architecture/` and fail `./gradlew test`; never loosen one, add or tighten
only, with the owner's OK.

## Layers, features and folders (Notion architecture 6.1, decision `0016`)

- Layer first: the top packages under `org.kshrd.webblock.project` are `presentation`, `application`, `domain`,
  `infrastructure` and `common`. Every class sits in one of the four layers or in common's four layers (ArchUnit
  `featureClassesSitInALayer`).
- Each class sits directly in one type folder and one feature folder of its layer (ArchUnit
  `layerClassesSitInATypeFolder`, `TYPE_FOLDERS`): `presentation/{rest,grpc,messaging}/<feature>`,
  `application/<feature>/{handler,model,port,job,config,request,response,validation}`, `domain/<feature>/model`,
  `infrastructure/{config,persistence,client,messaging,standin}/<feature>`. `common` keeps
  `common/<layer>/<type>` with its own folders (`COMMON_TYPE_FOLDERS`): presentation `envelope`, `error`, `filter`,
  `grpc`, `security`; application `config`, `validation`; domain `error`, `support`; infrastructure `config`,
  `security` (and the shared folder names).
- Features are not Spring Modulith modules: no `@ApplicationModule`, no `@NamedInterface`. Another feature's types
  are used through its `application` classes by convention; there is no per-feature isolation rule.
- `common` depends on no feature (ArchUnit `commonDependsOnNoFeature`).
- Direction (Java form of 6.1): presentation uses application; application uses domain and infrastructure;
  infrastructure uses domain and implements application ports. Presentation never uses infrastructure or a feature's
  domain (`presentationDoesNotUseDomain`; it may use common's), application and infrastructure never use
  presentation, domain uses no other layer (ArchUnit).
- The domain holds entities (`@Entity` allowed), enums and value rules, and no repository. It may use only the JDK,
  `jakarta.persistence`, Hibernate mapping types, `org.springframework.data.domain`, `com.fasterxml.uuid` and
  domain classes (ArchUnit `domainUsesOnlyAllowedLibraries`).
- Repositories are Spring Data JPA interfaces in `infrastructure/persistence/<feature>`, used directly by that
  feature's handlers. No repository interface in application or domain. Controllers never use a repository
  (ArchUnit `controllersDoNotUseRepositories`).
- Use cases are the `*Service` classes in `application/<feature>/handler`; they stay whole until they are split into
  one handler per use case.
- Request, response and mapping are per feature: request records with Bean Validation annotations, response
  records, and the mapping in a static factory (`ProjectView.of`) or the request (`toCommand`). Never share a
  request or response type across features. Project is not CQRS.

## Spring conventions

- Constructor injection only (ArchUnit `noFieldInjection`). Controllers and services keep one constructor.
- Controllers are package-private, thin, and call one application service (handler). A feature's `@Configuration` is
  package-private (ArchUnit `featureConfigurationIsPackagePrivate`) and uses `proxyBeanMethods = false`.
- Settings are `@ConfigurationProperties` records under `webblock.*`, in `application/<feature>/config` or
  `infrastructure/config/<feature>`. Every new setting goes into `application.yml`; `ProfileGuard` checks what
  production needs.
- Transactions on application service methods: `readOnly = true` for reads, `Propagation.MANDATORY` for a step the
  caller's transaction must hold. `open-in-view` is off and Hibernate only validates the schema (`ddl-auto: validate`).
- Business time comes from the injected `Clock` (`common/infrastructure/config/TimeConfig`), never `Instant.now()`.
- Logs through SLF4J; no `System.out` or `java.util.logging` (ArchUnit). Never log a token, a request body, personal
  data or a `Failing row contains` detail.

## Errors, responses and security

- Formats (decision `0003`): REST bodies camelCase, events snake_case, typed ID names (`projectId`, never `id`).
- Every refusal is `new WebBlockException(ErrorCode.X)` (with `ErrorDetail` for fields). `GlobalExceptionHandler`
  writes RFC 9457 Problem Details. A database constraint maps to an `ErrorCode` through the feature's
  `ConstraintErrorRegistration` bean. New codes go into `common/domain/error/ErrorCode`.
- Success bodies are wrapped by `ResponseEnvelopeAdvice`; nothing else may use `ApiResponse` (ArchUnit
  `onlyTheEnvelopeAdviceUsesApiResponse`). Lists return `PagedResult` with a cursor.
- Not the owner, or not found, is `PROJECT_NOT_FOUND` (404), never 403; the internal access check answers
  `allowed: false` with reason `NOT_FOUND` for an unknown project and `NOT_MANAGER` for a caller who is not the
  owner, and callers map both to 404 (Art. III, decision `0007`). The user comes from `CurrentUser`.
- No method security: `@PreAuthorize` and its kin are banned (ArchUnit `noMethodSecurityAnywhere`). Authorization
  lives in `SecurityConfig`'s filter chains and the services' owner checks. `/internal/**` takes no token; the
  Istio mesh policy is its gate (decision `0002`, `.claude/docs/contracts/authentication-contract.md`).
- Stand-ins implement `StandIn` and are `@Profile("prototype")` only (ArchUnit
  `standInsAreOnlyInThePrototypeProfile`); production refuses them (`ProfileGuard`).
- Outgoing HTTP only in the `payment`, `generation` or `publication` folders of infrastructure, with bounded
  timeouts (ArchUnit `onlyIntegrationModulesCallExternalHttpClients`). Never forward a token to another service.

## Events and durable work

- Events leave through the Namastack outbox (Modulith externalization, mode `outbox`) on the `project.events` topic
  exchange; handled messages are recorded in `project_messaging.processed_messages`. No
  `@ApplicationModuleListener` (ArchUnit `noApplicationModuleListeners`): the publication registry is excluded
  (decision `0001`, `.claude/docs/runbooks/outbox.md`).
- One deployable: API, listeners, outbox relay and jobs run in one process (decision `0008`).
- Store the change and its outbox row in one transaction. No transaction open across a remote call.

## Quality

- Format with `./gradlew spotlessApply` (palantir-java-format, unused imports removed). Error Prone runs in every
  compile: no new warning, no broad `@SuppressWarnings`; a narrow one names the check and the reason.
- No deprecated API: replace it, do not suppress it. Check `./gradlew compileJava` output for deprecation notes.
- No dead or duplicated code: delete what nothing calls; never keep a second copy of a rule, a query or a parser.
  No TODO stubs or fake success: an unsupported case is an explicit `WebBlockException`.
