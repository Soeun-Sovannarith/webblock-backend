# Notification Service Architecture

## Current project status

- Notification Service is a Spring Boot microservice.
- RabbitMQ is used to receive domain events from other microservices.
- Knock API credentials are already configured in Notification Service.
- Knock workflows have already been created. Reuse and verify them; do not recreate them by default.
- User Service is not ready for integration yet. Real profile/email lookup is a later phase.
- Current channels: Web in-app feed and Email. Mobile is outside the current implementation scope.

## Current development flow

```text
Source microservices
       |
       | publish domain events
       v
    RabbitMQ
       |
       v
Notification Service (Spring Boot)
  1. validate event
  2. deduplicate event
  3. map event to recipient + workflow
  4. resolve test recipient via development adapter/config
  5. identify/upsert recipient in Knock
  6. trigger existing Knock workflow
       |
       v
      Knock
       |
       +------------------+
       |                  |
       v                  v
  Web in-app feed       Email
```

## Future flow when User Service is ready

```text
RabbitMQ -> Notification Service
                  |
                  v
          Recipient Resolver
                  |
                  v
             User Service
          (user ID + email)
                  |
                  v
                Knock
          identify + trigger
                  |
             +----+----+
             |         |
             v         v
            Web      Email
```

## Staged recipient resolution

Keep recipient lookup behind an application interface, for example `RecipientProvider` or an equivalent pattern already used in the repository.

### Phase A — now: development provider

- Use a clearly named development/test implementation or configuration-backed recipient fixture.
- Keep it isolated from production profiles.
- Use stable test user IDs and test email addresses only.
- Do not hardcode a real user's email in source code.
- Do not treat an email field arriving in a RabbitMQ event as authoritative production identity data.
- If the current workflow can be tested using recipient IDs already known to Knock, use those IDs and avoid unnecessary profile lookup.
- Keep the Knock recipient identification/trigger code independent of where recipient data comes from.

### Phase B — later: User Service provider

When User Service and its API are ready:
- Confirm the actual endpoint, DTO, authentication, and service URL with its team.
- Implement a production `RecipientProvider` adapter that fetches the user by stable user ID.
- Return the stable ID and authoritative email/profile fields needed by Knock.
- Add timeouts, service authentication, error classification, and tests.
- Switch provider through environment/profile configuration; do not rewrite RabbitMQ event handling or Knock workflow orchestration.

Do not invent a User Service URL or endpoint now.

## Knock integration

- Preserve the existing Knock API key configuration and current client implementation.
- Never print, expose, or commit the secret key. Use the existing secret manager/environment variable approach.
- Inspect the actual workflow keys and Knock environment configured in the project.
- Reuse the workflows the user has already created.
- Confirm workflows are committed/published in the correct Knock environment.
- Use stable application user IDs as Knock recipient IDs, not email addresses.
- Keep workflow key mapping centralized.
- Use Knock's supported idempotency mechanism for workflow triggers where available.
- Do not create duplicate workflows or a second Knock client without a demonstrated need.

## RabbitMQ reliability

- Inspect existing exchange, queue, routing keys, bindings, message converter, retry, and DLQ settings before editing.
- Assume at-least-once delivery and possible redelivery.
- Use event ID deduplication and workflow trigger idempotency.
- Use bounded retry/backoff and a dead-letter path for poison messages.
- Consider transactional outbox in producer services where loss between DB commit and publish is unacceptable.

## Failure handling and retry (verified by tests, Phase 1)

How a failed message is treated. The rule is: retry what can pass later, dead-letter at once what never will.

| Failure | Treated as | What happens |
|---|---|---|
| Knock 400, 401, 403, 404 (incl. a missing workflow), 422, other 4xx | permanent | no retry, dead-letter queue (DLQ) at once |
| Knock 408, 409, 425, 429, 5xx, network error, unknown error | temporary | bounded retry, then DLQ |
| Event fails validation, or the body cannot be read | permanent | no retry, DLQ at once |
| Owner or contact lookup not integrated (see below) | permanent | no retry, DLQ at once |

- A permanent failure is any exception that implements `PermanentFailure` (`common/domain`). The rule that stops the retry is
  `ListenerRetryConfig`; it plugs into the retry Spring Boot builds from `spring.rabbitmq.listener.simple.retry.*`.
- Number of tries: 5 in total (1 + `max-retries: 4`). Delay: 1s, doubling, at most 10s.
  Spring Boot 4 renamed `max-attempts` to `max-retries` and **silently ignores the old key**. Before this change the service
  made 4 tries, not 5. A test now fails if the old key comes back.
- After the last try the message is rejected without requeue, and RabbitMQ moves it to `<queue>.dlq` (queue arguments are tested).
- Knock's `Retry-After` header is not honoured; the fixed backoff above is used for a 429.
- A 409 is treated as temporary because it may mean "same Idempotency-Key still being processed". Not verified with Knock.
- Logs carry the status code and the kind of error only, never the Knock response body, the email or a stack trace.

