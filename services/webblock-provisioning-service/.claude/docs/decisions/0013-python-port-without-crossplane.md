# 0013: Port Provisioning to Python, without Crossplane

Status: **Current.** The design reviews of section 8 checked the rulings marked "(to confirm)". Section 9 holds the
owner's answers to them and wins over sections 3 to 5 where they differ. The Python package under `src/provisioning`
implements this record, and the Go reference is kept only on branch `go-reference`. Source: the team's proposal
docs/architecture/archive/architecture-change.md (removed; in git tag docs-before-cleanup) ("Proposed"), reviewed
against branch `phase-4` at `53cb0e2`.

Supersedes, for the Python service: `0002` (whole), `0009` sections 4 (the XR mechanism; the separate bootstrap identity
stays), 5, 6, 13 (the Crossplane findings) and the XR parts of 11; `0011` sections 1, 7 (the function's check; the
`ImageLocation` port stays), 9, and the Crossplane-specific bullets of 11 and 12; `0012` section 1, "database and stores
only" (section 6 below). Every other rule of `0001` to `0012` stays and applies to the Python code unchanged, in
particular fencing, leases, idempotency, the request fingerprint, per-project serialization, the step guards, the
database roles, the naming rule of `0009` section 3, Kind-only clusters and the real-cluster rule.

## 1. Owner's answers

1. **Scope.** This decision, updated guidance, and a phased port plan. Port code starts after the plan is approved (it
   was approved).
2. **Phase 4 in progress stops and is archived as the Go reference.** G4's two final Kind runs are not run: they prove
   Crossplane, which is removed. Tags `go-reference-phase-4` (`phase-4` head), `g4-acceptance-wip` (G4's branch,
   `be97855`) and `f2-guarded-delete-wip` (task F2, `9286a72`, never merged) keep the work. Phase 4 has no validation
   report and is not claimed complete.
3. **Same repository, branch `python` from `phase-4`.** The Python package lives under `src/provisioning/`; the Go code
   under `src/` stays readable as the reference until each part reaches parity and is validated, then it is deleted.
4. **Namespace bootstrap and Secret delivery stay with a separate identity**, never the release worker (REQUIREMENT
   section 4: bootstrap authority is separate from agent actions). The proposal's single ServiceAccount (its section 8)
   is not adopted.

Nothing has run in production: production binaries refuse every mutation and Crossplane has run only on the local Kind
cluster. The proposal's migration steps 1 to 5 and 8 (freeze, inventory, adopt live objects, feature-flag retirement by
plan tier) therefore do not apply; this is a rewrite inside the repository.

## 2. Findings on the proposal

| # | The proposal says | The repository says | Ruling |
| --- | --- | --- | --- |
| 1 | Reference `db-design-v3.md` | Revision 19 (`docs/new-platform-db-design.md`, adopted by `0012`) | Revision 19 is authoritative; `.claude/PLATFORM_DB_DESIGN.md` becomes revision 19 unchanged, as `0012` section 1 already decided. The team is asked whether "v3" names another document |
| 2 | "Migration `00009` proposals A to D" are pending | Revision 19 numbering. B, C and E are in our `00010`; A is `0011` section 3, implemented; D is a Phase 6 rule | Not pending |
| 3 | An "ingress controller", "Route or Ingress" | Cilium's Gateway API, one shared Gateway `webblock-public`, HTTPRoute (`0009` section 2, mirrors DOKS) | Gateway API stays. The wildcard certificate sits on the Gateway listener; purchased domains use cert-manager HTTP-01 through the `gatewayHTTPRoute` solver |
| 4 | Open question: a namespace per site? | `wb-<projectId>`, one per project (`0009` section 3) | Already decided; the naming rule stays |
| 5 | RBAC on "namespaces carrying the platform label" | Kubernetes RBAC cannot select by label | Label scope is enforced by ValidatingAdmissionPolicies, the pattern of `deploy/policy/` |
| 6 | One ServiceAccount creates namespaces and Secrets | Bootstrap authority is separate (REQUIREMENT section 4, `0009` section 4) | Owner answer 4 |
| 7 | Tests use a "fake Kubernetes client" | REQUIREMENT section 5: a fake is not enough for acceptance | Fakes only in unit tests; the acceptance suite runs on Kind with a real API server |
| 8 | "Rollback happens only when a deployment fails" | Rollback when a promotion fails, or operator recovery of an uncertain route | Current rule stays; a failed candidate never moves the route |
| 9 | Suspend = scale to zero and route to a suspended page | Offline page is OD-44; revision 16 keeps the image and data | OD-44 also asks whether suspend scales to zero (open-decisions) |
| 10 | `sync_domains` is assumed adopted | OD-13 and OD-36 open | Stays open; blocks only Phase 5 hostname and certificate work |
| 11 | Outbox in the same transaction as the business change | Command dispatch is the `command_work` row (OD-10); the usage outbox is OD-42 | Unchanged |
| 12 | A CDN in front of the ingress | HTTP-01 needs `/.well-known/acme-challenge/` to reach the Gateway | New OD-56 |
| 13 | Python workers "classify failure codes as retryable or not" | Already one list in the domain (`0011` section 3) | Ported as is |
| 14 | No DNS ownership check: the platform sells and controls every custom domain | REQUIREMENT section 4: "custom domains require verified ownership and valid TLS before public promotion" | Bring-your-own domain stays unsupported, so ownership is shown by Project's `project.domains` row in `active`; Provisioning still requires a valid certificate before promotion. Part of OD-36 |

