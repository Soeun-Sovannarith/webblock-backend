# 0004: Durable acceptance, persistence, database roles and write guards

Status: **Current.** Decision `0007` records how this differs from the revision's illustrative SQL. The PostgreSQL
stores (`src/provisioning/infrastructure/postgres`) and migrations `00001` to `00003` implement it. Changing a rule
means changing this record first.

Scope: `internal/application`, `internal/adapters/postgres`, and the baseline migrations `00001` to `00003` as rewritten
in Phase 1R. Dispatch and leases are decision `0005`; transports, identity and error-to-status mapping are decision
`0006`.

## 1. Who checks what

- Authentication (token to principal) happens in the transport. **Authorization happens in the application**, at the
  start of every use case, reads included, so no transport or later caller can skip it. Order for every operation:
  action permission, then the operation gate (decision `0006` section 6), then project access, then validation and the
  use case.
- The principal carries the subject, the granted scopes and the user's platform ID (`user_id`, decision `0017`
  section 2; empty when the token names none). It carries no expiry: the token verifier refuses an expired token
  before a principal exists. It never carries the raw token. The zero principal is permitted nothing.
- No SQL transaction is open during a call to another service.

## 2. External ports and how production fails closed

Each port is defined in `internal/application` by its consumer. Their real contracts are blocked-external, so this
record defines only the Go shape Provisioning needs, not any path, scope or payload.

| Port | Used by | Open decision |
| --- | --- | --- |
| `ProjectAccess` | every operation | OD-01 |
| `Entitlement` | `deploy_release` and `promote_release`, at acceptance and again when the command runs | OD-04 |

- A production build starts and fails closed. `internal/adapters/unconfigured` implements both ports; every method
  returns an error that wraps `ErrDependencyNotConfigured` and names the open decision. No configuration key selects an
  implementation by name.
- Test doubles for these ports exist only in `_test.go` files. A test asserts that no non-test file under `internal/` or
  `cmd/` declares a stand-in for an external service and that the binaries do not link a test package.
- `rollback_release` does not consult `Entitlement`, so recovery is never blocked by an expired plan.
- The port that confirms a repository ID with Project arrives with release acceptance in Phase 3. Snapshot and
  generation-admission ports no longer exist: Project captures input and admits generation.

## 3. What each operation does in Phase 2

| Operation | Phase 2 behaviour |
| --- | --- |
| `deploy_release`, `promote_release`, `rollback_release` | Acceptance implemented end to end (section 4) and proven with doubles on real PostgreSQL 18 |
| `get_release`, `get_command`, `get_deployment` | Implemented (section 6) |
| `get_release_diagnostics` | Authorizes, then returns `ErrOperationNotAvailable`. An empty list would be a placeholder success; a diagnostic source arrives in Phase 6 |

 `suspend_hosting` and `resume_hosting` have no REST or MCP operation. They are created only from Payment events, in
 Phase 6. The acceptance store is written for all five kinds and tested for all five; no entry point creates a hosting
 command before Phase 6.

## 4. Accepting a command (deploy, promote, rollback)

Outside any transaction, in order: authorize; validate the key (`domain.ValidateIdempotencyKey`); compute `request_hash`
(decision `0001` section 4); look up the command by `(project_id, idempotency_key)` and, if one exists, return it when
the hash is equal or `ErrIdempotencyConflict` when it differs, without calling any other service; check entitlement for
deploy and promote.

Then one `READ COMMITTED` transaction with a configured lock timeout:

1. Lock the project's `project_release_state` row `FOR UPDATE`. No row means no release was ever accepted for the
   project: `ErrNotFound`.
2. Read the command by key again: an equal hash returns the stored command; a different hash is
   `ErrIdempotencyConflict`.
3. Read the project's command in flight. If there is one: `ErrOperationInProgress`, carrying its command ID.
4. Read the release by `(project_id, release_id)`, and the active release when one is set, with plain reads. An absent
   release is `ErrNotFound` (a release of another project is indistinguishable from a missing one).
