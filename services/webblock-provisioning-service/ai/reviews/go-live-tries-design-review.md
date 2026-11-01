# Review: go-live tries design

Stage: 2 Design review (independent; the reviewer did not write the design).
IDs: FR-19 (19.1 to 19.3), FR-21 (21.1 to 21.6), FR-22 (22.1, 22.2), FR-37.3; TR-57, TR-58; constitution I.5,
III.3, III.4.
Range: `39add4d..cc190ad` on branch `go-live-tries` (worktree `.claude/worktrees/go-live-tries`).
Files reviewed: `.claude/docs/designs/go-live-tries-design.md`, `.claude/docs/decisions/0027-go-live-tries.md`,
`.claude/docs/requirements/TRD.md` (TR-57, TR-58, "Constitution check", Q-6), `.claude/docs/requirements/spec.yaml`,
`.claude/docs/designs/README.md`. Checked against the code at `cc190ad` and Notion "5 · Provisioning" (fetched,
read-only, page last edited 2026-10-27).

## Verdict

**Approve with fixes.** The mechanism is sound: the claim, `retry_after`, `release_claim` and dispatcher already
carry a per-claim try counter and a named wait with no schema change, and every state move the design uses is one
Notion and the domain outcomes allow. Fix the three important findings (I-1 to I-3) in the design, TR-58 and 0027
before the planner cuts tasks; the minor ones can go into the plan.

## Constitution check of the design

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass, with I-3 | Every decision names its FR or TR; 0027 changes before code. Lead answers Q-1 to Q-3 are not recorded yet and TR-37 conflicts with G3 (I-3, I.3) |
| II Tech stack | pass | No library, image, tool or store added |
| III Architecture limits | pass | III.3: no migration, column, enum or grant; only `command_dispatch` columns the worker already writes (`job_store.py:386-431`). III.4: acceptance and ownership stay in SQL; the command stays `running`; no SQL in `_between_tries`; `release_claim` is one short transaction after the executor returns. III.2: `go_live.py` is stdlib only; `RetryLater` sits in application; consumer stays a mapper |
| IV Coding conventions | pass, with M-1 | One rule module, one use case method; a second retry-later class next to `DeployRetryLater` (M-1) |
| V Testing and quality | pass, with M-6 | Test per acceptance row; one new start check; no gate lowered. Two paths lack a named test (M-6) |
| VI Safety guardrails | pass | Kind only for live tests; no secret read; preview token stays in the adapter (`site_cluster.py:172`, `render.py:509`) |
| VII Docs | minor (M-5, M-8) | Plain English, no dashes found. 19 new table rows above 120 characters while §5 claims "lines within 120"; four docs that state the old rule are missing from §6 |
| VIII Agents | pass | Design only; no code |

TRD "Constitution check" (I.5): present, each article with a reason; accurate for the TRD. The FRD still has no
such section (outside this range; noted for the lead).

## FR and TR coverage

| ID | Design and test named | Status | Evidence |
| --- | --- | --- | --- |
| FR-19.1 | G4, G5; unit, integration, Kind kept | done | `confirm_live` (`outcome.py`), usage row only when a release goes live (`routing_store.py:167-171`) |
| FR-19.2 | G5 table; unit `RetryLater(route_not_accepted, 30 s)`, preview not confirmed gives the same code | done | today a preview timeout raises `Timeout` (`routing.py:613-614`); design maps it to the code |
| FR-19.3 | G5; unit `site_not_answering` | done | `routing.py:646-651` |
| FR-21.1 | G8; unit, integration (3 claims), Kind | done | `_restore` then `restore_verified` (`routing.py:654-676`, `outcome.py` `restore_verified`) |
| FR-21.2 | G6, G7; unit, integration (`BACKOFF` before `retry_after`), Kind gap | done | `claim` refuses while `retry_after` is ahead (`job_store.py:310-311`); `release_claim` sets it (`job_store.py:418-427`) |
| FR-21.3 | G5; unit, integration | done | a `changing` claim skips entitlement and intent |
| FR-21.4 | G6; unit, integration, Kind gap | done | |
| FR-21.5 | G1, G9; unit, integration, Kind | done, with I-2 | `execution_attempts + 1` per claim (`job_store.py:320`); `_stop` releases with `worker_stopping` (`consumer.py:552-569`) |
| FR-21.6 | G4, G6 step 2; unit asserts `apply_route` never called | done | the read of the route at L writes nothing |
| FR-22.1 | G8; unit, integration, Kind | done | `route_removed_verified` requires `promoting` (`outcome.py` lines 164-172) |
| FR-22.2 | G6 step 2 (route absent, pointed `OFFLINE`); unit, integration, Kind | done | `restore_target_of` (`routing.py:183-188`) |
| FR-37.3 | G13; unit, integration, Kind | done | Notion: `failure_code` set exactly when `failed` or `rolled_back` |
| TR-57 | G1 to G16; `go_live.py` and `RetryLater` tests | done, with M-1, M-2 | |
| TR-58 | G12; `test_worker_timing.py` | Drift risk (I-1) | formula is not the worst case |

