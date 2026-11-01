# Security review: Phase C Task 12, scoped re-review of security fix round 3 (N1, L1, L2, runbook)

Stage 5 Verify, Phase C exception: cites decision 0023 G17, G18c, G20a instead of FR and TR IDs.
Date 27 October 2026. Branch `phase-c`, commits 9b332c2 (N1), 1d010b9 (L2), 0fd7302 (L1), fa36a7d (runbook),
2622276 (handoff). Reviewer: security-reviewer, Claude Opus 5.5. Read only; nothing in the repository edited or
committed. Earlier report: `ai/reviews/phase-c-task-12-security-rereview.md`.

## Method

- Read: the full diff of the five commits; at head `postgres/hosting_insert.py`, `hosting_intake.py`,
  `intake_store.py` (take and `_insert_release`), `postgres/tx.py` (isolation level), `postgres/errors.py`,
  `release_intake.py`, `workers/inbox.py`, `site-route-shape.yaml`, the 00017, 00020, 00022 grants and triggers on
  `project_hosting`, the runbook section "Migration 00024", `rabbitmq/dlq.py`, `rabbitmq/client.py`, aiormq 10.1.0
  `channel.py` (return and confirm handling), the Gateway API v1.6.1 CRD in `bin/kind-state/proto/cache/`.
- Skills: differential-review and sharp-edges reasoning applied by hand.
- Ran:
  - `uv run pytest -q tests/unit`: 6717 passed.
  - `uv run pytest -q tests/integration/test_owner_status_store.py tests/integration/test_schema_00024.py
    tests/unit/core/application/test_release_intake.py tests/unit/workers/test_inbox.py
    tests/unit/deploy/test_route_shape_policy.py`: 168 passed (the three N1 race tests included, 12 rounds each).
  - Flaky test: the named test alone 8 times (8 passed); `tests/messaging/test_dlq.py` 10 times (1 failure, at
    line 199, a different test); `tests/messaging` 4 times (1 failure, again line 199). Two throwaway probes in the
    scratchpad (not in the repo) that import the test helpers: 40 failed re-drives, then 540 listings, each followed
    by an immediate depth read and, on a zero, a poll. No zero read and no loss in that unloaded setting.
- Not checked: no Kind or cluster command (brief), so the L1 CEL rule and the two new Kind cases are reasoned, not
  observed. `make py-vuln py-age` and `make image-scan` not run: no lock, pin or image change.

## Status of the earlier findings

| ID | Status | Evidence |
|----|--------|----------|
| N1 | Closed | `hosting_insert.py:33-51`. See "N1 in detail" below. |
| L2 | Closed | `release_intake.py:395-398, 442-465`: `SubdomainTaken` is an ERROR row while `attempts < max_attempts`, then a conditional `fail(take, repository_unconfirmed)` with reason `subdomain_taken`; `attempts` grows on every take (`intake_store.py:215`), so the registry is called at most `max_attempts` times. `inbox.py:299-308` logs at error the IDs, attempt, code, reason and `type(error).__name__` only. Unit tests cover within budget, at and past budget, superseded and a failed write. |
| L1 | Closed | `site-route-shape.yaml:135-139`. With the filter rule at 120-131 (a `RequestHeaderModifier` must be exactly `remove: ['X-Webblock-Preview']`, only on `isPreview`), "every rule has filters and exactly one `RequestHeaderModifier`" means every preview rule removes the header. `exists_one` is a standard CEL macro, `variables.rules` is `[]` when absent, and `size > 0` closes the empty case (the CRD defaults rules anyway). A site route is untouched (`!isPreview ||`). See new finding R1 for one of its Kind cases. |
| Runbook | Closed | `docs/runbooks/phase-1-database.md:171-194`. See "Runbook step" below. |

### N1 in detail