5. `domain.CheckCommandPreconditions` with the locked project state: `ErrRouteNotStable`, `ErrHostingOffline`,
   `ErrIllegalTransition`, `ErrReleaseOutdated`.
6. Advance `operation_generation` by one, conditional on the value read, checked for exactly one row.
   `ErrGenerationOverflow` at the maximum.
7. Insert the command as `accepted` with `fencing_generation` equal to the new value and `requested_by` equal to the
   principal's subject, then call the acceptance hook, which writes the work row of decision `0005` in the same
   transaction.

- The project row lock is held from step 1 to commit, and every worker write for the project waits on it. The store
  keeps that span short: the reads of steps 2 to 4 may be combined and the writes of steps 6 and 7 sent together, as
  long as each "exactly one row" check and the precedence stay as stated. The round trips under the lock are measured on
  PostgreSQL 18 in Phase 2 before any number is chosen.
- The order above is the precedence when several failures apply. Replay comes before busy, so a retry of the command in
  flight returns that command.
- The release is read, not locked. The API role has no update privilege on `releases`, which a row lock would need, and
  none is required: every transition out of `built` or a later status is a worker write that holds the project row.
- Acceptance does not change the release status or the route state. "Promote starts" is the worker's first fenced write,
  in Phase 5.
- A unique violation that races past these checks is never a 500: the store rolls back, re-reads by key, and returns the
  replay, `ErrIdempotencyConflict` (`commands_idempotency_key`) or `ErrOperationInProgress` (`commands_one_in_flight`).
  A violation of `commands_fencing_key` cannot be produced through this path and is an internal error. Lock timeout,
  deadlock and serialization failures become `ErrContention`. No driver error type leaves the adapter.
- A rejected acceptance rolls back, so the counter does not move.
- **Client keys and Payment event keys never meet.** The lookups in this section see only deploy, promote and rollback
  commands, and uniqueness is separate for hosting commands (section 9). A tenant who guesses a future Payment event key
  and sends it as an `Idempotency-Key` therefore cannot make the later `suspend_hosting` collide with it.

## 5. Releases

- **Decided for Phase 3 in decision `0008`** (inbox, registry port, acceptance transaction, roles `provisioning_intake`
  and `provisioning_build`, database clock on these paths). Where this section and `0008` differ, `0008` wins.
- Release acceptance is Phase 3: consuming Project's "source published" event, confirming the repository ID with
  Project, inserting the release with `ON CONFLICT (project_id, source_generation_request_id) DO NOTHING` and reading it
  back, and creating the state row on a project's first release. Phase 2 tests seed releases and state rows directly as
  the schema owner. The first design report proposed building the use case in Phase 2; the approved plan keeps it in
  Phase 3 with its consumer and inbox.
- Rules already fixed for that phase, so that it does not invent them: the state row is inserted before the release, in
  one transaction; a state row starts at generation 0, `stable`, `serving`, with no active release; a repeated event
  whose repository ID or commit SHA differs from the stored release is a conflict that changes nothing, not a replay.
- No rule limits the number of unfinished releases. `ErrUnfinishedReleaseExists` is removed.
- A build starts automatically after acceptance; there is no build operation and no build command. Who starts it, and
  where its dispatch state lives, is OD-51.

## 6. Reads

- One read-only transaction per read, after authorization. Every query filters on `project_id`.
- `get_release` returns the release and its steps. The contract allows 100 steps; more than 100 stored attempts is
  reported as an internal error, not silently cut, because retry budgets keep attempts far below that.
- `get_command` returns any command of the project, including hosting commands, whose `release_id` is absent.
- `get_deployment` returns the state row (active release, `operation_generation`, `route_state`, `hosting_state`) and,
  when one exists, a summary of the project's command in flight. A project with no state row is `ErrNotFound`: reads
  report stored state, and Provisioning holds none for a project that never had a release.
- Rows are validated on load (enum labels, generation ranges, digest and commit SHA formats, non-nil IDs, the command's
  kind against its `release_id`, and the recomputed `request_hash`). A row that fails is `ErrCorruptRow`, an internal
  error, never passed on.

## 7. Worker writes and validation finding V1-08 item 4

 Domain struct fields stay exported. The finding is closed at the store.

