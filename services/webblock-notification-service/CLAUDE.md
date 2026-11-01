# WebBlock Notification Service — Claude Code Instructions

## 1. Mission

Work on the WebBlock Notification Service: a Spring Boot microservice that consumes business events from multiple WebBlock services through RabbitMQ, resolves the intended recipients, and uses Knock to deliver notifications through:
- Web in-app notification feed
- Email

The product specification also describes mobile notifications. Mobile push is future/out-of-scope for the current implementation unless the user explicitly asks to add it.

The complete list of related microservices, event producers, and technical contracts is not yet finalized. Build a flexible foundation and integrate services progressively as their contracts become available. Do not assume that every related service already exists or is ready for integration.

## 2. Required workflow before changing code

1. Inspect the repository before proposing or editing code:
   - module and package structure
   - Spring Boot and Java versions
   - Gradle/Maven build
   - existing RabbitMQ exchanges, queues, bindings, message converters, retry/DLQ configuration
   - existing User Service client and authentication mechanism, if any
   - Knock integration, configured workflow keys, environment, and frontend feed integration
   - database entities/migrations, if any
2. Reuse existing conventions and dependencies. Do not add a second HTTP client, RabbitMQ setup, or persistence layer if the project already has one.
3. Identify and confirm the source service and event contract before implementing a consumer. If they are not finalized, document the missing details and treat examples as proposals.
4. Explain the planned flow and files to change briefly, then implement the smallest complete change.
5. Run the narrowest relevant tests, then the full build if practical. Report exactly what ran and what failed.
6. Never claim a Knock workflow exists or is published unless verified in the repository or Knock environment.
7. Preserve existing working configuration and workflows. Do not recreate or rename them without an explicit reason and approval.

## 3. Architecture boundaries

### Notification Service owns
- Consuming notification-relevant domain events from RabbitMQ.
- Validating and normalizing event payloads.
- Resolving recipient IDs and recipient roles through the currently configured recipient provider.
- Calling User Service (or an established shared identity API) to retrieve authoritative recipient details such as email and display name when that integration is available and agreed.
- Identifying/upserting the recipient in Knock using a stable application user ID.
- Mapping event type to workflow key, recipient(s), and template data.
- Triggering Knock workflows.
- Handling retries, duplicate delivery, dead-lettering, observability, and operational failures.
- Exposing only notification-specific APIs if required by the existing product design.

### Other business services own
- Their domain state and business rules.
- Publishing facts about completed/committed domain events.
- Supplying stable identifiers and event context needed by consumers, according to an agreed event contract.
- They must not call Knock directly unless the architecture is deliberately changed.

### User Service owns
- User identity and authoritative user profile/contact data.
- A service-to-service API for retrieving user details, if one exists or is agreed.
- It is not automatically the email delivery provider. Knock should deliver email through the configured Knock email channel unless the real system explicitly says otherwise.

### Knock owns
- Workflow execution and channel-step configuration.
- In-app feed and email delivery through configured channels.
- Email templates, branching/conditions, and preference evaluation configured in Knock.
- Delivery/engagement tracking available through Knock.

### Frontend owns
- Authenticated in-app feed UI and unread/read interactions.
- Identifying the signed-in user to Knock with the public key only, following the current Knock frontend SDK pattern.
- Never exposing the Knock secret API key.

## 4. Extensible microservice integration

The Notification Service must support events from multiple microservices across the platform. The complete list of related services and their event contracts may be defined later.

### Architecture principles
- Support multiple microservices as event producers through RabbitMQ.
- Keep core notification orchestration independent of individual business services.
- Keep service-specific integration logic separate from notification processing.
- Use reusable, versioned event contracts and explicit event-type-to-workflow mapping.
- Make adding a new event producer straightforward without rewriting the entire notification architecture.
- Do not build the system around a fixed, assumed list of producer services.

### Do not invent technical contracts
- Do not invent or assume actual service names, exchange names, queue names, routing keys, event names, payload fields, endpoints, or authentication mechanisms.
- Treat examples in documentation or code as proposed examples until confirmed by the repository, the owning team, or the user.
- Inspect existing RabbitMQ configuration and project conventions before adding queues, bindings, or listeners.
- Confirm the source service and its published event contract before implementing a production integration.

### Progressive integration process
For each new service integration:
1. Identify the source microservice and its owner.
2. Confirm the business event and when it is published.
3. Confirm the exchange, routing key, queue/binding strategy, and payload schema/version.
4. Identify the recipient or recipient-resolution rule.
5. Map the event to the appropriate Knock workflow and channel policy.
6. Implement and test the integration independently.

Do not block the reusable notification foundation on services that are not ready. Use clearly marked development/test fixtures or adapters where needed, without presenting them as production integrations.

