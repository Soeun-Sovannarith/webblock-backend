# Operator runbook: the inbox re-drive, hosting retries, project deletion, the usage event and the sub-domain backfill

Sources: wave 6, task W6-E; decision 0021.

Scope: the prototype on the local stack (`make proto-up proto-run`; `.claude/docs/runbooks/prototype-demo.md`). This
runbook covers the operator's tasks for decision 0014 section 21 (answers 2 and 4, ruling 11) and section 23 (item 4 and
the lead's rulings). It proves nothing about a production database, broker or cluster.

The real-cluster rule of `.claude/CLAUDE.md` section 0 applies unchanged. None of these steps runs a cluster client,
except the optional `kubectl` checks in the "Hosting retries" section. Those use
`bin/kubectl --kubeconfig bin/kind-state/proto/kubeconfig` only, after you check that the context is
`kind-webblock-proto` on `https://127.0.0.1:<port>`. Never print `bin/kind-state/proto/provisioning/provisioning.env` or
`env/*.env`; they hold the passwords.

## 1. The inbox re-drive (ruling 11)

Project's "source published" events are stored in `provisioning_ops.source_published_events`. The build worker's
processor confirms the repository with Project's registry. When the registry does not answer within the row's attempt
budget (`INBOX_MAX_ATTEMPTS`) or its deadline (`INBOX_DEADLINE`), it parks the row as `failed` with
`repository_unconfirmed`. Nothing retries a failed row by itself. An operator decides, then re-drives it.

The tool is `python -m provisioning.hosts.operator`. The run script starts it with a cleared environment and its own
file, `env/operator.env`. That file holds the database settings and the login `prov_operator_login`, a member of
`provisioning_operator` alone. The tool has no broker credential and no cluster credential.

| Command | What it does |
| --- | --- |
| `bash scripts/prototype/run-provisioning.sh operator list [--limit N]` | Lists the failed rows, then the pending ones, as tab-separated lines. The first column is the state: `failed` (needs a re-drive), `parked` (pending with its budget spent, so its next take fails it), or `waiting` (pending, due at `next_attempt_at`). |
| `bash scripts/prototype/run-provisioning.sh operator redrive --project P --generation-request G --by NAME [--extra-attempts N]` | Re-drives one event: a `failed` row goes back to `pending` (see below); a `pending` row is made due now. |
| `bash scripts/prototype/run-provisioning.sh operator-as-worker redrive ...` | The same command with the worker's login. The database refuses it with `permission_denied` (SQLSTATE 42501). This is how the rehearsal proves the boundary. |

What a re-drive of a `failed` row writes, in one transaction under the row lock:

- `status` becomes `pending`, and `outcome_code` and `finished_at` are cleared;
- `next_attempt_at` becomes now, so the row is due at once;
- `max_attempts` becomes the larger of the old budget and the attempts used, plus `--extra-attempts` (default 3, at most
  100);
- `last_manual_retry_by` gets `--by`, and `last_manual_retry_at` gets now. This also restarts the row's
  `INBOX_DEADLINE`.

The update guard of migration `00010` admits this change only from a member of `provisioning_operator`, and only with a
larger budget. An `accepted` or `rejected` row is final, and the tool refuses it with `not_redrivable`.

Exit status: 0 when the command succeeded, 1 when it was refused or failed (the reason is on standard error), and 2 for
a usage or configuration error. A configuration error names every refused key, never a value.

**Audit.** Every re-drive attempt is one JSON log line with `"audit": true`, whether it succeeded or was refused. The
line holds `action=inbox_redrive`, `actor_kind=operator`, `actor=<--by>`, `project_id`, `result` (`succeeded`, `refused`
or `failed`), `failure_code` (`not_found`, `not_redrivable`, `permission_denied`, `guard_refused` or `database_error`),
`at` and `correlation_id=redrive-<generation request>`. The run script appends the tool's standard error to
`bin/kind-state/proto/provisioning/logs/latest/operator.log`. A `list` is not audited.

### Rehearsal (as `tests/e2e/test_operator.py` runs it)

