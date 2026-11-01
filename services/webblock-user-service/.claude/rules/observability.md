---
paths:
  - "**/*.java"
  - "**/*.py"
  - "**/application*.yml"
  - "services/webblock-aicode-service/app/**"
---
# Logs, metrics, traces (Notion section 7.3) and what must never be logged

Istio does most of the telemetry (metrics, access logs, trace spans for each call) with no code in the service.
There is no separate telemetry library. `web-block-commons` (when it exists) only glues the IDs together.

## Every service must
- Expose `/actuator/health` (Java) or `GET /health` (Python); `management.endpoints.web.exposure.include: health,prometheus`.
- Write **JSON logs to stdout** with `trace_id`, `span_id` and `request_id` on every line, so Grafana jumps from a Loki line to its Tempo trace.
- Continue the incoming `traceparent` and pass it on (gRPC metadata, HTTP header, RabbitMQ header) together with `x-request-id`.
- Put `requestId` (and `traceId` when present) in every REST answer (`rest-response-format.md`).
- Respect the sampling decision that arrives (parent based). Spans inside a service are not in the first version.

## Never log (business and security rule)
| Never | Why |
|---|---|
| tokens, `Authorization` header, cookies, session IDs | anyone with the log can act as the user |
| passwords, OTPs, reset codes | account takeover |
| KHQR data, bank account numbers (`sender_account_no`), card-like numbers | payment data |
| full e-mail addresses, phone numbers, full names | personal data; log the `user_id` instead |
| request bodies of auth/payment endpoints | may hold all of the above |
| API keys (`GLM_API_KEY`, `WEBILL_API_KEY`, `GITEA_TOKEN`, DB passwords) | secrets |
| stack traces in API answers or events | leaks internals; they go to the log only, tied to `requestId` |
The `log-guard` hook warns when a log line mentions these words. If you really need to log a related fact, log that it
happened ("otp verified for user_id=...") not the value.

## Log levels
`ERROR` = a bug or a lost operation (someone should look). `WARN` = something recoverable (retry, dropped stale event,
fail-closed answer). `INFO` = business milestones (release LIVE, bill PAID) — one line, IDs only. `DEBUG` = off in uat/prod.
Do not log inside tight loops. Do not log a whole entity (`toString` may include personal data).

## Metrics worth having
Count and time: outbound gRPC calls (by method, status), events published/consumed/dead-lettered, outbox backlog size,
KHQR bills by status, deploys by result. Name them `webblock_<service>_<what>`.

## Health that tells the truth
Readiness checks the database and broker connection of THAT service. Liveness is simple (process is up).
A dependency being down returns 503 `DEPENDENCY_UNAVAILABLE` to callers; it must not crash the service.

## Alerting (who is told)
Alertmanager posts to Notification's webhook. Which alert reaches which person is in the notification table of
`docs/specs/business-rules.md` — do not decide recipients inside other services.
