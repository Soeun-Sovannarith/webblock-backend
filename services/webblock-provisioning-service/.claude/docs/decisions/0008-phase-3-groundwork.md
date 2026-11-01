# 0008: Phase 3 groundwork: source intake, release acceptance, build attempts, new roles

Status: **Current.** The build worker (`src/provisioning/hosts/build_worker`), the intake and build stores and
migrations `00007` and `00008` implement it. Changing a rule means changing this record first. This record amends
decisions `0001`, `0004` and `0005` where it says so; where they differ, this record wins.

Scope: Phase 3 groundwork that needs no other team's contract. Still open and not decided here: the event's envelope,
transport, producer identity and ordering (OD-02), the Project API (OD-01), the build coordinator (OD-37), how a build
is dispatched and started (OD-51), Payment's events (OD-04), the usage outbox, who verifies that a digest exists in the
project's own image repository (OD-32).

## 1. What is built and what runs

- No binary runs any of this in Phase 3. The intake entry point, the inbox processor and the build sweeper are library
  code proven by integration tests. A test asserts that `cmd/api` and `cmd/worker` do not link the intake or build
  packages.
- Enabling any consumer of Project's event requires a decided producer authentication (OD-02, OD-54); it is a
  precondition, not an addition. The owner will raise with Project that repository confirmation proves only the
  repository, not that the commit belongs to its published history.
- When wired, the intake path, the processor, build results and the sweeper run in `cmd/build-worker`, never in
  `cmd/api` or `cmd/worker` (one login role per binary, `0004` section 8).
- Release intake is not one of the seven transport operations. It is a separate use case (`ReleaseIntake`) outside
  `application.Service`, with no principal: decision `0004` section 1 (authorization at the start of every use case)
  applies to caller operations, not to system-event intake. No new `Err*` sentinel is added to `internal/domain` or
  `internal/application` for these paths; outcomes are returned as values, and existing sentinels are reused
  (`ErrIdempotencyConflict` for a conflicting event, `ErrResultMismatch` for an unrelated or late build result).

## 2. The inbox for Project's "source published" event (OD-42, Project half)

 One table per event kind, typed columns only, no payload column, in schema `provisioning_ops` (operational storage, not
 a business table): `provisioning_ops.source_published_inbox`.

| Column | Type | Rule |
| --- | --- | --- |
| `project_id`, `generation_request_id` | `uuid` | Primary key; the event's natural key (one release per generation request) |
| `repository_id` | `bigint` | 1 to 2^53−1 |
| `commit_sha` | `varchar(64)` | 40 or 64 lowercase hexadecimal |
| `status` | enum `provisioning_ops.inbox_status`: `pending`, `accepted`, `rejected`, `failed` | Last three terminal |
| `attempts` | `integer` | Not negative, never decreases |
| `next_attempt_at` | `timestamptz` | Take visibility or backoff |
| `outcome_code` | `varchar(100) NULL` | `domain.ValidateCode` format; present exactly when `rejected` or `failed` |
| `release_id` | `uuid NULL` | Present exactly when `accepted`; must be the release recorded for this project and generation request (composite foreign key, `ON DELETE RESTRICT`) |
| `conflict_count` | `integer` | Later events with the same key and a different repository or SHA; never decreases |
| `last_conflict_at` | `timestamptz NULL` | |
| `received_at`, `updated_at` | `timestamptz` | |
| `finished_at` | `timestamptz NULL` | Present exactly when terminal; write-once |

- Guards, same conventions as the baseline (`0004` section 9): an insert guard (fresh row: `pending`, zero attempts and
  conflicts, no outcome, release or finish) and an update guard (identity and event columns never change; `attempts` and
  `conflict_count` never decrease; `pending → accepted | rejected | failed` only; terminal status, `outcome_code`,
  `release_id` and `finished_at` write-once). A partial index on `next_attempt_at` where `finished_at IS NULL` serves
  the take. Rows are never deleted (retention: OD-15). *(Migration `00025` drops that index,
  `source_published_events_due`: Notion shows no such index, constitution III.3. The take is a sequential scan.)*
