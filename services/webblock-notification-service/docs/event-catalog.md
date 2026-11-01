# Proposed Event Catalog

This catalog is a starting proposal derived from the product events in Feature Detail. Confirm actual event names, owners, and payload contracts with each source microservice before implementation.

## Common event envelope

```json
{
  "eventId": "uuid",
  "eventType": "deployment.failed",
  "eventVersion": 1,
  "occurredAt": "2026-10-30T09:30:00Z",
  "producer": "deployment-service",
  "aggregateType": "website",
  "aggregateId": "website-123",
  "correlationId": "trace-or-request-id",
  "payload": {
    "websiteId": "website-123",
    "ownerUserId": "user-456"
  }
}
```

Required envelope fields:
- `eventId`: globally unique event identifier for deduplication.
- `eventType`: stable, namespaced event name.
- `eventVersion`: schema version.
- `occurredAt`: ISO-8601 UTC timestamp.
- `producer`: source service identifier.
- `aggregateType` and `aggregateId`: domain entity context.
- `payload`: event-specific facts.

Optional fields such as `correlationId` can be added according to the team's tracing standard.

## Proposed event names and owners

| Proposed event type | Likely producer | Minimum routing data | Product recipient |
|---|---|---|---|
| `website.generation.completed` | generation-service | websiteId, ownerUserId, result/reference URL | Owner |
| `website.generation.stuck` | generation-service | websiteId, ownerUserId if known, stuck duration/reason | Admin |
| `deployment.succeeded` | deployment-service | websiteId, ownerUserId, deploymentId, deployedAt | Owner |
| `deployment.failed` | deployment-service | websiteId, ownerUserId, deploymentId, failure summary/log reference | Owner + Admin |
| `subscription.payment_due` | payment-service | subscriptionId, ownerUserId, dueAt, plan | Owner |
| `website.crashed` | monitoring-service | websiteId, ownerUserId, incidentId, detectedAt, status/reference | Owner + Admin |
| `website.resource_limit_exceeded` | monitoring-service | websiteId, ownerUserId, resource, usage, limit | Owner + Admin |
| `platform.service_unhealthy` | monitoring-service | serviceName, incidentId, status, detectedAt | Admin |
| `subscription.activated` | payment-service | subscriptionId, ownerUserId, plan, activatedAt | Admin |
| `subscription.expired` | payment-service | subscriptionId, ownerUserId, plan, expiredAt | Admin |

These names are proposed, not asserted to be existing event contracts.

## Payload rules

- Send IDs and business facts, not entire database entities.
- Do not put passwords, tokens, payment card data, or unnecessary PII in events.
- Include ownerUserId when the source service has it and it is required for routing.
- For admin recipients, do not publish a hardcoded admin email list in every event. Resolve current admin recipients through an agreed configuration or admin/user service contract.
- Include a deep-link target or enough stable IDs for Notification Service to construct a safe, authorized route.
- Version schemas when breaking changes are required.
- Document which service owns each event and who approves schema changes.
