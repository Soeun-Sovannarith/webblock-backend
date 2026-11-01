# Provisioning database against Notion: independent audit

Branch `db-00025`, commit `cc92056` (HEAD when read; the schema change is `85bbb76`). Auditor: independent
sub-agent, read-only on the repository and on Notion. Stage 5 Verify, the merge-gate audit of constitution III.3 and
decision `0023` G20. IDs: TR-12 (the schema equals S1, FR-9 to FR-42), TR-13 (roles), TR-14 (inbox keys), TR-15
(usage outbox), TR-16 (no retention job; the scans that lose their index).

Notion pages (fetched for this audit, last edited):

- 5 · Provisioning, https://app.notion.com/p/40058175966e81769596d1ebcadb333d: 2026-10-27T08:42:46Z
- Database Design, https://app.notion.com/p/40058175966e81f790decccc6e9abf4a: 2026-10-26T16:20:45Z
- platform-db-v6 hub, https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad: 2026-10-26T16:21:34Z
- Web Block architecture (section 4.3 only), https://app.notion.com/p/40358175966e8198a088f123a9ab23fe:
  2026-10-29T09:17:42Z

The first three carry the same last-edited times as in the previous audit (`ai/reviews/db-notion-audit-cqrs-layout.md`),
so Notion's Provisioning design has not changed since. The architecture page changed, but section 4.3 still names
`(project_id, generation_request_id)` for source published and the event key for hosting events.

## 0. Method and sources

- Final columns, types, NULL rules, physical order and enum labels:
  `src/provisioning/infrastructure/postgres/schema.py`, which declares no index or constraint object, so it needs no
  change for `00025`. `tests/integration/test_schema_mirror.py` proves it equal to the migrated catalogue.
- Constraints, indexes, functions, triggers and grants: the Up sections of `src/migrations/00001` to `00025`; a later
  file overrides an earlier one. `00024` is absent (reverted before this branch, `a313b7e`); the number is skipped.
- Roles: `scripts/db/bootstrap-roles.sql`. Decisions: `.claude/docs/decisions/` (`0004`, `0005`, `0008`, `0011`,
  `0014`, `0021`, `0023`), constitution III.3.
- What this branch changes against its base `39add4d` (`git diff --stat`): one new migration
  `00025_drop_extra_indexes.sql`, one new test `tests/integration/test_schema_00025.py`, a docstring in
  `statements.py`, two test adjustments, the change note and four decision records. No other migration, no
  `schema.py` change, no role script change. So every row of the previous audit outside its section 6 and 7 still
  holds with the same `file:line` evidence; I re-read the cited lines for the rules below that 00025 could touch
  (key classes, guards, the final index set) and spot-checked the column lists in `schema.py`.
- Final index set, from every `CREATE INDEX`, `UNIQUE`, `PRIMARY KEY`, `ALTER INDEX` and `DROP INDEX` in the
  migrations (section 6 lists it).
- Not run: no database was built or migrated. This is a static reading of the SQL. `make py-test-integration` (which
  includes `test_schema_00025.py` and `test_schema_mirror.py`) was not run.
- Rule for objects Notion does not name, as before: an index, check, trigger or function that enforces a rule stated
  on the audited Notion pages is ACCEPTED-BY-DECISION under `0023` G20; an index that enforces no stated rule is
  DIFFERENT under III.3. A Notion rule enforced only by the application is MATCH ("app only").
- PLANNED: every item Notion marks "(planned)" or lists under "Planned, not yet applied" is in the database
  (`00018` to `00023`) as Notion describes, and counted as MATCH. Nothing planned is missing.

## 1. Columns: name, type, nullability, physical order

Every column of every table equals Notion in name, type, NULL rule and physical position. Unchanged by `00025`.

- MATCH `provisioning_ops.source_published_events`: 17 columns, Notion order (`schema.py:210-232`; `00022:297-315`).
- MATCH `provisioning.project_hosting`: 9 columns, `subdomain` third (`schema.py:135-149`; `00022:114-124`).
- MATCH `provisioning.releases`: 12 columns (`schema.py:116-133`; `00002:14-26`, renames `00011`, `00018`).
- MATCH `provisioning.release_steps`: 10 columns (`schema.py:151-166`; `00002:76-86`, renames `00011`).
- MATCH `provisioning.commands`: 13 columns, `hosting_reason` fifth (`schema.py:168-186`; `00022:189-203`).
- MATCH `provisioning_ops.command_dispatch`: 15 columns (`schema.py:188-208`; `00004:30-49`, renames `00011`).
- MATCH `provisioning_ops.usage_outbox`: 11 columns (`schema.py:234-250`; `00016:60-78`).
- MATCH Schemas `provisioning` (4 business tables, as the hub counts) and `provisioning_ops` (3 operational tables).

