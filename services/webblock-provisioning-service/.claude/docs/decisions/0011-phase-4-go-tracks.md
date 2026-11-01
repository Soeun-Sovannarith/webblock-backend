# 0011: Phase 4 Go tracks: acknowledgement, candidate failures, catalog credential, migration `00010`, revision 17

**Partly superseded by decision [`0013`](0013-python-port-without-crossplane.md)**: sections 1 and 9, the function's
check in section 7, and the Crossplane-specific rulings of sections 11 and 12 no longer apply. Sections 2 to 6, the
`ImageLocation` port of section 7, and the F2 guarded-delete rule carry over to the Python service; section 5 is amended
by `0013` section 9, K1 (the bootstrap component runs `access_setup`, not the deploy). Phase 4 stopped without a
validation report.

Status: **Current for the sections that still apply** (see the note above). The Python worker and migration `00010`
implement them. Amends `0001`, `0002`, `0005` section 5 and `0009` sections 6, 7, 8 and 9 where it says so; where
they differ, this record wins.

## 1. Acknowledgement of current intent (amends `0009` section 6)

Measured on Kind (`0009` section 13): Crossplane's own writes raise an XR's `metadata.generation` from 1 to 4 right
after the worker's create, status can briefly move backwards, and `Ready` flaps during a rollout. The worker therefore
acknowledges its write only when **one read** of the XR shows all of:

- `Ready=True` with `observedGeneration` equal to the XR's current `metadata.generation`;
- that `metadata.generation` is at least the generation the worker's write returned;
- `spec.operationGeneration` equals the command's fence and the intent fields equal what the worker wrote;
- `status.observedOperationGeneration` equals the fence;

and the worker's own read of the composed Deployment shows the pod-template annotation
`websites.webblock.internal/intent-digest` equal to the digest the worker recomputes from its SQL records (`0009`
section 13), the Deployment at its current generation, and its replicas updated, ready and available, with ready
EndpointSlices for the release Service. `Synced=True` alone is never enough. The field-wise transition rule (`0009`
section 6) makes "at least" safe: a later generation with the same `operationGeneration` carries the same intent.

## 2. No in-cluster HTTP probe by the worker in Phase 4 (amends `0009` section 6)

The tenant network policy admits ingress only from the Gateway, and the worker has no proxy right; neither is widened.
The worker's acknowledgement is section 1 (Crossplane, Deployment with intent digest, rollout, ready EndpointSlices, the
rendered TCP readiness probe). The Kind acceptance suite checks HTTP content from a short-lived test pod inside the
tenant namespace, created by the harness, not the worker. The worker's own content check comes with promotion through
the Gateway (Phase 5). PR-08 "content access" is therefore proven in Phase 4 by the suite only, and every report says
so.

## 3. Candidate failures (revision 17 item A; amends `0001` section 2 and `0009` section 7)

- A deploy that fails ends the **command** `failed` with a failure code; the **release stays `built`**, so a new deploy
  command can retry it without a new generation. A readiness timeout ends the command; it is not retried inside the same
  command.
- The release moves `built → failed` only when retrying cannot help: the cluster refuses the intent itself (the API
  server's validation or admission, or the composition function's fatal refusal, for example an image path that is not
  allowed).
- Each candidate attempt is recorded as a `candidate` step row (section 6); a failed attempt fails its step.
- The worker classifies each failure code as retryable or not in one list in the domain, which becomes part of the API
  contract with the next REST revision (OD-25).
- (`0009` section 7) A deploy that fails after its XR was written deletes that XR first (fenced, best effort, with
  `resourceVersion` precondition, never when the release is the route target or the XR's `operationGeneration` is higher
  than the fence). This also applies when the consumer fails the command for an exhausted deadline or attempt budget:
  the executor gets an `Abandon` hook that the consumer calls before it fails such a command, and the executor ends its
  own wait early enough before the deadline to clean up.