## 3. What replaces Crossplane (to confirm)

| Crossplane piece | Replacement |
| --- | --- |
| `WebsiteTenant` XR | A **bootstrap component** with its own ServiceAccount and process (`src/provisioning/hosts/bootstrap`). It creates the Namespace with its immutable labels, ResourceQuota, LimitRange, default-deny CiliumNetworkPolicy, the worker's RoleBinding and the `catalog-runtime` Secret. Who runs it in production is OD-34's remainder |
| `WebsiteRelease` XR and function | The release worker applies Deployment and Service `rel-<releaseId>` by server-side apply: field manager `webblock-provisioning-worker`, `fieldValidation=Strict`, `force=false`; another writer surfaces as a conflict (PR-18). The pod template keeps the intent-digest annotation `websites.webblock.internal/intent-digest` (`0009` section 13) |
| `WebsiteRoute` XR and its `Usage` | The worker applies HTTPRoute `route-<projectId>` (and, in Phase 5, a cert-manager `Certificate` per purchased hostname). The routed release carries a label that an admission policy uses to refuse its deletion |
| XRD fence rule | Annotation `websites.webblock.internal/command-number` on every object the worker writes; an admission policy refuses a write that lowers it, or changes intent at the same value (CEL on `oldObject`) |
| The function's fatal refusals | Domain-side manifest validation before any write, plus an admission policy refusing a Deployment whose image is not under `<registry>/webblock/sites/<namespace project id>` (the `0011` section 7 rule) |
| Acknowledgement (`0011` section 1) | One read shows the Deployment at its current generation (`observedGeneration == generation`), the intent digest equal to the one recomputed from SQL, replicas updated, ready and available, and ready EndpointSlices for the Service; for a route, parent status `Accepted` and `ResolvedRefs` from our Gateway at the route's current generation; for a certificate, `Ready=True` at its current generation. The fence annotation equals the command's number |
| Crossplane's drift correction | A periodic reconciler: desired state from SQL, actual state by label, re-apply of missing or changed objects under the same field manager, orphans reported, never adopted silently (proposal section 5.5) |
| Guarded delete of a candidate | Kept, on the Deployment and Service: fenced, `resourceVersion` precondition, never the route target, and the F2 rule of `0011` section 12 (start again from the first check on 409, at most five passes) because the Deployment controller also writes status |
| Exception for Crossplane advisories (`0011` section 9) | Withdrawn: Crossplane is not installed |

PR-17 of the retired `REQUIREMENT.md` ("Deliver Crossplane definitions") becomes "deliver the per-site manifest
contract": the renderer, its golden manifests, the admission policies and scoped RBAC, with unknown or invalid intent
refused.

## 4. Python stack (to confirm by the stack review)

