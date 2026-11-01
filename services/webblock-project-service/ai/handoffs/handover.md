# Handover: the Project service for its next owner

Read `.claude/CLAUDE.md` first, then this page. It says what is built, what is open and where each thing lives. Work
still owed in order is in [`next.md`](next.md); this page links it and does not copy it.

## 1. What the service is and who it talks to

The Project service is the backend for clients' websites: projects, pages (Puck JSON trees), generation contracts
and requests, Payment admission, the shared catalog database, and the events Provisioning consumes. It is one Spring
Boot service (Java 25) with one deployable (decision [0008](../../.claude/docs/decisions/0008-one-deployable.md)).
Every exchange with another service is in the table of the
[contracts index](../../.claude/docs/contracts/README.md).

A website lives at `<subdomain>.<site domain>` (today `roaa15.xyz`, on k3s) and its preview at
`<subdomain>-preview.<site domain>`.

Services today: Project, Provisioning and Payment (Payment is another team's). Account, AI Codegen and Notification
are not built; their contract pages are the spec they will be built to. Boundaries: `.claude/CLAUDE.md` section 6.

## 2. What is built

J0, J1, authentication, J2 and the J3 Notion alignment are on `main`:

- Projects and the wizard, pages with the `edit_revision` rule, the component catalog, the catalog registration,
  Payment admission, generation contracts, capture and admission, `codegen-input` (FR-8 to FR-29, FR-35).
- Migration `V6` and `V7`: the sub-domain on `project.projects`, `project.domains` gone (FR-13, FR-36).
- gRPC `CheckAccess` on port 9090 (FR-31 to FR-33, decision
  [0011](../../.claude/docs/decisions/0011-grpc-check-access-and-pinned-contracts.md),
  [`grpc.md`](../../.claude/docs/runbooks/grpc.md)).
- `manage_site` in the access check and the shared action list (FR-32, TR-27); `contracts.lock` pins
  `web-block-contracts` (remote `git@github.com:itme-roa15/webblock-contract.git`) at `18dd301`.
- The layer-first Java layout of Notion 6.1: `presentation`, `application`, `domain`, `infrastructure` first, then
  type and feature folders; features are not Spring Modulith modules (TR-5, decision
  [0016](../../.claude/docs/decisions/0016-layer-first-java-layout.md)). The migration job's main class is
  `org.kshrd.webblock.project.infrastructure.persistence.migration.Migrations`.

Gate results at the merge are in [`next.md`](next.md), "Last gate results". The status of each ID, with its tests, is
in [`DIRECTION.md`](../../.claude/docs/requirements/DIRECTION.md).

## 3. How to work here

- Lean spec-driven flow: a change note the owner agrees, tests first, one review, one fix round, the owner
  merges: [`WORKFLOW.md`](../../.claude/WORKFLOW.md). No FR ID, no code
  ([`spec-driven.md`](../../.claude/rules/spec-driven.md)). Gates and commands: `.claude/CLAUDE.md` sections 1 and 4.
- The constitution (version 7) binds every design, plan and review:
  [`CONSTITUTION.md`](../../.claude/docs/CONSTITUTION.md). Only the owner amends it.
- The real cluster is read-only for agents: reads are allowed, every change is denied and is a command written for
  the owner, no Secret value is printed (`.claude/CLAUDE.md` section 0, Art. VI). Agents never push or merge.
- The guard hook (`.claude/hooks/guard.py`) is a guard rail, not a sandbox. Known limits:
  - A write through an interpreter (`python3 -c`, a heredoc script, `node -e`) is not seen.
  - A failed `cd` before `;` is not tracked.
  - An "ask" verdict does not stop an agent in auto mode; only "deny" does. Rules that must hold are denies.
  - `rm -r` under `.superpowers/` is wrongly called a core folder.
- Hooks, rules, skills and agents: [`.claude/README.md`](../../.claude/README.md).

## 4. What is open, in priority order

1. **The rest of J3.** Git hosting on Gitea, repository creation, guarded publication, and `GetRepository` over gRPC
   (PR-8, PR-9, PR-18). Also the five items of `next.md`, "For the next owner of the service", "The rest of J3":
   two guards, the project state at publish, the exact `fieldSchema`,
   timestamp precision, and FR-34 (remove the REST access check once Provisioning calls gRPC everywhere).
2. **J4.** The `publish_completed` consumer that makes a website `active` (FR-37, PR-9), templates, scaffold, the
   Codegen exchange (FR-27, FR-29, PR-16, PR-22) and `project.source_published.v1` (PR-19). The pages are in the
   [AI Codegen note](../../.claude/docs/contracts/service-communication.md) and the
   [Provisioning contract](../../.claude/docs/contracts/provisioning-contract.md).
3. **J5.** Project deletion and its event are built (FR-38 to FR-40, PR-10, PR-20); still open: the owner-status
   event (PR-21), taking hosting offline for a suspended website, admin search by sub-domain (PR-11) and production
   hardening.
4. **FR-18 Drift** (ruling R-5). `max_pages` is a constant, Notion says a setting (FR-18). It is a Must item.
5. **The 28 Must FRs with no tagged test.** Tag or write tests named `FR-n: ...`, starting with FR-1 to FR-4 and FR-6
   to FR-12 (`ai/reviews/claude-config-evaluation.md`, "Top fixes", item 1). `python3 -I scripts/spec-check.py` lists
   them.
6. **API behaviour against Notion**
   ([`api-notion-check-claude-makeover.md`](../reviews/api-notion-check-claude-makeover.md), "Behaviour"):
   B1 `meta.traceId` is written as `null` when untraced; B2 no admin endpoint or roles read (PR-11);
   B3 Payment is HTTP, Notion says gRPC; B4 Codegen input is REST; B5 no read of AI runs; B6 only `CheckAccess` is
   served (`GetRepository`, `GetProjectOwner`, `CheckThreadOwner` are not); B7 only `generation_requested.v1` is
   published; B8 no consumer, queue or dead-letter queue; B9 no `traceparent` or `x-request-id` header on messages;
   B10 `manage_site` is built. The Notion gaps N1 to N10 and S1 are for the owner.
7. **The older QA items** ([`qa-project-notion-alignment.md`](../reviews/qa-project-notion-alignment.md)): Payment
   `HttpClient` not closed, two cursor parsers, the `"20"` page-size literal, `ProfileGuard` brackets, the
   `OutboxMetrics` null check, deprecated test calls, two Javadoc warnings, copying `List` and `Map` in
   `application/model` records. Also an IDE inspection pass on `src/main`.
8. **Grader items deferred** ([`claude-config-evaluation.md`](../reviews/claude-config-evaluation.md), "Top fixes"):
    split `guard.py` (item 10), limit the writes of the researcher and kind-runner agents (item 9), and explain or
    remove `Read(!.env.example)` in `.claude/settings.json`.
9. **Database audit gaps** ([`db-notion-audit-claude-makeover.md`](../reviews/db-notion-audit-claude-makeover.md)):
    G8, the project database keeps the default `public` schema and the runbook says nothing of it; G9, Notion "2 ·
    Project" still calls `subdomain` "planned". Result of the audit: 0 DIFFERENT. Planned items (archived sites,
    product limit, storage limits) wait for their features (FR-35, FR-36).