- The store has no method that saves an arbitrary release, command or project state. The write paths in Phase 2 are
  command acceptance (section 4), the claim and the command finish of decision `0005`, and the outcome of a deploy
  command on its release (`built → candidate_ready` or `built → failed`). Later phases add their own paths the same way.
- Every write that originates from a worker for a command takes a fence value: project ID, command ID, fencing
  generation and lease token. It makes **three checks**, and any failure writes nothing:

| Check | How | Stops |
| --- | --- | --- |
| Project fence | Lock the state row where `operation_generation` equals the fencing generation; no row is `ErrStaleFence` | A worker of an older command after a newer one was accepted. The lock also serializes worker writes with acceptance |
| Lease token | Lock the work row and compare the token; a different token is `ErrLeaseLost` | Two workers on the same command; a worker whose lease was taken over |
| Status | `commands.status = 'running'` (`accepted` for the deadline failure of a command that was never claimed) and the expected release status in the `WHERE` clause, checked for exactly one row | A worker of a command that already finished while no newer command exists |

- The three checks overlap on purpose. While `commands_one_in_flight` holds, an older command is terminal before a newer
  one is accepted, so the status check or the lease token always refuses a stale worker as well, and no Phase 2 test can
  show the project fence stopping a write on its own. In Phase 2 the fence's role is the row lock that serializes worker
  writes with acceptance, and the tests pin which check refuses first (`ErrStaleFence`, `ErrLeaseLost`,
  `ErrIllegalTransition`). Its role against stale Kubernetes writes arrives with Phase 5.
- **Lock order**, amended by decision `0008` section 7: `provisioning_ops.source_published_inbox` comes first. For every
  transaction: `project_release_state`, then `provisioning_ops.command_work`, then `commands`, then `releases`, then
  `release_steps`. A transaction may skip a level and never goes back. When two releases are written, the old active
  release comes before the target, which is also the order the one-live index forces.
- In production Phase 2 no executor can succeed (decision `0005` section 5), so the deploy outcome path is proven with a
  recording executor in test files.
- `Release.Validate`, `Command.Validate` and `ProjectState.Validate` are added to the domain for rows loaded from
  storage.

## 8. Database roles (OD-18)

 The API and the worker have separate roles. `scripts/db/bootstrap-roles.sql` creates the cluster-wide `NOLOGIN` roles
 `provisioning_migrator`, `provisioning_api` and `provisioning_worker`. The baseline fails with SQLSTATE `42704` when
 the API or worker role is missing. `provisioning_app` is no longer created or named.

| Role | Privileges in schema `provisioning` after the baseline |
| --- | --- |
| `provisioning_api` | `SELECT` on the four tables; `INSERT` on `commands`; `UPDATE (operation_generation, updated_at)` on `project_release_state` |
| `provisioning_worker` | `SELECT` on the four tables; `UPDATE (status, failure_code, updated_at)` on `commands`; `UPDATE (updated_at)` on `project_release_state`, which makes the fence lock legal; `UPDATE (status, failure_code, failure_summary, updated_at)` on `releases` |

- No role may `DELETE`, `TRUNCATE` or run DDL.
- No role may insert a release, a state row or a step, or write `image_digest`, `promoted_at`, `active_release_id`,
  `route_state` or `hosting_state`. Each arrives in a new migration with the phase that uses it: release insertion,
  digest and steps in Phase 3; `promoted_at`, `active_release_id` and `route_state` in Phase 5; `hosting_state` in
  Phase 6. Only the worker side ever writes them.
- Role names are fixed in the migrations: environments attach their own login roles by membership. One login role per
  binary: a login that is a member of both roles, or that can `SET ROLE` to the other, would unite the grants, so the
  runbook forbids it and a test shows a member of one role cannot assume the other.
- Neither role owns anything, may create objects in `provisioning` or `provisioning_ops`, or may disable a trigger.
  `PUBLIC` has no privilege on either schema, and the default `EXECUTE` of `PUBLIC` on the guard functions is revoked.
