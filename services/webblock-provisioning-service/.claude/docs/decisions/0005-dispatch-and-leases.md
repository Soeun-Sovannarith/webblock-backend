# 0005: Durable dispatch to RabbitMQ and worker leases

**Amended by [`0027`](0027-go-live-tries.md):** in section 4 an executor may return
`RetryLater` with its own wait before the next claim, instead of the jittered backoff.

**See also decision [`0011`](0011-phase-4-go-tracks.md)** section 3: a deploy failed by the consumer for an exhausted
deadline or attempt budget first runs the executor's `Abandon` hook, which deletes the candidate XR it wrote.


**See also decision [`0008`](0008-phase-3-groundwork.md)** (Phase 3): the inbox for Project's "source published" event,
its SQL-driven retries, and the lock order with the inbox first.

Status: **Current.** Resolves OD-10 and, for the database half, OD-11. Revision 16 lists the worker claim and lease
as "not yet designed"; this record is that design for the database half (decision `0007` section 6). The worker's job
store and fenced writes (`src/provisioning/infrastructure/postgres`), the RabbitMQ adapter and migration `00004`
implement it. Changing a rule means changing this record first.

Scope: `internal/jobs`, `internal/transport/messaging`, `internal/adapters/rabbitmq`,
`internal/adapters/postgres/jobstore`, `cmd/worker`, migration `00004`.

## 1. The mechanism

 PostgreSQL is the authority and RabbitMQ only notifies. Every accepted command, of any of the five kinds, gets one
 **work row** in a separate operational schema, inserted in the acceptance transaction (decision `0004` section 4). A
 dispatcher publishes a notification with publisher confirms and keeps republishing on a persisted schedule until the
 command is finished. The same row carries the worker lease. Watermill is not used. The generic outbox and the inbox
 that revision 16 calls for are designed in the phases that first need them: the inbox for Project's event in Phase 3,
 the outbox for the usage event in Phase 5 (OD-42).

Why this covers each failure window:

| Window | Outcome |
| --- | --- |
| Commit without publish | The row is due at commit; any dispatcher replica picks it up |
| Publish without confirm; confirm without recording it | The row's next-dispatch time was already pushed forward when it was taken; it is republished after that time. A duplicate is harmless |
| Side effect before acknowledgment; lost acknowledgment | Redelivery finds a finished command or a live lease and is acknowledged without executing |
| Broker loses or dead-letters a confirmed message | Republished while the command is unfinished |

This is at-least-once notification with idempotent consumption. It is not exactly-once and is not claimed to be.

## 2. Storage (migration `00004`)

Schema `provisioning_ops`, table `command_work`, one row per command. It is operational storage, not a fifth business
table, and the first migration after the baseline.

| Column | Type | Meaning |
| --- | --- | --- |
| `command_id` | `uuid` PK, FK to `provisioning.commands(command_id)` `ON DELETE RESTRICT` | The command |
| `correlation_id` | `varchar(128)` | Carried into the envelope |
| `deadline_at` | `timestamptz` | Latest time the command may still be worked |
| `next_dispatch_at` | `timestamptz` | When the dispatcher may publish again |
| `publish_attempts` | `integer` | Publish attempts so far |
| `last_confirmed_at` | `timestamptz NULL` | Last broker confirm |
| `claim_count` | `integer` | Execution attempts so far |
| `lease_token` | `bigint` | Fencing token, incremented on every claim |
| `claimed_by` | `varchar(100) NULL` | Worker holding the lease |
| `lease_expires_at` | `timestamptz NULL` | Lease expiry; null together with the owner |
| `retry_not_before` | `timestamptz NULL` | Earliest time the command may be claimed again after a transient failure |
| `last_error_code` | `varchar(100) NULL` | Last failure, in the `domain.ValidateCode` format |
| `finished_at` | `timestamptz NULL` | Set in the transaction that gives the command its terminal status |
| `created_at`, `updated_at` | `timestamptz` | |

