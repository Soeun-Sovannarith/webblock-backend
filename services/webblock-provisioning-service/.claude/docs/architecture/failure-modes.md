# What can fail: code generation and deployment

Each row says how Provisioning reacts today:
- **Handled**: detected, safe, and tested.
- **Partly**: detected or safe, but not proven end to end, or the outcome is weak.
- **Gap**: not handled. Wave 6 (`0014` section 21) or later.
- **Other**: another service's responsibility; Provisioning can only refuse safely.

The rule behind every row: **a failure must never take down the live site, and must never leave a project stuck.** A failed build or deploy leaves the live release serving; a failed promotion restores the route; anything unclear becomes `uncertain`, which an operator can recover.

## A. Code generation side (AI Codegen generates, Project publishes the commit)

Provisioning cannot see the generation. It sees the published commit, builds it, runs it and probes it. The contract a site must meet is in `README.md` ("Contract for generated sites").

### A1. The publication event

| What fails | What Provisioning sees | Today |
| --- | --- | --- |
| The event is never published, or published twice | Nothing, or a duplicate | Duplicate: **Handled** (one release per generation request). Never published: **Other** (Project) |
| The event names a commit not yet pushed, or force-pushed away | Jenkins cannot check out the commit | **Handled**: the attempt fails `commit_not_found`; a retry is allowed |
| The event repeats with another repository or commit | A mismatched repeat | **Partly**: counted and refused, store-tested only |
| The repository is not the project's | Repository confirmation fails | **Partly**: a stand-in (OD-01); not tested e2e |

### A2. The build (Jenkins builds the repository's Dockerfile)

| What fails | Today |
| --- | --- |
| No `Dockerfile` at the repository root | **Handled**: the pipeline fails the attempt with a clear reason |
| The build fails: dependency install, type errors, a missing file | **Partly** (QA-10): the attempt fails `build_failed`, but the next attempt is never started, so the release stays `building` until someone calls `build_release`; fixed in wave 6 |
| The build needs the network and a registry or mirror is down; Docker Hub rate-limits base-image pulls | **Partly**: the attempt fails; a retry may pass. Rate limits are accepted for now (OD-61) |
| **The site reads the catalog at build time** (Next.js static prerendering fetches data during `next build`) | **Gap in the contract**: builds have no database and must not get one. The site must render data pages at request time (the demo shop uses `force-dynamic`). Add it to the README contract |
| The build is too slow | **Handled**: the attempt deadline and the sweeper fail it |
| The image is huge, or built for the wrong CPU architecture | Huge: **Partly** (slow pull, may pass the deadline). Architecture: **Handled** (the pipeline builds `linux/amd64`) |
| The build bakes in a secret (an `.env` file, a key) | **Other, and serious**: everyone who can pull the image reads the secret. Site images are private (TR-52), but nothing scans for secrets yet (production hardening) |

### A3. The running site (the runtime contract)

| What fails | What happens | Today |
| --- | --- | --- |
| Listens on the wrong port, or on `localhost` only | Readiness never succeeds; the deploy fails `progress_deadline_exceeded` | **Handled** (live site untouched) |
| Needs root, or writes outside `/tmp` (read-only root) | Crash loop | **Handled**: `crash_loop` |
| Uses more memory than its limit | OOM-killed, crash loop | **Handled** at deploy; **Gap** if it happens only under real traffic after going live |
| Starts slower than the progress deadline | The deploy fails | **Handled**; the deadline is configuration |
| No `/readyz` (404) | The promotion is refused at the preview | **Handled** |
| **`/readyz` always answers 200** even when the catalog is broken | A broken release is promoted | **Gap in the contract**: the probe can only trust the site. Mitigated by the README rule and the demo's reference code |
| `/readyz` is slow or flaky | The promotion fails or is retried | **Gap** (QA-08): a 50 % flapping `/readyz` was promoted; huge bodies exhaust the worker's memory (QA-01) |
| Catalog queries return nothing (RLS, wrong role) | Empty pages served with 200 | **Partly**: `/readyz` must run a real catalog query; an empty catalog is still "ready" |
| The site needs more database connections than its pool | Errors under load | **Gap** (load is not tested; PgBouncer comes in production) |
| Hard-coded absolute URLs or hostnames | Broken links on purchased domains, or redirect loops | **Other** (contract: never depend on a hostname) |
| Works at first, fails minutes or days after going live (memory leak, expired dependency, data-dependent crash) | Kubernetes restarts it; nobody is told | **Gap**: Provisioning probes only during a promotion. Alerts and the DevOps agent (`0015`) |

## B. Deployment side (Provisioning)

### B1. Intake and messaging

| What fails | Today |
| --- | --- |
| RabbitMQ down, or a message lost or redelivered | **Handled**: SQL is the authority; the dispatcher republishes from `command_dispatch`; duplicates are processed once |
| PostgreSQL down | **Partly**: nothing is accepted and loops retry; recovery after the database returns is tested only at store level |
| Project's repository confirmation keeps failing | **Partly**: the event ends `failed` after its budget; **Gap**: no operator re-drive yet (wave 6) |
| A Payment or domain event arrives while the project is busy, then runs out of retries | **Gap**: it is parked in the dead-letter queue and never re-injected, so a suspend could be lost |
| An event for a project Provisioning does not know | **Partly**: acknowledged and logged (OD-52) |
| The worker restarts and loses its in-memory entitlement record | **Handled** (`0014` section 17): an unknown record never skips a suspend |

