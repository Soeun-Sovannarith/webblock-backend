# 0025: Domain kinds and CQRS layout

Status: Accepted (gate 2), on branch `cqrs-layout`, not merged. Amends `0010` (the folder layout inside `core/`).
TRD: TR-2 amended, TR-54 and TR-55 added. This record holds the CQRS rules (CQ1 to CQ12) and the folder layout
rules still in force (FL, section 3b); the design file they came from is in git history.

## 1. Context

The owner: "Inside the domain it is hard to distinguish between entity, values or enum, and in
the application I don't know which one is the handler, command, query, since I am following the CQRS pattern (if
there is a library for CQRS, use it)."

Before this record the domain was 15 flat modules that mixed kinds (an entity, its values, its rules and its enums
in one file), and the 14 REST and MCP operations were methods of one 904-line class, `Service`. A reader could not
tell a write from a read, or an entity from a value.

## 2. Constraints

- Owner decision: CQRS applies to the Provisioning service only. Java keeps per-feature request
  and response (Notion "Web Block architecture" section 6.1, unchanged).
- No database change: no table, column, enum, role or grant changes, no migration. The Notion DB pages are not
  touched and the database still matches them 100%.
- No behaviour change: the same REST and MCP answers, messages, log lines, audit events and metrics.
- Never loosen an import-linter contract (`0010`, `0013` section 4). No new dependency without the owner.
- The folder layout rules (section 3b) hold unless a CQ rule below says otherwise.

## 3. Decisions

The owner approved all of them with the design.

- **CQ1. Domain: one folder per kind.** `entities/`, `values/`, `enums/`, `rules/`. Option refused: one folder per
  aggregate (the biggest modules are rules that change several entities). Cost if wrong: one round of `git mv`.
- **CQ2. One file is not a folder.** `errors.py` and `events.py` stay single modules at the domain root.
- **CQ3. A domain layer contract.** `rules | events` above `entities` above `values` above `enums` above `errors`
  (import-linter `domain-kinds`); `tests/unit/core/domain/test_kinds.py` checks the kind of every class.
