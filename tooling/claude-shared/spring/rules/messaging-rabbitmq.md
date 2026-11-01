---
paths:
  - "**/*.java"
---

# RabbitMQ messaging rules

## Contract and naming
- The catalog is `contracts/events/` (`rabbitmq.asyncapi.yaml` + `schemas/`). Publish and consume only events defined there; contract changes come first.
- Exchange `webblock.<domain>` (topic); routing key `<entity>.<action>` (`user.registered`); queue `<consumer-service>.<entity>.<action>`; dead-letter queue `<queue>.dlq`.
- Envelope: `eventId` (UUID), `eventType`, `occurredAt` (UTC ISO-8601), `version`, `correlationId`, `payload`. Payload holds ids and required fields only: no secrets, no PII dumps, no entities.

## Publishing
- Publish after commit (outbox table or `@TransactionalEventListener(AFTER_COMMIT)`), with publisher confirms and persistent messages.

## Consuming
- Idempotent: dedupe on `eventId`; tolerate redelivery and out-of-order delivery.
- Ack only after successful processing; bounded retry with backoff; failures go to the DLQ. A poison message never blocks the queue.
- Validate against the schema. Ignore unknown fields; missing required fields -> dead-letter.

## Compatibility and access
- Additive optional fields only; never rename, retype or repurpose a field in place; publish a new version.
- Broker credentials come from config; one least-privilege RabbitMQ user per service.

## Example
```java
@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
void on(UserRegistered e) { publisher.publish(EventEnvelope.of("user.registered", e)); }
```
