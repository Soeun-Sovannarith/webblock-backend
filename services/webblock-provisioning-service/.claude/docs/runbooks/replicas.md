# Replicas on the prototype stack (task H5b, phase P-H)

Two API, two worker and two build worker processes against one Provisioning database and one broker,
on the local Kind stack. It shows that per-project serialization, the claim and the fence hold across
processes, which share nothing but PostgreSQL (CLAUDE.md section 3: durable ownership, never
in-process mutexes). A local Kind pass is not proof of production behaviour (scheduling, rolling
updates, network partitions, a managed broker).

## Commands

```sh
make proto-up                 # the stack, as usual
make proto-run-replicas       # PROV_REPLICAS=2 run-provisioning.sh start
make proto-e2e-replicas       # tests/e2e/replicas (clears finished sites first)
make proto-e2e                # the ordinary suite; works against either run (see below)
make proto-stop proto-down    # proto-stop stops every replica
```

`make proto-run` (no variable) still runs one of each, so every existing suite is unchanged. `make
proto-e2e` ignores `tests/e2e/replicas`; `make proto-e2e-replicas` refuses a stack that does not
run the second replicas (`require_replicas`), and fails rather than skips.

## What runs

| Process | Replica 1 | Replica 2 |
| --- | --- | --- |
| API (REST and MCP) | `127.0.0.1:18080`, ops `18081` | `127.0.0.1:18090`, ops `18091` |
| Worker | ops `18082`, `WORKER_ID=prototype-worker-1` | ops `18092`, `WORKER_ID=prototype-worker-2` |
| Build worker | ops `18083` | ops `18093` |
| Bootstrap | ops `18084` | `bootstrap-2`, ops `18094`: a standby, started only by `start-process bootstrap-2` (see below) |

Each replica `<process>-2` has its own env file (`env/<process>-2.env`: the first replica's file
with only `HTTP_LISTEN_ADDR`, `OPS_LISTEN_ADDR`, `MCP_ALLOWED_HOSTS` and `WORKER_ID` replaced, by
`derive_env`, which refuses a key the first file lacks), pid file (`pids/<process>-2.pid`), log file
(`logs/latest/<process>-2.log`, shipped to Loki with `wb_process=<process>-2`) and ports (override
with `PROV_API2_PORT`, `PROV_OPS2_PORT`, `PROV_WORKER2_OPS_PORT`, `PROV_BUILD_WORKER2_OPS_PORT`).
`start` records the count in `provisioning/replicas`; `restart`, `status` and `reset-sites.sh` follow
it, and `stop-processes` (used by `stop`, `restart` and `reset-sites.sh`) stops every name whatever
was recorded. `start` with the default count refuses while a second replica is still running.

There is no load balancer. The second API listens beside the first, and the tests call both
(`Stack.apis`; `post(..., api=)`, `mcp(api=)`). Both workers consume the one command queue and both
run a dispatcher, a reconciler and the Payment consumers; both build workers consume Project's event,
scan the inbox, poll Jenkins and LISTEN for build hints.

## The bootstrap is a singleton, enforced (task H5c)

Two bootstraps working at once are not safe: `CatalogAccessSetup.run` does `role_exists` → a new
credential → write the Secret `catalog-runtime` → `ALTER ROLE ... PASSWORD`, with no lock per
project. On a fresh tenant two of them can interleave as

1. bootstrap 1 writes Secret A,
2. bootstrap 2 writes Secret B,
3. bootstrap 2 sets the role's password to B,
4. bootstrap 1 sets the role's password to A,

which leaves Secret B beside a role whose password is A: the site's pods cannot log in to the
catalog. (The "created from nothing" mark of decision 0014 section 24 is no longer process memory:
since task F2B it is the namespace annotation `websites.webblock.internal/catalog-secret`, see
`prototype-stack.md`.)

So a bootstrap runs its rounds only while it holds the **singleton lock**
(`hosts/bootstrap/singleton.py`, `infrastructure/postgres/session_lock.py`):

- **The lock.** A PostgreSQL session-level advisory lock on the fixed key `BOOTSTRAP_LOCK_KEY` =
  `6287675231898391634` (`0x57424F4F54535452`, the ASCII bytes of `WBOOTSTR`; in `pg_locks` an
  `advisory` lock with `classid = 1463963471`, `objid = 1414747218`, `objsubid = 1`), in the
  Provisioning database. It is taken with `pg_try_advisory_lock` on a connection of its own (an
  engine of its own as the bootstrap's login, so the rounds' pool never loses a connection to
  it), before the first round, and kept for the life of the process. No grant is needed.
