# WebBlock Provisioning service

Provisioning turns a commit that Project has published into a running website. It accepts the release, has it
built on Jenkins, deploys it as a candidate, promotes it after probing it, rolls back when a promotion fails, and
suspends or resumes hosting on Payment's events. It applies plain Kubernetes objects by server-side apply (no
Crossplane, decision `0013`). It does not capture page input, generate source, own repositories, or handle billing:
Project, AI Codegen and Payment own those.

The service is one Python package, `src/provisioning/`, with four processes (decision `0014`):

- `api`: REST (FastAPI) and MCP over the same use cases. It holds no cluster credential.
- `worker`: runs commands (deploy, promote, rollback, suspend, resume) by server-side apply, and turns Payment
  events into hosting commands.
- `build_worker`: consumes Project's "source published" events, starts builds on Jenkins and polls them. It holds
  no cluster credential.
- `bootstrap`: creates each tenant namespace, its guard rails and the site's catalog login, under its own identity.

PostgreSQL 18 is the authority for every accepted operation; RabbitMQ only notifies. The Go reference is not on
this branch: it lives on branch `go-reference` and the tags `go-reference-phase-4`, `g4-acceptance-wip` and
`f2-guarded-delete-wip`. `.claude/docs/architecture/` describes the architecture;
`.claude/docs/runbooks/prototype-demo.md` runs the prototype.

**Docs start here: [`.claude/docs/README.md`](.claude/docs/README.md)**, one page that links every document by need.

To try the REST API and MCP tools by hand on the prototype stack, see the team testing guide,
`.claude/docs/guides/team-testing.md` (with a Bruno collection).

## Contract for generated sites

What a generated website must provide so that Provisioning can build, run and promote it. AI Codegen generates the
sites for Project, so this is written for those two teams. Status: Provisioning's requirement, from decision `0014`
section 15 and the per-site manifest renderer; to be confirmed with AI Codegen and Project (OD-64). A site that
breaks it fails its deploy or is not promoted, and the live site keeps serving.

### Endpoints the platform calls

| Endpoint | Called by | Must answer |
| --- | --- | --- |
| `GET /healthz` | Kubernetes (process checks) | `200` whenever the process is up, **without touching any dependency**. Fast and cheap. Kubernetes' readiness probe is `GET /healthz` over HTTP on port 8080 (decision `0014` section 21 ruling 12; the release intent digest, version 4, covers the probe). |
| `GET /readyz` | Provisioning's promotion probe, through the Gateway with the site's own hostname, before and after traffic moves to a new release | `200` **only** when the site's dependencies work, including one catalog query as the site's own database role, within a short timeout; `503` otherwise. No redirect, no authentication, a small body (it is not used, and one above 64 KiB fails the probe: `SITE_PROBE_MAX_BODY_BYTES`), and an answer within a few seconds (the probe timeout is configuration). |

- Both paths answer without authentication and without redirects. Never return `200` from `/readyz` while the
  catalog cannot be read: that is how a broken release is kept from going live.
- Do not set the response header `X-Webblock-Release`. The Gateway sets it to name the release that answered, and
  overwrites anything the site sends.
- Do not depend on a particular hostname. A site is served on its platform subdomain and on its purchased domains;
  redirects from secondary hostnames to the primary are done by the Gateway, not by the site.

### Repository and image

- **One Next.js application per site** (decision `0014` section 22): public pages, the CMS and the APIs in one
  codebase and one image. The backend part is the same for every site of a business type and should come from the
  platform's versioned template, not be generated per site, so the web portal and the mobile app see one API.
- A `Dockerfile` at the repository root. The build context is the repository root, the platform is `linux/amd64`,
  and exactly the published commit is built by the platform's Jenkins pipeline. Nothing else from the repository
  runs during the build.
- **The build has no database and no secrets.** Never read the catalog during the build: no static prerendering of
  data pages (in Next.js, render catalog pages at request time, for example `export const dynamic =
  "force-dynamic"`). A build that needs the catalog fails.
- The image goes to the platform's Gitea container registry as `<registry>/webblock/sites/<projectId>` (TRD TR-46).
  It is private, pulled with a pull Secret per site (TR-52, next phase), and still must contain no secret, key,
  token or customer content. Every runtime value arrives through environment variables.

### Runtime

| Rule | Value |
| --- | --- |
| Listen | HTTP on `0.0.0.0:8080` |
| User | non-root, uid and gid `65532` |
| Filesystem | read-only root; only `/tmp` is writable (64 MiB) |
| Privileges | no privilege escalation, all Linux capabilities dropped, no Kubernetes service-account token, no service-link variables |
| Resources | CPU and memory are fixed per site by the platform (requests equal limits); the site must start and stay within them |
| Catalog | connect directly as the site's own role with the standard variables `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` (from the Secret `catalog-runtime`). Row-level security limits the site to its own rows |

Reference implementation: the demo shop in `deploy/prototype/demo-sites/clothing-shop/` (`src/app/healthz`,
`src/app/readyz`, `src/lib/readiness.ts`, `Dockerfile`).

## Where things are