## 2. Enums: values and order

Unchanged by `00025`.

- MATCH `provisioning_ops.source_event_status`: pending, accepted, rejected, failed (`00007:27`, `00011`).
- MATCH `provisioning.routing_status`: stable, changing, uncertain (`00001:42`, `00011`).
- MATCH `provisioning.hosting_status`: online, offline (`00001:44`, `00011`).
- MATCH `provisioning.offline_reason`: billing, user_suspended, project_deleted, admin_suspended (`00018:79`,
  `00019`).
- MATCH `provisioning.release_status`: accepted, building, built, candidate_ready, promoting, live, superseded,
  failed, rolled_back (`00001:29-32`).
- MATCH `provisioning.release_step_status`: pending, running, succeeded, failed (`00001:38-40`).
- MATCH `provisioning.release_step_name`: build, candidate, promotion, rollback, access_setup, observation
  (`00022:392-399`).
- MATCH `provisioning.command_status`: accepted, running, succeeded, failed, skipped (`00001:50-52`).
- MATCH `provisioning.command_kind`: deploy, promote, rollback, suspend_hosting, resume_hosting, redeploy,
  sync_domains (`00001:46-48`, `00014`, `00019`).
- MATCH `provisioning_ops.usage_event_status`: pending, sent (`00016:58`).

## 3. Rules Notion states, and what enforces them

`00025` drops no constraint, trigger or function; only the five indexes of section 6. None of the five enforced a
rule below, so every rule keeps its enforcement.

### 3.1 source_published_events

- MATCH PK (project_id, generation_request_id), the inbox key of architecture 4.3 (TR-14): `00022:333`.
- MATCH repository_id 1 to 2^53-1: check `00022:338`.
- MATCH git_commit_sha is SHA-1 or SHA-256 hex: check `00022:340`.
- MATCH pending to accepted, rejected or failed; accepted and rejected final; failed to pending only by
  `provisioning_operator`: update guard `00018:365`.
- MATCH attempts never decreases: update guard `00018:365`.
- MATCH A manual retry clears outcome_code and finished_at and raises max_attempts: update guard `00018:365`.
- MATCH outcome_code set exactly when rejected or failed: check `00022:346`.
- MATCH release_id set exactly when accepted, FK to the created release: check `00022:348`; FK `00022:334-337`.
- MATCH last_mismatched_repeat_at set exactly when the count is above 0: check `00022:354`.
- MATCH finished_at set exactly when not pending: check `00022:350`.
- MATCH last_manual_retry_at set with last_manual_retry_by: check `00022:358`.
- MATCH A redelivered event finds the row; a mismatched repeat is counted, never accepted: PK, update guard
  `00018:365`; counting app only.
- MATCH The intake inserts a fresh pending row: insert guard `00011:706`; grant `00022:373`.
- MATCH Typed columns only, no payload column: `schema.py:210-232`.
- MATCH "When the row is next due" (next_attempt_at): a column only; Notion names no index. After `00025` the retry
  scan reads the table without `source_published_events_due` (app only, TR-16).
- MATCH The API reads a diagnostic subset, never the repository, commit or retry columns: column grant
  `00022:382-384`. See section 8, note 3.

### 3.2 project_hosting

- MATCH PK project_id; never deleted: `00022:139`; no DELETE grant.
- MATCH live_release_id is an FK to releases: composite FK `00022:140-141` (RESTRICT).
- MATCH live_release_id names the one live release and moves only when the new one is confirmed: app only.
- MATCH last_command_number 0 to 2^53-1, plus 1 per accepted command: check `00022:142`; `00011:427`, `00011:459`,
  trigger `00022:166`.
- MATCH A worker writes only while its command_number equals last_command_number: app only (`0004` section 9).
- MATCH routing_status moves as Notion's diagram; uncertain only with the two failure codes: app only.
- MATCH New commands need stable, rollback needs uncertain, deploy and promote need online: app only; redeploy's
  checks in `commands_insert_shape` `00020:158`.
