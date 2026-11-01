# Final review: claude-makeover ed5fcaf..2bee190

Stage 5 Verify, final review on the owner's fast path (real defects only). IDs: FR-13, FR-32, TR-13, TR-27, FR-31.
Worktree `.claude/worktrees/claude-makeover`, head `2bee190`.

Verdict: needs a fix wave

One doc-only commit closes it: several pages still say task 8 is not built, the contracts have no remote, and the
pin is `afff924`. The code, the tests and the spec rows for FR-13 are right. No Gradle rerun is needed after the fix
wave, because it changes no file under `src`, `build.gradle`, `gradle/` or `contracts/`.

## Evidence

| Check | Commit | Result |
| --- | --- | --- |
| Cherry-picked code equals the reviewed code | `208a073` vs `2bee190` | `git diff 208a073 HEAD -- src build.gradle contracts contracts.lock scripts` is empty |
| Non-doc files changed on the branch | `ed5fcaf..2bee190` | only task 8 files (`AccessAction`, `AccessCheckService`, 4 tests, `build.gradle` test input, `contracts*`, `contracts-sync.sh`) and `README.md` |
| spotless, test, integrationTest, coverage (`./gradlew check --rerun-tasks`) | `2bee190` | not run by me (brief: the lead runs it and adds the result) |
| OSV | | not run; no dependency change (`build.gradle` adds only a test input dir) |
| test_guard, test_hooks, contracts-sync --check, spec-check | `2bee190` | lead's run: 693/693, 215/215, clean, 0 errors; not rerun by me |
| DB audit | `ai/reviews/db-notion-audit-claude-makeover.md` | 0 DIFFERENT; current: no migration or entity changed since (`git diff c8ff16f HEAD` on db and entities empty) |
| Task 8 review | `ai/reviews/manage-site-task-8-review.md` | approved on re-review; code unchanged since (row 1) |
| Guard security review | `ai/reviews/claude-makeover-guard-security-review.md` | re-review 3: reviewer ran no probes ("review incomplete"); "ready" rests on the lead's 33 probes |
| Kind | | not run; no contract change on the wire beyond the action list, owner slot needed |
| DIRECTION.md | `2f66fa3` | updated; counts add up; no Drift on a Must item beyond FR-9.2 and FR-18, which the owner carries (R-5) |
| Docs style on changed `.md` (outside vendored skills) | `2bee190` | no em or en dash, no line over 120 |

## Spec coverage

| ID | Where built | Test | Status |
| --- | --- | --- | --- |
| FR-32 (32.1 to 32.3) | `AccessAction.java` (`MANAGE_SITE`, write), `AccessCheckService.java` | `AccessCheckServiceTest`, `AccessCheckControllerTest`, `AccessGrpcServiceTest` (FR-32.x tagged) | done |
| TR-27 | `contracts.lock` `18dd301`, `contracts/access/actions.v1*.json`, `build.gradle` test input | `AccessActionListTest` | done |
| FR-31 | unchanged gRPC server; guard and constitution work cites it | existing FR-31 tests | done |
| FR-13 (13.1 to 13.4) | `Subdomain.java`, `PlatformProperties.java` (unchanged) | existing FR-13 tests | done |
| FR-13 (13.5 to 13.12) | not built; spec only (`52f55f5`) | none; FRD, DIRECTION, handover item 1 and next.md all say not built | In progress (correct) |
| TR-13 | `.claude/hooks/guard.py`, `test_guard.py` | `test_guard.py` 693 (lead's run) | done |

FR-13 rows claim no code: FRD:120 says "FR-13.5 to FR-13.12: no test yet", DIRECTION marks FR-13 In progress, and
handover item 1 says "nothing is built". The BRD and DIRECTION conflict resolution is sound: BRD drops the
`manage_site` Drift and closes Q-2; DIRECTION's BR, PR, FR and TR counts match the row changes (FR-32 and FR-30 to
Done, FR-13 to In progress, TR-27 to Done).

## Constitution

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven | pass | every commit cites IDs; FR-13 spec changed first, code left for later; task 8 within 2 fix rounds |
| II Tech stack | pass | no dependency change; v5 first-party exemption is owner-confirmed; CLAUDE.md:181 says so |
| III Architecture | pass | no schema change; DB audit 0 DIFFERENT and current; S1 vs FR-13 length recorded as Drift (FRD Drift seen) |
| IV Coding | pass | task 8 code reviewed and unchanged since |
| V Testing | pass (pending lead's `check`) | FR-32 tests tagged; no gate lowered; `build.gradle` only adds a test input |
| VI Safety | pass | v4 read-only DOKS matches CLAUDE.md section 0 and `DOKS_READ = "allow"` with change and Secret denials |
| VII Docs | conflict | wrong current facts, Findings 1 to 4 |
| VIII Agents | pass, note | the guard's last verdict is the lead's probes, not an independent reviewer's (the lead did not write the code) |

## Findings

Important (fix before the merge, doc-only):

1. `.claude/docs/decisions/0011-grpc-check-access-and-pinned-contracts.md:81-83`: Status says "Still to build: task 8
   ... it waits until the owner pushes the action list". Task 8 is built (`e630c62`, `61c2e4b`). Fix: say task 8 is
   built and tested (`AccessActionListTest`, review `ai/reviews/manage-site-task-8-review.md`); keep the mesh and
   commit-check lines.
2. `.claude/docs/requirements/TRD.md:126-128`, "Drift seen": still lists "`manage_site` is refused" and "TR-27:
   `contracts.lock` pins no `access/actions.v1.json` ... no test". `2f66fa3` closed this Drift in BRD, PRD and FRD and
   DIRECTION marks TR-27 Done, so the TRD contradicts the spec set. Fix: remove both bullets.
3. `.claude/docs/contracts/README.md:31-34` and `:42`: "no remote yet", `main` at `afff924` "the pin in
   `contracts.lock`", the action list "on `access-actions-v1`", Built "no". The lock is `18dd301` on `main` (has
   `origin` `itme-roa15/webblock-contract`), and `access/actions.v1.json` is on that commit. Fix: state the remote,
   the pin `18dd301` on `main`, the action list on `main`, Built "yes".
