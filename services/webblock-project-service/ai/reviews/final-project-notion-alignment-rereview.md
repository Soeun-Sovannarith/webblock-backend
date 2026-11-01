# Final review: project-notion-alignment 546ceeb..5e01228

Verdict: needs a fix wave. The 19 fix-wave commits (`546ceeb..2c0c5e0`) close every code and docs finding of the
first review (findings 1, 4, 6 to 10 closed, 5 nearly closed), and every Gradle gate passes. The last commit,
`5e01228` (hooks), adds two guard regressions: a merge into `main` through a new worktree is now allowed with no
question, and `sed` forms that write a hook or settings file (`--in-place`, `-ni`, a `w` command) now pass
silently. Both weaken the hook that Art. VI and `.claude/CLAUDE.md` section 5 rely on, which is a conflict with
Art. VI. Fix wave item 1 closes them. After that, the merge still waits on owner rulings: R-1 to R-4 from the first
review, plus R-5 (Must Drift on FR-9.2 and FR-18, which `DIRECTION.md` names) and R-6 (FR-37 says Payment sets
`suspended`, which contradicts Notion "2 · Project").

Stage 5 Verify, scoped re-review of the 20 commits after `546ceeb`. IDs covered: FR-5, FR-13, FR-30, FR-31, FR-32,
FR-36, FR-37, TR-2, TR-24, TR-25, TR-27, TR-39; Art. I to VIII.

Read: `git diff 546ceeb..5e01228` in full (31 files), every commit message in the range,
`.claude/docs/requirements/DIRECTION.md`, `ai/handoffs/next.md`, the BRD, PRD, FRD and TRD changes, `spec.json`,
decisions 0007 and 0011, runbooks `grpc.md` and `migrations.md`, `GrpcServer`, `GrpcRequestIdInterceptor`,
`GrpcUnknownMethodRegistry`, the new `GrpcCallLimitInterceptorTest`, and `guard.py`'s merge, `sed` and constitution
rules. Notion "2 · Project" fetched read-only (last edited 2026-10-22T10:17:01.919Z, the time the DB audit and the
first review used).

## Evidence

| Check | Commit | Result | Notes |
| --- | --- | --- | --- |
| `./gradlew check test --rerun integrationTest --rerun` | 2c0c5e0 | passed | BUILD SUCCESSFUL in 2m 58s; `spotlessCheck`, `contractStubsInBootJar` and `jacocoTestCoverageVerification` ran. Current for 5e01228: it changes only `.claude/hooks/`, which no Gradle task reads |
| `test` | 2c0c5e0 | passed 826 | 0 failures, 0 errors, 0 skipped (result files written by this run) |
| `integrationTest` | 2c0c5e0 | passed 301 | 0 failures, 0 errors, 0 skipped; PostgreSQL 18 and RabbitMQ in Testcontainers |
| Coverage rules | 2c0c5e0 | passed | no rule or exclusion changed in the range |
| `./gradlew compileJava --rerun compileTestJava --rerun` | 2c0c5e0 | passed | the same 9 warnings as at 546ceeb, all from `main`; none in a file the fix wave touched |
| `python3 -I .claude/hooks/test_guard.py` | 5e01228 | passed 283 | reads the working-tree `.claude/settings.json` (`test_guard.py:448`); see "Hook tests and settings" |
| `python3 -I .claude/hooks/test_hooks.py` | 5e01228 | passed 214 | reads the working-tree `.claude/settings.json` (`test_hooks.py:351`) |
| `python3 -I scripts/spec-check.py` | 5e01228 | 0 errors | report only; Must FRs with no test: 28 (FR-1 to FR-4, FR-6 to FR-12, FR-14, FR-16 to FR-29, FR-34, FR-37); `--self-test` 15 of 15 |
| `scripts/contracts-sync.sh --check` | 2c0c5e0 | exit 0 | `contracts/` matches `afff9245444c628b74f88d281939f4bc8b4de3e2`; 5e01228 does not touch it |
| `scripts/osv-scan.sh` | 546ceeb | not run, still current | no change to `gradle/`, `build.gradle` or the lock files in the range; exit 0 at 546ceeb |
| Docs checks (dashes, 120 characters) | 5e01228 | passed | long lines only in `spec.json` data (as before) and `Rows.java:101` (from `main`) |
| DB audit | 498a5f0 | current, 0 DIFFERENT | no migration, entity, repository or role script changed in the range; Notion page unchanged |
| Kind | none | not run | needs an owner slot; the change crosses services |
| `DIRECTION.md` | f0f2214 | present, current | code as of `0d9e82b`; later commits change only the handoff and the hooks. Verdict "At risk": Must Drift on FR-30 to FR-33 (R-2), FR-32 (R-1), FR-9.2 and FR-18 (R-5) |

### Hook tests and settings