10. **Known limits** that still matter are listed in `next.md`, "Known limits (open)".
11. **Adopt `web-block-commons`**: replace Project's own token check, caller check, error format, request ID and logs
    with the shared library. The Java starter is on GitHub Packages Maven (group `io.github.itme-roa15`) and needs a
    read token in Gradle that never enters the repository or a lockfile (Art. II, first-party packages).
12. **Split the services into one handler per use case** (Notion 6.1, decision
    [0016](../../.claude/docs/decisions/0016-layer-first-java-layout.md)). The `*Service` classes sit whole in
    `application/<feature>/handler`; each use case becomes its own handler class, and the coverage rule in
    `build.gradle` (`*Service`) follows in the same change.

## 5. Owner questions still open

Ruled by the owner: sub-domain labels (FRD Q-2). FR-13 refuses a label that ends in `-preview`, reserves `preview`,
refuses `xn--` labels, caps a label at 55 characters, and `ProfileGuard` refuses an empty reserved list in
production. The spec is in the FRD; the code is open item 1.

- Who commits to Gitea: the proposal is Project (FRD Q-3, decision
  [0006](../../.claude/docs/decisions/0006-codegen-owns-the-build.md)).
- The eleven questions of the contracts index, "Open owner questions": Payment transport, AI Codegen transport, the
  Git host address in `clone_url`, who may call `GetRepository`, and others.