## 5. Event-driven processing rules

- Prefer domain events that describe facts that have already happened, e.g. `deployment.succeeded`, not commands such as `send.deployment.email`.
- Events should include a stable `eventId`, `eventType`, `occurredAt`, `version`, and enough aggregate/recipient identifiers to route the notification, once the contract is agreed.
- Use a stable event ID for deduplication. RabbitMQ delivery is generally at-least-once; assume a message can be redelivered.
- Use Knock's `Idempotency-Key` for workflow trigger requests where supported. Use the same key for retries of the same logical notification.
- Acknowledge a RabbitMQ message only after processing succeeds or after it is safely persisted for retry, according to the repository's established reliability design.
- Do not silently discard malformed or unprocessable messages. Log safe metadata and route them to the configured DLQ after the agreed retry policy.
- Do not retry permanent errors forever (e.g. invalid workflow key, invalid payload, unknown event type). Classify transient vs permanent failures.
- Never log API keys, access tokens, full email addresses, or sensitive message content.
- Do not put secrets in source code, committed config, test fixtures, or generated docs.

## 6. Recipient resolution

User Service is not currently ready for integration. Do not invent its endpoint, API contract, or authentication configuration, and do not call a guessed endpoint.

- Keep recipient resolution behind a replaceable abstraction/provider.
- For development and testing, use an isolated configured test recipient provider or fixture.
- Clearly distinguish test recipient data from production user data.
- Never hardcode a real person's email or silently fall back to a test email in production.
- When User Service becomes available, implement a dedicated adapter using its confirmed API and authenticated service-to-service mechanism.
- Retrieve authoritative user identity/contact details from the agreed source. Do not treat an arbitrary email in an event as authoritative if it can be resolved from User Service.
- Keep the event consumer and Knock orchestration independent of the specific recipient provider.

## 7. Knock implementation rules

- Use the server-side Knock secret API key only in Notification Service.
- Use the Knock public key only in the frontend.
- Use stable application user IDs as Knock recipient IDs. Do not use email address as the primary recipient ID.
- Ensure the recipient is identified in Knock with the authoritative email address before triggering an email workflow, unless the existing implementation uses a verified inline-recipient strategy.
- Trigger a committed/published workflow key. A workflow that exists only as an uncommitted draft may return `workflow_missing` in the target environment.
- Keep workflow keys in configuration or a single registry; do not scatter string literals through consumers.
- Keep template presentation in Knock. Keep business eligibility and recipient resolution in Notification Service or the owning service, unless the rule is explicitly designed as a Knock workflow condition.
- Prefer one workflow per event/notification journey when the channels share the same recipient and content intent. Use branching or conditions inside the workflow for legitimate recipient/channel differences. Do not create owner/admin variants by default if one workflow can safely handle both.
- Never assume a workflow key, channel ID, environment, or endpoint. Verify it against the existing configuration and Knock environment.
- Preserve existing configured Knock API credentials and workflows. Do not expose secrets or recreate existing workflows unnecessarily.
- Use Knock's supported workflow-trigger idempotency header/API option when triggering.
- Handle Knock 4xx permanent errors separately from 429/5xx/network transient errors.

## 8. Knock email UI and template design

Knock is responsible for delivering email notifications and in-app notifications through configured workflows. The Notification Service triggers the appropriate workflow and supplies recipient identity and event data. Do not implement a separate direct email-delivery mechanism unless explicitly requested and approved.

### Email template design principles
- Use a clean, professional, responsive email layout.
- Maintain consistent WebBlock branding across notification types.
- Make the most important alert information immediately visible.
- Use clear headings, concise descriptions, and a prominent, meaningful CTA when an action is available.
- Make the email understandable without requiring the user to open the application.
- Keep layouts compatible with common email clients; avoid relying on unsupported CSS or complex interactions.
- Avoid decorative elements that distract from the alert's purpose.
- Use accessible contrast, readable typography, and a clear content hierarchy.

### Dynamic template data
Use dynamic data supplied by the Notification Service rather than hardcoding user-specific or event-specific information. Potential variables include:
- user name
- website or project name
- event type and description
- event timestamp
- relevant status
- error or warning details
- action URL

Only use variables that are included in the confirmed event/workflow payload. Do not invent template variables or assume a field exists.

### Notification email categories
Design templates according to the actual notification requirements, which may include:
1. Success notifications — communicate completed actions.
2. Failure notifications — explain what failed and what action the recipient can take.
3. Warning notifications — highlight issues requiring attention.
4. Payment notifications — communicate payment status, due dates, and relevant actions.
5. System alerts — communicate service or platform issues to administrators.