Both suites read `.claude/settings.json` from the working tree, so today they read the owner's uncommitted edit. The
settings rows that remain read only the `hooks` block, and that block is equal in `git show HEAD:.claude/settings.json`
and the working tree (checked in Python; `allow` and `deny` are equal too; only `ask` and the place of
`enabledPlugins` differ). So both suites pass against the committed file as well. The two rows `5e01228` removed
("an edit of the constitution asks the owner", in both suites) pass against the committed file and fail only against
the working-tree edit. The commit therefore makes the tests follow an uncommitted file and stops asserting the five
`ask` entries the committed file still holds. The guard itself still asks before an Edit or Write of the
constitution, a hook or a settings file (`test_guard.py:249-253`), so Art. VI holds through the guard even without
those `ask` entries.

## Spec coverage

| ID | Where built | Test | Status |
| --- | --- | --- | --- |
| FR-5 (FR-5.3) | `GlobalExceptionHandler` with `DatabaseBusy` (unchanged) | `RetryAfterWebTest` fr5 (`dea79f9`) | done |
| FR-13 | unchanged; decision 0007 names the sub-domain in a repeat create (`be8dbd6`) | as before | done; label rule open (FRD Q-2) |
| FR-30 (FR-30.6) | `AccessCheckRequest.action` is text checked by `@AccessActionName` (`0d9e82b`) | `AccessCheckControllerTest` fr30_6 (unknown, upper case, untrimmed, missing, short UUID) | done; FR-30.2 Drift against Art. III (R-2) |
| FR-31 | `AccessGrpcMapper.toRequest` uses the same record and rule; `GrpcErrors.close` | `AccessGrpcServiceTest`, `CheckAccessIntegrationTest` | done; FR-31.2 Drift (R-2) |
| FR-32.3 | `AccessAction.fromWireName`, exact match only | `AccessGrpcServiceTest` fr32_3, `AccessCheckControllerTest` fr32 | done |
| FR-32.1, FR-32.2 | no `MANAGE_SITE` | none | Drift (task 8, R-1) |
| FR-36 | unchanged; `Rows.project` uses `TestSubdomains.of` (`6705160`) | schema tests | done |
| FR-37 | spec only, J4 | none | not started by plan; its `suspended` clause needs R-6 |
| TR-2 (cited by `5e01228`) | `guard.py:929-934`, `:1393-1394` | `test_guard.py` (3 new `sed` rows) | Drift: TR-2 is the Modulith module rule, not the guard (minor 4); the change itself regresses (important 1, 2) |
| TR-24 | `GrpcCallLimitInterceptor.java:41-59` takes the place at half-close (`5bf4a95`) | `GrpcServerTest` tr24_unfinishedCallsDoNotBlockOtherCalls, `GrpcCallLimitInterceptorTest` (7 tests) | done |
| TR-25 | `GrpcErrors.close` (`GrpcErrors.java:79-93`), used by the call limit, the error interceptor and the unknown-method answer (`0ee34e1`) | `GrpcErrorsTest`, `GrpcServerTest` | done |
| TR-27 | no `actions.v1.json`, no list test | none | missing (task 8, R-1) |
| TR-39 | `migrations.md` "Rolling out V6" takes a backup (`c032634`) | none (runbook) | done; minor 3 |

Orphan: none. Drift: as listed; all but TR-2 are known and in `DIRECTION.md`.

## Constitution

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass, with R-6 | All 20 commits cite IDs; spec-check 0 errors. `f0f2214` adds FR-37 and closes FRD Q-5 without naming the owner decision it follows (`.claude/rules/spec-driven.md`: "and say which"); `5e01228` cites TR-2, which is not the guard (minor 4) |
| II Tech stack | conflict | Unchanged: `CONSTITUTION.md:41` names a guard ask before lock rewrites that no longer exists, and the gRPC versions have no recorded owner approval. R-3 |
| III Architecture limits | conflict | Unchanged: `AccessCheckService` answers `NOT_MANAGER` where Art. III says `NOT_FOUND` (FRD Q-1). R-2. Layout, ArchUnit, Modulith and both databases pass; the new `access/application/validation` types sit in an allowed type folder |
| IV Coding conventions | pass | One gRPC error helper instead of three copies; the wire name is stated once; no new warning |
| V Testing and quality | pass | 826 and 301 passed, 0 skipped; coverage green; new tests carry FR or TR IDs; no `@Disabled` |
| VI Safety guardrails | conflict | `5e01228` lets a merge into `main` through a new worktree and in-place `sed` writes of hooks and settings pass the guard with no question (important 1, 2). "The hooks are on and are never worked around" and "No agent merges into `main`" rest on these rules. No secret, no cluster command otherwise. The uncommitted owner edit of `settings.json` alone does not break Art. VI: the guard still asks for Edit and Write of those files |
| VII Docs | pass | No dash, lines within 120, no dated notes in the changed docs. Stale lines in the handoff (minor 1) |
| VIII Agents | conflict | Unchanged: the plan was approved by the lead (`j3-notion-alignment-plan.md:3`). R-4 |

## Findings

State of the first review's findings:

| Finding | State | Where |
| --- | --- | --- |
| 1 `DIRECTION.md` missing | closed | `ff9895f`, `.claude/docs/requirements/DIRECTION.md` (at `f0f2214`, code at `0d9e82b`, still current) |
| 2 Art. II enforcement line and versions | open, owner | R-3; `CONSTITUTION.md:41` unchanged |
| 3 Plan approved by the lead | open, owner | R-4; `j3-notion-alignment-plan.md:3` unchanged |
| 4 A stalled call holds a place | closed | `5bf4a95`, `GrpcCallLimitInterceptor.java:41-59`; `GrpcServerTest` (8 open calls, limit 2, 5 calls answered); decision 0011 and `grpc.md` (`61c4f32`, `cf4b571`), TR-24 (`c64853c`) |
| 5 Handoff stale | partly closed | `4f88db2`, `e457f0a`, `1c81fc7`, `2c0c5e0`; three lines still stale (minor 1) |
| 6 "None open" in the requirement files | closed | `c64853c`, `f0f2214`: `FRD.md:421-428` Q-1 to Q-4; BRD, PRD and `TRD.md:131-135` point to them |
| 7 Decision 0007 repeat create | closed | `be8dbd6`, `0007-project-access-rules.md:12` |
| 8 No backup in the V6 rollout | closed | `c032634`, `migrations.md:48-50` and the check after the steps; minor 3 on the order |
| 9 FR-5 display name | closed | `dea79f9`, `RetryAfterWebTest.java:56`; FR-5.3 added by `c64853c` |
| 10 QA tasks 2, 3, `Rows` | closed | `0ee34e1` (`GrpcErrors.java:79-93`), `1536aa2` and `0d9e82b` (`AccessAction.java:39-45`, no `@JsonProperty`), `6705160` (`Rows.java:44-48`) |
| 11 `read` flag against G21 `write` | deferred | task 8, unchanged |

Also closed, which the first review missed: before `0d9e82b`, REST read `action` as an enum, so `drop_db` answered
400 `MALFORMED_REQUEST`, not `VALIDATION_FAILED` as FR-30.6 says, and Jackson's enum reading trimmed
`"deploy_release "` and accepted it while gRPC refused it. Now both read the text exactly and answer
`VALIDATION_FAILED`, `VALUE_INVALID` on `action`. Provisioning gets a 400 in both cases, so its fail-closed handling
does not change; a client that sent padded names is now refused (Kind, below).

Checked and clean in the new Java code: the request-ID interceptor is outermost (`GrpcServer.java:95`), so the call
limit's refusal at half-close still carries the call's request ID; a refused call never reaches the handler and its
later cancel or complete returns nothing; a handler that throws gives its place back (`finally`);
`AccessActionNameValidator` leaves null to `@NotNull`, so each field gets one error; `AccessCheckRequest.accessAction`
is called only after `@Valid` (REST) or `validator.validate` (gRPC); streaming methods are not capped, which the class
comment, decision 0011 and `grpc.md` state (Project serves unary only).

Critical: none.

Important

1. `.claude/hooks/guard.py:929-934` (`5e01228`): when the guard cannot read the current branch it now allows the
   merge; before, it asked. `git worktree add .claude/worktrees/m main && git -C .claude/worktrees/m merge
   project-notion-alignment` merges into `main`, yet the guard sees no branch (the worktree does not exist when the
   hook runs) and lets it through with no question. The guard at `2c0c5e0` answers "ask: the guard could not read
   the current branch" for the same command (both probed by piping the hook input to the two versions; nothing ran).
   `git checkout main && git merge` and `git switch main && git merge` are still denied. CLAUDE.md section 5 and
   Art. VI. Fix: keep the ask when the branch is empty, or read the branch a same-command `git worktree add <path>
   <branch>` checks out and deny when it is protected; add `test_guard.py` rows for `git worktree add <p> main &&
   git -C <p> merge x` (deny or ask) and `git -C <missing path> merge x` (ask).
2. `.claude/hooks/guard.py:1393-1394` (`5e01228`): `sed_reads` is true for any `sed` with no word starting with
   `-i`, so these now pass with no question, where `2c0c5e0` asked for each: `sed --in-place s/a/b/
   .claude/settings.json`, `sed -ni s/a/b/ .claude/settings.json` (`-n` and `-i` together), `sed -n
   's/a/b/w .claude/settings.json' .claude/settings.json` and `sed -n 1w.claude/hooks/x.py .claude/hooks/guard.py`
   (the `w` command writes a file). Each writes a settings or hook file. Art. VI. Fix: treat `sed` as a reader only
   when every option is a known read-only one (`-n`, `-e`, `-E`, `-r`, `-s`, `-u`, `-z`, `--quiet`) and no script
   holds a `w` or `W` command or `e`; else ask as before. Add the four commands above as ASK rows.
3. `.claude/docs/requirements/DIRECTION.md` "Drift" rows FR-9.2 and FR-18: two Must FRs drift that R-1 to R-4 do not
   cover. FR-9.2 says an unknown business type lists no template; `SiteTemplate.java:15` gives `blank` to every code.
   FR-18 follows S1 `max_pages` as a setting; `PageService.java:34` fixes 4. Both come from `main`, but
   `.claude/rules/spec-driven.md` blocks a merge on Must Drift until the owner rules, and the handoff lists them only
   under "For the next owner". Fix: owner ruling R-5 (carry both as known Drift, or say what changes); the lead adds
   R-5 to the handoff's rulings.