## 4. Replaced candidate (amends `0001` section 2 and `0009` section 9)

New transition `candidate_ready → built`, allowed only to a running deploy command of the same project for a release
that is not its own and not `active_release_id`. A new deploy, before it writes its own XR, moves every other
`candidate_ready` release of the project back to `built` and deletes their XRs under its own fence (same guarded delete
as section 3). The replaced release keeps its image and can be deployed again. OD-47 (older unfinished releases at
go-live, who removes them) stays open.

## 5. Catalog credential (amends `0009` section 8)

`access_setup` creates the role `site_<project id hex>` and delivers its password to the Secret `catalog-runtime` only
when the role is absent, or when the Secret is missing and the project has no `active_release_id`. Otherwise it only
verifies (the role exists, is a member of `catalog_site`, can log in, reads only its own rows). A missing Secret while a
live release exists is the permanent failure `runtime_secret_missing`, for an operator. A deploy never rotates the
password. The password is hashed with SCRAM-SHA-256 in Go, so the cleartext never appears in a SQL statement or log. The
Secret is written before the role's password is set. The production adapter stays fail-closed (OD-46, OD-38); in Kind
the test-only adapter writes the Secret with the bootstrap identity.

## 6. Migration `00010` (revision 17 items B, C, E and candidate steps)

One forward-only migration `00010`, written by one database task, test-first on PostgreSQL 18, with upgrade tests from
version 9 with representative rows:

- **B.** `provisioning_ops.source_published_inbox` gains `attempt_budget` (INTEGER), `last_redriven_by` (VARCHAR(255)
  NULL), `last_redriven_at` (TIMESTAMPTZ NULL, set together with `last_redriven_by`). New `NOLOGIN` role
  `provisioning_operator`. The inbox update guard allows `failed → pending` only for members of `provisioning_operator`;
  that update clears `outcome_code` and `finished_at`, raises `attempt_budget`, sets both re-drive columns; `attempts`
  never decreases; `rejected` and `accepted` stay final. Existing rows get an `attempt_budget` equal to the configured
  budget in force (the task states the value it uses and why).
- **C.** New `NOLOGIN` role `provisioning_payment_intake` with no members. `commands_insert_shape` refuses
  `suspend_hosting` and `resume_hosting` unless the session is a member of it.
- **E.** The terminal-row guards also freeze `failure_code` and `failure_summary` (releases) and `failure_code`
  (commands); `promoted_at` becomes write-once (null to value); `CHECK (… <= 9007199254740991)` on
  `releases.source_repository_id`, `project_release_state.operation_generation` and `commands.fencing_generation`.
- **Candidate steps.** `provisioning_worker` gets `INSERT` and the `UPDATE` columns it needs on `release_steps`. The
  insert guard admits a `candidate` step for a `built` release, as attempt 1 or the next attempt after a failed one,
  bounded like build attempts; the update guard admits `pending → running` for a `candidate` step only while the release
  is `built`. `access_setup` is recorded inside the candidate step, not as its own row. Repeated deploys of one release
  are bounded by the same attempt maximum; a release that reaches it can only be failed.
- Revision 17 item D (hosting event for a never-deployed project) is a Phase 6 rule and is not in `00010`.

## 7. Image repository (amends `0002`)

`spec.image.repository` comes from an `ImageLocation` port. Its production adapter is unconfigured and fails closed
until OD-32 and OD-37 are decided. A Kind test adapter returns `<local registry>/webblock/sites/<projectId>`. The
composition function also requires the project ID as the last path segment of the repository (Kind test value), so an
image of another project's path is refused by the function as well.

## 8. No wiring in Phase 4

The production binaries are unchanged: `src/hosts/api` and `src/hosts/worker` link no `k8s.io` or `sigs.k8s.io` module
and reference none of the new adapters or the deploy executor; a contract test pins it, and "the API never links
client-go" stays permanently. The Kind acceptance suite builds its own test worker under `tests/acceptance` (build tag
`acceptance`). No command kind is enabled in a production build.

