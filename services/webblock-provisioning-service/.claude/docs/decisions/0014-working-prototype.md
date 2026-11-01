# 0014: Working prototype first

**Amended by [`0027`](0027-go-live-tries.md):** going live takes up to 3 tries; section 3
"Promote" and section 17 item 1 (a failing preview fails the command, the release stays `candidate_ready`) no
longer apply to a promote.

Status: **Current; decided by the owner.** The prototype runs on the local Kind stack (`make proto-up`,
`make proto-e2e`). This decision changes the delivery goal and the process. It does not change the database design:
revision 19 (`.claude/PLATFORM_DB_DESIGN.md`) is agreed with the team and is not edited.

## 1. Goal

Build a **real, working prototype** of the Provisioning service in Python, as fast as possible. It runs on this machine
against the local Kind cluster. It handles the edge cases the service really meets (section 4) and does not defend
against cases that do not happen in practice. A production application comes later; this is not it.

## 2. Owner's answers

1. **Flows, all four, end to end on Kind:**
   - publish → build → live;
   - update with automatic rollback;
   - suspend and resume hosting;
   - MCP tools for agents.
2. **Database design untouched.** No table, column, enum value or constraint is added, removed or renamed beyond
   migration `00011`, the revision 19 rename the team already agreed (`0012`). If the prototype seems to need a new
   field, the lead asks the owner first. The operational tables `provisioning_ops.*` of `0005` and `0008` stay as they
   are, and `00011` renames them.
3. **Builds run on Jenkins.** The owner provides a dedicated Jenkins server that builds the generated website and pushes
   the image. **For the prototype, Jenkins runs locally in Docker**, with a pipeline the project supplies.
4. **Provisioning polls Jenkins** for the result. Jenkins needs no route back to Provisioning.
5. **A dedicated build endpoint calls Jenkins.**
6. **Registry: the project's local Kind registry for now.** The registry location is one configuration value.
7. **RabbitMQ stays.** Command dispatch follows `0005` (SQL row first, broker notifies).
8. **Lean process** (section 5).

## 3. Shape of the prototype

### Processes

Each process is a host under `src/provisioning/hosts/`:

| Process | Does | Identity |
| --- | --- | --- |
| `api` | REST (FastAPI) and MCP over the same use cases | No cluster credential |
| `worker` | Consumes command jobs; deploys, promotes, rolls back, suspends and resumes by server-side apply | ServiceAccount `provisioning-worker` |
| `build_worker` | Consumes Project's "source published" events into the inbox; starts builds on Jenkins; polls them | No cluster credential |
| `bootstrap` | Creates the tenant namespace and the worker's RoleBinding before the first deploy | Its own ServiceAccount `tenant-bootstrap` |

### Build

- **The build endpoint.** `POST /v1/projects/{project_id}/releases/{release_id}/build`, with the matching MCP tool
  `build_release`. It starts the build of an `accepted` release, or a new attempt for a release whose last build attempt
  failed while attempts remain.
- **The event uses the same path.** Accepting a "source published" event calls the same use case, so a build still
  starts by itself.
- **Not a command.** A build is tracked through `releases` and `release_steps` (`0008`), not `commands`, so no command
  kind is added. A release that has reached `failed` is not rebuilt: publishing again creates a new release.
- **What Provisioning sends.** It triggers the Jenkins job with:
  - project ID, release ID, repository URL and the exact commit SHA;
  - the target image repository `<registry>/webblock/sites/<projectId>`.
- **What it stores.** The Jenkins build URL goes into the build step's existing `log_ref` column.
- **What it accepts.** Provisioning polls Jenkins' REST API. It accepts the result only when project, release and commit
  SHA match the release, and it stores the image digest the pipeline reports.

### Deploy, promote, suspend and resume

- **Deploy.** The `deploy` command applies Deployment and Service `rel-<releaseId>` in `wb-<projectId>`, then waits for
  readiness (the acknowledgement of `0013` section 3 and `review-k8s-nocp.md` M1).
- **Promote.** The `promote` command switches HTTPRoute `route-<projectId>` to the candidate, then checks that the site
  answers through the Gateway.
  - If promotion fails, the route is restored to the live release and the candidate ends `rolled_back`.
  - On a first publish there is no live release to restore, so the route gets a tombstone backend and the site stays
    offline (K9).
- **Rollback.** `rollback` is operator recovery of an uncertain route.
- **Suspend and resume.** `suspend_hosting` points the route at an offline backend; `resume_hosting` points it back at
  the live release. Each comes from a **simulated Payment event** on RabbitMQ, never from REST or MCP (CLAUDE.md section
  3).
- **Simulated events.** A developer tool publishes simulated Project and Payment events on RabbitMQ.

### Prototype settings

- **Authentication.** A static development bearer token per caller, set in configuration. Project access and entitlement
  checks are stand-ins that allow, and they are named and confined to the prototype.
