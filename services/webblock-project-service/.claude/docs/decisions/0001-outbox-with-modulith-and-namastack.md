# 0001 Outbox with Spring Modulith and Namastack

## Decision

Outgoing events and durable local jobs go through a transactional outbox built from libraries, not by hand:
Spring Modulith event externalization (mode `outbox`) stored by Namastack Outbox and sent by Namastack's Rabbit module.

- A feature publishes an `IntegrationEvent` inside its `@Transactional` method. The outbox row is written in the same
  transaction, keyed by the project ID. A rolled-back transaction sends nothing.
- A record is marked `COMPLETED` only after RabbitMQ's publisher confirm (10 s timeout). Broker sends retry forever
  with backoff from 1 s up to 60 s. Records of one key are handled oldest first.
- A durable local job is `Outbox.schedule(job, key)` with an `OutboxTypedHandler`; it never reaches the broker.
- The outbox tables and the inbox (`processed_messages`) share the schema `project_messaging`. They come only from
  Flyway (`V4__outbox.sql`); Namastack's own table creation is off.
- `project.events` has the alternate exchange `project.events.unrouted` (fanout, quorum queue of the same name), so an
  event no consumer is bound for is kept, not dropped.
- One-thread `MessagingConfig.outboxDefaultScheduler`, so two instances never send the same record at once.
- Completed records are kept. Any future purge keeps `payment:` records (the admission release fence counts them).
- Forbidden: `spring-modulith-events-amqp` (every event would be sent twice), Modulith's JDBC event publication
  registry, and `@ApplicationModuleListener` in production code (its work would not be recorded). ArchUnit enforces
  the last one.
- Namastack stays at the version the Modulith BOM is built against, with the Kotlin runtime that version declares
  (`.claude/docs/dependency-versions.md`).

## Reason

Codegen and Provisioning react to Project's events, so an event must never be lost or sent without its business
change. A tested library pair gives this with configuration only. Spike S1 showed on real RabbitMQ and PostgreSQL
that every needed property holds (`OutboxSpikeTest`). The fallback, a hand-written `SKIP LOCKED` relay, was not needed.

## Status

Accepted. Built and in use. Operating it: `.claude/docs/runbooks/outbox.md`.