## 9. Exception: Crossplane core advisories

- **What:** Go advisories GO-2026-6225 and GO-2026-5709 in Crossplane core 2.4.2 (platform image, not Provisioning
  code), no upstream fix (dependency check L3).
- **Reason:** no fixed release exists; Crossplane 2.4.2 is the owner's pinned version (`0009` section 5); the advisories
  are in Crossplane's package-pull and runtime code, not reachable from Provisioning.
- **Reviewer:** the project owner.
- **Expiry:** 7 January 2027, or the first Crossplane release that fixes either advisory, whichever is first.
- **Follow-up:** recheck on every Crossplane release; before production, rerun the image scan (no scanner is installed
  today, so the runtime image gate itself is still not run).

## 10. Revision 17 of the platform database design

`.claude/PLATFORM_DB_DESIGN.md` is revision 17. For Provisioning, section 5 now documents migrations `00001` to `00008`
as merged, which matches the code (ARCHITECTURE.md section 9's tables stay as they are); its proposed migration
"`00009`" is `00010` here (section 6), because `00009` is the V3-03 step-guard hardening. `project.projects.slug` is
removed and `project.domains` holds a project's hostnames (Project-owned); this feeds OD-13 and OD-36 (Phase 5) and
changes nothing in Phase 4.

## 11. Lead rulings for the implementation briefs

- **Fence before every XR write.** A new store method `RenewFence` makes the three checks of `0004` section 7 (project
  fence, lease token, command `running`) plus "not finished", and extends the lease on the database clock, conditional
  on the token. It is called immediately before and after every XR write and before every XR delete. `ExtendLease` alone
  is not a fence.
- **Server-side apply** through `Patch` with `types.ApplyPatchType` and
  `PatchOptions{FieldManager: "webblock-provisioning-worker", Force: false, FieldValidation: "Strict"}` (the dynamic
  client's `Apply` drops `fieldValidation`). Fixed GVRs, no discovery, no disk cache. Gateway API objects are read as
  unstructured; no `sigs.k8s.io/gateway-api` module. client-go v0.36.5 only.
- **Client construction.** Production: in-cluster configuration only. Tests: a loader under `tests/acceptance` that
  reads only the project's Kind kubeconfig by explicit path, refuses anything but a `kind-webblock-*` context on
  `https://127.0.0.1:<port>` with mode 0600 under `bin/kind-state/`, never uses client-go's default loading rules,
  `$KUBECONFIG`, `~/.kube`, or proxy settings from the environment. The worker's identity is a TokenRequest token for
  `webblock-system/provisioning-worker`; the admin credential of the Kind kubeconfig is used only by the harness.
- **Missing tenant.** A missing namespace or RoleBinding (403 or 404 on the tenant check) is the transient
  `tenant_not_ready` until the command's budget or deadline. A terminating namespace or a wrong `project-id` label is
  the permanent `tenant_mismatch`. The worker never creates a `WebsiteTenant`.
- **Leftover XR of the same release.** Re-applied at the new fence and logged (`candidate_reapplied`); an XR being
  deleted is waited for; an XR whose `operationGeneration` is higher than the fence is the permanent
  `candidate_generation_ahead` and nothing is written.
- **Candidate resources.** Required configuration keys `DEPLOY_CPU_MILLICORES` (50 to 2000) and `DEPLOY_MEMORY_MIB` (64
  to 4096) with Kind test values; OD-33 stays open.
- **Waiting.** Polling with jittered backoff, not watches or informers, in Phase 4; the rest config sets QPS, burst and
  per-request timeouts explicitly. New required keys (no defaults) with cross-checks against `WORKER_ATTEMPT_TIMEOUT`,
  `WORKER_LEASE_TTL` and `DB_TX_TIMEOUT`; Kind test value of the readiness deadline 120 s. The exact key list is G0's.
- **Errors and logs.** API-server answers map to a closed set of typed errors and fixed failure codes (409 conflict →
  permanent `candidate_xr_conflict`, never `force`; 422 or admission denial → permanent refusal; 429, 5xx, timeouts →
  transient). Raw API-server or event text never reaches `commands.failure_code`, `failure_summary` or logs. Tokens,
  kubeconfig content, Secret data and the catalog password are never logged; tests assert this with canary strings.
- **Catalog connections.** `access_setup` uses its own short-lived connections (admin and site role) with their own
  timeouts, never the worker's pool; at most two at a time.
- **Coverage.** Code that only the `acceptance` suite runs does not count towards `cover-all`, so the adapters get unit
  tests against an `httptest` API server, and the catalog tests (PostgreSQL only) join `cover-all`.
- **The worker does not read Crossplane `Usage` objects**; the guarded delete reads the route XR and SQL. The test-only
  Secret written in Kind is `type: Opaque`, enforced by the test-only policy.
- **Kind and Testcontainers never run together**, and two Kind stacks never run together: the heavy Make targets check
  this before they start.
- Vulnerability database freshness limit for `make vuln`: 14 days. The function module's Kubernetes libraries stay on
  0.35.x and take patch releases with the next function change.
- First-publish route removal and the offline page stay Phase 5 and Phase 6 (OD-44).

## 12. Rulings on task G0's report

- **A replaced candidate can be deployed again (section 4 wins over section 6).** As section 6 was written, a new
  `candidate` step is admitted only as attempt 1 or after a failed attempt, but a release that a newer deploy returned
  to `built` has a succeeded last candidate step, so it could never be deployed again, contrary to the owner's answer in
  section 4. The candidate step rule is therefore: attempt 1, or the next attempt after a failed **or succeeded** one,
  only while the release is `built`, at most `MaxCandidateAttempts` in total. The same holds for the domain
  (`NewCandidateAttempt`) and for `00010`'s insert guard.
- **The narrowed `built → failed` of section 3 applies on every path,** not only the new candidate path: the existing
  worker outcome write (`FailWithoutRouteChange` with the release failed, `ApplyDeployOutcome`) accepts only the codes
  the classification marks "release failed". Task G2 tightens it and updates the two integration tests that use the
  unlisted code `candidate_failed`.
- **Accepted from G0:** ten `500 internal` rows in `src/presentation/ops` for the new typed errors (decision `0006`
  section 3 requires a row per error); `ImageLocation` and `RuntimeCredentialSink` counted as external ports, so their
  Kind adapters live under `tests/`, never in `src/`; the extra key `DEPLOY_ACCESS_SETUP_TIMEOUT`; the port
  `CredentialSource` (implemented by G3); the kubeconfig scan covering `tests/` too; `make test-acceptance` passing
  `WEBBLOCK_KIND_KUBECONFIG` and `WEBBLOCK_KIND_CLUSTER`, never `KUBECONFIG`.
- **No gate goes red at a merge.** The `catalog` suite joins `cover-all` in the same change that adds its tests (task
  G3), not before.
- **The `Abandon` hook and the consumer:** when the hook already ended the attempt and failed the command, the
  consumer's own failure write finds the command finished and changes nothing; that is the expected outcome, not an
  error (task G2).
- **A `built` release with no candidate attempt left is failed by the deploy that finds it** (G0 follow-up 1, concern
  1). Because a succeeded attempt is now followed by another (first ruling above), a release that was replaced after its
  last allowed attempt is `built` with `MaxCandidateAttempts` steps and no failed one, so `FailCandidateAttempt` does
  not apply. A deploy command for it, after loading the target under its fence and before any other side effect (no
  replaced-candidate return, no `access_setup`, no step row, no XR write), fails the command and moves the release
  `built → failed`, both with `candidate_attempts_exhausted` (classified "release failed"), in one fenced write. This is
  section 6's "a release that reaches it can only be failed"; the project needs a new source publication to deploy
  again. Task G2 implements and tests it; `00010` (task D1) refuses an eleventh `candidate` step and leaves the worker's
  existing `built → failed` path as it is.

### Rulings on task G2's report

- **The candidate step is opened before `access_setup`** (G2 concern 1). Section 6 records `access_setup` inside the
  candidate step, so the step row comes first; the order in task G2's brief was wrong. Consequence accepted: a failed
  `access_setup` uses one of the release's `MaxCandidateAttempts`.
- **An unfinished candidate step left by an earlier claim is failed with `deadline_exceeded`** when the next claim
  starts its attempt or writes its outcome (G2 concern 3). Only the command that holds the current fence can open an
  attempt, so any other `pending` or `running` candidate step of the release belongs to a claim whose lease or deadline
  has passed; `deadline_exceeded` is listed, permanent for that step, and leaves the release `built`. No new code is
  added; the step write never changes the release.
- **The per-attempt transaction count in the time-budget check becomes 8** (G2 concern 6): at most one other
  `candidate_ready` release exists per project, because every deploy returns the others to `built` under the per-project
  serialization, so one replaced candidate is the worst case. Task G2 changes `deployAttemptTransactions` and its tests
  in `src/infrastructure/config/deploy.go`.
- **The narrowed `built → failed` is enforced in the domain as well as the store** (G2 concern 5): task G2 tightens
  `FailWithoutRouteChange` (release failed) to codes classified "release failed".
- **Accepted as reported:** the image location is read before replaced candidates are returned (concern 2: a read only,
  so an unconfigured adapter fails before any write); generations, stages and effects go in the existing telemetry
  fields `reason`, `operation` and `error_code` (concern 7).
- **Known gap, not fixed in Phase 4** (concern 8): a candidate XR left by an earlier crashed claim is not deleted when
  the next claim fails before its own attempt starts (entitlement, tenant, image location); the next deploy re-applies
  it at its own fence, and the `Abandon` hook deletes it when the command's deadline or budget runs out. A sweeper for
  candidate XRs of releases that are not `candidate_ready` is Phase 5 work (with OD-47).

### Rulings on task G1's report

- **One acknowledgement function, in the application layer** (G1 concern 1). The deploy use case decides
  acknowledgement, and the application may not import infrastructure, so `application.AcknowledgesIntent` (task G2) is
  the only one; task G1 deletes `crossplane.Acknowledged` and its tests. Its endpoint rule is the stricter one: the
  release Service's ready endpoints are at least the Deployment's desired replicas (and at least one), matching section
  1's "replicas updated, ready and available, with ready EndpointSlices". A composition refusal is never an
  acknowledgement.
- **No events summary in Phase 4** (G1 concern 2). Section 1 does not use events, and section 11 keeps event text out of
  failure fields and logs; the observation stays as G0 declared it. Diagnostics from events are Phase 5 work if needed.
- **Accepted as reported** (G1 concern 4): an apply refused at the same fence because the stored XR carries other intent
  is `candidate_xr_conflict` (permanent, release stays `built`), not a refusal of the intent; the guarded delete also
  keeps a release that the route XR's status still serves, not only its spec target; a failed delete precondition is
  `candidate_xr_conflict` and is only logged; an XR being deleted is reported as transient (`cluster_unavailable` with
  `ErrCandidateDeleting`), which is section 11's "waited for" through the command's retry budget; 401 is transient.
- **Live confirmation is task G4's** (G1 concern 5): the split of 403 answers (authorization, admission policy, webhook)
  and the match on Crossplane 2.4.2's "returned a fatal result" text are proven on Kind by the acceptance suite. G4's
  test worker silences klog (concern 6). Since `tests/acceptance/kindguard` exists, `make test-acceptance` no longer
  stops at "no tests" and starts Kind (concern 3).

### Rulings on task G3's report

- **Finding for the owner and the catalog owner (OD-46):** `PLATFORM_DB_DESIGN.md` section 6 (revision 17) defines
  `catalog.current_website_id()` as `SECURITY DEFINER` matching `db_role_name = current_user`. Inside a
  `SECURITY DEFINER` function `current_user` is the function's owner, so every site role would see no rows; task G3
  proves it with a test. The test-only stand-in matches on `session_user` (the login role, which a site role cannot
  change) and says so in its header and runbook. The design document is not edited here; the owner decides the
  correction with the catalog owner.
- **Accepted:** `VerifySiteRole` proves the negative properties (no other website's rows, no access to
  `catalog.websites`, row-level security forced) and reads "can log in" from the role catalog; the positive "reads its
  own rows" is proven by `SetSiteRole`'s login check and by task G4's site pod on Kind. The adapter grants each new site
  role to its administrator with `SET TRUE, INHERIT FALSE` so verification can act as it; this adds nothing the
  administrator could not already do (reset the password).
- **The section 5 create-or-verify decision lives in the deploy use case** (task G2); the adapter only executes it, and
  the catalog suite's copy is test-only.
- The test-only Secret `catalog-runtime` carries `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE`
  (OD-38 stays open). The catalog suite needs a superuser `PG_USER` and `scram-sha-256` TCP authentication; it creates
  the cluster-wide `catalog_site` group when absent and drops it afterwards.
- *(G2 follow-up 1)* **Role absent with a live release:** section 5's "the role is absent" is its own reason to create,
  so `access_setup` creates the role and delivers a new credential even when the Secret is missing and an active release
  exists; that release cannot connect without the role anyway. `runtime_secret_missing` applies only when the role
  exists, the Secret is missing and an active release exists. `FailWithoutRouteChange` narrows only a deploy's
  `built → failed`; a promote's `candidate_ready → failed` codes come with Phase 5.

### Rulings on task D1's report

- **Existing integration tests adapted** (D1 concern 1): accepted. `00010` changes rules those tests pinned (hosting
  commands only from `provisioning_payment_intake`, `2^53−1` bounds, frozen failure fields), so their fixtures and
  pinned lists had to follow. Hosting commands are seeded on administrator sessions only, never in an assertion; the two
  store tests of counters above `2^53−1` are removed because the database now refuses such rows.
- **Step names belong to roles** (D1 concern 3; tightens section 6 before `00010` is merged): the step guards also check
  membership. A `candidate` step is inserted and moved only by a member of `provisioning_worker`; a `build` step only by
  a member of `provisioning_build`, as before; neither role can insert or move the other's steps. This keeps a
  compromised build or intake login from spending a release's candidate attempts.
- **The re-drive budget reaches the processor** (D1 concern 2). The row's `attempt_budget` replaces the configured
  maximum in the inbox processor's retry check; a new row is inserted with `attempt_budget` equal to the configured
  maximum. The retry deadline is counted from `last_redriven_at` when it is set, otherwise from `received_at`. Existing
  rows get `attempt_budget = GREATEST(attempts, 1)` (no budget was ever configured in a running binary), so they take no
  further attempt until an operator re-drives them.
- **Recorded as implemented** (D1 concern 4): `provisioning_operator` holds `SELECT` on the inbox and `UPDATE` only of
  `status`, `outcome_code`, `finished_at`, `attempt_budget`, `next_attempt_at`, `last_redriven_by`, `last_redriven_at`,
  `updated_at`; `last_redriven_by` and `last_redriven_at` are set together and `last_redriven_by` is not empty; every
  re-drive records a new `last_redriven_at`. A worker's `pending → failed` of a `candidate` step sets
  `started_at = finished_at` (the existing check `release_steps_finish_after_start`).
- *(D1 follow-up 1)* **"As before" for build steps** includes the intake's first build attempt (`0008` section 3): a
  `build` step is inserted by `provisioning_build` or, as attempt 1 at release acceptance, by `provisioning_intake`;
  never by `provisioning_worker`. A `candidate` step only by `provisioning_worker`; the superuser stays exempt as in
  `releases_build_transition`, the table owner does not. The configured inbox budget now has two settings
  (`intakestore.Config.AttemptBudget` for new rows, `InboxProcessorConfig.MaxAttempts` still validated but no longer
  read by the retry check); the future wiring sets both from one key.
- **Section 7's project-ID path rule was never implemented in the composition function**: no task brief assigned it (the
  lead's omission). Task F1 adds it (fatal refusal when the last path segment of `spec.image.repository` is not
  `spec.projectId`), with the fixtures and Kind proof inputs it changes; task G4's next run from nothing proves it live,
  and G4's test for it runs unconditionally once F1 is merged.

### Live findings of task G4

- **Content access goes through the Gateway, not a pod in the tenant namespace** (amends the method of section 2, not
  its rule). The tenant network policy admits ingress only from the Gateway, so a harness pod inside the namespace
  cannot reach the release Service; the policy is not widened for a test. The suite reaches the candidate through the
  Gateway with a route the harness writes with the admin identity on a test-only hostname. The worker still makes no
  HTTP probe, PR-08 "content access" is still proven by the suite only, and the scenario's "route untouched" assertion
  is made on the worker's writes (the worker never writes a route XR in Phase 4). G4's report states exactly which route
  object it writes and when.
- **A server-side apply with an unknown spec field returns HTTP 500 on Kind (Crossplane 2.4.2), not 422,** and is
  classified transient. The worker never sends such a field (the body is built only from the intent, `0011` section 11),
  so the effect is a bounded retry ending `attempts_exhausted` or `deadline_exceeded`, never a false success. The
  validator checks this; a 500 is not reclassified on its text.
- **Crossplane restores a scaled-down function Deployment**, so the missing-function scenario cuts ingress to the
  function pod with a test-only CiliumNetworkPolicy, removed afterwards.
- **A terminating tenant namespace is seen as `tenant_not_ready`, not `tenant_mismatch`** (G4, live): the worker's
  RoleBinding is removed with the namespace's content, so the worker's tenant check gets 403 and the failure is
  transient until the command's budget or deadline, which bounds it; no candidate is written. Section 11's
  `tenant_mismatch` for a terminating namespace applies only while the worker can still read it. A tenant namespace with
  a wrong `project-id` label cannot be created on Kind (admission refuses it even for the admin), so `tenant_mismatch`
  is proven by the adapter's unit tests only.
- **`candidate_reapplied` is logged for a leftover XR of an older command**; a restart of the same command re-applies at
  its own fence without that log (G4, live). The test-only readiness deadline is 30 s in the failure scenarios and 120 s
  elsewhere, both through `config.LoadDeploy`. `k8s.io/klog/v2` becomes a direct requirement because the test worker
  silences klog.
- **The guarded delete retries a lost precondition, with the guards each time** (G4 final run 1; amends section 3's
  "best effort" only in this). `TestAbandon/attempt_budget_exhausted` failed: the Abandon hook deleted the XR about 3 s
  after it was written, while Crossplane was still writing it (`0009` section 13: generation 1 → 4 and status writes
  right after a create), so the delete's `resourceVersion` precondition failed (409) and the XR was left behind as a
  running candidate. Ruling: when the delete answers 409, `DeleteCandidate` starts again from its first step (read the
  XR, check project, release, fence and deletion; read the route; delete with the new UID and `resourceVersion`), at
  most 5 passes in all, waiting 250 ms × the pass number between passes and never past the caller's context. Every pass
  applies every guard; a pass that finds the XR absent, ahead of the fence, being deleted or targeted by the route
  returns that outcome. Only after the fifth 409 is the result `ErrCandidateConflict`. A spec that names another project
  or release is still `ErrCandidateConflict` at once, never retried. Implemented by task F2.