1. **Park an event.** The prototype's registry is a file stand-in, and it always answers, so the processor never parks a
   row by itself. The rehearsal writes the row that a registry outage would leave:
   - the project's repository is registered and its commit exists;
   - the event is inserted as `failed`, with `repository_unconfirmed` and its budget spent (10 of 10);
   - the insert is done by the database owner, in the seeding path of decision 0008 section 5.
2. **See it listed.** `operator list` shows the row with state `failed` and outcome `repository_unconfirmed`.
3. **Show the boundary.** `operator-as-worker redrive ...` exits 1 with `permission_denied`. The row stays `failed`, and
   one audit line has `result=refused`.
4. **Re-drive it.** `operator redrive --project P --generation-request G --by <you>` exits 0 and prints, for example,
   `reopened ... status=pending attempts=10 max_attempts=13 ...`. One audit line has `result=succeeded`.
5. **See the release accepted.** Within the processor's interval (`INBOX_POLL_INTERVAL`, 1 s on the stack), the row
   becomes `accepted` with a release. The build starts by itself, as for any accepted release
   (`GET /v1/projects/P/releases/<release>`).

For a real registry outage, wait until the registry answers again before you re-drive: a re-drive during the outage only
uses up the new attempts.

## 2. Hosting retries (QA-03; section 23, lead's rulings)

A `suspend_hosting` or `resume_hosting` command that ends `failed` (for example `route_change_unconfirmed`, which leaves
the route `uncertain`) is retried by the worker:

- **The key.** The retry is a new command for the same intent, with the key `<event key>.retry-<n>`, accepted by the
  hosting intake (`provisioning_hosting_intake`).
- **The budget.** It is at most `HOSTING_RETRY_BUDGET` = 3 retries per intent, a value in code
  (`core/application/hosting/background/retry.py`).
- **When.** The project's latest hosting command must have failed, hosting must not yet be in the state it asked for,
  the route must be `stable`, and no other command may be in flight.
- **What drives it.** The hosting event path: a repeat of the Payment (or "project deleted") event that finds its
  command failed, and a sweep every `RECONCILE_INTERVAL` (30 s by default) over at most `RECONCILE_BATCH` projects.
- **The recheck.** A retry is rechecked against the Payment events that came after the original event. So a suspend
  whose entitlement has since been restored ends `skipped`.

What the operator does:

1. A failed route change leaves the project `uncertain` (`GET /v1/projects/P/deployment`: `routing_status=uncertain`).
   Run the operator rollback with a token holding `ADMIN` (rollback is `ADMIN` only, FR-50), naming the live release
   (or, on a first publish, the release that failed), as decision 0014 section 13 says. It restores the recorded state:
   - a suspended site (`offline`) stays offline;
   - a site whose suspend failed is still recorded `online`, so it serves again until the retry runs.
2. Within `RECONCILE_INTERVAL` the worker logs `hosting retry retried (project_id=... retry_key=<key>.retry-1)` and
   accepts the retry command, which then runs like any suspend or resume.
3. When the budget is spent, the worker logs at error level
   `hosting retry budget_spent: the failed suspend or resume needs an operator`. Find the cause (the route, the cluster)
   and fix it. Then ask Payment to send the event again under a new key; Provisioning has no endpoint that creates a
   hosting command.

Live proof: `tests/e2e/faults/test_hosting_retry.py`. QA's fault, a route stamped with command number 999999, makes the
suspend fail. The operator rollback takes the route back (QA-07), and the sweep's `<key>.retry-1` takes the site
offline. The same file proves the mirror case: a resume refused by the same fault, a rollback that keeps the suspended
site offline, and `<key>.retry-1` bringing the paying site back online.

## 3. Project deletion (answer 4)

Project's "project deleted" event (v1 schema `src/contracts/shared/events/project/project_deleted.v1.schema.json`;
exchange `project.events`, routing key `project_deleted.v1`, queue `provisioning.project_deleted.v1`) becomes a
`suspend_hosting` command with the fixed key `project-deleted`. A repeat of the event is the same command.

