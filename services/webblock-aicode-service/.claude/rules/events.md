---
paths:
  - "app/services/event_bus.py"
  - "app/schemas/events.py"
  - "app/services/harness.py"
  - "app/services/project_client.py"
  - "tests/test_event_bus.py"
---

# RabbitMQ events (cross-service contract with the Project Service)

The contract is the Project Service's: `.claude/docs/contracts/service-communication.md` in its repo, section 2 and "Message conventions". README section 4 has the examples, and section 10 the whole chain.

| Direction | Exchange and routing key | Model | Handler / publisher |
|---|---|---|---|
| Consumed | `project.events`, `generation_requested.v1` → queue `ai_codegen.generation_requested.v1` | `GenerationRequestedEvent` | `harness.handle_generation_requested_event` |
| Consumed | `project.events`, `generation_cancelled.v1` → queue `ai_codegen.generation_cancelled.v1` | `GenerationCancelledEvent` | `harness.handle_generation_cancelled_event` |
| Published | `ai_codegen.events`, `generation_completed.v1` | `GenerationCompletedEvent` | `event_bus.publish_generation_completed`, on every finished run |

- **Wire format is snake_case.** Every body is the envelope (`EventEnvelope`: `schema_version`, `message_id`, `message_type`, `occurred_at`, `correlation_id` = the generation request ID, `project_id`) plus the event's own fields.
  - Events carry identifiers, not content: the input comes from Project's `codegen-input`, the result from our `GET /internal/v1/generation-runs/{id}`.
  - Serialize with the model, never a hand-built dict.
  - Inbound models ignore unknown fields. The outbound model forbids extras, because Project validates it against its schema.
- **Published messages** get:
  - a UUIDv7 `message_id` (`event_bus.uuid7`);
  - the AMQP properties `type` = `message_type` and `message_id`;
  - persistent delivery and publisher confirms.

  `outcome` is `succeeded | failed` only.
- **`project.events` belongs to Project.** Declare it passively, never with other arguments, because it has an alternate exchange. Our queues are quorum queues with `x-delivery-limit` and a `<queue>.dlq` dead-letter queue (`_declare_queue`).
- **Acknowledge only after the effect is committed** (`event_bus._handle`).
  - Nack a malformed message without requeue, so it goes to the dead-letter queue.
  - A handler that can't finish now (Project unreachable, another run active) raises `event_bus.RetryLater`. The message is redelivered after `RETRY_DELAY_SECONDS`, up to `DELIVERY_LIMIT` times.
  - Never let a handler exception escape the consumer loop.
- **One run set per request.**
  - A repeated `generation_requested` re-publishes the completion when the request's run has finished, and does nothing while it runs.
  - Check the input's JCS SHA-256 (`app/services/jcs.py`) against both the event's `captured_input_hash` and the document's `capturedInputHash`.
  - On a mismatch, end the request with a FAILED run and a completion event. Use `store.create_failed_run`, which never inserts a QUEUED run, so no worker claims it.
- **Publishing is best-effort.** `publish_event` returns `False` and logs a warning when the broker is down. Never let it raise into the pipeline or a route. There is no outbox yet; Project's deadline reconciliation reads `/internal/v1/generation-requests/{id}/runs`.
- **A cancelled event** marks the active run `FAILED` with `error_code='CANCELLED'`. There is no CANCELLED status, and cancelling publishes no completion.
- **Versioning.** A `v1` schema only gains optional fields. A breaking change is a new `v2` routing key, published or consumed alongside `v1` until the other services move, and the user must agree.
- **Every change** updates `README.md` section 4 and `tests/test_event_bus.py`, which fakes `pika.BlockingConnection`. Use the `change-event-contract` skill.