| Concern | Choice |
| --- | --- |
| Runtime, packaging | Python 3.14; `uv` with a committed `uv.lock`; `uv` pinned and checksum-verified into `./bin` by `make py-tools`; nothing system-wide |
| Layout | src-layout package `src/provisioning/`: `core/domain`, `core/application`, `infrastructure/*`, `presentation/{http,mcp,messaging}`, `workers`, `hosts/{api,worker,bootstrap}`. Layers as `0010`, enforced by `import-linter`. Unit tests live under `tests/unit/` mirroring the package, not in a `test/` subfolder of each package: the package must not ship its tests, and the separate-package rule of `0003` (tests reach only the public API) is kept by importing only public names |
| HTTP | FastAPI, Pydantic v2, uvicorn; request-body limit; `src/contracts/openapi.yaml` stays the contract and a test compares it with the served schema |
| MCP | The official `mcp` Python SDK, calling the same use cases as HTTP |
| Database | psycopg 3 (async) with `psycopg_pool`; handwritten parameterized SQL per store, ported from `src/infrastructure/postgres/queries`; explicit transactions; no ORM |
| Migrations | The SQL files and the pinned `goose` binary stay (no second migration tool) |
| Messaging | aio-pika with publisher confirms; `0005`, `0006` and `0008` unchanged |
| Kubernetes | `kubernetes_asyncio` with server-side apply; a spike in P0 proves SSA with `fieldValidation=Strict` and conflict surfacing, else `lightkube` |
| Identity | PyJWT with a bounded JWKS cache (port of `src/infrastructure/identity`) |
| Logs, traces | structlog JSON and OpenTelemetry; `project_id`, `release_id`, `command_id` on every span; the redaction rules stay |
| Gates | `ruff format --check`, `ruff check`, `mypy --strict`, `pytest` with markers `integration` (PostgreSQL 18), `messaging` (RabbitMQ), `acceptance` (Kind); `pytest-cov` 80% core, 70% overall; `pip-audit` on the lockfile against one verified offline copy of the advisory data (OD-57); `import-linter`; contract tests |

## 5. Phases

Each phase: implementer agents in their own worktrees on branches from `python`, the lead reruns the gates in MAIN, an
independent validator, a report in `ai/reviews/`. Three read-only design reviews run before any dispatch: Python stack
and supply chain; Kubernetes without Crossplane (security, RBAC and admission, acknowledgement); port architecture and
task split. Their owner questions go in one batch.

| Phase | Content |
| --- | --- |
| P0 | Skeleton: `pyproject.toml`, `uv.lock`, `py-*` Make targets, import-linter, configuration loader (every key required, an empty environment exits naming every missing key), telemetry, the Kubernetes client spike |
| P1 | Domain and application, ported with their behaviour tests from `src/core/**` |
| P2 | Migration `00011` (`0012`) and the psycopg stores, with the PostgreSQL 18 suites ported from `tests/integration` |
| P3 | HTTP, MCP, identity, RabbitMQ dispatch and consumption, inbox processor, build sweeper; hosts fail closed as today; contract and messaging fault suites |
| P4 | Kubernetes without Crossplane: `deploy/crossplane/` removed and Crossplane taken out of `deploy/kind/`; cert-manager in Kind; admission policies and RBAC rewritten for plain objects; bootstrap component, worker adapters, deploy executor, `access_setup`; Kind acceptance suite adapting G4's scenarios |
| Go removal | After P1 to P4 are validated: `go.mod`, Go sources and Go Make targets are deleted, guidance updated |
| P5 onward | Promotion, routes and certificates, reconciler, usage outbox: after OD-13, OD-36, OD-42, OD-44, OD-56 |

Until Go removal the Go gates keep passing on `python`. A parity map lists every Go test file of `src/core/*/test`,
`tests/integration`, `tests/messaging` and `tests/acceptance` with the Python test that replaces it or the recorded
reason it has none; the validator checks it.

## 6. Names

The Python code uses the revision 19 names of `0012` section 3 everywhere, from the domain to the stores (for example
`command_number`, `last_command_number`, `live_release_id`, `routing_status`, `hosting_status` with `online`/`offline`).
`0012`'s restriction to "database and stores only" existed to limit Go churn and does not apply to new code. The
external contracts keep their current field names until a contract revision says otherwise: OpenAPI, MCP tools and the
event schemas are other services' interface (OD-25); the Python transport layer maps between them. Migration `00011` is
still written as `0012` sections 2 to 5 describe.

## 7. Unchanged by this decision

The real-cluster rule (`.claude/CLAUDE.md` section 0); one Kind stack at a time, never alongside Testcontainers; no
command kind enabled in a production build until OD-01 and OD-04; the API never holds a cluster credential; PostgreSQL
is the authority and RabbitMQ only notifies; retention 1; the service boundaries of the proposal's section 2 (Project
owns domains and DNS; Provisioning holds no registrar or DNS credentials).