- **Hostnames.** Sites are served under the test suffix `*.sites.webblock.test` (OD-36 stays open).
- **Catalog access.** `access_setup` is left out; prototype sites have no CMS database.

## 4. Edge cases in scope

Each has a test, with real PostgreSQL 18, real RabbitMQ, or Kind where it matters:

- Duplicate or redelivered events and messages are processed once.
- A retry with the same idempotency key returns the original operation; the same key with a different request is a
  conflict.
- A second command while one is in flight for the project is refused as busy.
- A worker that crashes mid-command is resumed by another worker after its lease expires. A stale worker's write is
  refused by fencing.
- A command row that was committed but never published to RabbitMQ is published again from SQL.
- A Jenkins build that fails, times out or reports the wrong commit fails the attempt. Retries are bounded.
- A candidate that never becomes ready fails the deploy, and the live site is untouched.
- A promotion that fails restores the route to the live release.
- Suspend while a deploy is in flight waits or is refused, by the existing rules.
- Provisioning restarting while a Jenkins build runs picks the build up again from `log_ref`.

## 5. Out of scope for the prototype

These are suspended, not deleted; the production plan of `0013` resumes after the prototype:
- per-phase independent validators;
- the parity map and per-package coverage thresholds;
- the offline vulnerability database gate;
- the 14-day version-age rule;
- admission policies against cluster administrators;
- the fail-closed production build;
- runtime image scanning.

The Go reference is no longer maintained: Go gates are not required. It stays pinned to `00010` (T2.0), so it does not
break.

## 6. Process

- One implementer agent per slice, with exclusive file ownership.
- Gates per slice: `ruff format --check`, `ruff check`, `mypy --strict`, `lint-imports`, the pytest unit suite, real
  PostgreSQL 18 and RabbitMQ tests for what they touch, and Kind tests for cluster behaviour.
- The lead reviews each diff and reruns its gates before merging.
- One independent review of the whole prototype at the end.

The real-cluster rule of `.claude/CLAUDE.md` section 0 is unchanged and overrides everything.

## 7. Supersedes

For the duration of the prototype, this decision supersedes:
- CLAUDE.md section 4 (the gate table) and section 6 (the per-phase validator), as section 5 above says;
- `0013` section 5 (phases P0 to P4) and section 10.1 (the P0 task split);
- CLAUDE.md section 3's "there is no build operation". The build endpoint starts or retries a build; it is not a
  command, and the automatic start stays.

Everything else in `0001` to `0013` applies, in particular fencing, leases, idempotency, the request fingerprint,
per-project serialization, the step guards, the database roles, and the naming rule of `0009` section 3.

## 8. Column grants for promote, suspend and resume

Revision 19 lists `releases.promoted_at`, `project_hosting.live_release_id`, `routing_status` and `hosting_status` as
"not yet granted to any role" (Phase 5 and 6). The prototype's promote, rollback, suspend and resume write exactly these
columns. The owner approved **migration `00012_worker_hosting_grants.sql`**, which runs after `00011` and does only
this:
- `GRANT UPDATE (promoted_at) ON provisioning.releases TO provisioning_worker`;
- `GRANT UPDATE (live_release_id, routing_status, hosting_status) ON provisioning.project_hosting TO
  provisioning_worker`.

It adds or changes no table, column, enum, constraint or guard, and `.claude/PLATFORM_DB_DESIGN.md` is not edited.

**Promotion and rollback get no step rows.** The step guards of `00010` admit only `build` and `candidate` steps, so
these two operations are recorded by the command row and the release status alone. Promotion and rollback step rows
would need a guard change, and that waits for the owner.

## 9. Command-acceptance grants for hosting intake

A suspend or resume command may be inserted only by a member of `provisioning_hosting_intake` (the guard of `00010`,
renamed by `00011`), and that role has no privilege yet. The design also says no login may join more than one of these
roles. The owner approved **migration `00013_hosting_intake_grants.sql`**, which gives `provisioning_hosting_intake`
exactly the command-acceptance privileges that `provisioning_api` holds:
- `USAGE` on the schemas `provisioning` and `provisioning_ops`;
- `SELECT` on `releases`, `project_hosting`, `release_steps` and `commands`;
- `INSERT` on `commands`;
- `UPDATE (last_command_number, updated_at)` on `project_hosting`;
- `INSERT` on `provisioning_ops.command_dispatch`.

No table, column, enum, constraint or guard changes. The Payment-event consumer logs in as a member of this role alone.

## 10. Lead rulings for the second wave

1. **Bootstrap's reads.** The bootstrap process finds projects that need a tenant through a login that is a member of
   `provisioning_api` alone, and it only reads. The read-only role `provisioning_bootstrap` of `0013` section 9 (K8)
   would need a migration, so it waits until after the prototype.