- **Intake** parses the four fields through the domain (nil UUID, repository ID and commit SHA rules) and stores nothing
  for malformed input. It inserts with `ON CONFLICT DO NOTHING` and reads the row back in the same transaction. Same
  repository and SHA: duplicate, no change. Different: conflict, `conflict_count` and `last_conflict_at` updated,
  nothing else, and `ErrIdempotencyConflict` returned; a conflict is never silent. A per-project cap on `pending` rows
  (required configuration) refuses intake beyond it with an outcome value. Every log line carries the project ID with
  the generation request ID, and never the event body.
- **Processor.** (a) Take: one short transaction, up to a batch of due rows `FOR UPDATE SKIP LOCKED`, fair across
  projects (at most a configured number per project per batch), `next_attempt_at` pushed by the visibility timeout,
  `attempts` incremented. (b) Outside any transaction, ask the repository registry (section 3), at most once per project
  per batch. (c) One transaction per row whose first write is the inbox row's conditional update (`status = 'pending'`
  and the `attempts` value it took), checked for exactly one row; zero rows means a newer take superseded it, and the
  transaction writes nothing.
- A required construction check: the visibility timeout exceeds
  `(ceil(batch / concurrency) + 1) × (registry call timeout + transaction timeout)`, so a taken row cannot become due
  again while it waits in its batch; the `+ 1` covers the take's own transaction (performance review; amended from task
  T4's finding).
- Retries are driven from SQL with `jobs.Backoff`; the budget is a maximum number of attempts and a deadline from
  `received_at`, both required. An exhausted budget ends the row `failed` `repository_unconfirmed`.
- `ErrDependencyNotConfigured` from the registry is not transient: the processor stops and returns it, leaving the rows
  `pending`. The future wiring must not start the processor with the unconfigured registry.

## 3. Release acceptance

- **Registry port**, defined by its consumer in `internal/application`:
  `RepositoryRegistry.RegisteredRepository(ctx, projectID) (repositoryID, registered bool, err error)`. The use case
  compares, so the rule is ours and tested. Production implementation: `internal/adapters/unconfigured`, naming OD-01.
  Equal: accept. Different: `rejected` `repository_mismatch`. Not registered: `rejected` `repository_not_registered`.
  The registered ID is never returned to anyone or logged next to another project.
- **The acceptance transaction**, after the inbox row's conditional update: insert the state row
  `ON CONFLICT (project_id) DO NOTHING` (generation 0, `stable`, `serving`, no active release); insert the release as
  `accepted` `ON CONFLICT (project_id, source_generation_request_id) DO NOTHING` and read it back (an insert with
  `RETURNING` returns no row on a conflict); a stored release with a different repository or SHA is `rejected`
  `release_conflict` and changes nothing else; insert the first build step, `build`, attempt 1, `pending`; finish the
  inbox row `accepted` with the release ID.
- (OD-49) Neither `hosting_state = offline` nor lost entitlement blocks release acceptance or the build. Deploy and
  promote already refuse both.
- The same generation request ID under two projects is accepted: the key is per project, as revision 16 writes it.
- `created_at` is the database time of the acceptance transaction (section 6). Events processed out of order make an
  older commit look newer to the older-release rule; whether the event carries an order is OD-02.

## 4. Build attempts (retry until a maximum)

- A build is not a command: no fence and no work row. Build writes lock the release, then the step, and never the
  project state row: no command touches a release before `built`.
- **Attempt identity.** `domain.BuildResult` gains the step ID and the attempt number, and the repository ID becomes
  required. The single entry point takes the release and the step: the step must belong to the release, be its `build`
  step, be `running`, and match the result's step ID and attempt; the release must be `building`; project, release ID,
  commit SHA and repository ID must equal the release. Anything else is `ErrResultMismatch` and changes nothing; the
  caller logs IDs only, which is the record (no audit table, OD-14). A late result for an attempt that was already
  failed is refused even when its digest looks valid.
- **One active attempt.** A partial unique index allows one `pending` or `running` step per release and step name.
- **Start** (`accepted → building` for attempt 1; the release stays `building` for later attempts): step
  `pending → running` with `started_at` and `deadline_at` = start time + a configured duration.