4. `.claude/docs/requirements/FRD.md:353` and `:372`: FR-37 and the States row say only Payment or an admin sets
   `suspended`. Notion "2 · Project" says `suspended` has no writer yet and is not used for unpaid plans; no Payment
   contract has a suspend signal (the handoff lists "Payment's suspend signal if any" as a gap). `f0f2214` names no
   owner decision for FR-37 or for closing Q-5. Notion ranks above the spec, and another service's behaviour is
   never invented (Art. III, Art. I). Nothing is built on it. Fix: owner ruling R-6: confirm FR-37 and the Payment
   clause (the lead records it; the owner updates Notion S1 if it changes), or the spec-writer removes "Payment or"
   from both lines until Payment's contract has the signal.

Minor

1. `ai/handoffs/next.md:31` says the spec-writer is still writing `DIRECTION.md` (committed in `ff9895f`);
   `:53-54` gives spec-check "Must FRs with no test: 27" at `5cfd49e` (28 since FR-37); `:68` names only R-1 to R-4.
   Art. VII. Fix: the docs-keeper states `DIRECTION.md` at `f0f2214`, the gate results of this report and the rulings
   R-1 to R-6.
2. `.claude/docs/requirements/FRD.md:236`: the generation "Proven today by" line dropped "FR-28 has no code or test
   yet", but FR-28 is still not built (DIRECTION.md Top 5, item 4). `FRD.md:415-419` "Drift seen" lacks FR-9.2. Fix:
   the spec-writer restores the FR-28 sentence and adds the FR-9.2 line.
3. `.claude/docs/runbooks/migrations.md:48-51`: the backup is taken before the old version stops, so saves made
   between the backup and the stop are lost on a restore. Fix: stop the old version first, then take the backup
   (swap steps 2 and 3), or say that a restore loses those saves.
4. Commit `5e01228` cites TR-2 (Modulith modules reached through their named interface). No TR covers the guard's
   merge and write rules; the nearest is TR-13 (the guard's commit ID check). The test changes follow the uncommitted
   owner edit of `settings.json` (section "Hook tests and settings"). Fix: history is not rewritten; the fix-wave
   commit for important 1 and 2 cites TR-13 and an FR it serves (or a new guard TR the spec-writer adds), and the
   lead tells the owner that the tests now assume the settings edit will be committed.
5. `.claude/hooks/guard.py:1340-1345` `writes_constitution` has the same `-i` prefix test: `sed -ni ...` and
   `sed --in-place ...` on `.claude/docs/CONSTITUTION.md` pass with no question, at `2c0c5e0` too (not new). Fix:
   the same reader rule as important 2, with ASK rows for the constitution.

## Fix wave

1. Implementer (the owner approves hook edits; the guard asks): important 1 and 2 and minor 5 in
   `.claude/hooks/guard.py`, failing `test_guard.py` rows first. IDs: TR-13 and an FR it serves (minor 4).
   Gates: `python3 -I .claude/hooks/test_guard.py`, `python3 -I .claude/hooks/test_hooks.py`, run against the
   committed `settings.json` as well as the working tree.
2. Docs-keeper and spec-writer: minor 1 to 3, and the answer to R-6 if the spec changes. IDs: FR-36, FR-31, FR-28,
   FR-9, FR-37, TR-39. Gates: the docs checks of `.claude/rules/docs-style.md`, `python3 -I scripts/spec-check.py`.
   It may ride with the commit of the untracked reviews.
3. Final reviewer: a scoped re-review of item 1 only (guard behaviour and the hook tests).

Owner rulings that the fix wave cannot replace:

- R-1 to R-4: as in `ai/reviews/final-project-notion-alignment.md` (push task 7 or carry FR-32; Art. III against
  FR-31.2; Art. II line 41 and the gRPC versions; ratify the plan).
- R-5: FR-9.2 and FR-18 Must Drift (important 3).
- R-6: FR-37's `suspended` clause and the FR-37 and Q-5 decisions on record (important 4).
- Whether the uncommitted `.claude/settings.json` edit will be committed; the hook tests now assume it.

## Deferred minors

| Ledger line | Decision | Reason |
| --- | --- | --- |
| First review 11 (`read` against `write`) | defer | task 8 (R-1) |
| QA tasks 1, 4, 5, rest of 6 | defer | on `main`; "For the next owner" in the handoff |
| QA owner questions 1, 2, 4 | defer | no effect on this branch's FRs |
| En dash in `OutboxSpikeTest.java:67` | defer | from `main`, with the QA tasks |
| Sub-domain labels (FRD Q-2) | defer | a small FR-13 change after the merge, owner's choice |
| Security review: `merge-base --is-ancestor` in `contracts-sync.sh` | defer | with the next `contracts.lock` move (task 8) |
| `GrpcUnknownMethodRegistry` no longer asserts the request ID is set (`GrpcErrors.close` falls back to a new one) | drop | the registry always runs inside `GrpcRequestIdInterceptor` (`GrpcUnknownMethodRegistry.java:57`); the fallback is harmless |
| Commit `1536aa2` says REST reads `@JsonValue`, which `0d9e82b` removed | drop | history is the record; the code and comments on the head are true |
| Minors 1 to 3 of this report | fix now | fix wave item 2 |
| Minors 4 and 5 of this report | fix now | fix wave item 1 |

## Not proven

- Kind: V6 with Provisioning's routing by sub-domain, the create with `subdomain`, the REST check (now refusing padded
  or upper-case actions with `VALIDATION_FAILED`) and CheckAccess from Provisioning's client have not run together.
