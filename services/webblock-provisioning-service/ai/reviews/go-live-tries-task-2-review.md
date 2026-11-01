# Review: go-live tries, Task 2 (one `RetryLater`)

Stage: 5 Verify (task). IDs: FR-21, FR-21.2, FR-21.4, TR-57. Constitution: III.1, III.2, IV.1, V.1 and all articles.
Diff reviewed: `git diff aff103e..c5a65c5` (commit c5a65c5, 18 files, 188 insertions, 64 deletions).
Checked against: plan `.claude/docs/plans/go-live-tries.md` Task 2, design G11 and G15, decision 0027 T8, design
review notes M-1, M-2 and N-1. The reviewer did not write this work.

## Verdict

Approved. No critical or important finding. Three minor notes below; none blocks the task.

## Constitution check

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | Commit cites FR-21, TR-57; each change maps to Task 2 or a lead decision |
| II Tech stack | not applicable | No dependency, image or tool change |
| III Architecture limits | pass | See the notes below the table |
| IV Coding conventions | pass | mypy strict and ruff clean; one class; `FAILURE_CODE_TRANSIENT` once |
| V Testing and quality | pass | New tests name TR-57 (one also FR-21.2); negative cases covered |
| VI Safety guardrails | pass | No secret, no cluster, no push; log names code, type, `retry_after` |
| VII Docs | pass | Docstrings and comments only; no en or em dash; no new line over 120 |
| VIII Agents | pass | Independent review; the reviewer wrote only this file |

Article III notes:

- III.1: `RetryLater` sits in `core/application/jobs/claims.py:140`, beside `PermanentFailure`.
- III.2: `py-layers` 16 kept, 0 broken. The consumer no longer imports `deploys/background/deploy.py`.
- III.3: no schema change. III.4: same `release_claim` path, no new transaction. III.6: no cluster code.

## Requirements and tests

| ID | Test | Status |
| --- | --- | --- |
| TR-57 (G11, T8) | `test_tr57_retry_later_refuses_a_zero_or_negative_wait` | done |
| FR-21.2, TR-57 (G11) | `test_tr57_a_retry_later_with_a_wait_releases_the_claim_for_exactly_that_wait` | done |
| TR-57 (G11) | `test_tr57_a_retry_later_without_a_wait_uses_the_backoff` | done |
| TR-57 (G11, M-2) | `test_tr57_a_retry_later_with_an_invalid_code_releases_with_transient_failure` | done |
| TR-57 (G15) | `test_tr57_a_retry_later_with_a_wait_is_measured_try_failed` | done |
| TR-57 (G15) | `test_tr57_a_retry_later_without_a_wait_is_measured_transient_failure` | done |
| TR-57 (G15) | `test_tr57_try_failed_is_a_bounded_execution_result` | done |
| FR-21 (deploy unchanged) | transient tests in `test_deploy.py`, `test_redeploy_executor.py` | done |
| N-1 | `test_failure_codes.py` `_FAILURE_CALLS` | done |
| FR-21.4 | none in this task | minor M-3 |

Evidence:

- Zero or negative wait: `claims.py:146-147` raises `ValueError` for `after <= timedelta(0)`; the test checks 0 s
  and -1 s, and that None and 30 s are kept with the code.
- Wait set: one release `(3, 30 s, "route_refused")`, ack, no finish, and the log line holds `retry_after=0:00:30`.
- No wait: it replaces `test_a_deploy_retry_later_releases_the_claim_with_its_code`, keeps both parameter rows, and
  asserts the attempt 1 backoff range.
- Invalid code: with and without a wait, the release code is `transient_failure`; the wait is the given one or the
  backoff.
- Metric: `_result_of` returns `try_failed` only when `after` is set (`consumer.py:598-599`);
  `EXECUTION_RESULTS` in `infrastructure/telemetry/metrics.py` gains `try_failed`, so it is not folded into `other`.
- Deploy and redeploy: the same codes, plus `assert raised.value.after is None`.
- N-1: `_FAILURE_CALLS = {"Failure", "PermanentFailure", "RetryLater"}` (`test_failure_codes.py:52`); docstring
  lines 13 and 14 match. The three call sites (`deploy.py:305`, `deploy.py:340`, `redeploy.py:239`) pass the same
  arguments as before, so the guard scans exactly what it scanned before the rename.

