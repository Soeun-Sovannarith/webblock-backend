---
name: microservice-integration
description: Use when one WebBlock service must talk to another (new REST call or new event). Chooses sync vs async, drives contract-first design, and covers discovery, auth, resilience, data ownership, tests and rollout. Works from the service that is being changed and hands off the other side.
---

# Integrate two services

Paths below are written for a session started in `services/<service>`: `../../docs/...` and `../../contracts/...` resolve to the repo-root `docs/` and `contracts/`. (At the repo root drop the `../../`.)

## Purpose
Add a REST call or an event between two services safely: contract first, one side at a time, resilient, secure, and with a clear handoff to the other owner.
Terms: **provider** owns the data or capability; **consumer** needs it.

## When to use / when NOT to use
- Use for a new or changed call or event between services.
- Do NOT use for a change inside one service (use `/plan`), or for editing a contract alone (use skill `contract-change`).
- Scope: change ONLY the service where this session started. The other side gets a handoff note (step 11), never an edit (`.claude/rules/cross-service-changes.md`).

## Inputs to confirm with the user
Consumer and provider (check `../../docs/service-map.md`; if unclear, stop and ask), the data or action, expected volume, what the user sees if the provider is down, whether the provider endpoint/event already exists in `../../contracts/`.

## Procedure
1. **Frame it** in 4 lines: consumer, provider, data/action, failure behavior.
2. **Choose the style**

| Situation | Use |
|---|---|
| Consumer needs the answer now to answer its own caller | REST (OpenAPI) |
| Something happened, others may react, no answer needed | Event (RabbitMQ, AsyncAPI) |
| Long work (code generation, deployment) | REST start (`202` + job id) plus status endpoint or completion event |
| Same slow-changing data needed often | Local derived copy fed by events, with TTL |

   Never both REST and an event for the same purpose. No distributed transactions: use outbox plus compensation.
3. **Contract first.** REST: operation in `../../contracts/openapi/<provider>.vN.yaml` (bearer scheme, RFC 7807 `Problem`, idempotency key if retried). Event: exchange, routing key, queue, DLQ in `../../contracts/events/rabbitmq.asyncapi.yaml` and payload in `../../contracts/events/schemas/<event>.vN.json`. Use skill `contract-change`; released versions take additive changes only.
4. **Provider side** (only if this is your service). Implement exactly the contract; authorize role AND ownership; validate input; stable error codes; DTOs not entities; idempotent for retried operations; publish events after commit (outbox).
5. **Consumer side** (only if this is your service).
   - Call through a port in the application layer; adapter in infrastructure (Spring: skill `feign-client`; Python: `httpx.AsyncClient` in `app/clients/`).
   - Address the provider by Eureka service id, never host or port; values come from config-server.
   - Own request/response models generated or written from the contract; no shared DTO jar.
   - Connect and read timeouts, circuit breaker, bounded retry with backoff and jitter for idempotent calls only, safe fallback ("unavailable", never invented data).
   - Map provider errors to your own domain errors; never forward the provider's error body.
   - Events: dedupe on `eventId`, validate schema, bounded retry then DLQ (skills `rabbitmq-event`, `rabbitmq-consumer`).
6. **Security.** Forward the caller JWT only if the contract says so; otherwise a client-credentials token with least-privilege scopes. The provider validates the token itself and ignores identity headers such as `X-User-Id`. Send minimal fields; no secrets or PII in URLs, logs or payloads. Internal-only endpoints get no gateway route (skill `gateway-route` otherwise).
7. **Data ownership.** Never query another service's tables. A local copy keeps ids plus minimal snapshot, is marked derived, refreshed by events, with a stale policy.
8. **Consistency.** For multi-step flows list each step, its compensation and the state after failure at each point. Expect duplicate and out-of-order events and unknown outcomes after timeouts (reconcile).
9. **Config and discovery.** New timeouts, service ids, queue names go to config-server per environment with safe defaults and no secrets. Confirm both services register in Eureka (see `../../docs/decisions/0002-gateway-vs-istio.md` if that changes).
10. **Test** with no real network, service or LLM: consumer (WireMock or respx: success, 4xx mapping, timeout, circuit open, malformed body); provider (response matches OpenAPI); events (RabbitMQ Testcontainer or fake: publish-consume, duplicate, invalid payload to DLQ, out of order); security (401, 403, wrong owner). Run the test command from `CLAUDE.md` and report the real result.
11. **Observability and handoff.** Propagate and log `X-Request-Id` / `correlationId` (ids, outcome, latency only). Write the handoff table below.

## Templates
Handoff note for the other owner:

| Item | Content |
|---|---|
| Contract change | file, additive or breaking |
| Provider owner must | implement/verify what, by when |
| Consumer owner must | implement/verify what |
| Gateway / config-server / docs | routes, properties, `docs/service-map.md` rows |
| Rollout order | contract -> provider -> consumer -> cleanup (flag if needed) |
| Rollback | how to disable the integration safely |

Resilience4j shape (values are examples; real ones come from config-server):
```yaml
resilience4j:
  circuitbreaker.instances.<provider>: { slidingWindowSize: 20, failureRateThreshold: 50, waitDurationInOpenState: 10s }
  retry.instances.<provider>: { maxAttempts: 3, waitDuration: 200ms, enableExponentialBackoff: true }
```

## Checklist
- [ ] Style justified; contract exists and matches code
- [ ] Timeouts, breaker, retry (idempotent only), fallback set
- [ ] No shared DTOs, no cross-database access
- [ ] Auth verified on both sides; PII minimized
- [ ] Tests listed and run; handoff written

## Common mistakes
Hard-coded host or port; retrying non-idempotent POSTs; trusting `X-User-Id`; publishing before commit; leaking provider errors; consumer breaking when the provider is still on the old version (keep expand/contract).

## Definition of done
Contract merged or requested, own-side code and tests green, config documented, handoff delivered, rollout order agreed.

## Related
Skills `contract-change`, `feign-client`, `rabbitmq-event`, `gateway-route`; rule `cross-service-changes.md`; `../../docs/service-map.md`, `../../docs/security-model.md`.
