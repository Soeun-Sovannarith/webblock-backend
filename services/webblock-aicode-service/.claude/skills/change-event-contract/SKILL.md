---
name: change-event-contract
description: Add, change or version a RabbitMQ event consumed or published by this service (project.generation_requested/cancelled, ai_codegen.generation_completed), keeping the platform's snake_case envelope, README section 4 and tests in sync. Use for any event payload or routing-key change.
argument-hint: "<event and the change>"
---

# Change an event contract

The Project Service parses these payloads, and the contract is its `service-communication.md` (section 2). Never invent a field it doesn't list: ask the user to agree it with Project. Read `.claude/rules/events.md` first.

1. **Classify the change.**
   - *Additive*: a new optional field with a default. Keep the `.v1` routing key.
   - *Breaking*: a rename, removal, type change, or a field becoming required. Stop and confirm with the user. Then add a `...v2` model and routing key and keep `.v1` working (consume both, or publish both) until the other services have moved.
2. **Model** in `app/schemas/events.py`: extend `EventEnvelope`, with snake_case fields and a `Literal` `message_type` default. Inbound models ignore unknown fields; outbound ones use `extra="forbid"`.
3. **Bus** in `app/services/event_bus.py`:
   - For a new consumed event, add a queue constant `ai_codegen.<event>.v<N>`, call `_declare_queue` in `run()` with routing key `<event>.v<N>` on the producer's exchange, and add an `_on_<event>` callback that calls `_handle(...)` with the model and a `harness.handle_*` function. `_handle` acks after the handler, dead-letters malformed messages and redelivers on `RetryLater`.
   - Publishers build the model with `message_id=uuid7()` and go through `publish_event` (best-effort, returns `False`) on `ai_codegen.events` with routing key `<event>.v<N>`.
4. **Handler** in `app/services/harness.py`: idempotent per `generation_request_id` (check the durable state, not the message), and tolerant of missing `project` rows. Raise `event_bus.RetryLater` for anything that may work later.
5. **Test** in `tests/test_event_bus.py`: patch `pika.BlockingConnection` with the fake channel there. Assert the published keys are exactly the envelope plus the event's fields and match the README example, the AMQP `type`/`message_id`, and that a malformed message is dead-lettered rather than raised.
6. **README section 4**: update the payload example, field table and routing key. Tell the user which other services must change, and how.
