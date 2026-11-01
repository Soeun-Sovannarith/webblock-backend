# Plan: going live takes up to 3 tries

> For agentic workers: one implementer at a time runs one task, then the provisioning-validator reviews it before the
> next task starts (`superpowers:subagent-driven-development` or `superpowers:executing-plans`, within
> `.claude/WORKFLOW.md`). Steps use checkbox (`- [ ]`) syntax.

Stage: 3 Plan. IDs: FR-19 (FR-19.1 to FR-19.3), FR-21 (FR-21.1 to FR-21.6), FR-22 (FR-22.1, FR-22.2), FR-37.3;
TR-57, TR-58. Decision record: `0027` (Proposed; accepted at this plan gate).

**Goal:** one `promote` command tries the go-live up to 3 times, with waits of 30 s and 60 s, the route back on the
live release or the offline page between tries, and the release ended only after try 3.

**Architecture:** each claim of a promote is one try (`Claim.execution_attempt`). After a failed try 1 or 2 the
executor puts the route on the restore target and raises `RetryLater(code, wait)`; the consumer gives the claim back
through `JobStore.release_claim`, so the wait lives in `command_dispatch.retry_after`. Try 3 restores and ends the
release. No schema change.

**Tech stack:** Python 3.14, the existing worker, consumer, `PgJobStore`, `PgRouteCommandStore`; pytest,
Testcontainers PostgreSQL 18 and RabbitMQ; the local Kind stack for live suites.

**Spec:** design `.claude/docs/designs/go-live-tries-design.md` (G1 to G16, section 6 files, section 7 tests per row),
decision `.claude/docs/decisions/0027-go-live-tries.md`, FRD rows FR-19, FR-21, FR-22, FR-37.3, TRD rows TR-57 and
TR-58. Base: branch `go-live-tries`, commit `2e2273d` (design fix round 2), worktree
`.claude/worktrees/go-live-tries`.

## Global Constraints

- Constitution `.claude/docs/CONSTITUTION.md`, Articles I to VIII, bind every task:
  - I: every task and commit cites its FR and TR IDs; drift stops the task (I.3); at most 2 fix rounds (I.4).
  - II: no new library, image, tool or store.
  - III: no database change (III.3); PostgreSQL holds the try counter and the wait (III.4); no transaction across a
    remote call; only the worker calls the cluster (III.6); import contracts unchanged (III.2).
  - IV: `.claude/rules/python-code.md`; `mypy --strict` covers `src` and `tests`, so a test imports a name from the
    module that defines or exports it.
  - V: tests first from the acceptance rows, each naming its ID; gates never lowered; a check not run is "not run".
  - VI: Kind only through `kind-runner` on the project's Kind cluster, after the owner grants a slot; no secret read,
    printed or logged.
  - VII: plain English, no em or en dash, prose lines at most 120 characters (tables may be longer).
  - VIII: one implementer at a time in the shared tree; someone else reviews.
- Owner and lead rules from the design:
  - No migration, column, index, enum value, constraint or grant (G2). A task that needs one stops and reports.
  - Try n is `Claim.execution_attempt`; the wait is `command_dispatch.retry_after` (G1).
  - `GO_LIVE_TRIES` 3 and the waits 30 s and 60 s are code constants in `core/domain/rules/go_live.py` (G3). The four
    try code constants move there; `routing.py` imports them; no copy of the strings stays (G3).
  - The intent is written before the preview on a fresh start (G4). The cluster route never moves before the
    preview passes.
  - No SQL write in the executor between tries (G6). The release stays `promoting`, routing `changing`, the command
    `running` (G7).
  - A claim that finds routing `changing` restores first (G9). An interrupted try counts and has no wait (G9).
  - One `RetryLater(code, after=None)`; `DeployRetryLater` is removed (G11).
  - The preview token, tokens, credentials and site content are never logged (G15).

## Constitution check

| Article | Result | Why |
| --- | --- | --- |
| I Spec-driven flow | pass | Every task cites FR-19, FR-21, FR-22, FR-37.3, TR-57 or TR-58; 0027 is accepted at this gate before code |
| II Tech stack | pass | No library, image, tool or store added |
| III Architecture limits | pass | No schema change in any task; tries and waits live in SQL; the executor writes no SQL between tries |
| IV Coding conventions | pass | Rule in the domain, use case in `routing/background`, one `RetryLater`, try codes defined once |
| V Testing and quality | pass | Each task writes its failing tests first; two start checks added; the failure-code guard keeps checking retries |
| VI Safety guardrails | pass | Live suites only on the local Kind stack through `kind-runner` (Task 10, blocked on an owner slot) |
| VII Docs | pass | Docs task (8) and spec task (9) state current facts only |
| VIII Agents | pass | Tasks run one at a time; provisioning-validator after each; Task 7 runs in parallel only in its own worktree |

No conflict.

## Review Focus

The cases most likely to bite that no acceptance row names. Each has its test in the owning task.

1. The cluster cannot be read at the start of a resumed claim: the route must still go back to the restore target
   before the try (Task 5, `test_fr21_5_an_unreadable_route_is_pointed_back_before_the_try`).
2. A duplicate delivery while a try waits must not burn a try: `BACKOFF`, `execution_attempts` unchanged (Task 3,
   `test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try`).
3. A worker stop after a failed try overwrites the try code with `worker_stopping`; a deadline that passes later must
   end with today's code, not a stale one (Task 4,
   `test_tr57_a_promote_whose_row_holds_another_code_ends_with_the_consumer_code`).
4. Production settings that fail the new start checks make the worker refuse to start after a deploy (Task 7 tests;
   Task 8 runbook; open question OQ-3).
5. Try 3 whose fresh-start preview fails must still end through `_restore` and stamp the route with this command's
   number (Task 5, `test_fr21_6_try_3_preview_failure_ends_as_fr21_1`).

## Run order and dependencies