- MATCH offline exactly when offline_reasons is not empty: check `00022:144`.
- MATCH offline_reasons never NULL, no duplicates: NOT NULL `00022:121`; check `00022:146`, function `00018:83`.
- MATCH project_deleted is never removed: `project_hosting_reasons_guard` `00020:276`.
- MATCH Each command adds or removes only its own reason; an admin resume removes only admin_suspended: app only.
- MATCH owner_status_version never decreases, 0 means none: guard `00020:276`; check `00022:152`.
- MATCH A new row starts at 0, stable, online, no live release, no reason, version 0; an owner-status row may be
  offline with user_suspended: `project_hosting_insert_shape` `00020:235`.
- MATCH subdomain VARCHAR(63) NOT NULL, set at insert, never changes: `00022:117`; guards `00020:235`, `00020:276`.
- MATCH A sub-domain is one DNS label (hub, readable sub-domain): check `00022:154`.

### 3.3 releases

- MATCH One release per generation request: unique `releases_project_request_key` `00002:31`.
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
- MATCH attempt from 1, at most 10: check `00002:92`; insert guard `00015:145`; attempt frozen by `00022:479`.
- MATCH One attempt in flight per release and stage: partial unique `release_steps_one_active_attempt` `00008:312`.
- ACCEPTED-BY-DECISION "The next attempt only after a failed one": build steps follow it (`00015:145`); candidate,
  promotion and rollback steps may follow a succeeded one. `0011` section 12; see section 8, note 1.
- MATCH started_at set once; finished_at not before start; a failure before start sets both: check `00002:95`;
  update guard `00022:479`.
- MATCH deadline_at required while running: check `00002:98`. "A sweeper fails late steps" is app only; after
  `00025` the sweep reads the table without `release_steps_running_deadline` (TR-16).
- MATCH failure_code and log_ref set once: update guard `00022:479`.
- MATCH log_ref is never a URL: app only (`src/provisioning/core/domain/values/text.py`, `validate_log_ref`).
- MATCH Status only moves forward; a finished step never changes: update guard `00022:479`.
- MATCH Build rows by the intake and the build role while the release is accepted or building: insert guard
  `00015:145`; update guard `00022:479`.
- MATCH Candidate rows by the worker only, while the release is built: `00015:145`; `00022:479`.
- MATCH promotion and rollback allowed; access_setup and observation refused: insert guard `00015:145`.

### 3.5 commands

- MATCH One command in flight per project: `commands_one_in_flight` `00022:265`.
- MATCH command_number unique per project, 1 to 2^53-1: `00022:221`, check `00022:224`.
- MATCH Inserted as accepted with command_number equal to last_command_number: `commands_insert_shape` `00020:158`.
- MATCH Keys unique per project within their class, exactly the two classes Notion names (client commands: deploy,
  promote, rollback, redeploy; hosting events), TR-14: `commands_idempotency_key` `00022:267`,
  `commands_hosting_event_key` `00022:269`. The third class (`sync_domains`, `00022:271`) is dropped by `00025:34`.
- MATCH The key fixes the reason, `admin:<key>` is admin_suspended, a resume never names project_deleted, retries
  use `.retry-<n>`: check `commands_hosting_reason_by_key` `00022:228`.
- MATCH hosting_reason required for hosting kinds, else NULL, never changes: check `00022:248`; `00018:213`.
- MATCH release_id NULL exactly for hosting kinds and sync_domains; redeploy names the live release: check
  `00022:250`; insert guard `00020:158`.
- MATCH requested_by_subject required for deploy, promote, rollback, redeploy and admin_suspended hosting commands,
  NULL for system reasons: check `00022:254`.
- MATCH deploy, promote, rollback, redeploy and admin hosting commands only from `provisioning_api`; other hosting
  commands only from `provisioning_hosting_intake`: insert guard `00020:158`; grants `00022:289`.
- MATCH sync_domains "In the enum, unused", "Started by: Never": no role holds both INSERT on commands and the
  membership `commands_insert_shape` asks for (`00020:180-182`, `00017:60-68`); the code refuses it before any SQL
  (`statements.py`, `command_by_key`).