### B2. Builds

| What fails | Today |
| --- | --- |
| Jenkins down or unreachable | **Handled**: the attempt fails within its budget; live sites unaffected |
| Jenkins restarts and renumbers a build | **Handled**: the build is found again by its parameters |
| The result does not match (project, release, commit, digest) | **Handled**: the attempt fails, the result is refused |
| Every attempt fails | **Gap** (QA-10): attempts after the first are never started automatically |
| A next attempt is created but never started | **Gap** (OD-51: no start deadline for `pending` steps) |
| Two build requests at once | **Partly**: the second gets `409 build_in_progress`; checked by hand only |

### B3. Tenant setup (bootstrap)

| What fails | Today |
| --- | --- |
| The namespace is terminating or not ready | **Handled**: `TenantNotReady` makes the deploy retry later |
| Creating the catalog login fails | **Partly**: the Secret is written before the password; `runtime_secret_missing` has no operator action |
| The bootstrap identity is compromised | **Partly**: `webblock-bootstrap-scope` limits it; it can still create a namespace for an unknown project (recheck N2) |

### B4. Deploy (candidate)

| What fails | Today |
| --- | --- |
| The image cannot be pulled (registry down, rate limit, wrong digest) | **Handled**: `image_pull_failed` ends the command at once; the release stays `built` |
| Pods cannot be scheduled (not enough CPU or memory) | **Partly**: ends `progress_deadline_exceeded`, which is not specific (`insufficient_resources` is proposed, `0015` section 4) |
| The ResourceQuota refuses new pods | **Partly**: also a timeout today (`quota_exceeded` proposed). **Gap**: old releases are never cleaned up, so the quota fills over time (wave 6) |
| The worker crashes mid-deploy | **Handled**: another claim resumes after the lease expires; fencing refuses the stale worker |
| Another writer owns a field (apply conflict) or a newer command owns the object | **Handled**: `candidate_apply_conflict`, `candidate_fence_ahead`, never forced |
| The Kubernetes API is down | **Handled**: retried within the command's deadline |
| Someone deletes or edits a running Deployment or the route by hand | **Gap** (QA-07): an edited route leaves the project `uncertain` and even rollback is refused; a deleted namespace is never rebuilt (QA-04); reconciler in wave 6 |

### B5. Promote, restore and rollback

| What fails | Today |
| --- | --- |
| The candidate fails the preview probe | **Handled**: a failed try (`candidate_not_answering`); the public route never moves; after 3 tries the candidate ends `rolled_back` (decision `0027`) |
| The public route switch is not accepted, or the probe after the switch fails | **Handled**: the route is restored after each try; after try 3 the candidate ends `rolled_back` (the e2e case arrives with task T) |
| The old backend answers for a moment after the switch | **Handled**: the probe requires `X-Webblock-Release` to name the candidate |
| The restore itself fails | **Partly**: the route becomes `uncertain`, new commands are refused, and an operator rollback recovers it; not shown end to end |
| **The command passes its deadline while the route is `changing`** | **Gap (defect)**: the project stays stuck; even rollback is refused. Wave 6's recovery sweeper fixes it |
| A purchased domain's certificate is not ready | **Handled with task T**: the promotion is refused with `domain_not_ready` |
| The first publish fails | **Partly**: the route points at the offline page; unit-tested only |

### B6. Hosting and domains

| What fails | Today |
| --- | --- |
| Suspend while a deploy is in flight | **Handled**: busy, retried |
| Resume for a site already online, or suspend for one already offline | **Partly**: ends `skipped`; not live |
| Certificate issuance fails (ACME rate limits, DNS-01 not visible) | **Partly**: `sync_domains` fails `domain_not_ready` and the old hostnames keep serving; the real ACME path comes in wave 6 |
| Two projects claim the same hostname | **Other**: hostname uniqueness is Project's (`project.domains`); the Gateway would show a listener conflict |
| A domain expires | **Partly**: removed from the route; its old listener and certificate stay (task T limit) |
| A project is deleted | **Gap**: the site goes offline in wave 6 |

### B7. Platform and infrastructure

| What fails | Today |
| --- | --- |
| The Gateway or its load balancer is down | All sites are down; promotions fail safely. **Other** (infrastructure); alerts later |
| A node fails | Kubernetes reschedules pods; the image must still be pullable (the Gitea registry and the site's pull Secret, TR-46 and TR-52) |
| The clocks of hosts disagree | **Partly**: deadlines use the application's and the database's clocks; not tested |
| A cluster or Cilium upgrade changes route behaviour | **Partly**: the release-header and route tests catch it on Kind; DOKS facts open (OD-67) |
| Two Provisioning replicas | **Partly**: leases and fencing are designed for it; never run (production hardening) |