- **Standby.** While another session holds the lock, the process runs no round, logs `bootstrap
  standby: another bootstrap holds the lock` at once and every 30 s, reports
  `provisioning_bootstrap_standby 1` on its `/metrics`, and tries the lock again every second.
  Once it holds the lock it logs `bootstrap holds the singleton lock; its rounds start` and reports
  `provisioning_bootstrap_standby 0`. Its `/metrics` and `/healthz` serve throughout.
- **Takeover.** A holder's session ends when it exits, is killed or loses its connection; the
  server frees the lock at once, and a standby takes it at its next try (one second; the replica
  test measures it, bound 15 s).
- **A lost lock.** The holder reads its connection between checks, so a session the server ends
  (terminated, server restart) is noticed at once, and every 5 s it confirms in `pg_locks` that it
  still holds the lock. A failed check, or a lock no longer held, cancels its rounds at once (even
  inside a round) and the process exits 1 (`bootstrap singleton lock lost; its rounds stop and the
  process exits`), so its supervisor restarts it, as a standby if another bootstrap took the lock
  meanwhile. Rounds never run without the lock.
- **A stall is not a loss (task HF-A; H-V finding F-1).** A check that has no answer within the
  statement timeout (`DB_STATEMENT_TIMEOUT`, 10 s on the prototype) while its connection has not
  failed (the database paused, failing over or slow) leaves the lock **uncertain**. The rounds are
  cancelled at once, as on a loss, and none starts while it is uncertain; the process logs
  `bootstrap singleton lock uncertain: a check passed its time bound; rounds paused until a check
  on the same session confirms the lock` and keeps its session. The check is awaited, not
  cancelled: psycopg answers a cancelled statement with a cancel request and, when the query has
  not ended 5 s later, closes the connection, which would end the session and free the lock. Once
  the check answers held, one more check on the same session must answer held; then the process
  logs `bootstrap singleton lock confirmed on the same session; its rounds resume` and the rounds
  start again. Nobody else can have held the lock meanwhile: a session lock lasts as long as its
  session, and the same session answered. It is still a loss, and the process exits 1, when the
  connection fails, the session ends, a check answers "not held", or no answer comes within
  `LOCK_STALL_BOUND` = 60 s of the stall (or the statement timeout, if longer). 60 s rides out the
  e2e suite's 20 s pause and a CloudNativePG switchover, and gives up within a minute on a
  connection that neither answers nor fails (a silent network drop), so its supervisor restarts it.
  No round runs while the lock is uncertain, so the bound concerns liveness only.
- **The prototype's supervisor.** `run-provisioning.sh` runs each bootstrap (`bootstrap`,
  `bootstrap-2`) under a supervisor: when the process exits non-zero, it is started again after a
  back-off of 1 s that doubles up to 30 s while it keeps exiting within a minute
  (`PROV_RESTART_BACKOFF_MS`). Each restart is a JSON line in the same log (`"supervisor":
  "run-provisioning.sh"`, with `exit_status`, `restart` and `backoff_ms`); the pid file names the
  supervisor, which runs in a session of its own. `stop-process` passes SIGTERM to the bootstrap
  once and the supervisor exits after it, never restarting it; `kill-process` sends SIGKILL to the
  whole session, so the supervisor and the bootstrap die together. The other processes have no
  supervisor: their work is durable in SQL and their absence is seen at once, and the suites stop
  them on purpose. In production the supervisor is Kubernetes (P-P).
- **Rolling updates.** An old pod holding the lock keeps the new pod in standby until the old one
  exits; the new one then takes over within a second. That is safe with either strategy: with
  `RollingUpdate` the new pod stands by beside the old one and runs no round until the old one is
  stopped (whether the rollout then proceeds depends on the readiness probe the P-P manifest gives
  the bootstrap: a standby answers `/healthz`); with `Recreate` the old pod is stopped first and
  the new one takes the lock at its first try. Production keeps `replicas: 1` and `Recreate` (no
  standby is needed); a second replica or a rolling update is now safe rather than a hazard.
- **Connection pooling.** A session lock lives in one server session, so the bootstrap must reach
  PostgreSQL directly or through a session-mode pool. Through PgBouncer in **transaction** mode
  it does not work (another client could get the session, or the lock's session could change
  under it): the same caveat as the build worker's `LISTEN` for build hints. A P-P item.