- MATCH Admin actions only for an `ADMIN` caller: app only (`0023` G10).
- MATCH A finished command never changes; identity never changes: `00022:548`; `00018:213`.
- MATCH failure_code set exactly when failed or skipped: check `00022:226`.
- MATCH skipped only for hosting kinds: check `00022:260`.
- MATCH The four skipped codes: app only (`src/provisioning/core/domain/values/failure_codes.py`).
- MATCH request_fingerprint is a SHA-256: hex check `00022:252`; its content is app only.
- MATCH "Accepted only while" preconditions and failure when unclaimed by the deadline: app only.

### 3.6 command_dispatch

- MATCH PK and FK command_id (RESTRICT): `00004:54-57`, FK re-created `00022:285`.
- MATCH Inserted with its command, in a fresh shape: `command_dispatch_insert_shape` `00011:642`.
- MATCH Counters only grow: `command_dispatch_monotonic` `00011:680`.
- MATCH claim_expires_at set with claimed_by_worker: check `00004:62`.
- MATCH finished_at set when the command ends, then fixed: app sets it; `00011:680` freezes it.
- MATCH A worker with an old claim_number may not write: app only (`0005`).
- MATCH Written by the API and the hosting intake (insert) and the worker (claims); never deleted: grants
  `00004:136-140`, `00013:22`; no DELETE grant.
- MATCH "A lost message is republished from the database" (next_publish_at): app only; after `00025` the
  dispatcher reads the table without `command_dispatch_due` (TR-16).

### 3.7 usage_outbox

- MATCH PK release_id; with project_id it references releases: `00016:79`, composite FK `00016:90` (RESTRICT).
- MATCH event_key UNIQUE, always `publish-completed-<release_id>`: `00016:80`, check `00016:82`.
- MATCH completed_at is the release's promoted_at: insert guard `00022:407`.
- MATCH attempts never decreases: update guard `00022:445`.
- MATCH sent_at set exactly when sent; pending to sent only (TR-15): check `00016:88`; update guard `00022:445`.
- MATCH The row never changes after sent: update guard `00022:445`.
- MATCH Only inserted for a live release: insert guard `00022:407`.
- MATCH Only `provisioning_worker` writes it: grants `00016:194-196`.

### 3.8 Shared catalog (Provisioning's part)

- MATCH Provisioning holds no catalog table: no migration touches schema `catalog`.
- MATCH Each site login `site_<uuid>` is a member of `catalog_site` only: runtime code, not a migration
  (`src/provisioning/infrastructure/catalog/site_access.py:89-94`, `credential.py:34`).

## 4. Roles and grants (final state after 00025)

`00025` grants and revokes nothing (`00025:34-38` are its only statements after the role check), so the final grants
are those after `00023`. Notion's roles: `provisioning_api`, `provisioning_worker`, `provisioning_hosting_intake`,
`provisioning_intake` / `provisioning_build`, `provisioning_operator`, `provisioning_bootstrap` (reads only). TR-13.

- MATCH `provisioning_api`: USAGE on both schemas; SELECT on releases, release_steps, project_hosting, commands;
  INSERT on commands and command_dispatch; UPDATE (last_command_number, updated_at) on project_hosting; SELECT on 9
  diagnostic columns of source_published_events; EXECUTE `offline_reasons_distinct` (`00003:27-30`, `00004:135-136`,
  `00022:174-179`, `00022:289`, `00022:382`, `00018:98`).
- MATCH `provisioning_worker`: USAGE on both; SELECT on the four business tables; the column UPDATEs on releases,
  project_hosting, release_steps, commands and command_dispatch; SELECT, INSERT, UPDATE of six columns on
  usage_outbox; EXECUTE the function (`00003:48`, `00012:10`, `00022:182`, `00010:364-366`, `00022:291`,
  `00004:137-140`, `00016:194-196`).
- MATCH `provisioning_hosting_intake`: USAGE on both; SELECT on releases, release_steps, project_hosting, commands;
  INSERT on commands, project_hosting, command_dispatch; UPDATE (last_command_number, updated_at,
  owner_status_version) on project_hosting; EXECUTE the function (`00013:13-22`, `00022:174-180`, `00022:289`).
  See section 8, note 4.
