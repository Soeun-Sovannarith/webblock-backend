# Review: go-live tries code, Tasks 4 to 7 and the decision 0027 docs

Stage: 5 Verify (the one independent review of the change, lean workflow). Branch `go-live-tries`, worktree
`.claude/worktrees/go-live-tries`. Range reviewed: `git diff 77e5fb4..ed60a83`. The commits are `ec840e4`, `657df1c`,
`0897c13` and `d648240`, plus the docs commit `ed60a83`. Tasks 1 to 3 had their own reviews and are out of scope.

IDs covered: FR-19 (19.1 to 19.3), FR-21 (21.1 to 21.6), FR-22 (22.1, 22.2), FR-23.1, FR-37.3; TR-57, TR-58.
Constitution rules checked hardest: III.2, III.3, III.4, III.7, V.1.

Sources read: the design `.claude/docs/designs/go-live-tries-design.md` (G4 to G16, section 7), decision `0027`, the
FRD rows above, TRD rows TR-57 and TR-58, and Notion "5 · Provisioning" (fetched read-only).

While I reviewed, commit `ac32edc` (Task 9, spec links) landed on the branch. In `tests/` it changes only two module
docstrings, so the gate results below on `ac32edc` hold for `ed60a83`.

## Verdict

Approved. I found no High or Medium finding, so no re-review is needed. There are three Low findings, one of them
for the owner.

## Lead rulings accepted

- `abandon` returns the failure code its outcome wrote, and None when it wrote none. The code follows this rule
  (`recovery.py`: `return out.command.failure_code`). A confirmed rollback or hosting change succeeds, so it gives
  None. Tests: `test_tr57_an_abandon_that_writes_nothing_returns_none` and
  `test_tr57_a_rollback_or_hosting_abandon_ignores_a_try_code`.
- `_check_entitlement(fence, release_id, try_number)` is at `routing.py:641`.
- The guard in `_between_tries` for the last try (`routing.py:519-520`) is kept. No test reaches it.
- There is one narrow `# noqa: E501` on the test name the plan gave (`test_routing.py`, FR-21.2 unconfirmed restore).
- The production profile, observability, failure codes, the four docs that described one try, and the
  `.env.example` comments were all updated in the code commits.

## The try loop, row by row (`core/application/routing/background/routing.py`)

