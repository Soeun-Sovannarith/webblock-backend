# 0016: Production environment

**Site images: sections 2 and 5 item 1 no longer apply.** The owner moved site images to the private Gitea
container registry, `<registry>/webblock/sites/<projectId>`, pulled with a pull Secret per site (gate 1 answer O3;
TRD TR-46 and TR-52). The record for it is reserved as `0024`.

Status: **Current; decided by the owner**, except the site image registry (the note above). The owner answered the
lead's questions while the target architecture was rewritten (the retired ARCHITECTURE.md section 13). Section 6
lists what the service implements; the clusters and data services of sections 1 and 3 are not set up from this
repository.

## 1. Clusters

- **Production runs on DigitalOcean DOKS** (as in `0009` section 1).
- **The owner provides a GKE cluster for testing.** It is not a production target. Until the owner names it in
  `.claude/CLAUDE.md` section 0, the real-cluster rule forbids touching it, as for DOKS (OD-66).

## 2. Builds and images

- **Jenkins builds** on the owner's dedicated server (`0014` section 2).
- **Images go to Docker Hub**, **one public repository per site**: `docker.io/<org>/site-<projectId>` (closes OD-59).
  *Changed by the owner: public, not private (section 5).*
- ~~Tenant pods pull with an image-pull Secret in each tenant namespace~~ *Superseded (section 5): the repositories are
  public, so no pull Secret is needed (OD-60).*

Consequences, to implement before production; each needs its own task:
- the image rule `<registry>/webblock/sites/<projectId>` becomes `docker.io/<org>/site-<projectId>`, in the renderer,
  the image admission policy, the Jenkins pipeline's `IMAGE_REPOSITORY` check and the configuration
  (`BUILD_IMAGE_REGISTRY`, `DEPLOY_IMAGE_REGISTRY`);
