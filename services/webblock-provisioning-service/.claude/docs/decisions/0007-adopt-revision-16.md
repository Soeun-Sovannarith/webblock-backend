# 0007: Adoption of database and flow revision 16

Status: **Current; revision 16 was decided by the project owner.** This record adopts it, lists what it replaces,
and records how the lead reconciled it with the rules this repository already tests. Later revisions change parts of
it (`0012`, `0021`); the migrations under `src/migrations` implement the result. The lead's rulings in section 7
still wait for the owner's review (OD-16).

## 1. Sources and precedence

- `.claude/PLATFORM_DB_DESIGN.md` (called P below), revision 16 of the platform database design.
- `ai/reviews/provision-db-migration-note.md` (called N below), the Provisioning behaviour and migration note for the
  same revision. P refers to it as `provisioning-migration-note.md`, and its own header names `db-design-v3.md`; both
  mean these two files.

Where either conflicts with another document under `.claude/` or with decisions `0001` to `0006`, revision 16 wins. The
`.claude/` documents and decisions `0001`, `0002` and `0004` to `0006` were rewritten or amended in the same change as
this record. Decision `0003` is unaffected.

## 2. What revision 16 replaces

| Area | Before | Now |
| --- | --- | --- |
| Start of a release | Provisioning captured frozen input (`frozen`, `generating`) | A release is created from Project's "source published" event at an accepted commit (`accepted`, `building`). Repository ID and commit SHA are in the baseline |
| Fencing | The client sent `expected_generation`; releases carried `generation` | One counter, `project_release_state.operation_generation`. The server assigns `commands.fencing_generation`. Key reuse is detected by `request_hash` |
| Rollback | Per-release predecessor; rollback of a live release | `active_release_id` is the target. Retention is 1. A failed promotion restores inside the same command. `rollback` is operator recovery when the route is `uncertain` |
| Project state | Active release and generation | Adds `route_state` (`stable`, `changing`, `uncertain`) and `hosting_state` (`serving`, `offline`) |
| Commands | `deploy`, `promote`, `rollback` | Adds `suspend_hosting` and `resume_hosting`, status `skipped`, `failure_code`; `release_id` is null for hosting commands |
| Tables and keys | Five tables keyed `id` | Four tables (`tenant_resources` removed) keyed `<table>_id` |
| REST and MCP | Nine operations | Seven. `create_release` and `build_release` are removed; a build starts automatically when a release is accepted |
| Unfinished releases | At most one per project, by index | No limit in the database |

Owner rulings this overturns, stated so that nothing is reversed silently:

- "Keep rollback of a live release." `live → rolled_back` and `superseded → live` are removed. Retention 1 leaves no
  target for them, and N's `promoted_at` constraint rejects the first.
- The client-supplied expected generation. The range 0 to 2^53−1 stays.
- Frozen-input capture in Provisioning, and `tenant_resources`.

Owner rulings that stand: bare `sha256:` digest; the nil UUID is not an identity; unit tests in `test/` folders with
qualified imports; API and worker database roles; 404 without project access and 403 without the action; a production
build that starts and fails closed; one work row per accepted command for dispatch and lease; SQL guards as column
privileges, conditional updates and small triggers, with the transition tables in Go.

 Deploy and promote are refused at acceptance when the target release was created before the currently live release.
 This applies from Phase 2.

## 3. One-time exception to migration immutability

CLAUDE.md requires immutable, forward-only migrations and an exception record with reason, reviewer, expiry and
follow-up.

| Item | Content |
| --- | --- |
| What | Migrations `00001` to `00003` are rewritten in place to the revision 16 baseline |
| Reason | Revision 16 replaces the baseline instead of altering it: PostgreSQL cannot remove enum values, the old rows cannot be mapped forward (N section 3), and nothing was ever deployed. There is no remote, no CI and no database holding real data |
| Reviewer | The project owner, who ruled it |
| Scope | Those three files, once, in Phase 1R task R3 on branch `phase-2`. No other migration and no later change |
| Expiry | The exception ends when Phase 1R is merged. From then on the three files are immutable again, and the rule has no further exception once any database beyond a developer's machine has been migrated |
| Follow-up | The runbook tells developers to drop and recreate any database migrated before the rewrite, because goose tracks version numbers and not file content, so such a database keeps the old schema without any error. Test databases (`prov_test_*`) are created fresh each run. The cluster-wide role `provisioning_app` is no longer created or referenced; it stays on the owner's local server until the owner asks for it to be dropped. The validator of Phase 1R checks the rewritten baseline |

Consequence for the gates: "test upgrades with representative old rows" has no earlier version to start from. It is
recorded as **not applicable until the first migration after the baseline**, not as passed.