## State moves against Notion "5 · Provisioning"

- Release: `candidate_ready → promoting` (intent, G4), then `live`, `rolled_back` (live release exists) or `failed`
  (none). All allowed. Between tries the release stays `promoting` (allowed; no `failure_code` while `promoting`).
  Q-3 leaves the release `candidate_ready` with the promote `failed`: Notion says "a failed promote may leave its
  release `candidate_ready`". Allowed.
- Routing: `stable → changing` at the intent, stays `changing` between tries (Notion: "a command is moving the route";
  decision `0001` section 3 invariant 2 needs `changing` or `uncertain` while `promoting`), then `stable` or
  `uncertain`. Allowed.
- Command: `accepted → running` at claim 1, `running` across tries, then `succeeded` or `failed`. Allowed.
- Hosting: untouched. `usage_outbox`: only from `confirm_live` (`routing_store.py:167-171`).
- `command_dispatch`: `execution_attempts` ("successful claims so far"), `retry_after` ("earliest next claim after a
  temporary failure"), `last_failure_code`, `next_publish_at`. Used as Notion describes. No schema change.

## Mechanics checked in the code

- `JobStore.claim` (`job_store.py:262-347`): checks in order held, then backoff, then claims; adds 1 to both
  `claim_number` and `execution_attempts`; a delivery during a wait is `BACKOFF`, acked, and burns no try
  (`consumer.py:299-306`). Confirms G1 and the FR-21.2 integration row.
- `release_claim` (`job_store.py:386-431`): clears the claim, sets `retry_after` and `next_publish_at` from the
  database clock, keeps the command `running`, validates the code, refuses a lost claim. Confirms G7 and G11 with no
  SQL change.
- Dispatcher: republishes a due row (`job_store.py:107-127`); fails only never-claimed rows past the deadline
  (`dispatcher.py:167-168`). A lost message after a wait is rebuilt from SQL.
- Consumer: deadline, then attempt budget, then execute (`consumer.py:347-355`); `_fail` refuses a `changing` route
  (`job_store.py:440-456` via `fail_without_route_change`), so `RouteAbandoner` must end a `changing` promote
  (`recovery.py:136-164`). With `WORKER_MAX_ATTEMPTS` 20, claim 4 reaches the executor (G9 and the new TR-58 check).
- Reconciler skips a project not `stable` or with an unfinished command (`reconcile.py:233`).
- Admin rollback needs `uncertain` (`command_preconditions.py:56-58`); refused while a try waits.
- Hosting events during a wait: "route not stable" or "command in flight" is a transient refusal
  (`accept_hosting_event.py:108-113`); 100 retries with 1 s to 10 s backoff (`.env.example:246-248`) last about
  16 minutes, longer than the go-live.
- Fence: `start_route_change`, `confirm_live`, `restore_verified`, `route_removed_verified`, `route_uncertain`
  (`outcome.py:115-205`) already allow every move; no domain rule change needed.
- Layering: domain rule is stdlib only; application only domain; `presentation` already imports from
  `core.application` (`consumer.py:23-41`); import-linter `layers` contract unchanged (`pyproject.toml:260-270`).
- 0027: needed, because `0014` section 17 item 1 states the opposite rule (a failing preview ends the command,
  release `candidate_ready`) and `0005` section 4 has no "executor names its wait" result. The amendment targets
  (`0014` section 3 "Promote" bullet, section 17 item 1, `0005` section 4 table) are the right ones.
- Settings (prototype `scripts/prototype/run-provisioning.sh:578-593`, `.env.example`): `ROUTE_SWITCH_DEADLINE` 20 s,
  `ROUTE_RESTORE_DEADLINE` 60 s, `WORKER_ATTEMPT_TIMEOUT` 10 m, `WORKER_LEASE_TTL` 15 s, `WORKER_MAX_ATTEMPTS` 20,
  `WORKER_RETRY_BACKOFF_MAX` 10 s, `COMMAND_DEADLINE` 15 m. G12's table is correct. The existing per-claim check is
  at `worker.py:344-348`.
- Every test the design says must change exists (`test_routing.py`, the six names; `test_routing_recovery.py`;
  `test_routing_live.py`; `test_scenarios_promote.py`; `test_flows.py`; `test_recovery_stuck_project.py`).

## Findings

### Critical

None.

### Important

**I-1. The TR-58 formula is not the worst case (TR-58, G12, 0027 T7).** `go-live-tries-design.md:187-189`,
`TRD.md` TR-58 row. G12 adds "2 lapsed claims" to 3 full tries, but a lapsed claim *is* a try (G9), so that term
does not match any real sequence. It also leaves out the restore that claim 4 runs after an interrupted try 3 (G9).
The worst sequence is: try 1 and 2 fail normally (2 x 100 s plus 30 s plus 60 s), try 3 crashes at the end of its
restore (100 s), the claim lapses (15 s), and claim 4 restores (60 s). That is 465 s, against 420 s from the formula,
plus dispatch latency (`DISPATCH_POLL_INTERVAL`, broker) and up to `WORKER_RETRY_BACKOFF_MAX` after a database error.
With 15 m it still fits, but the start check would let a deadline through that cannot hold.
Fix: TR-58, G12 and 0027 T7 use `COMMAND_DEADLINE > 3 x (2 x ROUTE_SWITCH_DEADLINE + ROUTE_RESTORE_DEADLINE) + 90 s
+ WORKER_LEASE_TTL + ROUTE_RESTORE_DEADLINE + WORKER_RETRY_BACKOFF_MAX` (about 475 s today), and G12 states the
worst sequence in words.

**I-2. An interrupted switch leaves the route on an unproven candidate until the next try ends (FR-21, FR-21.5;
G5, G9).** `go-live-tries-design.md:104-106`, `:143-144`. FR-21 says "the route is never left on a release that did
not answer". If a worker stops or crashes after its switch moved the route to R, the route stays on R for the claim
TTL (15 s), the republish, and the next try's preview (up to 20 s), and up to another 20 s if that preview passes and
the switch probe fails. G5 also removes today's resumed-claim read of the route (`routing.py:447-455`).
Fix (pick one, record it in 0027):
a) at the start of any claim with routing `changing`, read `route_status`; when it is not at the restore target,
run the G6 restore first, then the try. Then add one `ROUTE_RESTORE_DEADLINE` to the per-claim check
(`worker.py:344-348`) and to I-1's formula; or
b) state this window in 0027 as an accepted limit with the owner's ruling, next to the existing switch-window limit
in `prototype-deployment-flow.md:85`.

