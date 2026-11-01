---
name: knock-notifications
description: Implement and troubleshoot Knock workflows, recipient identity, in-app feed, email delivery, and secure server-side triggering in the WebBlock Spring Boot notification microservice.
---

# Knock Notifications Skill

Use this skill whenever editing Knock integration, workflow triggering, recipient identification, Knock frontend feed setup, email delivery, or workflow troubleshooting.

## Core concepts

- A Knock workflow has a unique workflow key and contains channel/function steps.
- A workflow run is executed for a recipient; a trigger can target one or more recipients.
- A Knock recipient is normally a user in WebBlock. Use the stable WebBlock user ID as Knock's user ID.
- Recipient properties such as `email` and `name` are used for delivery and personalization.
- Knock workflow preferences can affect whether a channel sends.
- Workflows must be committed/published to the target Knock environment before that environment can execute them.
- A workflow trigger returns a workflow run identifier.
- Knock supports `Idempotency-Key` for the workflow trigger endpoint; reuse the same key when retrying the same logical trigger.

## WebBlock-specific rules

1. Notification Service already has Knock credentials configured and the user has created workflows. Inspect and preserve the current implementation; do not recreate it by default.
2. Keep `KNOCK_API_KEY` server-side only. Never expose it to Next.js/browser code.
3. Frontend uses the Knock public key and authenticated stable user ID.
4. Fetch email from User Service; do not trust arbitrary event-supplied email as authoritative.
5. Identify/upsert the Knock recipient before triggering an email workflow, unless the codebase deliberately uses inline recipient identification.
6. Store workflow keys in configuration or a central enum/registry.
7. Map event + recipient + allowed channels using `docs/notification-matrix.md`.
9. Current channels are Web in-app feed and Email. Do not implement mobile push unless explicitly requested.
9. Do not invent workflow keys. Inspect existing config and ask if the key is unknown.
10. If Knock returns `workflow_missing`, verify exact key, environment, and whether the workflow was committed/published. Do not blindly change the key or retry indefinitely.

## Recommended logical sequence

```text
RabbitMQ event
  -> validate event
  -> deduplicate event
  -> resolve recipient user ID and role
  -> development recipient provider/config (now)
  -> User Service: get authoritative email/profile (later, when ready)
  -> Knock: identify/upsert recipient
  -> Knock: trigger workflow with event data + Idempotency-Key
  -> record processing result
  -> acknowledge message
```

## Trigger payload design

Keep workflow data minimal and explicit. A typical payload may include:
- `event_id`
- `event_type`
- `website_id`
- `website_name` (if available and needed)
- `deployment_id` (for deployment events)
- `status`
- `occurred_at`
- `action_url`

Do not send secrets, raw logs, full database objects, or payment-sensitive information to Knock.

## Workflow organization

- Prefer one workflow key for the same event journey when recipients receive the same intent and channel behavior.
- For owner and admin audiences, use explicit recipient resolution and workflow conditions when content/channel logic can be expressed clearly.
- Split into separate workflows only when the message intent, templates, lifecycle, or channel behavior is materially different and the team can maintain the separation.
- A shared workflow may trigger one run per recipient. Do not assume a single run means a single recipient.
- For a shared owner/admin workflow, use conditions or recipient-specific data only if the conditions are robust and tested. Avoid fragile logic based on display names or email domains.

## Error handling

| Failure | Handling |
|---|---|
| 400 invalid payload | Permanent; log safe details, DLQ/quarantine |
| 401/403 | Configuration/credential incident; do not infinite retry |
| workflow missing/inactive | Check key + environment + publish/commit; treat as configuration failure |
| 404 user missing in User Service | Apply agreed missing-recipient policy; do not invent email |
| 429 | Retry with bounded backoff and respect Retry-After if available |
| 5xx/network timeout | Bounded retry; same idempotency key |
| Duplicate event | Skip safely after deduplication |

## Existing project status

The user has already configured the Knock key in Notification Service and created workflows in Knock. Treat these as existing project assets:
- inspect current key loading/configuration without revealing the secret
- inspect existing workflow keys/environment and reuse them
- do not ask the user to recreate workflows
- do not overwrite existing Knock config

User Service is not ready yet. Do not create a fake production endpoint or assume its response shape. Keep recipient resolution behind a development provider now and a User Service adapter later.

## Before changing Knock code

- Inspect the current HTTP client/SDK and API version.
- Inspect existing `KnockClient`, `NotificationService`, properties, DTOs, and tests.
- Verify the workflow key and environment.
- Verify whether recipient identification already occurs elsewhere.
- Do not create a second Knock client or duplicate identity-sync path.

## Official references

- Knock docs: https://docs.knock.app/
- Workflows: https://docs.knock.app/concepts/workflows
- Triggering workflows via API: https://docs.knock.app/send-notifications/triggering-workflows/api
- Identifying recipients: https://docs.knock.app/managing-recipients/identifying-recipients
- Core concepts and environments/commits: https://docs.knock.app/concepts/overview
- Knock AI instructions: https://knock.app/ai-instructions

This is a project-local skill written for this repository. It is not an official Knock-maintained Claude skill.
