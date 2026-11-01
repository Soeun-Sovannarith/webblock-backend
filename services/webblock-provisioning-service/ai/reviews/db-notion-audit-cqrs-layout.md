# Provisioning database against Notion: independent audit

Branch `cqrs-layout`, commit `c5f9392` (HEAD when read). Auditor: independent sub-agent, read-only on the repository
and on Notion. Stage 5 Verify. IDs: TR-12, TR-13, TR-14, TR-15 (FR-9 to FR-42), constitution III.3.

Notion pages (fetched for this audit, last edited):

- 5 · Provisioning, https://app.notion.com/p/40058175966e81769596d1ebcadb333d: 2026-10-27T08:42:46Z
- Database Design, https://app.notion.com/p/40058175966e81f790decccc6e9abf4a: 2026-10-26T16:20:45Z
- platform-db-v6 hub, https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad: 2026-10-26T16:21:34Z
- 6 · Shared catalog, https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a: 2026-10-21T14:16:06Z
- Web Block architecture (section 4.3 only), https://app.notion.com/p/40358175966e8198a088f123a9ab23fe:
  2026-10-28T10:05:03Z

## 0. Method and sources

- Final columns, types, NULL rules, physical order and enum labels:
  `src/provisioning/infrastructure/postgres/schema.py`. `tests/integration/test_schema_mirror.py` compares it with
  the migrated catalogue (columns by `attnum`, enum labels by `enumsortorder`). I did not run that test.
- Constraints, indexes, functions, triggers and grants: the Up sections of `src/migrations/00001` to `00023`; a later
  file overrides an earlier one. `00022` rebuilds `project_hosting`, `commands` and `source_published_events`, so for
  those three tables `00022` (plus `00023`) is the final state of keys, checks, indexes, triggers and grants.
- Roles: `scripts/db/bootstrap-roles.sql`. Decisions: `.claude/docs/decisions/` (`0004`, `0005`, `0011`, `0014`,
  `0021`, `0023`), constitution III.3.
- `cqrs-layout` changes no migration. Against `python` it changes only one comment in `bootstrap-roles.sql` and one
  import line in `schema.py`. So this audit also describes the schema on `python`.
- Not run: no database was built or migrated (the skill does not ask for one). This is a static reading of the SQL.
  `make py-test-integration` was not run.
- Rule for objects Notion does not name. An index, check, trigger or function that enforces a rule stated on the
  audited Notion pages is ACCEPTED-BY-DECISION under `0023` G20. An index that enforces no stated rule is DIFFERENT
  under III.3 ("no extra index, column, grant or failure code"), even where an older decision accepted it, because
  III.3 and G20 are the owner's later rules. A Notion rule enforced only by the application is MATCH ("app only"):
  the database holds nothing that contradicts it.
- PLANNED: every item Notion still marks "(planned)" or lists under "Planned, not yet applied" is already in the
  database (`00018` to `00023`) as Notion describes. Those rows are counted as MATCH. Nothing planned is missing.

## 1. Columns: name, type, nullability, physical order

Every column of every table equals Notion in name, type, NULL rule and physical position. A Notion type without
`NULL` is read as NOT NULL.

- MATCH `provisioning_ops.source_published_events`: 17 columns, Notion order (`schema.py:210-232`; rebuilt
  `00022:297-315`; `max_attempts` is seventh).
- MATCH `provisioning.project_hosting`: 9 columns, Notion order, `subdomain` third (`schema.py:135-149`;
  `00022:114-124`).
- MATCH `provisioning.releases`: 12 columns (`schema.py:116-133`; `00002:14-26`, renames `00011`, `00018`).
- MATCH `provisioning.release_steps`: 10 columns (`schema.py:151-166`; `00002:76-86`, renames `00011`).
- MATCH `provisioning.commands`: 13 columns, `hosting_reason` fifth (`schema.py:168-186`; `00022:189-203`).
- MATCH `provisioning_ops.command_dispatch`: 15 columns (`schema.py:188-208`; `00004:30-49`, renames `00011`).
- MATCH `provisioning_ops.usage_outbox`: 11 columns (`schema.py:234-250`; `00016:60-78`).
- MATCH Schemas `provisioning` (4 business tables, as the hub counts) and `provisioning_ops` (3 operational tables),
  as Notion's table list.

