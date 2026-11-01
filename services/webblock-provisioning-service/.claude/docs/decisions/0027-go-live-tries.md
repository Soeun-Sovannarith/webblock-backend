# 0027: Going live takes up to 3 tries

Status: Accepted. TRD: TR-57, TR-58. FRD: FR-19, FR-21, FR-22, FR-37 (row FR-37.3); FR-48 reuses it. Design:
`.claude/docs/designs/go-live-tries-design.md`. It amends `0014` section 3 ("Promote") and section 17 item 1 (a
failing preview no longer ends the command), and `0005` section 4 (an executor may name the wait before its next
claim).

## 1. Context

The FRD (approved at gate 1) asks that one `promote` command tries the go-live up to 3 times, with waits of 30 s
and 60 s, the route back on the last good page between tries, and the release ended only after try 3 (FR-21,
FR-22). The code ends the command after the first failed try: `Routing.promote` in
`core/application/routing/background/routing.py` restores the route at once, and on a fresh start a failing preview
raises `PermanentFailure`. Record `0014` section 17 item 1 states that second rule, so it must change before the code.

## 2. Constraints

- No database change: no migration, column, index, enum value or grant (constitution III.3). The try counter is
  `command_dispatch.execution_attempts` and the wait is `command_dispatch.retry_after` (owner).
- Only release moves Notion "5 · Provisioning" allows: `candidate_ready` to `promoting`, then `live`, `rolled_back`
  (a live release exists) or `failed` (none). A promoting release needs routing `changing` or `uncertain` (`0001`
  section 3 item 2).
- PostgreSQL is the authority; no transaction open across a remote call; one command in flight per project
  (constitution III.4). Every worker write keeps the fence of `0005` section 4.
- FR-21: the route is never left on a release that did not answer.

## 3. Decisions

- **T1. Each claim of a promote is one try.** Try n is `Claim.execution_attempt` (`command_dispatch.execution_attempts`
  after the claim). The domain rule `core/domain/rules/go_live.py` holds `GO_LIVE_TRIES = 3`, the waits 30 s after
  try 1 and 60 s after try 2, the try code constants (moved from `routing.py`, which imports them) and
  `GO_LIVE_TRY_CODES` built from them. They are code constants, not settings: FR-21 fixes the
  numbers, so TR-37's "retry budgets are required settings" does not apply to them, as for `HOSTING_RETRY_BUDGET`.
- **T2. The intent comes first.** On a fresh start (routing `stable`) the worker rechecks entitlement, then writes
  `start_route_change` (release `promoting`, routing `changing`), then runs the preview. A failing preview is a
  failed try, never `PermanentFailure`. `entitlement_lost` still ends the command with the release
  `candidate_ready` and nothing touched.
- **T3. Between tries nothing moves in SQL but the dispatch row.** After a failed try 1 or 2 the worker makes sure
  the route is accepted at the restore target (`live_release_id`, or the offline page without one): it reads the
  route and points it there only when it is not there already. Then the executor raises `RetryLater(code, wait)`
  and the consumer calls `JobStore.release_claim`: claim cleared, `retry_after` and `next_publish_at` = now plus the
  wait, `last_failure_code` = the try's code. The release stays `promoting`, routing stays `changing`, the command
  stays `running`, `live_release_id` is unchanged. A restore not confirmed is `route_uncertain` (FR-23).
- **T4. Try 3 ends the command.** A failed try 3 restores the route with this command's number, confirms it, and
  writes `restore_verified` (release `rolled_back`) or `route_removed_verified` (release `failed`) with try 3's code;
  the command ends `failed`.
- **T5. A claim on a changing route restores first; an interrupted try counts.** Any claim of a promote that finds
  routing `changing` first puts the route on the restore target (read, then point and confirm only when needed),
  before any new try. A try whose worker stopped, crashed, lost its claim, hit a database error or ran out of
  `WORKER_ATTEMPT_TIMEOUT` counts as a try with no recorded code, and the next try has no wait. A fourth claim
  (try 3 interrupted) runs no try: with routing `changing` it ends with `promotion_interrupted`; with routing
  `stable` with `attempts_exhausted`.
- **T6. Every remote failure inside a try is a failed try.** A preview route not confirmed in time is
  `route_not_accepted`. Before the intent, an entitlement recheck Payment cannot answer is a failed try: tries 1
  and 2 wait like any try; on try 3 the command ends `failed` with `attempts_exhausted` and the release stays
  `candidate_ready` (Notion allows a failed promote to leave it there).
- **T7. Timing is checked at start.** The worker refuses to start unless `WORKER_ATTEMPT_TIMEOUT` is above
  2 x `ROUTE_SWITCH_DEADLINE` plus 2 x `ROUTE_RESTORE_DEADLINE` (one claim: restore first, preview, switch,
  restore); `COMMAND_DEADLINE` is above 3 x (2 x `ROUTE_SWITCH_DEADLINE` plus `ROUTE_RESTORE_DEADLINE`) plus 90 s
  plus 3 x (max(`WORKER_LEASE_TTL`, `WORKER_RETRY_BACKOFF_MAX`) plus `ROUTE_RESTORE_DEADLINE`), 615 s today; and
  `WORKER_MAX_ATTEMPTS` is above 3. Reason: an interruption costs a lapse or a backoff and then a restore, and it
  can happen on each of the 3 tries. The runbook `production-profile.md`, not a start check, says the API's and the
  worker's `COMMAND_DEADLINE` must be equal: one process cannot check the other's value.