- Istio: the `grpc` port name, STRICT mTLS, the port-pinned `AuthorizationPolicy` for `provisioning-api`, HTTP/2
  through Envoy, and how Envoy's limits interact with the half-close place and Netty's idle and age limits.
- DOKS: the migration job, V6 on real data (collision count O-6, lock time), the stop-then-migrate rollout.
- Backups: the CNPG backup step is written, but no backup or restore of the project database has been tried.
- TLS: the wildcard certificate and the address change of existing sites to `p-<12 characters>`.
- Scale: 8 calls in flight per pod against a pool of 10 is reasoned, not load tested; replicas multiply connections.
- Hooks: the guard reads one command at a time; other shell forms that write a file may exist beyond the ones probed.
- Other repositories: task 7 in `web-block-contracts` is not pushed; Provisioning's gRPC client and its
  `admin_allowed` flags are outside this repo.


## Re-review 2: 5e01228..7fb9c09

Verdict: needs a fix wave, one small guard task. The owner rulings R-1 to R-6 are in the spec, the constitution
(version 3), the plan and `dependency-versions.md`, and they match what the coordinator reports. Important 1, 2 and
minor 5 of the first re-review are closed. But the new worktree tracking in `guard.py` opens a different way to merge
into `main` that `2c0c5e0` denied (important R2-1). Also, the owner should confirm the constitution edit `c435675`
(important R2-2). Once R2-1 is fixed and the owner confirms R2-2, the branch is ready for the owner's merge OK, with
the known Drift FR-9.2, FR-18 and FR-32.1, FR-32.2 carried by the owner.

Stage 5 Verify, scoped re-review of the 10 commits after `5e01228`. IDs: FR-9, FR-18, FR-28, FR-30, FR-31, FR-32,
FR-36, FR-37, TR-10, TR-13, TR-32, TR-39; Art. I to VIII.

### Evidence

| Check | Commit | Result | Notes |
| --- | --- | --- | --- |
| `python3 -I .claude/hooks/test_guard.py` | 7fb9c09 | passed 370 | the working-tree `settings.json` now equals `HEAD` (`git diff HEAD -- .claude/settings.json` is empty), so the suite ran against the committed file |
| `python3 -I .claude/hooks/test_hooks.py` | 7fb9c09 | passed 215 | the same file; the new row checks the `git commit --amend` ask |
| `python3 -I scripts/spec-check.py` | 7fb9c09 | 0 errors | report only; Must FRs with no test: 28 (FR-1 to FR-4, FR-6 to FR-12, FR-14, FR-16 to FR-29, FR-34, FR-37); `--self-test` 15 of 15 |
| `scripts/contracts-sync.sh --check` | 7fb9c09 | exit 0 | matches `afff9245444c628b74f88d281939f4bc8b4de3e2` |
| Gradle `check` (826 and 301 passed, coverage passed) | 2c0c5e0 | current | `git diff --stat 2c0c5e0 HEAD` touches only `.claude/` (15 files); nothing under `src/`, `build.gradle`, `gradle/`, `contracts/` or `scripts/` |
| `scripts/osv-scan.sh` | 546ceeb | not run, still current | no dependency change |
| Docs checks (dashes, 120 characters) | 7fb9c09 | passed | every changed doc, `guard.py` and `test_guard.py` |
| DB audit | 498a5f0 | current | no schema change |
| Kind | none | not run | needs an owner slot |
| `DIRECTION.md` | a68cc5a | present, partly stale | written before `6525de0` and `670daaf` fixed TR-32 (minor R2-3) |

### Guard probes

These were dry probes: the hook input piped into `guard.py` at `HEAD`, and into the `2c0c5e0` or `c435675~1` version
read with `git show`. The guard only prints a verdict, so nothing ran.

Closed:

- Important 1: `git worktree add .claude/worktrees/m main && git -C .claude/worktrees/m merge x` is denied, and the
  same with `cd`, with `;`, with `-f`, with `--detach` and with a folder named `main`. `git -C <missing> merge x`,
  `--git-dir`/`--work-tree` and `cd $X && git merge` ask. `git checkout main && git merge` and `git switch main &&
  git pull` are denied. A plain merge on the work branch, and `worktree add -b feat` followed by a merge, are allowed,
  as intended.