- who creates each Docker Hub repository (Jenkins' push token, or a provisioning step), the plan's private-repository
  limit, pull rate limits, and retention of the live image stay open (OD-61, with OD-48).

## 3. Data services

- **PostgreSQL and RabbitMQ run in the cluster.**
- **CloudNativePG**: separate clusters for the Provisioning database and the catalog database, backups and point-in-time
  recovery to DigitalOcean Spaces, and a PgBouncer pooler for the catalog.
- **The RabbitMQ Cluster Operator**: three nodes and quorum queues on persistent volumes, with the Messaging Topology
  Operator for users and permissions.

Still open (OD-63): the recovery objectives (RPO and RTO), who operates the clusters day to day, TLS and getting the
database CA to tenant namespaces, and the database-restore runbook. That runbook must cover per-site objects stamped
with command numbers that SQL has not issued again after a restore.

## 4. What stays as before

Everything in `0001` to `0015` not changed here. The prototype on Kind keeps its local registry, local Jenkins and
Docker containers (the retired ARCHITECTURE.md section 16).

## 5. Further answers

1. **Public repositories.** Each site's Docker Hub repository is **public**, one per site
   (`docker.io/<org>/site-<projectId>`). The owner's team creates them. Consequences:
   - no image-pull Secret, so `webblock-bootstrap-scope`, the bootstrap RBAC and the renderer stay as they are for
     images;
   - anyone can download any site's image, so an image must never contain a secret or customer content; runtime values
     arrive only through Secrets such as `catalog-runtime` (already the rule);
   - anonymous pulls are rate-limited by Docker Hub per address, so the plan for pulls under load stays open (OD-61).
2. **Only `build_worker` calls Jenkins.** The build endpoint records that a build attempt is wanted. `build_worker`
   alone holds the Jenkins token and the `provisioning_build` login, and starts and polls the build. The API process
   holds neither. This narrows `0014` section 2 items 4 to 5 and restores `0008` section 1 ("build paths never run in
   the API"). No schema change; the use-case split changes in phase P-H.
3. **Phases.** P-H (hardening) first, then P-C (contracts) and P-P (platform) in parallel, then P-R (rollout).
4. **Observability.** `prometheus-client` (`/metrics`) and the Sentry SDK come in during P-H, with redaction tests.
   OpenTelemetry waits until a trace backend exists (OD-65).
5. **The registry can be replaced later** (owner's question), for example by a self-hosted registry when the budget
   allows. Provisioning stores only the bare digest, and the registry prefix is configuration (`BUILD_IMAGE_REGISTRY`,
   `DEPLOY_IMAGE_REGISTRY`, the renderer's and admission policy's repository prefix, the Jenkins pipeline's
   `IMAGE_REPOSITORY` check). A move needs: the new prefix in that configuration in one change; the images of live
   releases copied with their digests unchanged (for example `crane copy`), or the sites rebuilt; a pull credential
   again if the new registry is private (the per-tenant Secret design of section 2's history); and a cut-over while no
   command is in flight.


## 6. Implemented in phase P-H

1. **The build/API split (section 5 item 2, task H2).** `build_release` records nothing new: the release's pending build
   step, written by the intake or by a failed attempt, is the durable record that an attempt is wanted. The API checks
   it with the read grant `provisioning_api` already holds, refuses as before (`release_not_buildable`,
   `build_in_progress`, not found), and sends a PostgreSQL notification
   `pg_notify('provisioning_build_wanted', '<project_id>/<release_id>')`, which needs no grant. Only `build_worker`
   holds the Jenkins token and the `provisioning_build` login. It listens on that channel on a connection of its own,
   validates each payload as two canonical UUIDs (anything else is dropped and logged without its content), queues at
   most `BUILD_HINT_QUEUE_SIZE` hints, and starts each through `StartBuild`. A hint lost while the listener is down, or
   dropped, only waits for the grace scan (`BUILD_PENDING_GRACE`), so SQL stays the authority. The API refuses
   `PG_BUILD_*`, `BUILD_*`, `JENKINS_*` and the repository-file keys when set. Contract change, within `0014` section 3:
   the 202 body is the release as read, so its build step may still be `pending`; two requests for one pending attempt
   are both accepted, and the attempt starts once. No schema change. A hint is untrusted: any database login can send
   one, and it can at most start early an attempt the grace scan would start anyway.
2. **P-P item: connection pooling.** `LISTEN` needs a session of its own and does not work through PgBouncer in
   transaction-pooling mode. In production the build worker reaches the Provisioning database directly or through a
   session-mode pool. The API's `NOTIFY` runs in an ordinary transaction and works through either mode.
3. **The production profile (task H2).** Every process requires `PROVISIONING_PROFILE`, `prototype` or `production`,
   with no default. In production every key that selects or feeds a prototype stand-in is refused by name, and
   `hosts/shared/stand_ins.py`, the only module allowed to import `infrastructure.standins` (import-linter contract
   `standins-only-through-hosts-stand-ins`), refuses to construct any stand-in. While OD-01, OD-04 and OD-05 are open:
   - the API refuses to start until JWT identity exists (task H3), and the one place to add it is `IDENTITY_MODES`;
   - once it starts, the API refuses every mutation with `OpenContract` (503 `operation_unavailable`, audited
     `open_contract`, naming OD-01 and, for deploy and promote, OD-04); project access denies every request, and
     entitlement, the hosting entitlement, and the repository registry and locator raise `ContractNotDecided`;
   - the worker enables no command kind (a command that appears anyway fails `operation_unavailable`, never dropped) and
     builds none of the prototype's event consumers, retry or usage publisher;
   - the build worker runs its poller only and starts no build;
   - the bootstrap process refuses production (OD-46); the operator accepts it.

   This restores `0004` section 2 for production builds. It is the intended fail-closed state, not a usable production
   deployment: that needs P-C.
4. **Found in H2's live run, in wave 6 code (`0014` section 24 path); closed by task BT.** A rebuilt tenant namespace
   could be missed as new when the probe saw the old, terminating namespace, so its catalog Secret was never written
   again. H2 made `FreshTenants` also mark the tenant when `ensure_tenant` reports `terminating`. Task BT (owner
   approved, section 7 item 2) closes the remaining window, a deletion that finishes between the probe and ensure's read
   without any round seeing `terminating`:
   - `TenantBootstrap.ensure_tenant` takes a keyword-only `on_created: Callable[[UUID], None] | None`. The bootstrap
     adapter calls it synchronously, once, right after its namespace apply succeeds on a call whose own read found no
     namespace, and before any later step, so a later step that fails, or the loop's call timeout, cannot lose the
     report. The callback is never awaited and must not raise; what it raises is not swallowed.
   - `FreshTenants` marks the tenant on it, and the mark lasts until access_setup succeeds.
   - "Created" means the namespace was absent when ensure looked. If a rival writer (only the bootstrap identity or an
     administrator may write a tenant namespace, policy `webblock-tenant-writer`) created it in the gap, the report is
     still made, which is safe because that is the condition of section 24.
   - The return type and states are unchanged. No schema, contract, RBAC or admission-policy change, and no create call.

   Still open:
   - a cancellation during the namespace apply's own request (the namespace may exist, unreported);
   - a bootstrap restart between the creation and access_setup: marks are per-process memory and the probe checks only
     the namespace, so `0011` section 5 applies (`runtime_secret_missing`, for an operator), as section 24 rules.

   **Amendment.**
   - The first "Still open" bullet is closed: a cancellation during the namespace apply now leaves the namespace with
     `pending`, because the created namespace carries the mark in that same apply.
   - The second bullet is closed too, by the durable mark (F-2, task F2B, validated on Kind): the mark is the namespace
     annotation, kept across restarts and guarded by admission, so a bootstrap killed between the rebuild and
     `access_setup` recovers without an operator
     (`test_a_bootstrap_killed_between_the_rebuild_and_access_setup_recovers`).
   - `FreshTenants` is removed. BT's `on_created` now has no caller; the port is lead-owned and is not edited here.
     Follow-up: the lead decides whether to remove `on_created` from `TenantBootstrap.ensure_tenant`.

   The created path is proven by unit tests. The live fault test proves the rebuild still works (2 of 2 in each of 3
   runs, 0 `RuntimeSecretMissing`), but whether a run hits this exact window depends on timing.
5. **Replicas proven on Kind (task H5b; lead).** Two API, two worker and two build worker processes against one database
   and broker (`make proto-run-replicas`, `make proto-e2e-replicas`, 8 tests):
   - per-project serialization and idempotency hold across two APIs;
   - with every job delivered six extra times at once, each command was claimed and executed once;
   - a worker frozen past its lease is taken over, and every write it attempts on resuming is refused by the claim or
     the fence; the command ends once, and objects keep one field manager and a fence that never goes down;
   - both build workers listen for hints, and each attempt is built once;
   - two reconcilers restore a route without a fight.

   Every engine sets `idle_in_transaction_session_timeout` to `DB_TX_TIMEOUT`, so a worker paused inside a transaction
   frees its row locks (the Go stores did the same). A refused claim release is now logged.

   Not tested: more than two replicas, network partitions, broker or database failover with replicas, rolling updates,
   replicas on separate hosts or behind a load balancer, a stale worker frozen inside a cluster write (the
   read-then-apply window stays open, closed by routing `changing` and recovery), and the Payment consumers, usage
   publisher and inbox under two replicas beyond what the ordinary suite exercised. Five ordinary e2e tests assume one
   worker (they stop `worker` or read `worker.log` only) and fail on the replica stack while passing on the default
   stack: a harness limitation, not a service defect.

   **The bootstrap runs as exactly one replica** (`replicas: 1`, strategy `Recreate`; a P-P item) until access_setup is
   serialized per project. Two bootstraps can interleave: one writes Secret A, the other writes Secret B and sets the
   role's password to B, then the first sets it to A, leaving Secret B beside password A. **Enforced since task H5c:**
   the bootstrap takes a session-level PostgreSQL advisory lock on a fixed key, on a connection of its own held for the
   process's lifetime, before its first round. A second bootstrap stands by (no rounds; it logs "bootstrap standby:
   another bootstrap holds the lock" at start and every 30 s, and reports `provisioning_bootstrap_standby` 1), and takes
   over within about a second of the holder's session ending. A bootstrap that loses its lock stops its rounds at once
   and exits 1. **A stall is not a loss (task HF-A; H-V finding F-1).** A check that has no answer within the statement
   timeout while its connection has not failed leaves the lock uncertain: the rounds are cancelled at once and none
   starts, and the same check is awaited on the same session, never cancelled (psycopg closes a connection whose
   cancelled query does not end within 5 s, which would end the session and free the lock). When it answers held and one
   more check on the same session answers held, the rounds resume; nobody else can have held the lock, since the session
   existed throughout. The lock is lost, and the process exits 1, when the connection fails, the session ends, a check
   answers "not held", or no answer comes within 60 s of the stall (the longer of `LOCK_STALL_BOUND` and the statement
   timeout; a liveness bound only, since no round runs while uncertain). The prototype's run script supervises the
   bootstrap as Kubernetes would: a non-zero exit is restarted after a back-off of 1 s doubling to 30 s, `stop-process`
   stops it for good and `kill-process` kills it with its supervisor. Proven: the full `make proto-e2e`, 55 of 55 in one
   run, with the 20 s database pause logged as uncertain and resumed; a live 20 s `docker pause` of the database with no
   exit; a live SIGKILL restarted in about 2 s. A restart still forgets the "created from nothing" marks (item 4, still
   open, now rarer; HF-A's proposal of a `pending`/`delivered` namespace annotation is for the owner's review with
   `0014` section 24). A session lock does not work through PgBouncer in transaction mode, so the bootstrap uses a
   direct or session-mode connection (a P-P item, like the build hint's LISTEN). The five single-worker e2e tests now
   act on and read every worker replica, and integration tests race claims and renewals on one command.

## 7. The owner's rulings

1. **"Port done" after H-V.** The Go removal (`0014` section 18, branch `worktree-agent-a39082a9f9e986d8d`) is rebased
   and merged right after the independent validation of phase P-H approves. Until then the Go reference's contract
   checks stay red: 37 tests have failed since the prototype waves added `build_release`, the list tools and pagination
   to the shared contract files, and the Go code is not extended (CLAUDE.md section 1). Tag `go-reference-phase-4` keeps
   the Go code.
2. **`ensure_tenant` reports a namespace it created.** The port change of section 6 item 4 is approved (task BT), to
   close the window H2 left open.
3. **Image scan and age waivers, on or after 19 October 2026:**
   - bump the sample site's base image from nginx-unprivileged 1.31.5 to 1.31.6
     (`sha256:b9241c6e7b8e9a862f129d8d4199ab64b10390949a78bdd5603379b32c844083`) once it is 14 days old;
   - propose exceptions for the three remaining High findings (tiff ×2, zlib), expiring within 30 days, for the owner's
     sign-off;
   - drop npm from the clothing-shop sample's runtime image;
   - remove the 14 prototype waivers from `scripts/py/age-waivers.toml`, and confirm `make py-age` passes.
4. **The retry flags of the failure-code catalogue are approved as set** (`0015` section 4 step 1), including the
   judgment calls `image_pull_failed`, `pipeline_failed` and `progress_deadline_exceeded` (retryable), and
   `quota_exceeded` and `executor_failed` (not retryable).

## 8. The owner's rulings

Asked by the lead after "port done": is the service ready for the GKE test cluster, or for a local GCP emulator (Floci)?

**The lead's assessment, accepted by the owner.**
- The service is not yet deployable to any cluster: it has no container image and no manifests (`proto-run` runs the
  four processes on the host; only the sites run in Kind), and the production profile refuses every mutation until P-C
  (OD-01, OD-04, OD-05).
- Floci stays unused (`0009` section 1): its GKE mode is k3s, so a pass would prove neither GKE nor DOKS, and the Kind
  stack already runs Cilium's Gateway API, cert-manager and the admission policies.

**Rulings.**
1. **P-P, local slice first (task PK).** Build the service's runtime image and run all of its processes inside the Kind
   stack: the API, the worker, the build worker and the bootstrap, each with its own ServiceAccount and identity as
   section 6 and ARCHITECTURE section 2 require, plus a migration Job running as `provisioning_migrator`. Exit: the full
   `make proto-e2e` passes in one run against the in-cluster service, and an independent validation approves.
   Provisioning's PostgreSQL and RabbitMQ stay outside the chart and are reached through configured endpoints; no
   CloudNativePG or RabbitMQ operator rehearsal for now (OD-63 unchanged). No GKE and no DOKS (CLAUDE.md section 0).
2. **Packaging is a Helm chart** (part of OD-20; the CI engine stays open). One chart, values per environment (Kind now;
   GKE and DOKS later).
3. **The GKE test runs the prototype profile** (the stand-ins of `.claude/docs/architecture/prototype-stand-ins.md`),
   with Jenkins and the per-site Docker Hub repositories reachable from GKE. It starts only after task PK is validated,
   the owner names the cluster in CLAUDE.md section 0, and the owner reports the facts of ARCHITECTURE section 13.2
   (OD-66).
4. **F-2 is fixed as HF-A proposed (task F2B), amending `0014` section 24.** In `ensure_tenant`, before the RoleBinding,
   the bootstrap stamps the namespace `websites.webblock.internal/catalog-secret: pending`; `access_setup` sets it to
   `delivered` before it writes the Secret. After a restart, "namespace present, Secret absent, annotation `pending`"
   counts as a namespace the bootstrap created from nothing. A crash between `delivered` and the Secret leaves the
   operator case of `0011` section 5, the safe direction. `webblock-bootstrap-scope` and `webblock-tenant-writer` allow
   the annotation to the bootstrap only and protect it from everyone else. No schema change.
5. **The wire contract moves to the revision-19 names now (task RN)**, without waiting for the consumer review of OD-25:
   REST (`src/contracts/openapi.yaml`) and MCP (`src/contracts/mcp/tools.json`) field names and the value `online`
   follow migration `00011` (for example `fencing_generation` → `command_number`, `operation_generation` →
   `last_command_number`, `route_state` → `routing_status`, `hosting_state` → `hosting_status` with `serving` →
   `online`, `active_release_id` → `live_release_id`), and the transport's mapping layer goes. Event schemas that belong
   to Project or Payment are not renamed (CLAUDE.md section 6: no unilateral change of another service's contract). This
   closes `0013` section 9 A1's deferral; OD-25 stays open for the shapes.
6. **Cleanup done.** The 32 agent worktrees and their branches are removed; the unmerged QA bug-hunt branch is kept as
   tag `qa-bug-hunt`. The stray wait loop (PID 564063) had already exited.

## 9. The owner's rulings

Asked by the lead with the design review of task PK (`.superpowers/sdd/python/research/PK-design.md` section 8.2,
git-ignored). The owner accepted all five recommendations.

1. **Exit scope of task PK.** The exit of section 8 ruling 1 is the full `make proto-e2e` passing in one run with the
   API, the worker, the build worker and the bootstrap running as pods in the Kind stack. On PK's final tip,
   `make proto-demo` also passes once against the in-cluster service, and the host-mode `make proto-e2e` (the four
   processes on the host, as today) passes once as a regression. The replica suite (`make proto-e2e-replicas`) stays
   host-only; porting it to the cluster needs per-pod API access and pod-name worker IDs and is a later task.
2. **In-cluster identity.** The worker and the bootstrap connect from a pod only through an explicitly mounted,
   projected ServiceAccount token that expires after 600 seconds. The default automount is never used: every pod of the
   service sets `automountServiceAccountToken: false`, and the API and the build worker get no token at all. At start, a
   process that connects from a pod checks with a SelfSubjectReview that the token's username equals
   `KUBE_EXPECTED_USERNAME`, and exits when it does not. The production profile refuses the kubeconfig connection; the
   prototype profile accepts both, the project-local kubeconfig for the processes on the host and the projected token in
   Kind. The wrapper `infrastructure/kubernetes/_client.py` stays the only place that builds a client, and every other
   loader stays banned by lint.
3. **Stand-in files as ConfigMaps.** In the cluster, `access.json`, `entitlement.json` and `repositories.json`
   (`.claude/docs/architecture/prototype-stand-ins.md`) are one ConfigMap, mounted as a directory and never through
   `subPath`, so the processes' reload sees each change. The e2e harness updates it and waits until every pod that
   mounts it reads the new content. The Kind kubelets get `syncFrequency: 5s` in `deploy/kind/cluster.yaml`, which takes
   one rebuild of the prototype cluster. A host folder mounted into the Kind nodes is not used: it would not work on the
   GKE test cluster of section 8 ruling 3, which runs the same stand-ins.
4. **Base image.** The service image is built on `python:3.14-slim-trixie` (Debian 13), pinned by digest and at least 14
   days old when pinned, and keeps `psycopg[binary]`. A High finding in the base image that has no fix gets a
   time-bounded exception of at most 30 days, each signed off by the owner, as section 7 ruling 3 did for the sample
   site. Consequences: `psycopg[binary]` in this image is a recorded exception to `0013` section 9 S2 for the
   prototype-profile image only; a production image (phase P-P) still uses pure `psycopg` with the base image's `libpq`.
   Grype does not see the `libpq` and OpenSSL that the binary wheel bundles, which stays a known gap of this image.
5. **Build tool.** The image is built with a pinned, checksum-verified `docker-buildx` plugin in `./bin`, used through a
   project-local `DOCKER_CONFIG`, so nothing is installed system-wide. The legacy builder is the fallback, so the
   Dockerfile keeps to the syntax the legacy builder reads. The stack's BuildKit is never used for a platform image,
   because it runs untrusted tenant Dockerfiles (OD-37, OD-62). This also closes finding F-10 of the P-H validation
   (`ai/reviews/ph-validation.md`): `make image-scan` can build the sample site with BuildKit on the development
   machine.

**Amended by ruling 2**: `0013` section 9, "Real-cluster safety" ("in production from the in-cluster ServiceAccount
only, in tests from an explicit project-local kubeconfig"), and the last rule of the retired ARCHITECTURE.md section 2.
The in-cluster path is now the projected token with the username check, it may also run in the prototype profile on
Kind, and production accepts nothing else.

## 10. The MVP site domain

The owner ruled this for the first real deployment (the k3s spike, `.claude/docs/designs/k3s-site-cluster-spike.md`
G13 and question Q3). It serves TR-44, FR-19 and FR-20.

1. **Where sites run.** Generated websites run on k3s on the owner's EC2 instance. This is the MVP target for
   sites; sections 1 and 3 still describe DOKS for the platform's own services.
2. **The site domain is `roaa15.xyz`.** `SITE_DOMAIN=roaa15.xyz`, so a live site is `<subdomain>.roaa15.xyz`. It is a
   zone of its own, so Cloudflare's free edge certificate covers it (one wildcard level). `sites.roa-dev.digital`,
   named in web-block-config, is not the MVP site domain.
3. **The public path.** Cloudflare Tunnel runs on the EC2 instance and sends `*.roaa15.xyz` to Traefik.
4. **Previews are public with the project's token.** `<subdomain>-preview.roaa15.xyz` is one level deep, so the
   free Cloudflare certificate covers it and the tunnel's `*.roaa15.xyz` route reaches it. The preview route answers
   only a request carrying the project's preview token and removes the token before the site (FR-20, FR-52; decision
   `0023` G18b, G18c). Cloudflare caches static files by host and path, not by the token header, so every preview
   answer carries `Cache-Control: private, no-store` (FR-52.1), and the zone needs a Cloudflare cache rule that
   bypasses the cache for hosts ending in `-preview.<site domain>`. That rule is the owner's to pass to
   web-block-config; Provisioning does not write Cloudflare settings.
5. **The local prototype keeps `sites.webblock.test`** (`0014`, OD-36 stays open for the prototype only).

Cost if wrong: only configuration moves (`SITE_DOMAIN`, the Gateway listener hostnames, the certificate names, the
tunnel route and the admission policy's host pattern). No code or schema changes.

The spike's step 1 (local k3d) is approved to start after the go-live tries tasks; step 2 (EC2) is run by the owner.
