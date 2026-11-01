---
name: notification-testing
description: Test WebBlock notification event routing, recipient resolution, Knock workflow triggering, retry behavior, idempotency, and channel policy.
---

# Notification Testing Skill

## Required test scenarios

### Event handling
- Valid supported event.
- Missing required envelope field.
- Unknown event type.
- Unsupported event version.
- Duplicate event ID.
- Redelivery after transient failure.

### Recipient resolution
- Owner-only event.
- Admin-only event.
- Owner + admin event.
- User Service returns not found.
- User Service timeout/5xx.
- Admin recipient configuration is empty or invalid.

### Knock
- Recipient identify/upsert succeeds.
- Workflow trigger succeeds.
- Invalid secret / 401.
- Workflow missing or inactive.
- Rate limit / 429.
- Knock 5xx or network timeout.
- Trigger retried with the same idempotency key.
- Workflow is called with correct recipient ID and minimal data.

### Channel policy
- Web-only event does not send email.
- Email-only event does not create an in-app feed message.
- Web + email event sends both only when specified.
- Mobile is not enabled in the current scope.

### Authorization and privacy
- User cannot read another user's notification feed.
- Browser bundle does not contain the Knock secret key.
- Logs do not include credentials or unnecessary PII.

## Test design

- Unit-test event-to-workflow and channel mapping without Spring context where possible.
- Mock User Service and Knock clients in application tests.
- Use a local/test RabbitMQ or Testcontainers for listener integration tests if available.
- Use a dedicated Knock development environment for manual end-to-end verification.
- Never use production recipients or production workflow runs in automated tests.