- **It is never skipped.** A deletion suspend is not rechecked against Payment's events. Only a site that is already
  offline skips it.
- **No resume.** A later "entitlement restored" for a deleted project ends `skipped` (`entitlement_ended`). The worker's
  entitlement record reads the deletion from the database, so this also holds after a restart.
- **Nothing is deleted:** not the image, the releases, the catalog data or the source (OD-48). OD-52 stays open for
  production.

Simulate the event with `python -m provisioning.hosts.devtools.simulate project-deleted --project P`, with
`WEBBLOCK_SIMULATOR_AMQP_URL` set; the tool prints `event_key=project-deleted`. Live proof:
`tests/e2e/faults/test_hosting_deletion.py`.

## 4. The usage event "publish completed" (answer 2)

When a release is confirmed live, the same transaction writes one row of `provisioning_ops.usage_outbox` (migration
`00016`), keyed by the release, with the event key `publish-completed-<release_id>`. The worker's usage publisher then
handles the row:

- **The poll.** Every `USAGE_PUBLISH_INTERVAL` (5 s by default) it takes at most `USAGE_BATCH_SIZE` due rows.
- **The publish.** It publishes each with a publisher confirm to the exchange `provisioning.events`, routing key
  `publish_completed.v1`. Payment owns its queue `payment.publish_completed.v1`; on the prototype stack the simulator
  declares it. With no queue bound, the event is kept in `provisioning.events.unrouted` (the exchange's alternate
  exchange), not returned.
- **The mark.** It marks the row `sent` only after the confirm.
- **A failure.** A failed or unconfirmed publish records `publish_failed` or `publish_unconfirmed` and backs off with
  the dispatcher's settings.
- **A crash.** A crash between the confirm and the mark publishes the event again after `DISPATCH_VISIBILITY_TIMEOUT`.
  Duplicates are allowed, because Payment deduplicates on the key; a loss is not.

To inspect the outbox, read it as the worker's login (read only):

```sql
SELECT release_id, status, attempts, last_failure_code, sent_at
FROM provisioning_ops.usage_outbox ORDER BY created_at DESC LIMIT 20;
```

Live proof: `tests/e2e/test_usage_event.py`. A test queue bound to the exchange receives the release's event after its
promotion, and the row ends `sent`.

A row that stays `pending` with a growing `attempts` means the broker refuses or cannot be reached; the worker's log
names the error class. Each send that is marked is an audit line with `action=usage_event_published`.

## 5. Diagnostics read of the inbox (section 23 item 4)

Migration `00016` grants `provisioning_api` `SELECT` on these columns of `provisioning_ops.source_published_events`
only:

- `project_id`, `generation_request_id`, `status`, `outcome_code`, `release_id`, `attempts`, `mismatched_repeat_count`,
  `received_at`, `finished_at`.

It does not grant the repository, the commit, the schedule or the manual-retry columns. The API's diagnostics (task
W6-D) show the inbox outcome from these columns.

## 6. Re-driving a broker dead-letter message (task W6-E2)

Each consumed queue dead-letters to its own dead-letter queue. A message ends up there when:

- the consumer rejects it as invalid;
- the broker dead-letters it after `RABBITMQ_DELIVERY_LIMIT` redeliveries (5 by default; RabbitMQ 4 counts a return by a
  closed channel, not a `basic.nack` with requeue);
- the consumer dead-letters it at its fifth counted failure (any failure, a database outage included; contention and the
  per-project cap do not count), because RabbitMQ 4 does not count a requeue;
- the consumer parks it after `PAYMENT_MAX_RETRIES` transient refusals;
- its lifetime runs out.