## 2. Enums: values and order

- MATCH `provisioning_ops.source_event_status`: pending, accepted, rejected, failed (`00007:27`, `00011`).
- MATCH `provisioning.routing_status`: stable, changing, uncertain (`00001:42`, `00011`).
- MATCH `provisioning.hosting_status`: online, offline (`00001:44`, value renamed in `00011`).
- MATCH `provisioning.offline_reason`: billing, user_suspended, project_deleted, admin_suspended (`00018:79`,
  `00019`).
- MATCH `provisioning.release_status`: accepted, building, built, candidate_ready, promoting, live, superseded,
  failed, rolled_back (`00001:29-32`).
- MATCH `provisioning.release_step_status`: pending, running, succeeded, failed (`00001:38-40`).
- MATCH `provisioning.release_step_name`: build, candidate, promotion, rollback, access_setup, observation
  (`00022:392-399`).
- MATCH `provisioning.command_status`: accepted, running, succeeded, failed, skipped (`00001:50-52`).
- MATCH `provisioning.command_kind`: deploy, promote, rollback, suspend_hosting, resume_hosting, redeploy,
  sync_domains (`00001:46-48`, `00014`, `00019` adds redeploy before sync_domains).
- MATCH `provisioning_ops.usage_event_status`: pending, sent (`00016:58`).

## 3. Rules Notion states, and what enforces them

### 3.1 source_published_events

- MATCH PK (project_id, generation_request_id), also the inbox key of architecture 4.3: `00022:333`.
- MATCH repository_id 1 to 2^53-1: check `00022:338`.
- MATCH git_commit_sha is SHA-1 or SHA-256 hex: check `00022:340`.
- MATCH Status pending to accepted, rejected or failed; accepted and rejected final; failed to pending only by
  `provisioning_operator`: update guard `00018:365`.
- MATCH attempts never decreases: update guard `00018:365`.
- MATCH A manual retry clears outcome_code and finished_at and raises max_attempts: update guard `00018:365`.
- MATCH outcome_code set exactly when rejected or failed: check `00022:346`.
- MATCH release_id set exactly when accepted, FK to the created release: check `00022:348`; composite FK
  `00022:334-337` (ON DELETE RESTRICT).
- MATCH last_mismatched_repeat_at set exactly when the count is above 0: check `00022:354`.
- MATCH finished_at set exactly when not pending: check `00022:350`.
- MATCH last_manual_retry_at set with last_manual_retry_by: check `00022:358`.
- MATCH A redelivered event finds the row; a mismatched repeat is counted, never accepted: PK, plus
  mismatched_repeat_count never decreases (update guard `00018:365`); counting is app only.
- MATCH The intake inserts a fresh pending row: insert guard `00011:706`; grant `00022:373`.
- MATCH Typed columns only, no payload column: `schema.py:210-232`.
- MATCH The API reads a diagnostic subset, never the repository, commit or retry columns: column grant
  `00022:382-384` (keys, status, attempts, outcome_code, release_id, mismatched_repeat_count, received_at,
  finished_at). See section 8, note 3.

### 3.2 project_hosting

- MATCH PK project_id; never deleted: `00022:139`; no DELETE grant.
- MATCH live_release_id is an FK to releases: composite FK `00022:140-141` (RESTRICT).
- MATCH live_release_id names the one live release and moves only when the new one is confirmed: app only
  (`0004` section 9).
- MATCH last_command_number 0 to 2^53-1, plus 1 per accepted command: check `00022:142`;
  `project_hosting_generation_step` `00011:427`; deferred `project_hosting_generation_has_command` `00011:459`,
  trigger `00022:166`.
