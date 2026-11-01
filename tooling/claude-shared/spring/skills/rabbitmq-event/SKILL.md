---
name: rabbitmq-event
description: Use when a Spring Boot service must publish or consume a RabbitMQ event. Contract first, idempotent consumers, dead-letter queues, publish after commit.
---

# Publish or consume an event

## Purpose
Exchange domain events between services reliably: contract-defined, published after commit, consumed idempotently with a DLQ.

## When to use / NOT to use
- Use: state change others react to (`user.registered`), decoupled work.
- Not for: queries needing an immediate answer (`feign-client`). A new event is published first, then consumed (publisher before consumers).

## Inputs to confirm with the user
- Event name (`<entity>.<action>`), publisher, consumers, payload fields.
- Is it a new event or a change to an existing one (additive only)?
- Ordering and retry expectations; what should happen to poison messages.

## Procedure
1. **Contract first.** Add/adjust `contracts/events/rabbitmq.asyncapi.yaml` and `contracts/events/schemas/<event>.v1.json` via `contract-change`. Optional fields only on released versions.
2. **Publish**: port in `application/`, `RabbitTemplate` adapter in `infrastructure/messaging/`, envelope `eventId, eventType, occurredAt, version, correlationId, payload`.
3. Publish after commit (outbox table, or `@TransactionalEventListener(AFTER_COMMIT)`), publisher confirms on, persistent messages.
4. **Consume**: `@RabbitListener` in `infrastructure/messaging/` only deserializes, validates and calls a use case.
5. Make the use case idempotent: record processed `eventId` (unique constraint; migration via `flyway-migration`).
6. Declare queue, DLX/DLQ and bounded retry with backoff; reject without requeue to dead-letter.
7. Test, then `./gradlew test`.

## Templates
```java
// publisher
@Component @RequiredArgsConstructor
class UserEventPublisherAdapter implements UserEventPublisher {
    private final RabbitTemplate rabbit;
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void on(UserRegisteredDomainEvent e) {
        var env = EventEnvelope.of("user.registered", 1, MDC.get("correlationId"), new UserRegisteredPayload(e.userId()));
        rabbit.convertAndSend("webblock.user", "user.registered", env);
    }
}

// consumer
@Component @RequiredArgsConstructor
class UserRegisteredListener {
    private final HandleUserRegisteredUseCase useCase;
    @RabbitListener(queues = "notification-service.user.registered")
    void onMessage(EventEnvelope<UserRegisteredPayload> env) {
        useCase.handle(env.eventId(), env.payload());   // dedupes on eventId inside
    }
}

// topology
@Bean Queue q() { return QueueBuilder.durable("notification-service.user.registered")
    .deadLetterExchange("").deadLetterRoutingKey("notification-service.user.registered.dlq").build(); }
@Bean Queue dlq() { return QueueBuilder.durable("notification-service.user.registered.dlq").build(); }
```
```yaml
spring.rabbitmq.listener.simple:
  acknowledge-mode: auto          # ack after the listener returns
  default-requeue-rejected: false # failures dead-letter instead of looping
  retry: { enabled: true, max-attempts: 3, initial-interval: 1s, multiplier: 2 }
spring.rabbitmq.publisher-confirm-type: correlated
```
Use credentials from config-server/environment only. Verify property names against the Spring Boot version in use.

## Checklist
- [ ] Event and schema exist in `contracts/events/`; payload validated on receipt.
- [ ] Names follow `webblock.<domain>` / `<entity>.<action>` / `<queue>.dlq`.
- [ ] No secrets, PII dumps or entities in payloads.
- [ ] Published after commit; consumer idempotent; DLQ exists.

## Common mistakes
- Publishing inside the transaction (event sent, DB rolled back).
- Infinite requeue of a poison message.
- Deduping by payload content instead of `eventId`.
- Renaming or retyping a field in place.
- Assuming ordering.

## Definition of done
Contract merged, unit test (duplicate, invalid payload), RabbitMQ Testcontainers test (publish -> consume, DLQ routing), `./gradlew test` green, consumers notified in the changelog.

## Related
Skills: `contract-change`, `microservice-integration` (root), `flyway-migration`. Rule: `messaging-rabbitmq`.
