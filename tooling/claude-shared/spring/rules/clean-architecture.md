---
paths:
  - "**/*.java"
---

# Clean architecture (Spring Boot services)

## Dependency direction
    presentation   -> application -> domain
    infrastructure -> application -> domain     (infrastructure implements ports defined inward)

Package layout: `com.webblock.<service>.{domain,application,infrastructure,presentation}`. Confirm deviations in the service `CLAUDE.md`.

## What lives where
- `domain`: entities/aggregates, value objects, domain services, repository interfaces, domain exceptions. No Spring, JPA, Jackson, Feign or AMQP imports.
- `application`: one class per use case, ports (DB, messaging, other services), command/query objects. `@Transactional` lives here.
- `infrastructure`: JPA entities + Spring Data repositories, Feign clients, Rabbit publishers/listeners, Keycloak adapter, config. Maps entity <-> domain.
- `presentation`: controllers, request/response records, `@RestControllerAdvice`. Controllers call use cases only.

## Hard rules
- Controllers never touch repositories, entities or Feign clients.
- JPA entities and domain objects never appear in an API response or event payload.
- Business rules live in `domain`/`application`, not in controllers, mappers or entity callbacks.
- Constructor injection only (`final` fields, `@RequiredArgsConstructor`); no field `@Autowired`.
- Lombok: `@Getter`, `@Builder`, `@RequiredArgsConstructor` are fine; never `@Data` or `@EqualsAndHashCode` on entities.
- Do not move classes between layers during another task; report violations as technical debt.

## Example
```java
// application/port/ProjectQueryPort.java  (port, no Feign types)
public interface ProjectQueryPort { Optional<ProjectSummary> findById(UUID id); }
// infrastructure/client/ProjectFeignAdapter.java implements ProjectQueryPort
```
