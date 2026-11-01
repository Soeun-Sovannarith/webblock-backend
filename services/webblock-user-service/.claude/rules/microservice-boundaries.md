# Microservice boundaries (always on)

- **One service, one bounded context, one database.** A service owns its tables; no other
  service reads or writes them. No shared entities, no shared schema.
- **No compile-time coupling.** Never add another service as a Gradle/pip dependency or copy
  its entity classes. The consumer defines its own DTOs for the contract.

## Two runtimes, one codebase (must not conflict)

| | Local / dev | Kubernetes (uat / prod) |
|---|---|---|
| Entry point | `api-gateway` (Spring Cloud Gateway) | Istio ingress gateway + VirtualService |
| Service lookup | `discovery-service` (Eureka) | Kubernetes Service DNS |
| Spring profile | default (`local`) | `k8s` |
| `api-gateway`, `discovery-service` | running | **not deployed** |

Rules that keep both working:
1. **Same public paths** in both: `/api/<service>/v1/**`, where `<service>` is the folder
   name without `-service` (`/api/user/v1`, `/api/ai-generator/v1`). A route added to
   `services/api-gateway/application.yml` must also be added to the Istio VirtualService, and the reverse.
2. **Call other services by name only, with OpenFeign**: `@FeignClient(name = "<folder-name>")`
   (Python services: an httpx client with the base URL from config). Locally Eureka resolves it. In the `k8s` profile Eureka is off
   (`eureka.client.enabled: false`) and
   `spring.cloud.discovery.client.simple.instances.<name>[0].uri: http://<name>:<port>`
   maps the same name to Kubernetes DNS. Never hard-code `localhost:<port>` or cluster URLs in code.
3. **No business logic in `api-gateway`.** Only routing and things Istio can also do
   (CORS, timeouts, rate limit, request id). Anything else would vanish in prod.
4. **Every service validates the Keycloak JWT itself** (see `security.md`). Never trust
   identity headers added by a gateway.
5. Every service exposes `/actuator/health` (Python: `GET /health`) and sets
   `spring.application.name` / app name equal to its folder name.

## Communication
- Three channels: REST (frontend/mobile), gRPC (ask and wait), RabbitMQ events (something happened). Contracts for gRPC/events live in `contracts/`
  (`contracts.md`). Existing calls are OpenFeign and stay until migrated one by one (`grpc.md`, `feign-to-grpc` skill).
- Never edit another service's contract; changes others depend on follow `cross-service-change.md`.
- Synchronous HTTP for queries; events for side effects ("send email after payment").
  Notification work must not block the request path.
- Outbound calls: timeouts always; retries / circuit breaker (Resilience4j) where they can fail.
- Cross-service contract changes must be backward compatible or rolled out in steps. Call out the impact.
