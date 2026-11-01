---
name: spring-endpoint
description: Use when adding or changing a REST endpoint of the Project service (/api/v1 for users, /internal/v1
  for other services), or when a task touches a controller, a request or response body, the envelope, an error
  code, paging or the owner check. Written for this repo only.
---

# Add or change a REST endpoint

Generic Spring guides teach shapes this repo forbids (`@PreAuthorize`, `ResponseEntity<ApiResponse<...>>`,
`@ControllerAdvice` per feature, 403 for "not yours"). Follow this file. Paths below are under
`src/main/java/org/kshrd/webblock/project/` unless they start with `src/` or `.claude/`.

## Read first

| What | Where |
| --- | --- |
| A user endpoint, end to end | `presentation/rest/project/ProjectController.java`, `application/project/handler/ProjectService.java` |
| A write with 201, an edit revision, a body | `presentation/rest/page/PageController.java`, `application/page/request/`, `application/page/response/` |
| Idempotency-Key, 202 and replay | `presentation/rest/generation/GenerationRequestController.java` |
| An internal endpoint (no token) | `presentation/rest/access/AccessCheckController.java`, `presentation/rest/generation/CodegenInputController.java` |
| The envelope `{success, data, meta}` | `common/presentation/envelope/ResponseEnvelopeAdvice.java`, `ApiMeta.java`, `PagedResult.java` |
| Errors as RFC 9457 Problem Details | `common/presentation/error/GlobalExceptionHandler.java`, `ProblemDetailFactory.java` |
| Error codes, categories, field codes | `common/domain/error/ErrorCode.java`, `ErrorCategory.java`, `ValidationCodes.java`, `ErrorDetail.java` |
| Who is calling | `common/presentation/security/CurrentUser.java`, `common/infrastructure/security/SecurityConfig.java` |
| Shared validators | `common/application/validation/` (`@PagePath`, `@IdempotencyKey`) |
| The same check over gRPC (not REST) | `presentation/grpc/access/AccessGrpcService.java`, `common/presentation/grpc/GrpcServer.java`, `.claude/docs/runbooks/grpc.md` |
| Layer and folder rules the build enforces | `src/test/java/org/kshrd/webblock/project/architecture/ArchitectureRulesTest.java` |
| Rules | `.claude/rules/java-code.md`, `.claude/rules/testing.md`, decisions `0002`, `0003`, `0007`, `0016` |
| Wire shape for everyone | Notion "Web Block architecture" §2 (https://app.notion.com/p/40358175966e8198a088f123a9ab23fe) |

## Where each class goes (enforced by `ArchitectureRulesTest`)

| Class | Package (`<feature>` = access, catalog, generation, page, project, ...) |
| --- | --- |
| Controller, package-private, `@RestController` | `presentation.rest.<feature>` |
| Request record with Bean Validation | `application.<feature>.request` |
| Response record with `static of(<model>)` (the mapping) | `application.<feature>.response` |
| Custom constraint and validator | `application.<feature>.validation` |
| Use case: public `@Service` class, `@Transactional` methods | `application.<feature>.handler` (`*Service`) |
| Records the service takes and returns | `application.<feature>.model` |
| JPA entity, enum and value rules | `domain.<feature>.model` |
| Spring Data JPA repository, used by the service directly | `infrastructure.persistence.<feature>` |
| Feature wiring (package-private `@Configuration`) | `infrastructure.config.<feature>` |

Another feature is used through its `application` classes; its `request`, `response` and `validation` types stay
its own (features are not Modulith modules, so this is a review rule). Controllers never touch a repository. This
is the layer-first layout of Notion §6.1 (decision `0016`); a class outside the table above fails the build. A gRPC
service is not a REST endpoint: it sits in `presentation.grpc.<feature>` and follows decision `0011`.

## The rules every endpoint keeps

- **Paths:** users under `/api/v1/...`, other services under `/internal/v1/...`. Path IDs are typed `UUID`
  variables named after the thing (`projectId`, `generationRequestId`).
- **Success** is the envelope, added for you by `ResponseEnvelopeAdvice`: return the response record (200), a
  `ResponseEntity.status(HttpStatus.CREATED)` or `ACCEPTED` of it for 201 or 202, or a `PagedResult` for a list
  (items in `data`, `{limit, nextCursor}` in `meta.page`). Never build `ApiResponse` yourself (ArchUnit).
- **Names** the client sees are camelCase record components (`projectId`, `editRevision`); enum values go out as
  stored (`draft`). Never expose `ownerUserId` on user endpoints unless the FR asks.
- **Paging:** `limit` with `@Min(1) @Max(<MAX>)`, default 20; `cursor` with `@Size(max = 100)`, opaque, made and
  read only by the feature's cursor class (`application/project/handler/ProjectCursor.java`). Newest first.
- **Errors:** throw `new WebBlockException(ErrorCode.X)` (or with `ErrorDetail.of("field", ErrorCode.Y)` for a
  field). The status comes from the code's `ErrorCategory`. Messages are fixed per code and never echo input. A
  database constraint the user can hit maps to a code in the feature's `ConstraintErrorRegistration` bean.
- **Who:** `/api/**` needs a valid user token; take the user only from `CurrentUser.userId()` (never from the body
  or a header). `/internal/**` has no token at all: the Istio mesh policy is the gate, so never read a bearer
  there. No `@PreAuthorize` or `@Secured` anywhere (`noMethodSecurityAnywhere`). There is no `ADMIN` endpoint
  yet; the first one needs a decision record and a `SecurityConfig` change approved by the owner.
- **The owner rule:** a project that is not the caller's answers exactly like one that does not exist: 404
  `PROJECT_NOT_FOUND` (or `PAGE_NOT_FOUND`, `GENERATION_REQUEST_NOT_FOUND`), same body. Load through
  `ProjectService.getOwned` or `lockOwned` (inside a transaction), never `find` then compare in the controller. A
  deleted or suspended project is readable by its owner and refuses every change with `PROJECT_READ_ONLY` (`0007`).
- **Writes** that edit a project send the `editRevision` they last read; a stale one is 409
  `STALE_EDIT_REVISION`. Retried creates carry an `Idempotency-Key` header (`@IdempotencyKey`); a replay answers
  200 with the original result, the same key with another body is 409 `IDEMPOTENCY_KEY_REUSED`.
- **Contracts:** this repo has no OpenAPI document. An `/internal` endpoint or event another service uses is
  defined in `.claude/docs/contracts/` (`provisioning-contract.md`, `service-communication.md`,
  `payment-contract.md`); changing one needs the owner and that service's team first.

## Steps

Each change cites its FR ID; with no FR, stop and go to stage 1 (`.claude/WORKFLOW.md`).

1. **Tests first, with the FR ID** (`@DisplayName("FR-12.1: ...")`), and see them fail:
   - web slice: `@WebMvcTest(controllers = XController.class)`, `@Import(WebSliceSupport.Imports.class)`,
     `@MockitoBean JwtDecoder`, `@MockitoBean` the service, requests `.with(Users.user(OWNER))`; assert
     `$.data...`, `$.meta...`, and for errors the Problem fields (`$.code`, `$.status`, `$.errors[0].field`).
     Shape: `src/test/java/org/kshrd/webblock/project/presentation/rest/project/ProjectControllerTest.java`;
   - service: `src/test/java/org/kshrd/webblock/project/application/<feature>/handler/`;
   - database or concurrency: `@ServiceIntegrationTest` or `@JpaSliceTest` (`support/`), tagged `integration`.
   Cover 401 (no token), 404 for another user's project, 400 field codes, and each 409 or 422 the FR lists.
2. **Error code** (if new): add it to `ErrorCode` in the right group with its category and one fixed sentence; a
   field code also gets a `ValidationCodes` constant (`ErrorCodeCatalogTest` checks both).
3. **Request and response records** in `application/<feature>/request` and `application/<feature>/response`;
   validation messages use `ValidationCodes` constants; the response maps from the application model in a
   `static of(...)`.
4. **Service method** in `application/<feature>/handler`: owner check first, then the rule, then the repository;
   `@Transactional` (or `readOnly = true`); time from the injected `Clock`. New repository query methods go in
   `infrastructure/persistence/<feature>`.
5. **Controller method**: thin; validate (`@Valid`, constraint annotations on parameters), call one service
   method with `currentUser.userId()`, map to the response.
6. **Gates**: `./gradlew spotlessApply`, then the `project-gates` skill (`spotlessCheck`, `test`, and
   `integrationTest` when the service touches the database).

## Never

- A repository, `EntityManager` or JPA entity in a controller or response body.
- 403 or a different message for "exists but not yours"; an input value echoed in a Problem; a stack trace or SQL
  in a response.
- A token, credential or page content in a log line.
- `ResponseEntity<ApiResponse<...>>`, a String body, or a second exception handler in a feature.