On the prototype stack, `make proto-run-replicas` also writes `env/bootstrap-2.env` (the first
bootstrap's file with its own `OPS_LISTEN_ADDR`, port `18094`, `PROV_BOOTSTRAP2_OPS_PORT`), and
`run-provisioning.sh start-process bootstrap-2` starts a second bootstrap, which stands by.
`start` never starts it; `stop-processes` (and so `stop`, `restart` and `reset-sites.sh`) stops it;
`start` with the default count refuses while it runs. The replica suite's
`test_bootstrap_standby.py` starts it, and stops it again at the end.

## Lease settings

The replica run uses the prototype's own settings: `WORKER_LEASE_TTL=15s`,
`WORKER_HEARTBEAT_INTERVAL=3s`, `DISPATCH_VISIBILITY_TIMEOUT=10s`, `DISPATCH_REDELIVERY_INTERVAL=10s`,
`RABBITMQ_HEARTBEAT=10s`. A frozen worker's command is taken over once its claim has expired and a
delivery reaches the other worker: a dispatcher republish, or the broker requeueing the frozen
worker's unacknowledged delivery after its connection missed its heartbeats. In the live run that
took about the times recorded in the H5b report.

A worker paused inside one of its short transactions keeps that transaction's row locks, and its own
transaction timeout cannot fire while it is paused. Every engine therefore sets the server's
`idle_in_transaction_session_timeout` to `DB_TX_TIMEOUT` (task H5b, port parity with the Go stores),
so the server ends such a session and the other worker's claim gets the lock instead of meeting
`Contention` until the paused worker resumes. A session idle outside a transaction (the pool's, the
build hint listener's autocommit `LISTEN`) is not affected
(`tests/integration/test_engine_idle_timeout.py`).

## The replica suite

| File | What it proves |
| --- | --- |
| `test_serialization_across_apis.py` | eight deploys with eight keys through both APIs at once: one 202, seven 409 `operation_in_progress`, one command; six promotes with one key through both: one command, all 202; MCP on the second API |
| `test_no_duplicate_work.py` | four projects deployed, then promoted, at once through both APIs, each job published six more times at once: every command has `claim_number = 1` and one execution attempt, one worker logged under its claim, one audit event ended it; the objects have only the worker's field manager and the command's fence |
| `test_stale_worker_fenced.py` | the claiming worker frozen (SIGSTOP) past its lease during a deploy and during a promote; the other worker takes over; the resumed worker logs refusals only (lost claim, or a write fenced off with `ClaimLost`/`StaleFence`); the command ends once, by the last claim; objects single-writer; the route's fence never goes down; the site serves the records |
| `test_builds_across_build_workers.py` | both build workers drop one malformed hint (both LISTEN); `build_release` through both APIs on a pending attempt: three attempts, three Jenkins builds, three `build_attempt_started` events; a burst of four releases: one build each |
| `test_reconciler_two_workers.py` | a live site's route and Service deleted by hand: both restored at the project's last command number, then the route's generation, spec and fence stay the same for three reconcile intervals, and no reconciler reports a foreign manager, a fence ahead or a refusal |
| `test_bootstrap_standby.py` | task H5c: `bootstrap-2` started beside the holder stands by (the standby line, `provisioning_bootstrap_standby 1`, no round); a live site's namespace deleted while both run is rebuilt by the holder alone (one rotation, a Secret that logs in to the catalog); the holder killed, `bootstrap-2` takes over within 15 s and rebuilds the namespace deleted again (one rotation, the Secret logs in); the first bootstrap started again and `bootstrap-2` stopped, so the first holds the lock again |

The duplicate deliveries are injected (`inject_command_jobs`): a job message carries identifiers only,
and the claim decides on the rows, so a copy published by the test is exactly what a dispatcher
republish or a broker redelivery looks like. The command's dispatch row keeps only its last owner
(`claimed_by_worker`); there is no lease owner history, so "one execution" is read from the claim
number, the attempt count and the two workers' logs.

## Running the ordinary suite on two replicas

`make proto-e2e` runs against the replica stack too: `reset-sites.sh` stops every replica first.
Since task H5c the harness knows replicas (`tests/e2e/_stack.py`): `replica_names(process)` is
every replica the run started (from `provisioning/replicas`), `Stack.process_all(action, process)`
stops or kills all of them at once and starts them in order, and `process_logs(process)` lists
every `logs/latest/<process>[-N].log`; `tests/e2e/faults/_recovery_support.py` adds `log_marks`,
`logs_since` and `signal_workers` over every worker. The five tests H5b found assuming one worker
(both route-reclaim tests, the stuck-project test, the dead-letter re-drive and the no-dispatcher
edge case) use them, so they stop every worker and read every worker's log. Other fault tests
still act on the first worker only (`signal_worker`, `log_mark`/`worker_log_since`,
`stop-process worker`); they passed on the replica stack in H5b's run, where the second worker
doing part of the work did not change their outcome.