- MATCH `provisioning_intake`: USAGE on both; SELECT, INSERT, UPDATE of 9 columns on source_published_events;
  INSERT and SELECT (project_id) on project_hosting; INSERT and SELECT on releases; INSERT on release_steps; EXECUTE
  the function (`00008:56-77`, `00017:60-72`, `00022:173-177`, `00022:373-375`).
- MATCH `provisioning_build`: USAGE on provisioning; SELECT on releases and release_steps; UPDATE of five release
  columns; INSERT and UPDATE of six release_steps columns (`00008:82-87`).
- MATCH `provisioning_operator`: USAGE on provisioning_ops; SELECT and UPDATE of eight retry columns on
  source_published_events (`00010:193`, `00022:377-379`); the `00020` backfill grants are revoked (`00021:77-81`).
- MATCH `provisioning_bootstrap`: SELECT (project_id, status) on releases; SELECT (project_id, live_release_id,
  subdomain) on project_hosting; no write (`00015:284`, `00022:178`, `00023`).
- MATCH Nothing is granted to PUBLIC: `REVOKE ALL ... FROM PUBLIC` on every rebuilt table and every function.

One-login note (owner ruling, not counted here): no migration and no line of `bootstrap-roles.sql` creates a login
role (all nine roles are NOLOGIN, `bootstrap-roles.sql:36-116`), but three final guards still need separate logins,
because they refuse a login that is a member of two groups: `release_steps_insert_shape` `00015:167` and
`release_steps_update_shape` `00022:494` refuse a build step by a member of `provisioning_worker` (not superuser),
and `project_hosting_insert_shape` `00020:249` refuses the intake's version-0 insert by a member of
`provisioning_hosting_intake`. One login `provisioning` that is a member of every group fails both paths.

## 5. Notion's global rules

- MATCH No column defaults: no `DEFAULT`, `SERIAL`, `IDENTITY` or `GENERATED` in any migration; `00025` adds none.
- MATCH Every FK blocks deletes: the six final FKs are ON DELETE RESTRICT (`00002:89`, `00016:91`, `00022:141`,
  `00022:223`, `00022:286`, `00022:334-337`); no CASCADE (`00025` uses none, `test_schema_00025.py` asserts it).
- MATCH Command numbers and repository IDs fit 2^53-1: `00022:224`, `00022:142`, `00022:338`, `00010:339-342`.
- MATCH No application role may delete rows: no DELETE or TRUNCATE grant in any migration.
- MATCH Status and kind types are native lowercase enums (section 2).
- MATCH Service references have no FK: project_id and generation_request_id reference nothing outside the database.

## 6. In the database but not in Notion

Final index set after `00025` (besides the seven primary keys): `releases_project_release_key`,
`releases_project_request_key`, `releases_project_request_release_key`, `releases_one_live_per_project`,
`releases_one_promoting_per_project`, `release_steps_attempt_key`, `release_steps_one_active_attempt`,
`commands_command_number_key`, `commands_one_in_flight`, `commands_idempotency_key`, `commands_hosting_event_key`,
`usage_outbox_event_key_key`. Each holds a rule of section 3 or a Notion UNIQUE.

- MATCH Extra tables, columns, views, sequences, schemas or extensions: none.
- MATCH Index `release_steps_running_deadline` (`00002:102`): dropped by `00025:35`. Not in the final database.
- MATCH Index `command_dispatch_due` (`00004:74`, renamed `00011:149`): dropped by `00025:36`.
- MATCH Index `source_published_events_due` (`00022:362`): dropped by `00025:37`.
- MATCH Index `usage_outbox_due` (`00016:96`): dropped by `00025:38`.
- MATCH Unique index `commands_domain_event_key` (`00015:68`, re-created `00022:271`): dropped by `00025:34`. Notion's
  two key classes remain (section 3.5).
- ACCEPTED-BY-DECISION Unique keys `releases_project_release_key` (`00002:29`) and
  `releases_project_request_release_key` (`00007:34`): targets of the composite FKs that hold "the release of this
  project" (usage_outbox, the inbox's created release). `0023` G20.
- ACCEPTED-BY-DECISION Unique `release_steps_attempt_key` (`00002:91`): a retry is "a new row with the next attempt
  number". `0023` G20.
- ACCEPTED-BY-DECISION Code format `^[a-z][a-z0-9_]{0,99}$` on outcome_code (`00022:344`),
  command_dispatch.last_failure_code (`00004:66`) and usage_outbox.last_failure_code (`00016:86`). `0023` G20.
