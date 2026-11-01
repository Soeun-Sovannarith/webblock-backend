# Review: go-live tries, Task 1 (domain rule and one copy of the try codes)

Stage: 5 Verify (task). IDs: FR-21, FR-21.2, FR-21.4, TR-57. Constitution focus: III.1, III.2, V.1.

Scope: `git diff 6b9d73f..4c7c19c` (commit 4c7c19c "Domain: go-live tries, waits and try codes in one rule (FR-21,
TR-57)") in the worktree `.claude/worktrees/go-live-tries`, checked against plan Task 1
(`.claude/docs/plans/go-live-tries.md`), design G3 (`.claude/docs/designs/go-live-tries-design.md`), decision 0027
T1, FRD rows FR-21, FR-21.2, FR-21.4 and TRD row TR-57. The reviewer did not write this code.

## Verdict

Approved. No critical or important finding. Two minor notes below; neither needs a fix round.

## What the diff does

Seven files. All are Task 1 files; `tests/unit/core/domain/test_kinds.py` was added by the lead's decision.
Nothing outside these files changed (`git show --stat 4c7c19c`).

- `src/provisioning/core/domain/rules/go_live.py` (new): `GO_LIVE_TRIES = 3`, the four try codes,
  `GO_LIVE_TRY_CODES` (the four plus `CODE_DEPENDENCY_UNAVAILABLE`) and `wait_after_failed_try`.
- `src/provisioning/core/domain/__init__.py`: imports and exports the 7 new names. The plan says "six"; it
  miscounts, and all 7 are exported.
- `src/provisioning/core/application/routing/background/routing.py`: the 4 constants removed (old lines 94 to 97)
  and imported from `provisioning.core.domain`.
- `tests/unit/core/application/routing/background/test_routing.py`: imports the 4 codes from `core.domain`.
- `tests/integration/kubernetes/test_routing_live.py`: imports `CODE_CANDIDATE_NOT_ANSWERING` from `core.domain`.
- `tests/unit/core/domain/rules/test_go_live.py` (new): 7 test functions, 9 cases.
- `tests/unit/core/domain/test_kinds.py`: `go_live` listed under `rules`, so the kind folder check knows it.

## Constitution check

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | Commit cites FR-21 and TR-57; matches plan Task 1, G3 and 0027 T1; no Drift |
| II Tech stack | pass | No dependency, image or tool change; `uv.lock` untouched |
| III Architecture limits | pass | See the notes below the table |
| IV Coding conventions | pass | `mypy --strict` and ruff clean; no `type: ignore` or `noqa`; `Final` on constants |
| V Testing and quality | pass | Every new test names FR-21.2, FR-21.4 or TR-57; fast gates rerun below |
| VI Safety guardrails | pass | No cluster command, no secret, no push; Notion fetched read-only |
| VII Docs | not applicable | No doc changed; docstrings are plain English, no dashes, lines within 120 |
| VIII Agents | pass | Independent reviewer; this report is the only file written |

Article III notes:

- III.1: the new module sits in `core/domain/rules/` as decision 0025 sets, and is exported from `core.domain`.
- III.2: `go_live.py` imports only `__future__`, `datetime`, `typing` and
  `provisioning.core.domain.values.deploy_failures` (checked with `ast`). import-linter: 16 kept, 0 broken,
  including "rules above values" (0025 CQ3).
- III.3: no schema, enum, role or grant change. Notion "5 · Provisioning" (fetched read-only) holds `failure_code`
  as `VARCHAR(100)`; the code strings are unchanged and `values/failure_codes.py` is untouched.
- III.4 to III.8: not touched.

V.1 note: the tests import names that did not exist at 6b9d73f, so they failed first on import. This is inferred
from the base tree, not rerun there.

## Requirement and test table

| ID | Test | Status |
| --- | --- | --- |
| FR-21 (3 tries) | `test_tr57_three_tries` | done |
| FR-21.2 (30 s after try 1) | `test_fr21_2_the_wait_after_try_1_is_30_s` | done for the rule |
| FR-21.4 (60 s after try 2) | `test_fr21_4_the_wait_after_try_2_is_60_s` | done for the rule |
| TR-57 (no wait after the last) | `test_tr57_no_wait_after_the_last_try` (3, 4) | done |
| TR-57 (refusal below 1) | `test_tr57_a_try_number_below_1_is_refused` (0, -1) | done |
| TR-57 (one set of try codes) | `test_tr57_the_try_codes_are_five_release_and_command_codes` | done |
| TR-57 (strings kept) | `test_tr57_the_try_codes_keep_their_values` | done |
| TR-2 (kind folders) | existing tests in `test_kinds.py` | done |