| What | Where |
| --- | --- |
| Behaviour and acceptance criteria | `.claude/docs/requirements/` (BRD, PRD, FRD, TRD) |
| Architecture | `.claude/docs/architecture/` |
| Database design and API contract | The owner's Notion pages, linked at the top of `.claude/CLAUDE.md` (no local copy) |
| Tools and versions | `.claude/TECH_STACK.md` |
| Rules for contributors and agents | `.claude/CLAUDE.md` |
| Index of all docs | `.claude/docs/README.md` |
| Decisions the code implements | `.claude/docs/decisions/` (index: `README.md`; not decided: `open-decisions.md`) |
| Runbooks | `.claude/docs/runbooks/` (`phase-1-database.md`: roles, migrations, database tests; `api-testing-with-swagger.md`: the REST API by hand in Swagger UI at `/docs`, prototype profile only) |
| Migrations | `src/migrations/` (forward-only, run as `provisioning_migrator`) |
| Validation reports and handoffs | `ai/README.md` (current handoffs) |

## Folder map

The layout is Notion "Web Block architecture" section 6.2 as amended by decision
[`0025`](.claude/docs/decisions/0025-domain-kinds-and-cqrs.md). All service code is the package `src/provisioning/`.

| Folder | Layer | Holds |
| --- | --- | --- |
| `src/provisioning/hosts/{api,worker,build_worker,bootstrap,operator}` | Composition roots | The four processes and the operator's command-line tool, and the wiring of each |
| `src/provisioning/hosts/shared` | Composition roots | Start-up code several processes share: settings, engines, broker wiring, stand-ins |
| `src/provisioning/hosts/devtools` | Development only | The event simulator and the catalog platform stand-in; never imported, not in the image |
| `src/provisioning/core/domain` | Domain | One folder per kind: `entities`, `values`, `enums`, `rules`, plus `errors.py` and `events.py`; imports nothing of the package |
| `src/provisioning/core/application` | Application | CQRS by feature (`operations`, `jobs`, `intake`, `builds`, `deploys`, `routing`, `hosting`, `reconcile`, `usage`, `tenants`): `commands/` and `queries/` hold each intent with its handler, `background/` the jobs, loops and relays, `ports.py` the store protocols; `shared` (with the buses, `cqrs.py`) and `ports` serve every feature; imports only the domain |
| `src/provisioning/infrastructure/postgres` | Infrastructure: persistence | PostgreSQL stores |
| `src/provisioning/infrastructure/rabbitmq` | Infrastructure: messaging | The broker adapter |
| `src/provisioning/infrastructure/kubernetes` | Infrastructure: cluster | The worker's site adapter, the bootstrap adapter, the per-site manifest renderer |
| `src/provisioning/infrastructure/jenkins`, `catalog` | Infrastructure | The build system and the shared catalog's per-site logins |
| `src/provisioning/infrastructure/project`, `payment`, `grpc` | Infrastructure | Project's access check (HTTP or gRPC, `PROJECT_ACCESS_MODE`) and `GetRepository`; Payment's `GetEntitlement`; the shared gRPC call code |
| `src/provisioning/infrastructure/identity`, `standins` | Infrastructure | Token verification; the prototype's named stand-ins for the open Project and Payment contracts |
| `src/provisioning/infrastructure/config`, `telemetry` | Infrastructure: cross-cutting | Environment configuration, logging and its allow-list |
| `src/provisioning/presentation/{http,mcp,operations,messaging}` | Presentation | REST, MCP, the shared operation layer, message formats; `messaging/consumers` holds the RabbitMQ listeners |
| `src/contracts` | Published contracts | OpenAPI, MCP tool list, event schemas and their examples; `shared/` pinned from `web-block-contracts` |
| `src/migrations` | Database schema | goose migrations |

Dependencies point inward: `infrastructure` and `presentation` depend on `core`, and only `hosts` wires concrete
types. `import-linter` (`make py-layers`, contracts in `pyproject.toml`) enforces this and which host may hold which
cluster identity. Unit tests live under `tests/unit/`, mirroring the package.

Supporting folders at the top level, not service code:

| Folder | Holds |
| --- | --- |
| `tests/` | Unit, contract, integration (real PostgreSQL 18), messaging (real RabbitMQ), chart, live Kind and end-to-end tests |
| `deploy/` | `image/` (the service Dockerfile), `helm/provisioning/` (the chart), and the prototype's Kind stack (`kind/`, `prototype/`: Cilium, Gateway, cert-manager, RBAC, admission and network policies, Jenkins, catalog, Loki) |
| `scripts/` | Tool installers, database role bootstrap, the prototype's helpers |
| `.claude/` | Agent rules, the constitution, the stack, and `docs/` (requirements, architecture, decisions, runbooks, guides) |
| `ai/` | Validation reports and handoffs written by the agents |
| `Makefile`, `mk/`, `pyproject.toml`, `uv.lock` | Targets, the Python project and its locked dependencies |

## Gates

Run from the repository root. `make py-tools` installs the pinned tools into `./bin`.

```sh
make py-fmt-check py-lint py-typecheck py-layers py-test   # static checks and unit tests
make py-test-integration                                   # real PostgreSQL 18 (Testcontainers)
make py-test-messaging                                     # real RabbitMQ
make proto-up proto-smoke proto-run proto-e2e proto-demo   # the prototype on Kind, end to end (owner's slot)
make proto-test-kind                                       # live Kubernetes suite (owner's slot)
```

`.claude/CLAUDE.md` lists every gate and what it requires.

## Database roles

- `scripts/db/bootstrap-roles.sql` creates the cluster-wide `NOLOGIN` group roles. An environment attaches its own
  login roles to them by membership: one login per process, a member of exactly one group role.
- Every grant and guard comes from the migrations in `src/migrations/`; `tests/integration/` checks them.
- The design is Notion "5 · Provisioning" (read-only, linked from `.claude/CLAUDE.md`). How to run the migrations and
  the database tests: `.claude/docs/runbooks/phase-1-database.md`.
