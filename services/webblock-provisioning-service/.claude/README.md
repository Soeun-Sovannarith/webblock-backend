# What is in `.claude/` (one page)

Claude Code reads this folder when a session starts in this repo. Four kinds of things live here.

| Kind | What it is | Who uses it |
| --- | --- | --- |
| Hooks | Small scripts that run before or after every tool call and can block it | Claude Code, automatically |
| Rules | Short instruction files loaded into every session (or when matching files are touched) | Every agent |
| Skills | Step-by-step recipes for a recurring job, loaded when the job comes up | Any agent, on demand |
| Agents | Specialists with their own job, tools and limits; the lead hands them one task each | The lead |

`CLAUDE.md` holds the repo's main rules; it is the one instruction file and imports the constitution,
[`docs/CONSTITUTION.md`](docs/CONSTITUTION.md) (Articles I to VIII; only the owner amends it, and the guard asks
before any change to it). [`docs/requirements/spec.yaml`](docs/requirements/spec.yaml): the spec register, every BR,
PR, FR and TR with its status, parents and tests, checked by `make spec-check`. [`WORKFLOW.md`](WORKFLOW.md): the
stages, agents and skills, step by step. [`TECH_STACK.md`](TECH_STACK.md): what the service uses, with exact versions.
`settings.json` (shared) switches the hooks on and lists allowed and denied commands. `settings.local.json` and
`worktrees/` are personal and not in git.

## Hooks (`hooks/`)

- `guard.py` runs before every command, file read or edit, and Notion call. It **blocks**: changes to any cluster
  except the local Kind one, printing or reading secrets, `git push`, history rewrites, merges into `python` or
  `main`, `git add -A`, `git add .` and `git commit -a`, edits to the Notion database design pages, large
  `rm -rf`, and `doctl` or `cloudflared` changes. Reads of the real cluster **ask** you first, and so does any change to
  `docs/CONSTITUTION.md` (only the owner amends it; settings.json asks too). If the guard crashes or times
  out, the call is blocked.
- `post_edit_check.py` runs after an edit and only **warns**: em or en dashes, lines over 120 characters, images
  without a digest, dependency pin changes, ruff problems.
- `test_guard.py` proves the guard: `python3 -I .claude/hooks/test_guard.py` must print "278 of 278 passed".
- The guard also **asks** before a commit that changes `src/`, `tests/` or `deploy/` with no FR-n or TR-n ID;
  `reviewer_writes.py` lets the five reviewer agents (provisioning-validator, final-reviewer, security-reviewer,
  db-notion-auditor, qa-reviewer) write only under `ai/reviews/`. It runs from each one's frontmatter and also
  from settings.json, by agent type, so the limit holds in `claude -p` too, where frontmatter hooks do not run.
  `session_start.py` prints the branch, tree state, one spec line (FRs by status, FRs with no test, the
  constitution version) and the read-first list. Proof: `python3 -I .claude/hooks/test_hooks.py` must print
  "193 of 193 passed".
- Limits: a command hidden inside a script file or a `python -c` line, and Notion edits made in the browser, are not
  seen. The guard is a guard rail, not a sandbox.

## Rules (`rules/`)

- `spec-driven.md` (always): spec first, code follows. Stages Specify, Design, Plan, Implement, Verify, each with a
  gate. No FR ID, no code, with no exception. Drift stops the work.
- `vendored-skills.md` (always): this repo's rules win over copied skills (no branch switch, reports in
  `ai/reviews/`, Kind only).
- `docs-style.md` (when a `.md` file is touched): plain English, short, no em or en dashes, 120 characters.
- `migrations.md` (when a migration is touched): Notion first, the guards, the change report.
- `python-code.md` (when `src/` or `tests/` is touched): layers, CQRS names, async and typing, service invariants.
- `testing.md` (when `tests/` is touched): tests first, the FR ID in each test, no skips, fakes not mocks.

## Skills (`skills/`)

Spec (write and check the requirements, output in `.claude/docs/requirements/`):
- `writing-brd`: why the service exists, who it serves, goals (BR-n).
- `writing-prd`: what each user type gets, with "done when" (PR-n).
- `writing-frd`: exact behaviour, error codes, Given / When / Then acceptance rows (FR-n).
- `writing-trd`: how it is built, linked to FRs and decision records (TR-n).
- `checking-requirements-direction`: from the `make spec-check` output, a verdict (on track, at risk, off
  track), top 5, and each item marked Done, In progress, Not started, Drift or Orphan.

Repo work:
- `provisioning-gates`: which checks to run and how to report them.
- `kind-slot-run`: the full local Kind run (started by hand only).
- `db-notion-audit`: the database against Notion (merge gate).
- `new-migration`: add a migration the right way.
- `security-exceptions`: the vulnerability exception files and their expiry.
- `contracts-pin`: pin and sync `web-block-contracts`.
- `fastapi-endpoint`: add or change a REST endpoint and its MCP tool the way this repo does it.
- `qa-sweep`: run the code quality sweep and triage every finding (`make qa-sweep`).
- `decision-record`: write a decision record and update the index.
- `phase-handoff`: write a handoff when a session stops; at the end of each task, keep `spec.yaml` current.

Copied from public sources (each folder has `SOURCE.txt` and its licence): `claude-md-improver`,
`differential-review`, `sharp-edges`, `async-python-patterns`, `python-anti-patterns`, `python-resilience`,
`python-testing-patterns`, `python-type-safety`, `k8s-security-policies`, `helm-chart-scaffolding`.

## Agents (`agents/`)

Twelve agents, one job each; the roster with what each may edit is `agents/README.md`. How they work together, the
stages and the four owner gates: [`WORKFLOW.md`](WORKFLOW.md).
