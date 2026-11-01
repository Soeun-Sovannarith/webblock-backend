# 0012: Adopt the revision 19 Provisioning names (migration `00011`)

**Amended by decision [`0013`](0013-python-port-without-crossplane.md)**: the Python code uses the new names everywhere,
not only in the stores (`0013` section 6). Sections 2 to 5 (migration `00011`) still apply.

Status: **Current.** Migration `00011` implements it, and the Python code uses the new names everywhere (`0013`
section 6). Source: `docs/new-platform-db-design.md` (revision 19 of the platform database design), section 5 and its
"Changes in revision 19". Earlier decisions keep the old names as history; the tables below map them, as `0010` does
for paths.

## 1. Scope

- **Renames only.** No table, column, constraint, index, trigger, enum value or row is added or removed. Types,
  nullability, checks, grants and guard behaviour stay as they are after `00010`.
- **Database and stores only.** The new names stop at the store boundary (`src/infrastructure/postgres/**`: queries,
  generated sqlc code, the hand-written `rows.go` mapping). Go domain and application names (`FencingGeneration`,
  `OperationGeneration`, `ActiveReleaseID`, …), the OpenAPI and MCP field names, the event schemas
  (`fencing_generation`) and the XR fields (`spec.operationGeneration`) keep their current names. A later decision may
  align them.
- **One exception: the value `serving` becomes `online` everywhere**: the enum value in the database,
  `domain.HostingStateServing` (becomes `HostingStateOnline = "online"`), and `src/contracts/openapi.yaml`
  (`enum: [online, offline]`). There is no translation layer.
- **The role `provisioning_payment_intake` is replaced by `provisioning_hosting_intake`** (section 4).
- `.claude/PLATFORM_DB_DESIGN.md` becomes revision 19 unchanged; section 6 lists where revision 19 differs from the
  repository.

## 2. Migration number

Revision 19 calls the rename `00009_rename_for_clarity.sql` and its review fixes "proposed `00010`". Revision 19 was
written from branch `phase-3` (migrations `00001` to `00008`). On `phase-4`:

- `00009_step_guard_hardening.sql` (validation finding V3-03) and `00010_revision17_and_candidate_steps.sql` (decision
  `0011` section 6) already exist and are immutable.
- Revision 19's "proposed `00010`" items B, C and E are **already implemented in our `00010`**, under the old names
  (`attempt_budget`, `last_redriven_by`, `last_redriven_at`, `provisioning_payment_intake`). Item A is `0011` section 3.
  Item D is a Phase 6 rule.
- The rename is therefore our **`00011_rename_for_clarity.sql`**. It renames what `00001` to `00010` created, including
  the items of `00010`.

## 3. Names

### 3.1 Tables, columns, types, enum value (revision 19 naming guide)

| Old | New |
| --- | --- |
| table `provisioning.project_release_state` | `provisioning.project_hosting` |
| `project_release_state.active_release_id` | `live_release_id` |
| `project_release_state.operation_generation` | `last_command_number` |
| `project_release_state.route_state`; type `provisioning.route_state` | `routing_status`; type `provisioning.routing_status` |
| `project_release_state.hosting_state`; type `provisioning.hosting_state`; value `serving` | `hosting_status`; type `provisioning.hosting_status`; value `online` |
| `releases.source_generation_request_id` | `generation_request_id` |
| `releases.source_repository_id` | `github_repository_id` |
| `releases.source_commit_sha` | `git_commit_sha` |
| `releases.image_digest` | `container_image_digest` |
| `commands.fencing_generation` | `command_number` |
| `commands.request_hash` | `request_fingerprint` |
| `commands.requested_by` | `requested_by_subject` |
| `release_steps.reason_code` | `failure_code` |
| `release_steps.details_ref` | `log_ref` |
| table `provisioning_ops.command_work` | `provisioning_ops.command_dispatch` |
| `command_work.lease_token` | `claim_number` |
| `command_work.claimed_by`, `lease_expires_at` | `claimed_by_worker`, `claim_expires_at` |
| `command_work.claim_count` | `execution_attempts` |
| `command_work.next_dispatch_at`, `last_confirmed_at` | `next_publish_at`, `last_broker_ack_at` |
| `command_work.retry_not_before`, `last_error_code` | `retry_after`, `last_failure_code` |
| table `provisioning_ops.source_published_inbox`; type `provisioning_ops.inbox_status` | `provisioning_ops.source_published_events`; type `provisioning_ops.source_event_status` |
| `source_published_inbox.repository_id`, `commit_sha` | `github_repository_id`, `git_commit_sha` |
| `source_published_inbox.conflict_count`, `last_conflict_at` | `mismatched_repeat_count`, `last_mismatched_repeat_at` |
| `source_published_inbox.attempt_budget` | `max_attempts` |
| `source_published_inbox.last_redriven_by`, `last_redriven_at` | `last_manual_retry_by`, `last_manual_retry_at` |

