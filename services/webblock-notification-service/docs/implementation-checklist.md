# Implementation Checklist

## Phase 1 — Inspect and agree contracts
- [ ] Confirm Java and Spring Boot versions, build tool, package structure.
- [ ] Confirm RabbitMQ exchange, queue, routing keys, bindings, vhost, durability, DLX, retry policy.
- [ ] Agree common event envelope and event-specific payloads with source services.
- [ ] Confirm source service names and event ownership.
- [ ] User Service integration is deferred until that service/API is ready.
- [ ] For now, confirm development/test recipient provider and test user/email configuration.
- [ ] Confirm how admin recipients are resolved.
- [ ] Confirm whether Knock feed is the source of truth for web notification content/read state.
- [ ] Inspect the existing Knock API key configuration without exposing the secret.
- [ ] Verify existing Knock workflow keys and environment; reuse the workflows already created and confirm they are committed/published.
- [ ] Confirm frontend Knock SDK version, public key configuration, and authenticated user identity mapping.

## Phase 2 — Core consumer
- [ ] Configure RabbitMQ connection and typed message converter.
- [ ] Add event envelope DTO and schema validation.
- [ ] Add event type/version registry.
- [ ] Add recipient resolver for owner/admin audiences.
- [ ] Add a development/test recipient provider now.
- [ ] Add User Service client with authentication, timeout, and error mapping later, when the API is available.
- [x] Add Knock recipient identify/upsert client. (`KnockSdkDeliveryClient`, behind `KnockDeliveryClient`; Phase 2.)
- [x] Add workflow registry and trigger client. (Workflow keys in `NotificationProperties.Workflows`; trigger in the same client; Phase 2.)
- [ ] Add eventId/inbox deduplication.
- [x] Add Knock idempotency key. (Knock-only dedup, 24h window; no inbox yet, see `architecture.md`.)
- [x] Add bounded retry and DLQ behavior. (5 tries, permanent failures dead-lettered at once; broker-level DLQ routing not verified.)
- [x] Add structured logs, metrics, correlation IDs, and tracing where available. (Prometheus metrics, opt-in `json-logs` profile, `traceparent` / `x-request-id` continued; no spans. Enabling JSON logs in `application-k8s.yml` is still to be approved.)

## Phase 3 — Web + email workflows
- [ ] Implement workflow mappings from `notification-matrix.md`.
- [ ] Verify web-only and email-only rows do not accidentally send the other channel.
- [ ] Configure Knock in-app feed step for Web rows.
- [ ] Configure Knock email step for Email rows.
- [ ] Test owner and admin recipients separately.
- [ ] Verify all workflow changes are committed/published to the correct environment.

## Phase 4 — Frontend and access control
- [ ] Initialize Knock frontend provider with public key and authenticated stable user ID.
- [ ] Never ship the secret API key to frontend.
- [ ] Add bell and unread count.
- [ ] Add feed list and loading/empty/error states.
- [ ] Add mark-one-read and mark-all-read behavior.
- [ ] Add contextual navigation.
- [ ] Ensure the signed-in user can only see their own feed.

## Phase 5 — Tests and operations
- [ ] Unit-test event-to-workflow mapping.
- [ ] Unit-test owner/admin recipient resolution.
- [x] Test duplicate event delivery. (Proves a stable Idempotency-Key; Knock dropping the repeat is not tested.)
- [ ] Test malformed and unknown event types.
- [ ] Test User Service timeout, 404, and 5xx.
- [x] Test Knock 401/403, workflow missing/inactive, 429, 5xx, and timeout. (Mocked SDK client. A missing workflow is tested as 404; "inactive" is not told apart.)
- [ ] Test retry exhaustion and DLQ routing. (Partly done: retry exhaustion and queue arguments are tested; a real broker moving the message to the `.dlq` is not.)
- [ ] Test no-email and no-web matrix rows. (Partly done: who is told per event is tested; the web/email channels live inside the Knock workflows and are not verified.)
- [ ] Test workflow run in Knock dashboard and actual recipient feed/email.
- [ ] Document local run and operational recovery procedures.