- OD-50 decided by the owner: two more roles, `provisioning_intake` and `provisioning_build`, with the grants and guards
  of decision `0008` section 5.

## 9. Write guards

 Three layers: column privileges (section 8), conditional updates checked for rows affected (sections 4 and 7), and
 constraints and small triggers for invariants that never change. The release, command, step and route transition tables
 stay in Go only.

Constraints of the baseline, beyond primary keys and `NOT NULL`. Names are N's where it gives one.

| Table | Constraints |
| --- | --- |
| `releases` | `releases_project_release_key`, `releases_project_request_key` (unique); positive repository ID; commit SHA and digest formats; `releases_digest_after_build`; `releases_no_digest_before_built`; `releases_promoted_at_matches_status`; `releases_failure_code_matches_status`; unique indexes `releases_one_live_per_project` and `releases_one_promoting_per_project` |
| `project_release_state` | `project_release_state_active_fk`; `operation_generation` not negative |
| `release_steps` | `release_steps_release_fk`; `release_steps_attempt_key`; positive attempt; `release_steps_finish_after_start`; `release_steps_running_has_deadline`; index `release_steps_running_deadline` (dropped by migration `00025`: Notion shows no such index, constitution III.3) |
| `commands` | `commands_fencing_key` (unique); unique index `commands_idempotency_key` on `(project_id, idempotency_key)` for deploy, promote and rollback; unique index `commands_hosting_event_key` on the same columns for the two hosting kinds; `commands_release_fk`; `commands_release_by_kind`; `request_hash` format; positive `fencing_generation`; `commands_failure_code_matches_status`; `commands_requested_by_by_kind` (`requested_by`, `VARCHAR(255)`, is null exactly for the two hosting kinds, and not empty otherwise); unique index `commands_one_in_flight` |

Every foreign key is `ON DELETE RESTRICT`. No column has a default.

| Trigger guard | Rule |
| --- | --- |
| `releases_identity_immutable` | `release_id`, `project_id`, `source_generation_request_id`, `source_repository_id`, `source_commit_sha` and `created_at` never change; `image_digest` may change only from `NULL` to a value |
| `commands_identity_immutable` | `command_id`, `project_id`, `release_id`, `kind`, `idempotency_key`, `request_hash`, `fencing_generation`, `requested_by` and `created_at` never change |
| `project_release_state_generation_step` | `project_id` never changes; `operation_generation` stays the same or increases by exactly one |
| `commands_insert_shape` | A command is inserted as `accepted`, without a failure code, and with `fencing_generation` equal to the project's current `operation_generation` |
| `project_release_state_generation_has_command` | Deferred to commit: after `operation_generation` changed, a command of the project carries that value as its `fencing_generation`. The counter cannot move without a command |
| `releases_terminal_status_final` | The status of a `superseded`, `failed` or `rolled_back` release never changes |
| `commands_terminal_status_final` | The status of a `succeeded`, `failed` or `skipped` command never changes |

Guards apply to every role including the owner and raise SQLSTATE `23514` with the guard's name as the constraint name,
so callers and tests treat them like check constraints. Guard functions run with the caller's rights (never
`SECURITY DEFINER`), set `search_path` to `pg_catalog`, name every object with its schema, and are owned by
`provisioning_migrator`. A guard that only freezes columns may be declared `BEFORE UPDATE OF` those columns, so it does
not run on the frequent status-only updates; the tests must still show every guarded column refused. A superuser or the
owner can still disable a trigger; the guards limit bugs and a compromised application role, not the database
administrator.

 The four guards after the first three come from the security review. Without them a compromised or faulty API process
 could move the counter without a command, which leaves the project's running command unable to pass its fence and
 blocks the project, or insert a command in any status; and a faulty worker could turn a failed release back into
 `built`. They back invariant 4 of decision `0001` section 3 and the terminal statuses; the rest of the transition
 tables stays in Go.

