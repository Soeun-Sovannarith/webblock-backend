# Review: migration 00025 (db-00025)

Stage: 5 Verify (lean workflow, the one independent review).
IDs: TR-12 (the schema equals Notion S1 exactly, serving FR-9 to FR-42); constitution III.3, V.1;
`.claude/rules/migrations.md`.
Range: `git diff 39add4d..HEAD` on branch `db-00025` (commits `85bbb76`, `cc92056`), worktree
`.claude/worktrees/db-00025`. The reviewer hook allows reports only under the main checkout's `ai/reviews/`, so
this file is there and not in the worktree.
Reviewer: validation agent. I wrote none of this change.

## Verdict

Needs fixes, docs only. The migration, its tests and the code are correct and the gates pass. One owner rule
(the separate change report) is not met and three doc lines are stale or wrong. No code change is needed.

Fix list:

1. I1: write the separate change report, or record the owner's waiver for the lean lane.
2. M1: correct the `schema.py` line of the change note.
3. M3: update the `new-migration` skill and the handoff to "next file is 00026".
4. M2 (lead's call): the HINT points at a path that does not exist.

## Constitution check

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | Both commits cite TR-12 and FR-42; TRD row TR-12 (`TRD.md:75`) serves FR-9 to FR-42. Change note present. |
| II Tech stack | not applicable | No dependency, image or tool change. |
| III Architecture | pass | III.3: the migration drops exactly the five extra indexes (test and diff below). The Notion page, fetched read-only, names two key classes, marks `sync_domains` "In the enum, unused", "Started by: Never", and names none of the five indexes. The parallel `db-notion-audit-db-00025.md` reports 0 DIFFERENT. III.4: no store relies on an index for correctness. |
| IV Coding conventions | pass | Only a docstring changed in `src/`; ruff and mypy --strict pass. |
| V Testing and quality | pass, one note | V.1: every new test names TR-12. Red first is not provable from git (test and migration share commit `85bbb76`), but each test fails without the file by construction (`APPLIED == 25`, the five present at 23). V.2: gates rerun below. |
| VI Safety guardrails | pass | No cluster command, no secret, no push. The migration runs only as the migrator. |
| VII Docs | pass, see M1 and M3 | No em or en dash in new lines. The only changed line over 120 characters is a table row in `0004` that was already that long. The decision notes state current facts. |
| VIII Agents | pass | Reviewed by an agent that did not write it; report only. |

## Requirement table

| ID | Tests | Status | Evidence |
| --- | --- | --- | --- |
| TR-12 (no extra index) | `test_tr12_the_five_extra_indexes_are_gone_after_migrate_up` | done | `tests/integration/test_schema_00025.py:62` |
| TR-12 (drops exactly five, adds none) | `test_tr12_00025_drops_exactly_the_five` | done | `:69`; compares full `pg_indexes` with definitions before and after |
| TR-12 (Notion rule indexes kept) | `test_tr12_the_indexes_notion_rules_need_still_exist` | done | `:84`; the six unique indexes Notion's rules need |
| TR-12 (nothing else changes) | `test_tr12_00025_changes_nothing_else` | done | `:91`; constraints, flags, triggers, tables, types, functions, every grant class; non-empty checks guard against vacuous passes |
| TR-12 (rows kept) | `test_tr12_upgrade_from_00023_keeps_rows` | done | `:107`; every table seeded, non-empty asserted |
| TR-12 (forward-only, only drops) | `test_tr12_00025_has_no_down_section_and_drops_only_indexes` | done | `:122`; no Down, no CASCADE or IF EXISTS, exactly five `DROP INDEX` |
| TR-12 (migrator only) | `test_tr12_00025_refuses_other_roles` | done | `:136`; negative path: exit not 0, message, `42501`, still at 23, five still present |

No Drift. No Orphan: the only `src/` change is the docstring at `statements.py:50` to `:56`, which states the
new fact.

## Checks of the brief

- **Forward-only goose SQL, like the repo's migrations.** `-- +goose Up`, no Down, `StatementBegin` and
  `StatementEnd` around the `DO` block, header says what does not change. Plain `DROP INDEX`, no `CASCADE`, no
  `IF EXISTS`, so a missing index fails the whole transaction. Pass.
- **Drops exactly the five and nothing else.** `00025_drop_extra_indexes.sql:34` to `:38`; proven by the two
  structure tests. Pass.
- **No code relies on them.** A grep over the repo (outside `ai/`) finds the five names only in older migrations,
  decision records, `_catalog_00011.py` (used only at version 11 by `test_schema_00011.py`) and
  `test_schema_00014.py`. No store names an index. `command_by_key` refuses `sync_domains` before any SQL
  (`statements.py:57` to `:59`), so nothing depends on `commands_domain_event_key`. The `sync_domains` duplicate
  test at `test_schema_00014.py:345` runs at version 16, before 00025. Pass.
- **Lead ruling: numbering test.** `tests/unit/migrations/test_intake_revoke_migration.py:44` to `:45`. Not
  weakened. I ran the two assertions on synthetic lists: head `1..23, 25` and `.., 25, 26` pass; a file numbered
  24, a gap at 10, a gap at 26, a duplicate 25, a tree ending at 23 and a tree ending at 24 all fail. The old
  assertion would have failed on any tree after the 00024 revert, so the change is required, and it allows only
  the one recorded gap.
- **Lead ruling: `test_schema_00014.py:582`.** `migrate(15)` now proves that 00015 creates the index once the
  bootstrap role is back. Before, `migrate()` to head proved the same end state only because 00022 rebuilt the
  index. Pinning 15 is the narrower and stronger proof. Pass.
- **Lead ruling: one migrator guard only.** The rule "stops before any change when a role it needs is missing"
  applies to roles the migration names; 00025 names, grants and revokes none. Precedent: 00001, 00004 to 00007,
  00009, 00012 to 00014 have no missing-role guard. Accepted.
- **Lead ruling: HINT.** Message `migrations must run as role provisioning_migrator (current role: %)`, SQLSTATE
  `42501`, the pointer moved to `HINT`. The test asserts the message and `42501`. Accepted, but see M2 on the path.
- **Lead ruling: `statements.py` docstring.** True after 00025: `sync_domains` has no key class and is refused
  before SQL. Pass.
- **Docs 0004, 0005, 0008, 0014.** Each note states the drop and the reason as a current fact, in the style the
  records already use for amendments. 0014 section 23 now says the index is dropped. Pass.

## Commands and outcomes

Run in the worktree. `free -m` showed 9839 MB available; `docker ps` showed no Kind stack (one unrelated
container). One heavy suite at a time.

| Command | Result | Notes |
| --- | --- | --- |
| `CONTRACTS_REPO=... make py-fmt-check py-lint py-typecheck py-layers` | passed | 632 files formatted; ruff all checks passed; mypy no issues in 626 files; import-linter 16 kept, 0 broken |
| `CONTRACTS_REPO=... make py-test` | passed 7142 | 134.68 s |
| `make py-test-integration` | passed | exit 0; 566 tests selected (collect-only count); output was not kept, `lastfailed` empty |
| `pytest -m integration tests/integration/test_schema_00025.py tests/integration/test_schema_00014.py` | passed 46 | rerun with visible output for the touched files |
| `make spec-check` | not run | not asked in the brief |
| `make contracts-check chart-check` | not run | no contract or chart change |
| `make py-test-messaging`, Kind suites | not run | no broker or cluster change |
| `make py-vuln py-age image-scan` | not run | no dependency change |
| Notion audit | not run here | `db-notion-auditor` ran in parallel; its report says 0 DIFFERENT. I fetched the page read-only only to confirm the quotes in the migration header. |

## Findings

### Critical

None.

### Important

- **I1: no separate change report.** `.claude/rules/migrations.md` (owner rule): "Every schema change ships with
  a test in `tests/integration/`, a matching `schema.py`, and a separate change report (changed, not changed, UI
  impact)". The `new-migration` skill places it at `../platform-db-rev25-provisioning-00025-report.md`. No such
  file exists (00018, 00019, 00022, 00023 and 00024 have one). `.claude/docs/changes/db-00025.md` has no "Not
  changed" or "UI impact" part. Fix: write the report, or record that the owner waived it for this lane.

### Minor

- **M1: the change note says `schema.py` follows.** `.claude/docs/changes/db-00025.md:9`. `schema.py` declares no
  index and is not in the diff, so nothing follows. Say "`schema.py` holds no index, so it does not change".
- **M2: the HINT names a path that does not exist.** `src/migrations/00025_drop_extra_indexes.sql:28`:
  `docs/runbooks/phase-1-database.md`; the runbook is `.claude/docs/runbooks/phase-1-database.md`. Older
  migrations carry the same stale path and cannot change. This file is not merged, so the lead may correct it now
  or accept it. If corrected, keep `42501`; the test checks only the message and the code.
- **M3: stale "next migration" facts.** `.claude/skills/new-migration/SKILL.md:26` to `:28` still says the last
  file is `00023` and the next is `00025`. `ai/handoffs/after-phase-c.md:20` says "The next migration file is
  `00025`". Both are now wrong; the next is `00026`.

### Notes (no action)

- Red first cannot be shown from history, because the tests and the migration are one commit. Each new test fails
  without the file by construction, so V.1 holds in substance.
- Dropping the due indexes turns the dispatcher, inbox, outbox and sweeper scans into sequential scans. This is
  the accepted risk in the change note, tied to retention (TR-16, OD-15).
- Security: no auth, RBAC, policy, secret or dependency change; the grant test proves every privilege is
  unchanged. `security-reviewer` is not needed.

## Skipped checks and blockers

Skipped: spec-check, contracts-check, chart-check, messaging, Kind, vulnerability and age scans, as the table
says. No blocker.