Unchanged on purpose (revision 19): `idempotency_key`, `attempt`, `deadline_at`, every other status value and every step
name, and every other column.

### 3.2 Constraint, index, trigger and guard function names

Rule: replace the old table prefix with the new table name, and replace every token that is an old column name, or an
old concept word of the naming guide (`lease` → `claim`, `conflict` → `mismatched_repeat`, `redriven` → `manual_retry`,
`digest` → `image` where revision 19 says so), with the new one. Revision 19 fixes the names marked †. A guard function
keeps the name of its trigger.

| Old | New |
| --- | --- |
| `releases_source_repository_id_check` | `releases_github_repository_id_check` † |
| `releases_source_commit_sha_check` | `releases_git_commit_sha_check` † |
| `releases_image_digest_check` | `releases_container_image_digest_check` † |
| `releases_digest_after_build` | `releases_image_after_build` † |
| `releases_no_digest_before_built` | `releases_no_image_before_built` † |
| `project_release_state_pkey` | `project_hosting_pkey` † |
| `project_release_state_operation_generation_check` | `project_hosting_last_command_number_check` † |
| `project_release_state_active_fk` | `project_hosting_live_release_fk` † |
| `commands_fencing_key` | `commands_command_number_key` † |
| `commands_fencing_generation_check` | `commands_command_number_check` † |
| `commands_request_hash_check` | `commands_request_fingerprint_check` † |
| `commands_requested_by_by_kind` | `commands_requested_by_subject_by_kind` † |
| `command_work_pkey` | `command_dispatch_pkey` |
| `command_work_command_fk` | `command_dispatch_command_fk` |
| `command_work_publish_attempts_check` | `command_dispatch_publish_attempts_check` |
| `command_work_claim_count_check` | `command_dispatch_execution_attempts_check` |
| `command_work_lease_token_check` | `command_dispatch_claim_number_check` |
| `command_work_claimed_by_check` | `command_dispatch_claimed_by_worker_check` |
| `command_work_lease_owner_with_expiry` | `command_dispatch_claim_owner_with_expiry` |
| `command_work_correlation_id_check` | `command_dispatch_correlation_id_check` |
| `command_work_last_error_code_check` | `command_dispatch_last_failure_code_check` |
| index `command_work_due` | `command_dispatch_due` |
| `source_published_inbox_pkey` | `source_published_events_pkey` |
| `source_published_inbox_repository_id_check` | `source_published_events_github_repository_id_check` |
| `source_published_inbox_commit_sha_check` | `source_published_events_git_commit_sha_check` |
| `source_published_inbox_attempts_check` | `source_published_events_attempts_check` |
| `source_published_inbox_attempt_budget_check` | `source_published_events_max_attempts_check` |
| `source_published_inbox_conflict_count_check` | `source_published_events_mismatched_repeat_count_check` |
| `source_published_inbox_conflict_time_matches_count` | `source_published_events_mismatched_repeat_time_matches_count` |
| `source_published_inbox_redriven_by_check` | `source_published_events_manual_retry_by_check` |
| `source_published_inbox_redriven_together` | `source_published_events_manual_retry_together` |
| `source_published_inbox_outcome_code_check`, `_outcome_matches_status`, `_release_matches_status`, `_finished_matches_status`, `_release_fk` | same suffix under `source_published_events_` |
| index `source_published_inbox_due` | `source_published_events_due` |
| trigger and function `project_release_state_generation_step` | `project_hosting_generation_step` † |
| constraint trigger and function `project_release_state_generation_has_command` | `project_hosting_generation_has_command` † |
| trigger and function `project_release_state_insert_shape` | `project_hosting_insert_shape` † |
| trigger and function `command_work_insert_shape`, `command_work_monotonic` | `command_dispatch_insert_shape`, `command_dispatch_monotonic` † |
| trigger and function `source_published_inbox_insert_shape`, `_update_shape` | `source_published_events_insert_shape`, `_update_shape` † |
| PostgreSQL 18 automatic `NOT NULL` names `<old table>_<old column>_not_null` | `<new table>_<new column>_not_null` |