## 4. Deviations from N's illustrative SQL

Each keeps a convention the repository already tests. Each is to be proven on PostgreSQL 18 in Phase 1R.

| N's SQL | Adopted | Why |
| --- | --- | --- |
| `DEFAULT now()`, status, state and counter defaults, optional `uuidv7()` default | No column defaults. The application supplies IDs, timestamps, statuses and counters | A forgotten column fails instead of becoming `accepted` or `0`; an injected clock makes a replay equal the stored row. N calls `uuidv7()` optional |
| `updated_at = now()` in statements | A parameter | Same |
| `BEGIN`, `DROP SCHEMA … CASCADE`, `COMMIT` | Not used. goose supplies the transaction; the migrator-role guard stays | The migrations never drop a schema |
| Foreign keys without an action | `ON DELETE RESTRICT` on every foreign key | Existing rule; release history is never cascaded |
| Unnamed inline constraints | Every constraint is named: N's names where it gives them, otherwise `<table>_<column>_check` and `<table>_pkey` | Tests and the error mapper match on names |
| The identity trigger raises a bare exception | Raises SQLSTATE `23514` with a constraint name; functions set a fixed `search_path` | Callers and tests treat it like a check constraint |
| The identity trigger covers project and source fields and a set digest | Also covers `release_id` and `created_at`. A second trigger freezes the identity columns of `commands`. A third keeps `operation_generation` at the same value or exactly one higher | Stable invariants, carried from the earlier decision `0004` |
| No roles or grants | The baseline creates grants for `provisioning_api` and `provisioning_worker` and fails when either role is missing. `provisioning_app` disappears | Owner ruling on roles; grants are listed in decision `0004` |
| `fencing_generation > 0` with a counter starting at 0 | Kept as written. A project's counter is 0 to 2^53−1; a command's fencing generation is 1 to 2^53−1 | 0 means no command was ever accepted. The upper bound is enforced in Go and in every contract; the columns stay `BIGINT` |

Constraints added to N's set:

| Constraint | Rule | Why |
| --- | --- | --- |
| `releases_no_digest_before_built` | No digest while `accepted` or `building` | N's check runs in one direction only |
| `releases_failure_code_matches_status` | A failure code is present exactly when the release is `failed` or `rolled_back` | P says the reason is recorded; nothing enforced it |
| `commands_failure_code_matches_status` | A failure code is present exactly when the command is `failed` or `skipped` | Same; hosting commands have no release history to hold the reason |
| `releases_one_promoting_per_project` | At most one `promoting` release per project | The route can move for one release at a time |
| `release_steps_running_has_deadline` | A `running` step has a deadline | Otherwise the sweeper never finds it |
| `commands_hosting_event_key`, and `commands_idempotency_key` narrowed | N's single unique constraint on `(project_id, idempotency_key)` becomes two partial unique indexes: one for deploy, promote and rollback, one for the hosting kinds | A caller chooses its own key; a Payment event key is chosen by Payment. In one namespace a tenant who guessed an event key could reserve it and make the later suspend a conflict. The stored key is still exactly the Payment event key, as P says |
| Column `commands.requested_by` and `commands_requested_by_by_kind` | (OD-53) A column P's table does not have: the token subject of the caller, required for deploy, promote and rollback, null for hosting commands, immutable | Rollback is an operator action and agents issue commands through MCP; without it the authoritative row cannot say who asked. It is written in the acceptance transaction. `.claude/PLATFORM_DB_DESIGN.md` is the owner's document and still lacks the column; until it is updated this row is the record of the difference |
| Four trigger guards: `commands_insert_shape`, `project_release_state_generation_has_command`, `releases_terminal_status_final`, `commands_terminal_status_final` | See decision `0004` section 9 | Security review: they keep the counter tied to accepted commands and terminal rows terminal, against a faulty or compromised application role |

## 5. Deviations from N's flow text

Found in the design pass. Each changes what N literally says.

1. **Three checks on every worker write, not one.** N's fence (`operation_generation = fencing_generation`) stops a
   worker of an older command. A worker of a command that already finished still passes it until the next command is
   accepted, and so does a second worker on the same command. The lease token on the work row and a `status = 'running'`
   condition close both. The lock order is always project state row, work row, command, release, step (decisions `0004`
   section 7 and `0005` section 4).
2. **Entitlement is checked before the acceptance transaction and again when the command runs**, not between the lock
   and the update as N's SQL comment places it, because no transaction may be open during a call to another service.
3. **Replay and busy are decided by reads under the project row lock, before the counter moves.** N catches unique
   violations after incrementing. A violation aborts the transaction in PostgreSQL, so nothing can be compared inside
   it, and any variant that commits the increment on a replay would fence out the command in flight. The unique
   constraints stay as a backstop.
