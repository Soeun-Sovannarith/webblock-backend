---
paths:
  - "**/*.java"
---

# Service-to-service calls (OpenFeign + Resilience4j)

## Placement and contract
- A Feign client lives in `infrastructure/client`, behind a port defined in `application`. Controllers and domain never call Feign.
- The client mirrors the callee's OpenAPI file in `contracts/openapi/`. If the operation is missing, stop: the owner adds it first. The contract wins over guesses.
- Resolve targets by Eureka service name (`@FeignClient(name = "<service-name>")`); no hard-coded hosts or ports.

## Resilience
- Explicit connect and read timeouts on every client; no unbounded waits.
- `@CircuitBreaker` plus `@Retry` (idempotent calls only: GET, or POST with an idempotency key); optional `@Bulkhead`.
- Retry with exponential backoff and jitter; never on 4xx.
- Fallback fails safe ("unavailable"); never fabricate data.
- A `ErrorDecoder` maps 404/409/422/5xx to domain exceptions; the user gets a clear error within the timeout.

## Headers and logging
- Propagate `X-Request-Id`. Prefer a client-credentials token; forward the user JWT only if the contract requires it.
- Never log bodies of auth, payment or personal data.

## Example
```yaml
resilience4j:
  circuitbreaker.instances.projectClient: { slidingWindowSize: 20, failureRateThreshold: 50, waitDurationInOpenState: 30s }
  retry.instances.projectClient: { maxAttempts: 3, enableExponentialBackoff: true, enableRandomizedWait: true }
```