## 8. Design reviews before dispatch

Read-only, outputs under `.superpowers/sdd/python/research/`: Python stack and supply chain (dependency pins,
maintenance, licences, offline audit data); Kubernetes without Crossplane (the replacement table of section 3, RBAC and
admission, what an admission policy can and cannot check, acknowledgement); port architecture and task split (file
ownership per task, parity map). Rulings marked (to confirm) change only through a recorded amendment here.

## 9. Answers to the design reviews

Sources: `.superpowers/sdd/python/research/review-stack.md`, `review-port-arch.md`, `review-k8s-nocp.md` (git-ignored).
This section amends sections 3 to 5 where it says so; where they differ, this section wins.

**Stack (amends section 4).**
- S1: the HTTP client is `httpx2` (already pulled in by `mcp`); `httpx` is not used.
- S2: the runtime image uses pure `psycopg` with the base image's patched `libpq`; `psycopg[binary]` only for
  development and tests. psycopg is LGPL-3.0-only, used unmodified as a library.
- S3: `mcp` 2.3.0 behind a thin adapter in `presentation/mcp`, with explicit transport-security settings and a client
  conformance test (OD-28 stays open).
- S4: `kubernetes_asyncio`, **typed API only** (server-side apply through
  `_content_type="application/apply-patch+yaml"`, `field_manager`, `force=False`, `field_validation="Strict"`, and an
  explicit `_request_timeout` on every call). The P0 spike runs the same proof against the official `kubernetes.aio` and
  the choice is made on that evidence. `lightkube` is dropped: it cannot send `fieldValidation`.
- S5: `src/contracts/openapi.yaml` stays OpenAPI 3.0.3; the contract test compares normalised operations with what
  FastAPI serves (3.1). FastAPI's own `/docs`, `/redoc` and `/openapi.json` are off.
- S6: a dependency version is adopted only when it is at least 14 days old; a security fix may be adopted earlier with a
  recorded exception here.
- OD-57 decided: the Python vulnerability gate is osv-scanner (pinned, checksum-verified) offline on `uv.lock`, against
  one verified download per run of the OSV PyPI bulk export, with bounded retries, a freshness limit of 14 days, a
  self-test and a canary; it fails closed. `pip-audit` has no offline mode and is not the gate.
- `goose`, `uv` and `osv-scanner` are installed as pinned, checksum-verified binaries into `./bin` (no `go install`, so
  nothing needs a Go toolchain after Go removal).

**Real-cluster safety (critical, lead ruling).** The Kubernetes client loads `$KUBECONFIG` or `~/.kube/config` by
default and can write refreshed credentials back. One wrapper in `infrastructure/kubernetes` is the only place that
builds a client: in production from the in-cluster ServiceAccount only, in tests from an explicit project-local
kubeconfig path that must name a `kind-webblock-*` context on `https://127.0.0.1:<port>`, with `persist_config` off.
Lint bans every other loader (`load_kube_config`, `new_client_from_config`, `config.load_*`); a test with a poisoned
`$HOME/.kube/config` and `$KUBECONFIG` proves neither is read.

**Port architecture (amends section 5).**
- A1: before migration `00011` lands, a lead task pins the Go reference to `00010` (goose `UpTo(10)` in the Go
  harnesses, explicit migration files in the sqlc configurations). `openapi.yaml` keeps `serving` until Go removal; the
  Python transport maps `online` to `serving` on the wire, and the contract change to `online` comes with Go removal.
  This amends `0012` section 1's "everywhere" for the contract only.
- A2: P1 ports only the validated Phase 1R to 3 core. The Phase 4 parts of `src/core` (`intent.go`, `naming.go`,
  `candidate.go`, `deploy_ports.go`, `deploy_usecase.go` and their tests) are redesigned in P4. The job port types that
  Go keeps in `src/workers` (work reference, lease, acceptance hook, lease-lost error, job store) move to
  `core/application`.
