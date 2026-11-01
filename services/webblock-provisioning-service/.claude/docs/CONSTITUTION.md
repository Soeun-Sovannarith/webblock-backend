# Provisioning constitution

Version 2

The rules that bind every spec, design, plan, task and review of the Provisioning service. Other files give the
details; this file wins over them, and only `.claude/CLAUDE.md` section 0 (the real cluster) ranks with it. Cite a
rule by its number (`III.3`) and a whole article by its numeral (`Article III`). Reports written before this file
cite `P-n`: P-1 is I.2, P-2 is III.3, P-3 is III.4, P-4 is III.7, P-5 is III.8, P-6 is III.6, P-7 is VI.1, P-8 is
V.1, P-9 is VIII.1, P-10 is VII.1.

## Article I. Spec-driven flow

- **I.1 Stages and gates.** Every change runs Specify, Design, Plan, Implement, Verify. The owner approves the spec
  (gate 1), the design (gate 2), the plan (gate 3) and the merge (gate 4). A docs fix or a bug fix inside an
  existing FR that sets no new rule takes the small change lane and still gets one independent review.
  Details: `.claude/WORKFLOW.md`, `.claude/rules/spec-driven.md`.
- **I.2 No FR, no code.** The spec changes first and the code follows it. Every change cites an FR ID (a technical
  change cites its TR and that TR's FR). A change with no ID goes back to stage 1.
- **I.3 Drift stops the work.** Code that must differ from the spec stops, and the implementer reports Drift with
  both sides. Nobody edits the spec to match the code without the owner's decision; then the spec changes first.
- **I.4 Two fix rounds.** A task gets at most 2 fix rounds after its review. Findings still open after round 2 go
  to the owner.
- **I.5 Constitution check.** Every FRD, TRD, change design and plan has a "Constitution check" section: each of
  Articles I to VIII marked pass, not applicable, or conflict, with one line of reason. A conflict stops the work
  until the owner rules at the next gate.

## Article II. Tech stack

- **II.1 The fixed stack.** Python 3.14 in one package; FastAPI, Pydantic v2 and uvicorn for REST; the MCP SDK for
  MCP; SQLAlchemy Core on psycopg 3 against PostgreSQL 18 (the shared CloudNativePG cluster); goose SQL
  migrations; RabbitMQ through aio-pika; gRPC clients from `web-block-contracts`; the shared auth library
  `web-block-commons` (Python package, from a tagged release); kubernetes_asyncio; httpx2; PyJWT; structlog,
  OpenTelemetry, prometheus-client and Sentry; Jenkins builds into the Gitea registry; a Helm chart on Kubernetes
  with Istio. Tools: uv, ruff, mypy, import-linter, pytest, Testcontainers.
- **II.2 Versions.** Exact versions live only in `.claude/TECH_STACK.md` and the files it names. Every dependency
  is pinned exactly, at least 14 days old and open source; images are pinned by digest (TR-9). First-party
  WebBlock packages (`web-block-commons`) are exempt from the age and open-source rules: each is pinned to an exact
  released version built from a reviewed, annotated tag on that repository's `main` whose own gates passed (tests,
  OSV scan, age check of its third-party pins), installed with its hash, and fetched with a read credential that
  never enters `uv.lock`, an image layer or a build argument. Its own dependencies keep every rule of this article.
- **II.3 Changing the stack.** Adding, removing or replacing a library, image, tool, data store or engine needs an
  amendment of this constitution. No Crossplane, Terraform, Kafka, Redis or second workflow engine.

## Article III. Architecture limits

- **III.1 Domain kinds and CQRS layers.** The folder layout is Notion "Web Block architecture" section 6.2 as
  amended by decision `0025`: `core/domain` (one folder per kind: entities, values, enums, rules, errors, events),
  `core/application` (`<feature>/commands`, `queries`, `background`, `ports.py`, the buses in `shared/cqrs.py`),
  `infrastructure`, `presentation` (thin) and `hosts`. One intent, one handler, one module.
- **III.2 Import contracts.** The domain imports no framework and nothing of ours outside it; application imports
  only the domain; only `hosts` wires concrete types. The import-linter contracts (`make py-layers`) are only
  added or tightened, never loosened.