- **CQ4. Rule inputs and results live with their rule** in `rules/`, because they carry entities.
- **CQ5. The domain facade keeps its public names.** Values stay plain `str`, `int` and `UUID` checked by parsers.
- **CQ6. Application: CQRS inside each feature.** `commands/` (a write intent and its handler per module),
  `queries/` (a read intent and its handler per module), `background/` (jobs, loops and relays that start from a
  durable row), `ports.py` (the feature's store protocols). A feature has only the folders it needs.
- **CQ7. Names.** `<Verb><Noun>Command` with `<Verb><Noun>Handler`; `Get<Noun>Query` or `List<Nouns>Query` with
  `...Handler`. A handler has one public method, `async def handle(self, intent)`. The CQRS base class is used as
  `cqrs.Command[R]`, never imported bare, so it is never confused with the domain entity `Command`. The folder
  `core/application/commands/` (a command job's life) became `core/application/jobs/`.
- **CQ8. The 14 REST and MCP operations live in `operations/`:** 7 commands and 7 queries sharing `gate.py`,
  `auditing.py` and `acceptance.py`. Messaging commands live in their feature (`intake/`, `hosting/`).
- **CQ9. Worker jobs are not sent through the command bus.** An accepted command row is the durable job; the
  worker claims it and the `Executor` table runs it with fence, lease and deadline.
- **CQ10. A small in-repo dispatcher, no library** (against the owner's stated preference, approved by the owner).
  `core/application/shared/cqrs.py`: `Command[R]`, `Query[R]`, the handler protocols, `CommandBus` and `QueryBus`
  (`register` refuses a second handler with `ValueError`; `send` of an unregistered type raises `NotRegistered`,
  which the hosts' start-up check makes unreachable in a running process). No library passed every rule
  (licence, age, typing, frozen dataclasses, no service locator); the owner's alternative is `mediary` 0.4.0, a
  change of one module.
- **CQ11. Presentation sends intents.** The `Invoker` takes the two buses; the RabbitMQ consumers send
  `ReceiveSourcePublishedCommand`, `AcceptHostingEventCommand` and `AcceptOwnerStatusCommand`. Each process
  registers only its own handlers and fails at start when one it needs is missing. The operator CLI is unchanged.
- **CQ12. import-linter: tighten only.** New: `domain-kinds`, `cqrs-is-a-leaf`, and two contracts that keep
  commands and queries apart (queries import no command and no `background/`; commands import no query). The
  bootstrap contract names the new paths. Nothing removed or loosened.
- **Owner's open questions 2 to 5, answered as recommended:** the operator CLI stays outside the buses; no typed
  value classes now; the operations stay in `operations/`; `errors.py` and `events.py` stay single modules.

## 3b. Folder layout rules still in force (FL)

- **FL1.** Four layers plus hosts: `core/domain`, `core/application`, `infrastructure`, `presentation`, `hosts`.
- **FL2.** `core/application` is grouped by feature: what changes together sits together (inside a feature, CQ6).
  Two leaf folders serve every feature: `shared/` and `ports/`.
- **FL3.** `core/application/ports/` holds the ports to systems outside Provisioning (Jenkins, the cluster, metrics,
  Project, Payment, clock, IDs). A store protocol for our own database lives in the feature that uses it (`ports.py`).
- **FL4.** Replaced by CQ1 (the domain has one folder per kind).
- **FL5.** Module names change only when a module is split or its name no longer says what it is.
- **FL6.** A module goes where its imports put it: broker consumers in `presentation/messaging/consumers/`;
  executors, loops and outbox relays that call only ports in `core/application/<feature>/` (`background/`, CQ6).
- **FL7.** No loop lives in presentation: the inbox processor and the hosting retry sweeper are in
  `core/application/intake/` and `core/application/hosting/`; their consumers stay in presentation.
- **FL8.** Host loops that import only core live in core (the bootstrap loop in `tenants/`, build hints in
  `builds/`). Process guards stay in `hosts/`.
- **FL9.** The REST and MCP operation table is `presentation/operations`.
- **FL10.** `hosts/` holds only process folders plus `shared/` (start-up code several processes share) and
  `devtools/` (`python -m` tools; nothing imports them and they import no observability library).
- **FL11.** `core/application/__init__.py` and `core/domain/__init__.py` are the only facades; other `__init__.py`
  files hold a docstring only.
- **FL12.** import-linter contracts are tightened, never loosened: `application-shared-and-ports-are-leaves`,
  `devtools-stand-apart` and the contracts of CQ12.
- **FL13.** A layout change changes no behaviour, public name or log name.

Where new code goes:

- A write or read intent: `core/application/<feature>/commands/` or `queries/`, one intent and handler per module;
  a new feature gets a new folder.
- A job, loop or relay that starts from a durable row: the feature's `background/`.
- A store protocol: the feature's `ports.py`. A port to an outside system: `core/application/ports/`.
- Code every feature needs that imports only the domain: `core/application/shared/`.
- A REST route or MCP tool: `presentation/http` or `presentation/mcp`, through `presentation/operations`. A RabbitMQ
  listener: `presentation/messaging/consumers/`. An adapter: `infrastructure/<outside system>/`.
- A new process: `hosts/<process>/` with `__main__.py` and `wiring.py`.

## 4. Consequences

- Code: `src/provisioning/core/domain/**` and `core/application/**` reshaped (CQ1, CQ6); the
  facade of each keeps its public names, except that `Service`, `ServiceDeps`, the request types
  (`ReleaseCommandRequest`, `GetReleaseRequest` and the other six) and `OperationsService` are gone. Callers build
  the handlers with `register_operations(commands, queries, OperationDeps(...))` and send intents.
- Tests mirror the new folders; the transport fakes answer on buses.
- Docs: `README.md`, `.claude/CLAUDE.md`, `.claude/WORKFLOW.md`, the runbooks and the `writing-frd` skill name the
  new paths. Notion "Web Block architecture" section 6.2 gets the new text (section 6).
- Cost if wrong: a round of `git mv` behind unchanged facades; swapping the dispatcher for `mediary` is one module.

## 5. What was built and tested

Seven commits on `cqrs-layout`, one per step:

| Step | Commit | What |
| --- | --- | --- |
| 1 | `06a7a9d` | `shared/cqrs.py`, its tests, contract `cqrs-is-a-leaf` |
| 2 | `87dc1de` | Domain by kind, `domain-kinds` contract, `test_kinds.py` |
| 3 | `5d86764` | `commands/` to `jobs/`, `ports.py` per feature, `background/` folders |
| 4 | `90aa20a` | The three messaging commands through each worker's command bus |
| 5 | `3ffe536` | The 14 operations as commands and queries; `Invoker` and API host on the buses |
| 6 | `475eff8` | `Service`, `ServiceDeps`, `OperationsService` and the request types retired |
| 7 | this record | Docs, TRD rows, this record |

Gates (local runs, after step 6 and again after step 7, same results):
`make py-fmt-check py-lint py-typecheck py-layers py-test` pass (7057 unit and contract tests; import-linter 16
contracts kept, 12 on `main`); `make py-test-integration` 559 passed; `make py-test-messaging` 34 passed (the first
run after step 6 had one failure in `tests/messaging/test_dlq.py`, a module this work does not touch; every later
run passed); `make chart-check` and
`make contracts-check` pass; `python3 -I .claude/hooks/test_guard.py` 243 of 243. `make py-cover`: domain 99.1%,
application 96.9% (on `main`: 98.9% and 96.6%); its total gate fails only for the missing Kind recordings
(`proto-test-kind`, `py-test-catalog-live`, `py-test-jenkins`), which need a Kind run.

Not run: Kind (`proto-test-kind`, `proto-e2e`), owed once before the merge because the hosts' wiring changed.

## 6. What stays open

- The Kind run before the merge (kind-runner, in a memory slot the owner gives).
- Notion "Web Block architecture" section 6.2: the lead pastes the text of the design's section 9 (in git history:
  `git show 94d57e1:.claude/docs/designs/2026-10-28-provisioning-domain-and-cqrs-layout-design.md`).
- No OD entry is opened or closed by this record.