- **Success:** step `succeeded`, release `building → built` with the digest.
- **Failure or deadline** (`FailBuildAttempt`): the step goes `failed` with a reason code (`deadline_exceeded` from the
  sweeper). Below `BUILD_MAX_ATTEMPTS` the next attempt is inserted as `pending` and the release stays `building`; at
  the maximum the release goes `building → failed` with `build_attempts_exhausted`. `BUILD_MAX_ATTEMPTS` is required
  configuration from 1 to 10, so that later steps fit the 100-step read limit (`0004` section 6). Starting the next
  attempt is OD-51's dispatcher.
- **Sweeper**, `build` steps only (command steps are fenced writes of later phases): an unlocked scan of `running` steps
  past their deadline, with literal predicates and the clock materialized once (as the dispatcher's take does); then per
  candidate one transaction that locks the release `FOR UPDATE SKIP LOCKED` (busy means skip it this round), then the
  step, re-checks (release `building`, step `running`, deadline passed, highest attempt of its release), and calls
  `FailBuildAttempt`. Every release write in the build paths carries `status = 'building'` (or `accepted` for the start)
  in its `WHERE` clause and is checked for exactly one row.
- `pending` steps are not swept in Phase 3: nobody starts a build until OD-51 is decided, so accepted releases wait with
  a `pending` step. Recorded as a known consequence; a start deadline for pending steps comes with OD-51.
- `details_ref` is an opaque identifier validated by the domain (letters, digits, `.`, `_`, `-`, `/`; no `//`, no
  scheme, at most 500 characters), never a URL or free text.

## 5. Roles and guards (OD-50: two new roles)

| Role | May |
| --- | --- |
| `provisioning_intake` | `SELECT`, `INSERT` and the processor's column `UPDATE`s on the inbox; `INSERT` of a state row; `INSERT` of a release on its identity, status and time columns only (never `image_digest`, `promoted_at`, failure fields); `INSERT` of the first build step; `SELECT` on what it reads back and on every conflict target (`ON CONFLICT` needs it) |
| `provisioning_build` | `SELECT` on releases and steps; `UPDATE (status, image_digest, failure_code, failure_summary, updated_at)` on releases; `INSERT` of later build attempts; `UPDATE (status, started_at, finished_at, deadline_at, reason_code, details_ref)` on steps |

- `provisioning_api` and `provisioning_worker` are unchanged. Neither new role may delete, truncate, own objects, or
  write anything of another table. `scripts/db/bootstrap-roles.sql` creates both, and the migration that grants to them
  fails with SQLSTATE `42704` when either is missing, like the baseline.
- **Why a guard cannot test the role name** (security review): environments attach login roles by membership, so
  `current_user` is the login role. The build-role guard on `releases` therefore tests membership: when the session is a
  member of `provisioning_build` (`pg_has_role(current_user, 'provisioning_build', 'MEMBER')`) and is not a superuser,
  the only status changes allowed are `accepted → building`, `building → built` (with a digest) and `building → failed`
  (with a code). A superuser is exempt because it can disable any trigger anyway (`0004` section 9). Tests connect as
  login members, never with `SET ROLE`, and a mutation that replaces the membership test with a name comparison must be
  caught.
- **Role-independent guards** (every role, the owner included): the state-row insert guard of validation finding VR-08
  (generation 0, `stable`, `serving`, no active release); a release insert guard (`accepted`, no digest, failure or
  `promoted_at`, `created_at = updated_at` within the inserting transaction's time window, and the project's state row
  exists); a step insert guard (`pending`, no times, reason or reference; `build` steps only in Phase 3, on a release
  that is `accepted` or `building`; attempt 1, or the next attempt after a `failed` one); a step update guard (identity
  columns never change; `pending → running → succeeded | failed` and `pending → failed` only; terminal status final;
  `started_at`, `deadline_at`, `finished_at`, `reason_code` and `details_ref` write-once).
- Integration tests may seed rows that the guards refuse (state rows at other generations or states, releases and steps
  in later statuses, invalid shapes for constraint tests) through one seeding path that runs as superuser with
  `SET LOCAL session_replication_role = replica`, which skips triggers for that transaction only; check constraints
  still apply. Amends the last sentence of `0004` section 10.
- The roles are explained in the repository's `README.md`, at the owner's request.

## 6. Clocks (amends `0004` section 10 for these paths)

Release acceptance and the build paths read the database clock (`clock_timestamp()`) once per transaction and use it for
every timestamp they write and as `now` for the domain. Reason (performance and code-fit reviews): with an injected
clock on several hosts, a finish time from a host whose clock lags can fall before a start written by another, and
`release_steps_finish_after_start` and the domain then refuse the finish on every try, so the step can never be
resolved. The replay-equality reason for the injected clock in `0004` section 10 does not arise on these paths. Command
acceptance keeps the injected clock.

## 7. Lock order (amends `0004` section 7)

`provisioning_ops.source_published_inbox`, then `project_release_state`, then `provisioning_ops.command_work`, then
`commands`, then `releases`, then `release_steps`. Nothing that holds a later lock waits for an inbox row: only the
processor locks inbox rows.

## 8. Migrations

`00006` the state-row insert guard; `00007` the inbox (enum, table, guards, index); `00008` the two roles' grants, the
release and step guards and the one-active-attempt index; `00009` (section 13, validation finding V3-03) the attempt
bound and the step-start release check. The upgrade tests (`tests/integration/upgrade_test.go`) extend their version
lists and add representative version-5 rows (state rows of every shape, steps of every status) that must survive the
upgrade unchanged.

## 9. Implementation constraints from the dependency check

- A query must not bind a list of values of a `provisioning_ops` enum (`ANY($1::provisioning_ops.inbox_status[])`): pgx
  v5.11.0 cannot encode it. Status literals stay in the SQL text (also required by the partial-index plan rule of
  validation finding V2-02) or go through `text[]`.
- Both generated sqlc packages change with each migration and are regenerated in the same change; `make generate-check`
  enforces it. Each new store package has its own `sqlc.yaml` and `internal/generated`, and only it imports that
  package.
- `postgres:18` is not pinned by digest; pinning it is a separate change.

## 10. Choices accepted while implementing T0

- A failed build result has its own entry point, `ApplyFailedBuildResult`, sharing the match check with
  `ApplyBuildResult` (which applies successes only and refuses a matching result that carries a failure code), because a
  failure needs the attempt policy and the next step ID.
- `BuildResult` carries no failure summary (the step has no column for one and the release fails only with
  `build_attempts_exhausted`). It carries an optional `details_ref`, validated and write-once.
- `details_ref` also refuses empty, `.` and `..` path segments and a trailing `/`.
- A loaded inbox row is corrupt unless `last_conflict_at` is set exactly when `conflict_count > 0`, the three required
  times are set, and the counters fit `integer`; migration `00007` enforces the same.
- The per-project intake cap is a count and may be exceeded slightly by concurrent intakes of one project (no advisory
  lock outside the lock order of section 7). Intake checks duplicate and conflict before the cap. `AcceptRelease`
  retries once after a release-insert miss, then returns `ErrContention`. A missing release or step in the result path
  is `ErrResultMismatch`. `SweepBusy` covers a locked or vanished release.
- `IntakeParams.Validate` and `TakeInboxParams.Validate` return plain errors (no new sentinel; `application` cannot
  import `jobs.ErrInvalidConfig`); the loops validate the same values first with `jobs.ErrInvalidConfig`.
- `StartBuildAttempt` refuses a pending step that already has a deadline; this changes when OD-51 gives pending steps a
  start deadline.

## 11. Choices accepted while implementing T1 and T4

- `releases_build_transition` also lets a build member update an `accepted` or `building` release without changing its
  status, digest or failure columns (so the release's `updated_at` can move when an attempt below the maximum fails).
  Every other same-status write is refused.
- The intake role has table-level `INSERT` on `project_release_state` and `release_steps` (guards enforce the shape),
  `SELECT (project_id)` on `project_release_state` and no `SELECT` on `release_steps`. The inbox also enforces
  `last_conflict_at` exactly when `conflict_count > 0` and keeps `received_at` unchanged; no time bound on inbox
  inserts.
- `00008`'s index `release_steps_one_active_attempt` would fail on a database already holding two active attempts for
  one release and step; recorded in the migration header and the runbook.
- The inbox processor makes all registry calls of a batch, then all row writes; rows of one project may be written
  concurrently. Concurrency is at most the batch size; batch maximums are 1000. A registry answer of "registered" with
  an out-of-range repository ID is retried like an outage. The retry budget is checked only when the registry fails; a
  definite answer after the deadline is still applied. Retry delays are at least 1 µs.
- The take increments `attempts` before the registry is asked, so each start of a processor with the unconfigured
  registry adds one attempt to each taken row without finishing it. Accepted while no binary runs the processor (section
  1); the wiring decision must not start it with the unconfigured registry.
- The inbox processor receives a constructed `ReleaseIntake`; the sweeper builds its `BuildAttempts` from its configured
  maximum, which the future build-result wiring must share. `internal/application` logs the result-mismatch line itself,
  because that line is the record.
- Two telemetry keys are added for these paths: `generation_request_id` and `step_id`.

## 12. Choices accepted while implementing T2 and T3

- Intake of a conflicting event returns the outcome value `IntakeConflict` from the store; the use case turns it into
  `ErrIdempotencyConflict` (the store contract, not the brief's wording, is followed).
- The intake store refuses, as its internal error `ErrInvalidDecision`, any decision that names a release whose source
  differs from the locked inbox row, accepts an existing release other than the one it loaded, or creates rows that do
  not match the values the SQL writes or the transaction's clock. Decisions built by the application's constructors
  always pass. The take reads the database clock in its own statement, so an empty batch still carries a database time.
  The finishes inside the acceptance also check `attempts`. The second-miss `ErrContention` branch of intake has no
  deterministic test.
- A failed build attempt below the maximum leaves the release row untouched (`updated_at` included), as
  `domain.FailBuildAttempt` returns it unchanged; the same-status clause of `releases_build_transition` (section 11) is
  therefore unused today. If `updated_at` should move, the domain changes first.
- In the sweep, a vanished step under a locked release is `SweepStale`; a release missing for the project is
  `SweepBusy`. No store method takes a time, so "skewed application clocks" is tested as: every stored time lies between
  two database-clock readings around the call, and release and step share one `now`. The scan limit's upper bound is
  `MaxInt32` until a product maximum is chosen (OD-15).

## 13. Rulings on the Phase 3 validation findings

- **V3-03, adopted.** A new migration `00009` hardens the step guards: a build attempt number is at most 10
  (`domain.MaxBuildAttempts`, the upper bound of `BUILD_MAX_ATTEMPTS`, pinned by a test that compares the two), and a
  step may move `pending → running` only while its release is `accepted` or `building`. Both limit a compromised build
  login; neither changes a legal path.
- **V3-02, adopted as tests, not as a recorded redundancy.** The status conditions of the build store's release and step
  writes and the project filter of its release lock are pinned by store tests that run each write against a stale row
  and expect zero rows.
- **V3-04 and V3-06: preconditions of the wiring decision.** Before any binary runs the intake, the processor or the
  sweeper (OD-02, OD-51, OD-01), the wiring decision must provide: authenticated producers (OD-02, OD-54) and a global
  bound on pending inbox rows; a rule that the processor never starts with the unconfigured registry; and a start
  deadline for `pending` build steps with the sweeper covering them. Until then nothing runs this code (section 1).
- **V3-05:** documentation corrected (`0010` status, the `0005` pointer, the coverage script's comment).
- **V3-07, V3-08:** informational; `E2E_MUTANT` stays confined to the end-to-end test helper.

## 14. Choices accepted from tasks FA and FB

- The stale-row tests of the build store read the SQL text of the store's private generated queries from source and run
  it, because `tests/integration` cannot import a store's `internal/generated` package and no API is exported for tests;
  `make generate-check` ties that text to the store's query files.
- `00009` reads the release without a row lock when a step starts, so the release-then-step lock order holds; two
  compromised build sessions racing could miss an uncommitted release failure in the other (the `00008` insert guard
  reads the same way). Attempts above 10 written before `00009` are not rewritten; they can no longer get a next
  attempt.