- **T8. One retry-later result.** `RetryLater(code, after=None)` in `core/application/jobs/claims.py` replaces
  `DeployRetryLater`: None means the generic `WORKER_RETRY_BACKOFF`, a set wait must be positive, and a code that
  is not valid becomes `transient_failure`.
- **T9. A promote past its deadline keeps its try code.** When `RouteAbandoner` confirms the restore of a promote
  whose dispatch row still holds a code of `GO_LIVE_TRY_CODES`, the release and the command end with that code
  instead of `deadline_exceeded` or `attempts_exhausted`. Otherwise, also when a later `worker_stopping` or
  `transient_failure` overwrote the try code in the row, they end with the consumer's code as today. An unconfirmed
  restore writes `route_uncertain` with the consumer's code, as today. `abandon` returns the code it wrote, and the
  consumer's "abandon hook ran" log line names that code. Notion types `releases.failure_code` as VARCHAR(100) with
  no value list; these codes are release codes in the catalogue already. `Claim.last_failure_code` is its last
  field, default None, and `extend_claim` copies it.

Options and cost if wrong, one line each:

- T1: settings instead of code constants: more knobs, and the FR's numbers could drift; cost if wrong: one more
  reviewed change to make them settings.
- T2: keep the preview before the intent and write the intent on the failure: two code paths for one rule; cost if
  wrong: none to data, only a reordering.
- T3: routing `stable` between tries: forbidden by `0001` section 3 item 2 while a release is `promoting`.
- T5: no restore first, only after the next try fails: shorter claims, but the route may stay on an unproven
  candidate for a claim lapse and a preview after a crash, against FR-21; cost of the choice: one restore deadline
  more per claim and in the command deadline.
- T5: enforce the wait after an interrupted try too: needs state a crash cannot record; cost if wrong: a try may
  start 30 s or 60 s early after a crash.
- T9: keep `deadline_exceeded`: simpler, but the client would not see why the go-live failed.

## 4. Consequences

- Code: `core/domain/rules/go_live.py` (new); `core/application/jobs/claims.py` (`RetryLater`,
  `Claim.last_failure_code`) and its export from `core/application/__init__.py`; `deploys/background/deploy.py` and
  `redeploy.py` (`DeployRetryLater` removed); `routing/background/routing.py` (`Routing.promote`);
  `routing/background/recovery.py` (`RouteAbandoner`; `abandon` returns the code it wrote, None
  in the other executors); `infrastructure/postgres/fenced.py` and `job_store.py`
  (the claim reads `last_failure_code`, no SQL change); `presentation/messaging/consumers/consumer.py`;
  `infrastructure/config/worker.py`; `infrastructure/telemetry/metrics.py` (result `try_failed`);
  `core/domain/values/failure_codes.py` (the meaning of `promotion_interrupted`).
- Tests and docs: the design lists them. Every failing go-live in the live suites now waits at least 90 s.
- Other services: none. No contract change; Payment still hears only of a release confirmed live.
- Cost if wrong: a go-live takes at most about 10 minutes (615 s) before it ends; a stuck candidate costs 3
  tries instead of 1.
- Accepted limit: if try 3 proved the candidate but its `confirm_live` write failed (a database error), claim 4
  restores and ends `promotion_interrupted`, rolling back a release that worked.
- Accepted limit: a rolling restart of the worker during a go-live uses up a try.
- Pre-existing, out of scope: a worker whose claim just lapsed may still write the route while the next claim works,
  at the same command number; the cluster fence cannot tell two claims apart (`0005` section 4, "What fencing
  proves"). More claims per promote make it a little more likely.

## 5. What was built and tested

- The rule: `core/domain/rules/go_live.py` (3 tries, waits of 30 s and 60 s, the try codes once).
- One `RetryLater(code, after)` for deploy, redeploy and go-live; the consumer releases the claim for `after`.
- A claim carries the dispatch row's `last_failure_code`; an abandoned promote keeps its last try code.
- `Routing.promote`: restore first on a resumed claim, preview, switch, the route restored between tries, try 3
  ends as FR-21.1 or FR-22.1; Payment unreachable is a failed try; one info log line per failed try.
- The worker refuses to start with timing that cannot hold 3 tries (TR-58).
- Tested: unit and contract tests, the PostgreSQL integration suite (`tests/integration/test_go_live_tries.py`) and the
  RabbitMQ messaging suite. The Kind (k3d) and end-to-end suites still assert one try; they change and run in the
  phase's cluster run.

## 6. What stays open

Nothing. The lead answered the design's questions under the owner's delegation: an interrupted try counts and the
next try has no wait (T5); the tries and waits are code constants (T1); Payment unreachable on all 3 tries ends
`attempts_exhausted` with the release `candidate_ready` (T6); a claim on a changing route restores first (T5).