- MATCH A worker writes only while its command_number equals last_command_number: app only (`0004` section 9).
- MATCH routing_status moves as Notion's diagram; uncertain only with the two failure codes: app only.
- MATCH New commands need stable, rollback needs uncertain, deploy and promote need online: app only; redeploy's
  live, online and stable checked by `commands_insert_shape` `00020:158`.
- MATCH offline exactly when offline_reasons is not empty: check `00022:144`.
- MATCH offline_reasons never NULL, no duplicates: NOT NULL `00022:121`; check `00022:146` with
  `offline_reasons_distinct` `00018:83`.
- MATCH project_deleted is never removed: `project_hosting_reasons_guard` `00020:276`.
- MATCH Each command adds or removes only its own reason; an admin resume removes only admin_suspended: app only
  (`0021` D5 to D7).
- MATCH owner_status_version never decreases, 0 means none: guard `00020:276`; check `00022:152`.
- MATCH A new row starts at 0, stable, online, no live release, no reason, version 0; an owner-status row may be
  offline with user_suspended: `project_hosting_insert_shape` `00020:235`.
- MATCH subdomain VARCHAR(63) NOT NULL, set at insert, never changes: `00022:117`; insert guard `00020:235`;
  reasons guard `00020:276` (only NULL to a value, impossible after NOT NULL).
- MATCH A sub-domain is one DNS label (hub, readable sub-domain): check `00022:154`.

### 3.3 releases

- MATCH One release per generation request: unique `releases_project_request_key` `00002:31` (architecture 4.3 keys
  the event by the same pair).
- MATCH At most one live and one promoting per project: partial unique indexes `00002:54`, `00002:58`.
- MATCH repository_id 1 to 2^53-1: check `00010:339-342`, renamed `00018`.
- MATCH git_commit_sha is hex and never changes: check `00002:35`; `releases_identity_immutable` `00018:335`.
- MATCH Digest is `sha256:`, NULL while accepted or building, required afterwards except failed, set once: checks
  `00002:38-44`; identity guard `00018:335`.
- MATCH failure_code set exactly when failed or rolled_back: check `00002:48`.
- MATCH promoted_at set exactly when live or superseded, set once: check `00002:46`; identity guard `00018:335`.
- MATCH Identity fields never change: identity guard `00018:335`.
- MATCH Once terminal, status and failure fields are final: `releases_terminal_status_final` `00010:283`.
- MATCH The build role moves only accepted to building, building to built (digest), building to failed (code):
  `releases_build_transition` `00011:314`.
- MATCH The intake creates the release as accepted: `releases_insert_shape` `00011:275`; column grant `00008:72`.
- MATCH Other transitions of the status table: app only (`0004` section 9).

### 3.4 release_steps

- MATCH release_id FK: `00002:88` (RESTRICT).
- MATCH attempt from 1, at most 10: check `00002:92`; insert guard `00015:145`; attempt frozen by the update guard.
- MATCH One attempt in flight per release and stage: partial unique `release_steps_one_active_attempt` `00008:312`.
- ACCEPTED-BY-DECISION "The next attempt only after a failed one": build steps follow it (`00015:145`); candidate,
  promotion and rollback steps may also follow a succeeded one. `0011` section 12; Notion's own release flow
  ("candidate_ready to built: replaced while idle", then a new deploy) needs it. See section 8, note 1.
- MATCH started_at set once; finished_at not before start; a failure before start sets both: check `00002:95`;
  update guard `00022:479`.
- MATCH deadline_at required while running: check `00002:98`.
- MATCH failure_code and log_ref set once: update guard `00022:479`.
- MATCH log_ref is never a URL: app only (`validate_log_ref`, `src/provisioning/core/domain/values/text.py`).
- MATCH Status only moves forward; a finished step never changes, no column of it: update guard `00022:479`.
- MATCH Build rows by the intake and the build role while the release is accepted or building: insert guard
  `00015:145`; update guard `00022:479`. One build-worker login holds both roles (`bootstrap-roles.sql:22`).