- Important 2 and minor 5: `sed --in-place`, `sed -ni`, `sed -ns 'w …'`, `s///w`, `s///gpw`, `1!w`, `y///;w`,
  `W`, `--expression[=]`, `-f`, `--posix`, `-z`, labels, `l 5`, `q5`, addresses (`\%x%`, `0~3`, `1,/x/I`) and
  `s/a/b/ w` all ask on a settings or hook file, and on the constitution. `s///e` and `e` ask on any file;
  `1r .claude/hooks/guard.py` asks. Plain reads (`sed -n 25,50p`, `sed 's/a/b/g'`, `sed -s -n p`) pass. All of
  these asked or passed as intended.
- `7fb9c09`: `worktree add -B main`, `-Bmain`, `switch -C main`, `--force-create[=]main`, `checkout -B main`,
  `-qBmain`, `branch -m main`, `-M x main`, `--move x main` and `branch -f main` are denied; `branch -m x` asks.
  `git branch -c main backup` is also denied although a copy does not move `main`. That is stricter than needed and
  harmless.

**The constitution (coordinator item 2).** At `HEAD`, and at `c435675~1` (the guard in force when `c435675` was
written), the guard asks for an Edit, Write or MultiEdit of `.claude/docs/CONSTITUTION.md`. It also asks for shell
writes by redirect, heredoc into `cat >`, `cp`, `tee`, `perl -pi`, `sed -ni` and `sed --in-place`. It does not see
a write made by an interpreter: `python3 -c "open('.claude/docs/CONSTITUTION.md','w')…"`, a `python3 - <<EOF`
script, or `node -e` pass with no question, at `HEAD`, at `c435675~1` and on `main`. Writes to hooks and
`settings.json` behave the same way. `guard.py:8-9` states this limit ("an interpreter one-liner can get past it").
So if the docs-keeper got no question, either it wrote the file through an interpreter, or the session's permission
mode answered the guard's ask. The repo cannot show which.

### Spec and docs against the rulings

| Ruling | Where | Result |
| --- | --- | --- |
| R-1 merge with `manage_site` as known Drift | FRD and PRD "Drift seen", DIRECTION Top 5 item 1 | matches |
| R-2 Art. III allows `NOT_MANAGER` | `CONSTITUTION.md:66-69` (version 3), `java-code.md:58-60`, TR-32 (`6525de0`), `spec.json` TR-32 title (`670daaf`), FRD Q-1 closed | matches; DIRECTION still lists TR-32 as Drift (minor R2-3) |
| R-3 Art. II line and gRPC pins | `CONSTITUTION.md:41-42`, `dependency-versions.md:9-10` | matches |
| R-4 plan ratified | `j3-notion-alignment-plan.md:3` | matches |
| R-5 FR-9.2 and FR-18 carried | FRD and PRD "Drift seen", DIRECTION | matches |
| R-6 nothing sets `suspended` | `FRD.md` FR-37 and States, BRD | matches Notion "2 · Project" ("No writer yet") |
| FR-37 = first `publish_completed` | FRD FR-37.1 to FR-37.4, PRD PR-9, `spec.json` | the input fields are in `web-block-contracts` `main:events/provisioning/publish_completed.v1.schema.json`. The missing Project queue in Notion S2 §4.4 is listed as Drift and as an owner question (DIRECTION Q-1). Not built (J4) |

Also closed: minor 2 (`FRD.md:235`, the FR-28 sentence is back; FR-9.2 is in "Drift seen") and minor 3
(`migrations.md:48-51`, stop first, then back up). All 10 commit messages cite IDs. `2bea88c`, `14c38b4` and
`7fb9c09` cite TR-13 without one of its FRs, but `.claude/` is not a code path in `.claude/rules/spec-driven.md`. Note
only.

### Findings

Critical: none.

Important

- R2-1. `.claude/hooks/guard.py:801-809` with `:975-980`: a branch recorded for a new worktree always wins over a later
  checkout in that worktree. `current_branch` looks in `ctx.worktrees` before `ctx.branches`, and the checkout writes
  only `ctx.branches`. So `git worktree add -b feat .claude/worktrees/f && git -C .claude/worktrees/f checkout main
  && git -C .claude/worktrees/f merge x` is allowed with no question, and the same with `cd` and `git switch main`.
  `main` is not checked out anywhere, so git would do it: a merge into `main`. The guard at `2c0c5e0` denies all three.
  With `worktree add <p> x` (a branch the guard cannot name) followed by `switch main`, the guard asks instead of
  denying. CLAUDE.md section 5, Art. VI. Fix: when a checkout or switch runs in a folder recorded in
  `ctx.worktrees` (the folder or a parent), write the new branch into that entry. Add `test_guard.py` rows for the
  `checkout main`, `switch main` and `cd` forms (DENY).
- R2-2. `c435675` changed `.claude/docs/CONSTITUTION.md`, and the commit is an agent's. The "Amendments" section says
  an agent never edits the file itself, and the guard asks the owner before any edit. The text matches rulings R-2
  and R-3, but the edit did not go through the owner's confirmation (the docs-keeper reports no question). Fix (owner):
  read `git show c435675 -- .claude/docs/CONSTITUTION.md` and confirm version 3 as yours, or apply it yourself. The
  interpreter gap of the guard is a known, stated limit; closing it is the owner's choice (deferred below).

Minor

