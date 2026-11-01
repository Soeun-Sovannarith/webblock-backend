# Provisioning: Direction check

**Status:** Draft
**Checked:** branch cqrs-layout at commit ffbc220
**Verdict:** At risk, Must Drift on 8 FRs; the go-live retry rule (FR-19, FR-21, FR-22) has no plan task yet

## In short

At risk: 32 of 51 current FRs are Done, every Phase C Must offer but PR-20 (FR-19's retry rule) is Done or In
progress, and the 7142 unit and contract tests pass. Must Drift sits on 8 FRs: FR-2 and FR-8 wait for owner
rulings, FR-19, FR-21 and FR-22 are the owner's 3-try go-live rule that the FRD marks not built, and FR-1, FR-5 and
FR-43 are known and planned in the auth and telemetry handoff. New here: the token check also keeps a 2 minute skew
cap and lets production accept more than RS256 (TR-26), and two pieces of code (the reconciler and the bootstrap's
site catalog login) have no FR. Before the merge the owner decides when the 3-try rule is planned and gives a Kind
slot; Kind has never run on this branch.

**Read next:** [BRD.md](BRD.md) to start the chain again; the items below link into each document.

## Top 5 to look at

1. **Go-live is tried once, not 3 times** (FR-21, FR-22, FR-19; FR-48 later): the owner's rule (3 tries, waits of
   30 s and 60 s, visitors kept on the last good page) is in the FRD and marked not built. No plan task covers it,
   so it is open drift. The tagged tests of FR-21.1 and FR-22.1 prove the old one-try rule and must change with
   it. PR-3's "Done when" still holds today. Decide: plan it before this merge, or as the first task after it.
2. **Identity rules waiting for you** (FR-2, FR-8): a token without `user_id` answers 404, not 403, and
   `CheckAccess` sends no `x-user-id`. Both are small (handoff `auth-telemetry-alignment.md` tasks 3 and 6). Rule
   whether they are built now, on this branch or the next, or wait for `web-block-commons` (TR-32).
3. **The token check is looser than TR-26 says** (FR-1, TR-26): `typ` is not checked, the skew cap is 2 minutes
   (`config/identity.py:85`, TR-26 says 60 s), and production accepts any asymmetric algorithm, not only RS256.
   The FRD's "Drift seen" names only `typ`. Handoff tasks 2 and 4 cover all three and are marked "now". Action:
   add the skew and RS256 gaps to "Drift seen" (spec-writer, with your OK) and schedule tasks 2 and 4.
4. **Merge checks never run on this code** (TR-12, FR-20, FR-52): Kind has not run on `cqrs-layout` though the
   hosts' wiring changed; the Notion DB audit is owed (`after-phase-c.md` item 1); the preview host cases are
   written but never run. Action: give a Kind slot and run the DB audit before the final review.
5. **Code no FR asks for** (Orphans): the reconciler (`reconcile/background/reconcile.py`), the bootstrap's site
   catalog login and Secret (`hosts/bootstrap/tenants.py`, prototype only, OD-46), and the owner's
   `deploy_release` and `promote_release` operations that GATE1 O2 removed (PRD Q-4). Decide: give each an FR, or
   remove it when PR-26 ships.

## Count

| Level | Done | In progress | Not started | Drift | Orphan |
| --- | --- | --- | --- | --- | --- |
| BR | 7 | 2 | 0 | 6 | |
| PR | 13 | 6 | 2 | 6 | |
| FR | 32 | 6 | 5 | 8 | |
| TR | 35 | 9 | 5 | 4 | |
| Code and work | | | | | 3 |

Current items only: 15 BR, 27 PR, 51 FR, 53 TR.

## Trace