Unchanged: `releases_pkey`, `releases_project_release_key`, `releases_project_request_key`,
`releases_project_request_release_key`, `releases_promoted_at_matches_status`, `releases_failure_code_matches_status`,
`releases_one_live_per_project`, `releases_one_promoting_per_project`, every `release_steps_*` and every other
`commands_*` name, including the three the error mapper matches (`commands_idempotency_key`,
`commands_hosting_event_key`, `commands_one_in_flight`).

The implementer checks this table against `pg_constraint`, `pg_index` and `pg_trigger` of a database migrated to
`00010`, and reports any name the table misses before writing the migration. The lead adds the missing names here.

## 4. Role

A migration runs as `provisioning_migrator` and cannot rename a cluster-wide role. `scripts/db/bootstrap-roles.sql`
therefore creates `provisioning_hosting_intake` (`NOLOGIN`, no members), and keeps creating
`provisioning_payment_intake` so that `00010` still runs on an empty server. `00011` refuses to run when
`provisioning_hosting_intake` is missing (one message naming every missing role, as in `00010`), and its
`commands_insert_shape` admits `suspend_hosting` and `resume_hosting` only for members of `provisioning_hosting_intake`.
After `00011`, membership of `provisioning_payment_intake` grants nothing; the owner drops it from servers where it
exists, as with `provisioning_app`.

## 5. Guard function bodies

PostgreSQL carries constraints, indexes, foreign keys, column grants and stored enum values through a rename, but not
PL/pgSQL function bodies. `00011` re-creates every guard whose body, `TABLE =` or `CONSTRAINT =` names an old table,
column, type, value or role, from its latest body (`00002`, `00004`, `00005`, `00006`, `00008` or `00010`), with only
the names changed. It ends with a check that fails the whole migration if any function body in `provisioning` or
`provisioning_ops` still contains an old name from section 3. `releases_terminal_status_final` and
`commands_terminal_status_final` contain no old name and are not touched.

## 6. Where revision 19 differs from the repository

`.claude/PLATFORM_DB_DESIGN.md` is revision 19 unchanged. Where it differs from `phase-4`, the repository and this
record win:

- It calls the rename `00009` and its fixes "proposed `00010`, not implemented"; here they are `00011` and the existing
  `00010` (section 2).
- Its section 5 describes migrations `00001` to `00008` as the merged state and says only `build` steps can be inserted;
  `00009` hardened the step guards, and `00010` added `candidate` steps written by `provisioning_worker` only (`0011`
  sections 6 and 12).
- Its roles table lists five roles and the old `cmd/` paths; the repository has seven (`provisioning_operator` and the
  hosting-intake role) and `src/hosts/…` paths (`0010`).
- Its section 7 ("Outside this document") still mentions `provisioning_ops.command_work` and `source_published_inbox` by
  their old names.

## 7. Revision 19 behaviour not implemented by this change

No field is needed for any of these, and none is implemented now. They are recorded in `open-decisions.md`:

- Project's "project deleted" event becomes a `suspend_hosting` command through the hosting-intake role; its event
  contract is Project's and is not invented.
- `resume_hosting` re-checks at execution that the client is entitled **and** that the project is not deleted.
- `offline` also covers a deleted project.