- **Equivalence of "own row exists" with "who owns the sub-domain".** The check runs only after a unique violation
  of `project_hosting_subdomain_key`, so some committed row holds the sub-domain and this insert wrote nothing (the
  savepoint is rolled back). Two cases:
  - Own row present with the same sub-domain: the same-project race. Returning False is exactly what
    `ON CONFLICT (project_id) DO NOTHING` would have said.
  - Own row present with a different sub-domain, and another project holds this one: also returns False. Nothing
    is written to either row, and the caller goes on with the project's own row. That is the same result the
    pre-check would have given had it seen the own row first. No takeover is possible: nothing writes the other
    project's row, and `subdomain` stays set-once (00020 guard).
  - No own row: another project holds the name, so `SubdomainTaken`. Correct.

  So the check is safe and as strict as needed. Reading `subdomain` would add nothing, and the intake login can
  read only `project_id` (00022:177).
- **Visibility.** Writes run in READ COMMITTED (`tx.py:127`). A unique violation is raised only after the
  conflicting transaction committed, so the next statement's snapshot sees that row. In REPEATABLE READ this would
  give a false `SubdomainTaken`, but that is fail-closed, and it is not the case here.
- **Savepoint.** `async with conn.begin_nested()` rolls back to the savepoint when the insert raises, so the outer
  transaction stays usable for the SELECT and the rest. Any other `DBAPIError` is re-raised and mapped by
  `Transactions.run` (`map_db_error`, no driver text).
- **No driver text.** `SubdomainTaken(...) from None`. Its message names only the caller's own `project_id`,
  never the sub-domain or the other project. The test checks `__cause__ is None` and that the name is absent. Both
  consumers log the type name only.
- **Test-only trigger.** `_install_insert_barrier` exists only in `tests/integration/test_owner_status_store.py`.
  No match under `src`, `deploy`, `scripts` or the migrations. It is created as admin in the per-test database
  (`conftest.py:77`, `new_db`), and that database is dropped after the test. The harness fails the run on
  leftovers. It cannot reach production code or the migration chain.

### Runbook step

- `SET LOCAL session_replication_role = replica` lasts only until that transaction's COMMIT or ROLLBACK. Run
  outside `BEGIN`, it only warns and has no effect, so the guard then refuses the UPDATE (fails closed).
- What it skips for that one UPDATE:
  - `project_hosting_reasons_guard`: the intent.
  - `project_hosting_generation_step`: generation columns are not touched.
  - `project_hosting_generation_has_command`: its WHEN clause needs `last_command_number` to change.
  - FK RI triggers: no key column changes.
- CHECK `project_hosting_subdomain_check` still applies. `WHERE ... AND subdomain = '<wrong value>'` and
  "expect UPDATE 1" bound the change. The guard is never altered.
- A collision with a concurrent insert between steps 2 and 3 is caught by step 4: 00024's guard re-checks for
  duplicates.
- See I2 for the superuser prerequisite.

## New findings

### Low

**R1. The Kind case "preview-route-with-the-header-removal-twice" will fail when Task 9 runs it, for a test
reason.** `tests/integration/stack/test_site_policies_live.py:438-441`, with `assert_refused` at 56-59 expecting
`ValidatingAdmissionPolicy 'webblock-site-route-shape'` in stderr.
- Cause: the Gateway API v1.6.1 CRD itself refuses a repeated filter (`RequestHeaderModifier filter cannot be
  repeated`, `bin/kind-state/proto/cache/gateway-api-v1.6.1-standard-install.yaml:6811-6813`). CRD schema
  validation runs before validating admission, so the API server refuses the route before the policy is called,
  and the stderr names the CRD rule, not the policy.
- Security impact: none. The route is refused either way, and the "exactly one" half of `exists_one` is already
  enforced by the CRD.
- Fix, either of:
  - Drop that case.
  - Let it accept either refusal, for example a variant of `assert_refused` that also takes `cannot be repeated`.

  Keep "preview-route-without-the-header-removal", which is the case that proves L1.

### Informational

- **I1. Owner-status vs release intake with different sub-domains.** In the race where the owner-status event and
  the release intake carry different sub-domains for one new project, whichever insert wins sets the row. The
  loser goes on with the winner's value, as with a sequential ON CONFLICT. This is not a security issue, since no
  other project's row changes. It is the existing Project-answer consistency question (S7, with the owner).