One row per current FR under the BR of its first PR. The TR column gives TR numbers; TR-10, TR-11 and TR-12 (the
schema rules) serve FR-9 to FR-42 and are left out to keep rows short. An FR takes Drift from a TR only where the
TR's gap is that FR's part (TR-27's missing 403 is FR-2's, not FR-4's; TR-48's admin reads are FR-51's).

### BR-1: A published change becomes a live site with no staff work, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-11 | PR-1 | 7, 25, 36, 46, 49 | `test_store_build.py`, `test_build_dispatch.py` | Done |  |
| FR-12 | PR-1 | 7 | `test_store_build.py`, `rules/test_build.py` | Done |  |
| FR-13 | PR-7 | 7 | `builds/test_request_build.py` | In progress | closest tests, no ID |
| FR-14 | PR-26, PR-10 | 4, 13, 39 | `test_store_acceptance.py` | Done | admin path; automatic is FR-47 |
| FR-15 | PR-26 | 4, 23 | `test_store_jobs.py`, `test_dispatcher.py` | Done |  |
| FR-17 | PR-26 | 6, 34, 35, 44, 46, 52 | `test_deploy_store.py`, `test_deploy.py` | Done | owner deploy today |
| FR-19 | PR-26, PR-20 | 6, 15, 34, 35, 38, 44, 45 | `test_routing.py` | Drift | 19.2, 19.3: 1 try, not 3 |
| FR-44 | PR-23 | 13 | `test_operator_store.py`, `operator/test_cli.py` | In progress | closest tests, no ID |
| FR-45 | PR-23 | none | `messaging/test_dlq.py`, `operator/test_dlq_tool.py` | In progress | closest tests, no ID |
| FR-47 | PR-26, PR-9 | 20, 38, 51 | search `auto-deploy`, `deploy` on `built` | Not started | phase Later |
| FR-48 | PR-26, PR-3 | 20, 38, 51 | same search | Not started | phase Later; not built (3 tries) |
| FR-49 | PR-26, PR-9 | 51 | same search | Not started | phase Later |
| FR-53 | PR-26, PR-12 | 52 | search `imagePullSecrets` | Not started | phase Later |
| FR-54 | PR-20 | 53 | `test_owner_status_store.py`, `test_hosting_insert.py` | Done |  |

### BR-2: A failed update never takes a working site down, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-20 | PR-21 | 35, 44, 45 | `kubernetes/test_render.py` | In progress | Kind cases written, never run |
| FR-21 | PR-3 | 49 | `routing/background/test_routing.py` | Drift | not built: restores after try 1 |
| FR-22 | PR-3, PR-22 | none | `routing/background/test_routing.py` | Drift | not built: offline page after try 1 |
| FR-23 | PR-3 | none | `test_routing_store.py`, `test_hosting_job.py` | Done |  |
| FR-25 | PR-3 | 5, 39 | `test_claim_race.py`, `test_store_jobs.py` | Done |  |
| FR-52 | PR-29 | none | `kubernetes/test_kubernetes_live.py` (Kind) | In progress | Kind cases never run |

### BR-3: A site is offline exactly while a reason holds, Drift

Drift comes from PR-22 through FR-22 (under BR-2); the FRs below are all Done.

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-26 | PR-18 | 13, 14, 20, 22, 24 | `hosting/background/test_hosting_job.py` | Done |  |
| FR-27 | PR-18 | 14, 22, 24 | `hosting/background/test_hosting_job.py` | Done |  |
| FR-29 | PR-17 | 14, 22, 24 | `test_accept_owner_status.py`, `test_owner_status_store.py` | Done |  |
| FR-30 | PR-18 | 14, 22 | `consumers/test_payment_events.py` | Done |  |
| FR-31 | PR-18 | 22 | `test_hosting_job.py`, `test_hosting_qa03.py` | Done |  |

### BR-4: An owner acts only on their own sites, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-1 | PR-6 | 26, 32, 33, 43 | `identity/test_jwt.py` | Drift | `typ` unchecked; skew 2 min |
| FR-2 | PR-6 | 27, 32 | no test; `project/grpc_access.py:59` | Drift | no `user_id` is 404, not 403; owner ruling |
| FR-3 | PR-6 | 29, 48 | `project/test_grpc_access.py`, `test_access.py` | Done | 404 equality by closest tests |
| FR-5 | PR-6 | 20, 21, 31, 43 | `test_grpc_access.py` | Drift | production uses HTTP check |
| FR-8 | PR-6 | 30, 32 | no test; `grpc/call.py:72` | Drift | no `x-user-id`; owner ruling |
| FR-46 | PR-24 | 3, 19 | `mcp/test_tools.py` | In progress | closest tests, no ID |

### BR-5: Staff can act on any site, named, In progress

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-4 | PR-10 | 27, 48 | `operations/test_gate.py` | Done |  |
| FR-33 | PR-10 | 18 | `test_admin_commands.py`, `operations/test_admin.py` | Done |  |
| FR-34 | PR-11 | 18 | `test_admin_commands.py`, `operations/test_admin.py` | Done |  |
| FR-35 | PR-12 | 6, 18, 52 | `test_redeploy_executor.py`, `operations/test_admin.py` | Done |  |
| FR-36 | PR-14 | 18 | `test_admin_commands.py`, `operations/test_admin.py` | Done |  |
| FR-50 | PR-13 | 48 | `operations/test_gate.py`, `config/test_identity.py` | Done |  |
| FR-51 | PR-28 | 48 | `operations/gate.py:70` asks every read | Not started | phase Later |

### BR-6: Payment hears of each publish gone live, once, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-42 | PR-19 | 15, 23, 24 | `messaging/test_usage_events.py`, `test_usage_publisher.py` | Done |  |

### BR-7: Owners and staff see where a version is, Drift

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-37 | PR-4 | 16, 17, 38 | `operations/test_reads.py`, `test_views.py` | Done | 37.3 by a closest test |
| FR-38 | PR-4, PR-8 | 16, 17 | `test_read_history.py`, `test_bus_surface.py` | Done |  |
| FR-39 | PR-4 | 16, 17 | `operations/test_reads.py`, `test_views.py` | Done |  |
| FR-40 | PR-4 | 16, 17 | `operations/test_reads.py`, `test_operations.py` | Done |  |
| FR-41 | PR-4 | 16, 17 | `test_read_history.py`, `test_bus_surface.py` | Done |  |
| FR-43 | PR-25 | 32, 33, 40, 41, 43 | `test_tracing.py` | Drift | no `x-request-id`, `span_id` |

### BR-8: One published commit is one version, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-9 | PR-15, PR-1 | 13, 14, 20, 22, 24, 31 | `test_store_intake.py`, `test_release_intake.py` | Done |  |
| FR-10 | PR-15 | 14 | `test_store_intake.py`, `consumers/test_inbox.py` | Done |  |
| FR-32 | PR-15, PR-18 | 22 | `messaging/test_topology_v1.py`, `consumers/test_inbox.py` | Done |  |

### BR-9: One action per site at a time, Done

No FR lists a PR of BR-9 first; PR-13 serves it through FR-50 (under BR-5, Done).

### BR-10: Going offline never deletes code, content or the live version, Drift

No FR lists a PR of BR-10 first; PR-22 serves it through FR-22 (under BR-2, Drift, not built).

### BR-11: A deleted project never comes back online, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-28 | PR-16 | 14, 22, 24 | `test_routing_store.py`, `test_payment_events.py` | Done |  |

### BR-12: Follows the platform rules, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-7 | PR-8 | 17, 33 | `shared/test_strict_json.py`, `http/test_problems.py` | Done | 7.2 to 7.4 by closest tests |

### BR-13: Shared cluster and data stores, Done

No PR; TR-8, TR-9, TR-11 and TR-42 serve it, all Done.

### BR-14: Jenkins builds site images into the Gitea registry, In progress

No PR; TR-25 (Done) and TR-46 (In progress, the Gitea image name, handoff task 12) serve it.

### BR-15: Production refuses every change until contracts are agreed, Done

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-6 | PR-27 | 47 | `shared/test_profile.py`, `api/test_project_access.py` | Done |  |

### Cross-cutting

TR-1, TR-2, TR-8, TR-9, TR-37, TR-42, TR-54, TR-55 and TR-56 serve every FR. All are Done: import-linter keeps the
layer and CQRS contracts (`tests/contracts/test_layers.py` passes), the chart holds four Deployments and the
migrate Job, the settings have no defaults (`make py-test`), and the agent hooks pass their tests
(`.claude/hooks/test_guard.py`, `test_hooks.py`).

### TRs not Done

| TR | FR | Status | Evidence |
| --- | --- | --- | --- |
| TR-26 | FR-1 | Drift | no `typ` check; `MAX_CLOCK_SKEW` 2 min (`config/identity.py:85`); no RS256 pin |
| TR-27 | FR-2, FR-4 | Drift | no `user_id` is 404 (`grpc_access.py:59`); the roles part is Done |
| TR-30 | FR-8 | Drift | `grpc/call.py:72` sends no `x-user-id` |
| TR-41 | FR-43 | Drift | no `x-request-id` header (`rabbitmq/client.py`); logs lack `span_id` |
| TR-20 | FR-5, FR-9, FR-26 | In progress | gRPC clients built; production example uses the HTTP access check |
| TR-31 | FR-5, FR-9 | In progress | mesh policy is the owner's (web-block-config); not run on Kind |
| TR-35 | FR-17, FR-19, FR-20 | In progress | `<subdomain>-preview` host, no-store and sub-domain label cases (the dotted host refused) never run on Kind |
| TR-36 | FR-11 | In progress | DevOps proof owed before the first production build |
| TR-40 | FR-43 | In progress | Tempo endpoint not set in production (handoff task 10) |
| TR-45 | FR-19, FR-20 | In progress | one wildcard `*.<site domain>` covering the `-preview` hosts never checked on Kind |
| TR-46 | FR-11, FR-17 | In progress | nested Gitea image name unchecked (handoff task 12) |
| TR-48 | FR-3, FR-4, FR-50, FR-51 | In progress | admin reads still ask `CheckAccess` (`operations/gate.py:70`) |
| TR-49 | FR-11, FR-21 | In progress | no image cleanup exists, so none is deleted; no test |
| TR-32 | FR-1, FR-2, FR-8, FR-43 | Not started | `web-block-commons` waits for approval |
| TR-38 | FR-19, FR-37, FR-47, FR-48 | Not started | no publish-to-live or availability measure |
| TR-43 | FR-1, FR-5, FR-43 | Not started | API egress is DNS and database only (`networkpolicies.yaml`) |
| TR-51 | FR-47 to FR-49 | Not started | automatic flow not built (TRD Q-5) |
| TR-52 | FR-53 | Not started | search `imagePullSecrets` in `src/`: nothing |

## FRs with no test

38 of 51 current FRs have every acceptance row covered by a tagged or a closest test. These rows have neither:

| FR | Acceptance rows without a test | Closest test, no ID yet |
| --- | --- | --- |
| FR-1 | FR-1.2 (the `typ` case) | none |
| FR-2 | FR-2.2 | none |
| FR-8 | FR-8.1, FR-8.2 | none |
| FR-20 | FR-20.1 | `kubernetes/test_dry_run_live.py` (Kind, never run) |
| FR-21 | FR-21.2 to FR-21.6; FR-21.1's tagged tests prove try 1 | none |
| FR-22 | FR-22.2; FR-22.1's tagged test proves try 1 | none |
| FR-43 | FR-43.1 (`x-request-id`), FR-43.2 (`span_id`, `request_id`) | `test_tracing.py` (trace parts) |
| FR-47 | FR-47.1 | none |
| FR-48 | FR-48.1 to FR-48.4 | none |
| FR-49 | FR-49.1 to FR-49.4 | none |
| FR-51 | FR-51.1 | none |
| FR-52 | FR-52.1, FR-52.2 | `kubernetes/test_kubernetes_live.py` (Kind, never run) |
| FR-53 | FR-53.1 to FR-53.4 | none |

18 rows are covered only by closest tests: FR-2.1, FR-3.1, FR-3.2, FR-5.1, FR-5.2, FR-7.2 to FR-7.4, FR-13.1 to
FR-13.3, FR-37.3, FR-44.1, FR-44.2, FR-45.1, FR-45.2, FR-46.1, FR-46.2.

## Drift

Must Drift, split by where it stands. Every item below is Must.

**Waits for an owner ruling**

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| FR-2 (TR-27) | No `user_id`: 403, Project not called | 404 `PROJECT_NOT_FOUND` | `grpc_access.py:59` |
| FR-8 (TR-30) | `CheckAccess` carries `x-user-id` | sends `x-request-id` only | `grpc/call.py:72` |

**Marked "not built yet" in the FRD (the owner's 3-try go-live rule), no plan task**

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| FR-21 | 3 tries, waits 30 s and 60 s, live page between tries | restores after try 1 | `routing.py:472` |
| FR-22 | first deploy: offline page between tries, `failed` after try 3 | `failed` after try 1 | `routing.py:472` |
| FR-19 | a failed try is tried again, 3 in all | one try | `routing.py:407` |
| FR-48 | the automatic go-live uses the same 3 tries | not built (phase Later) | none |

FR-37.3 (a `rolled_back` release reads with its failure code) works today and stays true under the new rule.

**Known and planned** (FRD "Drift seen", tasks in `ai/handoffs/auth-telemetry-alignment.md`)

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| FR-1 (TR-26) | `typ` must be `Bearer` | `typ` not read (task 2) | `identity/verifier.py:18` |
| FR-5 (TR-20) | production asks gRPC `CheckAccess` | production uses the HTTP check (task 11) | `config/api.py` |
| FR-43 (TR-41) | `x-request-id` header; logs `span_id`, `request_id` | neither (tasks 14, 15) | `rabbitmq/client.py` |

FR-5: Project's gRPC `AccessService/CheckAccess` is built on Project's branch `project-notion-alignment`, not yet on
its `main`; task 11 can be planned once that merges and passes on Kind.

**New in this check**

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| TR-26 (FR-1) | skew at most 60 s | cap 2 minutes (task 4, not in "Drift seen") | `config/identity.py:85` |
| TR-26 (FR-1) | RS256 only | production accepts any asymmetric algorithm (task 4) | `config/identity.py:269` |

## Orphans

| Work, code or test file | Where | Suggested home |
| --- | --- | --- |
| Owner `deploy_release`, `promote_release` | `shared/action.py:24`, `operations.py:90` | PRD Q-4: remove with PR-26 |
| The reconciler (foreign managers, fences) | `reconcile/background/reconcile.py` | new FR under PR-3; ask owner |
| Bootstrap site catalog login and Secret | `hosts/bootstrap/tenants.py` | ask owner; contract open (OD-46) |

## How I checked

Commit `ffbc220` (`cqrs-layout`), working tree clean. `make spec-check` (base `main..HEAD`, 96 commits): BR 15, PR 29
(27 current), FR 54 (51 current), 134 acceptance rows, TR 55 (53 current); 38 of 51 current FRs with a tagged test;
Must FRs 45, 7 listed as no test yet (FR-2, FR-8, FR-47, FR-48, FR-49, FR-51, FR-53); 383 code files, 3146 tests,
44 docs read; problems 0. `make spec-check SPEC_CHECK_FLAGS=--strict`: same counts, 0 problems, exit 0.

Read: BRD, PRD, FRD, TRD, `spec.yaml`, `ai/handoffs/after-phase-c.md`, `ai/handoffs/auth-telemetry-alignment.md`,
decision `0025`, and the code at each cited line. Searched tests for FR IDs, `typ_mismatch`, `x-user-id`,
`imagePullSecrets`, `rolled_back`, `build_release`, `redrive` and the preview host. No plan exists in
`.claude/docs/plans/`.

Run by me: `make py-test` at `ffbc220`: 7142 passed in 158 s. Not run by me: `make py-test-integration` 559 passed
and `make py-test-messaging` 34 passed (decision `0025`, at `de31486`); Kind (`proto-test-kind`, `proto-e2e`) never
run on this branch; `make chart-check` and the Notion DB audit not run.

## Questions for the owner

| ID | Question | Why it matters | Suggested answer |
| --- | --- | --- | --- |
| Q-1 | Build FR-2 and FR-8 now, or wait for the commons? | Must Drift stays open | suggestion: now, tasks 3, 6 |
| Q-2 | Plan the 3-try rule before or after this merge? | Open drift (FR-19, 21, 22) | suggestion: after |
| Q-3 | Add TR-26's skew and RS256 gaps to "Drift seen"? | It lists only `typ` | suggestion: yes, with task 4 |
| Q-4 | An FR for the reconciler and the site catalog login? | Code with no FR | suggestion: yes; catalog after OD-46 |

## Glossary

| Word | Meaning here |
| --- | --- |
| Drift | The code differs from the requirement |
| Open drift | Drift on a Must item that no plan or handoff task fixes |
| Orphan | Code no requirement asks for |
| Closest test | A test with no ID that still proves a row's "Then" |
| Try | One claim of the one `promote` command |
| Kind | The local Kubernetes cluster of the prototype stack |
| `CheckAccess` | Project's call that says whether a user may act on a project |
| Handoff task | A numbered task in `ai/handoffs/auth-telemetry-alignment.md` |