2. **Repository location.** `RepositoryLocator` (task B1) is a prototype stand-in fed by a configuration file that maps
   `github_repository_id` to a Git URL on the local Git server. Project's registry (OD-01) replaces it later.
3. **Build retry.** After a failed attempt, the next build starts through `build_release` (REST or MCP), not by itself.
4. **Prototype event payloads.** Project's "source published" event and Payment's "entitlement ended" and "entitlement
   restored" events get prototype-only JSON schemas under `src/contracts/events/prototype/`. Each is marked as a
   stand-in, not an agreed contract (OD-25 and OD-42 stay open), and a developer tool publishes them.
5. **Who consumes what.** The `worker` process consumes Payment events, through a login of `provisioning_hosting_intake`
   alone, and turns each into a `suspend_hosting` or `resume_hosting` command whose idempotency key is the Payment event
   key. The `build_worker` process consumes Project's events.
6. **Failure codes.** The deploy failure codes carried over from Go with Crossplane names are renamed to plain-object
   names by the deploy task.

## 11. Data access through SQLAlchemy Core

The owner asked for SQL handling that rules out injection, and chose SQLAlchemy. The stores use **SQLAlchemy Core 2.1**
(`sqlalchemy[asyncio]==2.1.3`) on the psycopg driver, not the ORM unit of work, for three reasons:
- the guard triggers, column-level grants and lock order need explicit statements;
- `ON CONFLICT` with index predicates and `FOR UPDATE SKIP LOCKED` must be expressed exactly;
- every value is bound by construction.

Table definitions in `infrastructure/postgres/schema.py` mirror the migrated schema and **never create or alter it**. A
drift test compares them with the database catalogue, and the goose migrations stay the only schema source. Engines run
with `hide_parameters=True`, and SQLAlchemy and driver error text never leaves the store. The rules are in
`.superpowers/sdd/python/proto-sql-safety.md`, and a contract test enforces them. This supersedes `0013` section 4's
"handwritten parameterized SQL per store" and `review-port-arch.md` M2 for the prototype.

## 12. Rulings on task F1 (deploy)

1. **An unexpired candidate attempt is resumed, not failed.** The next claim of a `deploy` command resumes a running
   candidate step whose deadline has not passed, instead of failing it with `deadline_exceeded`. This amends `0011`
   section 12 (the rulings on G2) for the Python service. Without it, every transient cluster error (`TenantNotReady`,
   `ClusterUnavailable`) or worker crash would use up one of the release's 10 candidate attempts. An expired step is
   still failed by the claim that finds it.
2. **An image pull failure fails the command at once.** The release stays `built`: under `0011` section 3, only a
   refusal by the cluster moves a release from `built` to `failed`.
3. **Failure codes are renamed** for plain objects:
   - `candidate_xr_conflict` → `candidate_apply_conflict`;
   - `candidate_generation_ahead` → `candidate_fence_ahead`;
   - `candidate_xr_refused` → `candidate_refused`;
   - `candidate_refused_by_composition` is removed.

   New codes: `progress_deadline_exceeded`, `image_pull_failed`, `crash_loop`, `release_not_deployable`,
   `route_not_stable` and `hosting_offline`.
4. **A known gap that cannot occur.** A deploy refused for `release_not_deployable`, `route_not_stable` or
   `hosting_offline` is written through `fail_without_route_change`, which requires a built release and a stable route.
   Under per-project serialization these refusals cannot meet a running deploy, so the gap is recorded, not closed.

## 13. Leaving an uncertain route (closes OD-45 for the prototype)

**Problem.** A suspend or resume whose route change cannot be confirmed leaves `routing_status = uncertain`. Under
`0001`, operator `rollback` needs a `promoting` release, so nothing could clear that state, and every later Payment
event would be refused and retried indefinitely.

**Decision.** The operator `rollback` command is accepted for **any** project whose route is `uncertain`, with or
without a `promoting` release. It points the route where the recorded state says:
- the offline page when `hosting_status = offline`, or when there is no live release;
- otherwise the live release (`live_release_id`).

It confirms the route at its command number, then sets `routing_status = stable`. If a `promoting` release is still
recorded, it becomes `rolled_back` as before.

This amends `0001`'s rollback precondition for the Python service. It needs no database change: `rollback` commands
already carry a release ID, so the operator names the live release, or, on a first publish, the release that failed.

## 14. Catalog database and a real demo shop

The owner asked for the shared catalog database (design section 6) and a real clothing shop, built with **Next.js** and
**Drizzle ORM** for its database access, to test the service from build to live.

**Catalog database.** The catalog is built exactly as `.claude/PLATFORM_DB_DESIGN.md` section 6 describes:
- tables, enums and constraints as designed;
- the roles `catalog_owner`, `catalog_platform` and `catalog_site`;
- `ENABLE` and `FORCE ROW LEVEL SECURITY` with `USING` and `WITH CHECK`;
- `catalog.current_website_id()` as `STABLE SECURITY DEFINER` using `session_user`.

