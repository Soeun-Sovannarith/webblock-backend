---
name: rabbitmq-event-contracts
description: Design and implement versioned RabbitMQ domain-event consumers and producers for WebBlock microservices, including retries, deduplication, DLQs, and contract tests.
---

# RabbitMQ Event Contracts Skill

Use for any change involving events published by Payment, Deployment, Generation, Monitoring, or other WebBlock services.

## Rules

- Inspect existing RabbitMQ topology and conventions first.
- Do not invent exchange, queue, routing key, vhost, or retry names as if they exist.
- Use domain facts, not imperative notification commands.
- Use a common versioned event envelope; see `docs/event-catalog.md`.
- Include stable event ID, event type, schema version, UTC occurrence time, producer, aggregate identity, and payload.
- Keep payloads small and avoid unnecessary PII.
- Treat messages as untrusted input; validate required fields and supported versions.
- Assume at-least-once delivery and redelivery. Consumers must be idempotent.
- Use durable topology and persistent messages for important events where appropriate.
- Use bounded retry with backoff and DLQ; do not endlessly requeue poison messages.
- Consider transactional outbox in producers to avoid losing events after DB commit.
- Preserve correlation/trace IDs across service boundaries when the platform supports them.

## Consumer design

Keep responsibilities separated:
- listener/adapter: receives and acknowledges messages according to container configuration
- validator: validates envelope and event-specific payload
- handler/router: selects supported event handler
- recipient resolver: determines owner/admin recipients
- application service: coordinates User Service + Knock
- retry/error classifier: distinguishes transient and permanent errors

Do not put the whole workflow in an `@RabbitListener` method.

## Contract evolution

- Add optional fields compatibly where possible.
- For breaking changes, publish a new event version or follow the team's schema registry policy.
- Keep old-version handling only as long as required.
- Add producer/consumer contract tests.
- Document event ownership and changes with the producing service team.