4. `.claude/docs/contracts/provisioning-contract.md:9` (pin `afff924`) and `:111-118` (branch `access-actions-v1`,
   "Built: no", "ten actions and no `MANAGE_SITE` until ... (task 8)"). Fix: pin `18dd301`; Built: yes, 11 actions,
   `MANAGE_SITE` a write action, proven by `AccessActionListTest`.
5. `ai/handoffs/handover.md:196`: "`web-block-contracts` (no remote yet)" contradicts `handover.md:31-32` (remote
   named). Fix: name the remote. `ai/handoffs/handover.md:84`: "B10 `manage_site` is built (item 1)": item 1 is now
   the FR-13 code. Fix: drop "(item 1)" or point to section 2.

Minor (fix in the same commit if cheap):

6. `.claude/docs/requirements/DIRECTION.md:4` and `:210-221` cite `be8083f`, `03f669f`, `208a073` and `3ca6db0`,
   which are commits of the `manage-site` branch, not on this branch; after the merge they are not in `main`. The
   content holds (the code is identical). Fix: cite `2f66fa3`, `61c2e4b` and `a3be2b8`.
7. FRD:120 says FR-13.12 has no test; DIRECTION's FR-13 row and its "FRs with no test" table count
   `PlatformPropertiesTest` (`fr13_theSiteDomainLeavesRoomForTheLongestSubdomain`, the 189 limit) for FR-13.12. Pick
   one: either FRD says "FR-13.12: `PlatformPropertiesTest`" or DIRECTION lists FR-13.12 as untested.
8. `ai/handoffs/next.md:41-46`, "Last gate results": still the earlier merge (`2c0c5e0`, hook tests 402). The lead is
   adding the `check` result at `2bee190`; put the 693 and 215 hook counts there too.

## Fix wave

1. One doc commit (cites FR-32, TR-27): Findings 1 to 5 (decision 0011 status, TRD Drift seen, contracts README,
   provisioning contract, handover lines 84 and 196), plus 6 to 8 if cheap. Then the docs-style checks on those
   files. No Gradle rerun needed; a scoped re-review checks only that commit.

## Deferred minors

| Ledger line | Decision | Reason |
| --- | --- | --- |
| Historical reviews `ai/reviews/final-project-notion-alignment.md:64`, `contracts-inventory.md:229` say task 8 waits | drop | review records of their own commit; git history is the record |
| Plan and design of J3 say task 8 waits on the push (`j3-notion-alignment-plan.md:178,249`, design `:367`) | drop | the plan's order, true when written; the phase is closed |
| `next.md:93-96` says Project accepts 3 to 63 and `xn--` | drop | true of the current code; FR-13 code is handover item 1 |

## Not proven

- The lead's `./gradlew check --rerun-tasks` at `2bee190` (not seen by me).
- Guard behaviour on the real cluster: I could not run my own probes (the reviewer hook allows writes only under
  `ai/reviews/`); the last round's "ready" rests on the lead's probes and test rows, G2 and G3 only by test rows.
- Istio mesh policy for port 9090 and `/internal/**`, TLS, DOKS, backups, scale, and Provisioning reading
  `manage_site` on Kind: none can be shown by a local run.

# Re-review 1

Stage 5 Verify, scoped re-review of the fix wave `1ebc70a` and `8351a87` (`git diff 2bee190..8351a87`), docs only.
IDs: FR-32, TR-27, TR-11, TR-13, FR-31.

Verdict: ready for the owner's OK once two lines are fixed (no further review needed for them)

Closed: findings 1 to 5, and minors 6 and 8. Decision 0011 Status says task 8 is built at `18dd301`. The TRD Drift
list has only CMN §4.1 left. The contracts README and the provisioning contract name the remote and pin `18dd301` on
`main`, and section D says Built: yes. Handover lines 84 and 196 are right. next.md "Last gate results" gives the
lead's `check --rerun-tasks` at `2bee190`. Minor 7 is left as the lead chose, which is consistent: the FRD says
FR-13.12 has no test of its own. Every file the pages now place on `main` exists at `18dd301` in
`web-block-contracts`: `user_status_changed`, `project_owner.proto`, the three Codegen event schemas and
`repository.proto` with Project's answer header. `5708a31` is an ancestor of `18dd301`. No en or em dash, no line over
120 outside tables, in the changed files. Only docs and this report changed.

Still wrong (same kind as findings 3 and 4, one line each):

1. `.claude/docs/contracts/service-communication.md:110-111`: "They are not in `contracts.lock` until the owner
   pushes the branch and the pin moves." The files are on `main` at `18dd301`, the commit `contracts.lock` pins.
   Fix: drop the sentence, or say `contracts-sync.sh` copies only the access files.
2. `.claude/docs/requirements/DIRECTION.md:4`: "branch `manage-site` at commit `52f55f5`". `52f55f5` is a
   `claude-makeover` commit. Fix: "branch `claude-makeover`".
