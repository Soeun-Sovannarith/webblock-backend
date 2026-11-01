# Review: go-live tries, Task 3 (the claim carries the last failure code)

Stage: 5 Verify (task). IDs: FR-21.2, FR-21.5, TR-57 (design G12, review note N-5, decision 0027 T9).
Reviewed: `git diff 48133c8..f109a46` on branch `go-live-tries` (commit f109a46). Commit 45828d9 after it changes
only `.claude/docs/CONSTITUTION.md` and is out of scope. Also checked: the Task 2 carry overs M-1 and M-2.
Reviewer: provisioning-validator. I did not write this work.

## What the diff does

- `core/application/jobs/claims.py:114`: `Claim.last_failure_code: str | None = None`, the last field. The
  docstring names decision 0027 T9.
- `infrastructure/postgres/fenced.py:43, 213`: `DispatchRow.last_failure_code`, filled by `_dispatch_of` from the
  row of `lock_dispatch`. That statement is `select(command_dispatch, clock)`: it already reads every column.
- `infrastructure/postgres/job_store.py:343`: `claim` sets the field from the locked `dispatch`, read before the
  claim UPDATE. That UPDATE does not touch `last_failure_code`, so the value is the row's value at the claim.
- `job_store.py:383`: `extend_claim` copies it from the claim it extends (N-5).
- Three integration tests in `tests/integration/test_store_jobs.py:254, 276, 309`.
- M-1: `tests/unit/core/application/shared/test_error_table.py:119-124`, the comment wrap rejoined.
- M-2: `tests/unit/core/domain/values/test_failure_codes.py:220-221, 231`, the scanner self test gets
  `raise RetryLater("retry_code")` and expects `retry_code`.

No SQL text, migration, schema module, role or grant changed (the diff touches no `src/migrations`, `schema.py`
or `statements.py`, and no statement builder). The Notion page "5 · Provisioning" (fetched read only) types
`command_dispatch.last_failure_code` as VARCHAR(100) NULL; `str | None` matches.

## Constitution check

| Article | Result |
| --- | --- |
| I Spec-driven flow | pass |
| II Tech stack | not applicable |
| III Architecture limits | pass |
| IV Coding conventions | pass |
| V Testing and quality | pass |
| VI Safety guardrails | pass |
| VII Docs | pass |
| VIII Agents | pass |

- I: the commit cites FR-21 and TR-57; plan Task 3 names FR-21.2, FR-21.5, TR-57. The code follows G12 and T9
  word for word. No Drift, no Orphan.
- II: no dependency, tool or image change.
- III.1, III.2: the field lives in application, the read in infrastructure; `py-layers` 16 kept, 0 broken.
  III.3: no schema or SQL change; the type matches Notion. III.4: `claim` and `extend_claim` stay one short
  transaction each through `self._tx.run`, with no remote call inside. III.6: only the worker's store changes.
- IV: `mypy --strict` and ruff pass; no `type: ignore` or `noqa` added.
- V.1: every new test names its ID. Two tests fail first on the old code (`AttributeError` on
  `last_failure_code` of a slotted `Claim`). The BACKOFF test guards behaviour that already existed; see the V.1
  note below. V.2: I ran the fast tier and `py-test-integration`, both green.
- VI: no secret, no cluster call, no Notion edit, no push.
- VII: docstrings and comments are plain English, no em or en dash in the diff, lines within limits (ruff).
- VIII: one implementer; this review is independent.

## Requirement table

| ID | Test | Status |
| --- | --- | --- |
| FR-21.5 | `test_fr21_5_a_claim_reads_the_last_failure_code_of_its_row` | done |
| FR-21.2 | `test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try` | done |
| TR-57 (G12, N-5) | `test_tr57_extend_claim_keeps_the_last_failure_code` | done |
| TR-57 (T9), default None | the existing tests that build a `Claim` without the field | done |

Evidence:

- FR-21.5: claim 1 has None; release with `route_not_accepted` and 30 s; `retry_after` moved into the past
  through `db.admin()`; claim 2 is CLAIMED with `execution_attempt` 2 and `last_failure_code`
  `route_not_accepted`. Matches plan Task 3. See minor N-1 on the precondition.
- FR-21.2: after the release there is no holder and no expiry, `retry_after` is 25 to 30 s ahead,
  `next_publish_at` equals it and the code is kept; a claim is BACKOFF with no claim; `execution_attempts` 1,
  `claim_number` 1, command `running`. Matches the plan text.
- TR-57 (N-5): row seeded with `route_not_accepted`; the claim carries it; after `extend_claim` the expiry moved
  and the code is kept.