It runs as a PostgreSQL 18 container `webblock-catalog-db` on the prototype stack's Docker network, started by
`make proto-up`. Following the design's ownership, the catalog schema migrations and the `catalog.websites` and first
`business_profiles` rows are **Project's**. In the prototype they come from clearly named stand-ins: migrations under
`deploy/prototype/catalog/`, and a developer tool acting as `catalog_platform`.

**Provisioning's part, `access_setup`, run by the bootstrap component** (`0013` section 9 K1):
- It creates the login role `site_<project id without hyphens>`, a member of `catalog_site` only, with a SCRAM-SHA-256
  verifier computed in Python, so the cleartext password never appears in SQL or a log.
- It writes the Secret `catalog-runtime` in `wb-<projectId>` **before** setting the role's password. The Secret holds
  the keys `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` and `PGSSLMODE`.
- It verifies the role, as `0011` section 5 describes. It creates the role or delivers a credential only under the
  conditions of `0011` section 5 and the G2 follow-up 1 ruling.

The bootstrap identity gains create and patch on Secrets named `catalog-runtime` in tenant namespaces, a prototype RBAC
change. The worker never reads the Secret. Its Deployment takes the values through `envFrom` a `secretRef`, and the
intent digest includes that reference.

**Demo shop.** A real Next.js (App Router) clothing shop, `deploy/prototype/demo-sites/clothing-shop/`:
- product cards with image, name and price, and a "View details" page per product;
- it reads its own website's products from the catalog through Drizzle ORM, logged in as its `site_<uuid>` role from the
  Secret;
- it runs unprivileged on port 8080 with a read-only root filesystem.

It is published to the local Git server and goes through the real flow: Project event → Jenkins build → deploy →
promote. Its seed data (realistic products) loads through the `catalog_platform` stand-in.

## 15. Readiness contract for generated sites

**Problem.** A generated site that cannot reach its catalog still shows a friendly page with HTTP 200. Next.js cannot
server-render its error page with a 5xx status. A promotion probe that accepts anything below 500 would therefore
promote a site with a broken `catalog-runtime` Secret.

**Rule.** For the prototype, every generated site serves two endpoints:
- **`/healthz`:** 200 when the process is up, without touching dependencies. Kubernetes uses it for readiness and
  liveness.
- **`/readyz`:** 200 only when the site's dependencies work, including a catalog query as its own role; 503 otherwise.

**The promotion probe** (`SiteCluster.probe_site`) sends `GET /readyz` through the Gateway with the site's host name,
and requires **200**. The path comes from configuration.

Generated workloads are produced by AI Codegen for Project, so this endpoint contract has to be agreed with those owners
before production (a new open question). In the prototype, the demo shop and the sample sites implement it.

## 16. Database changes and custom domains

The owner approved the following changes to the database design. Each is a migration, and each is recorded in
`.claude/PLATFORM_DB_DESIGN.md` by the lead once its migration is merged. Nothing else in the design changes.