- R2-3. `.claude/docs/requirements/DIRECTION.md:13`, `:30-34`, `:103-105`, `:173` and `:197` (and the Count table at
  `:40-43`) still say TR-32 drifts and the TRD text is behind. `6525de0` and `670daaf` fixed both after `a68cc5a`, so
  FR-30, FR-31 and FR-33 are Done on the head. Art. VII. Fix: the spec-writer refreshes those lines and the counts at
  `7fb9c09`.
- R2-4. `.claude/hooks/guard.py:1460-1475` (`sed_words`): an abbreviated long option hides the script. GNU sed takes
  `--expr=…` as `--expression` (checked: `echo x | sed --expr='s/x/ok/'` prints `ok`). The guard marks the call
  "not read only" but never reads the value, so `sed --expr='w .claude/settings.json' README.md`,
  `sed --expr='w .claude/docs/CONSTITUTION.md' README.md` and `sed --expr='1r .env' README.md` pass with no question.
  The same was true at `2c0c5e0` and on `main`. Fix: read the value of any unambiguous prefix of `--expression` (and
  `--file`) as that option, and add the three as ASK or DENY rows. It can go in the same task as R2-1.

### Coordinator items 4 and 5

- Minor 1 (stale handoff): acceptable to defer. The handoff is a working note, the lead rewrites it as the first
  commit on `main` after the merge, and no rule or gate reads it. It is more stale now (R-1 to R-6 are settled). So
  the rewrite must come before any other session starts from it. Open item: "handoff rewrite, first commit after the
  merge".
- Minor 4 (`5e01228` cites TR-2): note only. History is not rewritten, and the later guard commits cite TR-13.

### Fix wave

1. Implementer (hook edit; the owner approves): R2-1 and R2-4 in `.claude/hooks/guard.py`, failing
   `test_guard.py` rows first. IDs: TR-13 and an FR it serves. Gates: `test_guard.py`, `test_hooks.py`.
2. Spec-writer: R2-3. IDs: FR-30, FR-31, FR-33, TR-32. Gate: `spec-check.py`.
3. Owner: R2-2.
4. Final reviewer: a scoped check of item 1 only (the probes above plus the new rows).

### Deferred minors

| Ledger line | Decision | Reason |
| --- | --- | --- |
| First re-review minor 1 (handoff) | defer | the lead's first commit on `main` after the merge |
| First re-review minor 4 (TR-2 in `5e01228`) | drop | history is the record |
| Interpreter writes (`python3 -c`, heredoc scripts, `node -e`) to the constitution, hooks and settings | defer | a stated limit of the guard (`guard.py:8-9`), also on `main`; the owner decides whether a guard task closes it |
| `git branch -c main backup` denied | drop | stricter than needed, no harm |
| Commits citing TR-13 without an FR | drop | `.claude/` is not a code path in `spec-driven.md` |

### Not proven

Unchanged from the first re-review. The guard probes cover the command forms listed above, not every shell form.


## Re-review 3: 7fb9c09..7793638

Verdict: ready for the owner's merge OK. R2-1 and R2-4 are closed (`7793638`), R2-3 is closed (`5aed118`), and the
owner confirmed constitution version 3 (`c435675`) as their amendment, which closes R2-2. Every article passes. The
known Drift FR-9.2, FR-18, FR-32.1 and FR-32.2 goes through the merge by the owner's rulings R-1 and R-5. Nothing
is left for a further fix round. The open items below are the owner's, and none of them blocks the merge.

Stage 5 Verify, scoped re-review of `5aed118` and `7793638`. IDs: TR-13, FR-31, FR-30, FR-33, TR-32; Art. I to VIII.

### Evidence

| Check | Commit | Result | Notes |
| --- | --- | --- | --- |
| `python3 -I .claude/hooks/test_guard.py` | 7793638 | passed 402 | the committed `settings.json` (the working tree equals `HEAD`) |
| `python3 -I .claude/hooks/test_hooks.py` | 7793638 | passed 215 | |
| `python3 -I scripts/spec-check.py` | 7793638 | 0 errors | report only; Must FRs with no test: 28 (FR-1 to FR-4, FR-6 to FR-12, FR-14, FR-16 to FR-29, FR-34, FR-37) |
| `scripts/contracts-sync.sh --check` | 7793638 | exit 0 | matches `afff9245444c628b74f88d281939f4bc8b4de3e2` |
| Gradle `check`: `test` 826, `integrationTest` 301, 0 failed, 0 skipped, coverage passed | 2c0c5e0 | current | `git diff --stat 2c0c5e0 7793638` touches only `.claude/`; no source, build, Gradle, contracts or scripts change |
| `scripts/osv-scan.sh` | 546ceeb | not run, still current | no dependency change on the branch since |
| Docs checks (dashes, 120 characters) | 7793638 | passed | `DIRECTION.md`, `guard.py`, `test_guard.py` |
| DB audit | 498a5f0 | current, 0 DIFFERENT | no schema change |
| Kind | none | not run | an owner slot, before the rollout (below) |
| `DIRECTION.md` | 5aed118 | current | checked at `7fb9c09`; later commits change only hooks. "At risk" on the Must Drift the owner carries |