| Task | Title | Needs before it |
| --- | --- | --- |
| 1 | Domain rule and one copy of the try codes | none |
| 2 | One `RetryLater` | 1 |
| 3 | The claim carries the last failure code | 2 (same `claims.py`) |
| 4 | The abandon keeps the try code and returns what it wrote | 1, 3 |
| 5 | The promote try loop | 1, 2, 3, 4 |
| 6 | Payment unreachable is a failed try; the try log line | 5 (same `routing.py`) |
| 7 | Worker start checks (TR-58) | 1; may run beside 2 to 6 in its own worktree |
| 8 | Docs | 4, 5, 6, 7 |
| 9 | Spec notes and test links (spec-writer) | 1 to 7 |
| 10 | Live suites on Kind (written; run blocked on the owner's Kind slot) | 5, 6 |

The fast tier in every task is, from the worktree root:

```sh
CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts \
  make py-fmt-check py-lint py-typecheck py-layers py-test spec-check
```

`make py-test-integration` and `make py-test-messaging` run where a task says so. Each task ends with the
provisioning-validator review. Commits stage named files only and end with the Co-Authored-By line the harness gives.

---

### Task 1: Domain rule and one copy of the try codes

IDs: FR-21, FR-21.2, FR-21.4, TR-57 (G3)
Parallel: no
Needs: none
Files: Create `src/provisioning/core/domain/rules/go_live.py`; Modify `src/provisioning/core/domain/__init__.py`,
`src/provisioning/core/application/routing/background/routing.py` (lines 94 to 97: the four try code constants
removed, imported from `core.domain`), `tests/unit/core/application/routing/background/test_routing.py` and
`tests/integration/kubernetes/test_routing_live.py` (import the codes from `provisioning.core.domain`); Test
`tests/unit/core/domain/rules/test_go_live.py`
Failing tests first: `test_tr57_three_tries`, `test_fr21_2_the_wait_after_try_1_is_30_s`,
`test_fr21_4_the_wait_after_try_2_is_60_s`, `test_tr57_no_wait_after_the_last_try` (3 and 4 give None),
`test_tr57_a_try_number_below_1_is_refused` (0 and -1 raise `ValueError`),
`test_tr57_the_try_codes_are_five_release_and_command_codes` (each code of `GO_LIVE_TRY_CODES` is in
`FAILURE_CODES` with places release and command; the set equals the four constants plus
`CODE_DEPENDENCY_UNAVAILABLE`)
Steps:
- [ ] 1. Write the tests; run `.venv/bin/python -X dev -m pytest -m unit tests/unit/core/domain/rules/test_go_live.py`;
  see them fail on the import.
- [ ] 2. In `go_live.py` (stdlib and `core.domain.values` only) define the interface later tasks use:
  `GO_LIVE_TRIES: Final = 3`; `CODE_ROUTE_REFUSED = "route_refused"`, `CODE_ROUTE_NOT_ACCEPTED =
  "route_not_accepted"`, `CODE_CANDIDATE_NOT_ANSWERING = "candidate_not_answering"`, `CODE_SITE_NOT_ANSWERING =
  "site_not_answering"`; `GO_LIVE_TRY_CODES: Final[frozenset[str]]` built from those four and
  `CODE_DEPENDENCY_UNAVAILABLE` (`values/deploy_failures.py`); `wait_after_failed_try(try_number: int) ->
  timedelta | None` (30 s after 1, 60 s after 2, None from `GO_LIVE_TRIES` on, `ValueError` below 1).
- [ ] 3. Export all six names from `core/domain/__init__.py` (import and `__all__`).
- [ ] 4. `routing.py` imports the four codes from `provisioning.core.domain`; the two test files import them from
  there. No behaviour change.
- [ ] 5. Fast tier green. The Kind test file is type-checked only; it is not run here.
Gates: fast tier
Commit: `Domain: go-live tries, waits and try codes in one rule (FR-21, TR-57)`

### Task 2: One `RetryLater`

IDs: FR-21, FR-21.2, FR-21.4, TR-57 (G11, G15 metric)
Parallel: no
Needs: none
Files: Modify `src/provisioning/core/application/jobs/claims.py`, `src/provisioning/core/application/__init__.py`,
`src/provisioning/core/application/deploys/background/deploy.py` (class at line 98 removed; raises at 313 and 348),
`.../deploys/background/redeploy.py` (import at 39, raise at 239, docstring 127),
`.../deploys/background/deploy_executor.py` and `redeploy_executor.py` (docstrings),
`src/provisioning/presentation/messaging/consumers/consumer.py` (`_transient_code`, `_execute`, `_result_of`, import
at line 40), `src/provisioning/infrastructure/telemetry/metrics.py` (`EXECUTION_RESULTS`); Test
`tests/unit/core/application/jobs/test_claims.py`, `tests/unit/presentation/messaging/consumers/test_consumer.py`,
`tests/unit/core/application/deploys/background/test_deploy.py`, `test_deploy_executor.py`,
`test_redeploy_executor.py`, `tests/unit/core/domain/values/test_failure_codes.py`,
`tests/unit/infrastructure/telemetry/test_metrics.py`
Failing tests first: `test_tr57_retry_later_refuses_a_zero_or_negative_wait` (0 s and -1 s raise `ValueError`;
None and 30 s are kept), `test_tr57_a_retry_later_with_a_wait_releases_the_claim_for_exactly_that_wait` (after 30 s:
one release with 30 s and the code, ack, no finish), `test_tr57_a_retry_later_without_a_wait_uses_the_backoff`
(replaces `test_a_deploy_retry_later_releases_the_claim_with_its_code`, both parameter rows kept),
`test_tr57_a_retry_later_with_an_invalid_code_releases_with_transient_failure` (with and without a wait),
`test_tr57_a_retry_later_with_a_wait_is_measured_try_failed`,
`test_tr57_a_retry_later_without_a_wait_is_measured_transient_failure`,
`test_tr57_try_failed_is_a_bounded_execution_result`
Steps:
- [ ] 1. Write the tests; see them fail.
- [ ] 2. In `claims.py`, beside `PermanentFailure`: `class RetryLater(Exception)` with
  `__init__(self, code: str, after: timedelta | None = None)`, attributes `code` and `after`; `after` zero or
  negative raises `ValueError`. Move `FAILURE_CODE_TRANSIENT = "transient_failure"` here from `consumer.py` (one copy;
  Task 6 uses it). Export both from `core/application/__init__.py`.
- [ ] 3. `deploy.py` and `redeploy.py` raise `RetryLater(code)`; delete `DeployRetryLater`. Deploy tests use
  `RetryLater` and also assert `after is None` (deploy behaviour unchanged).
- [ ] 4. Consumer: one branch for `RetryLater` after the attempt-timeout stop, as today's order: code checked by
  `validate_code` (invalid becomes `FAILURE_CODE_TRANSIENT`), delay `error.after` or
  `retry_backoff.delay(attempt)`, then `release_claim`. `RESULT_TRY_FAILED = "try_failed"` when `after` is set.
  The consumer imports `FAILURE_CODE_TRANSIENT` from `provisioning.core.application`; tests import it from there.
- [ ] 5. `EXECUTION_RESULTS` gains `"try_failed"`.
- [ ] 6. `test_failure_codes.py` line 52: `RetryLater` replaces `DeployRetryLater` in `_FAILURE_CALLS`; docstring lines
  13 and 14 match, so the guard keeps checking every retry code against the catalogue.
- [ ] 7. Fast tier, then `make py-test-messaging` (the command consumer runs on a real broker in
  `tests/messaging/test_loops.py`).
Gates: fast tier; py-test-messaging
Commit: `Jobs: one RetryLater with an optional wait replaces DeployRetryLater (FR-21, TR-57)`

### Task 3: The claim carries the last failure code

IDs: FR-21.2, FR-21.5, TR-57 (G12 "Claim gains last_failure_code")
Parallel: no
Needs: none (no SQL statement changes; no schema change)
Files: Modify `src/provisioning/core/application/jobs/claims.py` (`Claim`),
`src/provisioning/infrastructure/postgres/fenced.py` (`DispatchRow`, `_dispatch_of`),
`src/provisioning/infrastructure/postgres/job_store.py` (`claim` at line 336, `extend_claim` at line 375); Test
`tests/integration/test_store_jobs.py`
Failing tests first: `test_fr21_5_a_claim_reads_the_last_failure_code_of_its_row` (claim 1 has None; release it with
`route_not_accepted` and 30 s; move `retry_after` into the past through `db.admin()`; claim 2 has
`execution_attempt` 2 and `last_failure_code` `route_not_accepted`),
`test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try` (after the release: no claim, `retry_after`
about now plus 30 s, `next_publish_at` equal to it, command `running`; a claim is `BACKOFF` and
`execution_attempts` stays 1), `test_tr57_extend_claim_keeps_the_last_failure_code`
Steps:
- [ ] 1. Write the tests; run `make py-test-integration`; see them fail.
- [ ] 2. `Claim.last_failure_code: str | None = None` as the last field (the 8 test files that build a `Claim` stay
  as they are). `DispatchRow.last_failure_code: str | None` filled by `_dispatch_of` from the row the SELECT already
  reads. `claim` sets it from `dispatch`; `extend_claim` copies it from the claim.
- [ ] 3. Fast tier and `make py-test-integration` green.
Gates: fast tier; py-test-integration
Commit: `Jobs: a claim carries the dispatch row's last failure code (FR-21, TR-57)`

### Task 4: The abandon keeps the try code and returns what it wrote

IDs: FR-21, FR-22, FR-37.3, TR-57 (G12 "A deadline that passes anyway"; 0027 T9)
Parallel: no
Needs: none
Files: Modify `src/provisioning/core/application/routing/background/recovery.py` (`RouteAbandoner.abandon`),
`.../routing/background/routing_executor.py`, `.../hosting/background/hosting_executor.py`,
`.../deploys/background/deploy_executor.py` (each `abandon` returns `str | None`),
`src/provisioning/presentation/messaging/consumers/consumer.py` (`Abandoner` protocol, `_abandon_then_fail` log);
Test `tests/unit/core/application/routing/background/test_recovery.py`,
`.../routing/background/test_routing_executor.py`, `tests/unit/core/application/hosting/background/` (the hosting
executor test), `tests/unit/core/application/deploys/background/test_deploy_executor.py`,
`tests/unit/presentation/messaging/consumers/test_consumer.py`
Failing tests first: `test_tr57_a_promote_abandoned_after_a_failed_try_ends_with_that_try_code` (one case per code of
`GO_LIVE_TRY_CODES`: release `rolled_back` and command `failed` with it; `abandon` returns it),
`test_tr57_a_first_publish_abandoned_after_a_failed_try_fails_with_that_try_code` (route `OFFLINE`, release
`failed`), `test_tr57_a_promote_whose_row_holds_another_code_ends_with_the_consumer_code` (None, `worker_stopping`,
`transient_failure`: release and command get `deadline_exceeded`; returns it),
`test_tr57_an_unconfirmed_abandon_keeps_the_consumer_code` (row holds a try code; `route_uncertain` with
`deadline_exceeded`; returns it), `test_tr57_an_abandon_that_writes_nothing_returns_none` (routing `stable`),
`test_tr57_a_rollback_or_hosting_abandon_ignores_a_try_code`, `test_tr57_the_abandon_log_names_the_code_written`
(consumer with an abandoner that returns `route_not_accepted`: the "abandon hook ran" line names it),
`test_tr57_an_abandon_that_wrote_nothing_is_logged_so` (returns None: the line says no outcome was written)
Steps:
- [ ] 1. Write the tests (use `Claim(..., last_failure_code=...)`); see them fail.
- [ ] 2. `RouteAbandoner.abandon(claim, failure_code) -> str | None`: for a promote whose restore is confirmed, the
  code written is `claim.last_failure_code` when it is in `GO_LIVE_TRY_CODES`, else `failure_code`; the unconfirmed
  branch writes `route_uncertain` with `failure_code`; other kinds as today. Return the code written, None when no
  outcome was written.
- [ ] 3. `RoutingExecutor.abandon` and `HostingExecutor.abandon` return the abandoner's result;
  `DeployExecutor.abandon` returns None (OQ-5, answered b).
- [ ] 4. `Abandoner.abandon` returns `str | None`; `_abandon_then_fail` logs the returned code, or that no outcome
  was written; it still calls `_fail` with its own code (refused and acknowledged when the outcome ended the command).
- [ ] 5. Fast tier and `make py-test-messaging`.
Gates: fast tier; py-test-messaging
Commit: `Routing: an abandoned promote keeps its last try code; abandon returns the code written (FR-21, TR-57)`

### Task 5: The promote try loop

IDs: FR-19.1, FR-19.2, FR-19.3, FR-21.1 to FR-21.6, FR-22.1, FR-22.2, FR-23.1 (unchanged rule on a new path),
FR-37.3, TR-57 (G1, G4 to G9)
Parallel: no
Needs: none
Files: Modify `src/provisioning/core/application/routing/background/routing.py` (`Routing.promote`, `_preview`, new
`_to_restore_target` and `_between_tries`, module and method docstrings),
`src/provisioning/core/domain/values/failure_codes.py` (meaning of `promotion_interrupted`),
`.claude/docs/runbooks/failure-codes.md` (only the catalogue table between its markers, which
`test_the_runbook_shows_the_catalogue` compares), `tests/unit/core/application/routing/background/test_routing.py`,
`test_routing_recovery.py`, `test_routing_support.py`, `tests/integration/test_routing_store.py`; Create
`tests/integration/test_go_live_tries.py`
Failing tests first (unit, `test_routing.py`):
- `test_fr21_6_on_a_fresh_start_the_intent_is_stored_before_apply_preview`
- `test_fr19_each_failed_step_gives_its_try_code` (one case per G5 row: preview refused `route_refused`; preview not
  confirmed `route_not_accepted`; candidate silent `candidate_not_answering`; candidate 503
  `dependency_unavailable`; switch refused `route_refused`; switch not accepted `route_not_accepted`; site silent
  `site_not_answering`)
- `test_fr19_2_a_switch_not_accepted_on_try_1_points_back_to_l_and_waits_30_s`
- `test_fr19_3_a_site_not_answering_on_try_1_points_back_to_l_and_waits_30_s`
- `test_fr21_2_a_failed_try_1_writes_nothing_after_the_intent_and_waits_30_s` (release `promoting`, routing
  `changing`, command `running`, `live_release_id` L, `RetryLater(code, 30 s)`)
- `test_fr21_2_a_restore_after_try_1_not_confirmed_is_route_uncertain_without_retry_later` (FR-23.1)
- `test_fr21_3_claim_2_passing_reads_the_route_once_and_goes_live` (no entitlement call, no second intent)
- `test_fr21_4_a_failed_try_2_waits_60_s`
- `test_fr21_1_try_3_failing_restores_l_and_rolls_back_with_its_code` (no usage row, routing `stable`)
- `test_fr21_5_claim_2_after_a_lapse_points_the_route_back_before_its_preview`
- `test_fr21_5_an_unreadable_route_is_pointed_back_before_the_try`
- `test_fr21_5_a_resumed_claim_whose_restore_first_is_not_confirmed_is_route_uncertain`
- `test_fr21_5_claim_4_on_a_changing_route_ends_promotion_interrupted`
- `test_fr21_5_claim_4_on_a_stable_route_is_attempts_exhausted` (release `candidate_ready`)
- `test_fr21_6_a_fresh_start_preview_failure_never_moves_the_route` (`apply_route` never called;
  `RetryLater(candidate_not_answering, 30 s)`)
- `test_fr21_6_try_3_preview_failure_ends_as_fr21_1`
- `test_fr22_1_no_live_release_try_3_failing_goes_offline_and_fails_with_its_code`
- `test_fr22_2_no_live_release_try_1_failing_points_an_absent_route_offline` (release `promoting`)
Failing tests first (integration, `test_go_live_tries.py`, real PostgreSQL 18, `PgJobStore` and
`PgRouteCommandStore` as the worker role, `FakeCluster`; a helper claims, runs `Routing.promote`, and on `RetryLater`
calls `release_claim(claim, after or a fixed test backoff, code)`; a test moves `retry_after` into the past through
`db.admin()` instead of sleeping):
- `test_fr21_1_three_failed_tries_roll_back_with_try_3s_code` (R `rolled_back`, `execution_attempts` 3,
  `finished_at` set, L live, routing `stable`, no `usage_outbox` row; FR-37.3: `PgReadStore` as the API role reads
  `rolled_back` and the code)
- `test_fr21_2_after_try_1_the_row_waits_30_s_and_the_release_stays_promoting` (FR-37.3: the read gives `promoting`
  with no failure code or summary)
- `test_fr21_3_claim_2_passing_goes_live_with_one_usage_row` (L `superseded`, command `succeeded`)
- `test_fr21_4_after_try_2_the_row_waits_60_s`
- `test_fr21_5_a_lapsed_claim_is_try_2_and_its_writes_are_refused` (FR-25)
- `test_fr21_6_a_preview_failure_leaves_the_release_promoting`
- `test_fr22_1_three_failed_tries_of_a_first_deployment_fail_the_release`
- `test_fr22_2_between_tries_of_a_first_deployment_the_release_stays_promoting`
Steps:
- [ ] 1. Add a helper to `test_routing_support.py`: `async def run_failed_tries(routing: Routing, world: World,
  command: Command, tries: int) -> list[RetryLater]` (claims `tries` times, each expected to raise `RetryLater`).
- [ ] 2. Write the unit and integration tests; see them fail (unit: `.venv/bin/python -X dev -m pytest -m unit
  tests/unit/core/application/routing/background/test_routing.py`; integration: `make py-test-integration`).
- [ ] 3. `_preview` returns `CODE_ROUTE_NOT_ACCEPTED` when the preview route is not confirmed (no `Timeout`).
- [ ] 4. `async def _to_restore_target(self, fence: Fence, inp: OutcomeInput) -> bool`: within one
  `restore_deadline` in all, read `route_status`; accepted at `restore_target_of(live_release_id)` writes nothing;
  anywhere else, absent or unreadable, `RoutePointer.point` at the target with this command's number. True when
  confirmed.
- [ ] 5. `async def _between_tries(self, fence: Fence, inp: OutcomeInput, try_number: int, code: str) -> None`: only
  for tries below `GO_LIVE_TRIES`; confirmed raises `RetryLater(code, wait_after_failed_try(try_number))`; not
  confirmed records `route_uncertain` with `route_restore_unconfirmed` and returns.
- [ ] 6. `promote`, in this order (design section 3): routing `uncertain` is `CorruptRow` as today; routing
  `changing` runs `_to_restore_target` first (not confirmed: `route_uncertain`, end); `execution_attempt` above
  `GO_LIVE_TRIES` ends through `_restore` with `promotion_interrupted` when `changing`, or raises
  `PermanentFailure(CODE_ATTEMPTS_EXHAUSTED)` when `stable`; routing `stable` rechecks entitlement (unchanged in
  this task) and records `start_route_change`; then the try: `_preview`, then `_switch`; passing is `confirm_live`
  and cleanup; failing goes to `_between_tries` for tries 1 and 2, to `_restore` with the code for try 3. The old
  "a restore was under way" check goes.
- [ ] 7. Rewrite every existing test that asserts the old rule: either its try 3 form (two failed tries first, then
  the same end) or the FR test above that states the new rule. At least these in `test_routing.py`:
  - `test_promote_proves_the_candidate_on_its_preview_before_recording_the_intent`
  - `test_a_candidate_failing_its_preview_fails_the_command_and_leaves_the_route`
  - `test_a_candidate_answering_503_fails_with_dependency_unavailable`
  - `test_a_refused_preview_is_a_permanent_route_refused`
  - `test_a_preview_never_confirmed_is_transient_and_writes_nothing`
  - `test_a_resumed_claim_whose_preview_fails_restores_the_route`
  - `test_a_resumed_claim_whose_preview_answers_503_restores_with_the_outage`
  - `test_a_claim_resumed_during_a_restore_finishes_the_restore`
  - `test_a_probe_that_fails_restores_the_route_to_the_live_release`
  - `test_a_route_never_accepted_is_restored_without_probing`
  - `test_a_refused_switch_is_restored_at_once`
  - `test_a_first_publish_that_fails_points_the_route_offline_and_fails_the_release`
  - `test_a_site_answered_only_by_the_old_backend_is_restored`
  - `test_a_site_answering_503_after_the_switch_keeps_site_not_answering`
  - `test_transient_errors_during_the_restore_are_retried_within_the_deadline`
  - `test_a_restore_that_fails_leaves_the_route_uncertain_until_an_operator_rollback`
  - `test_a_lost_claim_mid_switch_writes_nothing_and_the_next_claim_inspects_the_route`
  - `test_a_claim_resumed_before_the_switch_landed_switches_again`
  - `test_a_resumed_claim_runs_the_preview_again_before_repeating_the_switch`

  In `test_routing_recovery.py`: each promote test that expects a restore after one failure. Keep a test's name
  where its end is the same, so the spec links stay; list every rename in the report for Task 9.
- [ ] 8. `tests/integration/test_routing_store.py::test_promote_with_restore_runs_on_the_store` runs as try 3
  (`execution_attempt` 3 in its claim; docstring names FR-21.1).
- [ ] 9. `promotion_interrupted` meaning: "The last go-live try was interrupted; the next claim restored the route
  and ended the promotion." Regenerate the catalogue table in the runbook from `_table()`.
- [ ] 10. Check `tests/unit/core/domain/rules/test_rollback_uncertain.py` still holds (no change expected).
- [ ] 11. Fast tier and `make py-test-integration` green. The Kind and e2e suites now assert the old rule; Task 10
  updates them, and they must not be run before it.
Gates: fast tier; py-test-integration
Commit: `Routing: a promote makes up to 3 tries, the route restored between them (FR-19, FR-21, FR-22)`

### Task 6: Payment unreachable is a failed try; the try log line

IDs: FR-21.1, FR-21.2, FR-21.4, TR-57 (G10, G14, G15)
Parallel: no
Needs: security (a new log line on the go-live path; the preview token must never appear)
Files: Modify `src/provisioning/core/application/routing/background/routing.py` (`_check_entitlement`, the log line);
Test `tests/unit/core/application/routing/background/test_routing.py`,
`tests/unit/core/application/reconcile/background/test_reconcile.py` (docstring only)
Failing tests first: `test_fr21_2_payment_unreachable_on_try_1_waits_30_s_and_touches_nothing` (no cluster call,
routing `stable`, release `candidate_ready`, `RetryLater(transient_failure, 30 s)`),
`test_fr21_4_payment_unreachable_on_try_2_waits_60_s`,
`test_fr21_1_payment_unreachable_on_try_3_is_attempts_exhausted` (release `candidate_ready`; replaces
`test_entitlement_that_cannot_be_checked_is_transient`), `test_tr57_each_failed_try_logs_one_info_line`
(`caplog`: fields `command_id`, `project_id`, `release_id`, `try`, `of`, `failure_code`,
`next_try_after_seconds`), `test_tr57_the_try_log_line_holds_only_its_named_fields` (no other value, so no token,
credential or site content)
Steps:
- [ ] 1. Write the tests; see them fail.
- [ ] 2. `_check_entitlement(project_id, try_number)`: `LimitReached` stays `PermanentFailure(entitlement_lost)`;
  any other error raises `RetryLater(FAILURE_CODE_TRANSIENT, wait_after_failed_try(n))` for tries 1 and 2 and
  `PermanentFailure(CODE_ATTEMPTS_EXHAUSTED)` on try 3. No route step runs: nothing was touched.
- [ ] 3. A module logger in `routing.py` writes one info line per failed try (route tries and Payment tries), in the
  `key=value` style of the consumer's lines; on try 3 `next_try_after_seconds=none`.
- [ ] 4. Add "FR-21.2: a project whose promote waits between tries is skipped" to the docstring of
  `test_a_project_that_is_not_idle_is_skipped_without_any_cluster_call` (its `changing` and `unfinished-command` cases
  are the G14 state; no code change).
- [ ] 5. Fast tier green.
Gates: fast tier
Commit: `Routing: Payment unreachable is a failed try; one log line per failed try (FR-21, TR-57)`

### Task 7: Worker start checks

IDs: FR-21, FR-22, TR-58 (G12)
Parallel: yes, only in its own worktree (shares no file with Tasks 2 to 6; VIII.1)
Needs: none
Files: Modify `src/provisioning/infrastructure/config/worker.py` (`_check_timing` and its docstring),
`tests/unit/hosts/envs.py` (`worker_env` `COMMAND_DEADLINE` 15m to 20m); Test
`tests/unit/infrastructure/config/test_worker_timing.py`
Failing tests first: `test_tr58_an_attempt_timeout_below_two_switches_and_two_restores_is_refused` (S 60 s, R 60 s,
timeout 230 s: refused naming `ROUTE_SWITCH_DEADLINE` and `ROUTE_RESTORE_DEADLINE`; today's check lets it pass),
`test_tr58_a_command_deadline_below_three_tries_and_their_interruptions_is_refused` (S 20 s, R 60 s, L 15 s, B 10 s:
615 s refused, 616 s accepted; the reason names `ROUTE_SWITCH_DEADLINE`, `ROUTE_RESTORE_DEADLINE`,
`WORKER_LEASE_TTL`, `WORKER_RETRY_BACKOFF_MAX`), `test_tr58_the_longer_of_lease_and_backoff_counts` (B above L
moves the bound), `test_tr58_an_attempt_budget_of_three_is_refused` (3 refused, 4 accepted, reason names
`WORKER_MAX_ATTEMPTS`), `test_tr58_the_prototype_timing_is_accepted` (the values of
`scripts/prototype/run-provisioning.sh`: S 20 s, R 60 s, L 15 s, B 10 s, 15m, 10m, 20)
Steps:
- [ ] 1. Write the tests; see them fail.
- [ ] 2. Per-claim check: `WORKER_ATTEMPT_TIMEOUT` above 2 x S plus 2 x R (replaces today's max form).
- [ ] 3. Command check: `COMMAND_DEADLINE` above `GO_LIVE_TRIES` x (2 x S plus R) plus the sum of
  `wait_after_failed_try(n)` for n from 1 to `GO_LIVE_TRIES` minus 1 plus `GO_LIVE_TRIES` x (max(L, B) plus R).
  No literal 90 s or 3.
- [ ] 4. Budget check: `WORKER_MAX_ATTEMPTS` above `GO_LIVE_TRIES`.
- [ ] 5. Each check uses `loader.ok(...)` on its keys, so a key already refused is not reported twice. Reasons name
  keys, never values.
- [ ] 6. `worker_env` `COMMAND_DEADLINE` becomes `20m`: with its S 60 s, R 60 s, L 30 s, B 30 s the bound is exactly
  900 s, which 15m does not exceed. This is a test fixture value, not a gate.
- [ ] 7. Fast tier green.
Gates: fast tier
Commit: `Config: the worker refuses timing that cannot hold 3 go-live tries (FR-21, TR-58)`

### Task 8: Docs

IDs: FR-21, FR-22, TR-57, TR-58
Parallel: no
Needs: none
Files: Modify `.claude/docs/runbooks/failure-codes.md` (prose outside the catalogue markers: try codes in
`last_failure_code` while a promote waits; `promotion_interrupted`), `.claude/docs/runbooks/observability.md`
(result `try_failed` in the histogram row; the try log line), `.claude/docs/runbooks/production-profile.md` (the
three start checks with today's 615 s; the API's and the worker's `COMMAND_DEADLINE` must be equal),
`.claude/docs/architecture/prototype-deployment-flow.md` (line 63 and the preview paragraph: intent before the
preview), `.claude/docs/architecture/failure-modes.md` (line 100), `.claude/docs/architecture/acceptance-scenarios.md`
(lines 31 and 32; test names after Task 10), `.claude/docs/guides/team-testing.md` (line 315 and the 4b line),
`.claude/docs/decisions/0027-go-live-tries.md` section 5 (what was built and tested; the lead writes it);
`.env.example` comments on `WORKER_ATTEMPT_TIMEOUT` (line 197) and `COMMAND_DEADLINE`, only if the guard hook allows
an edit of that file (else the lead asks the owner)
Failing tests first: none (docs). `test_the_runbook_shows_the_catalogue` must stay green.
Steps:
- [ ] 1. Each doc states the 3-try rule once and links to 0027 or the runbook instead of copying it.
- [ ] 2. Run `grep -nP '[\x{2013}\x{2014}]' <files>` and `awk 'length > 120 {print FILENAME ":" FNR}' <files>` on
  the changed files: no dash; no new prose line above 120.
- [ ] 3. `make py-test` green (the runbook catalogue test).
Gates: docs checks above; py-test
Commit: `Docs: going live takes up to 3 tries in the runbooks, architecture and guide (FR-21, FR-22, TR-57, TR-58)`

### Task 9: Spec notes and test links (spec-writer)

IDs: FR-19, FR-21, FR-22, FR-37, TR-57, TR-58
Parallel: no
Needs: none
Files: Modify `.claude/docs/requirements/FRD.md` (FR-21 and FR-22 "Not built yet" sentences, the FR-21.6 "(not built
yet)" note, the "Drift seen" entry for FR-21 and FR-22), `.claude/docs/requirements/TRD.md` (line 4 "drafts for gate
2", the TR-57 drift line at 177), `.claude/docs/requirements/spec.yaml` (test links of FR-19, FR-21, FR-22, FR-23,
FR-37, TR-57, TR-58: the new test node IDs of Tasks 1 to 7; renamed or removed nodes from Task 5's report)
Failing tests first: none (spec). Owner basis: FR-21 and FR-22 were approved at gate 1; 0027 at gate 3. The notes only
stop saying "not built" once the code is merged.
Steps:
- [ ] 1. Edit only the notes and links named above; no row text changes.
- [ ] 2. `make spec-check`: FR-19, FR-21, FR-22, FR-37, TR-57 and TR-58 list tests that exist; quote the counts.
- [ ] 3. Docs checks (dashes, line length) on the changed prose.
Gates: spec-check; docs checks
Commit: `Spec: FR-21 and FR-22 built, drift closed, test links (FR-21, FR-22, TR-57, TR-58)`

### Task 10: Live suites on Kind

IDs: FR-21.1, FR-21.2, FR-21.4, FR-21.5, FR-21.6, FR-22.1, FR-22.2, FR-37.3
Parallel: no
Needs: Kind. Blocked on the owner's Kind slot (about 7000 MB). Written and committed after the fast tier; run later by
`kind-runner`, the whole `proto-e2e` in one run.
Files: Modify `tests/integration/kubernetes/test_routing_live.py` (step 2), `tests/e2e/test_flows.py` (tests 4 and 4b,
the promote wait, per-test timeouts), `tests/e2e/test_scenarios_promote.py`,
`tests/e2e/faults/test_qa_worker_killed.py`, `tests/e2e/faults/test_recovery_stuck_project.py` (check and docstring)
Failing tests first (written first; seen failing on the first Kind run only if Task 5 were missing, so the review
checks each assertion against the rows instead):
- `test_routing_live.py`: the never-ready candidate runs 3 claims through the use case; after claim 3 R is
  `rolled_back` with `candidate_not_answering`, L still serves, the route is L at this command's number (FR-21.1,
  FR-21.6).
- `test_flows.py` test 4: R ends `rolled_back` with `dependency_unavailable`; every visit is answered by v2 (FR-21.6).
  Test 4b: R ends `rolled_back` with `site_not_answering` after 3 tries (FR-21.1).
- `test_scenarios_promote.py`: new `test_fr21_2_and_fr21_4_the_gaps_between_tries_are_30_s_and_60_s` (polls
  `command_dispatch` through `stack.query` every 200 ms; claim 2 starts at least 30 s, claim 3 at least 60 s, after
  the previous release, less one poll interval); the failed first publish ends `failed` after 3 tries and visits
  between tries get the offline page (FR-22.1, FR-22.2); the unready candidate ends `rolled_back` while v1 keeps
  serving (FR-21.6); a GET of the release returns `rolled_back` and try 3's code (FR-37.3).
- `test_qa_worker_killed.py`: the claim after the kill is try 2; the command ends `succeeded` with
  `execution_attempts` 2 (FR-21.5). Fix line 47, `deployment['live_release_id']`, to `liveReleaseId` (the API returns
  camelCase).
- `test_recovery_stuck_project.py`: the intent is now written before the preview, so the kill lands inside try 1 and
  the expected code stays `deadline_exceeded`; state that in the docstring.
Steps:
- [ ] 1. A failing promote now waits up to the command's 615 s budget: add `FAILED_PROMOTE_TIMEOUT = 900.0` (the
  prototype `COMMAND_DEADLINE`) for promotes expected to fail; raise `pytest.mark.timeout` of those tests to 1500.
- [ ] 2. Fast tier green (type check of the suites).
- [ ] 3. After the owner grants a slot: `kind-runner` runs `make proto-up proto-test-kind proto-run proto-e2e` in one
  run and reports real results. Extra slot time: about 15 minutes (six failing go-lives, each about 2.5 minutes
  longer: two more tries and 90 s of waits).
Gates: fast tier now; Kind (`proto-test-kind`, `proto-e2e`) when the slot is granted
Commit: `Tests: live suites expect 3 go-live tries (FR-21, FR-22)`

Before the final review the lead runs `make py-vuln py-age py-cover` and `make qa-sweep` once (V.3).

---

## Coverage

| ID | What | Tasks | Tests |
| --- | --- | --- | --- |
| FR-19.1 | try 1 passes: intent, preview, switch, `confirm_live` | 5 (kept) | `test_promote_supersedes_the_old_live_release_before_the_candidate_goes_live` (kept); integration `test_a_promotion_writes_the_old_live_superseded_and_the_candidate_live` (kept); Kind step 1 of `test_routing_live.py` (kept) |
| FR-19.2 | switch or preview route not accepted is a failed try | 1, 5 | `test_fr19_2_a_switch_not_accepted_on_try_1_points_back_to_l_and_waits_30_s`; `test_fr19_each_failed_step_gives_its_try_code` |
| FR-19.3 | site not answering is a failed try | 5 | `test_fr19_3_a_site_not_answering_on_try_1_points_back_to_l_and_waits_30_s`; Kind test 4b |
| FR-21.1 | try 3 fails: R `rolled_back` with its code | 5, 6, 10 | `test_fr21_1_try_3_failing_restores_l_and_rolls_back_with_its_code`; `test_fr21_1_payment_unreachable_on_try_3_is_attempts_exhausted`; integration `test_fr21_1_three_failed_tries_roll_back_with_try_3s_code`; Kind `test_routing_live.py`, test 4b |
| FR-21.2 | try 1 fails: route on L, R `promoting`, wait 30 s | 1, 2, 3, 5, 6, 10 | `test_fr21_2_the_wait_after_try_1_is_30_s`; consumer `test_tr57_a_retry_later_with_a_wait_releases_the_claim_for_exactly_that_wait`; `test_fr21_2_a_claim_before_retry_after_is_backoff_and_counts_no_try`; `test_fr21_2_a_failed_try_1_writes_nothing_after_the_intent_and_waits_30_s`; `test_fr21_2_a_restore_after_try_1_not_confirmed_is_route_uncertain_without_retry_later`; `test_fr21_2_payment_unreachable_on_try_1_waits_30_s_and_touches_nothing`; integration `test_fr21_2_after_try_1_the_row_waits_30_s_and_the_release_stays_promoting`; Kind gap test |
| FR-21.3 | try 2 passes | 5 | `test_fr21_3_claim_2_passing_reads_the_route_once_and_goes_live`; integration `test_fr21_3_claim_2_passing_goes_live_with_one_usage_row`; Kind: none (OQ-1) |
| FR-21.4 | try 2 fails: wait 60 s | 1, 5, 6, 10 | `test_fr21_4_the_wait_after_try_2_is_60_s`; `test_fr21_4_a_failed_try_2_waits_60_s`; `test_fr21_4_payment_unreachable_on_try_2_waits_60_s`; integration `test_fr21_4_after_try_2_the_row_waits_60_s`; Kind gap test |
| FR-21.5 | the claim after a stop is the next try | 3, 5, 10 | `test_fr21_5_a_claim_reads_the_last_failure_code_of_its_row`; `test_fr21_5_claim_2_after_a_lapse_points_the_route_back_before_its_preview`; `test_fr21_5_an_unreadable_route_is_pointed_back_before_the_try`; `test_fr21_5_a_resumed_claim_whose_restore_first_is_not_confirmed_is_route_uncertain`; `test_fr21_5_claim_4_on_a_changing_route_ends_promotion_interrupted`; `test_fr21_5_claim_4_on_a_stable_route_is_attempts_exhausted`; consumer `test_shutdown_mid_execution_releases_with_worker_stopping_then_requeues` (kept); integration `test_fr21_5_a_lapsed_claim_is_try_2_and_its_writes_are_refused`; Kind `test_qa_worker_killed.py` |
| FR-21.6 | fresh-start preview failure is try 1, route untouched | 5, 10 | `test_fr21_6_on_a_fresh_start_the_intent_is_stored_before_apply_preview`; `test_fr21_6_a_fresh_start_preview_failure_never_moves_the_route`; `test_fr21_6_try_3_preview_failure_ends_as_fr21_1`; integration `test_fr21_6_a_preview_failure_leaves_the_release_promoting`; Kind test 4, unready scenario |
| FR-22.1 | first deployment, try 3 fails: offline, R `failed` | 5, 10 | `test_fr22_1_no_live_release_try_3_failing_goes_offline_and_fails_with_its_code`; integration `test_fr22_1_three_failed_tries_of_a_first_deployment_fail_the_release`; Kind failed first publish |
| FR-22.2 | first deployment, try 1 or 2 fails: offline, R `promoting` | 5, 10 | `test_fr22_2_no_live_release_try_1_failing_points_an_absent_route_offline`; integration `test_fr22_2_between_tries_of_a_first_deployment_the_release_stays_promoting`; Kind offline page between tries |
| FR-23.1 | an unconfirmed restore is `route_uncertain` (rule unchanged, new paths) | 5 | `test_fr21_2_a_restore_after_try_1_not_confirmed_is_route_uncertain_without_retry_later`; `test_fr21_5_a_resumed_claim_whose_restore_first_is_not_confirmed_is_route_uncertain` |
| FR-37.3 | GET a `rolled_back` release returns `failureCode`; `promoting` none | 4, 5, 10 | integration reads in `test_fr21_1_...` and `test_fr21_2_...`; `test_tr57_a_promote_abandoned_after_a_failed_try_ends_with_that_try_code`; Kind GET; read mapping `test_views.py` (kept) |
| TR-57 | tries, waits, codes, `RetryLater`, abandon, log | 1, 2, 3, 4, 5, 6 | `test_go_live.py` (all); `test_tr57_retry_later_*`; `test_tr57_extend_claim_keeps_the_last_failure_code`; `test_tr57_*` in `test_recovery.py` and `test_consumer.py`; `test_tr57_each_failed_try_logs_one_info_line`; `test_tr57_the_try_log_line_holds_only_its_named_fields`; `test_failure_codes.py` guard with `RetryLater` |
| TR-58 | start checks | 7, 8 | `test_tr58_*` in `test_worker_timing.py`; the equal `COMMAND_DEADLINE` of API and worker is a runbook sentence (Task 8), not a check |
| G14 | reconcile skips a waiting promote; rollback refused | 6 | `test_a_project_that_is_not_idle_is_skipped_without_any_cluster_call` (tagged FR-21.2); FR-50 tests (kept) |
| G16, FR-48 | automatic promote reuses the tries | none | Not built here (TRD Q-5); FR-48.2 to FR-48.4 reuse the FR-21 and FR-22 tests later |

No FR row or TR of the design is without a task. FR-21.3 has no Kind test (OQ-1).

## Pre-flight

| Pair | One produces, the other consumes | Finding |
| --- | --- | --- |
| 1 to 4 | `GO_LIVE_TRY_CODES` | `RouteAbandoner` reads it; defined once in the domain |
| 1 to 5 | `GO_LIVE_TRIES`, `wait_after_failed_try`, the four `CODE_*` constants | `routing.py` already imports the codes after Task 1 |
| 1 to 7 | `GO_LIVE_TRIES`, `wait_after_failed_try` | The config layer may import the domain; the 90 s is computed, not copied |
| 2 to 5 | `RetryLater(code, after)` | `_between_tries` raises it; the consumer releases with `after` |
| 2 to 6 | `FAILURE_CODE_TRANSIENT`, `RetryLater` | Moved to `claims.py` in Task 2, so `routing.py` does not copy the string |
| 2 and 3 | both edit `claims.py` and `core/application/__init__.py` | Sequential, no conflict |
| 3 to 4 | `Claim.last_failure_code` | Unit tests build the claim with the field; the store fills it |
| 3 to 5 | `PgJobStore.claim` fills `last_failure_code` | The integration helper of Task 5 uses the real store |
| 2 and 4 | both edit `consumer.py` and `test_consumer.py` | Sequential, no conflict |
| 5 and 6 | both edit `routing.py` and `test_routing.py` | Sequential; Task 6 changes only `_check_entitlement` and adds the log line |
| 5 to 8 | the catalogue table in `failure-codes.md` | Task 5 regenerates the table; Task 8 edits prose outside the markers only |
| 5 to 9 | renamed or removed test names | Task 5's report lists them; Task 9 updates `spec.yaml` |
| 5, 6 to 10 | the new behaviour | Kind and e2e suites assert the old rule between Task 5 and Task 10; they are not run until Task 10 |
| 7 to 8 | the three start checks | The runbook states the formula and today's 615 s |

## Open questions for the owner

Answered by the lead (owner delegated): OQ-1 a, OQ-4 a, OQ-5 b. Open for the owner: OQ-2 (Notion `commands`
wording) and OQ-3 (production values against the TR-58 checks before the next DOKS deploy); no task waits on them.

| ID | Question | Options | Recommendation |
| --- | --- | --- | --- |
| OQ-1 | FR-21.3 on Kind needs a demo site that fails its first answers, then answers. None exists. | a) no Kind test; unit and integration cover it. b) a new site variant (a new fixture image) | a |
| OQ-2 | The Notion `commands` prose "a failed promote may leave its release `candidate_ready` (preview failed)" now holds only for `entitlement_lost` and Payment unreachable on all 3 tries | a) the owner edits the wording. b) leave it | a; prose only, no schema |
| OQ-3 | Production values are set outside the repo (OD-15). If they fail the new start checks, the worker will not start after the next DOKS deploy | a) the owner checks them against the runbook before that deploy. b) nothing | a |
| OQ-4 | The spec-writer edits the FRD notes and `spec.yaml` (Task 9) under the gate 1 approval of FR-21 and FR-22 | a) that is enough. b) a separate owner ruling | a |
| OQ-5 | The design says the hosting executor's `abandon` returns None, though it calls `RouteAbandoner`, which now returns the code it wrote (for a hosting kind, `route_uncertain` writes the consumer's code) | a) as designed: None, the consumer logs "no outcome written". b) pass the abandoner's result through | b (the log then names the code really written); Task 4 follows the design until the lead rules |