Evidence per row:

- FR-21: `GO_LIVE_TRIES == 3` (`go_live.py:17`).
- FR-21.2 and FR-21.4: `wait_after_failed_try(1)` is 30 s and `(2)` is 60 s (`go_live.py:36`, `:46`). The rest
  of each row (no claim before the wait, route back on L, release `promoting`) belongs to Tasks 2, 3 and 5 in the
  plan, so "done for the rule" is the right status for Task 1.
- No wait after the last: `None` for 3 and 4 (`go_live.py:44-45`).
- Refusal: `ValueError` matching "try number" for 0 and -1 (`go_live.py:42-43`).
- Try codes: the set equals the four constants plus `dependency_unavailable`; each code is in the catalogue with
  stage `promote` and places release and command.
- Strings kept: kept by the lead's decision; it pins the strings that rows and audits already hold.
- TR-2: `test_tr2_the_listed_modules_are_the_files_of_each_kind_folder` and
  `test_tr2_rule_classes_are_rule_inputs_or_results` pass with `go_live` listed.

Design G3 and decision 0027 T1 match point by point:

- The numbers are code constants, not settings.
- The try codes exist once (`go_live.py:20-23`). No copy of the four strings stays in `src/` apart from the
  failure code catalogue (`values/failure_codes.py:227`, `:234`, `:241`, `:256`), which is the catalogue itself.
- `routing.py` imports them from `core.domain`. No other module imported the four names from `routing.py`
  (`hosting_job.py`, `reconcile.py` and `recovery.py` import other names only), so no import broke.
- `routing.py` behaviour is unchanged: the names are bound to the same strings, only their home moved. Its unit
  suite passes with only its import lines changed.

## Commands and outcomes

Run from the worktree root with `CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts`.

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check` | passed | 633 files already formatted |
| `make py-lint` | passed | All checks passed |
| `make py-typecheck` | passed | No issues in 627 source files, the Kind test file included |
| `make py-layers` | passed | 16 contracts kept, 0 broken |
| `make py-test` | passed 7152 | 146.57 s, exit 0 |
| pytest on `test_go_live.py` and `test_kinds.py` | passed 39 | `-m unit`, `-X dev` |
| `make spec-check` | not run | Refused by the reviewer hook |
| `make contracts-check chart-check` | not run | Not in the brief; no contract or chart file changed |
| `make py-test-integration py-test-messaging` | not run | No schema, store, SQL or broker change |
| Kind suites | not run | Only an import changed; Task 10 runs it through kind-runner |

## Findings

Critical: none.

Important: none.

Minor:

1. `src/provisioning/core/domain/rules/go_live.py:36` and `:46`: `_WAITS` must have exactly `GO_LIVE_TRIES - 1`
   entries, and nothing checks it. If someone raised `GO_LIVE_TRIES` to 4 without a third wait,
   `wait_after_failed_try(3)` would raise `IndexError` instead of giving a wait. FR-21 fixes both numbers today,
   so this guards a later change; it is not a defect now. Suggestion: one TR-57 test that
   `wait_after_failed_try(n)` is a `timedelta` for every `n` from 1 to `GO_LIVE_TRIES - 1`. No fix round needed.
2. `src/provisioning/core/application/routing/background/routing.py:21-24` and `:413-415`: the docstrings still
   say a failed preview fails the command at once with `candidate_not_answering` or `route_refused`. That is
   still true of the code after Task 1. Design G4 and G5 and plan Task 5 change the code and these docstrings.
   Recorded so the Task 5 review checks them; nothing to fix in Task 1.

No security issue. The change does not touch auth, RBAC, policies, secrets or dependencies, so security-reviewer
is not needed for this task.

## Skipped checks and blockers

- `make spec-check`: not run (reviewer hook refusal). The lead should run it and confirm the 7 expected problems
  are only the unlinked new tests that Task 9 links.
- Tests failing first: not rerun on the base commit; inferred from the base tree, where `rules/go_live.py` and the
  new `core.domain` exports do not exist.
- No blocker.