- ACCEPTED-BY-DECISION Not-empty checks on correlation_id (`00004:64`), claimed_by_worker (`00004:63`) and
  last_manual_retry_by (`00022:356`). `0023` G20.
- ACCEPTED-BY-DECISION Counter checks (attempts, publish_attempts, execution_attempts, claim_number,
  mismatched_repeat_count at least 0; max_attempts at least 1): `00004:58-60`, `00022:342-343`, `00022:352`,
  `00016:84`. `0023` G20.
- ACCEPTED-BY-DECISION Upper bound 2^53-1 on owner_status_version (`00022:152`): `0021` section 2.
- ACCEPTED-BY-DECISION Guard functions and triggers (`*_insert_shape`, `*_identity_immutable`,
  `*_terminal_status_final`, `*_update_shape`, `*_monotonic`, `releases_build_transition`,
  `project_hosting_generation_step`, `project_hosting_generation_has_command`, `project_hosting_reasons_guard`): each
  enforces a rule counted in section 3. Caller's rights, fixed search_path, no EXECUTE for PUBLIC. `0023` G20.
- ACCEPTED-BY-DECISION Function `offline_reasons_distinct` (`00018:83`): enforces "no duplicates". `0023` G20.
- ACCEPTED-BY-DECISION Role `provisioning_migrator`: owns every object and runs the migrations (`00025:24-31` refuses
  any other role); not an application role. `0021` section 2.
- ACCEPTED-BY-DECISION Role `provisioning_payment_intake` (`bootstrap-roles.sql:96`): holds no privilege, kept so
  `00010` runs on an empty server, dropped by `scripts/db/drop-retired-roles.sql`. `0021` D2.

## 7. DIFFERENT findings, most important first

None. The five DIFFERENT findings of the `cqrs-layout` audit (section 7 there) were all extra indexes, and
`00025:34-38` drops exactly those five with plain `DROP INDEX` statements, schema-qualified to the schema each index
lives in (`provisioning` for the two on `commands` and `release_steps`, `provisioning_ops` for the three on the ops
tables). `00025` adds, alters, grants or revokes nothing else, has no Down section, and runs only as the migrator.
`test_schema_00025.py` checks the same facts against PostgreSQL 18 (not run by me).

## 8. Notes for the lead (gaps Notion does not settle, and small doc points)

1. release_steps: Notion says "the next attempt only after a failed one" but also returns an idle candidate to
   `built` for a new deploy. The database follows the releases section (`0011` section 12). Unchanged.
2. Database Design says `commands.idempotency_key` is "unique per website"; 5 · Provisioning says "unique per project
   within their class". The database follows 5 · Provisioning. Unchanged.
3. The API's diagnostic read (`00022:382`) includes `release_id` and `mismatched_repeat_count`; read as outcome and
   attempt data (MATCH). A stricter reading would revoke them. Unchanged.
4. `provisioning_hosting_intake` holds SELECT on `release_steps` (`00013:14`); I found no statement of it that reads
   that table. A stricter reading of "no extra grant" would revoke it. Unchanged.
5. `project_hosting_subdomain_check` accepts a lower-case DNS label; Project's database has no matching check.
6. One login: see the note at the end of section 4. When the owner's single `provisioning` login lands, the three
   membership-exclusive guards (`00015:167`, `00022:494`, `00020:249`) need a new migration, and the Notion role
   design change has to say how a build step and a first hosting row are told apart. Also, with one login that is a
   member of `provisioning_intake` and holds INSERT on commands, the database would no longer stop a `sync_domains`
   insert, which now has no key class; only the code refusal in `statements.py` would remain.
7. The change note `.claude/docs/changes/db-00025.md` line 8 says "`schema.py` follows". `schema.py` declares no
   index, so nothing follows there and the branch leaves it unchanged; the line could say so.
8. `tests/integration/test_schema_00014.py:582` reads `database.migrate(15)  # 00025 drops the index again`; the
   pin to 15 is right, but the comment reads as if 00025 runs there.

PLANNED: 0 missing; every item Notion marks planned is applied and counted as MATCH. This is a static reading: no
database was migrated and `make py-test-integration` was not run.

Result: 0 DIFFERENT, 11 ACCEPTED-BY-DECISION, 127 MATCH
