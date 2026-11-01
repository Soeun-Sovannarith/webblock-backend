---
paths:
  - "src/**"
  - "tests/**"
---

# Python code rules (owner rules)

Python 3.14, `mypy --strict`, ruff (config in `pyproject.toml`). CLAUDE.md section 3 gives the short form; this file
is the full one.

## Layers (`make py-layers`, import-linter)

- `core/domain`: one folder per kind, `rules | events` above `entities` above `values` above `enums` above `errors`.
  It imports no FastAPI, PostgreSQL, RabbitMQ or Kubernetes code.
- `core/application`: imports only the domain. Each feature has `commands/`, `queries/`, `background/` (jobs, loops
  and relays that start from a durable row) and `ports.py` (the feature's store protocols), only the ones it needs.
- `infrastructure`: adapters behind the ports. `presentation`: thin HTTP, MCP and messaging handlers that send
  commands and queries through the buses; no workflow logic, no Kubernetes.
- Only `hosts` wires concrete types. Small interfaces defined by their users, not one per class. Constructor
  injection; no global service locator. Each process registers only its own handlers.
- Never loosen an import-linter contract; add or tighten only.

## CQRS names (decision `0025`)

- One intent and its handler per module: `<Verb><Noun>Command` with `<Verb><Noun>Handler`; `Get<Noun>Query` or
  `List<Nouns>Query` with `...Handler`. A handler has one public method, `async def handle(self, intent, /)`.
- Use the base classes as `cqrs.Command[R]` and `cqrs.Query[R]`, never imported bare (the domain has an entity named
  `Command`). Intents are `@dataclass(frozen=True, slots=True)`.
- Queries import no command and no `background/`; commands import no query. Worker jobs are not sent through the
  command bus: an accepted command row is the durable job.

## Async and typing

- Everything that waits is `async`; no blocking call inside a coroutine. Every remote call has a bounded timeout and
  honours cancellation. No unbounded task, queue, request body or retry. No dangling task (ruff `RUF006`).
- Full type hints; no broad `# type: ignore` or `noqa`. A narrow one names its rule and reason.
- HTTP only through `httpx2` with explicit timeouts (`requests` is banned). Kubernetes clients only through
  `infrastructure/kubernetes/_client.py` with an explicit project-local kubeconfig (config loaders are banned).
- No deprecated API: `filterwarnings = ["error"]` turns every warning into a test failure; fix the cause.

## Service invariants

- Explicit transactions, parameterized SQL, typed errors. No transaction open across a remote call.
- Validate every state change. Accept a build result only when project, release and commit SHA match. A worker
  write checks the command's fence, its lease, and that it still runs. Check authorization on reads too.
- Store acceptance before replying. Same key and request returns the first operation; same key, other request is a
  conflict (compare `request_hash`). The server assigns the fence.
- One command in flight per project through a durable owner; none while the route is not stable, except an ADMIN
  rollback of an uncertain route. No in-process locks across replicas.
- Store intent, act, inspect real state, reconcile after a crash. Never claim distributed exactly-once.
- PostgreSQL is the authority; RabbitMQ only notifies, and a lost message is rebuilt from SQL. Acknowledge after
  durable work; publisher confirms and durable retry. Inbox and outbox tables are separate storage.
- Build, deploy, promote and rollback stay separate. Builds start automatically; MCP cannot skip a gate. Hosting
  commands come only from Payment, Project's "project deleted" event and admins.
- Build only the recorded commit with a trusted template: no platform credentials, Docker socket or cluster token.
- Only the worker calls Kubernetes: field manager `webblock-provisioning-worker`, strict validation, never forced,
  except an ADMIN rollback may force the HTTPRoute. Only the bootstrap creates namespaces, RoleBindings and Secrets.
  Names, routes and registry paths come from trusted records.
- Keep the live release's image until the next one is live. Recovery points the route back, never rebuilds. A
  failed first publish removes its route.
- When hosting stops, delete no catalog data, source or live release. Recheck a shutdown against current
  entitlement; a command no longer needed ends `skipped`.
- Never log tokens, credentials, source or customer content. Errors are sanitized; Sentry data is project-scoped.

## No dead or duplicated code

- No TODO stubs or fake success: an unsupported case is an explicit error. Handle every error and close resources.
- Delete code that nothing calls; never keep a second copy of a rule, a parser or an SQL statement. A plain
  function before a framework. Adapt a sound structure and record the mapping; do not rewrite it, and keep
  unrelated work as it is. Test adapters are named as such, kept in tests, and cannot turn on in production.
