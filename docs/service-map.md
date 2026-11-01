# Service map

Single source of truth for "who owns what". Keep it in sync with the root `CLAUDE.md` table.
Unknown values are marked `TODO(owner)` - the owner fills them in; do not guess.

| Service | Folder | Tech | Owner | Database | Port | Registers in Eureka |
|---|---|---|---|---|---|---|
| User | `services/webblock-user-service` | Spring Boot, JPA, Flyway | Vannda | PostgreSQL `webblock_user` | 8081 | yes |
| AI Code Generator | `services/webblock-aicode-service` | Python, FastAPI | Sovannarith | PostgreSQL `aicode_db` | 8000 | no (Mesh / Gateway direct) |
| Project | `services/webblock-project-service` | Spring Boot, JPA, Flyway | Devid | PostgreSQL `webblock_project` + `catalog` | 8080 (gRPC: 9090, mgmt: 8089) | yes |
| Provisioning / Deployment | `services/webblock-provisioning-service` | Python, FastAPI | Chamreun | PostgreSQL `provisioning` | 8084 | no (Mesh / Gateway direct) |
| Payment / Subscription | `services/webblock-payment-service` | Spring Boot, JPA, Flyway | Endy | PostgreSQL `webblock_payment` | 8083 | yes |
| Notification | `services/webblock-notification-service` | Spring Boot, JPA, Flyway | Lina | PostgreSQL `webblock_notification` | 8085 | yes |
| API Gateway | `services/api-gateway` | Spring Cloud Gateway | - | none | 8090 | yes |
| Discovery | `services/discovery-service` | Eureka Server | - | none | 8761 | n/a |
| Config Server | `services/config-server` | Spring Cloud Config | - | none | 8888 | yes |

## Synchronous calls (REST / gRPC)
| Caller | Callee | Purpose | Protocol / Endpoint | Contract |
|---|---|---|---|---|
| Provisioning | Project | Verify access permission for project release operations | REST: `POST /internal/v1/access-checks` or gRPC `CheckAccess` (9090) | `proto/webblock/project/v1/access.proto` |
| Provisioning | Project | Look up repository details (clone_url, subdomain) | gRPC `GetRepository` (9090) | `proto/webblock/project/v1/repository.proto` |
| AI Codegen | Project | Fetch frozen pages and contract for code generation | REST: `GET /internal/v1/generation-requests/{id}/codegen-input` | `contracts/events/project/generation_requested.v1.schema.json` |
| AI Assistant | Project | Verify chat thread owner access | REST: `POST /internal/v1/access-checks` | `services/webblock-project-service/.claude/docs/contracts/service-communication.md` |
| Project | AI Codegen | Read generation run details & presigned artifact download URLs | REST: `GET /internal/v1/generation-runs/{id}` | `contracts/events/ai_codegen/generation_completed.v1.schema.json` |

## Events (RabbitMQ)
| Event (routing key) | Topic Exchange | Publisher | Consumers | Schema |
|---|---|---|---|---|
| `generation_requested.v1` | `project.events` | project-service | aicode-service (`ai_codegen.generation_requested.v1`) | `contracts/events/project/generation_requested.v1.schema.json` |
| `generation_completed.v1` | `ai_codegen.events` | aicode-service | project-service (`project.generation_completed.v1`) | `contracts/events/ai_codegen/generation_completed.v1.schema.json` |
| `source_published.v1` | `project.events` | project-service | provisioning-service (`provisioning.source_published.v1`), notification-service | `contracts/events/project/source_published.v1.schema.json` |
| `publish_completed.v1` | `provisioning.events` | provisioning-service | project-service (`project.publish_completed.v1`), payment-service | `contracts/events/provisioning/publish_completed.v1.schema.json` |
| `project_deleted.v1` | `project.events` | project-service | provisioning-service, payment-service | `contracts/events/project/project_deleted.v1.schema.json` |
| `owner_status_changed.v1` | `project.events` | project-service | provisioning-service | `contracts/events/project/owner_status_changed.v1.schema.json` |
| `user_status_changed.v1` | `account.events` | user-service | project-service, provisioning-service | `contracts/events/user-status-changed.v1.json` |

## Shared infrastructure
Keycloak (identity at `http://localhost:8180/realms/webblock`), RabbitMQ (broker at `localhost:5672`), PostgreSQL (databases: `webblock_user`, `webblock_project`, `catalog`, `aicode_db`, `provisioning`), Redis (OTP caching at `localhost:6379`), Mailpit (SMTP at `localhost:1025`).