| Row | What the code does | Evidence | Status |
| --- | --- | --- | --- |
| FR-19.1 | Fresh start: entitlement, intent, preview, switch, `confirm_live`, cleanup (`:462-475`) | `test_fr21_6_on_a_fresh_start_the_intent_is_stored_before_apply_preview`; integration `test_fr21_3_...` (one usage row) | done |
| FR-19.2 | A preview not confirmed gives `route_not_accepted` (`:677-678`), and so does a switch not accepted (`:708-709`). Each is a failed try | `test_fr19_2_a_preview_never_confirmed_...`, `test_fr19_2_a_switch_not_accepted_on_try_1_...`, `test_fr19_each_failed_step_gives_its_try_code[*]` | done |
| FR-19.3 | No answer after the switch gives `site_not_answering` (`:712-715`). It is a failed try | `test_fr19_3_a_site_not_answering_on_try_1_...` | done |
| FR-21.1 | A failed try 3 logs, then `_restore` runs (`:479-480`) and writes `restore_verified` with try 3's code. The command is `failed`, routing `stable`, and there is no usage row | unit `test_fr21_1_try_3_failing_restores_l_and_rolls_back_with_its_code` (try 3's code differs from tries 1 and 2); integration `test_fr21_1_three_failed_tries_roll_back_with_try_3s_code` (`usage_rows() == 0`, `execution_attempts` 3, `finished_at` set) | done |
| FR-21.2 | After a failed try 1, `_between_tries` (`:511-526`) calls `_to_restore_target`, then raises `RetryLater(code, 30 s)`. The intent is the only SQL write. The release stays `promoting`, routing `changing`, the command `running`, and `live_release_id` is unchanged | `test_fr21_2_a_failed_try_1_writes_nothing_after_the_intent_and_waits_30_s`; integration `test_fr21_2_...` (no claim, `retry_after` about now plus 30 s, `next_publish_at = retry_after`, an early claim is `BACKOFF` and leaves `execution_attempts` at 1) | done |
| FR-21.3 | Claim 2 on a `changing` route reads the route once, writes nothing to it, makes no entitlement call and records no second intent, then goes live | `test_fr21_3_claim_2_passing_reads_the_route_once_and_goes_live`; integration `test_fr21_3_...` (one usage row, `succeeded`, 2 attempts) | done |
| FR-21.4 | A failed try 2 waits 60 s (`wait_after_failed_try(2)`) | `test_fr21_4_a_failed_try_2_waits_60_s`; integration `test_fr21_4_after_try_2_the_row_waits_60_s` | done |
| FR-21.5 | The try number is `claim.execution_attempt` (`:449`). A `changing` route is restored first (`:453`). Claim 4 ends with `promotion_interrupted` on a `changing` route, or with `attempts_exhausted` on a `stable` one (`:456-461`) | `test_fr21_5_claim_2_after_a_lapse_points_the_route_back_before_its_preview`, `..._an_unreadable_route_is_pointed_back...`, `..._claim_4_on_a_changing_route...`, `..._claim_4_on_a_stable_route...`; integration `test_fr21_5_a_lapsed_claim_is_try_2_and_its_writes_are_refused` | done |
| FR-21.6 | Fresh-start preview failure: the intent is written, `apply_route` is never called, and the route read finds L accepted, so nothing is written (`:497-498`). Try 1 waits 30 s. Try 3 ends as FR-21.1 | `test_fr21_6_a_fresh_start_preview_failure_never_moves_the_route` (`applied_targets() == []`, route still at number 0), `test_fr21_6_try_3_preview_failure_ends_as_fr21_1`; integration `test_fr21_6_...` | done |
| FR-22.1 | No live release: the restore target is `OFFLINE` and `route_removed_verified` sets the release `failed` with try 3's code | `test_fr22_1_no_live_release_try_3_failing_goes_offline_and_fails_with_its_code`; integration `test_fr22_1_...` | done |
| FR-22.2 | An absent route is pointed `OFFLINE` between tries. The release stays `promoting` and the command `running` | `test_fr22_2_no_live_release_try_1_failing_points_an_absent_route_offline`; integration `test_fr22_2_...` | done |
| FR-23.1 | Three paths end with `route_uncertain` and `route_restore_unconfirmed`, and none raises `RetryLater`: restore first not confirmed (`:453-455`), the between-tries restore not confirmed (`:525-526`), and `_restore` not confirmed (`:731-733`) | `test_fr21_2_a_restore_after_try_1_not_confirmed_is_route_uncertain_without_retry_later`, `test_fr21_5_a_resumed_claim_whose_restore_first_is_not_confirmed_is_route_uncertain`, `test_tr57_an_unconfirmed_abandon_keeps_the_consumer_code` | done |
| FR-37.3 | Read through the API role: `rolled_back` with try 3's code; `promoting` with no code or summary | integration `_api_read` in `test_fr21_1_...` and `test_fr21_2_...` | done |
| TR-57 | One `RetryLater` per failed try 1 or 2. Payment unreachable is a failed try: it waits on tries 1 and 2, and on try 3 it is `attempts_exhausted` with the release still `candidate_ready`. An abandon keeps the try code. Each failed try writes one log line | `test_fr21_2/4_payment_unreachable_...`, `test_fr21_1_payment_unreachable_on_try_3_is_attempts_exhausted`, the 6 `test_tr57_*` in `test_recovery.py`, `test_tr57_each_failed_try_logs_one_info_line`, `test_tr57_the_try_log_line_holds_only_its_named_fields`, consumer `test_tr57_the_abandon_log_names_the_code_written` and `..._wrote_nothing_is_logged_so`, executor `test_tr57_abandoning_*` | done |
| TR-58 | `_check_timing` requires `attempt > 2S + 2R`, `COMMAND_DEADLINE > _go_live_bound` and `WORKER_MAX_ATTEMPTS > 3` (`config/worker.py`). `_go_live_bound` = 3 x (2S + R) + the waits (30 s plus 60 s, from the domain rule) + 3 x (max(L, B) + R). This is the TRD formula exactly | `test_tr58_*` (5 tests): 615 s refused and 616 s accepted, 660 s and 661 s with either L or B longer, 230 s and 241 s, budget 3 refused and 4 accepted, prototype accepted, and no value appears in a reason | done |