- **I2. The runbook needs a database superuser.** On CNPG, superuser access is usually off
  (`enableSuperuserAccess: false`). On PostgreSQL 15 or later, the narrower alternative is a one-off
  `GRANT SET ON PARAMETER session_replication_role TO <admin role>`, revoked afterwards. Optional: name this in the
  runbook. Not a vulnerability.

## The flaky DLQ test: a test race, not a product bug

- **Observed.** The named test did not fail in 62 runs here (8 alone, 14 in file runs, 40 probe runs). The flake
  that did show up is the sibling `test_listing_more_often_than_the_delivery_limit_loses_nothing`:
  `assert 0 == 1` at `tests/messaging/test_dlq.py:199`, in 2 of 14 runs of the file or suite. Both tests end
  with the same pattern: a `DeadLetterTool` session closes with the parked message taken and not acknowledged,
  and the test then reads `queue_depth(...) == 1` at once (`test_dlq.py:199` and `:238`).
- **Why it is a race.**
  - `queue_depth` (`tests/messaging/_broker.py:169-178`) is the passive declare's `message_count`, which counts
    ready messages only.
  - Both queues are quorum queues (`config.py:190-196`). Returning a message that a closed channel held
    unacknowledged is a Raft command the queue applies after the connection's close is answered, so a declare on
    another connection can briefly see 0. Under load (full suite, other tests restarting the broker app) that
    window is wider, which matches failures that show only in suite runs.
  - `park` already polls (`wait_for`) for the same reason. These two assertions do not.
- **Why it is not a product bug.**
  - The code acknowledges the parked message only after the broker confirmed the copy (`dlq.py:441-451`).
  - A returned copy raises `PublishRefused("publish_returned")` from aiormq's return handling, which matches the
    return to the publish by `message_id`; the confirm that follows is ignored. That leads to
    `RedrivePublishFailed`, and the message is never acked.
  - In the listing test, the next iteration's `(item,) = await tool.list(...)` would fail if the message were
    really gone. Passing runs do 45 of them, and the probe did 540 with no loss.
  - A real loss would also need an ack, and none is sent on these paths.
- **Fix (test only).** Replace the immediate depth asserts after a session close with a bounded poll, for example
  `await wait_for(lambda: depth_is(broker, q, 1), within_seconds=5)` at `test_dlq.py:199`, `:216-217` and `:238`.
  Better still for 238: assert by content, `(item,) = await tool.list(names.dead_letter_queue, 5)` and
  `item.message_id == sent.message_id`, which proves the message is still parked rather than counting it. Leave
  the `== 0` asserts after a successful re-drive as they are (the ack round trip in `dlq.py:541` makes them
  settled), or poll them too for symmetry.
- **Possible cause of failures in this test specifically (not proven).** If it was the named test that failed in
  the implementer's runs, the most likely line is 238, by the same mechanism. A `DID NOT RAISE` there would point
  elsewhere: the work queue would have to be redeclared. The `BrokerClient` declares topology only on (re)connect
  (`client.py:303-361`) and has no consumer in this test, so that would need a reconnect during the test. If
  someone captures a failure trace, check which line failed.

## Checked and clean

- Tokens, x-user-id, RBAC, network policies: not touched by this round.
- Secrets: no value in the diff. The runbook uses placeholders only.
- Logs: the new error log in `inbox.py:299-308` and the `SubdomainTaken` messages carry IDs, codes and type names
  only. There is no sub-domain, driver text or parameter in them.
- Grants: no migration change. The new SELECT uses the existing `SELECT (project_id)` grant of the intake login
  (00022:177), and the integration test runs it as `ROLE_INTAKE`.

Verdict: approved. N1, L1, L2 and the runbook point are closed. R1 is a low test-only fix for Task 9's Kind slot,
and the DLQ flake is a test race with a test-only fix. Neither blocks.