- Notion "Web Block architecture" 4.4 and 5.2: a queue `project.publish_completed.v1` on `provisioning.events` and a
  read right for Project's broker user (FR-37).
- The mesh steps for port 9090: [`grpc.md`](../../.claude/docs/runbooks/grpc.md).
- The V6 rollout: [`migrations.md`](../../.claude/docs/runbooks/migrations.md), "Rolling out V6".
- A Kind slot for the Kind exit check: [`j1-kind-exit.md`](../../.claude/docs/runbooks/j1-kind-exit.md).
- What other teams must do is in `next.md`, "Waiting on others".

## 6. Endpoint status

Every exchange Project serves or consumes, and whether it is built. "Arch" is Notion "Web Block architecture"
(section numbers follow it); "SC" is
[`service-communication.md`](../../.claude/docs/contracts/service-communication.md);
other pages are in the [contracts index](../../.claude/docs/contracts/README.md). Proto and schema files are in
`web-block-contracts`. The differences from Notion are in
[`api-notion-check-claude-makeover.md`](../reviews/api-notion-check-claude-makeover.md) and
[`contracts-inventory.md`](../reviews/contracts-inventory.md).

| Kind | Name | Direction | State | FR or PR | Defined in |
| --- | --- | --- | --- | --- | --- |
| REST | `GET /api/v1/business-types` | Frontend calls Project | built | FR-8, PR-1 | FRD "Creating a website"; Arch 2 |
| REST | `GET /api/v1/templates` | Frontend calls Project | built | FR-9 | FRD "Creating a website"; Arch 2 |
| REST | `PUT /api/v1/projects/{projectId}` | Frontend calls Project | built | FR-10 to FR-13 | FRD "Creating a website"; Arch 2 |
| REST | `GET /api/v1/projects` | Frontend calls Project | built | FR-14 | FRD "My websites"; Arch 2.1 |
| REST | `GET /api/v1/projects/{projectId}` | Frontend calls Project | built | FR-15 | FRD "My websites"; Arch 1 |
| REST | `PATCH /api/v1/projects/{projectId}` | Frontend calls Project | built | FR-16 | FRD "My websites"; Arch 2 |
| REST | `GET /api/v1/projects/{projectId}/pages` | Frontend calls Project | built | FR-17 | FRD "Pages and the builder"; Arch 2 |
| REST | `POST /api/v1/projects/{projectId}/pages` | Frontend calls Project | built | FR-18 | FRD "Pages and the builder"; Arch 2 |
| REST | `PUT /api/v1/projects/{projectId}/pages/{pageId}` | Frontend calls Project | built | FR-19 | FRD "Pages and the builder"; Arch 2 |
| REST | `DELETE /api/v1/projects/{projectId}/pages/{pageId}` | Frontend calls Project | built | FR-20 | FRD "Pages and the builder"; Arch 2 |
| REST | `GET /api/v1/component-catalog/{version}` | Frontend calls Project | built | FR-21, PR-5 | FRD "Pages and the builder"; Arch 2 |
| REST | `POST /api/v1/projects/{projectId}/generation-contracts` | Frontend calls Project | built | FR-22 | FRD "Generation contracts"; Arch 2 |
| REST | `GET /api/v1/projects/{projectId}/generation-contracts` | Frontend calls Project | built | FR-23 | FRD "Generation contracts"; Arch 2 |
| REST | `POST /api/v1/projects/{projectId}/generation-requests` | Frontend calls Project | built | FR-24 | FRD "Generation requests and admission"; Arch 2 |
| REST | `GET /api/v1/projects/{projectId}/generation-requests` | Frontend calls Project | built | FR-25 | FRD "Generation requests and admission"; Arch 2 |
| REST | `GET /api/v1/projects/{projectId}/generation-requests/{generationRequestId}` | Frontend calls Project | built | FR-25 | FRD "Generation requests and admission"; Arch 2 |
| REST | `DELETE /api/v1/projects/{projectId}` (delete a website) | Frontend calls Project | built | FR-38, PR-10 | PRD PR-10; not in a contract page or Arch 2 |
| REST | `POST /api/v1/projects/{projectId}/suspend` and `/reactivate` (owner) | Frontend calls Project | built | FR-39, PR-10 | PRD PR-10; not in a contract page or Arch 2 |
| REST | Admin: suspend, resume, search by sub-domain | Admin pages call Project | missing | PR-11 (no FR yet) | Arch 5.3 and 2.4; no contract page |
| REST | `POST /internal/v1/access-checks` | Provisioning calls Project | built | FR-30, FR-32, FR-34, PR-12 | provisioning-contract B; SC 3; Arch 9 point 5 (REST goes when Provisioning calls gRPC) |
| REST | `GET /internal/v1/generation-requests/{id}/codegen-input` | AI Codegen calls Project | partial | FR-29, PR-16, PR-22 | SC 2.2; Arch 5.1 says gRPC `GetCodegenInput` |
| REST | `GET /internal/v1/generation-requests/{id}/scaffold` | AI Codegen calls Project | missing | PR-22 (J4) | SC 2.2; Arch 5.1 says gRPC `GetScaffold` |
| REST | Public site-portal ownership check | Site portal calls Project | missing | PR-14, FR-32 | Arch 9 point 3 (a proposal); the mesh path is the `CheckAccess` row |
| gRPC | `webblock.project.v1.AccessService/CheckAccess` (port 9090, `manage_site` included) | Provisioning, site portals call Project | built | FR-31 to FR-33, FR-32, PR-13 | `proto/webblock/project/v1/access.proto`; provisioning-contract D; Arch 3.2, 3.8; `grpc.md` |
| gRPC | `RepositoryService/GetRepository` | Provisioning build worker calls Project | missing | PR-18, PR-8 | `proto/webblock/project/v1/repository.proto` (draft); provisioning-contract E; Arch 5.1, 3.8 |
| gRPC | `GetProjectOwner` | Notification calls Project | missing | PR-22 | notification-contract 1; `project_owner.proto` (draft branch); Arch 5.1 |
| gRPC | `CheckThreadOwner` | AI Codegen calls Project | missing | PR-22 | Arch 2.4 and 5.1; no proto, no contract page |
| gRPC | `GetCodegenInput`, `GetScaffold` (the gRPC form of the two REST rows) | AI Codegen calls Project | missing | PR-22 | Arch 5.1 and 3.7; REST stays until AI Codegen is ready |
| event out | `project.generation_requested.v1` | Project publishes; AI Codegen consumes | partial | FR-27, FR-26, PR-16 | SC 2.1; Arch 4.4; no schema file in `web-block-contracts` |
| event out | `project.generation_cancelled.v1` | Project publishes; AI Codegen consumes | missing | PR-7, PR-9 | SC 2.5; Arch 4.4; no schema |
| event out | `project.source_published.v1` | Project publishes; Provisioning, Payment, Notification consume | missing | PR-19, PR-8, PR-9 | provisioning-contract F; `events/project/source_published.v1.schema.json` (draft); Arch 4.4, 5.2 |
| event out | `project.project_deleted.v1` | Project publishes; Provisioning, Payment consume | built | FR-38, PR-20, PR-10 | provisioning-contract F; `events/project/project_deleted.v1.schema.json` (draft); payment-contract 4 |
| event out | `project.owner_status_changed.v1` | Project publishes; Provisioning consumes | missing | PR-21 | provisioning-contract F; `events/project/owner_status_changed.v1.schema.json` (draft); Arch 4.4, 5.2 |
| event in | `provisioning.publish_completed.v1` | Provisioning publishes; Project consumes | missing | FR-37, PR-9 (J4) | provisioning-contract G; `events/provisioning/publish_completed.v1.schema.json`; Arch 4.4 and 5.2 do not list Project |
| event in | `ai_codegen.generation_completed.v1` | AI Codegen publishes; Project consumes | missing | PR-9, PR-16 | SC 2.4; Arch 4.4; no schema |
| event in | `account.user_status_changed.v1` | Account publishes; Project consumes | missing | PR-21 | account-contract 1; Arch 4.4, 5.2; no schema |
| call out | `POST /internal/v1/website-admissions` (admit a website) | Project calls Payment | partial | FR-10, FR-11, PR-15 | payment-contract 2.1; Arch 5.1 says gRPC `AdmitWebsite` |
| call out | `DELETE /internal/v1/website-admissions/{projectId}` (release) | Project calls Payment | partial | FR-12, PR-15 | payment-contract 2.2; Arch 5.1 says gRPC `ReleaseWebsiteAdmission` |
| call out | `POST /internal/v1/generation-admissions` | Project calls Payment | partial | FR-26, PR-15 | payment-contract 2.3; Arch 5.1 says gRPC `AdmitGenerationAttempt` |
| call out | `GET /internal/v1/projects/{id}/entitlement` (for the UI) | Project calls Payment | missing | none (BR-11) | payment-contract 2.4; `proto/webblock/payment/v1/entitlement.proto` (draft); Arch 5.1 `GetEntitlement` |
| call out | `GET /internal/v1/clients/{id}/entitlement` (plan summary for the UI) | Project calls Payment | missing | none | payment-contract 2.5; not in Arch |
| call out | Read a run and its runs (`GetGenerationRun`, `ListGenerationRuns`) | Project calls AI Codegen | missing | PR-9, PR-16 | SC 2.4; Arch 5.1 says gRPC |
| call out | Download the generated source (presigned URL, SHA-256) | Project reads AI Codegen storage | missing | PR-9 | SC 2.3, 2.6; Arch 5.4 |
| call out | Gitea: create the site repository, commit the accepted overlay | Project calls Gitea | missing | PR-8, PR-9 | Arch 5.4 and 9 point 6; decision 0006; no contract page |
| call out | Keycloak key set (JWKS), token check | Project calls Keycloak | built | FR-1, FR-2, PR-23 | authentication-contract; Arch 2.4, 8 |
| call out | Shared catalog database (PostgreSQL roles) | Project writes; Provisioning and sites read | built | FR-35, FR-36, PR-17 | provisioning-contract A; Notion "6 · Shared catalog" |