## Specific checks

- `DeployRetryLater` left anywhere: none in `src`, `tests`, `deploy`, `scripts`, runbooks or architecture docs
  (grep). Only the design, plan, decision 0027 and the design review name it, to describe the replacement.
- `FAILURE_CODE_TRANSIENT` once: `claims.py:35`. The consumer and its tests import it from
  `provisioning.core.application`. Other `"transient_failure"` strings are the catalogue entry, the metric label
  sets and `RESULT_TRANSIENT_FAILURE`, all from before this task.
- Consumer `_execute` order, before and after: claim lost, finished, `PermanentFailure`, `ClaimLost` or
  `StaleFence`, then the release. `_release_of` (`consumer.py:158`) keeps `_transient_code`'s order: attempt
  timeout first (no wait of its own), then the `RetryLater` code check, else `transient_failure`. The delay is
  `after`, or `retry_backoff.delay(claim.execution_attempt)` as before when `after` is None. `_result_of` keeps its
  order and adds `try_failed` after the attempt timeout branch. Deploy and redeploy behaviour is unchanged.
- The consumer knows nothing about promote: the branch tests only `isinstance(error, RetryLater)` and `after`. The
  comment at `consumer.py:75` names a go-live try as the use; no logic depends on the kind.
- `release_claim` still checks the duration and the code (`job_store.py:386-391`), so a bad value is refused in
  the constructor and again in the store.
- Error tables (lead decision): `_internal(RetryLater)` at `presentation/operations/errors.py:334`, as for
  `PermanentFailure`; both table tests carry the new row.
- Log line (`consumer.py:455-462`): command fields, code, error type name and `retry_after`. No token, credential or
  error message text.

## Commands and outcomes

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | passed | 633 formatted; ruff clean; mypy 627 files; 16 kept |
| `make py-test` | passed 7160 | `CONTRACTS_REPO` set; 149 s |
| `make py-test-messaging` | passed 34 | Testcontainers RabbitMQ; no Kind stack; 4399 MB free |
| `make spec-check` | not run | The reviewer hook refuses it; new test links are Task 9 |
| `make contracts-check chart-check` | not run | No contract, chart or generated file in the diff |
| `make py-test-integration` | not run | No SQL, store or migration change |
| Kind suites | not run | No cluster code; Kind belongs to kind-runner |
| Dash and line length check, changed files | passed | No U+2013 or U+2014; no new line over 120 |

Tests failing first: not observable after the fact. Each new test imports `RetryLater` or `FAILURE_CODE_TRANSIENT`
from `provisioning.core.application`, which did not exist at `aff103e`, so each fails there by construction.

## Findings

### Critical

None.

### Important

None.

### Minor

**M-1. A broken comment wrap.** `tests/unit/core/application/shared/test_error_table.py:121-122`: the rewrap leaves
`# row for them);` alone on a short line before the next sentence. Cosmetic; rejoin it when the file is next
touched.

**M-2. The scanner's self-test does not prove `RetryLater` is scanned.**
`tests/unit/core/domain/values/test_failure_codes.py:209`
(`test_the_scan_finds_constants_literals_and_imported_names`) exercises `PermanentFailure`, `Failure` and
`failure_code=`, never a `RetryLater(...)` call. N-1 is fixed in `_FAILURE_CALLS`, but a later rename could narrow
the guard silently again, as N-1 described. The gap was there before this task (no `DeployRetryLater` row either).
Suggest one line such as `raise RetryLater("retry_code")` in the synthetic module, with `retry_code` in the expected
set. Optional.

**M-3. FR-21.4 is cited but no test in this task names it.** The plan lists FR-21.4 in Task 2's IDs; the 60 s wait
appears only as a parametrize id (`"60s"`) in the invalid-code test and as a value in the metric test. The FR-21.4
acceptance row (try 3 no sooner than 60 s) is promote behaviour that Task 5 owns, so this is traceability only.
Task 9 (spec links) should link FR-21.4 to Task 5's tests, not to this task's.

### Notes, not findings

- `.claude/docs/runbooks/observability.md:54` does not list `try_failed` yet. Task 8 plans that change.

## Skipped checks and blockers

`make spec-check` was not run (reviewer hook). `contracts-check`, `chart-check`, `py-test-integration` and the Kind
suites were not run because the diff does not touch them. No blocker.
