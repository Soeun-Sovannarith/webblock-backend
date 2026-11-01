---
name: add-feature-folder
description: Add a new business feature to a WebBlock Spring Boot service using the four-layer layout (presentation/application/domain/infrastructure) - creates the controller, request/response records, mapper, handler, entity, repository, Flyway migration and tests in the right folders. Use when adding a use case such as "create project", "list bills" or "suspend user".
argument-hint: "[service] [feature] [use case, e.g. payment bill CreateBill]"
---
# New feature in a Java service: $ARGUMENTS

Read first: `.claude/rules/java-layout.md`, `rest-response-format.md`, `secure-coding.md`, `java-spring.md`, `database.md`.
Work in ONE service folder (`services/<name>-service`). Package: `org.kshrd.webblock.<name>`.

## 0. Before you write code
1. Find the feature in `docs/specs/feature-detail.md` and the step in `workflow.md`; list every decision branch (the "No" branches too).
2. Check `business-rules.md` and `status-flows.md`. A needed status or rule that is missing = ask, do not invent.
3. Does it need data from another service? Then it is a contract question (`add-grpc-method` / `add-rabbit-event`). Do not query that service's tables.
4. Does an `open-questions.md` item touch it? Ask first.

## 1. Files to create (feature = `<feature>`, use case = `<UseCase>`)
```
presentation/rest/<feature>/<Feature>Controller            // thin: @Valid, call ONE handler, return the response record
application/<feature>/request/<UseCase>Request             // record + Bean Validation
application/<feature>/response/<Feature>Response           // record, never an @Entity
application/<feature>/mapper/<Feature>Mapper               // request -> model, model -> response
application/<feature>/handler/<UseCase>Handler             // ONE use case; @Transactional; uses the repository directly
domain/<feature>/model/<Entity>                            // @Entity allowed; business rules as methods; enums here
domain/<feature>/event/<Thing>Happened                     // only if other services must know (-> add-rabbit-event)
infrastructure/persistence/<feature>/<Entity>Repository    // Spring Data JPA interface
src/main/resources/db/migration/V<yyyyMMddHHmm>__<what>.sql
```
Naming: handlers end in `Handler`, requests in `Request`, responses in `Response`. Keep `common/` for things used by many features only.

## 2. Rules while writing
- IDs `UUID`; time `Instant` -> `timestamptz` UTC; enums `@Enumerated(EnumType.STRING)` with exact values from `status-flows.md`.
- Owner endpoint: load the resource, compare owner with the current user (`CurrentUser` in `infrastructure/security`), not the owner -> **404**.
  Admin endpoint under `/api/<name>/v1/admin/**`: `ADMIN` only; audit the action.
- Retryable write: `Idempotency-Key` header, stored, the first result returned on repeat.
- Limits that "can change" (retry = 2, trial = 14 days, products = 30) come from `application.yml` or the DB, never hard-coded.
- Errors: a code in the service's `ErrorCode` enum, thrown as a domain exception, turned into problem+json by the shared handler.
- No `@Transactional` in controllers/domain. No repository in controllers. No entity in an API answer. The `layer-guard` hook will warn.
- Migration: NEW file; table/column names exactly as `data-dictionary.md`. Never edit an applied one.

## 3. Tests
- Handler unit test: every rule and each "No" branch. `@WebMvcTest`: 200/201, 400 with `errors[]`, 401, 404 for another owner (same body as missing).
- Repository/migration: Testcontainers PostgreSQL (needs Docker running).
- Run `./gradlew test` in that service and show the result.

## 4. Report
Files created (by layer) · the rule/spec lines you followed · tests and results · open question or contract you need from another service.