- TR-57 (T9): `make py-test` passed 7160 with no change to the 8 test files that build a `Claim`.

### V.1 note: the BACKOFF test passed before the code

The implementer reports that `test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try` passed before
the change. I agree that it must: it reads `last_failure_code` through SQL, not through `Claim`, and the BACKOFF
rule (`job_store.py:308-309`) is old behaviour. It is a guard of an FR-21.2 acceptance row that the go-live loop
of Task 5 will rely on, and it is not assertion free: removing the `retry_after` check makes the second claim
CLAIMED and fails the test. I accept it under V.1, because the test can fail on wrong behaviour and names its FR
row, and it was reported honestly. The plan lists it under "Failing tests first", which is not quite true; Task 9
(spec notes and test links) can say it guards existing behaviour. No fix needed in this task.

### M-2 check: does the self test catch a narrowed guard?

Yes. I ran the self test in memory (no file written) with `_FAILURE_CALLS` narrowed, using the worktree venv:

| `_FAILURE_CALLS` | `test_the_scan_finds_constants_literals_and_imported_names` |
| --- | --- |
| as committed | passes |
| without `RetryLater` | fails (caught) |
| without `Failure` | fails (caught) |
| without `PermanentFailure` | passes (not caught) |

`retry_code` is a string literal argument, so only the `RetryLater` entry of `_FAILURE_CALLS` can find it: M-2
is closed. The `PermanentFailure` gap is older than this task (its argument `CODE_ONE` is also found by the
`CODE` name rule); see minor N-2.

## Commands and outcomes

Run from the worktree at HEAD 45828d9; the only change after f109a46 is the constitution, so the code under test
is f109a46. `free -m` showed about 3500 to 4000 MB available, no Kind stack and no other Testcontainers suite.

| Command | Result |
| --- | --- |
| `CONTRACTS_REPO=... make py-fmt-check py-lint py-typecheck py-layers` | passed |
| `CONTRACTS_REPO=... make py-test` | passed 7160 |
| `make py-test-integration` | passed 562 (271 deselected) |
| In-memory mutation of `_FAILURE_CALLS` (python `-B`, no file written) | see the M-2 table |
| `make spec-check` | not run |
| `make py-test-messaging` | not run |
| `make contracts-check chart-check` | not run |
| Kind suites | not run |

- Static gates: 633 files formatted; ruff all checks passed; mypy no issues in 627 files; import-linter 16 kept,
  0 broken.
- `py-test-integration`: `test_store_jobs.py` 18 of 18, the three new tests among them.
- `spec-check`: the reviewer hook refuses it (not a check target for reviewers); the lead should run it.
- `py-test-messaging`: no broker, consumer or outbox code changed.
- `contracts-check chart-check`: no contract or chart change.
- Kind: no cluster code; Kind runs belong to kind-runner.

## Findings

Critical: none.

Important: none.

Minor (no fix required in this task):

- **N-1. The FR-21.5 test does not start from a stop.** `tests/integration/test_store_jobs.py:263` releases with
  `route_not_accepted`, a failed try, while the FR-21.5 row's precondition is "the worker running try 1 stops".
  The assertion that claim 2 is try 2 holds, and the plan names the test so; the stop and lapse paths are covered
  by `test_an_expired_claim_is_taken_over_and_the_old_claim_cannot_write` (FR-25.1) and the planned Task 5 and
  Task 10 FR-21.5 tests. Task 9 can link this test to FR-21.5 as the "claim reads the row" part only.
- **N-2. The scanner self test does not catch a missing `PermanentFailure`.**
  `tests/unit/core/domain/values/test_failure_codes.py:215` feeds it `CODE_ONE`, which the name rule finds anyway.
  Older than this task; a literal argument there would close it. Out of scope here.
- **N-3. The broker suite's fake store drops the field.** `tests/messaging/_memstore.py:93-100` builds a `Claim`
  without `last_failure_code`, so a messaging test always sees None. Harmless now; Task 4 or 5 must seed it there
  if a consumer test depends on a try code.

## Security

No auth, RBAC, policy, secret or dependency change. A failure code is a catalogue value, not customer content.
security-reviewer is not needed for this task.

## Verdict

Approved. Task 3 matches plan Task 3, design G12, note N-5 and decision 0027 T9: `Claim.last_failure_code` is the
last field with default None, `claim` fills it from the row `lock_dispatch` already locks, `extend_claim` copies
it, and no SQL statement or schema changes. M-1 and M-2 are closed. The minor notes N-1 to N-3 need no fix round.