### Workflow and template mapping
- Map each notification event to the correct existing Knock workflow.
- Verify that the workflow exists and is committed/published in the correct Knock environment.
- Verify that the workflow receives the expected recipient and data.
- Ensure the email template uses only variables available in the actual workflow payload.
- Reuse existing workflows and templates where appropriate; do not create duplicates by default.
- Keep recipient-specific conditions and channel behavior inside the appropriate workflow when that is the agreed design.

### Before changing an email template
1. Inspect the existing workflow and email template.
2. Understand the notification requirement and intended recipient.
3. Reuse existing branding and design conventions where appropriate.
4. Avoid changing unrelated workflows or templates.
5. Verify dynamic variables against the actual workflow payload.
6. Preview and test the rendered email where the available Knock tooling supports it.
7. Report any unverified rendering/client compatibility rather than claiming it was tested.

## 9. Web and email channel policy

Current implementation scope:
- Web: in-app feed notifications visible in WebBlock user or Admin Portal.
- Email: important owner alerts and critical admin alerts as specified in the feature matrix.
- Mobile: not implemented in this scope.

Do not send a channel merely because the workflow has that channel configured. Preserve the event/recipient/channel policy in `docs/notification-matrix.md`. If requirements change, update the matrix and obtain confirmation before expanding scope.

## 10. Security

- When User Service integration is implemented, require authenticated service-to-service communication using the confirmed project mechanism. Until then, keep development recipient data isolated and never expose it as a production fallback.
- Validate event schemas and constrain payload sizes.
- Treat RabbitMQ messages as untrusted input even when published internally.
- Do not accept a recipient email address from an arbitrary event as authoritative if it can be resolved from User Service.
- Authorize notification read/mark-read APIs by the authenticated user ID; users must never read another user's feed.
- Keep admin recipients and owner recipients separate by explicit role/recipient resolution.
- Do not expose Knock secret key, RabbitMQ credentials, or internal service credentials to the browser.
- Use environment variables or the repository's secret manager for credentials.

## 11. Data ownership and persistence

Do not create a duplicate copy of all business data (websites, deployments, subscriptions, payments) inside Notification Service.

Persist only data required by the notification service's own responsibilities, if required by the chosen design:
- processed event/idempotency records
- delivery/processing audit metadata
- notification preferences or read state only if the selected architecture requires local ownership

Knock's in-app feed can be the feed store if that is the chosen product architecture. Do not create a second local notification table without checking current implementation and product requirements. If local persistence is needed for audit, define exactly which fields are stored and why.

## 12. Coding conventions

- Follow the repository's existing Java version, package naming, DTO, exception, logging, and test conventions.
- Prefer constructor injection.
- Keep RabbitMQ listener, event validation, recipient resolver/provider, workflow mapper, User Service client, and Knock client as separate responsibilities.
- Use typed event DTOs and explicit event-version handling when contracts are confirmed.
- Keep configuration in typed `@ConfigurationProperties` where the project already uses that pattern.
- Avoid broad catch-and-ignore blocks.
- Avoid hardcoded URLs, workflow keys, credentials, queue names, or recipient IDs.
- Add unit tests for mapping and recipient rules, integration tests for message consumption where practical, and contract tests for external clients where practical.

## 13. Definition of done

A notification feature is not complete until:
- source event contract and version are known and confirmed
- correct recipient(s) are resolved through the active provider (isolated development adapter now; User Service adapter later)
- correct workflow key is selected and verified
- recipient has the correct Knock identity/email data
- web/email channel policy matches the product matrix
- duplicate delivery is controlled
- retry/DLQ behavior is defined
- errors are observable without leaking secrets/PII
- tests cover success, invalid payload, missing user, Knock failure, and duplicate event
- workflow is committed/published in the correct Knock environment
- email template variables and rendered content are checked where applicable
- frontend feed behavior is verified for the intended recipient

## 14. Reference files

Read these when relevant:
- `docs/architecture.md`
- `docs/recipient-resolution.md`
- `docs/notification-matrix.md`
- `docs/event-catalog.md`
- `docs/implementation-checklist.md`
- `.claude/skills/knock-notifications/SKILL.md`
- `.claude/skills/rabbitmq-event-contracts/SKILL.md`
- `.claude/skills/springboot-microservices/SKILL.md`
- `.claude/skills/notification-testing/SKILL.md`

## 15. Important uncertainty rule

The feature document defines product behavior, not the exact technical contracts. If a service name, queue name, exchange, event schema, User Service endpoint, authentication mechanism, database, Knock workflow key, environment, channel ID, or frontend SDK version is not present in the repository or confirmed by the user, ask or mark it as a proposal. Never invent it and present it as an existing fact.