**Provisioning (migration `00014` and later, forward-only):**
1. **The `sync_domains` command.**
   - **Kind:** new `command_kind` value `sync_domains`. A domain change (a purchased domain becoming active, a primary
     hostname switch, an expiry) is applied to the route and the certificates under the same fencing as every other
     command. The application gets the active hostnames from Project (a prototype stand-in until Project's contract
     exists; OD-13 and OD-36). The release-wide preconditions stand: the route must be `stable`, and hosting `online`.
   - **Command row:** like the hosting kinds, it has no `release_id` and no `requested_by_subject`;
     `commands_release_by_kind` and `commands_requested_by_subject_by_kind` are adjusted.
   - **Idempotency:** the key is the Project event key, in its own partial unique index `commands_domain_event_key` on
     `(project_id, idempotency_key) WHERE kind = 'sync_domains'`. *(Migration `00025` drops that index: Notion
     names two key classes only and never starts `sync_domains`, constitution III.3.)*
   - **Who inserts it:** only a member of `provisioning_intake` (the consumer of Project's events), which receives the
     command-acceptance grants `provisioning_api` has. *(Those grants are revoked by migration `00017`: section 23,
     amendment.)*
2. **The guard gap.** `commands_insert_shape` also refuses `deploy`, `promote` and `rollback` unless the session is a
   member of `provisioning_api`. Found by task B3.
3. **The design document** records the grants of `00012` and `00013`.
4. **Promotion and rollback step rows.** The step guards admit `promotion` and `rollback` steps written by
   `provisioning_worker`, for history and diagnostics.
5. **A read-only role `provisioning_bootstrap`** (`0013` section 9 K8). It may `SELECT` the columns bootstrap needs, and
   it replaces the `provisioning_api` login of section 10.1.

**Catalog (the Project stand-in migrations under `deploy/prototype/catalog/`, and design section 6):**

6. `catalog_platform` gains `SELECT` on `catalog.websites`, needed for its `UPDATE … WHERE website_id = …`.
7. `catalog_platform` gains `SELECT` on `catalog.business_types`.
8. `TEMP` on the catalog database is revoked from `PUBLIC`, so site roles cannot create temporary tables ("no DDL").

**Custom domains and certificates are required for the prototype.** On Kind:
- cert-manager with a local CA `ClusterIssuer`, standing in for the public ACME issuer (OD-58);
- HTTPS on the Gateway, with a wildcard certificate for the platform subdomain suffix;
- for each purchased domain, a per-project `ListenerSet` and a cert-manager `Certificate` in the tenant namespace
  (`0013` section 9 K3), which needs Gateway API v1.5 and Cilium 1.20 on the Kind stack.

A promotion to a purchased hostname requires its certificate to be `Ready`, and the route's listener to have resolved
its references and be programmed (REQUIREMENT section 4).

**GKE.** The owner may later provide a GKE cluster for verification. Until the owner explicitly allows a named cluster
in `.claude/CLAUDE.md` section 0, the real-cluster rule still forbids every cluster except the local Kind cluster.

## 17. Fixes after the independent review

The review ai/reviews/prototype-validation.md (removed; in git tag docs-before-cleanup) approved the prototype with 4
medium and 5 low findings. The owner chose items 1 and 4 below; the rest are the lead's rulings.

1. **A promotion proves which release answers (M1, M4; owner).**
   - **Preview first.** Before the site's route moves, the worker points a per-project HTTPRoute `preview-<projectId>`
     with the one hostname `preview-<projectId>.<site domain>` at the candidate's Service, confirms it at the command's
     number, and probes `/readyz` through the Gateway. The worker re-points this route for each candidate and never
     deletes it.
   - **The platform names the backend.** Every rule of the site's route and of the preview route sets the response
     header `X-Webblock-Release` to the backend's release ID, or `offline`, with a `ResponseHeaderModifier` filter.
     Generated code cannot set it, because the Gateway overwrites it, and generated sites need no change.
   - **The probe requires identity.** `probe_site` succeeds only on 200 with that header equal to the expected target.
     After the switch, the probe of the site's own hostname must name the candidate. A response from the old backend,
     which the Gateway may still use for a moment after the route is accepted, no longer counts.
   - **A candidate that fails the preview probe** fails the command with `candidate_not_answering`. The release stays
     `candidate_ready` and the site's route is never touched, like `entitlement_lost`.
   - The flow document's "never broken" claim is corrected to match.
   - **A resumed claim**: when the claim is resumed with the release already `promoting` and routing `changing`, a
     failing preview cannot leave the release `candidate_ready`. The promotion then restores the route as for a failed
     switch: the candidate ends `rolled_back` (`failed` on a first publish), and the command fails with
     `candidate_not_answering`, or `route_refused` when the cluster refuses the preview. The preview and the switch each
     have `ROUTE_SWITCH_DEADLINE`; the prototype sets it to 20 s.
2. **A lost entitlement record never skips a shutdown (M2).** A `suspend_hosting` command ends `skipped` only when the
   worker has recorded an "entitlement restored" event that arrived after the event that created the suspend. When the
   record does not know (a restarted worker, another replica), the suspend runs. Production asks Payment instead
   (OD-04).
3. **The bootstrap identity is limited by admission (M3).** The prototype stack installs a ValidatingAdmissionPolicy
   `webblock-bootstrap-scope` for `tenant-bootstrap`. It allows writes only in namespaces named `wb-<uuid>` (plus the
   ReferenceGrants in `webblock-offline`), only Secrets named `catalog-runtime` of type `Opaque`, only RoleBindings to
   the ClusterRole `webblock-tenant-worker` for the worker's ServiceAccount, and namespace labels that keep Pod Security
   `restricted`. This lifts the "admission policies" item of section 5 for this one identity.
4. **Down sections (L2; owner).** `00012` and `00013` keep their grant-only `-- +goose Down` sections as a recorded
   exception; the files are merged and stay unchanged. Every later migration is forward-only with no Down section.
5. **Low findings.** L1: a make target builds the two test images through Jenkins and runs the live Kind suite. L3: the
   authentication middleware closes WebSocket scopes with code 1008. L4: closed by `provisioning_bootstrap` (section 16
   item 5). L5: a restart during a build becomes an end-to-end test, and each run of `proto-run` keeps its logs and
   timings in its own folder.

## 18. Where the Go reference goes

- **The Go code is kept on its own branch, `go-reference`,** created from `python` at `5ffd27e`. The last Go state with
  its gates passing is there, together with the earlier tags `go-reference-phase-4`, `g4-acceptance-wip` and
  `f2-guarded-delete-wip`. That branch is never merged back.
- **The main line drops the Go code once the Python port is done.** The removal is one change: every `.go` file,
  `go.mod` and `go.sum`, the Go targets of the Makefile, the Go-only tests (`tests/acceptance` and the Go parts of
  `tests/contracts`, `tests/integration` and `tests/messaging`), the Go tools pinned under `bin/`, and the Crossplane
  material (`deploy/crossplane/` and the Crossplane parts of `deploy/kind/`, phase P4 of `0013`). `.claude/CLAUDE.md`,
  `.claude/AGENTS.md`, `.claude/TECH_STACK.md` and the runbooks are updated in the same change.
- **"Done"** means the owner says so. The lead proposes it after the recheck R2 and task T are merged with every gate
  passing. Until then the Go reference stays pinned to `00010` (T2.0), and its gates are not required (section 5).

## 19. Every feature exists

**Rule.** The prototype must contain every feature of the service, each working end to end on Kind: everything in the
retired REQUIREMENT.md and in the retired ARCHITECTURE.md that is not marked as another service's or as production
hardening. Production readiness can wait (phases P-H to P-R). Stand-ins remain allowed where another service's contract
or a production resource is missing. Each stand-in is named, listed in ARCHITECTURE section 16, and replaceable by
configuration.

**Example: certificates.** On Kind, purchased-domain certificates are issued through a real ACME flow, not only the
local CA:
- the issuer is Pebble, Let's Encrypt's test ACME server;
- DNS-01 is answered by Pebble's challenge test DNS server;
- moving to the production CA (OD-58) is then a change of issuer configuration only.

**Process.** A read-only gap analysis lists every requirement and architecture feature against the code and its
evidence. The missing features become wave 6, after task T.

## 20. One source for per-site manifests

**Decision.** The worker's renderer (`infrastructure/kubernetes/render.py`) is the only source of the per-site and
tenant manifests. The reasons:
- the manifests carry logic (a listener per purchased domain, the redirect route, release or offline backends, the
  intent digest computed from the same structure) that templates could express only with loops and conditions;
- the renderer builds objects from validated values, never by text substitution, under type checks and golden tests.

**Readable copies.** A make target generates the example manifests from the renderer into `deploy/prototype/manifests/`,
for people to read and for `proto-smoke`. A test fails if those files differ from the renderer's output. The
hand-written `*.yaml.tmpl` copies are removed.

**Validation.** Every manifest kind is validated on Kind with a server-side dry run.

**Helm** is used to install Provisioning itself and the platform, never to render per-site objects. All of this is a
wave 6 task.

## 21. Wave 6 scope: the remaining features

The gap analysis ai/handoffs/archive/wave6-gap-analysis.md (removed; in git tag docs-before-cleanup) lists what section
19 still needs. Wave 6 starts after task T is merged.

**The owner's answers:**
1. **Per-site admission policies and a tenant network policy are features now** (PR-08 and PR-17 of the retired
   `REQUIREMENT.md`).
   - The admission policies cover the binding, the fence, the image, the route shape, the guard against deleting the
     routed release, and the writer.
   - The network policy blocks cross-tenant traffic, the API server and the metadata address, and allows Gateway → site
     and site → catalog.
2. **The usage event "publish completed" is added through an outbox.**
   - One new operational table in `provisioning_ops`, following the `command_dispatch` pattern, published once per
     release that goes live.
   - This is an owner-approved database change; the lead records it in `.claude/PLATFORM_DB_DESIGN.md` when it is
     merged.
   - Payment's real contract stays open (OD-04, OD-42).
   - The table has no due index: migration `00025` drops `usage_outbox_due`, which Notion does not show
     (constitution III.3).
3. **Audit means structured audit log events**: who, what, when and the result, for operator actions and every mutation.
   They are shipped to Loki. There is no audit table (OD-14 stays open for one).
4. **Project deletion takes the site offline, and images are kept.**
   - A simulated "project deleted" event becomes a `suspend_hosting` command, accepted under
     `provisioning_hosting_intake`.
   - A later "entitlement restored" never resumes a deleted project.
   - No image is deleted in the prototype (OD-48).

**The lead's rulings:**
5. **Certificates through real ACME on Kind.**
   - Pebble is the ACME server. DNS-01 is answered by a small DNS server that cert-manager updates natively (RFC2136, as
     cert-manager's own tests do), and Pebble resolves through it.
   - The platform wildcard certificate is issued by Pebble too.
   - Switching to the production CA (OD-58) is a change of issuer configuration only.
6. **Diagnostics come from rows that exist:** steps, commands, failure codes and the inbox outcome. They are bounded and
   redacted, with no new column. This amends `0004` section 3.
7. **Listing reads:** `list_releases` and `list_commands` for a project, in REST and MCP. The MCP tool set grows from
   eight with these two reads only; no new mutation.
8. **Cleanup.** Superseded, rolled-back and failed workloads are deleted by the worker (guarded: never the routed
   release, fenced like every write). The worker may delete a `Certificate` and a listener only for a hostname that is
   no longer active.
9. **Recovery.** A recovery sweeper resolves commands that pass their deadline while routing is `changing`, ending
   `stable` or `uncertain`, never stuck. This fixes the stuck-project defect of the gap analysis. A periodic reconciler
   re-applies drifted objects and reports orphans and foreign field managers, never forcing.
10. **Refusals shown live.** The project-access and entitlement stand-ins become file-based and reloadable, so refusals
    can be shown end to end.
11. **The inbox re-drive** gets an operator tool with its own `provisioning_operator` login, and an operator runbook.
12. **Readiness.** The site container's readiness probe becomes HTTP `/healthz` (intent digest version 4).
13. **Manifests:** section 20.

Metrics and Sentry stay in phase P-H (`0016` section 5).

## 22. One Next.js application per site

- **Each website is one Next.js application:** its public pages, its CMS (the user's content management) and its APIs
  live in the same code and the same image. There is no separate backend image, and Provisioning's model is unchanged:
  one release, one image, one Deployment per site.
- **The backend part is the same code for every site of a business type** (phone shop, clothing and so on). Only the CMS
  type changes with the business type. The web portal and the planned mobile app therefore use the same API on every
  site of a type, and their UI can stay consistent.
- **Recommended to AI Codegen and Project** (their decision): the backend part is not generated per site. It comes from
  a versioned template maintained by the platform, for example a package per business type on a shared core, that the
  generated site uses. The AI generates only the public pages and the wiring. A backend fix then reaches every site
  through new commits and builds, by the normal release flow.
- **A headless CMS instance per site** (Strapi, Directus, Payload) is not recommended: it conflicts with the shared
  catalog's per-site roles, row-level security and no-DDL rule, and costs a server per site. Payload as the base of the
  template is possible only after a spike against that catalog design.

## 23. No custom domains for now; wave 6 answers

**1. Sites use only their platform subdomain.**
- The platform does not provide custom domains for now. Every site is served only on
  `<projectId>.sites.<platform domain>`.
- The custom-domain code of task T2 is removed in one task, W6-R, before the rest of wave 6:
  - the `sync_domains` executor and intake;
  - the Project domains stand-in and the domains-changed event;
  - `ListenerSet` and per-domain `Certificate` rendering, and the adapter and port methods;
  - redirects to a primary hostname;
  - the tests of all of the above.
- What stays:
  - migrations `00014` and `00015` are immutable, so the `sync_domains` enum value and its guard remain in the database,
    unused;
  - `CommandKind.SYNC_DOMAINS` stays, to mirror the schema;
  - the preview hostname and its token (N1) stay.
- The code can be restored from git (`c5796ec`) if custom domains return. This supersedes the domain parts of sections
  16, 17 and 21 (QA-09 is dropped).

**2. Certificates: the local CA stays.** The only certificate is the platform wildcard, installed with the cluster.
Ruling 5 of section 21 (Pebble ACME) is withdrawn. Production gets the wildcard from a real CA through the cluster setup
(OD-58), not through Provisioning.

**3. Operator rollback may take back a route that was edited by hand (QA-07).**
- Only the explicit, audited operator rollback may force-apply the project's one HTTPRoute, re-stamping the fence. That
  covers a route edited by a foreign field manager, or stamped with a higher command number.
- Every other write stays never-forced, the reconciler included.

**4. Diagnostics show the inbox outcome.** `provisioning_api` gets `SELECT` on the non-sensitive columns of
`provisioning_ops.source_published_inbox` that diagnostics need. The grant goes in migration `00016`, together with the
usage outbox (section 21, answer 2).

**5. Audit events go to Loki on Kind.**
- The Kind stack gains Loki and a log shipper.
- The audit events (JSON log lines with `audit=true`) are queried from Loki in the live tests.
- Metrics and Sentry stay in phase P-H.

**The lead's rulings for wave 6:**
- The QA-03 retry reuses the Payment intent with key `<event_key>.retry-<n>`.
- Project deletion is remembered by a `suspend_hosting` command with the fixed key `project-deleted`. A later resume for
  that project ends `skipped` (OD-52 stays open for production).
- The usage event `publish_completed` has a prototype stand-in schema under `src/contracts/events/prototype/`.
- The uncertain-route alert is an audit log event with `alert=true`, not a broker message.
- `BuildSystem` gains `cancel(handle)`, so that a build attempt failed by its deadline is aborted in Jenkins.

**Amendment (migration `00017`): the intake loses the `sync_domains` grants.** Item 1 keeps the `sync_domains` schema
value but said nothing of the grants `00015` gave `provisioning_intake` for it (section 16 item 1). They are revoked.
- **Why.** `00015` widened `provisioning_intake` only so that it could accept `sync_domains` commands. Item 1 removed
  that intake and its executor, so nothing uses these grants. They left the build worker's intake login able to read
  every command and the whole hosting row, to lock and advance the hosting counter, and to insert commands and dispatch
  rows: privilege with no use.
- **What is revoked.** A new forward-only migration `00017`, with no Down section (section 17 item 4), revokes every
  `00015` extra: `SELECT` on `releases`, `project_hosting`, `release_steps` and `commands`; `INSERT` on `commands` and
  on `provisioning_ops.command_dispatch`; `UPDATE (last_command_number, updated_at)` on `project_hosting`.
- **What stays.** The `00008` reads that the intake's release and step guards need: `SELECT` on `releases` and
  `SELECT (project_id)` on `project_hosting`. A table-level `REVOKE` also removes the column privileges of that table,
  so `00017` grants these two again. `USAGE` on both schemas, held since `00008`, is not touched. After `00017` the
  intake holds exactly the privileges it held at `00014`.
- **`sync_domains` stays in the `command_kind` enum, unused**, with the rule of `commands_insert_shape` that only a
  member of `provisioning_intake` inserts one. Migration `00025` drops its index `commands_domain_event_key`. With
  no `INSERT` on `commands`, the intake can no longer insert any command. If custom domains return, a new migration
  grants the acceptance privileges again, together with the code of `c5796ec`.

## 24. A rebuilt tenant namespace gets a new catalog password (narrows `0011` section 5)

**The problem.**
- `0011` section 5 makes a missing `catalog-runtime` Secret, while a live release exists, the permanent failure
  `runtime_secret_missing`, for an operator.
- After a tenant namespace is deleted, that rule blocks recovery forever. The Secret and the live pods are gone
  together.
- Wave 6 rebuilds the namespace and re-applies the workload (QA-04, W6-L; the reconciler, W6-B), so recovery needs a
  password.

**Ruling.**
- When the bootstrap process itself observed the tenant namespace as **absent** before it created it again,
  `access_setup` treats the project as having no running consumer of the password. It delivers a new password: the
  Secret is written first, then the role's password is set.
- No pod can be using the old password, because the old pods went with the namespace.
- In every other case, `0011` section 5 holds unchanged. A Secret missing from a namespace that exists, while a live
  release exists, is still `runtime_secret_missing`.
- The mark lives only in the process. After a restart it is forgotten, and the rule falls back to `0011` section 5,
  which is the safe direction: an operator, not a silent rotation.

**Audit.** The rotation is an audit event (actor `bootstrap`).

**Amended by `0016` section 8 ruling 4.** The mark becomes durable: the namespace annotation
`websites.webblock.internal/catalog-secret` (`pending` from `ensure_tenant`, `delivered` before the Secret is written)
replaces the in-process mark, so a restart no longer forgets it.

**Amendment.** The ruling above reads as follows after F2B; the rest of this section is unchanged.
- First bullet: "When the bootstrap process itself observed the tenant namespace as absent" becomes "When the namespace
  is marked `pending` (the bootstrap created it, possibly in an earlier process)", and `access_setup` treats the project
  as having no running consumer of the password only if the Secret is also absent.
- Third bullet: "In every other case" becomes "for a namespace not marked `pending`" (including one with no annotation,
  from before F2B, or marked `delivered` with no Secret): a Secret missing from it, while a live release exists, is
  still `runtime_secret_missing`.
- Fourth bullet (the mark lives only in the process) is replaced: the mark is the namespace annotation. `ensure_tenant`
  creates the namespace with `pending` in the same apply, before the worker's RoleBinding. `access_setup` sets
  `delivered` before it writes the Secret. "Namespace present, Secret absent, mark `pending`" counts as created from
  nothing, also after a restart of the bootstrap. A crash, failure or call timeout between the two writes, or an
  administrator deleting the Secret, leaves `delivered` with no Secret: the case of `0011` section 5,
  `runtime_secret_missing`, for an operator.
- `webblock-bootstrap-scope` lets the bootstrap set `pending` only on creation, and `delivered` only from `pending` or
  no mark, never back and never removed. `webblock-tenant-writer` refuses any other identity the policy matches
  (break-glass administrators and kube-system controllers are not matched).
- The rotation audit event is unchanged. The move of the mark to `delivered` is recorded in the `access_setup done` log
  line (`catalog_mark=pending->delivered`), not in the audit event: an audit field would need a database schema change,
  and the owner's revision-19 design is not edited (lead ruling).

Phase A of the Notion alignment: see [`0021`](0021-notion-alignment-phase-a.md).