**Not verified:** that a real RabbitMQ broker routes a rejected message into the `.dlq` queue. The tests run the real listener
retry and the real queue arguments without a broker (no Testcontainers in this phase). Replaying messages from a DLQ is a manual step.

## Owner notifications on Kubernetes (until the real clients exist)

The real `ProjectOwnerClient` (project-service) and `UserContactClient` (user-service) are not written yet. Outside the
`local-stub` profile the service uses `UnavailableProjectOwnerClient` and `UnavailableUserContactClient`:

- The service starts under the `k8s` profile. Before this change it failed to start (`NoSuchBeanDefinitionException` for `ProjectOwnerClient`).
- They return no owner and no contact and never invent one: **notifications for an owner cannot be delivered** until the real clients are integrated.
  Such messages are dead-lettered at once and kept in their `.dlq` queue. **Do not purge those DLQs**; replay them after the real clients ship.
- Admin-only notifications do not need an owner lookup and keep working (subscription activated, subscription expired, generation stuck,
  platform service down). For a failed deployment the admins are told first, then the owner lookup fails closed.
- Delete the two `Unavailable*` classes when the real clients are added.
- Affected today: generation completed, deployment succeeded/failed (owner part), payment due, website crashed (owner part), resource limit (owner part).

## Idempotency and its limits

Duplicate protection relies on Knock only: every trigger carries `Idempotency-Key = <message type>:<event key>:<workflow>:<recipient id>`.
The service keeps no table of processed events (no inbox). Whether to add one is a team decision, not made here.

- **Window: 24 hours.** Knock's official docs say a retry with the same key within 24 hours returns the original response, and call
  24 hours the default idempotency window ("Triggering workflows via API" and "Idempotent requests", docs.knock.app, read 2026-10-30).
- **Trade-off 1:** a message replayed from a DLQ more than 24 hours after the first attempt can send a second notification.
- **Trade-off 2:** the service cannot see that Knock dropped a repeat; there is no local record to audit.
- **Trade-off 3:** Knock returns an error when the same key arrives with different parameters. Which HTTP status that is **has not been
  verified**; a 4xx would be classed as permanent above.
- Tests prove that the key is the same for the same event delivered twice (also with a new `message_id`) and different for a different event.
  They do **not** prove that Knock drops the repeat: no test calls Knock.

## Knock behind an infrastructure client (Phase 2)

The application layer no longer touches the Knock SDK:

- `KnockNotificationService` (application) only orders the work: identify the recipient, then trigger the workflow, with the
  event context (`eventId`, `recipientRole`) added to the data. It logs failures with that context.
- `KnockDeliveryClient` (`infrastructure/client`) is the interface; `KnockSdkDeliveryClient` (`infrastructure/client/knock`) is the
  only class that imports the SDK. It also decides permanent vs temporary and times each call.
- The exception types live in `common/domain` (`NotificationDeliveryException`, `PermanentNotificationDeliveryException`,
  `PermanentFailure`). `LayerBoundaryTest` fails if the SDK is imported outside `infrastructure`.
- Nothing changed on the wire: same workflow keys, same data keys, same `Idempotency-Key`.

## Observability (Phase 2)

- **Endpoint:** `GET /actuator/prometheus` (needs `micrometer-registry-prometheus`, the only dependency added in Phase 2). Every metric
  carries `application="notification-service"`. It is open without a token so Prometheus can scrape it, and it is not under
  `/api/notification/v1`, so the gateway and Istio routes do not publish it. **Before uat/prod, confirm it is reachable only from
  inside the cluster (NetworkPolicy / Istio).** `/actuator/info` still needs a token; no other actuator endpoint is exposed.
- **Metrics** (no id, email or message body is ever a tag):

  | Metric | Tags | Meaning |
  |---|---|---|
  | `webblock_notification_events_received_total` | `queue` | messages delivered to a listener (retries inside the listener are not counted again) |
  | `webblock_notification_events_dead_lettered_total` | `queue` | messages rejected without requeue, i.e. moved to the `.dlq` |
  | `webblock_notification_delivery_total` | `workflow`, `outcome` | one count per try to hand a notification to Knock: `success`, `temporary_failure`, `permanent_failure` |
  | `webblock_notification_knock_call_seconds` | `step` (identify/trigger), `outcome`, `status_class` (2xx..5xx/none) | time spent in one Knock call; `status_class="4xx"` with `permanent_failure` is the credentials/workflow incident |
  | `webblock_notification_sender_exchange_missing` | `exchange` | 1 while a sender exchange does not exist (see below) |

  Spring AMQP's own `spring.rabbit.listener` timer is on too (`observation-enabled: true`).
- **JSON logs:** opt-in profile `json-logs` (`application-json-logs.yml`, Spring Boot's built-in `logstash` format, one JSON object per
  line, with `trace_id`, `span_id`, `request_id`, `messageId`). It is **not** enabled in `application-k8s.yml`: switching the
  deployment to it is a deployment-configuration change that needs separate approval (add `json-logs` to `SPRING_PROFILES_ACTIVE`).
