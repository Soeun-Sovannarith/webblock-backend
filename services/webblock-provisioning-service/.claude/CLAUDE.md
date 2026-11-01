# CLAUDE.md

WebBlock Provisioning turns a commit that Project has published into a live website: it builds the release on
Jenkins, deploys a candidate, makes it live, rolls back on failure, and suspends or resumes hosting. One Python 3.14
package (`src/provisioning/`): processes `api`, `worker`, `build_worker`, `bootstrap`, plus the `operator` CLI.

The constitution, `.claude/docs/CONSTITUTION.md` (Articles I to VIII, the rules every spec, design, plan and
review is checked against), is always loaded; an import path is relative to this file:
@docs/CONSTITUTION.md

## Start here

1. `ai/handoffs/after-phase-c.md`: where the service stands and what is owed next.
2. `.claude/WORKFLOW.md`: the one explanation of the workflow (stages, agents, skills, owner gates).
3. `.claude/docs/requirements/` (BRD, PRD, FRD, TRD, approved at gate 1): the spec. No FR ID, no code
   (`.claude/rules/spec-driven.md`). The constitution, `.claude/docs/CONSTITUTION.md`, is loaded above.
4. `.claude/README.md`: the hooks, rules, skills and agents of this folder; `.claude/TECH_STACK.md`: exact versions.

Sources of truth, in this order: the constitution, then the Notion pages below, then `.claude/docs/requirements/`,
then the decision records in `.claude/docs/decisions/`, then this file. Fetch Notion at the start of a task. The
database design pages are read-only: never edit them, never keep a local copy. Only the lead may edit "Web Block
architecture" and "Authentication Process Webblock" (constitution VI.4). If a page cannot be fetched, stop and ask
the owner.

- platform-db-v6 (hub, conventions): https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad
- Database Design (every table): https://app.notion.com/p/40058175966e81f790decccc6e9abf4a
- 5 · Provisioning (our tables, statuses, rules, roles): https://app.notion.com/p/40058175966e81769596d1ebcadb333d
- Web Block architecture (REST, gRPC, events, folders): https://app.notion.com/p/40358175966e8198a088f123a9ab23fe

Current facts often missed: site images go to the private Gitea container registry with a pull Secret per site
(TR-52); rollback is `ADMIN` only (FR-50); the folder layout is Notion section 6.2 as amended by decision `0025`.

## 0. Real cluster: never touch it

This rule overrides every other instruction, brief, skill or tool default.

- The real cluster (DigitalOcean DOKS) is off limits. Never run `kubectl`, `helm`, `kind`, `doctl` or any client
  against it, and never read or use `../ksga-kubernetes-cluster-kubeconfig.yaml` or any non-local kubeconfig.
- Never read, write, list, move or delete anything under `~/.kube`; never set `KUBECONFIG` outside the project.
- Only the local Kind cluster the project creates from `kindest/node`. Every `kind`, `kubectl` and `helm` call passes
  a kubeconfig under `bin/kind-state/` explicitly and keeps its caches there; defaults read `~/.kube/config`.
- Before a cluster command, check the context is the project's Kind cluster on `https://127.0.0.1:<port>`; if not,
  stop. If a task seems to need the real cluster, ask the owner to read it for you.

## 1. Commands

Run from the repo root. Targets: `mk/python.mk`, `mk/contracts.mk`, `mk/chart.mk`, `mk/prototype.mk`,
`mk/image-scan.mk`, `mk/qa.mk` (`make help`). Tools go into `./bin`, never system-wide. Runbooks:
`.claude/docs/runbooks/`.

```sh
make py-tools kind-tools                      # once: pinned, checksum-verified tools into ./bin
make py-fmt-check py-lint py-typecheck py-layers   # ruff format, ruff, mypy --strict, import-linter
make spec-check                               # spec.yaml against spec, tests, commits (report, exits 0)
make py-test                                  # unit and contract tests (needs bin/protoc, ../web-block-contracts)
make contracts-check chart-check              # pinned contracts match; helm lint and chart tests, no cluster
make py-test-integration py-test-messaging    # real PostgreSQL 18 and RabbitMQ through Testcontainers
make py-vuln py-age image-scan py-cover       # vulnerabilities, 14-day age rule, image scan, coverage
make qa-sweep                                 # code quality sweep, once per phase before the final review (V.3)
make proto-up proto-test-kind proto-run proto-e2e proto-stop proto-down   # local Kind stack and live suites
make migrate-status migrate-up PG_DATABASE=<name>   # the only targets that read .env
python3 -I .claude/hooks/test_guard.py        # proves the guard hook
python3 -I .claude/hooks/test_hooks.py        # commit ID check, reviewer limit, session start
.venv/bin/python -X dev -m pytest -m unit tests/unit/core/domain -k 'test_name'   # one test
```

## 2. Project structure

