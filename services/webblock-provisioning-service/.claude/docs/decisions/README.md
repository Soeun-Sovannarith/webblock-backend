# Decision records

Each record is one decision, kept by its number. Other files link to them by number, so they are never renumbered
or renamed. A later record changes an earlier one by saying so at the top of the earlier one. Read that note first.

Status words:

- **Current**: still applies. "Amended by N" means record N changed some sections.
- **Partly superseded by N**: some sections no longer apply; the note at the top lists them.
- **Superseded by N**: history only.
- **Proposed**: not decided.

Many early records (0001 to 0012) were written for the Go version. Their rules carry over to Python unless
`0013` or a later record says otherwise. Their file paths, `make` targets and settings name the Go reference, which
is kept on branch `go-reference`.

Older records also name files that are no longer here:

- `.claude/PLATFORM_DB_DESIGN.md` (0001 to 0014) was the local copy of the database design. It is removed; the
  Notion pages listed in `.claude/CLAUDE.md` are the design (0021).
- `review-*.md` research files (0009, 0013, 0014) live under `.superpowers/`, which git ignores.
- `REQUIREMENT.md` and `ARCHITECTURE.md` are retired. Their IDs `PR-01` to `PR-19` are not the PRD's PR-n.
- `0013` and `0014` plan the move from Go: `src/core`, `src/workers`, `deploy/policy`, `deploy/crossplane` and
  `tests/acceptance` are the Go reference's (branch `go-reference`); `src/contracts/events/prototype/` was removed
  by `0022` (E4); `ai/spikes/k8s-ssa/` is in no branch of this repository.

| No. | Title | Status |
| --- | --- | --- |
| [0001] | Domain rules | Current; see also 0011 |
| 0002 | Crossplane XR contract draft | Removed: superseded by 0013 whole; still in git history |
| [0003] | Repository layout: AI workflow folder and unit test folders | Current; amended by 0010 |
| [0004] | Durable acceptance, persistence, database roles and write guards | Current |
| [0005] | Durable dispatch to RabbitMQ and worker leases | Current; see also 0008, 0011 |
| [0006] | REST and MCP transports, identity, error mapping, exposure | Current; amended by 0013, 0023, 0024 |
| [0007] | Adoption of database and flow revision 16 | Current; later revisions in 0012, 0021 |
| [0008] | Phase 3 groundwork: source intake, releases, build attempts, roles | Current |
| [0009] | Phase 4: Kind stack, Crossplane composition, naming, tenancy | Partly superseded by 0013 |
| [0010] | Service code under `src/` in Clean Architecture folders | Current; amended by 0025 |
| [0011] | Phase 4 Go tracks: candidate failures, catalog login, migration 00010 | Partly superseded by 0013 |
| [0012] | Adopt the revision 19 Provisioning names (migration 00011) | Current; amended by 0013 |
| [0013] | Port Provisioning to Python, without Crossplane | Current |
| [0014] | Working prototype first | Current |
| [0015] | DevOps agent for failures outside the worker's control | Proposed |
| [0016] | Production environment | Current, except the site image registry (TRD TR-46, TR-52); section 10 holds the MVP site domain |
| [0017] | Project owns the catalog migrations; `ProjectAccess` over HTTP | Current; amended by 0019, 0024 |
| [0018] | Errors are RFC 9457 Problem Details | Current; amended by 0023 |
| [0019] | Service identity is the mesh | Current; amended by 0023, 0024 |
| 0020 | Sub-domain only | Not on this branch (only on branches `db-rev21` to `db-rev23`) |
| [0021] | Provisioning follows the Notion design, Phase A | Current |
| [0022] | Provisioning follows the Notion design, Phase B | Current |
| [0023] | Phase C: the REST shape release and admin operations | Current; merged into `python` |
| [0024] | Auth and telemetry alignment: web-block-commons for the token check and inner calls | Current; FR-43 open |
| [0025] | Domain kinds and CQRS layout | Current; on branch `cqrs-layout`, not merged |
| [0026] | Code quality sweep and stricter lint rules | Current; on branch `cqrs-layout`, not merged |
| [0027] | Going live takes up to 3 tries | Accepted; on branch `go-live-tries` |

Not decided yet: [open-decisions.md][open].

When you add a record, take the next free number, add a row here, and put a note at the top of every record it
changes.

[0001]: 0001-phase-1-domain-rules.md
[0003]: 0003-repository-layout.md
[0004]: 0004-acceptance-and-persistence.md
[0005]: 0005-dispatch-and-leases.md
[0006]: 0006-transports-identity-and-exposure.md
[0007]: 0007-adopt-revision-16.md
[0008]: 0008-phase-3-groundwork.md
[0009]: 0009-phase-4-cluster-stack.md
[0010]: 0010-src-layout.md
[0011]: 0011-phase-4-go-tracks.md
[0012]: 0012-adopt-revision-19-names.md
[0013]: 0013-python-port-without-crossplane.md
[0014]: 0014-working-prototype.md
[0015]: 0015-devops-agent-proposal.md
[0016]: 0016-production-environment.md
[0017]: 0017-project-service-catalog-and-access.md
[0018]: 0018-problem-details-errors.md
[0019]: 0019-service-identity-is-the-mesh.md
[0021]: 0021-notion-alignment-phase-a.md
[0022]: 0022-notion-alignment-phase-b.md
[0023]: 0023-phase-c-rest-shape-and-admin.md
[0024]: 0024-auth-and-telemetry-alignment.md
[0025]: 0025-domain-kinds-and-cqrs.md
[0026]: 0026-code-quality-sweep.md
[0027]: 0027-go-live-tries.md
[open]: open-decisions.md