### Guard probes at 7793638

These were dry probes: the hook input piped into `guard.py`. It only prints a verdict, so nothing ran.

| Command | Verdict |
| --- | --- |
| The owner's `git worktree add ../x -b release-prep main && cd ../x && git merge python-track` | allowed (a merge into `release-prep`) |
| `worktree add -b feat <p> && git -C <p> checkout main && git -C <p> merge x` (R2-1) | deny |
| The same with `cd <p> && git switch main && git merge`, with `checkout main`, with `checkout -f main`, with `master`, from `<p>/src`, and after `cd` back out | deny |
| `worktree add <p> x && git -C <p> switch main && git -C <p> merge x` | deny (it asked before the fix) |
| `... checkout main; git -C <p> pull . x` | deny |
| `... checkout main -- `, `switch --detach main`, `checkout origin/main`, `checkout -`, `checkout v1.0` before a merge | ask |
| `... checkout -b feat2 && merge`; `git checkout project-notion-alignment && git merge y`; `git merge y` | allowed |
| `git checkout main && git checkout -b feat && git merge y` | deny (cautious, as the implementer notes) |
| An existing worktree (`project-service-auth-amend`): `checkout main && merge`, or `cd && switch main && merge` | deny |
| The first re-review's forms (`worktree add <p> main && merge`, `git -C <missing> merge`, `switch main && merge`) | deny, ask, deny as before |
| `sed --expr='w .claude/settings.json'`, `--expr='w …CONSTITUTION.md'`, `--e='w …guard.py'`, `--ex 'w …'`, `--expre='s/a/b/w …'`, `--in=bak`, `--fi=x.sed` on a config file (R2-4) | ask |
| `sed --expr='1r .env' README.md` | deny (secret) |
| `sed --q --expr=p`, `sed -n --expr=25,50p`, `sed --regexp-e -n p` on `guard.py`, `sed -n 25,50p` | allowed (reads) |

R2-1 is closed: a checkout inside a worktree this command added now updates that worktree's branch
(`guard.py` `Ctx.set_branch`), and a name that is not a local branch is treated as unknown, so the merge asks.
R2-4 is closed: every unambiguous prefix of a GNU sed long option is read as the full option (`sed_long`).

### R2-3

Closed. `DIRECTION.md` no longer lists TR-32 as Drift. FR-31, FR-33 and FR-3 are Done, FR-30 is In progress because
TR-27 waits on task 8, and the Count table and the Drift table follow. The header names `7fb9c09`, with the code
as of `0d9e82b`.

### Constitution at 7793638

| Article | Result | Evidence |
| --- | --- | --- |
| I Spec-driven flow | pass | every commit cites IDs; spec-check 0 errors; Must Drift reported and carried by owner rulings R-1 and R-5 |
| II Tech stack | pass | version 3 enforcement line; the gRPC pins are approved in `dependency-versions.md`; no dependency change since the OSV run |
| III Architecture limits | pass | version 3 allows `NOT_MANAGER`, as TR-32, FRD and `java-code.md` say; databases 0 DIFFERENT |
| IV Coding conventions | pass | no Java change since `2c0c5e0` |
| V Testing and quality | pass | 826 and 301 passed, 0 skipped, coverage passed; hook suites 402 and 215 |
| VI Safety guardrails | pass | merges into `main` and protected-branch resets are denied in every form probed; writes to the hooks, settings and constitution ask; the interpreter limit is stated in `guard.py:8-9` (owner's choice, below) |
| VII Docs | pass | changed docs clean; the handoff rewrite is deferred to the first commit after the merge |
| VIII Agents | pass | the plan was ratified by the owner; the owner confirmed constitution version 3 |

### Findings

Critical: none. Important: none. Minor: none new.

### Open for the owner (not merge blockers)

| Item | Decision | Reason |
| --- | --- | --- |
| Handoff rewrite (first re-review minor 1) | defer | the lead's first commit on `main` after the merge, before any other session starts from it |
| Interpreter writes (`python3 -c`, heredoc scripts, `node -e`) to the constitution, hooks and settings | defer | a stated limit of the guard, also on `main`; the owner decides whether a guard task closes it |
| Merges or checkouts through worktrees made outside the command, aliases, `--git-dir` | defer | git reads the branch for an existing worktree (probed: deny); `--git-dir` asks; aliases are resolved by the existing alias check |
| FR-32.1, FR-32.2 and TR-27 (task 8) | defer | waits for the owner's push of `web-block-contracts` `access-actions-v1` (R-1) |
| FR-9.2, FR-18 | defer | known Must Drift for the next owner (R-5) |
| Project's queue for `provisioning.publish_completed.v1` in Notion S2 | defer | owner question (DIRECTION Q-1), before J4 |
| Kind check, mesh steps, V6 rollout with backup | before the rollout | `.claude/docs/runbooks/j1-kind-exit.md`, `grpc.md`, `migrations.md` |

### Not proven

Unchanged from the first re-review (Kind, Istio, DOKS, backups, TLS, scale, other repositories). The guard probes
cover the forms listed above, not every shell form.