- A3: amends `0006` section 7. The gateway enforces slow-client limits (header and body read time, write time); the
  service keeps its body-size limit, global in-flight cap and per-request timeout in middleware, because uvicorn has no
  read or write timeouts. FastAPI's defaults that break the contract are switched off: validation errors use our error
  shape and never echo input, no trailing-slash redirects. *(Noted by decision
  [`0019`](0019-service-identity-is-the-mesh.md) section 5: no gateway sits in front of `/v1/*` any more; per-user
  limits are Cloudflare's and the Next.js server's, and the service keeps its global in-flight cap. Lead's ruling, which
  the owner was told about and did not object to: `/v1/*` has one caller, the trusted Next.js server, so slow-client
  protection sits at the edge of `app.<domain>`, in Cloudflare and the Istio ingress gateway's timeouts; the service
  keeps its application-level timeouts and body limits.)*
- A4: the production worker host imports no Kubernetes client until a recorded decision enables a command kind (the rule
  of `0011` section 8, carried over); the acceptance suite has its own test composition root. A contract test pins it.

**Kubernetes (amends section 3).**
- Bootstrap scope (critical, lead ruling): a production admission policy `webblock-bootstrap-scope` limits the bootstrap
  identity to tenant namespaces, the fixed object names of `0009` section 3, Secrets of `type: Opaque` only, and
  RoleBindings only to the ClusterRole `webblock-tenant-worker`. Without it the identity could mint a ServiceAccount
  token Secret or bind any ClusterRole.
- K1: the bootstrap component runs `access_setup` (the catalog role and the `catalog-runtime` Secret, under the
  credential rules of `0011` section 5); a deploy only requires the tenant to be ready (`tenant_not_ready` is
  transient). Amends `0011` section 5 and `0009` section 8. The worker never holds the bootstrap credential, in tests
  too.
- K2: an admission policy refuses spec changes to per-site objects by anyone but the worker, and `deployments/scale` by
  anyone. The reconciler re-creates deleted objects of live projects and reports conflicts; it never forces. The
  proposal's "a manual change is overwritten on the next apply" does not hold with `force=false` and is not relied on.
- K3: a purchased domain gets a per-project ListenerSet plus a cert-manager `Certificate` in the tenant namespace,
  provided DOKS runs Cilium 1.20 or later with Gateway API 1.5 or later (K5). Until those versions are confirmed this
  stays conditional; listeners on the shared Gateway are not used (64-listener cap, second writer of a platform object).
- K4: purchased domains are validated by DNS-01 through the platform's cert-manager with a DNS credential held by
  infrastructure, never by Provisioning (Project still owns the domain records). No solver pods in tenant namespaces; it
  works behind a CDN, so OD-56 is closed.
- K5: the owner reads from DOKS, before the P4 policies are final: the Cilium version, the Gateway API CRDs'
  `bundle-version` and whether `listenersets` exists, whether kube-controller-manager uses per-controller
  ServiceAccounts, and the API priority-and-fairness configuration. Nobody else reads the real cluster.
- K6: Phase 5: bootstrap mirrors Project's active hostnames into a namespace annotation that a route policy checks. An
  HTTPRoute without hostnames is refused now (it would capture every hostname of the listener).
- K7: the reconciler lists namespaces cluster-wide (label-selected) and reads per-site objects per namespace for live
  projects; no cluster-wide list or watch of Deployments, Services or HTTPRoutes.
- K8: the bootstrap component polls SQL through a new read-only database role `provisioning_bootstrap`, starting from
  release acceptance. The role needs a migration in P4 and an entry in `README.md` and `scripts/db/bootstrap-roles.sql`.
- K9: the worker never deletes `route-<projectId>`. After a failed first publish it writes a tombstone backend (empty or
  offline, with OD-44), because a delete and re-create would reset the fence annotation.
- The fence annotation is a fence only together with K2 and K9: admission sees no old object on CREATE. Acknowledgement
  also requires the old replicas to be gone (as `kubectl rollout status` checks), and `progressDeadlineSeconds` is set
  below the readiness deadline. The guarded delete keeps the F2 retry, because Deployment status writes change
  `resourceVersion`.

## 10. Lead rulings for phase P0

Points the documents and reviews leave open, settled so that P0 can be dispatched. Each can change through an amendment
here.

1. **Task split.** P0 is four tasks, as `review-port-arch.md` section 7 has it. P0.1 (skeleton) goes first. P0.2
   (configuration, telemetry, minimal hosts), P0.3 (parity inventory and checker, plus the Python vulnerability gate)
   and P0.4 (the Kubernetes client spike) follow in parallel once P0.1 is merged. T2.0 (A1) is a lead task and runs
   alongside P0.1. P2.1 (`00011` and its schema tests) may start once P0.1 and T2.0 are merged.
