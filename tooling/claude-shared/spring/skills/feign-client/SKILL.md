---
name: feign-client
description: Use when one Spring service must call another service over HTTP (OpenFeign with Eureka, Resilience4j timeouts, retry and circuit breaker).
---

# Add a Feign client

## Purpose
Call another service synchronously, contract-first, with timeouts, retry, circuit breaker and a safe fallback, behind a port.

## When to use / NOT to use
- Use: you need an immediate answer from another service.
- Not for: fire-and-forget or fan-out notifications (use `rabbitmq-event`); reading the other service's database (forbidden). Choose sync vs async with the root `microservice-integration` skill.

## Inputs to confirm with the user
- Target service name (as registered in Eureka) and the operation in its `contracts/openapi/` file.
- Is the call idempotent? Which timeout is acceptable to the user-facing flow?
- Forward the user JWT, or use a client-credentials token?

## Procedure
1. Read the target's OpenAPI contract. If the operation is missing, stop and write a note for the owner.
2. Define a port in `application/` in your own domain terms (`ProjectQueryPort`).
3. Implement the adapter and client in `infrastructure/client/`; DTOs are your own copies of the contract schemas.
4. Add an `ErrorDecoder` mapping 404/409/422/5xx to exceptions.
5. Configure timeouts and Resilience4j (templates below). Retry only idempotent calls, never on 4xx.
6. Add a fail-safe fallback ("unavailable"), never invented data.
7. Propagate `X-Request-Id`.
8. Test with WireMock: success, 4xx mapping, timeout, circuit open. Run `./gradlew test`.

## Templates
```java
@FeignClient(name = "webblock-project-service", path = "/api/v1/projects", configuration = ProjectClientConfig.class)
interface ProjectFeignClient {
    @GetMapping("/{id}") ProjectDto get(@PathVariable("id") UUID id);
}

@Component @RequiredArgsConstructor
class ProjectFeignAdapter implements ProjectQueryPort {
    private final ProjectFeignClient client;

    @Override
    @CircuitBreaker(name = "projectClient", fallbackMethod = "unavailable")
    @Retry(name = "projectClient")
    public Optional<ProjectSummary> findById(UUID id) {
        return Optional.of(client.get(id).toSummary());
    }
    private Optional<ProjectSummary> unavailable(UUID id, Throwable t) {
        throw new DownstreamUnavailableException("project-service");
    }
}
```
```yaml
spring.cloud.openfeign.client.config.webblock-project-service:
  connectTimeout: 2000
  readTimeout: 3000
resilience4j:
  circuitbreaker.instances.projectClient:
    slidingWindowSize: 20
    failureRateThreshold: 50
    waitDurationInOpenState: 30s
  retry.instances.projectClient:
    maxAttempts: 3
    waitDuration: 200ms
    enableExponentialBackoff: true
    enableRandomizedWait: true
    ignoreExceptions: [com.webblock.<service>.infrastructure.client.ClientErrorException]
```
Check the exact property names against the Spring Cloud / Resilience4j versions in the service `build.gradle`; values here are examples to be tuned.

## Checklist
- [ ] Operation exists in the callee's contract.
- [ ] Explicit connect and read timeouts.
- [ ] Retry only idempotent; 4xx not retried.
- [ ] Fallback fails safe; callers get a clear error within the timeout.
- [ ] Service name, no hard-coded host; no bodies with tokens/PII logged.

## Common mistakes
- Sharing the other service's DTO classes or jar.
- Retrying a non-idempotent POST.
- Fallback that returns fake data.
- Letting the Feign type leak into `application` or controllers.
- Annotating a method called from the same class (AOP proxy is bypassed).

## Definition of done
Contract-backed client, resilience configured, WireMock tests, `./gradlew test` green. Expand/contract: keep working against the previous contract version until the provider deploys.

## Related
Skills: `microservice-integration` (root), `exception-handling`. Rules: `resilience-feign`, `clean-architecture`.
