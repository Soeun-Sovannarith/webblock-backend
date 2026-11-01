# Wave 6 independent validation (W6-V), 10 October 2026

Validator: task W6-V, who wrote none of this code. Base: branch `python` at `87b9fa8` (every wave 6 task merged: R, S, K, M, L, D, B, E, F, E2), in a fresh worktree with its own `bin/` and `.venv`. Evidence files (git-ignored): `.superpowers/sdd/python/w6v-evidence/` in the validator's worktree (`gates.txt`, one log per suite, `plants.txt`, the Loki dumps).

**Verdict: APPROVE WITH FINDINGS.** 0 critical, 0 high, 2 medium, 5 low. Every gate and suite named in the brief ran from nothing and exited 0, except `make py-cover` (a gate-tool fault, finding M-1) and the sixth `proto-demo` run (a node-capacity fault, finding M-2). 23 planted defects were caught and 1 was an equivalent mutant (not a defect). The stack is down and the worktree is clean.

## 1. Gates and suites from nothing

All commands ran in the validator's worktree. Docker held only the owner's three unrelated containers before and after (`magical_villani`, `inspiring_dirac`, `mcp-excalidraw-canvas`); `postgres` and `rabbitmq` containers of the owner were not present on this machine and nothing of the owner's was touched. Kind context was checked before cluster calls: `kind-webblock-proto`, `https://127.0.0.1:38583`, kubeconfig `bin/kind-state/proto/kubeconfig` of the worktree.