- MATCH Candidate rows by the worker only, while the release is built: insert guard `00015:145`; update guard
  `00022:479`.
- MATCH promotion and rollback allowed; access_setup and observation refused: insert guard `00015:145`.

### 3.5 commands

- MATCH One command in flight per project: `commands_one_in_flight` `00022:265`.
- MATCH command_number unique per project, 1 to 2^53-1: `00022:221`, check `00022:224`.
- MATCH Inserted as accepted with command_number equal to last_command_number: `commands_insert_shape` `00020:158`.
- MATCH Keys unique per project within their class (client: deploy, promote, rollback, redeploy; hosting events,
  also architecture 4.3): `00022:267`, `00022:269`.
- MATCH The key fixes the reason, `admin:<key>` is admin_suspended and never takes a system key, a resume never
  names project_deleted, retries use `.retry-<n>`: check `commands_hosting_reason_by_key` `00022:228`.
- MATCH hosting_reason required for hosting kinds, else NULL, never changes: check `00022:248`; identity guard
  `00018:213`.
- MATCH release_id NULL exactly for hosting kinds and sync_domains; redeploy names the live release: check
  `00022:250`; insert guard `00020:158`.
- MATCH requested_by_subject required for deploy, promote, rollback, redeploy and admin_suspended hosting commands,
  NULL for system reasons: check `00022:254`.
- MATCH deploy, promote, rollback, redeploy and admin hosting commands only from `provisioning_api`; other hosting
  commands only from `provisioning_hosting_intake`: insert guard `00020:158`; grants `00022:289`.
- MATCH Admin actions only for an `ADMIN` caller: app only (the token is not in the database; `0023` G10).
- MATCH A finished command never changes; identity never changes: `00022:548`; identity guard `00018:213`.
- MATCH failure_code set exactly when failed or skipped: check `00022:226`.
- MATCH skipped only for hosting kinds: check `commands_skipped_only_for_hosting` `00022:260`.
- MATCH The four skipped codes: app only (`src/provisioning/core/domain/values/failure_codes.py`).
- MATCH request_fingerprint is a SHA-256: hex check `00022:252`; its content is app only.
- MATCH "Accepted only while" preconditions and failure when unclaimed by the deadline: app only (`0004` section 9).

### 3.6 command_dispatch

- MATCH PK and FK command_id (RESTRICT): `00004:54-57`, FK re-created `00022:285`.
- MATCH Inserted with its command, in a fresh shape: `command_dispatch_insert_shape` `00011:642`.
- MATCH Counters only grow: `command_dispatch_monotonic` `00011:680`.
- MATCH claim_expires_at set with claimed_by_worker: check `00004:62`.
- MATCH finished_at set when the command ends, then fixed: app sets it; monotonic guard `00011:680` freezes it.
- MATCH A worker with an old claim_number may not write: app only (`0005`).
- MATCH Written by the API and the hosting intake (insert) and the worker (claims); never deleted: grants
  `00004:136-140`, `00013:22`; no DELETE grant.

### 3.7 usage_outbox

- MATCH PK release_id; with project_id it references releases: `00016:79`, composite FK `00016:90` (RESTRICT).
- MATCH event_key UNIQUE, always `publish-completed-<release_id>`: `00016:80`, check `00016:82`.
- MATCH completed_at is the release's promoted_at: insert guard `00022:407`.
- MATCH attempts never decreases: update guard `00022:445`.
- MATCH sent_at set exactly when sent; pending to sent only: check `00016:88`; update guard `00022:445`.
- MATCH The row never changes after sent: update guard `00022:445`.
- MATCH Only inserted for a live release: insert guard `00022:407`.
- MATCH Only `provisioning_worker` writes it: grants `00016:194-196`.

### 3.8 Shared catalog (Provisioning's part)

- MATCH Provisioning holds no catalog table: no migration touches schema `catalog` (Project owns it).
- MATCH Each site login `site_<uuid>` is a member of `catalog_site` only, with no RLS bypass: runtime code, not a
  migration (`src/provisioning/infrastructure/catalog/site_access.py:89-94`, `credential.py:34`); Project writes the
  same name into `catalog.websites.db_role_name`.

