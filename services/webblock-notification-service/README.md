# WebBlock Notification Service

Centralized, event-driven notification microservice for the WebBlock platform. Built on **Spring Boot 4.1.1** and **Java 21**, it consumes business and operational domain events from RabbitMQ, handles internal monitoring alerts, resolves recipients, and orchestrates delivery across **Web In-App Feed** and **Email** channels through [Knock](https://knock.app).

---

## Table of Contents

- [Overview & Architecture](#overview--architecture)
- [Key Features](#key-features)
- [Event Ingestion & Message Topology](#event-ingestion--message-topology)
- [Notification Matrix & Workflow Mapping](#notification-matrix--workflow-mapping)
- [Reliability, Idempotency & Error Handling](#reliability-idempotency--error-handling)
- [API Endpoints & Security](#api-endpoints--security)
- [Configuration Reference](#configuration-reference)
- [Active Profiles & Recipient Staging](#active-profiles--recipient-staging)
- [Local Development & Testing](#local-development--testing)
- [Project Directory Structure](#project-directory-structure)

---

## Overview & Architecture

Notification Service operates as an asynchronous, stateless orchestrator. It does not own a local relational database:
- **Feed Storage & Unread State:** Handled directly in Knock. The frontend connects to Knock's in-app feed using user tokens issued by this service.
- **Event Deduplication:** Enforced through Knock's 24-hour idempotency key window (`Idempotency-Key`).
- **Resilience:** Backed by RabbitMQ Quorum Queues, Dead Letter Queues (DLQ), and automated retry classification (permanent vs. transient errors).

```text
  +---------------------------------------------------------------------------------+
  |                             WebBlock Microservices                              |
  |   (project-service, provisioning-service, payment-service, Alertmanager)       |
  +---------------------------------------------------------------------------------+
                      |                                       |
           RabbitMQ Domain Events                     HTTP Webhook Alerts
                      |                                       |
                      v                                       v
          +-------------------------------------------------------+
          |             WebBlock Notification Service             |
          |                                                       |
          |  * Event Validation & Payload Normalization           |
          |  * Recipient Resolution (Owner / Admin)              |
          |  * Trace Context Propagation (traceparent, req-id)   |
          |  * Idempotency Key Generation                         |
          |  * Knock Java SDK Client (with retry/DLQ routing)     |
          +-------------------------------------------------------+
                                      |
                                      v
                               +--------------+
                               |  Knock API   |
                               +--------------+
                               /              \
                              v                v
                 Web In-App Feed (Bell)      Email (SMTP/SES)
```

---

## Key Features

1. **Multi-Channel Orchestration:**
   - **Web In-App Feed:** Real-time bell notifications, unread counts, and action deep-links.
   - **Email:** Transactional alerts for critical system incidents, billing notices, and owner status updates.
   - *(Note: Mobile push notifications described in high-level specs are out-of-scope for the current release).*
2. **Secure User Feed Token Generation:**
   - Issues short-lived, cryptographically signed Knock JWT tokens (`/api/notification/v1/users/token`) so the frontend can securely connect to Knock without exposing backend API secrets.
3. **Alertmanager Webhook Receiver:**
   - Dedicated internal HTTP endpoint (`/internal/v1/alertmanager/alerts`) protected by shared-secret Bearer authentication for infrastructure and cluster-level alerts.
4. **Resilient Broker Topology & Dynamic Binding:**
   - Employs RabbitMQ Quorum Queues with individual Dead Letter Queues (`.dlq`).
   - Uses `SenderExchangeWatcher` to poll and safely bind to upstream topic exchanges once producer services start, preventing broker startup race conditions.
5. **Observability & Micrometer Metrics:**
   - Prometheus metrics endpoint (`/actuator/prometheus`) tracking listener consumption, dead-letter counts, Knock response timings, and missing sender exchange status.

---

## Event Ingestion & Message Topology

Upstream services own their respective topic exchanges. Notification Service declares and binds only its dedicated quorum queues:

| Upstream Service | Topic Exchange | Routing Key | Consumer Queue | Dead-Letter Queue |
|---|---|---|---|---|
| `project-service` | `project.events` | `source_published.v1` | `notification.source_published.v1` | `notification.source_published.v1.dlq` |
| `provisioning-service` | `provisioning.events` | `publish_completed.v1` | `notification.publish_completed.v1` | `notification.publish_completed.v1.dlq` |
| `payment-service` | `payment.events` | `subscription_activated.v1` | `notification.subscription_activated.v1` | `notification.subscription_activated.v1.dlq` |
| `payment-service` | `payment.events` | `hosting_entitlement_ended.v1` | `notification.hosting_entitlement_ended.v1` | `notification.hosting_entitlement_ended.v1.dlq` |
| `payment-service` | `payment.events` | `payment_due.v1` | `notification.payment_due.v1` | `notification.payment_due.v1.dlq` |

> [!NOTE]
> When starting on a fresh local RabbitMQ instance where upstream services have not created their exchanges yet, set `NOTIFICATION_DECLARE_SENDER_EXCHANGES=true` to let Notification Service declare them locally. In UAT/Production, keep this `false`.

---

## Notification Matrix & Workflow Mapping

Each event is validated and mapped to the appropriate recipient audience and Knock workflow:

| Event / Trigger | Source | Target Audience | Channels | Knock Workflow Key |
|---|---|---|---|---|
| **Website Generation Completed** | `project.events` | Website Owner | Web In-App | `generation-completed` |
| **Deployment Succeeded** | `provisioning.events` | Website Owner | Web In-App | `deployment-succeeded` |
| **Deployment Failed** | `provisioning.events` | Website Owner & Admins | Web In-App (Owner & Admin) | `deployment-failed-owner`, `deployment-failed-admin` |
| **Subscription Activated** | `payment.events` | Admins | Web In-App | `subscription-activated-admin` |
| **Subscription Expired** | `payment.events` | Admins | Web In-App | `subscription-expired-admin` |
| **Monthly Payment Due** | `payment.events` | Website Owner | Email | `payment-due-owner` |
| **Website Generation Stuck** | Alertmanager | Admins | Web In-App + Email | `generation-stuck-admin` |
| **Website Crashed** | Alertmanager | Website Owner & Admins | Web In-App (Admin) + Email (Owner) | `website-crashed-owner`, `website-crashed-admin` |
| **Resource Limit Exceeded** | Alertmanager | Website Owner & Admins | Web In-App (Admin) + Email (Owner) | `resource-limit-exceeded`, `resource-limit-exceeded-admin` |
| **Platform Service Down** | Alertmanager | Admins | Web In-App + Email | `platform-service-down-admin` |

---

## Reliability, Idempotency & Error Handling

### Idempotency
Every Knock workflow trigger carries an idempotency header:
```text
Idempotency-Key: <message_type>:<event_key>:<workflow>:<recipient_id>
```
Knock caches trigger keys within a 24-hour sliding window. Re-delivered messages or retries within 24 hours return the original response and will not duplicate notifications.

### Failure Classification & Retries
Retries are configured using Spring AMQP with exponential backoff (5 total attempts: 1 initial attempt + 4 retries, starting at 1000ms doubling up to 10s max):
- **Permanent Errors (`PermanentFailure`):**
  - Validation failures (missing required event attributes).
  - Knock client errors (`400`, `401`, `403`, `404` including missing workflows, `422`).
  - Unconfigured recipient lookups in production mode.
  - *Action:* Immediately rejected without requeue; routed straight to the respective `.dlq`.
- **Transient Errors:**
  - Network timeouts, rate limiting (`429`), Knock server errors (`5xx`).
  - *Action:* Retried up to 4 times with exponential backoff before being forwarded to `.dlq`.

---

## API Endpoints & Security

| Method | Endpoint | Auth Mechanism | Description |
|---|---|---|---|
| `GET` | `/api/notification/v1/users/token` | Bearer JWT (Keycloak) | Issues user token for Knock in-app notification feed |
| `POST` | `/internal/v1/alertmanager/alerts` | Bearer Token (`ALERTMANAGER_WEBHOOK_TOKEN`) | Ingests Prometheus Alertmanager webhook payloads |
| `GET` | `/actuator/prometheus` | None (Internal / Cluster-only) | Prometheus metrics scrape endpoint |
| `GET` | `/actuator/health` | None | Service liveness and readiness probe |
| `GET` | `/actuator/info` | Bearer JWT | Service metadata |
| `GET` | `/swagger-ui.html` | Public / Local | OpenAPI documentation UI |
| `GET` | `/v3/api-docs` | Public / Local | OpenAPI specification |

---

## Configuration Reference

The service reads configuration through `application.yml`, overridable by environment variables:

### Core & Server
| Environment Variable | Default Value | Description |
|---|---|---|
| `SERVER_PORT` | `8084` | HTTP server listening port |
| `SPRING_PROFILES_ACTIVE` | `local-stub` | Active Spring profile (`local-stub`, `k8s`, `json-logs`) |
| `KEYCLOAK_ISSUER_URI` | `https://kc-admin.roa-dev.digital/realms/master` | Keycloak realm URL for JWT token validation |
| `EUREKA_URL` | `http://localhost:8761/eureka/` | Netflix Eureka service discovery URL (disabled under `k8s`) |

### RabbitMQ Messaging
| Environment Variable | Default Value | Description |
|---|---|---|
| `RABBITMQ_HOST` | `localhost` | RabbitMQ broker hostname |
| `RABBITMQ_PORT` | `5672` | RabbitMQ broker AMQP port |
| `RABBITMQ_USERNAME` | `guest` | RabbitMQ username |
| `RABBITMQ_PASSWORD` | `guest` | RabbitMQ password |
| `RABBITMQ_VHOST` | `/` | RabbitMQ virtual host |
| `NOTIFICATION_DECLARE_SENDER_EXCHANGES` | `false` | Set to `true` only on empty local brokers to declare producer exchanges |

### Knock Provider
| Environment Variable | Default Value | Description |
|---|---|---|
| `KNOCK_API_KEY` | `sk_test_placeholder` | Knock secret backend API key |
| `KNOCK_SIGNING_KEY` | *(None)* | PKCS#8 PEM private key used to sign client feed tokens |
| `KNOCK_TOKEN_TTL` | `1h` | Time-to-live for issued Knock client tokens |

### Internal Webhook
| Environment Variable | Default Value | Description |
|---|---|---|
| `ALERTMANAGER_WEBHOOK_TOKEN` | *(None)* | Shared secret token required in `Authorization: Bearer <token>` for `/internal/v1/alertmanager/alerts` |

---

## Active Profiles & Recipient Staging

- **`local-stub` (Default for local development):**
  - Uses `StubProjectOwnerClient` and `StubUserContactClient` to supply test recipient IDs and emails without requiring running instances of `project-service` or `user-service`.
- **`k8s` (Kubernetes UAT / Production):**
  - Activated with `SPRING_PROFILES_ACTIVE=k8s`.
  - Disables Eureka client discovery; uses Kubernetes Service DNS (`user-service:8081`, `project-service:8082`, `payment-service:8083`).
  - Uses `UnavailableProjectOwnerClient` and `UnavailableUserContactClient` pending completion of upstream gRPC/REST clients (owner events safely fail-closed to DLQ while admin alerts remain active).
- **`json-logs` (Optional logging profile):**
  - Enables single-line Logstash-compatible JSON log formatting with correlation IDs (`trace_id`, `span_id`, `request_id`).

---

## Local Development & Testing

### Prerequisites
- **Java 21** (JDK installed and configured)
- **Docker** (for local RabbitMQ broker)

### 1. Start RabbitMQ
```powershell
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

### 2. Copy and Configure Environment
Copy `.env.example` to `.env` (or pass environment variables into your IDE/shell):
```powershell
cp .env.example .env
```

### 3. Run the Application
```powershell
# Windows PowerShell
.\gradlew.bat bootRun

# Linux / macOS
./gradlew bootRun
```

### 4. Run Tests
```powershell
# Run all unit tests and isolated listener tests
.\gradlew.bat test

# Optional: Run integration tests against a local Docker RabbitMQ instance
$env:NOTIFICATION_BROKER_IT="local"
.\gradlew.bat cleanTest test --tests "*LocalBroker*"
```

---

## Project Directory Structure

```text
services/notification-service/
├── docs/                           # Architectural specs, event catalog & checklist
│   ├── architecture.md
│   ├── event-catalog.md
│   ├── implementation-checklist.md
│   ├── notification-matrix.md
│   └── recipient-resolution.md
├── src/
│   ├── main/
│   │   ├── java/org/kshrd/webblock/notification/
│   │   │   ├── application/        # Handlers, use cases, event DTOs & response models
│   │   │   ├── common/             # Tracing context, permanent domain exceptions
│   │   │   ├── infrastructure/     # Knock SDK client, RabbitMQ config, security, metrics
│   │   │   └── presentation/       # REST controllers & AMQP event consumers
│   │   └── resources/
│   │       ├── application.yml
│   │       ├── application-k8s.yml
│   │       └── application-json-logs.yml
│   └── test/                       # Unit, webmvc, security, and broker integration tests
├── .env.example                    # Sample environment variables
├── .gitignore                      # Git ignore rules (includes .vscode/, .gradle/, build/)
├── build.gradle                    # Gradle build configuration
└── README.md                       # Service documentation
```