- **III.3 The database matches Notion 100%.** Every table, column, type, NULL rule, order, enum value, constraint,
  role and grant equals Notion "5 · Provisioning": no extra index, column, grant or failure code. The Notion
  database pages are read-only. A schema merge needs a `db-notion-audit` with zero differences.
- **III.4 PostgreSQL is the authority.** Acceptance is stored before the answer; RabbitMQ only notifies and a lost
  message is rebuilt from SQL. One command in flight per project, owned durably. No transaction stays open across
  a remote call.
- **III.5 Service boundaries.** Provisioning starts at Project's "source published" event and runs builds, deploys,
  go-live, rollback and hosting. Project owns pages, generation, repositories and domains; Payment owns plans and
  billing; Account and Keycloak own identity. Another service's contract is agreed with its owner, never invented.
- **III.6 Least privilege in the cluster.** Only the worker changes site objects; only the bootstrap creates
  namespaces, RoleBindings and Secrets; the API holds no cluster token.
- **III.7 The auth model, no token forwarding.** The gateway and then the first service check each token. Inner
  calls carry only `x-user-id` (with `x-request-id` and `traceparent`) over mTLS, never the token. Tokens never
  appear in logs, traces, errors or outgoing calls.
- **III.8 Not the owner is 404.** A caller who may not see a project gets 404, never a hint that it exists.
  Rollback and admin actions need `ADMIN`.

## Article IV. Coding conventions

- **IV.1 The code rules.** `.claude/rules/python-code.md` is the full set (layers, CQRS names, async, typing,
  service invariants); `.claude/CLAUDE.md` section 3 is the short form. `mypy --strict` and ruff hold; no broad
  `# type: ignore` or `noqa`.

## Article V. Testing and quality

- **V.1 Tests first.** Each test is written from an FR acceptance row, fails first, and names its FR ID in its
  name or docstring. A purely technical test names its TR ID. Details: `.claude/rules/testing.md`.
- **V.2 The gates.** ruff format and check, `mypy --strict`, import-linter, unit and contract tests, contracts and
  chart checks; real PostgreSQL 18 and RabbitMQ when touched; Kind for cluster work; no open vulnerability; the
  coverage floors. Gates are never lowered; a check not run is "not run", never "passed". An exception needs a
  reason, a reviewer, an expiry and a follow-up (`security-exceptions` skill). How to run: `provisioning-gates`.
- **V.3 The QA sweep.** `make qa-sweep` runs once per phase before the final review and before any dependency
  upgrade; the qa-reviewer triages every finding (`qa-sweep` skill).

## Article VI. Safety guardrails

- **VI.1 The real cluster is read-only for agents.** Agents run only the project's local Kind cluster; every change
  to a real cluster is a command for the owner (`.claude/CLAUDE.md` section 0).
- **VI.2 No secrets.** Never read, print, copy or write a secret value or a `.env` value.
- **VI.3 No push, no merge.** Agents never push, rewrite history or merge into `python` or `main`; the owner merges
  and pushes. Commits stage named files only.
- **VI.4 Notion database pages are read-only.** Only "Web Block architecture" and "Authentication Process
  Webblock" may be edited, and only by the lead.
- **VI.5 The hooks.** `.claude/hooks/guard.py` blocks cluster changes, secret reads, pushes, merges into protected
  branches and Notion database edits, and asks the owner before a change to the hooks, the settings or this
  constitution. Nobody works around a hook.

## Article VII. Docs

- **VII.1 Current facts only, no secrets.** Docs state what is true now (git history is the record) and never hold
  a secret value. No dates of changes, change logs or provenance notes.
- **VII.2 Plain English.** Short sentences, no em or en dashes, lines at most 120 characters
  (`.claude/rules/docs-style.md`).

## Article VIII. Agents

- **VIII.1 One implementer at a time, someone else reviews.** Only one implementer works in the shared tree at a
  time. The owner approves the spec, the design, the plan and the merge.
- **VIII.2 Independent reviewers.** A reviewer never checks its own work and never fixes what it reviews; the
  reviewer agents write only their reports under `ai/reviews/` (`reviewer_writes.py`). Agents do not start other
  agents; the lead briefs each one.

## Amendment

Only the owner amends this constitution; each amendment raises the version number at the top (Version 2); no
dates.
