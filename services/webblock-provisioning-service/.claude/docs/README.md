# Provisioning docs: start here

Provisioning turns a commit that Project has published into a running website. It builds the release on Jenkins,
deploys it as a candidate, checks it, makes it live, rolls back when that fails, and suspends or resumes hosting
when Payment says so. It is one Python package (`src/provisioning/`) with four processes: `api`, `worker`,
`build_worker` and `bootstrap`.

The database design and the API contract live only in the owner's Notion pages (linked at the top of
`.claude/CLAUDE.md`). There is no local copy on purpose.

## Understand it

- [requirements/README.md](requirements/README.md): the spec (BRD, PRD, FRD, TRD, approved).
- [CONSTITUTION.md](CONSTITUTION.md): the rules every spec, design, plan and review is checked against.
- [provisioning-overview.md](architecture/provisioning-overview.md): what the service does and where it fits.
- [prototype-deployment-flow.md](architecture/prototype-deployment-flow.md): how a deploy moves, step by step.
- [failure-modes.md](architecture/failure-modes.md): what can fail and how the service reacts.
- [acceptance-scenarios.md](architecture/acceptance-scenarios.md): every security and recovery case and its test.
- [prototype-stand-ins.md](architecture/prototype-stand-ins.md): what the prototype runs that production does not.
- [TECH_STACK.md](../TECH_STACK.md): what the service uses, with exact versions.

## Run and test it

- [python-p0.md](runbooks/python-p0.md): tools, the Python environment and the `py-*` gates.
- [prototype-stack.md](runbooks/prototype-stack.md): the local Kind stack the prototype runs on.
- [prototype-demo.md](runbooks/prototype-demo.md): run the four processes, the end-to-end suite and the demo shop.
- [prototype-in-cluster.md](runbooks/prototype-in-cluster.md): run the service as pods of the Helm chart in Kind.
- [replicas.md](runbooks/replicas.md): two copies of each process against one database.
- [team-testing.md](guides/team-testing.md): call the REST API and MCP tools by hand (Bruno files in `guides/bruno/`).
- [api-testing-with-swagger.md](runbooks/api-testing-with-swagger.md): try the REST API in Swagger UI (prototype only).

## Operate it

- [operator.md](runbooks/operator.md): re-drive, hosting retries, project deletion, sub-domain backfill.
- [failure-codes.md](runbooks/failure-codes.md): each failure code and whether a retry helps.
- [identity.md](runbooks/identity.md): how the API checks bearer tokens.
- [observability.md](runbooks/observability.md): metrics and Sentry.
- [production-profile.md](runbooks/production-profile.md): the production profile and the build and API split.
- [phase-1-database.md](runbooks/phase-1-database.md): database roles and migrations (first written for the Go version).

## Decisions

- [decisions/README.md](decisions/README.md): every decision record with its status (current or superseded).
- [decisions/open-decisions.md](decisions/open-decisions.md): what is not decided yet.

## Work in progress

- [ai/handoffs/after-phase-c.md](../../ai/handoffs/after-phase-c.md): where the service stands and what is owed.
- [ai/handoffs/auth-telemetry-alignment.md](../../ai/handoffs/auth-telemetry-alignment.md): next auth and telemetry
  work.
- [ai/README.md](../../ai/README.md): the agent handoffs and the review reports still in use.

Removed documents stay in git tag `docs-before-cleanup` (`git checkout docs-before-cleanup -- <path>`).

## Folder map

| Folder | Holds |
| --- | --- |
| `architecture/` | Overview, deploy flow, failure modes, acceptance scenarios, stand-ins |
| `decisions/` | Numbered decision records (never renumbered) and the open list |
| `runbooks/` | How to run, test and operate the service |
| `guides/` | The team testing guide and its Bruno collection |
| `designs/` | The change design in progress, one per change (`designs/README.md`) |
| `plans/` | The task plan in progress, one per change (`plans/README.md`) |