- Indexes: the dispatcher's scan is served by a partial index on `next_dispatch_at` where `finished_at` is null, so its
  cost follows the number of unfinished commands and not the size of the table; "unfinished" lives on `commands`, which
  a partial index on this table cannot see, hence the `finished_at` column. The heartbeat writes only columns that no
  index covers, so it stays a heap-only update, and the claim moves `next_dispatch_at` to the lease expiry so that a
  command with a live lease is not republished. Storage settings for a table this hot (fill factor, autovacuum) and
  whether finished rows are ever removed are measured and decided before production (OD-15).
  *(Migration `00025` drops this index, named `command_dispatch_due` since `00011`: Notion shows no such index,
  constitution III.3. The dispatcher's scan is a sequential scan.)*
- Counters are not negative; `claimed_by` and `lease_expires_at` are set or null together. No column defaults.
- Migration `00005` guards the row against a faulty or compromised worker: `lease_token`, `claim_count` and
  `publish_attempts` never decrease, and `finished_at` once set never changes. Not enforced, and stated as such: the
  other dispatch columns (`next_dispatch_at`, `retry_not_before`, `claimed_by`, `lease_expires_at`, `last_error_code`,
  `last_confirmed_at`) may be rewritten by the worker role; the conditional updates of the job store are their guard.
- The row holds no copy of the generation: `commands.fencing_generation` is the single source, and
  `commands_fencing_key` makes a project and a generation name exactly one command. The lease columns carry the names
  both revision 16 documents use.
- A guard on insert requires a fresh row: token and counters zero, no lease, no confirm, and a command that is
  `accepted`. The API role can therefore not hand a worker a row with a lease already set.
- Privileges: `provisioning_api` may `INSERT`; `provisioning_worker` may `SELECT` and `UPDATE`. Nobody may `DELETE`: the
  table has the same cardinality as `commands`.
- Lease and dispatch arithmetic uses the database clock (`clock_timestamp()`), so skew between workers cannot stretch a
  lease. This is a deliberate exception to "the application supplies timestamps".
- An unfinished command with no work row cannot be produced by the acceptance transaction. The dispatcher counts such
  rows and reports them; it does not repair them silently.

## 3. Dispatcher

Runs in `cmd/worker`. The API process holds no broker credentials; the delay between acceptance and first publish is at
most the poll interval.

1. In one short transaction, take up to a batch of due rows of unfinished commands (`FOR UPDATE SKIP LOCKED`), push
   `next_dispatch_at` forward by the visibility timeout, increment `publish_attempts`, commit.
2. Publish each: persistent, mandatory, on a confirm channel. Success means a broker acknowledgment and no return. A
   negative acknowledgment, a return, or a confirm timeout is a failure. The client library drops a return or confirm
   notification when its listener is not reading, which would make an unroutable publish look confirmed. So each publish
   waits on its own deferred confirmation, and returns are read by a listener that is always draining and matched to the
   publish by message ID. The library's optional automatic recovery stays off; the adapter owns reconnecting.
3. Record the confirm, or the failure with a bounded jittered backoff. Both updates are conditional on
   `publish_attempts`, and the confirm also on no lease being held, so a slow dispatcher cannot overwrite newer state.
4. A command whose deadline has passed and that was never claimed is moved to `failed` by the dispatcher, with a failure
   code, in one transaction that follows the lock order of decision `0004` section 7: the project row with the fence,
   then the work row, where it takes the lease token, then the command with the status condition `accepted`. It never
   locks the work row before the project row.

## 4. Consumer, claim and fence

A delivery is validated (section 6), then claimed in one short transaction: lock the work row, compare the message with
the command and the work row, and decide. Acknowledgment always follows the committed decision.

| Situation | Database effect | Broker action |
| --- | --- | --- |
| Too large, wrong content type, not JSON, fails the schema, unknown message type | none | reject without requeue (dead-letter); log reason code, size and body hash, never the body |
| No work row, or project, kind or release differ from the command (a hosting command has no release, and a message that names one differs) | none | dead-letter |
| Generation differs from `commands.fencing_generation`; command already finished; lease held by a live owner | none | acknowledge |
| An unfinished command whose `fencing_generation` differs from the project's `operation_generation` | none; this cannot be produced by acceptance and is reported as corruption | dead-letter |
| Claimed | `lease_token` and `claim_count` incremented, owner and expiry set, command `accepted → running` if it was `accepted` | continue to execution |
| Attempt budget or deadline exhausted | command `running → failed` with a failure code (fenced), unless `route_state` is `changing` | acknowledge after commit |
| Executor returns success | executor's own fenced writes, the last of which gives the command its terminal status and sets `finished_at`; that write ends the lease, and no separate release follows (validation finding V2-05) | acknowledge after commit |
| Executor returns a transient error | lease released with jittered backoff | acknowledge after commit; the dispatcher redelivers |
| Executor returns a permanent error | command `running → failed` with a failure code (fenced) | acknowledge after commit |
| Database unreachable | none | hold for a bounded backoff, then requeue; repeated failure closes the consumer, which reconnects with backoff |

- **Fence.** Every write that originates from a worker for a command makes the three checks of decision `0004` section
  7, in its lock order: the project fence (`operation_generation` equals the command's `fencing_generation`, else
  `ErrStaleFence`), the lease token on the work row (else `ErrLeaseLost`), and `commands.status = 'running'` with the
  expected row status. Any failure writes nothing. Revision 16 names only the first check; it passes for a worker of a
  finished command until the next command is accepted, and for a second worker on the same command, which is why the
  other two stay. Token equality rejects writes; expiry only permits another worker to take over. A worker whose lease
  expired but was not taken over still owns the command.
- **Claim.** The claim locks the work row and then the command, and takes no project lock: it changes no release or
  project state. It succeeds only when no live lease exists and `retry_not_before` has passed, and only a successful
  claim increments `claim_count`. A duplicate or forged message that arrives during a backoff is acknowledged without a
  claim, so it cannot burn the attempt budget. The heartbeat and the dispatcher's take lock the work row only.
- **No budget failure while the route is changing.** A command is failed on an exhausted budget or deadline only while
  `route_state` is not `changing`. Once a route change has started, only a verified outcome or the recovery sweeper of
  Phase 5 ends the command, and the sweeper claims the work row before it resolves anything.
- **Heartbeat.** A worker extends its lease at a configured interval no longer than a third of the lease time. Losing
  the lease cancels the execution's context.
- (read from the broker's documentation, not yet run) The queue's delivery limit is set explicitly, because its default
  differs between broker series, and on the 4.x series it counts only rejections and lost channels, not requeues or
  consumer timeouts: only the SQL attempt budget bounds those. A consumer timeout cancels the consumer instead of
  closing the channel on that series, so the adapter subscribes again when its consumer is cancelled. The Phase 2 fault
  tests must show both on the pinned image.
- **Retries are driven from SQL**, not from broker requeues. The broker delivery limit is a crash-loop safety net; a
  dead-lettered legitimate notification is regenerated by the dispatcher. Dead-letter queue contents are evidence and
  are never re-injected.
- **What fencing proves.** Phase 2 proves that a stale worker's SQL writes are rejected, for an older command, for a
  finished command, and for a lost lease. It does not stop a stale Kubernetes write; Phase 5 adds a fence re-check and
  lease extension before each XR write, the release ID and fencing generation stamped on the XR, resource-version
  preconditions, and inspection of the composed route (ARCHITECTURE.md section 7).

## 5. What a command job does in Phase 2

 Phase 2 has no build, cluster or route work, so nothing can honestly execute a command. The worker hands a claimed
 command to an `Executor` port. The only implementation outside test files returns a permanent `executor_unavailable`
 error, and the command ends `failed` with that code in `commands.failure_code` and in `last_error_code`. The release is
 not touched: a deploy that fails this way leaves it `built` (decision `0001` section 2). No code in `internal/jobs`
 writes `succeeded`. Tests use a recording executor in `_test.go` files to prove delivery, claim and fencing semantics.

The API keeps deploy, promote and rollback switched off in production (decision `0006` section 6), so in a production
build no command reaches this path; the refusing executor is the second line. The worker has its own set of enabled
kinds, passed by `cmd/worker` in code like the API's. A claimed command of a kind that is not enabled ends `failed` with
`operation_unavailable` and no executor is called, whatever put the row in the database.

 Failure codes the worker writes in Phase 2: `executor_unavailable`, `operation_unavailable`, `deadline_exceeded`,
 `attempts_exhausted`, `executor_failed`, `attempt_timeout`; `transient_failure` is a work-row `last_error_code` only
 and never a command's final code. A heartbeat that cannot extend the lease for a whole lease time cancels the execution
 as if the lease were lost. A requeue after a database outage uses a negative acknowledgement, so it does not count
 towards the broker's delivery limit; the SQL attempt budget is the bound.

Failing a command on deadline or exhausted attempts is safe only while its executor has changed nothing outside SQL. The
rule in section 4 covers route changes. For a deploy that has already written a release XR, decision
[`0009`](0009-phase-4-cluster-stack.md) section 7: it may end `failed` after a fenced, best-effort delete of its
candidate XR; leftovers go to the Phase 5 sweeper.

## 6. Messages and topology

- Message type `provisioning.command_job`, schema `api/events/command-job.v1.schema.json` (OD-26, still a draft name).
  The body carries identifiers only: project, command, kind, `fencing_generation` (1 to 2^53−1, replacing
  `operation_generation`), and `release_id`, which is present exactly when the kind is not a hosting kind.
  `build_input_hash` is removed from the envelope and the job. All five kinds are in the schema. In the shared envelope
  `fencing_generation`, `command_id` and `release_id` are optional, because a later message may belong to no command (a
  build job, OD-51); the command job requires the first two.
- Incoming and outgoing messages are validated at runtime against the embedded schemas with format assertion on, so
  `occurred_at` is checked (OD-27), then parsed through the domain (`ParseID` refuses the nil UUID, `ParseCommandKind`,
  the fencing generation range, the release against the kind). The AMQP `type` property must equal the body's
  `message_type`. A fresh message ID is used for every publish; the deduplication key is the command and its durable
  state, never the message ID.

| Item | Value (names are drafts for DevOps review) |
| --- | --- |
| Exchange | `provisioning.jobs`, direct, durable |
| Queue | `provisioning.command_job.v1`, quorum, dead-letter exchange `provisioning.dlx`, delivery limit, maximum length with `reject-publish`, consumer timeout |
| Routing key | `command_job.v1` |
| Dead-letter queue | `provisioning.command_job.v1.dead`, quorum, bounded length and message lifetime, no consumer |
| Consume | manual acknowledgment, prefetch equal to the worker concurrency, bounded handler pool of the same size |
| Connection | reconnect with bounded jittered backoff; topology declared on connect, or only checked when declaration is switched off |

## 7. Configuration

Every key is required and range-checked; there are no defaults in code, and tests pass explicit values. Both processes
also require the database keys `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASS` (secret), `PG_DATABASE`, `PG_SSLMODE`,
`DB_MAX_CONNS`, `DB_CONNECT_TIMEOUT`, `DB_TX_TIMEOUT`, `DB_STATEMENT_TIMEOUT`, `DB_LOCK_TIMEOUT` (decision `0004`
section 10), with `DB_LOCK_TIMEOUT` ≤ `DB_STATEMENT_TIMEOUT` ≤ `DB_TX_TIMEOUT`. `COMMAND_DEADLINE` is an API key only:
it becomes the work row's `deadline_at` at acceptance. The exact lists are returned by `config.APIKeys()` and
`config.WorkerKeys()`; `docs/runbooks/phase-2-binaries.md` explains each. Production values are the owner's to choose
(OD-15) and block deployment, not implementation.

`DISPATCH_POLL_INTERVAL`, `DISPATCH_BATCH_SIZE`, `DISPATCH_PUBLISH_CONFIRM_TIMEOUT`, `DISPATCH_VISIBILITY_TIMEOUT`,
`DISPATCH_REDELIVERY_INTERVAL`, `DISPATCH_BACKOFF_BASE`, `DISPATCH_BACKOFF_MAX`, `WORKER_CONCURRENCY`,
`WORKER_LEASE_TTL`, `WORKER_HEARTBEAT_INTERVAL`, `WORKER_ATTEMPT_TIMEOUT`, `WORKER_MAX_ATTEMPTS`,
`WORKER_RETRY_BACKOFF_BASE`, `WORKER_RETRY_BACKOFF_MAX`, `COMMAND_DEADLINE`, `RABBITMQ_URL` (secret, never logged),
`RABBITMQ_HEARTBEAT`, `RABBITMQ_DIAL_TIMEOUT`, `RABBITMQ_RECONNECT_BACKOFF_BASE`, `RABBITMQ_RECONNECT_BACKOFF_MAX`,
`RABBITMQ_QUEUE_MAX_LENGTH`, `RABBITMQ_DELIVERY_LIMIT`, `RABBITMQ_CONSUMER_TIMEOUT`, `RABBITMQ_DLQ_MAX_LENGTH`,
`RABBITMQ_DLQ_MESSAGE_TTL`, `MESSAGING_MAX_BODY_BYTES`, `RABBITMQ_DECLARE_TOPOLOGY`.

 Three more required keys: `WORKER_STORE_HOLD` (how long a delivery is held while the database is unreachable before it
 is requeued), `WORKER_STORE_FAILURE_LIMIT` (consecutive store failures before the consumer is closed and reconnects),
 and `RABBITMQ_MAX_IN_FLIGHT_PUBLISHES` (outstanding confirms per publisher channel). The AMQP connection name is fixed
 in code per process, not configured.

Cross-checks enforced at start: `RABBITMQ_MAX_IN_FLIGHT_PUBLISHES` is at least `DISPATCH_BATCH_SIZE`; the visibility
timeout exceeds the confirm timeout; the heartbeat interval is at most a third of the lease time; the attempt timeout is
below the broker consumer timeout. Backoff is `min(max, base · 2^(attempt−1))` with jitter that is never zero.

## 8. Tests and their limits

- Real RabbitMQ, always. A local broker container is available to the tests: when `.env` defines `RABBITMQ_USER` and
  `RABBITMQ_PASS` (`RABBITMQ_HOST` and `RABBITMQ_PORT` default to `localhost:5672`), tests that only need a working
  broker connect to it, each in its own virtual host or with uniquely named queues that it removes afterwards. Without
  those keys they start a broker through Testcontainers. Tests that inject faults (stopping the broker application,
  severing connections at the broker) always start their own broker through Testcontainers, so they never stop the
  shared one. The image tag is pinned in the harness and the runbook. An unreachable broker or a start failure fails the
  test; nothing is skipped.
- A command run inside the broker container (`rabbitmqctl`, `rabbitmq-diagnostics`) must run as the `rabbitmq` user. Run
  as root before the server has started, it creates the node's cookie file owned by root and the server then fails to
  start. Found with image 4.3.6.
- Faults are injected with a connection wrapper (blocked reads or writes, severed connection), with port decorators that
  stop responding to model a process crash, and with broker commands that stop and restart the broker application. Every
  wait polls a condition with a deadline; no sleep is used as an assertion.
- Not proven in Phase 2: an executor with real side effects, a real `SIGKILL` of the binary, network partitions,
  PostgreSQL failover, TLS and production broker permissions, replication or leader failover (single-node broker),
  throughput, and tooling to replay dead letters.

## Not decided here

The production RabbitMQ series and its delivery-limit semantics (OD-41); broker users, virtual host and permissions,
which decide who can publish a job message (OD-54); production values of section 7 (OD-15); exchange and queue names
(OD-26); messages from other services (OD-02, OD-04) and their inbox, and the usage outbox (OD-42); build dispatch,
which has no command row (OD-51); recovery under a lease while the route is `changing` (Phase 5); retry ownership for
HTTP calls (OD-40); for AMQP and PostgreSQL the application is the only retry owner.