Not enforced by the database, and stated as such (validation findings VR-03, VR-04): that only Payment events create
hosting commands: `provisioning_api` can insert a `suspend_hosting` or `resume_hosting` row and so take a Payment event
key for a project; the worker's enabled-kinds gate (decision `0005` section 5) keeps such a row from executing, and the
role that inserts hosting commands in Phase 6 (OD-50) must close this with a guard or a separate role before hosting is
enabled; that the failure code and summary of a terminal row stay as first written, and that a summary appears only on a
`failed` or `rolled_back` release (a worker can rewrite them; a summary on another status is caught when the row is
loaded); transition legality between non-terminal statuses; that a command inserted with plain SQL meets its
preconditions (a compromised API process can still insert a well-formed command for any project, and the worker's own
checks are the second line); agreement between `active_release_id` and the live release; that `route_state` and
`hosting_state` move only as decision `0001` section 3 allows; the fence itself; the older-release rule.

## 10. Mechanics

*See also decision [`0009`](0009-phase-4-cluster-stack.md) section 12: `make vuln` fetches the vulnerability database
once per run and scans offline.*

- sqlc is pinned in the Makefile and installed into `./bin`. `make generate` regenerates
  `internal/adapters/postgres/internal/generated` and `internal/adapters/postgres/jobstore/internal/generated`;
  `make generate-check` fails on any difference. Generated code is excluded from coverage only. Each generated package
  lives under its own store's `internal/` folder, and a test pins that only that store imports it, so no other package
  can reach the unconditional write queries and bypass the fenced paths.
- Transaction, statement and lock timeouts and pool sizes are required configuration with no default; a zero value is
  refused at construction. The numbers are OD-15.
- The application generates UUIDv7 identifiers from the system's cryptographic random source and takes time from an
  injected clock truncated to microseconds, so a replay equals the stored row.
- A database migrated before the baseline was rewritten keeps the old schema without any error. The runbook says to drop
  and recreate it (decision `0007` section 3).
- **Vulnerability gate and test-only dependencies.** `govulncheck -test` (v1.8.0) does not analyse what an in-package
  test file imports, and every `test/` folder here is an in-package test of its own folder (decision `0003`). A clean
  `make vuln` therefore did not cover dependencies used only by tests, such as Testcontainers. The gate adds a
  binary-mode scan of each test binary (`go test -c` for each test package and build tag, then
  `govulncheck -mode=binary`), so test-only dependencies are scanned as built. Advisories found that way are fixed like
  any other; at the time of writing `golang.org/x/crypto` v0.55.0 (GO-2026-6354, GO-2026-6355, reached only through
  Testcontainers in the integration test binary) is raised to v0.56.0. Both changes belong to the Phase 2 wiring task,
  which owns the module tidy.
- **Coverage of the store packages.** `make cover` measures unit tests only, which cannot exercise a PostgreSQL store.
  The 70% gate for handwritten packages is therefore enforced by `make cover-all`, which also counts the integration and
  messaging tests (added by the wiring task); `make cover` keeps the 80% gate for the core packages and reports its
  total for information. Store packages still carry unit tests for what needs no database (construction, refusal before
  connecting, context handling).
- **An unreachable corruption in a test.** `commands_fencing_key` cannot be violated while the guards hold. One
  integration test stores such a row from a superuser session with `session_replication_role = replica`, which skips
  triggers for that session only, to prove the store maps the violation to an internal error. This is not disabling a
  guard: no guard is altered and every other session is unaffected. ** Since Phase 3 the integration tests also seed
  rows the guards refuse through one seeding path of this kind (decision `0008` section 5); no test of a guard's
  behaviour uses it.
- On a cold connection the acceptance transaction spends two extra round trips under the project lock preparing
  statements (pgx's statement cache). Whether to change the pool's query mode is decided with the measurements of OD-15,
  not now.
- Layout mapping against ARCHITECTURE.md section 3: `accept_command.go` holds deploy, promote and rollback acceptance;
  `internal/adapters/unconfigured` is an addition.

## Not decided here

Project, Payment and identity contracts (OD-01, OD-02, OD-04, OD-05); the numbers (OD-15); inbox and outbox (OD-42); who
may issue a rollback and where it is audited (OD-45); the role for release acceptance and builds (OD-50); build dispatch
(OD-51).
