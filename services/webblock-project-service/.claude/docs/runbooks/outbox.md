# Outbox runbook

The Project service sends its events and runs its durable local jobs through a transactional outbox: Spring Modulith
externalization stored by Namastack Outbox 1.7.3, sent to RabbitMQ by Namastack's Rabbit module. The decision and its
rules:
`.claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md`.

## Where things are

| Thing | Where |
| --- | --- |
| Outbox records (events and jobs) | `project_messaging.outbox_record` |
| Instances and partition owners | `project_messaging.outbox_instance`, `project_messaging.outbox_partition` |
| Inbox (consumed messages) | `project_messaging.processed_messages` |
| Flyway history | `project_ops.flyway_schema_history` (nothing else lives in `project_ops`) |
| Outgoing exchange | `project.events` (topic), alternate exchange `project.events.unrouted` (fanout) |
| Events no consumer is bound for | quorum queue `project.events.unrouted` |

A record is `NEW` until its handler succeeds, then `COMPLETED` (kept; `project_app` cannot delete it). `FAILED` means a
handler's retry policy gave up. Broker sends never give up: their default policy retries forever, with backoff from
1 s up to 60 s. A key (the project ID, or the job's key) is handled oldest record first, and its later records wait
while an older one is not done.

## The gauges

Served on the management port at `/actuator/prometheus`. Each one is a single indexed query, run at scrape time.

| Gauge | Meaning | Healthy |
| --- | --- | --- |
| `webblock_outbox_oldest_unsent_age_seconds` | Age of the oldest `NEW` record (0 when none) | A few seconds; it grows while the broker is unreachable or a job keeps failing |
| `webblock_outbox_max_failures` | Highest `failure_count` among `NEW` records | 0, sometimes 1 or 2 briefly |
| `webblock_outbox_failed_records` | Number of `FAILED` records | 0. Each blocks its key until it is re-driven |

The age is the database's `now()` minus `created_at`, which the application writes, so clock skew between the two
shows up here. NaN means the query failed (the database is unreachable). Alert rules are not defined yet.

## Find a stuck record

Connect as the migrator login (the owner of the tables). The application login can read the tables too, but use it only
for reads.

```sql
-- the oldest unfinished records, with the reason of their last failure
SELECT id, record_key, record_type, handler_id, status, failure_count, failure_reason, created_at, next_retry_at
FROM project_messaging.outbox_record
WHERE status IN ('NEW', 'FAILED')
ORDER BY created_at
LIMIT 20;

-- which instance owns the record's partition, and whether that instance is alive
SELECT p.partition_number, p.instance_id, i.status, i.last_heartbeat
FROM project_messaging.outbox_partition p
LEFT JOIN project_messaging.outbox_instance i USING (instance_id)
WHERE p.partition_number = <partition_no of the record>;
```

Typical `failure_reason` values for events: `Timed out waiting for RabbitMQ publisher confirm`, `RabbitMQ send
failed`, `RabbitMQ publisher confirm was nacked`. Each points to the broker: check its health, the `project` user's
permissions and the connection settings. For a job, the reason is the job's own exception message.

## Re-drive

- **`NEW` and failing:** fix the cause. The record retries by itself within about a minute (60 s backoff cap plus ±0.5 s
  jitter). To retry at once:
  `UPDATE project_messaging.outbox_record SET next_retry_at = now() WHERE id = '<id>' AND status = 'NEW';`
- **`FAILED`:** fix the cause first, then put the record back in line:

  ```sql
  UPDATE project_messaging.outbox_record
  SET status = 'NEW', failure_count = 0, failure_reason = NULL, next_retry_at = now()
  WHERE id = '<id>' AND status = 'FAILED';
  ```

  The key's later records follow it in order.
- **A message in `project.events.unrouted`:** the broker confirmed it but no consumer queue was bound for its routing
  key. Once the consumer's queue exists, move the messages back to `project.events` with a shovel (management UI,
  "Move messages", or a shovel whose destination is the exchange `project.events` with no routing key set, so each
  message keeps its own key). Messages keep their `message_id`, so consumers that already saw one ignore it. The queue
  is
  for an operator; nothing consumes it automatically. Delivery limits and dead-lettering on RabbitMQ 4 are still open
  (Provisioning's OD-70) and apply to it too.

## Never

- Never delete, truncate or hand-mark `COMPLETED` a `NEW` or `FAILED` record: the event or job is then lost without
  trace. `project_app` cannot delete records; do not grant it.
- Never purge `payment:<projectId>` records, completed ones included: the admission release fence
  (`AdmissionReleaseFence`) reads `outbox_record` by `record_key` and `status` and counts them
  to decide whether a project may still be created; any future cleanup must keep them.
- Never edit `payload`, `record_type`, `record_key` or `handler_id`. `record_type` is a Java class name and `handler_id`
  a method signature: renaming an event, job or handler class while records are pending strands them. Drain the outbox
  first, or ship a migration that rewrites those rows.
- Never edit `outbox_partition` or `outbox_instance` while instances run; the instances maintain them.
- Never purge `project.events.unrouted` without re-publishing or recording what it held.
- Never add `spring-modulith-events-amqp` or Modulith's JDBC event publication registry; the first would send every
  event twice, the second is excluded on purpose (decision 0001).
- Never remove `MessagingConfig.outboxDefaultScheduler`: without it, two instances can send the same records at the
  same time (decision 0001).
