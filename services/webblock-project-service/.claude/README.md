# What is in `.claude/` (one page)

Claude Code reads this folder when a session starts in this repo. Four kinds of things live here.

| Kind | What it is | Who uses it |
| --- | --- | --- |
| Hooks | Small scripts that run before or after a tool call and can block it | Claude Code, automatically |
| Rules | Short instruction files loaded into every session, or when matching files are touched | Every agent |
| Skills | Step-by-step recipes for a recurring job, loaded when the job comes up | Any agent, on demand |
| Agents | Specialists with their own job, tools and limits; the lead hands them one task each | The lead |

[`CLAUDE.md`](CLAUDE.md) holds the repo's main rules and imports the constitution,
[`docs/CONSTITUTION.md`](docs/CONSTITUTION.md): eight articles every design, plan and review is checked against; only
the owner amends it. [`WORKFLOW.md`](WORKFLOW.md): the stages, agents, skills and owner gates.
[`TECH_STACK.md`](TECH_STACK.md): what the service uses, with exact versions. `settings.json` (shared) switches the
hooks on and lists allowed, asked and denied commands. `settings.local.json` and `worktrees/` are personal and not in
git. `docs/`: the constitution, requirements, contracts ([`docs/contracts/README.md`](docs/contracts/README.md) lists
every exchange), runbooks, decision records, designs, plans, owner proposals
([`docs/proposals/`](docs/proposals/README.md)) and dependency versions. `docs/requirements/spec.json` lists every
spec ID as data (BR, PR, FR with its acceptance rows and tests, TR); `scripts/spec-check.py` checks it and the IDs the
work cites.

## Hooks (`hooks/`)

- `guard.py` runs before every command, file read or edit, and MCP call. It lets reads of the real cluster pass and
  **blocks** changes to any cluster, printing or reading secrets, any open of a kubeconfig or `~/.kube`, `git push`,
  history rewrites, `git reset --hard`, `git clean`, merges into `main`, and edits to the Notion database design
  pages other than "2 · Project" and "6 · Shared catalog". It **asks** before risky but legal steps, before any
  edit or shell write of `docs/CONSTITUTION.md`, and before a commit that changes `src/main/`, `src/test/`,
  `build.gradle` or `gradle/` with no FR-n or TR-n ID. If it crashes or times out, the call is blocked.
- `post_edit_check.py` runs after an edit and only **warns**: em or en dashes, lines over 120 characters, a reminder
  to run `./gradlew spotlessCheck` after a Java edit, and the dependency rules after a build file edit.
- `reviewer_writes.py` (in the frontmatter of the five reviewer agents, and in `settings.json` for `claude -p`) lets
  a reviewer write only under `ai/reviews/` and never change git state.
- `session_start.py` prints the branch, the tree state, one spec status line (FR counts and FRs with no test
  from `docs/requirements/spec.json`, the constitution version) and the read-first list.
- Proof: `python3 -I .claude/hooks/test_guard.py` and `python3 -I .claude/hooks/test_hooks.py` must pass.
- Limits: a command hidden in a script file or an interpreter one-liner is not seen. The guard is a guard rail, not
  a sandbox.

## Rules (`rules/`)

- `spec-driven.md` (always): spec first, code follows. No FR ID, no code. Drift stops the work. Fix rounds at most 2.
- `docs-style.md` (any `.md`): plain English, short, no em or en dashes, 120 characters, current facts only.
- `java-code.md` (`src/main/java/**`): layers and type folders, the ArchUnit rules, Spring conventions,
  errors, security, outbox, Error Prone, no dead or deprecated code.
- `testing.md` (`src/test/**`): tests first, the FR ID in each test, no `@Disabled`, Testcontainers, mocks only at
  a boundary.
- `migrations.md` (Flyway files and `scripts/db/`): Notion "2 · Project" and "6 · Shared catalog" 100%, never edit
  an applied migration, the change report.

## Skills (`skills/`)

Spec (output in `docs/requirements/`):
- `writing-brd`, `writing-prd`, `writing-frd`, `writing-trd`: the four documents, with BR, PR, FR and TR IDs.
- `checking-requirements-direction`: `DIRECTION.md`, each item Done, In progress, Not started, Drift or Orphan.

Repo work:
- `project-gates`: which Gradle checks to run and how to report them.
- `spring-endpoint`: add or change a REST endpoint the way this repo does it.
- `new-migration`: add a Flyway migration the right way.
- `db-notion-audit`: both databases against Notion (merge gate).
- `qa-sweep`: PMD, CPD duplicate code, SpotBugs, deprecations, warnings (decision `0009`); triaged, not fixed.
- `decision-record`: write a decision record and update the index.
- `phase-handoff`: write the handoff when a session stops.

Vendored security skills (Trail of Bits, CC-BY-SA-4.0; `SOURCE.txt` in each names the commit and what differs
here: only dashes replaced). The security-reviewer preloads both:
- `differential-review`: security review of a diff, with blast radius and the changed code no test covers.
- `sharp-edges`: dangerous APIs, insecure defaults and settings; start with its Java, auth and config references.

## Agents (`agents/`)

Twelve agents, one job each: spec-writer, designer, planner, implementer, project-validator, security-reviewer,
db-notion-auditor, kind-runner, qa-reviewer, final-reviewer, docs-keeper, researcher. The roster with what each may
edit: [`agents/README.md`](agents/README.md). How they work together: [`WORKFLOW.md`](WORKFLOW.md).