The dead-letter queues are named `<queue>.dlq` (decision `0022` E6); a stack made before Phase B has the old names and
is recreated with `make proto-down; make proto-up`. A re-drive publishes to Provisioning's own queue through the default
exchange. The consumer's own count (5 counted failures per `message_id`) lives in the process: it resets on a restart,
and with several replicas a message can fail up to 5 times on each before it parks. Every failure counts, a database
outage (unreachable or timed out) included (decision `0023` G15, the owner's rule from Notion 4.3). Only waits do not
count: lock contention, the inbox per-project cap and the hosting retries of `PAYMENT_MAX_RETRIES`. During an outage the
consumer holds each message for the store hold (at most `WORKER_STORE_HOLD_MAX` or `INBOX_STORE_HOLD_MAX`), requeues it,
and parks it at the fifth failure. It does not restart its stream for an outage.

**After a database outage**, check every dead-letter queue and move the parked messages back once the database answers
again:

1. Check that the database answers (for example `make migrate-status`, or the processes' `/healthz`).
2. List each dead-letter queue: `bash scripts/prototype/run-provisioning.sh operator-dlq list` (with no `--queue`, it
   lists each allowed queue).
3. Re-drive each message parked during the outage: `operator-dlq redrive --queue <queue> --message-id <id> --by <you>`.
   A duplicate is harmless: each consumer processes an event once.
4. A command job (`provisioning.command_job.v1.dlq`) does not need a re-drive: the dispatcher publishes the job again
   from its database row after `DISPATCH_REDELIVERY_INTERVAL` (once any claim has expired), until the command's
   deadline. Re-driving it is harmless too.

Nothing moves a parked message back by itself. Payment's events (suspend, resume) and Project's "project deleted" event
have no inbox table, so a parked one is lost unless an operator moves it back. A dead-letter queue keeps a message for
`RABBITMQ_DLQ_MESSAGE_TTL` (7 days on the stack), at most `RABBITMQ_DLQ_MAX_LENGTH` of them.

The tool is the operator CLI's `dlq` command. The run script starts it with a cleared environment and its own file,
`env/operator_dlq.env`. That file holds `RABBITMQ_URL`, `RABBITMQ_DIAL_TIMEOUT`, `DLQ_CALL_TIMEOUT` and `LOG_LEVEL`. It
holds no database or cluster setting. **The prototype broker has one user (`provisioning`), so the tool connects with
the same broker identity as the processes.** A broker user of the operator's own is a production follow-up.

| Command | What it does |
| --- | --- |
| `bash scripts/prototype/run-provisioning.sh operator-dlq list [--queue Q] [--limit N]` | Lists the parked messages of one dead-letter queue, or of each allowed one, as tab-separated lines. It shows at most `N` per queue (default 50, at most 500), from the head of the queue. |
| `bash scripts/prototype/run-provisioning.sh operator-dlq redrive --queue Q --message-id ID --by NAME [--scan-limit N]` | Moves the first parked message with that message ID back to its work queue, through the default exchange (never to `project.events` or `payment.events`, which would reach other services' queues). The printed exchange and routing key are where the message first came from. It searches the first `N` messages (default and maximum 500). |

**The allowed queues** are the platform's own dead-letter queues. Any other name is a usage error (exit 2).

- `provisioning.command_job.v1.dlq`
- `provisioning.source_published.v1.dlq`
- `provisioning.project_deleted.v1.dlq`
- `provisioning.owner_status_changed.v1.dlq`
- `provisioning.hosting_entitlement_ended.v1.dlq`
- `provisioning.hosting_entitlement_restored.v1.dlq`

Payment's queue `payment.publish_completed.v1` is Payment's own and is not on the list. Before Phase B the names were
the prototype's (decision 0022 E6).

**What a listing shows.** The columns are:

- `queue` and `message_id`;
- `death_reason` (`rejected`, `expired`, `maxlen` or `delivery_limit`) and `death_count`;
- `exchange` and `routing_key`, where the message was first published;
- `message_type`, `event_key` and `project_id`, from the envelope. `event_key` holds the event key, the generation
  request or the command ID, whichever the envelope has.

A value that is absent or not of the expected form shows as `-`. The body is never printed. A message with no usable
message ID shows `-` and cannot be re-driven with this tool.

**A listing keeps every message.** It takes the messages with `basic.get` on one channel, then closes the channel
without acknowledging or rejecting any, and the broker returns them.

- **The limit.** On RabbitMQ 4 each such return counts towards the quorum queue's delivery limit: 20 by default, and the
  dead-letter queues set none. A dead-letter queue has no dead-letter exchange of its own, so at the limit the broker
  drops the message. On 4.3.6 the 20th listing of one message lost it; the slot found this.
- **The refresh.** So a message the broker reports as taken 10 times or more (`x-delivery-count` or `x-acquired-count`)
  is refreshed:
  1. A copy is published to the same dead-letter queue through the default exchange, with a confirm. It keeps the body,
     the properties and `x-death`, without the two counters.
  2. Only then is the original acknowledged.
- **The copy** starts a new count at the tail of the queue. A listing or a search stops when it reaches its own copy. A
  crash between the two steps leaves a duplicate parked, never a loss.
- **The proof.** The messaging suite lists one message 45 times. It is still parked, with its death record, and it can
  still be re-driven.

**What a re-drive does:**

1. It takes the queue's messages until it finds the message ID.
2. It reads the `x-death` record of the queue's own work queue. The exchange and routing key there must belong to that
   queue's topology. Otherwise it refuses with `no_origin` and publishes nothing.
3. It publishes a copy to that exchange and routing key, persistent and mandatory, with a publisher confirm. The copy
   keeps the body, the message ID, the type, the content type, the correlation ID, the timestamp and the other headers.
   It leaves out the broker's death records (`x-death`, `x-first-death-*`, `x-last-death-*`, `x-delivery-count`).
4. Only after the confirm does it acknowledge the parked message. A round trip on the same channel then proves that the
   broker processed the acknowledgment.
5. The other messages it took go back to the queue.

If the process dies between the confirm and the acknowledgment, the message is delivered and also still parked. Run
`list` before re-driving it again: a second re-drive delivers a duplicate. Every consumer tolerates a duplicate, because
the event key, the generation request and the command ID are idempotency keys. A message is never lost.

Exit status: 0 when the re-drive succeeded, 1 when it was refused or failed (the reason is on standard error), and 2 for
a usage or configuration error.

| Failure code | Result | Meaning |
| --- | --- | --- |
| `not_found` | refused | No message with that ID among those searched. |
| `no_origin` | refused | The message has no death record of the work queue, or its record names another exchange or routing key. Nothing was published. |
| `publish_returned` | failed | The copy matched no queue, because the work queue is not bound. The message stays parked. |
| `publish_nacked`, `publish_unconfirmed` | failed | The broker refused the copy, or no confirm came within `DLQ_CALL_TIMEOUT`. The message stays parked. |
| `removal_unconfirmed` | failed | The copy was delivered, but the parked message may still be there. List before re-driving again. |
| `broker_unreachable` | failed | The broker could not be reached. |

**Audit.** Every re-drive attempt is one JSON log line with `"audit": true`, whether it succeeded, was refused or
failed. A listing is not audited. The line holds:

- `action=dlq_redrive`, `actor_kind=operator` and `actor=<--by>`;
- `project_id`: the envelope's project, or the nil UUID when the message was not found or names none;
- `command_id` and `release_id`, for a command job;
- `result` and `failure_code`;
- `correlation_id=dlq-redrive-<message id>`.

The run script appends the tool's standard error to `logs/latest/operator.log`, which the stack ships to Loki as
`process="operator"`:

```sh
bash deploy/kind/proto-loki-query.sh '{job="webblock-provisioning", process="operator"} | json | audit="true" | action="dlq_redrive"'
```

**Fix the cause before a re-drive.**

- A message the consumer rejected as invalid (`rejected`, with no envelope fields shown) is rejected again unless the
  consumer's code changed.
- A Payment event parked by `PAYMENT_MAX_RETRIES` or `delivery_limit` was refused again and again while something
  blocked it: a command in flight, or a route that is not `stable`. Check `GET /v1/projects/P/deployment` first. If the
  route is `uncertain`, run the operator rollback of section 2.

### Rehearsal (as `tests/e2e/test_dlq_redrive.py` runs it)

1. **A live site.** Publish and promote v1 of the sample site.
2. **Park a blocked event.** Stop the worker (`run-provisioning.sh stop-process worker`), the queue's only consumer.
   Publish a well-formed "hosting entitlement ended" v1 event with a message lifetime of one second. The broker
   dead-letters it with reason `expired` and a real `x-death` record.
3. **See it listed.** `operator-dlq list --queue provisioning.hosting_entitlement_ended.v1.dlq` shows the message with
   `expired`, the exchange, the routing key, the event key and the project.
4. **Fix the cause.** Start the worker again (`start-process worker`). The parked event did nothing: there is no hosting
   command, and the site still serves.
5. **Re-drive it.** `operator-dlq redrive --queue ... --message-id <id> --by <you>` exits 0 and prints
   `redriven queue=... message_id=... exchange=payment.events routing_key=hosting_entitlement_ended.v1 project_id=...`.
   The message is no longer listed.
6. **See the suspend.** The `suspend_hosting` command with the event's key succeeds, and the site shows the offline
   page. One audit line with `action=dlq_redrive` and `result=succeeded` is in `operator.log` and in Loki.

The messaging suite (`tests/messaging/test_dlq.py`) proves the rest on a real RabbitMQ:

- a rejected event is listed, re-driven and received exactly once;
- a queue outside the allow-list is refused;
- a copy the broker returns leaves the message parked;
- a crash between the confirm and the removal gives a duplicate, never a loss.

## 7. The sub-domain backfill (decision 0021 D8, migrations 00020 and 00021)

Migration `00020` adds `project_hosting.subdomain`, the site's DNS label. A row made after `00020` gets it from the
repository lookup when the first release is accepted. A row from before `00020` has none. Migration `00021` makes the
column `NOT NULL`, and it refuses to run while any row has none:

    1 hosting row(s) have no subdomain; run the operator command "subdomain set" for each (docs/runbooks/operator.md)

It stops with SQLSTATE `23502` and changes nothing. Then fill every missing row, as the operator login
(`provisioning_operator`), with the value Project holds for that project:

| Command | What it does |
| --- | --- |
| `bash scripts/prototype/run-provisioning.sh operator subdomain missing [--limit N]` | Prints `project_id`, then one project per line whose row has no sub-domain, by project ID (default 50, at most 1000). |
| `bash scripts/prototype/run-provisioning.sh operator subdomain set --project P --subdomain S --by NAME` | Sets that row's sub-domain once. It prints `subdomain_set project_id=P`. |

A `set` is refused (exit 1) with:

- `invalid_subdomain` when `S` is not a DNS label (1 to 63 lower-case letters, digits or `-`, no `-` at either end). The
  message never repeats the value. Write a value that starts with `-` as `--subdomain=VALUE`;
- `already_set_or_missing` when the project has no hosting row or its sub-domain is already set. A sub-domain is set
  once and never changes; the database guard refuses any other change;
- `permission_denied` (SQLSTATE 42501) for another login, and for every login after `00021`. `subdomain missing` is
  refused the same way after `00021`; the message says the backfill is over.

**Audit.** Every `set` attempt is one JSON log line with `"audit": true`: `action=subdomain_backfill`,
`actor_kind=operator`, `actor=<--by>`, `project_id`, `result` (`succeeded`, `refused` or `failed`), `failure_code`
(`invalid_subdomain`, `already_set_or_missing`, `permission_denied`, `guard_refused`, `database_error` or
`operator_error`), `at` and `correlation_id=subdomain-<project>`. A `missing` is not audited.

When `subdomain missing` prints no project, run `make migrate-up` again: `00021` applies. It takes back every grant
`00020` gave the operator for the backfill (the column update and read on `project_hosting`, `EXECUTE` on
`offline_reasons_distinct`, `USAGE` on schema `provisioning`), so the operator holds only the inbox manual retry again,
and both `subdomain` commands are refused. The order for a database is in `.claude/docs/runbooks/phase-1-database.md`.