**I-3. The lead decisions are not recorded, and TR-37 now conflicts with TR-57 (I.3, Q-1 to Q-3).**
`go-live-tries-design.md:318-324` and 0027 section 6 still list Q-1 to Q-3 as open; 0027 T1 and T5 still say
"owner question in the design"; `TRD.md` Q-6 is still open. TR-37 says retry budgets are required settings with no
default, which G3 and T1 contradict (code constants). The planner would cut tasks from a design whose rules read as
undecided.
Fix: state the three answers as facts in the design §4 (G3, G9, G10) and 0027 (T1, T5, T6), remove §9 or mark it
answered, move TRD Q-6 to "Answered", and write the exception into TR-57 or TR-37 ("the go-live tries and waits are
fixed by FR-21 in code, like `HOSTING_RETRY_BUDGET`"). In the report to the owner, label them
"Lead decision (owner delegated)".

### Minor

**M-1. A second retry-later class.** G11 adds `RetryLater(code, after)` in `jobs/claims.py`, while
`DeployRetryLater(code)` already exists (`deploys/background/deploy.py:98-104`) and the consumer special-cases it
(`consumer.py:40`, `:158-170`). Two classes for "release the claim with this code" is a second copy of a rule
(`python-code.md`). Fix: one `RetryLater(code, after: timedelta | None)` where None means the generic backoff;
`DeployRetryLater` becomes it or subclasses it; one branch in `_execute`. Or state in G11 why both stay.

**M-2. Fallback code and wait checks in G11.** G11 maps an invalid `RetryLater` code to `executor_failed`, a command
failure code; the transient path uses `transient_failure` (`consumer.py:158-170`). Use `transient_failure`.
`release_claim` refuses a non-positive duration (`job_store.py:391`) and `wait_after_failed_try` returns None after
try 3: say that `_between_tries` runs only for try 1 or 2, and add a unit test that `RetryLater` refuses a None or
zero wait.

**M-3. A wait that runs past the deadline.** If `claim.deadline_at` falls before the next try can run, the design
still waits, then claim n+1 is abandoned with `deadline_exceeded` (`consumer.py:347-350`) and the release ends with
that code, not a try code. Fix: when the deadline cannot hold the next try, end now as the last try (G8 with this
try's code); or record that the deadline wins. `Claim.deadline_at` is on the database clock (`claims.py:109`).

**M-4. What claim 4 rolls back.** If try 3 proved the candidate but its `confirm_live` write failed (a database
error), claim 4 restores and ends `promotion_interrupted`, rolling back a working release. A rolling restart of the
worker also uses up a try (Q-1). Both follow from the lead's Q-1 answer; record them in 0027 section 4.

**M-5. Docs not listed in §6 "What changes".** These still state the one-try rule:
`.claude/docs/architecture/prototype-deployment-flow.md:63` (preview failure keeps `candidate_ready`),
`.claude/docs/architecture/failure-modes.md:100`, `.claude/docs/architecture/acceptance-scenarios.md:31-32`,
`.claude/docs/guides/team-testing.md:315`. Also add the export of `RetryLater` from `core/application/__init__.py`
(the consumer imports from that facade, `consumer.py:23-39`). Notion "5 · Provisioning" `commands` prose ("a failed
promote may leave its release `candidate_ready` (preview failed)") now holds only for the Q-3 path. That is prose,
not schema, so III.3 is not broken; tell the owner in case they want the wording changed (read-only for agents).

**M-6. Test plan gaps.** No named test for: G6 step 3 (try 1's restore not confirmed: `route_uncertain`,
`route_restore_unconfirmed`, no `RetryLater`; FR-23.1); G9 claim 4 with routing `stable` (`attempts_exhausted`,
release `candidate_ready`); G10 Payment unreachable on try 3. Also check `tests/integration/test_routing_store.py`
and `tests/unit/core/domain/rules/test_rollback_uncertain.py`, which use these codes. The plan must budget e2e time:
each failing go-live scenario now waits at least 90 s, and the waits are constants (Q-2), so the `pytest.mark.timeout`
values of the affected e2e tests and the Kind slot time grow.

**M-7. The deadline is checked where it is not written.** The API writes `deadline_at` from its own
`COMMAND_DEADLINE` (`config/api.py:261`); TR-58 checks the worker's copy (`config/worker.py:246`). G12 notes this
and asks for a runbook line only. Fix: a plan task that sets one shared key for both processes in the deployment
config, with a chart test, or the same check in the API's config.

**M-8. Line length (VII.2).** 16 new table rows in the design and 3 in the TRD exceed 120 characters
(`docs-style.md`: "must print nothing for new lines"), while design §5 says "lines within 120". The TRD and FRD
already hold 52 and 96 such rows at the base, so tables look like a working exception. Fix: wrap, or ask the owner to
confirm tables are exempt, and correct the §5 claim.

**M-9. Stale claim and cluster writes.** A worker whose claim just lapsed may still run a `_between_tries` restore
while the next claim switches, at the same command number. The cluster fence cannot tell the two claims apart.
This was already true (`0005` section 4, "What fencing proves"), but G9 makes more claims per promote. Record it in
0027.

## Commands and outcomes

| Command | Result | Notes |
| --- | --- | --- |
| `git diff 39add4d..cc190ad -- .claude/docs/...` | read | design, 0027, TRD, spec.yaml, designs README |
| `git diff --stat 39add4d..cc190ad -- src tests deploy` | 2 files | only `src/contracts/openapi.yaml`, `mcp/tools.json` (commit `4c3bd44`, outside this brief) |
| Notion fetch "5 · Provisioning" | fetched | read-only; state tables and `command_dispatch` fields checked |
| `make contracts-check CONTRACTS_REPO=/home/roa/Documents/web-block/web-block-contracts` | passed | the default `../web-block-contracts` does not resolve from the worktree |
| dash check on changed docs | passed | no U+2013 or U+2014 |
| line length on new lines | 19 over 120 | all table rows (M-8) |
| `make spec-check` | not run | refused by the reviewer hook (not a reviewer target) |
| `make py-fmt-check py-lint py-typecheck py-layers`, `make py-test` | not run | no `src/provisioning` or `tests` change in the range |
| `make chart-check` | not run | no chart change |
| `make py-test-integration py-test-messaging` | not run | no schema, store or broker change; design stage |
| Kind suites | not run | kind-runner only; design stage |

## Skipped checks and blockers

- spec-check could not run from the reviewer (hook); the lead should run it and quote the TR-57 and TR-58 counts.
- The contracts and Bruno edits in `4c3bd44` were not reviewed (outside the brief).
- Security: no auth, RBAC, policy, secret or dependency change; security-reviewer is not required for this design.
  The G15 log test (no preview token in the try log line) should stay in the plan.
- No blocker. No owner question beyond I-2 option b and M-8.

## Re-review: fix round 1

Stage: 2 Design review, fix round 1. Range `cc190ad..b94da0f` (one commit, `b94da0f`). Scope: each finding I-1 to
I-3 and M-1 to M-9, and new problems from the fixes. Lead decisions applied: I-2 option (a), restore first;
table rows may exceed 120 characters, prose may not; Q-1 to Q-3 as written.

### Verdict

**Still open: two small fixes.** I-1 is only partly fixed (the command formula bounds today's values but not every
setting), and the switch to one `RetryLater` silently narrows the failure-code guard test (N-1). Both are one-line
changes to the design, TR-58 and 0027 T7. Everything else is fixed correctly. This was fix round 1 of 2.

### Each finding

| Finding | Status | Evidence |
| --- | --- | --- |
| I-1 timing formula | partly fixed | see "Remaining on I-1" below |
| I-2 route on an unproven candidate | fixed | G9 "Restore first" (design lines 151-155), 0027 T5, TR-57 row; the per-claim check grows to 2S + 2R (G12 lines 213-216, T7, TR-58); unconfirmed restore ends `route_uncertain` |
| I-3 decisions not recorded | fixed | design §9 answered; 0027 §6 "Nothing", T1, T5, T6; TRD Q-6 moved to Answered; TR-57 states the TR-37 exception |
| M-1 second retry-later class | fixed, with N-1 | G11: `RetryLater(code, after=None)` replaces `DeployRetryLater`; T8 |
| M-2 fallback code and wait | fixed | G11: bad code becomes `transient_failure`; `ValueError` for a zero or negative wait; `_between_tries` only for tries 1 and 2 |
| M-3 wait past the deadline | fixed (other way) | T9: `RouteAbandoner` keeps the try code in the release and command |
| M-4 claim 4 and rolling restarts | fixed | design §8 and 0027 §4 "Accepted limit" |
| M-5 docs and facade export | fixed | §6 lists the four docs, the runbook and the `core/application/__init__.py` export; Notion prose left to the owner |
| M-6 test gaps and e2e time | fixed | FR-21.2 row (unconfirmed restore), FR-21.5 row (claim 4 `stable`), technical tests (Payment on try 3), §6 test list, §7 time note |
| M-7 two copies of the deadline | partly fixed (minor) | TR-58 now says "API and worker hold the same `COMMAND_DEADLINE`", but only a runbook line backs it (N-3) |
| M-8 line length | fixed | no new prose line above 120 characters in the changed docs; table rows allowed by the lead; §5 claim corrected |
| M-9 stale claim writes | fixed | design §8 and 0027 §4 "Pre-existing, out of scope" |

Docs checks on the round's new lines: no en or em dash; no prose line above 120 characters. Decision notes at the
top of `0014` and `0005` and the `0027` row in the decisions README are present and correct.

### Checks the coordinator asked for

- **Per-claim formula.** One claim is restore first (R), preview (S), switch (S), restore or between-tries (R):
  2S + 2R = 160 s. A fresh start has no restore-first step and adds only the entitlement call. Claim 4 is
  restore first plus `_restore` (at most 2R = 120 s). The operator rollback is 2R. So
  `WORKER_ATTEMPT_TIMEOUT > 2S + 2R` covers every claim and replaces today's `max(2S + R, 2R)`
  (`worker.py:344-348`). With 10 m: passes. Correct.
- **Command formula.** `3 x (2S + R) + 90 s + L + 2R + B` = 300 + 90 + 15 + 120 + 10 = 535 s today. The arithmetic
  and both quoted sequences are right: 465 s (try 3 interrupted) and 525 s (all three tries interrupted, each adding
  a lapse and the next claim's restore first). See "Remaining on I-1".
- **`Claim.last_failure_code` with no SQL change.** Confirmed. `lock_dispatch` runs
  `select(command_dispatch, clock)` FOR UPDATE, which already returns every column (`fenced.py:58-66`).
  `JobStore.claim` already holds that row (`job_store.py:274`). Only `DispatchRow` (`fenced.py:34-44`) and
  `_dispatch_of` (`fenced.py:197-213`) gain the field, and `Claim` is built in two places (`job_store.py:336`,
  `:375`). `claim` does not touch `last_failure_code` (`job_store.py:315-326`), so a claim sees the last code written
  before it.
- **`RouteAbandoner` keeping a try code.** Sound. Notion types `releases.failure_code` as VARCHAR(100), set exactly
  when `failed` or `rolled_back`, with no value list. The five codes are release and command codes in the catalogue
  (`failure_codes.py:226-261`). `restore_verified` and `route_removed_verified` accept any valid code. See N-2 and
  N-4 for two details.
- **`RetryLater(code)` in place of `DeployRetryLater`.** Same behaviour for deploy and redeploy if `_execute` keeps
  today's order: claim lost first, then `PermanentFailure`, then `ClaimLost`/`StaleFence`, then the attempt-timeout
  stop before the code (`consumer.py:425-449`, `:158-170`). With `after=None` the delay stays
  `retry_backoff.delay(execution_attempt)` and the metric stays `transient_failure`. The raise sites are
  `deploy.py:313`, `deploy.py:348`, `redeploy.py:239`; the test sites are `test_deploy.py`, `test_deploy_executor.py`,
  `test_redeploy_executor.py`, `test_consumer.py`, and `test_failure_codes.py` (N-1). The only visible change is the
  type name in the consumer's log line (`error=RetryLater`).

### Remaining on I-1 (important, still open)

`design.md:217-218`, 0027 T7, TRD TR-58. The formula covers 525 s today only because the 90 s of waits are large
next to the other settings. An interruption adds I = max(L, B) + R: the lapse or backoff, then the next claim's
restore first. It also replaces that try's wait. So the worst case is
`3 x (2S + R) + max(30 s, I) + max(60 s, I) + I`. The design's formula is at least that only while
`2L + R <= 90 s + B`. Example: R = 90 s with L = 15 s and B = 10 s. The worst case is 3 x 130 + 3 x 105 = 705 s,
but the formula gives 390 + 90 + 15 + 180 + 10 = 685 s. The worker would start, and a promote could hit its deadline
before try 3 (FR-21: 3 tries).
Fix: use `COMMAND_DEADLINE > 3 x (2 x ROUTE_SWITCH_DEADLINE + ROUTE_RESTORE_DEADLINE) + 90 s
+ 3 x (max(WORKER_LEASE_TTL, WORKER_RETRY_BACKOFF_MAX) + ROUTE_RESTORE_DEADLINE)`. That is a simple upper bound,
615 s today, still inside 15 m. Or write the exact max form above. Same text in G12, T7 and TR-58.

### New findings

**N-1 (important). The failure-code guard would stop checking `RetryLater` codes.**
`tests/unit/core/domain/values/test_failure_codes.py:52` scans the source for failure codes passed to the calls in
`_FAILURE_CALLS = {"Failure", "PermanentFailure", "DeployRetryLater"}` (docstring lines 13-14). After the rename,
no call matches `DeployRetryLater`, and `RetryLater` is not in the set. The test still passes but no longer checks
the codes of the deploy, redeploy and go-live retries against the catalogue. That weakens a guard (CLAUDE.md
section 6). §6 lists "the consumer and deploy tests that name `DeployRetryLater`" but not this one.
Fix: name `test_failure_codes.py` in §6 and plan it: `_FAILURE_CALLS` gets `RetryLater` in place of
`DeployRetryLater`, and the docstring changes to match.

**N-2 (minor). `GO_LIVE_TRY_CODES` would be a second copy of the code strings.** G3 puts the five codes in
`core/domain/rules/go_live.py`. Their constants live in the application today (`routing.py:93-99`; only
`CODE_DEPENDENCY_UNAVAILABLE` is in the domain), and the domain cannot import them. Fix: move the
`CODE_ROUTE_REFUSED`, `CODE_ROUTE_NOT_ACCEPTED`, `CODE_CANDIDATE_NOT_ANSWERING` and `CODE_SITE_NOT_ANSWERING`
constants into `go_live.py`, then build the set from them and have `routing.py` import them. One copy then serves
both.

**N-3 (minor). TR-58's equal-deadline clause has no check.** TR-58 now requires the API and the worker to hold the
same `COMMAND_DEADLINE`, but only a runbook line (`production-profile.md`, not yet written) backs it. A TR row
should be testable. Fix: either a plan task with a test (the prototype run script, or the deployment config, sets
one value for both processes), or move the sentence out of TR-58 into the runbook.

**N-4 (minor). The abandon path's code and logs.** T9 does not say which code the `route_uncertain` branch of
`RouteAbandoner` writes (`recovery.py:161-164`): today it is the consumer's `deadline_exceeded`. The consumer then
logs "abandon hook ran (failure_code=deadline_exceeded)" and "command failure not applied
(failure_code=deadline_exceeded)" (`consumer.py:387`, `:399-406`), even when the try code was written. Also, a stop
or database error after a try code overwrites `last_failure_code` (`worker_stopping`, `transient_failure`), so T9
then falls back to `deadline_exceeded`. Fix: one line in T9: the unconfirmed branch keeps today's code, and the code
written is the last try code still in the row. The consumer log names the abandoner's result or says the hook chose
the code.

**N-5 (minor). Defaults in `Claim`.** `Claim` is built in 8 test files. G12 does not say that
`last_failure_code: str | None` comes last with the default None, nor that `extend_claim` copies it
(`job_store.py:375-382`). State both, so existing tests and the heartbeat keep the value.

### Commands

| Command | Result | Notes |
| --- | --- | --- |
| `git diff cc190ad..b94da0f` (designs, decisions, requirements) | read | 7 files; the review file was committed by the lead, unchanged since |
| dash and prose-length check on new lines | passed | no dash; no prose line above 120 |
| code reads: `fenced.py`, `job_store.py`, `consumer.py`, `recovery.py`, `deploy.py`, `redeploy.py`, `test_failure_codes.py` | done | cited above |
| `make py-*`, `chart-check`, integration, messaging, Kind | not run | docs-only round, no code change |
| `make spec-check` | not run | not a reviewer target (hook) |

### What must change for approval

1. I-1: the command formula in G12, 0027 T7 and TR-58 as given above.
2. N-1: list `test_failure_codes.py` (`_FAILURE_CALLS`) in §6.

N-2 to N-5 can go into the plan as written. This is fix round 1. If I-1 or N-1 is still open after round 2, the
task goes to the owner (I.4).