Partial, codegen-input and `generation_requested`: Project's side works and is tested, but Notion names gRPC (or no
schema file is pinned) and the AI Codegen side is not built.
Partial, the three Payment calls: Project's side works over HTTP, but Notion names gRPC and Payment has not accepted.

## 7. Where things live

| What | Where |
| --- | --- |
| Rules for every agent, commands, layout | `.claude/CLAUDE.md` |
| Environment variables and how to run locally | `.env.example`, `README.md` "Running the service locally" |
| Stages, agents, owner gates | `.claude/WORKFLOW.md` |
| Spec: BRD, PRD, FRD, TRD, `spec.json`, `DIRECTION.md` | `.claude/docs/requirements/` |
| Designs and plans | `.claude/docs/designs/`, `.claude/docs/plans/` |
| Decision records 0001 to 0015 | `.claude/docs/decisions/` (index `README.md`) |
| Runbooks (database, catalog, migrations, outbox, gRPC, Kind) | `.claude/docs/runbooks/` |
| Contracts with other services | `.claude/docs/contracts/` (index `README.md`) |
| Owner proposals | `.claude/docs/proposals/` |
| Reviews and audits | `ai/reviews/` |
| Handoff and this page | `ai/handoffs/next.md`, `ai/handoffs/handover.md` |
| Hooks, rules, skills, agents | `.claude/hooks/`, `.claude/rules/`, `.claude/skills/`, `.claude/agents/` |
| Exact versions, dependency dates | `.claude/TECH_STACK.md`, `.claude/docs/dependency-versions.md` |
| Proto and event schemas | repository `web-block-contracts` (`git@github.com:itme-roa15/webblock-contract.git`), pinned in `contracts.lock` |
| Notion: platform-db-v6 (hub) | https://app.notion.com/p/f8558175966e8233b37e81847a58e2ad |
| Notion: Database Design | https://app.notion.com/p/40058175966e81f790decccc6e9abf4a |
| Notion: 2 · Project | https://app.notion.com/p/40058175966e81d4a77bfb48e736757a |
| Notion: 6 · Shared catalog | https://app.notion.com/p/40058175966e81ba92e8e5f6b3e4db2a |
| Notion: Web Block architecture | https://app.notion.com/p/40358175966e8198a088f123a9ab23fe |

The Notion database design pages are read only: never edit them.