## 4. Roles and grants (final state after 00023)

Notion's roles: `provisioning_api` (client commands), `provisioning_worker` (runs commands, routing, hosting,
`usage_outbox`), `provisioning_hosting_intake` (suspend and resume), `provisioning_intake` / `provisioning_build`
(inbox, hosting rows, releases, build steps), `provisioning_operator` (inbox manual retry), `provisioning_bootstrap`
(reads only).

- MATCH `provisioning_api`: USAGE on both schemas (`00003:27`, `00004:135`); SELECT on releases and release_steps
  (`00003:30`), project_hosting and commands (`00022:174`, `00022:289`); INSERT on commands (`00022:289`) and
  command_dispatch (`00004:136`); UPDATE (last_command_number, updated_at) on project_hosting (`00022:179`); SELECT
  on 9 diagnostic columns of source_published_events (`00022:382`); EXECUTE `offline_reasons_distinct` (`00018:98`).
- MATCH `provisioning_worker`: USAGE on both; SELECT on the four business tables; UPDATE releases (status,
  failure_code, failure_summary, updated_at, promoted_at) (`00003:48`, `00012:10`); UPDATE project_hosting
  (live_release_id, routing_status, hosting_status, offline_reasons, updated_at) (`00022:182`); INSERT and UPDATE
  (status, started_at, finished_at, deadline_at, failure_code) on release_steps (`00010:364-366`); UPDATE commands
  (status, failure_code, updated_at) (`00022:291`); SELECT and UPDATE of the dispatch columns of command_dispatch
  (`00004:137-140`); SELECT, INSERT, UPDATE (status, attempts, next_attempt_at, last_failure_code, sent_at,
  updated_at) on usage_outbox (`00016:194-196`); EXECUTE the function.
- MATCH `provisioning_hosting_intake`: USAGE on both (`00013:13`); SELECT on releases and release_steps
  (`00013:14`), project_hosting and commands (`00022:174`, `00022:289`); INSERT on commands, project_hosting and
  command_dispatch (`00022:289`, `00022:176`, `00013:22`); UPDATE (last_command_number, updated_at,
  owner_status_version) on project_hosting (`00022:180`); EXECUTE the function. See section 8, note 4.
- MATCH `provisioning_intake`: USAGE on both (`00008:56`); SELECT, INSERT and UPDATE of 9 columns on
  source_published_events (`00022:373-375`); INSERT and SELECT (project_id) on project_hosting (`00022:176-177`);
  INSERT of the identity, status and time columns and SELECT on releases (`00008:72`, `00017:72`); INSERT on
  release_steps (`00008:77`); EXECUTE the function. The `00015` command grants are revoked (`00017:60-68`).
- MATCH `provisioning_build`: USAGE on provisioning; SELECT on releases and release_steps; UPDATE releases (status,
  container_image_digest, failure_code, failure_summary, updated_at); INSERT and UPDATE (status, started_at,
  finished_at, deadline_at, failure_code, log_ref) on release_steps (`00008:82-87`).
- MATCH `provisioning_operator`: USAGE on provisioning_ops (`00010:193`); SELECT and UPDATE (status, outcome_code,
  finished_at, max_attempts, next_attempt_at, last_manual_retry_by, last_manual_retry_at, updated_at) on
  source_published_events (`00022:377-379`). The `00020` backfill grants are revoked (`00021:77-81`).
- MATCH `provisioning_bootstrap`: USAGE on provisioning; SELECT (project_id, status) on releases (`00015:284`);
  SELECT (project_id, live_release_id, subdomain) on project_hosting (`00022:178`, `00023`). Reads only (`0023`
  G18a).
- MATCH Nothing is granted to PUBLIC: every rebuilt table runs `REVOKE ALL ... FROM PUBLIC` (`00022:173`, `288`,
  `372`) and every function `REVOKE ALL ON FUNCTION ... FROM PUBLIC`.