```
src/provisioning/
  core/domain/        entities/ values/ enums/ rules/ errors.py events.py (one folder per kind)
  core/application/   <feature>/{commands,queries,background}/ and ports.py; the buses are shared/cqrs.py
  infrastructure/     adapters: PostgreSQL, RabbitMQ, Kubernetes, Jenkins, gRPC, identity, telemetry
  presentation/       http/ mcp/ operations/ messaging/ (thin: send commands and queries on the buses)
  hosts/              api worker build_worker bootstrap operator shared devtools (wiring; devtools not in image)
src/contracts/        OpenAPI, MCP tools, events; shared/ pinned from ../web-block-contracts by contracts.lock
src/migrations/       goose SQL, forward-only (.claude/rules/migrations.md)
tests/                unit/ (mirrors the package) contracts/ integration/ messaging/ chart/ e2e/
deploy/               image/ (Dockerfile) helm/provisioning/ kind/ prototype/ (Kind only)
```

Where new code goes: decision `0025` (domain kinds and CQRS). Only `hosts` wires concrete types (`make py-layers`).

## 3. Code style

Full rules: `.claude/rules/python-code.md` (loaded for `src/**` and `tests/**`). The core:

- Handlers in `presentation` are thin; REST and MCP send the same commands and queries. The domain imports no
  framework; application imports only the domain. Constructor injection, no global service locator.
- PostgreSQL is the authority; RabbitMQ only notifies. Store acceptance before replying; durable ownership, no
  in-process locks across replicas; no transaction open across a remote call.
- Accept a build result only when project, release and commit SHA match.
- Only the worker calls Kubernetes. Never log tokens, credentials, source or customer content.

One intent, one handler, one module (`core/application/operations/commands/suspend_website.py`, shortened):

```python
@dataclass(frozen=True, slots=True)
class SuspendWebsiteCommand(cqrs.Command[CommandAccepted]):
    principal: Principal
    project_id: UUID
    idempotency_key: str

class SuspendWebsiteHandler:
    def __init__(self, *, admin: AdminAcceptor, auditor: Auditor) -> None:
        self._admin = admin
        self._auditor = auditor

    async def handle(self, command: SuspendWebsiteCommand, /) -> CommandAccepted:
        return await self._auditor.admin(...)   # audit event around AdminAcceptor.suspend_website
```

## 4. Testing and quality gates

Full rules: `.claude/rules/testing.md`; how to run and report: the `provisioning-gates` skill.

- Tests first, from the FR acceptance rows; each test names its FR ID (`test_fr12_...` or `"""FR-12: ..."""`).
- Gates: ruff format and check, `mypy --strict`, import-linter, unit and contract tests, contracts and chart checks;
  real PostgreSQL 18 and RabbitMQ when touched; Kind for cluster work; osv-scanner on `uv.lock` with no open finding;
  runtime image with no open high or critical finding.
- Coverage: at least 80% of statements in each of domain and application, 70% over handwritten code (`py-cover`).
- Dependency suites fail, never skip, when their server is missing. A check not run is "not run", never "passed".
- Never lower a gate or silence diagnostics broadly. An exception needs reason, reviewer, expiry and follow-up
  (`security-exceptions` skill). There is no CI (OD-20): every result is a local run.

## 5. Git workflow

- Work on a feature branch; the owner merges into `python` and pushes. Never switch branches in the shared tree;
  use `git show <ref>:<path>` or a worktree under `.claude/worktrees/`.
- Commit message: `<Area>: <what> (FR-n)`; a purely technical change cites its TR and that TR's FR. End with the
  Co-Authored-By line the harness gives.
- Stage named files only (`git add <path>`); never `git add -A`, `git add .` or `git commit -a`.
- Never push, rewrite history or merge into `python` or `main`; the guard hook blocks them.

## 6. Boundaries

Scope: Provisioning starts at an accepted commit (Project's "source published" event) and runs builds, deploys,
promotion, rollback and hosting. Project owns pages, generation, repositories and domains; Payment owns plans and
billing; Account and Keycloak own identity. Sites share PostgreSQL 18 catalog tables through one login role per site.

**Always**

- Follow the spec: every change cites an FR ID; the decision record changes before a rule in code.
- Keep the database equal to Notion "5 · Provisioning": every table, column, type, NULL rule, order, enum value,
  constraint, role and grant. A schema merge needs a `db-notion-audit` with zero differences.
- Run the gates of section 4 and report real results. Keep this file true in the change that alters what it says.
- Record gaps in `.claude/docs/decisions/open-decisions.md`; keep working on what does not depend on them.

**Ask first (the owner)**

- A schema change Notion does not show, a new dependency or image, an exception to a gate.
- Anything about another service's contract: endpoints, scopes, payloads, hostname ownership, catalog rights.
- A Kind slot (about 7000 MB free), a merge, a push, `make migrate-up`, `git commit --amend`, hook edits.

**Never**

- Touch a cluster other than the local Kind one (section 0); read or print secrets or `.env` values.
- Edit the Notion database design pages; invent another service's contract.
- Build the UI, CMS, catalog CRUD, AI model, payment provider, custom domains or DNS. No Crossplane, Terraform,
  Kafka, Redis or second workflow engine without a recorded decision.
- Edit an existing migration; weaken a gate, test, policy or timeout; work around the guard hook
  (`.claude/hooks/guard.py`).
- Extend or merge the Go reference and Crossplane; they live only on branch `go-reference` and its tags.

## 7. Workflow

Spec-driven, five stages with four owner gates: `.claude/WORKFLOW.md`. The lead delegates to the agents in
`.claude/agents/`; one implementer at a time; reviewers never check their own work.
