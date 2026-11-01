---
name: springboot-microservices
description: Implement maintainable Spring Boot microservice integrations for the WebBlock notification service using the repository's existing Java, Spring, Gradle/Maven, HTTP, security, and configuration conventions.
---

# Spring Boot Microservices Skill

## Before coding

- Inspect Java/Spring Boot versions and Gradle/Maven configuration.
- Inspect package structure, existing dependency injection style, exception handling, logging, configuration, security, and tests.
- Reuse existing dependencies and conventions; do not upgrade versions without a reason.
- Confirm whether the project uses RestClient, WebClient, OpenFeign, or another HTTP client before adding one.

## Design guidance

- Use constructor injection.
- Keep controllers, Rabbit listeners, application services, domain/policy logic, external clients, DTOs, and configuration separate.
- Use typed configuration properties for service URLs, timeouts, queue names, and workflow registry when consistent with the project.
- Add connection/read timeouts to User Service and Knock clients.
- Propagate correlation IDs where possible.
- Do not leak secrets or PII in logs.
- Map external errors to explicit internal exceptions/results.
- Do not swallow exceptions in listeners.
- Make transaction boundaries explicit.
- Avoid coupling notification service to other services' database schemas.
- Use service-to-service authentication already established in the repository.

## Suggested component boundaries

Names below are illustrative only; adapt to actual package conventions:
- `NotificationEventListener`
- `NotificationEventValidator`
- `NotificationEventRouter`
- `NotificationPolicyRegistry`
- `RecipientResolver`
- `UserServiceClient`
- `KnockClient`
- `KnockRecipientService`
- `WorkflowTriggerService`
- `ProcessedEventRepository` (only if persistent deduplication is selected)

Do not create all of these classes mechanically. Keep the design proportional to the codebase.

## Testing

- Unit-test pure mapping/policy logic.
- Mock external clients for service tests.
- Use Testcontainers or the project's existing integration test approach for RabbitMQ if available.
- Test configuration binding and invalid/missing configuration.
- Avoid live Knock API calls in ordinary CI tests.
