# 0021: Provisioning follows the Notion design, Phase A

Status: Accepted; implemented (Tasks 1 to 11) and merged into `python`. Kind results: decision `0022` section 5.

## 1. Context

The owner's Notion design is now the source for Provisioning. Two pages matter:

- "5 · Provisioning" (https://app.notion.com/p/40058175966e81769596d1ebcadb333d): the sections "Planned, not yet
  applied: Migration 00018" and "The next Provisioning migration", and the clarifications.
- "Web Block architecture" (https://app.notion.com/p/40358175966e8198a088f123a9ab23fe): sections 3.8 and 4.3.

An audit compared the code with these pages. It is kept in git history
(`git show de31486:docs/notes/2026-10-24-provisioning-audit.md`). Phase A is the first slice of the work: the system
namespace, migration 00018 (offline reasons, owner status, repository rename) and the migration after it (admin suspend,
redeploy, sub-domain), with the code that reads and writes each new column.

Rule for this work: the Notion pages are read-only. The code and the database follow them. Nothing in this repository
proposes or makes a change to Notion. Where Notion is silent, the lead chooses inside what Notion already defines and
records the choice here.

The cluster repository already moved Provisioning to the namespace `webblock`. The tenant label is already
`websites.webblock.internal/tenant`, so it needs no change.

## 2. Global constraints

- No column defaults. The application supplies every value. No `DEFAULT` in any new migration.
- Every foreign key blocks deletes (`ON DELETE RESTRICT`). No `CASCADE`.
- Command numbers, repository IDs and `owner_status_version` fit in 0 to 2^53-1 (`9007199254740991`), so they are safe
  in JSON.
- No application role may delete rows. No `GRANT ... DELETE`.
- Roles: `provisioning_api` (client commands; from 00020 also `redeploy` and hosting commands with `admin_suspended`),
  `provisioning_worker` (runs commands, routing, hosting, `usage_outbox`), `provisioning_hosting_intake` (suspend and
  resume from events, and their `command_dispatch` rows; from 00018 also inserts owner-status hosting rows),
  `provisioning_intake` and `provisioning_build` (build worker), `provisioning_operator` (inbox manual retry; from 00020
  the sub-domain backfill), `provisioning_bootstrap` (reads only).
- Migrations run only as `provisioning_migrator`, are forward-only and immutable, have no Down section, and run in one
  transaction (goose). A mistake is fixed with a new migration.
- Every migration starts with the two guards of `00017_revoke_intake_command_grants.sql` (lines 28 to 56): the current
  role check and the missing-role check.
- Every guard raises SQLSTATE `23514` with `SCHEMA`, `TABLE`, `CONSTRAINT` and a `DETAIL` sentence, in the style of
  `00011_rename_for_clarity.sql` (lines 355 to 380). Every new function is `SECURITY INVOKER`, has
  `SET search_path = pg_catalog`, and runs `REVOKE ALL ... FROM PUBLIC`.
- `infrastructure/postgres/schema.py` mirrors the migrated schema exactly. `tests/integration/test_schema_mirror.py`
  fails on drift.
- The idempotency key fixes the reason: `project-deleted` gives `project_deleted`, `owner-status:<version>` gives
  `user_suspended`, a Payment event key gives `billing`, `admin:<key>` gives `admin_suspended`. Each may carry
  `.retry-<n>`. A resume never names `project_deleted`.
- Keys are unique per project within their class (client commands; hosting commands). The request fingerprint is
  unchanged (`provisioning.command.v1`, kind, project, release or key). `hosting_reason` is not part of it.
- Unit tests live under `tests/unit/` mirroring the package and import only public names. No `__init__.py` under
  `tests/`.
- The dependency suites fail, never skip, when Docker or the stack is missing. One Testcontainers or Kind stack at a
  time.
- The real cluster is never touched. Kind uses only the project-local kubeconfig.
- Docs and reports use plain, easy English, are short, and never use an em dash or an en dash.
- The decision record changes before a rule changes in code.
- Every DB step ships its change report (changed, not changed, UI impact) next to the repository.
- The code always follows Notion. No task proposes or performs a Notion edit.

## 3. Decisions D1 to D10

D1, D2 and D6 are implementation details: the end state matches Notion exactly. The others choose where Notion is
silent, inside what Notion already defines.

- **D1 (implementation detail).** Notion's next migration is delivered as three files: 00019 (enum values), 00020
  (everything that uses them, and the nullable sub-domain) and 00021 (NOT NULL). After 00021 the schema is exactly
  Notion's next migration. Why three: PostgreSQL refuses a new enum value in the transaction that added it, and Notion's
  own order is nullable, backfill, then NOT NULL.
- **D2 (implementation detail).** The end state is Notion's: `provisioning_payment_intake` no longer exists. Roles are
  cluster-wide and `provisioning_migrator` has no `CREATEROLE`, so an administrator script does the drop. Migration
  00018 asserts the role holds no privilege and no membership in this database. The new script
  `scripts/db/drop-retired-roles.sql` drops it after `make migrate-up` has passed 00018.
  `scripts/db/bootstrap-roles.sql` keeps creating it, because `00010` refuses to run without it on an empty server.
- **D3.** Wire names stay `github_repository_id` in Phase A (REST `openapi.yaml`, MCP `tools.json`, the prototype event
  schemas). Only the DB columns, the Python names and the docs change. The REST rename comes with the camelCase release
  (audit step 11) and the event rename with the v1 events (audit step 4), so clients break once, not twice. The key
  `github_repository_id` of the prototype stand-in repositories file (`infrastructure/standins/repositories.py`) is a
  wire name too; it is renamed with the D8 change of that file's format in Task 9.
- **D4.** Backfill of `offline_reasons` for existing rows: online rows get `{}`. An offline row with a `suspend_hosting`
  whose root key is `project-deleted` and whose status is `succeeded` or `skipped` gets `{project_deleted}`. Every other
  offline row gets `{billing}` (today a site goes offline only for those two reasons). Backfill of
  `commands.hosting_reason` follows the key rule in section 2.
- **D5.** Notion names no code for this case, so only its existing skip codes are used. A suspend whose reason is
  already present ends `skipped` with `hosting_already_offline`. A resume whose reason is absent ends `skipped` with
  `hosting_already_online`, even when the site stays offline for another reason. `entitlement_restored` and
  `entitlement_ended` stay as today. There is no new failure code. Limitation: in that edge case the command's code
  reads "already online" while `hosting_status` and `offline_reasons` show the true state (offline, and why).
- **D6 (implementation detail).** "Never routed" is a hosting row with `live_release_id IS NULL`. Notion: a resume on
  such a row only clears its reason. A suspend on such a row likewise only records its reason (and the hosting status
  that follows from the reasons) and never calls the cluster. There is no live route to take down, and an owner-status
  row may have no tenant namespace. The stored state is the one Notion defines.
- **D7.** A suspend that adds a reason to a site already offline, and a resume that removes one reason while another
  remains, end `succeeded` without a route change (`record_hosting_reason`).
- **D8.** The sub-domain comes from the repository lookup (`RepositoryRegistry`), which now answers
  `RegisteredRepository(repository_id, subdomain)`. The prototype event does not carry it (D3). The backfill of existing
  rows is an operator CLI command with values the owner takes from Project.
- **D9.** The owner-status consumer (RabbitMQ, v1 envelope) is audit step 6, Phase B. Phase A builds and tests the use
  case `OwnerStatusIntake.accept_owner_status` and its store. Nothing calls it from the broker yet.
- **D10.** `redeploy` is in the enums and the DB in Phase A, but the API refuses to accept it and the worker refuses to
  run it (left out of `PROTOTYPE_KINDS`, like `sync_domains`) until Phase C builds the admin endpoint and the executor.
- **Column order (implementation detail).** The physical column order differs from Notion's field lists:
  `project_hosting.subdomain`, `offline_reasons`, `owner_status_version` and `commands.hosting_reason` are appended by
  ADD COLUMN in 00018 and 00020. Matching Notion's order would need a table rebuild. This is accepted as an
  implementation detail, since nothing depends on physical order.

## 4. Consequences

- Task 1 renames the system namespace from `webblock-system` to `webblock`: the worker identity is
  `system:serviceaccount:webblock:provisioning-worker`, the bootstrap identity
  `system:serviceaccount:webblock:tenant-bootstrap`, and the policy ConfigMap `webblock/webblock-site-policy`. Tenant
  namespaces `wb-<projectId>`, `webblock-gateway` and `webblock-offline` keep their names. Decision records older than
  0021 and `ai/` keep `webblock-system` as history.
- Wire names are unchanged until audit steps 4 and 11 (D3). Hostnames are unchanged until audit step 13: routing keeps
  `<projectId>.<site domain>`, and Phase A only stores the sub-domain.
- Notion lets a `user_suspended` command (and its retry) run only while its version equals `owner_status_version`. The
  acceptance checks this on the locked hosting row, not only when the retry reads its history, so a newer owner status
  that lands in between still stops an old retry. The refusal is `OwnerStatusOutdated`; only the hosting intake sends
  owner-status keys, and the retry treats it as not needed.
- Enum order (lead ruling, Task 8): the order in which Notion lists an enum's values is the enum order. So 00019 adds
  `redeploy` before `sync_domains` (`ALTER TYPE ... ADD VALUE 'redeploy' BEFORE 'sync_domains'`) and appends
  `admin_suspended`; the Python enums and the wire enums follow the same order.
- Migration 00020 (Task 9). The API store refuses `redeploy` and every hosting command whose key starts with `admin:`
  before any SQL (`statements.command_by_key`), until Phase C. The hosting intake never sends such a key; a Payment
  event key that starts with `admin:` is refused the same way, because the key rule would make it `admin_suspended`. The
  operator gets `USAGE` on schema `provisioning`, which its column grants for the backfill need. Phase C's redeploy
  executor must not insert a `usage_outbox` row: Payment does not count a redeploy as a publish.
- Migration 00020 first refuses, with SQLSTATE `23514` and a message that names the cause, a database that already holds
  a hosting command whose key starts with `admin:` or any `redeploy` command. The code cannot write either before 00020,
  so a database that holds one was changed by hand; an administrator corrects it, then runs the migration again. 00020
  had not left this branch, so the check was added to the file itself (Task 9 review).
- The backfill grants are temporary (lead ruling, Task 10 review): Notion gives `provisioning_operator` only the inbox
  manual retry, so 00021 revokes everything 00020 gave it (the column update and read on `project_hosting`, `EXECUTE` on
  `offline_reasons_distinct`, `USAGE` on schema `provisioning`). After 00021 the operator holds exactly its 00019
  rights, and the `subdomain` commands are refused (42501).
- The prototype repository file (Task 9, D3 and D8): each entry is `repository_id`, `project_id`, `subdomain` and
  `git_url`. A sub-domain is a DNS label and appears once in the file. A file with the old key `github_repository_id` is
  refused, not read in a compatible way.
- Two limits are accepted on purpose: the D5 wording of a skipped resume, and the D9 gap (a use case with no broker
  caller yet).
- Existing databases keep their rows. Migration 00018 and the migrations after it carry tests that upgrade from 00017
  with representative rows.

## 5. What was built and tested

- The namespace `webblock` (Task 1); migrations `00018` to `00021` with the code that reads and writes every new column
  (Tasks 2 to 10); the operator command `subdomain missing` and `subdomain set` for the backfill between `00020` and
  `00021` (Task 10). The change reports are `platform-db-rev25-provisioning-00018-report.md` and
  `platform-db-rev25-provisioning-00019-report.md` next to the repository (the second covers `00019`, `00020` and
  `00021`).
- The five risks of the plan's Review Focus each have a test that passes:
  1. Existing rows during the rename and the backfill: `test_upgrade_from_00017_keeps_rows_and_backfills_reasons`
     (`tests/integration/test_schema_00018.py`).
  2. Out-of-order owner-status events: `test_older_owner_status_changes_nothing` (unit) and
     `test_owner_status_version_never_decreases` (integration).
  3. A billing resume while `user_suspended`: `test_billing_resume_keeps_site_offline_while_user_suspended` (unit) and
     `test_offline_exactly_when_reasons_non_empty` (integration).
  4. Admin keys against system keys: `test_admin_key_cannot_take_a_system_key` and
     `test_only_the_api_inserts_admin_reason` (`tests/integration/test_schema_00020.py`).
  5. NOT NULL sub-domain on existing rows: `test_00021_refuses_while_a_row_has_no_subdomain` (`test_schema_00021.py`)
     and `test_subdomain_is_set_once` (`test_schema_00020.py`).
- Also tested: `00020` refuses old `admin:` hosting keys and old `redeploy` rows before any change; after `00021` the
  operator holds exactly its `00019` rights (`test_the_operator_is_back_to_its_00019_rights`); the API refuses
  `redeploy` and `admin:` keys and leaves the rows unchanged.

Full run of Task 11 (at `279b35f` plus docs only):

| Gate | Result |
| --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | exit 0 (498 files formatted, ruff clean, mypy 493 files, layer contracts kept) |
| `make py-test` | 6262 passed |
| `make py-test-integration` | 487 passed, 241 deselected |
| `make py-test-messaging` | 20 passed |
| `make chart-check` | exit 0 (1 chart linted, 23 chart tests passed) |
| `make py-vuln` | first failed with 11 new advisories (aiohttp 3.14.3: 8, certifi 2026.7.22: 2, opentelemetry-api 1.45.0: 1). Fixed: `exclude-newer` moved to 2026-10-10 (14 days back) and only aiohttp 3.14.3 to 3.14.4 (uploaded 2026-10-05) and opentelemetry-api 1.45.0 to 1.45.1 (uploaded 2026-10-06) moved in `uv.lock`. The certifi fix (2026.10.19, uploaded 2026-10-20) is not 14 days old, so both certifi ids have an exception in `scripts/py/vuln-exceptions.toml` until 2026-11-03, the day the fix becomes eligible (the owner signed it, commit 3fa8290). Now exit 0: 84 packages scanned, 2 findings, both under a current exception |
| `make proto-test-kind`, `make proto-e2e` | not run (they need the single Kind slot and about 7000 MB free memory) |

## 6. What stays open

- Before the merge, the owner runs, in its own slot with about 7000 MB free memory:
  `make proto-down; make proto-up; make proto-test-kind; make proto-e2e`. These cover the namespace `webblock`, the new
  repository file format (D8) and the migrations on the Kind stack. Until then they are not run, never passed.
- `make py-vuln` (section 5): the owner signs the certifi exception, and on 2026-11-03 the lead locks a fixed certifi
  that is 14 days old and deletes the two entries of `scripts/py/vuln-exceptions.toml`.
- An existing prototype stack database with hosting rows from before `00020` stops at `00021` until it is backfilled or
  recreated (`make proto-down`).
- Phase B: the owner-status consumer (D9) and the v1 events (wire rename, D3). Phase C: the admin endpoints for suspend
  website, resume website and redeploy, and the redeploy executor (D10). Later: the hostname cut-over to
  `<subdomain>.<site domain>` (audit step 13) and the REST camelCase release (audit step 11).
- After `00018` an administrator drops `provisioning_payment_intake` with `scripts/db/drop-retired-roles.sql` (D2).
- The two accepted limits: the D5 wording of a skipped resume, and the D9 gap. The D9 gap is closed by 0022 Task 5: the
  worker consumes the owner-status event.