## 5. Notion's global rules

- MATCH No column defaults: no `DEFAULT`, `SERIAL`, `IDENTITY` or `GENERATED` in any migration (grep of
  `src/migrations/*.sql`).
- MATCH Every FK blocks deletes: all six final FKs are ON DELETE RESTRICT (`00002:89`, `00016:91`, `00022:141`,
  `00022:223`, `00022:286`, `00022:334-337`); no CASCADE anywhere.
- MATCH Command numbers and repository IDs fit 2^53-1: `00022:224`, `00022:142`, `00022:338`, `00010:339-342`.
- MATCH No application role may delete rows: no DELETE or TRUNCATE grant in any migration.
- MATCH Status and kind types are native lowercase enums (section 2).
- MATCH Service references have no FK: project_id and generation_request_id reference nothing outside the database.

## 6. In the database but not in Notion

- MATCH Extra tables, columns, views, sequences, schemas or extensions: none.
- ACCEPTED-BY-DECISION Unique keys `releases_project_release_key` (`00002:29`) and
  `releases_project_request_release_key` (`00007:34`): targets of the composite FKs that hold "the release of this
  project" (Notion: usage_outbox "with project_id references releases"; the inbox's "created release"). `0023` G20.
- ACCEPTED-BY-DECISION Unique `release_steps_attempt_key` (`00002:91`): a retry is "a new row with the next attempt
  number". `0023` G20.
- DIFFERENT Index `release_steps_running_deadline` (`00002:102`): a scan index for the deadline sweeper. It enforces
  no rule. Earlier acceptance: `0004` section 9.
- DIFFERENT Index `command_dispatch_due` (`00004:74`, renamed `00011`): a scan index for the dispatcher. It enforces
  no rule. Earlier acceptance: `0005` section 3.
- DIFFERENT Index `source_published_events_due` (`00022:362`): a scan index for the inbox retries. It enforces no
  rule. Earlier acceptance: `0008`.
- DIFFERENT Index `usage_outbox_due` (`00016:96`): a scan index for the usage publisher. It enforces no rule.
  Earlier acceptance: `0014` section 21.
- DIFFERENT Unique index `commands_domain_event_key` (`00015:68`, re-created `00022:271`): a key class for
  `sync_domains`, which Notion marks "In the enum, unused" and "Started by: Never". Notion names two key classes
  (client commands, hosting events). Earlier acceptance: `0014` section 16 item 1 and the `00017` amendment.
- ACCEPTED-BY-DECISION Code format `^[a-z][a-z0-9_]{0,99}$` on outcome_code (`00022:344`),
  command_dispatch.last_failure_code (`00004:66`) and usage_outbox.last_failure_code (`00016:86`): they keep each
  value a failure category, as Notion describes these fields. `0023` G20.
- ACCEPTED-BY-DECISION Not-empty checks on correlation_id (`00004:64`), claimed_by_worker (`00004:63`) and
  last_manual_retry_by (`00022:356`): a value that names nobody is not the worker or token subject Notion describes.
  `0023` G20.
- ACCEPTED-BY-DECISION Counter checks (attempts, publish_attempts, execution_attempts, claim_number,
  mismatched_repeat_count at least 0; max_attempts at least 1): counters "so far" that "only grow", and an attempt
  limit. `00004:58-60`, `00022:342-343`, `00022:352`, `00016:84`. `0023` G20.
- ACCEPTED-BY-DECISION Upper bound 2^53-1 on owner_status_version (`00022:152`): `0021` section 2.
- ACCEPTED-BY-DECISION Guard functions and triggers (`*_insert_shape`, `*_identity_immutable`,
  `*_terminal_status_final`, `*_update_shape`, `*_monotonic`, `releases_build_transition`,
  `project_hosting_generation_step`, `project_hosting_generation_has_command`, `project_hosting_reasons_guard`): each
  enforces a rule counted in section 3. Caller's rights, fixed search_path, no EXECUTE for PUBLIC. `0023` G20.