- **Trace context:** the service continues the incoming W3C `traceparent` and `x-request-id` (RabbitMQ message headers, and HTTP headers
  through `TraceContextFilter`). They are untrusted input: only the exact format is accepted (this also blocks log injection); a bad
  `traceparent` is dropped, a missing or bad request id is replaced by a new one. The context covers every retry of a message and is
  removed when the message is done. No tracing library creates spans here. `TraceContext.outgoingHeaders()` gives the headers to add
  to calls to our OWN services once the real clients exist; it has no caller yet, and the headers must never be sent to Knock.

## Sender exchanges: who owns them (Phase 2)

The exchanges `project.events`, `provisioning.events` and `payment.events` belong to the sending services. This service declares only
its own queues, dead-letter queues and bindings.

- Setting `webblock.notification.rabbitmq.declare-sender-exchanges` (env `NOTIFICATION_DECLARE_SENDER_EXCHANGES`), default **false**.
  Use `true` only on a fresh LOCAL broker where no sender has started yet. Never in uat/prod.
- Why not redeclare (shown on a real broker): when a sender declared its exchange with other settings, the declaration fails with
  `PRECONDITION_FAILED - inequivalent arg 'durable'`, the service does not start and **no queue is declared**. It also needs the
  `configure` permission on another service's exchange.
- The price, also shown on a real broker: RabbitMQ refuses to bind a queue to an exchange that does not exist yet, and the service then
  runs with consumers attached but **bound to nothing**. `SenderExchangeWatcher` closes that gap. Every 15 s
  (`sender-exchange-check-interval`) it asks, with a passive declare (read permission is enough), whether each sender exchange exists:
  a missing one is logged once as an ERROR and shown by `webblock_notification_sender_exchange_missing{exchange}` = 1 (**alert on it**);
  once all exist the service binds its queues by itself, no restart (verified: 5 bindings appeared within the check interval).
  Broker unreachable = nothing changes (the listeners reconnect and redeclare on their own). It runs only when the listeners
  start automatically. It is deliberately not part of `/actuator/health`, so a Kubernetes probe cannot start a restart loop that
  cannot fix a missing exchange; whether it should feed a readiness probe is a deployment decision.
- Platform prerequisite: create the sender exchanges before (or together with) the first deployment, or accept that the service waits
  for them, with the gauge at 1, until the senders start.

## Is an inbox table needed? (analysis, no decision yet)

Not implemented. Duplicate protection stays Knock-only (24 h window, see above). Findings:
- Redelivery and sender outbox re-publishes happen within minutes, inside the window.
- The real gap: an Alertmanager alert for an incident longer than 24 h keeps its idempotency key (that is the intent of
  `HandleAlertmanagerWebhookHandler`), but after 24 h Knock accepts it again, so such an incident re-notifies about once a day.
- An inbox that records a delivery only after success would close that gap, but would not help the other residual case (Knock
  accepted a trigger and the reply was lost, then a DLQ replay after 24 h); a "claim before work" inbox would turn the service into
  at-most-once (a crash after the claim loses the notification).
- Options: keep as is; a Redis "delivered" marker (TTL 7-30 d, new dependency and infrastructure); a Postgres inbox table (the platform
  pattern, durable and auditable, but a database, migrations, a cleanup job and provisioning for a service that has none).
- Needed from the team: is a roughly daily re-notification for incidents longer than 24 h acceptable?

## Tests and the developer's real RabbitMQ (Phase 2)

A broker may run on `localhost:5672` with real queues. Tests must never attach a consumer to it:
- `spring.rabbitmq.listener.simple.auto-startup=false` in every test context that does not consume messages;
- `src/test/resources/spring.properties`: `spring.test.context.cache.pause=never`. Spring Framework 7 pauses an idle cached context and
  `start()`s it again on reuse, which starts every lifecycle bean, **including listener containers that are not auto-start**; one real
  connection per full run came from this;
- `src/test/resources/config/application.yml`: the default RabbitMQ port in tests is `1`, so a context that still tries to connect reaches nothing;
- `TestIsolationGuardTest` fails if the last two are removed.

Opt-in tests against the LOCAL Docker RabbitMQ (`LocalBrokerIntegrationTest`, `LocalBrokerSenderExchangeTest`) run only with
`NOTIFICATION_BROKER_IT=local` (`./gradlew cleanTest test --tests '*LocalBroker*'`), use their own throw-away vhost
`notification-it-xxxxxxxx` (created and deleted through the management API, and deleted at JVM exit even if a context fails to load),
fix the host to `localhost`, and mock Knock. They never touch vhost `/`.

## User Service is not an email sender in this design

User Service will provide user identity/profile/email data when available. Knock remains responsible for sending email through its configured email channel. Do not send the same message from both systems.

## Persistence

Do not copy website, deployment, subscription, or payment business tables into Notification Service.

Decide whether local storage is needed for:
- processed-event/inbox deduplication
- processing audit metadata
- local notification read state only if Knock is not the chosen feed/read-state source

Inspect the current implementation before adding tables. Knock's in-app feed may already provide the required notification feed and read/unread behavior.
