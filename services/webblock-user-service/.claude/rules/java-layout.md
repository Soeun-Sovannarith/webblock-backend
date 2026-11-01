---
paths:
  - "src/**"
---
# Java service folder layout (source: Notion "Web Block architecture" §6.1)

Base package: `org.kshrd.webblock.<service>` (`user`, `project`, `payment`, `notification`).
Path: `services/<service>-service/src/main/java/org/kshrd/webblock/<service>/`.
Not covered: `services/api-gateway`, `services/discovery-service` (keep as they are), Python services.

## Where does my code go?

| I am writing... | Put it in |
|---|---|
| A REST endpoint | `presentation/rest/<feature>/XController` |
| A gRPC server | `presentation/grpc/<feature>/XGrpcService` + `XGrpcMapper` |
| A RabbitMQ consumer (+ its incoming message record) | `presentation/messaging/` |
| Request body / validation (`@Valid`) | `application/<feature>/request` |
| Response record | `application/<feature>/response` |
| Request/entity/response conversion | `application/<feature>/mapper` |
| One use case (business flow, `@Transactional`) | `application/<feature>/handler/XHandler` |
| JPA entity, enum, value object, business rule | `domain/<feature>/model` |
| Event the domain raises (saved to outbox) | `domain/<feature>/event` |
| Spring Data JPA repository | `infrastructure/persistence/<feature>/XRepository` |
| RabbitMQ publisher, exchange/queue setup, outbox | `infrastructure/messaging` |
| Call to another service or outside system (Feign, gRPC client, Webill, Gitea) | `infrastructure/client` |
| `@Configuration`, `@ConfigurationProperties`, OpenApiConfig | `infrastructure/config` |
| `SecurityConfig`, current-user helper | `infrastructure/security` |
| Scheduled job | `infrastructure/scheduler` |
| Response envelope, `@RestControllerAdvice`, request-ID filter | `common/presentation` |
| `ErrorCode` enum, base exceptions | `common/domain` |
| Flyway SQL | `src/main/resources/db/migration` |

`<feature>` = one business area, e.g. `payment`, `clientstatus`, `project`. Add a new feature folder in each layer you need.

## What each layer does

- **presentation**: the way work comes in (HTTP, gRPC, message). It only checks the input, calls ONE handler and returns the answer. No business logic, no repository.
- **application**: what the service does. One handler = one use case. Request/response/mapper live here.
- **domain**: what things are and which rules are legal. `@Entity` is allowed on the model. No repository here.
- **infrastructure**: everything that talks to the outside (database, broker, other services, config, security).
- **common**: small shared code used by all features of this service only. Do not copy it to another service (no shared code between services).

## Rules (keep the arrows one way)

```
presentation -> application -> infrastructure
                    |               |
                    +-> domain <----+
```
1. `domain` never imports `presentation`, `application` or `infrastructure`.
2. `presentation` goes through a handler and does not import `infrastructure`, with two small exceptions: `infrastructure/security` (the current-user helper)
   and queue-name constants from `infrastructure/messaging`. The `layer-guard` hook enforces this.
   A message record that a handler needs (for example an incoming event) lives in `application/<feature>/request`, not in `presentation`.
3. Handlers use the Spring Data repository from `infrastructure/persistence` directly. No extra repository interface.
4. `@Transactional` only in `application/.../handler`.
5. Never return an `@Entity` from an API. Return a `response` record.
6. A new class goes in the folder from the table above. If none fits, ask before creating a new top-level folder.
7. Tests mirror the main package: test for `X` sits in the same package as `X`.

## Example: "create a project"

`ProjectController` (presentation) -> `CreateProjectHandler` (application) -> `PaymentGrpcClient` (infrastructure/client) + `ProjectRepository` (infrastructure/persistence) -> `ProjectResponse` made by `ProjectMapper`.

## Status of the move (27 Oct 2026)
All four services live under `services/`. Related rules: `repo-layout.md`, `rest-response-format.md`, `rabbitmq-events.md`, `grpc.md`.
- `payment-service`: fully moved. Services are in `handler/` as `PaymentService` + `Impl`; split into one handler per use case when you touch them.
- `user`, `project`, `notification`: scaffolds moved; empty layer folders have a `.gitkeep`. Delete the `.gitkeep` when you add a file.