2. **Make targets.** The targets live in `mk/python.mk`, which the Makefile pulls in with one `include` line. They are
   `py-tools`, `py-sync`, `py-fmt-check`, `py-lint` (ruff check), `py-typecheck` (mypy), `py-layers` (import-linter),
   `py-test` (unit and contract tests), `py-cover`, `py-vuln` (osv-scanner, OD-57) and `py-parity`. Later phases add
   `py-test-integration`, `py-test-messaging` and `py-test-acceptance`. Every target runs `uv sync --locked` (or
   `uv lock --check`) first, and none reads `.env` unless its Go counterpart does.
3. **Tool binaries.** `scripts/py/tools.sh` installs `uv`, `osv-scanner` and `goose` into `./bin` as pinned release
   binaries, each checked against a pinned SHA-256 and against the checksum the project publishes, in the manner of
   `scripts/kind-tools.sh`. Go's `make tools` installs goose through the same script, so there is one goose (v3.28.0) at
   one path. It still installs golangci-lint, govulncheck and sqlc with `go install` until Go removal.
4. **Interpreter and environment.** The system Python 3.14 is used (`UV_PYTHON_DOWNLOADS=never`,
   `UV_PYTHON_PREFERENCE=only-system`), and a target fails if the minor version is not 3.14. The virtual environment is
   `.venv/` at the module root, which is git-ignored. It is not under `bin/`, because agent worktrees link `bin/` to
   MAIN's and must not share one environment. The uv cache is `bin/uv-cache`, and `UV_NO_CONFIG=1` is set. That setting
   also makes uv ignore `[tool.uv]` in `pyproject.toml` (found by P0.1 with uv 0.12.18), so `mk/python.mk` passes
   `UV_NO_BUILD=1` and `UV_EXCLUDE_NEWER`, the latter read from `pyproject.toml`. Nothing is written under `$HOME`.
5. **Version age (S6).** `[tool.uv] exclude-newer` is an absolute timestamp at least 14 days before the change that sets
   it. For P0 it is `2026-09-24T00:00:00Z`. Tool binaries follow the same rule: the newest release published on or
   before that date. Moving the timestamp is a lock change that the lead reviews, and it is recorded in the merge
   report.
6. **What the lock holds.** P0's `uv.lock` holds the development tools and the runtime dependencies that P0 imports
   (`structlog` for telemetry). Each later task adds only the dependencies it imports, with names checked against the
   stack review's list (N1). OpenTelemetry tracing arrives with the first spans in P3; the Go telemetry package being
   ported has none.
7. **Where the Kubernetes spike lives.** P0.4 lives in `ai/spikes/k8s-ssa/` as a separate uv project with its own lock
   and environment, outside the service lock. It proves the server-side-apply request shape, `fieldValidation=Strict`
   (400 on an unknown field), conflict surfacing with `force=false` (409), and the poisoned-kubeconfig test, against
   both `kubernetes_asyncio` and `kubernetes.aio`, on a bare Kind cluster. The production wrapper
   `infrastructure/kubernetes/_client.py` is written in P4 from that evidence. P0.1 already puts the banned loaders into
   ruff's `banned-api`.
8. **Hosts in P0.** P0.2 writes `hosts/api/__main__.py` and `hosts/worker/__main__.py`. Each loads its configuration
   and, when keys are missing, exits 2 naming every one of them. With a complete configuration it exits non-zero with
   the explicit error "not implemented before phase P3". There is no placeholder success path. P3.5 replaces both.
9. **Test layout.** Tests are under `tests/unit/` (mirroring the package), `tests/contracts/`, and later
   `tests/integration/`, `tests/messaging/` and `tests/acceptance/`. No `__init__.py` goes under `tests/`. pytest runs
   with `--import-mode=importlib` and `--strict-markers`, and markers are applied by directory in `tests/conftest.py`. A
   skip in the integration, messaging or acceptance suites is a failure.
10. **Coverage in P0.** `py-cover` implements both rules of `0013` section 4 (M7). A core package with no statements yet
    is reported as empty, never as passed. A core package that has statements but no mirror test folder fails.