- ACCEPTED-BY-DECISION Function `offline_reasons_distinct` (`00018:83`): enforces "no duplicates". `0023` G20.
- ACCEPTED-BY-DECISION Role `provisioning_migrator`: owns every object and runs the migrations; not an application
  role. `0021` section 2.
- ACCEPTED-BY-DECISION Role `provisioning_payment_intake` (`bootstrap-roles.sql:96`): holds no privilege (asserted
  by `00018`), created only so `00010` runs on an empty server, dropped by `scripts/db/drop-retired-roles.sql`.
  `0021` D2.

## 7. DIFFERENT findings, most important first

All five are extra indexes. None changes data or behaviour; each needs either a new migration `00025` that drops it,
or a written owner exception (III.3 says "no extra index"; the older decisions that accepted them predate III.3 and
`0023` G20). The branch `cqrs-layout` itself changes no schema object.

1. `commands_domain_event_key` (`00015:68`, `00022:271`), unique on `(project_id, idempotency_key) WHERE kind =
   'sync_domains'`. Notion has only two key classes and never starts `sync_domains`. No role can insert a
   `sync_domains` command today (its guard admits only `provisioning_intake`, which lost INSERT on commands in
   `00017`). Change: `DROP INDEX provisioning.commands_domain_event_key;`, or the owner records it.
2. `release_steps_running_deadline` (`00002:102`), `deadline_at WHERE status = 'running'`. Change: drop, or the
   owner records it as allowed for the sweeper Notion names ("a sweeper fails late steps").
3. `command_dispatch_due` (`00004:74`), `next_publish_at WHERE finished_at IS NULL`. Change: drop, or the owner
   records it for the dispatcher Notion names ("a lost message is republished from the database").
4. `source_published_events_due` (`00022:362`), `next_attempt_at WHERE finished_at IS NULL`. Change: drop, or the
   owner records it ("When the row is next due").
5. `usage_outbox_due` (`00016:96`), `next_attempt_at WHERE status = 'pending'`. Change: drop, or the owner records
   it ("When the row is next due").

Dropping 2 to 5 makes each scan a sequential scan of the table; the tables are small in the prototype, but they grow
without a retention job (TR-16). That trade-off is the owner's.

All seven DIFFERENT findings of the 25 October audit are fixed by `00022`: column order of three tables, the
`release_step_name` order, `completed_at` equal to `promoted_at`, a sent usage row final, a finished step final,
skipped only for hosting kinds, and a finished command final in every column.

## 8. Notes for the lead (gaps Notion does not settle)

1. release_steps: Notion says "the next attempt only after a failed one" but its releases section returns an idle
   candidate to `built` for a new deploy, which needs a candidate attempt after a succeeded one. The database follows
   the releases section (`0011` section 12). The two Notion sentences disagree.
2. Database Design says `commands.idempotency_key` is "unique per website"; 5 · Provisioning says "unique per project
   within their class". The database follows 5 · Provisioning, so one project may hold the same key in a client
   command and a hosting command.
3. The API's diagnostic read (`00022:382`) includes `release_id` and `mismatched_repeat_count`. Notion lists "status,
   outcome, attempts, times" and forbids only the repository, commit and retry columns. I read the two as outcome and
   attempt data (MATCH); a stricter reading would revoke them.
4. `provisioning_hosting_intake` holds SELECT on `release_steps` (`00013:14`, `0014` section 9). Notion gives no grant
   list per table, and I found no hosting intake statement that reads `release_steps` (its acceptance reads
   `releases` for the live release). A stricter reading of "no extra grant" would revoke it.
5. `project_hosting_subdomain_check` accepts a lower-case DNS label. Project's database has no matching check, so
   Project's code must never store a sub-domain this check refuses, or the hosting insert fails.

PLANNED: 0 missing; every item Notion marks planned is applied and counted as MATCH. This is a static reading: no
database was migrated and `make py-test-integration` was not run.

Result: 5 DIFFERENT, 11 ACCEPTED-BY-DECISION, 119 MATCH