4. **Row locks need an update privilege.** The worker gets `UPDATE (updated_at)` on `project_release_state` so that the
   fence lock is legal. The API reads a release without locking it, which is safe because every later release transition
   holds the project row.
5. **Going live is two statements in a fixed order:** the old release to `superseded`, then the new one to `live`. The
   one-live index cannot be deferred.
6. **`request_hash` is not a hash of the request body.** P and N both say "SHA-256 of the request body". REST mutations
   now have no body, and CLAUDE.md requires comparing a normalized request. The hash covers a canonical text of the
   command's kind, project and target (decision `0001` section 4).

## 6. Where P and N differ

| Point | N | P | Ruling |
| --- | --- | --- | --- |
| Step timestamps | A set `finished_at` requires `started_at` | `finished_at`, when set, is not earlier than `started_at` | N's constraint is kept, and a step that fails before starting (`pending → failed`) records start and finish as the same instant. This satisfies both texts and is what the approved plan says. The handoff leaned to P's weaker form with a null start; the owner may choose that instead |
| Rollback acceptance | "`stable` (or `uncertain` for operator rollback)" | Rollback "requires `uncertain`" | P: a rollback is refused unless the route is `uncertain` |
| Failure code on a failed command | No constraint | "`failure_code` is recorded" | Enforced (section 4) |
| Outbox location | "in the Provisioning schema" | "per service schema" | Both say it needs its own design. Command dispatch keeps the work row in the separate schema `provisioning_ops` (decision `0005`); the generic outbox and the inbox are designed in the phases that first need them (OD-42) |
| Worker claim and lease | "not yet designed" | Listed as outside the document | The work row of decision `0005` is that design for the database half. Its lease columns are named `claimed_by` and `lease_expires_at` as both documents name them |
| Open items | Lease design, offline page | Suspended-state authority | The union applies (`open-decisions.md`) |

## 7. Flow rules the documents leave open

Ruled so that Phase 1R and Phase 2 can be built. The decision record that holds each rule is named.

- With an active release present, the only failure exit from `promoting` is `rolled_back`; `promoting → failed` is for a
  first publish. A promote that ends in a rollback is a `failed` command (`0001`).
- A release whose restore failed stays `promoting` while the route is `uncertain`. The operator `rollback` names that
  release (`0001`).
- `skipped` is reachable only from `running` and only for hosting commands (`0001`).
- A command that fails before its work touched the release leaves the release where it was: a failed deploy with no
  failed candidate step leaves it `built` (`0001`).
- `suspend_hosting` and `resume_hosting` are created only from Payment events, never through REST or MCP. They are in
  the schema and the domain now; their acceptance path comes with the Payment contract in Phase 6 (`0004`).
- `rollback` acceptance does not consult entitlement, so recovery is never blocked by an expired plan (`0004`). It does
  require `serving` hosting until OD-45 is decided, which no reachable state contradicts today (`0001`).
- The form of `request_hash`, and REST mutations without a body (`0001`, `0006`).
- "Created before the live release" compares `created_at` and then `release_id`; the refusal has its own error code,
  `release_outdated` (`0001`, `0006`).
- `get_deployment` for a project with no state row is `404` (`0004`). The first design report proposed `200` with
  default values.
- When two releases are written in one transaction, the old active release is locked and written before the target
  (`0004`).
- Release acceptance from the Project event stays in Phase 3. Phase 2 tests seed releases directly (`0004`).
- The Crossplane XRD drafts keep their fields in this revision. `spec.operationGeneration` is the fencing generation
  with minimum 1 (`0002`).
- Failure summaries reject control characters, line and paragraph separators and bidirectional controls only. A summary
  made only of legal invisible characters such as U+200B is not blank and is accepted (`0001`).

## 8. Not adopted, not proven

- The header comment of `migrations/00002_tables.sql` says "the three trigger guards"; the file defines seven. The file
  is immutable again since Phase 1R merged, so the comment stays and this line records the error (validation finding
  VR-07).

- N's validation checklist (section 9) is the acceptance list for the schema in Phase 1R, for the items a schema test
  can prove. Four items are not schema tests and belong to later phases: the sweeper resolving a crash in `changing`
  (Phase 5), suspend racing a payment (Phase 6), site-role isolation in the catalog (Phase 4, another team's schema),
  and hash detection "by the service" (Phase 2 application test).
- Questions that are the owner's or another service's are in `open-decisions.md` (OD-42 to OD-52). None of them is
  answered here by assumption.
- Kubernetes writes are not fenced by any of this. N says so itself; Phase 5 owns that half.