Other checks:

- **Transitions.** Every state move is one Notion "5 · Provisioning" allows:
  - release: `candidate_ready` to `promoting`, then to `live`, `rolled_back` (a live release exists) or `failed` (none);
  - routing: `stable` to `changing`, then to `stable` or `uncertain`;
  - command: `running` to `succeeded` or `failed`.
  Between tries `releases.failure_code` stays NULL, as Notion requires ("set exactly when `failed` or
  `rolled_back`"). Only the dispatch row's `retry_after`, `next_publish_at` and `last_failure_code` move. These are
  Task 2's `release_claim`, which needs no SQL change.
- **III.4.** Each SQL write is one call to `apply_route_outcome`, `load_route_command` or `release_claim`, and none
  holds a transaction across a cluster or Payment call. The entitlement recheck finishes before the intent write.
  The cluster calls (`_to_restore_target`, `_preview`, `_switch`, `_restore`) run between store calls, never inside
  one. The outcome lambdas are pure domain functions.
- **The usage row.** It is written only through `confirm_live`. The integration tests count 0 rows after FR-21.1 and
  FR-22.1, and 1 row after FR-21.3.
- **Time bounds.**
  - `_to_restore_target` stays within one `restore_deadline` for the read and the point together (`:489-509`).
  - The read uses `route_status`. The adapter computes `accepted` against `metadata.generation`, and it takes the
    target from the spec (`site_cluster.py:262-279`, `:551-566`). So "accepted at the target" cannot be a stale
    status of an older apply.
- **III.7.** `_log_failed_try` (`:743-759`) formats only `command_id`, `project_id`, `release_id`, `try`, `of`,
  `failure_code` (a code constant) and the wait. It never logs an error's text, a token or the preview token. The
  Routing use case cannot reach the preview token at all, since only the cluster adapter holds it.
  `test_tr57_the_try_log_line_holds_only_its_named_fields` puts a fake token and an address into the Payment error,
  and they never reach the log or the `RetryLater`. The `RetryLater` also carries no `__cause__`.
- **Renamed and removed tests.** Each old test's assertion is still covered:
  - `test_promote_proves_the_candidate_on_its_preview_before_recording_the_intent` became
    `test_fr21_6_on_a_fresh_start_the_intent_is_stored_before_apply_preview`. The new order is intent before preview,
    and the check that every probe expects the candidate is kept.
  - `test_a_candidate_failing_its_preview_fails_the_command_and_leaves_the_route` became
    `test_fr21_6_a_fresh_start_preview_failure_never_moves_the_route`. The route is still untouched, and probing
    until the deadline is still checked in `test_a_candidate_answering_503_...`.
  - `test_a_refused_preview_is_a_permanent_route_refused` became `test_fr19_a_refused_preview_is_a_failed_try_...`.
    It keeps the same 3 refusals.
  - `test_a_preview_never_confirmed_is_transient_and_writes_nothing` became `test_fr19_2_a_preview_never_confirmed_...`.
    It still checks that nothing is probed and that the site route is not applied.
  - `test_entitlement_that_cannot_be_checked_is_transient` became the three Payment tests.
  - The tests that expected a restore after one failed switch now run two failed tries first (`fail_tries`, or
    `run_failed_tries` in `test_routing_recovery.py`). They keep their assertions. `test_routing_store.py` runs the
    restore on claim 3.
  - `test_a_claim_resumed_during_a_restore_finishes_the_restore` now proves claim 4 and `promotion_interrupted`.
- **Notion and III.3.** There is no migration and no SQL change in the range. The worker's state moves match the
  Notion page (fetched 2026-10-27 08:42).

## Constitution check

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | All 5 commits cite FR and TR IDs (I.2). The code matches design G4 to G15. No Drift found (I.3). This is the one review (I.1) |
| II Tech stack | not applicable | `uv.lock` and the tool pins are unchanged. No library, image or store is added |
| III Architecture limits | pass | III.1: the rule is in the domain and the use case is in `routing/background`. III.2: `make py-layers` keeps 16 contracts and breaks 0. III.3: no schema change, and every state move is one Notion allows. III.4: see above. III.6: only the worker's `Routing` calls the cluster. III.7: see above. III.8: no API change |
| IV Coding conventions | pass | ruff and `mypy --strict` are clean. The one `noqa` names its rule and its reason (lead ruling) |
| V Testing and quality | pass | V.1: every new test names an FR or TR ID. That each test failed first cannot be seen in the history, because tests and code land in the same commits. V.2: the gates below. V.3: the phase sweep is not part of this task |
| VI Safety guardrails | pass | No secret is read or written, and `.env.example` has comments only. I ran no cluster command |
| VII Docs | pass, Low L-1 | No en or em dash in the changed docs. No new prose line is over 120. The docs state current facts, except L-1 |
| VIII Agents | pass | I did not write this code. This file is the only file I wrote |

## Commands and outcomes

I checked first: `free -m` showed about 3000 MB available, and `docker ps` showed no Kind stack and no
Testcontainers. I ran one suite at a time.

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | passed | 634 files formatted; ruff clean; mypy: no issues in 628 files; 16 contracts kept, 0 broken |
| `CONTRACTS_REPO=../web-block-contracts make py-test` | passed 7210 | 157 s |
| `make py-test-integration` | passed 570 | `test_go_live_tries.py` 8 of 8; `test_routing_store.py` passed |
| `make py-test-messaging` | passed 34 | The consumer and `_memstore.py` are touched |
| `make contracts-check` | passed | Matches `afff924` |
| `make chart-check` | passed 23 | |
| `make spec-check` | not run | The reviewer hook refuses it as a non-check target. Task 9 (`ac32edc`) runs it |
| `make py-cover`, `py-vuln`, `py-age`, `image-scan` | not run | No dependency or image change. Coverage needs every suite, Kind included |
| Kind and e2e suites | not run | Task 10. Reviewers never run Kind |

## Findings

### High

None.

### Medium

None.

### Low

- **L-1: the docs cite e2e evidence that the e2e tests do not assert yet (VII.1).**
  - Where:
    - `.claude/docs/architecture/acceptance-scenarios.md:27` and `:32` list "e2e" as evidence for "after 3 tries the
      release is `rolled_back`".
    - `.claude/docs/guides/team-testing.md:312` and the lines after it describe `test_4_...` ending `rolled_back`
      with `dependency_unavailable`.
  - Problem: `tests/e2e/test_flows.py:190-192` still asserts `failed` and `candidate_ready`. Decision `0027:120`
    states this openly, and Task 10 owns it.
  - Fix: Task 10 updates `test_flows.py` test 4 and 4b, `test_scenarios_promote.py` and `test_routing_live.py` to 3
    tries. If Task 10 slips, mark those two evidence cells "unit, store" until the e2e run passes.
- **L-2: the Notion prose is out of date (for the owner, not a schema difference).**
  - Problem: the `commands` section, under "Not the same as", says "a failed `promote` may leave its release
    `candidate_ready` (preview failed)". After this change, only `entitlement_lost` and Payment being unreachable on
    all 3 tries leave it there, and a failed preview is now a failed try. Design section 6 already names this, and
    the page is read-only for agents (VI.4).
  - Fix: the owner decides whether to change "(preview failed)" to "(entitlement lost, or Payment unreachable on
    every try)".
- **L-3: an accepted limit, noted only.**
  - Problem: if `release_claim` itself fails after a `RetryLater` (a database error), no `retry_after` is written.
    The claim then lapses after `WORKER_LEASE_TTL` (15 s), so the next try can start before the 30 s or 60 s wait.
    This is the same class as decision `0027` T5 ("a try may start 30 s or 60 s early after a crash"), and the
    command deadline bound covers it.
  - Fix: no change. Add one clause to `0027` T5 only if the owner wants this case named.

## Skipped checks and blockers

- Kind and e2e suites: not run. They are Task 10, and they still assert one try.
- `make spec-check`: not run (blocked by the reviewer hook).
- Coverage: not run.
- No blockers.