| Command | Exit | Result |
| --- | --- | --- |
| `make py-tools kind-tools py-sync` | 0 | tools installed in the worktree |
| `make py-fmt-check` | 0 | 398 files already formatted |
| `make py-lint` | 0 | all checks passed |
| `make py-typecheck` | 0 | mypy strict, no issues in 397 source files |
| `make py-layers` | 0 | import-linter: 5 kept, 0 broken |
| `make py-test` | 0 | **4593 passed** |
| `make py-cover` | **2 (fails)** | see M-1. Unit-mirror coverage measured by hand: domain 99.39 percent (1147 of 1154), application 96.02 percent (2893 of 3013), both above 80. The combined run counted 92 percent total with 60 failed and 199 errors because Kind was not up |
| `make py-test-integration` | 0 | **377 passed**, 203 deselected, 0 skipped (real PostgreSQL 18) |
| `make py-test-messaging` | 0 | **20 passed**, 0 skipped (real RabbitMQ 4.3.6) |
| `make proto-up` | 0 | from nothing in 433 s; policies, network policy, Loki and shipper installed |
| `make proto-smoke` | 0 | 15 PASS, 0 FAIL (routed-release refusal and the Loki audit line included) |
| `make proto-test-kind` | 0 | **187 passed** in 329 s |
| `pytest -m catalog_live tests/integration/catalog` | 0 | **9 passed** |
| `pytest -m jenkins tests/integration/jenkins` | 0 | **7 passed** |
| `make proto-run`, `make proto-e2e` | 0 | **53 passed** in 2285 s (matches W6-E2's 53) |
| `make proto-demo` runs 1 to 5 | 0 | 2 passed each (156, 85, 92, 88 and about 90 s) |
| `make proto-demo` run 6 | 2 | both demo tests failed at the deploy step: M-2 |
| After the plants: restart and `tests/e2e` intake, audit, MCP, promote, api_surface | 0 | 17 passed (restoration proof) |
| `make proto-stop proto-down` | 0 | stack down, containers gone |

Not run, and not counted as passed: osv-scanner / `py-vuln` (suspended for the prototype, decision `0014` section 5), the Go suites (Go is reference only and is being removed; D reported that the Go contract tests break on the changed contracts, unverified here), runtime image scanning, CI. Skipped tests: none seen in any suite log.

## 2. Findings

### Medium

**M-1. `make py-cover` cannot pass, for two reasons.** Evidence: `w6v-evidence/py-cover.log`.
- (a) The core-package step fails its own inventory check: `provisioning.core.domain: the report counts 1154 statements, the inventory 1151`, and `provisioning.core.application: 3013 vs 2931`. I traced the domain case to `core/domain/release.py` (146 counted by the inventory run, 149 when the module executes). `scripts/py/cover.py` was last changed in the P0 commit `c4f7975`, and wave 6 touched only `domain/__init__.py` and `domain/outcome.py`, so this is not wave 6's doing, but nobody ran the target to find out. It is the only tool that enforces the 80 and 70 percent gates of CLAUDE.md section 4.
- (b) Its last phase runs every suite under `tests/`, which includes the Kind suites and the Testcontainers suites. The rules forbid running those at the same time (one Kind stack, never Testcontainers next to Kind), so the phase cannot succeed on this machine as specified.
- Fix: repair the inventory comparison (count the same files the report counts), and make phase 2 take the coverage data of separately run suites (`coverage combine`) instead of one pytest run.

**M-2. Repeated live runs on one stack exhaust the Kind worker node; no test removes its sites.** Evidence: demo run 6 (`proto-demo-6.log`): both tests failed with `deploy ... failed` and the database shows `progress_deadline_exceeded` for six deploy commands; `kubectl get events` shows `FailedScheduling ... 1 Insufficient memory`; `kubectl describe nodes` showed the worker node at **99 percent memory requests (15546 Mi)** with 61 tenant pods left by the earlier e2e and demo runs. Every live release stays running with 256 MiB requested, and the tests never delete their sites. A single from-nothing run (e2e then demo) fits, a second demo on the same stack does not. After I deleted the `wb-*` namespaces and restarted Provisioning, everything ran again. The consequence is a flaky signal in the lead's repeat runs, not a product defect. Fix: a test fixture or make target that deletes the tenant namespaces of finished tests, or a lower request in the test sites.

### Low

- **L-1. Stale architecture text.** `.claude/ARCHITECTURE.md` line 230 says MCP `tools/list` returns exactly eight tools; the code has ten (`Action` enum: three mutations, `build_release`, six reads). Lines 26, 83, 247, 260 and 281 still describe `sync_domains`, a ListenerSet and per-domain certificates as designed, although decision `0014` section 23 removed custom domains. Lead-owned file.
- **L-2. Down sections in two migrations.** `00012` and `00013` still contain `-- +goose Down`; `00016` (and the others) do not. The handoff and decision `0014` section 17 speak of forward-only migrations. Pre-existing, not wave 6.
- **L-3. The API process holds a Jenkins API token and the `provisioning_build` database login** (keys `JENKINS_API_TOKEN`, `PG_BUILD_USER`, `PG_BUILD_PASS` in `env/api.env`), for the `build_release` endpoint. It holds no cluster credential (see section 4). The build/API split is production hardening (decision `0014` section 19), so this is recorded, not blocking.
- **L-4. The fence admission policy checks the form of the annotation, not that it grows** (W6-K's own note). Stale-writer protection therefore rests on the adapter's read-then-apply window plus the SQL fence (both tested: planted OWN-1 and OWN-5 are caught). Acceptable for the prototype.
- **L-5. The generated shop answers HTTP 200 with its outage page when the catalog is unreachable.** I paused `webblock-catalog-db` and fetched a live shop: `200` in 6.0 s with 0 product cards and the "We'll be right back" text. This matches the signature of W6-F's flaky demo run (see section 5). The status code cannot tell an outage from a page, so only `/readyz` (503) is honest. The shop is a sample site, not Provisioning code. Also a low note: the dead-letter re-drive audits an unknown project with the nil UUID (W6-E2 deviation 3).

## 3. The gap and QA tables

Evidence is a test and its result in the runs of section 1 unless marked. All e2e files are under `tests/e2e/`.

| Row | Evidence | Status |
| --- | --- | --- |
| PR-01 | `test_api_surface.py` 1 (404, no row written) and 2 (403); `test_scenarios_mcp.py` scope test. Plant of the scope check caught live | closed |
| PR-02 | `test_scenarios_intake.py` pr02 (second release refused, first commit kept). Plant caught live | closed |
| PR-03 | `test_flows.py`, idempotency cases in `test_edge_cases.py`, integration `test_store_accept` family | closed |
| PR-04 | `test_edge_cases.py`, `faults/test_qa_*`, concurrency integration tests | closed |
| PR-05 | `test_scenarios_intake.py` pr05 (rejected, no release, refused audit line). Plant caught live | closed |
| PR-06 | `test_scenarios_build.py` (retry after a failed build, concurrent `build_release` refused), `faults/test_build_failed_attempts.py`, `faults/test_build_jenkins_restart.py` | closed |
| PR-07 | privileged BuildKit, isolation proof is production work (OD-37, OD-62) | **still open** (by decision) |
| PR-08 | `integration/stack/test_tenant_network_live.py` (8 tests: no cross-tenant traffic, no API server, no metadata address; Gateway to site and site to catalog allowed), preview probe before the switch | closed (prototype) |
| PR-09 | `test_flows.py`, `test_scenarios_promote.py` pr09 (release_outdated). Plant caught live | closed |
| PR-10 | `test_scenarios_promote.py` pr10 (first publish failure goes offline; live keeps serving while a promotion fails; unconfirmed restore leaves `uncertain` until the operator rolls back), `faults/test_qa_worker_killed.py`. Plant caught live | closed |
| PR-11 | `faults/test_recovery_stuck_project.py`, `test_recovery_reconciler.py` (3), `test_recovery_route_reclaim.py` (2), `test_recovery_deploy_margin.py`; the `route_uncertain` alert is an audit line with `alert=true` (found in Loki) | closed |
| PR-12 | `test_api_surface.py` 5 (listings) and 6 (diagnostics, REST and MCP) | closed |
| PR-13 | `test_api_surface.py` 3 (limit reached at acceptance) and 4 (entitlement withdrawn before the run ends the promote refused) | closed |
| PR-14 | `test_flows.py` suspend and resume keep data, `faults/test_hosting_deletion.py` (deleted project offline, images kept, never resumed) | closed |
| PR-15 | `test_mcp.py`, `test_scenarios_mcp.py` (every tool called; a token without the scope is refused by every tool and route). Ten tools, none reaches a hosting kind | closed |
| PR-16 | unit tests for bounds, `faults/test_qa_broker_paused.py`, `test_qa_database_paused.py` | closed (prototype) |
| PR-17 | `integration/stack/test_site_policies_live.py` (30 planted bad objects refused by the named policy), `integration/kubernetes` dry run of every kind (22) | closed; monotonic fence only by form (L-4) |
| PR-18 | `test_worker_rbac_live.py`, reconciler and cleanup tests, `test_forced_apply.py` | closed |
| PR-19 | this report | closed on delivery |
| QA-01 | `kubernetes/test_probe_live.py` (1 GiB declared and chunked bodies), unit `test_site_probe.py`. Plant caught | closed |
| QA-02 | `jenkins/test_jenkins_live.py`, unit `test_jenkins_build_system.py` | closed |
| QA-03 | `faults/test_hosting_retry.py` (2), `unit/.../test_hosting_qa03.py` | closed |
| QA-04 | `faults/test_bootstrap_namespace_rebuilt.py` (2), `unit/hosts/bootstrap/test_recheck.py` | closed |
| QA-05 | `faults/test_recovery_deploy_margin.py`, unit `test_deploy.py` | closed |
| QA-06 | unit `infrastructure/config/test_worker_timing.py` | closed |
| QA-07 | `faults/test_recovery_route_reclaim.py` (annotate form and patch form), `inventory_live`. Plant OWN-2 (a second forced apply) caught | closed |
| QA-08 | `kubernetes/test_probe_live.py` (`qa-flap` 0 of 10 with three in a row), unit `test_site_probe.py` | closed |
| QA-09 | dropped (custom domains removed, `0014` section 23) | n/a |
| QA-10 | `faults/test_build_failed_attempts.py` (2), `test_build_jenkins_restart.py`, `jenkins/test_jenkins_live.py`. Plant (no cancel) caught | closed |

## 4. Security and database invariants (observed)

- **Single writer and the fence.** The worker writes the per-site objects; the API host cannot import Kubernetes (import-linter contract `hosts-no-kubernetes`; plant OWN-3 made `py-layers` fail, `Contracts: 4 kept, 1 broken`). Every worker write is fenced in the adapter (`apply_route`, `apply_preview`, deletes call `_check_fence`) and in SQL (`fenced.py`: hosting row at the command number, dispatch claim, command status). Plants OWN-1 (adapter fence off), OWN-5 (SQL fence off) and OWN-7 (claim check off) were each caught.
- **`reclaim_route` is the only forced apply.** `site_cluster.py:583` is the only call with `force=True`; `_client.py` refuses force for anything but an HTTPRoute; the AST test `test_forced_apply.py` guards it, and plant OWN-2 (a second forced apply) was caught. Its only caller is the operator rollback in `routing.py` (`_point_or_reclaim`), which audits `route_reclaim`.
- **No token in any log or audit line.** I extracted 44 secret values from `tokens.json`, `provisioning.env` and `env/*.env` (never printed) and searched five Loki dumps and every process log: 0 hits for any token, password or URL password (the only matches were token subject names and the Jenkins URL, which are not secrets). `grep -i password` over 5000 Loki lines: 0. 485 audit lines were present, including `catalog_credential_rotated` with no value.
- **The API holds no cluster credential.** Its environment file has no kubeconfig key, `/proc/<api pid>/environ` has no `KUBE` variable, the import contract bars the client (L-3 lists what it does hold).
- **Worker and bootstrap scope.** With the worker's own kubeconfig: `can-i create namespaces` no, `get secrets` no, `create rolebindings` no, `patch httproutes` yes in the tenant namespace, `list namespaces` yes (the reconciler's read). Deleting the routed release's Deployment with `--dry-run=server` is refused by `ValidatingAdmissionPolicy webblock-site-routed-release`. The bootstrap scope policy and the tenant network policy are covered by 187 passing Kind tests; plants of the routed-release policy (live, policy reapplied and test failed) were caught and the policy was restored and reapplied.
- **Migrations.** Only `00016_usage_outbox.sql` was added after the base `2a6d341` (`git diff --stat 2a6d341 HEAD -- src/migrations`); `00014`/`00015` unchanged; `00016` has no Down section. `.claude/PLATFORM_DB_DESIGN.md` against its last revision 19 commit `170f58d`: 6 lines added, 2 changed, all in the grants/guards/roles rows (usage outbox, `usage_outbox_update_shape`, `provisioning_operator`); no business table, column or enum changed.
- **Grants.** On the live database `provisioning_api` holds `SELECT` on exactly nine columns of `source_published_events` (`attempts, finished_at, generation_request_id, mismatched_repeat_count, outcome_code, project_id, received_at, release_id, status`) and no table-level right; `git_commit_sha` and `github_repository_id` are denied (`has_column_privilege` false). `provisioning_worker` has `SELECT, INSERT` plus `UPDATE` on six columns of `usage_outbox`; nobody but the migrator has DELETE. `provisioning_operator` has only the inbox re-drive columns. Plant E (a tenth column in the grant) was caught by `test_schema_00016`.

## 5. Observed status matches intent (one promotion)

Project `01a122ab-57a9-76a0-b573-1adf469dcd8d`, release `01a122ab-5a63-7647-bbba-7f082dc3c82b`, from a live run:
- The database shows the promote command with command number 2, status `succeeded`, release live, digest `sha256:1418e4f2...919e`.
- The cluster's `route-<project>` carries annotation `command-number: 2` and routes to `rel-01a122ab-5a63-...`, the promoted release. The Deployment carries `command-number: 1` (its deploy), image digest identical to the database digest, and `release-id` equal to the release.
- The Deployment's `intent-digest` annotation (`sha256:438322ba...2fcb`) equals the digest I recomputed independently with `scripts/prototype/render-examples.py` for the same project, release and image.
- Through the Gateway over HTTPS with the local CA, the response header is `x-webblock-release: 01a122ab-5a63-7647-bbba-7f082dc3c82b`.

**The flaky `proto-demo` run (0 product cards at `test_demo.py:113`).** Not reproduced: five clean runs after the first, all green; the sixth failed for a different reason (M-2). Likely cause, not proven: the page is `force-dynamic`, and when the catalog read fails the shop renders its outage page with HTTP 200 and no cards. I reproduced exactly that signature by pausing the catalog container (200, 6.0 s, 0 cards, outage text). A first request after promotion under load (W6-F ran the demo after the whole e2e suite, with the node near capacity, M-2) can meet a slow or failed catalog connection while `/readyz` passed a moment before. The test asserts the status and the card count but not the body. I temporarily widened the assertion message to print the page, ran the demos, then restored the file. Suggested: print the body in the assertion, retry the visit for a few seconds, and let the sample shop answer 503 on outage.

## 6. Planted defects (all in the validator's worktree; each restored, `git status` clean after)

Caught (test and result in `plants.txt`): R sync_domains enabled in the worker (`test_consumer`); S actor dropped from the audit allow-list (`test_audit`); K routed-release policy consults the wrong routes (live policy applied, `test_the_worker_cannot_delete_the_routed_release...` failed, policy restored and reapplied, 2 passed); M probe reads an unbounded body (`test_site_probe`); L overdue build not cancelled (`test_a_swept_attempt_is_audited_and_its_build_cancelled`); D project access never checked (`test_accept`); B no deferral budget (`test_a_delete_refused_every_round_is_given_up...`); E API inbox grant widened to `git_commit_sha` (`test_schema_00016`, integration); E2 a refresh whose publish failed still acks (`test_a_refresh_that_is_not_confirmed_leaves_the_original_parked`). W6-F's scenario tests, planted live with the four processes restarted on the planted source: PR-05 wrong repository accepted (failed), PR-02 mismatched repeat treated as duplicate (failed), PR-09 `release_outdated` never raised (failed), PR-15 and PR-01 scope never checked (2 failed), audit written twice (failed, "an audit action was written twice"), PR-10 unconfirmed restore taken as verified (failed). My own, in areas no task planted: adapter fence check off (`test_apply_route_over_a_higher_command_number...`); a second forced apply (`test_reclaim_route_is_the_only_forced_call_in_the_package`); the API host importing the Kubernetes client (`py-layers` broken); worker write without the project's fence in SQL (`test_a_stale_fence_is_refused...`); worker write without the dispatch claim (`test_a_lost_claim_is_refused...`); no usage event when a release goes live (`test_confirm_live_writes_one_pending_usage_event...`); the outbox insert guard without its shape check (`test_the_insert_guard_refuses...[sent]`) and without the live-release check (`...[not-live]`).

Not a defect: OWN-4 (dropping only the `status <> 'pending'` clause of the outbox insert guard) was not caught because the clause is redundant: the table constraint `usage_outbox_sent_matches_status` plus the guard's `sent_at` test already refuse a sent row. I treated it as an equivalent mutant and replaced it with OWN-4b and OWN-4c.

W6-E2's refresh workaround for RabbitMQ 4's delivery limit (OD-70) is covered by messaging `test_dlq.py` (5 tests, including a listing repeated 25 times without loss and a crash between confirm and removal giving a duplicate, never a loss), the live `test_dlq_redrive.py` (passed in the 53), and my plant. OD-70 itself (a broker policy for production) stays open as recorded.

## 7. File ownership

`git diff --name-only` over the eight merge commits (`68fa92a` R+S+K, `6cfcd77` M, `5f4e092` L, `0642eab` D, `bf66be6` B, `21b069d` E, `c333f34` F, `574ae25` E2) shows files edited by more than one merge. Every overlap is one of the recorded exceptions or follows from the sequence: the `run-provisioning.sh` family (D, E, E2 and its test), the shared audit declarations (`audit.py`, `test_audit.py`: S then L then E2, additive enum values), the worker wiring (B and E, rebased in order), test files of code that R had deleted or that D's audited wrappers changed, and E2's additions to E's operator CLI. No file changed meaning between tasks in a way that contradicts the merge checklist. The F merge changed no production file (checked: tests and docs only).

## 8. Skipped checks and blockers

Skipped, with reason: `py-vuln`/osv-scanner (suspended, `0014` section 5), Go suites and Go contract tests (Go is leaving; D's claim that they break is unverified), runtime image scanning, CI (none exists). Not testable here by design: PR-07 isolation of hostile builds, a real cluster, multiple replicas, real Project and Payment contracts. No blockers.
